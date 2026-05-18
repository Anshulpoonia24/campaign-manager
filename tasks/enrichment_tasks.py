"""
tasks/enrichment_tasks.py — Contact Enrichment Tasks
=====================================================
Queue: enrichment_queue  (Priority 6 — LOWEST)
Handles: website scraping, AI context generation, company research
These are slow background jobs that must NEVER block email or IMAP.
"""
import os
from celery import shared_task
from celery.utils.log import get_task_logger
from tasks._db import get_db, get_setting

logger = get_task_logger(__name__)

QUEUE = 'enrichment_queue'


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
                      'max_tokens': 400},
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


# ── ENRICH SINGLE CONTACT ─────────────────────────────────────
@shared_task(
    bind=True,
    name='tasks.enrichment_tasks.enrich_single_contact',
    queue=QUEUE,
    max_retries=2,
    default_retry_delay=60,
    rate_limit='20/m',
    acks_late=True,
)
def enrich_single_contact(self, contact_id, force=False):
    """
    Scrape company website + generate AI context for one contact.
    Runs in enrichment_queue — lowest priority, never blocks sending or IMAP.
    """
    import requests as http_requests
    conn = get_db()
    try:
        contact = conn.execute('SELECT * FROM contacts WHERE id=?', (contact_id,)).fetchone()
        if not contact:
            return {'success': False, 'reason': 'not_found'}

        existing_context = contact['context'] if 'context' in contact.keys() else ''
        if not force and existing_context:
            return {'success': False, 'reason': 'already_enriched'}

        domain  = contact['email'].split('@')[1] if '@' in (contact['email'] or '') else ''
        company = contact['company'] or domain

        # Step 1: Scrape website
        website_text = ''
        if domain:
            try:
                r = http_requests.get(
                    f'https://{domain}', timeout=8,
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; OutreachOS/1.0)'}
                )
                if r.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(r.text, 'html.parser')
                    title     = soup.title.string if soup.title else ''
                    meta      = soup.find('meta', attrs={'name': 'description'})
                    meta_desc = meta.get('content', '') if meta else ''
                    paras     = ' '.join([p.get_text() for p in soup.find_all('p')[:5]])
                    website_text = f"Title: {title}. Description: {meta_desc}. Content: {paras[:500]}"
            except Exception as e:
                logger.debug(f'Website scrape failed for {domain}: {e}')

        # Step 2: AI summarize
        prompt = (
            f"In 2-3 bullet points (under 60 words), summarize what {company} does.\n"
            f"{'Website data: ' + website_text[:600] if website_text else 'Use only well-known public facts.'}\n"
            f"Include: what they do, any known funding/stage, tech focus. Plain text only."
        )

        priority = (get_setting('ai_priority') or 'groq,gemini').split(',')
        result = None
        for provider in priority:
            provider = provider.strip().lower()
            if provider == 'groq':
                result, _ = _call_groq(prompt)
            elif provider == 'gemini':
                result, _ = _call_gemini(prompt)
            if result:
                break

        if result:
            conn.execute('UPDATE contacts SET context=? WHERE id=?', (result.strip(), contact_id))
            conn.commit()
            logger.info(f'Enriched contact {contact_id} ({company})')
            # Also run industry detection
            try:
                from services.industry_detector import enrich_contact_intelligence
                enrich_contact_intelligence(contact_id)
            except Exception as ie:
                logger.debug(f'Industry detection failed for {contact_id}: {ie}')
            return {'success': True, 'contact_id': contact_id, 'company': company}
        else:
            return {'success': False, 'reason': 'ai_failed'}

    except Exception as exc:
        logger.error(f'enrich_single_contact error contact={contact_id}: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {'success': False, 'error': str(exc)}
    finally:
        conn.close()
