"""
tasks/ai_tasks.py — AI Email Generation Tasks
==============================================
Queue: ai_generation_queue  (Priority 5 — MEDIUM)
Handles: personalized email generation, reply drafting
NOTE: Contact enrichment is in enrichment_tasks.py (lower priority queue)
"""
import os
from datetime import datetime
from celery import shared_task
from celery.utils.log import get_task_logger
from tasks._db import get_db, get_setting

logger = get_task_logger(__name__)

QUEUE = 'ai_generation_queue'


def _call_groq(prompt):
    import requests
    keys_str = get_setting('groq_api_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    if not keys:
        return None, 'No Groq keys'
    for key in keys:
        try:
            r = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={'model': 'llama-3.3-70b-versatile',
                      'messages': [{'role': 'user', 'content': prompt}],
                      'max_tokens': 1000},
                timeout=45
            )
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content'].strip(), None
            elif r.status_code == 429:
                continue
        except Exception as e:
            logger.warning(f'Groq error: {e}')
    return None, 'Groq exhausted'


def _call_gemini(prompt):
    import requests
    api_key = get_setting('gemini_api_key')
    if not api_key:
        return None, 'No Gemini key'
    try:
        r = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}',
            json={'contents': [{'parts': [{'text': prompt}]}]},
            timeout=45
        )
        if r.status_code == 200:
            return r.json()['candidates'][0]['content']['parts'][0]['text'].strip(), None
        return None, f'Gemini {r.status_code}'
    except Exception as e:
        return None, str(e)


def _generate_with_ai(prompt):
    """Try providers in priority order, log usage."""
    priority = (get_setting('ai_priority') or 'groq,gemini').split(',')
    conn = get_db()
    try:
        for provider in priority:
            provider = provider.strip().lower()
            if provider == 'groq':
                body, err = _call_groq(prompt)
            elif provider == 'gemini':
                body, err = _call_gemini(prompt)
            else:
                continue
            try:
                conn.execute("INSERT INTO ai_usage (provider, purpose, success) VALUES (?,?,?)",
                             (provider, 'email', 1 if body else 0))
                conn.commit()
            except Exception:
                pass
            if body:
                return body, None
            logger.warning(f'AI provider {provider} failed: {err}')
        return None, 'All AI providers failed'
    finally:
        conn.close()


# ── GENERATE AI EMAIL + QUEUE SEND ────────────────────────────
@shared_task(
    bind=True,
    name='tasks.ai_tasks.generate_ai_email_task',
    queue=QUEUE,
    max_retries=2,
    default_retry_delay=30,
    rate_limit='30/m',
    acks_late=True,
)
def generate_ai_email_task(self, campaign_id, contact_id, subject_template):
    """
    Generate personalized email for one contact, then queue to send_email_queue.
    Part of the AI campaign chain: ai_generation_queue → send_email_queue.
    """
    conn = get_db()
    try:
        contact = conn.execute('SELECT * FROM contacts WHERE id=?', (contact_id,)).fetchone()
        if not contact:
            return {'success': False, 'reason': 'contact_not_found'}

        context     = contact['context']     if 'context'     in contact.keys() else ''
        designation = contact['designation'] if 'designation' in contact.keys() else ''

        if not context:
            conn.execute("""
                INSERT INTO emails_sent
                  (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (campaign_id, contact_id, contact['email'], subject_template,
                  '', 'failed', 'No AI context — enrich contact first', datetime.now()))
            conn.commit()
            conn.close()
            return {'success': False, 'reason': 'no_context'}

        prompt_template = get_setting('email_prompt')
        prompt = prompt_template \
            .replace('{name}', contact['name'] or '') \
            .replace('{company}', contact['company'] or '') \
            .replace('{designation}', designation or 'founder/executive')
        if context:
            prompt = f"CONTEXT ABOUT {contact['company']}:\n{context}\n\nUSE this context for a specific opening line.\n\n" + prompt

        body, error = _generate_with_ai(prompt)

        if not body:
            conn.execute("""
                INSERT INTO emails_sent
                  (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (campaign_id, contact_id, contact['email'], subject_template,
                  '', 'failed', f'AI failed: {error}', datetime.now()))
            conn.commit()
            conn.close()
            return {'success': False, 'reason': 'ai_failed', 'error': error}

        conn.close()

        subject = subject_template \
            .replace('{company}', contact['company'] or '') \
            .replace('{name}', contact['name'] or '')

        # Get SMTP creds and route to send_email_queue
        from services.smtp_rotation import get_next_smtp_account
        account = get_next_smtp_account()
        if account:
            creds = {
                'server': account['smtp_server'], 'port': account['smtp_port'],
                'username': account['email'],     'password': account['password'],
                'from_email': account['email'],
                'from_name': account['from_name'] or get_setting('from_name'),
                'account_id': account['id'],
            }
        else:
            creds = {
                'server': get_setting('smtp_server'),
                'port': int(get_setting('smtp_port') or 587),
                'username': get_setting('smtp_username'),
                'password': get_setting('smtp_password'),
                'from_email': get_setting('from_email') or get_setting('smtp_username'),
                'from_name': get_setting('from_name'),
                'account_id': None,
            }

        from tasks.email_tasks import send_single_email
        send_single_email.apply_async(
            args=[campaign_id, contact_id, subject, body, creds],
            queue='send_email_queue',
            priority=9,
        )
        logger.info(f'AI email generated → queued to send_email_queue | campaign={campaign_id} contact={contact_id}')
        return {'success': True}

    except Exception as exc:
        logger.error(f'generate_ai_email_task error: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {'success': False, 'error': str(exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── ENRICH ALL (orchestrator — routes to enrichment_queue) ────
@shared_task(
    name='tasks.ai_tasks.enrich_all_contacts',
    queue=QUEUE,
    acks_late=True,
)
def enrich_all_contacts(force=False):
    """
    Fan out enrich_single_contact tasks to enrichment_queue (lowest priority).
    Keeps AI generation queue free for email personalization.
    """
    from tasks.enrichment_tasks import enrich_single_contact
    conn = get_db()
    if force:
        contacts = conn.execute("SELECT id FROM contacts WHERE email_valid=1").fetchall()
    else:
        contacts = conn.execute(
            "SELECT id FROM contacts WHERE (context IS NULL OR context='') AND email_valid=1"
        ).fetchall()
    conn.close()

    queued = 0
    for i, c in enumerate(contacts):
        enrich_single_contact.apply_async(
            args=[c['id'], force],
            countdown=i * 2,
            queue='enrichment_queue',
            priority=2,
        )
        queued += 1

    logger.info(f'Enrichment queued for {queued} contacts → enrichment_queue')
    return {'queued': queued}
