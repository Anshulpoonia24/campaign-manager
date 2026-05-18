"""
tasks/sequence_tasks.py — Sequence Engine Celery Tasks
=======================================================
Queue: automation_queue (Priority 3)
Runs: every 15 minutes via Beat

Responsibilities:
- Pick up due contacts (next_run_at <= now, status=active)
- Check stop conditions (reply/bounce/unsub/thread status)
- Send next step email (AI or template)
- Advance contact state to next step
- Mark completed when all steps done
"""
import smtplib
import uuid
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

from celery import shared_task
from celery.utils.log import get_task_logger

from tasks._db import get_db

logger = get_task_logger(__name__)
QUEUE  = 'automation_queue'


# ══════════════════════════════════════════════════════════════
# MAIN BEAT TASK — runs every 15 min
# ══════════════════════════════════════════════════════════════

@shared_task(
    bind=True,
    name='tasks.sequence_tasks.process_sequences_task',
    queue=QUEUE,
    max_retries=1,
    acks_late=True,
    priority=8,
)
def process_sequences_task(self):
    """
    Master sequence processor.
    Finds all workspaces, gets due contacts, dispatches per-contact tasks.
    """
    try:
        conn = get_db()
        workspaces = conn.execute(
            "SELECT id FROM workspaces"
        ).fetchall()
        conn.close()

        total_dispatched = 0
        for ws in workspaces:
            wid = ws['id']
            from services.sequence_engine import get_due_contacts, is_sequence_cap_reached
            if is_sequence_cap_reached(wid):
                logger.info(f'[SEQ] Daily cap reached for workspace {wid}, skipping')
                continue
            due = get_due_contacts(wid, limit=200)
            for contact_state in due:
                process_single_contact_task.apply_async(
                    args=[contact_state['contact_id'], contact_state['campaign_id'], wid],
                    queue=QUEUE,
                    priority=7,
                )
                total_dispatched += 1

        logger.info(f'[SEQ] Dispatched {total_dispatched} contact tasks')
        return {'success': True, 'dispatched': total_dispatched}

    except Exception as exc:
        logger.error(f'[SEQ] process_sequences_task error: {exc}')
        try:
            raise self.retry(exc=exc, countdown=60)
        except self.MaxRetriesExceededError:
            return {'success': False, 'error': str(exc)}


# ══════════════════════════════════════════════════════════════
# PER-CONTACT TASK
# ══════════════════════════════════════════════════════════════

@shared_task(
    bind=True,
    name='tasks.sequence_tasks.process_single_contact_task',
    queue=QUEUE,
    max_retries=2,
    acks_late=True,
    priority=7,
)
def process_single_contact_task(self, contact_id: int,
                                 campaign_id: int, workspace_id: int):
    """
    Process one contact's next sequence step.
    1. Check stop conditions
    2. Get current step definition
    3. Send email
    4. Advance state or mark complete
    """
    from services.sequence_engine import (
        check_stop_conditions, get_steps, get_contact_state,
        advance_state, mark_completed, mark_stopped,
        calculate_next_run, get_smart_delay,
    )

    try:
        # ── 1. Stop condition check ───────────────────────────
        should_stop, reason = check_stop_conditions(contact_id, campaign_id)
        if should_stop:
            mark_stopped(contact_id, campaign_id, reason)
            logger.info(f'[SEQ] Stopped contact {contact_id} — {reason}')
            return {'stopped': True, 'reason': reason}

        # ── 2. Get current state + steps ─────────────────────
        state = get_contact_state(contact_id, campaign_id)
        if not state or state['status'] != 'active':
            return {'skipped': True, 'reason': 'not_active'}

        steps = get_steps(campaign_id)
        if not steps:
            mark_completed(contact_id, campaign_id)
            return {'completed': True, 'reason': 'no_steps'}

        current_step_num = state['current_step']

        # Find the step matching current_step_num
        step = next(
            (s for s in steps if s['step_order'] == current_step_num),
            None
        )

        # If no matching step, try first step >= current_step_num
        if not step:
            candidates = [s for s in steps if s['step_order'] >= current_step_num]
            step = candidates[0] if candidates else None

        if not step:
            mark_completed(contact_id, campaign_id)
            return {'completed': True, 'reason': 'all_steps_done'}

        # ── 3. Handle step type ───────────────────────────────
        if step['step_type'] == 'wait':
            # Pure wait step — just advance to next
            _advance_to_next(
                contact_id, campaign_id, steps,
                step['step_order'], step['delay_days']
            )
            return {'sent': False, 'step_type': 'wait'}

        # ── 4. Send email step ────────────────────────────────
        conn = get_db()
        contact = conn.execute(
            "SELECT * FROM contacts WHERE id=?", (contact_id,)
        ).fetchone()
        conn.close()

        if not contact:
            mark_stopped(contact_id, campaign_id, 'contact_not_found')
            return {'stopped': True, 'reason': 'contact_not_found'}

        # Safety: unsubscribe check
        from utils.db import is_unsubscribed
        if is_unsubscribed(contact['email']):
            mark_stopped(contact_id, campaign_id, 'unsubscribed')
            return {'stopped': True, 'reason': 'unsubscribed'}

        # Duplicate guard — don't resend same step
        conn = get_db()
        already = conn.execute("""
            SELECT id FROM emails_sent
            WHERE contact_id=? AND campaign_id=? AND status='sent'
            AND subject=?
        """, (contact_id, campaign_id, step['subject'])).fetchone()
        conn.close()
        if already:
            _advance_to_next(
                contact_id, campaign_id, steps,
                step['step_order'], step['delay_days']
            )
            return {'skipped': True, 'reason': 'already_sent'}

        # ── 5. Build email body ───────────────────────────────
        subject = _render(step['subject'], contact)
        body    = _render(step['body'], contact)

        if step['ai_enabled']:
            ai_body = _generate_ai_body(contact, step, workspace_id)
            if ai_body:
                body = ai_body

        # ── 6. Send via SMTP ──────────────────────────────────
        sent, error = _send_email(contact, subject, body,
                                  campaign_id, workspace_id)

        if sent:
            # ── 7. Advance to next step ───────────────────────
            _advance_to_next(
                contact_id, campaign_id, steps,
                step['step_order'], step['delay_days']
            )
            logger.info(
                f'[SEQ] Sent step {step["step_order"]} to '
                f'{contact["email"]} campaign {campaign_id}'
            )
            return {'sent': True, 'step': step['step_order']}
        else:
            logger.warning(f'[SEQ] Send failed contact {contact_id}: {error}')
            return {'sent': False, 'error': error}

    except Exception as exc:
        logger.error(f'[SEQ] process_single_contact error: {exc}')
        try:
            raise self.retry(exc=exc, countdown=120)
        except self.MaxRetriesExceededError:
            return {'success': False, 'error': str(exc)}


# ══════════════════════════════════════════════════════════════
# ENROLL TASK — called from API route
# ══════════════════════════════════════════════════════════════

@shared_task(
    name='tasks.sequence_tasks.enroll_contacts_task',
    queue=QUEUE,
    acks_late=True,
    priority=6,
)
def enroll_contacts_task(contact_ids: list, campaign_id: int,
                          workspace_id: int) -> dict:
    """Async bulk enrollment — called from API."""
    from services.sequence_engine import enroll_contacts_bulk
    result = enroll_contacts_bulk(contact_ids, campaign_id, workspace_id)
    logger.info(f'[SEQ] Enrolled {result["enrolled"]} contacts in campaign {campaign_id}')
    return result


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _render(template: str, contact) -> str:
    """Replace {name}, {company}, {email} placeholders."""
    if not template:
        return ''
    return (template
            .replace('{name}',    contact['name']    or '')
            .replace('{company}', contact['company'] or '')
            .replace('{email}',   contact['email']   or ''))


def _advance_to_next(contact_id: int, campaign_id: int, steps: list,
                     current_order: int, current_delay: int):
    """
    Find next step after current_order and schedule it,
    or mark completed if no more steps.
    """
    from services.sequence_engine import (
        advance_state, mark_completed, calculate_next_run, get_smart_delay
    )
    next_steps = [s for s in steps if s['step_order'] > current_order]
    if not next_steps:
        mark_completed(contact_id, campaign_id)
        return

    next_step   = next_steps[0]
    smart_delay = get_smart_delay(contact_id, next_step['delay_days'])
    next_run    = calculate_next_run(smart_delay)
    advance_state(contact_id, campaign_id, next_step['step_order'], next_run)


def _generate_ai_body(contact, step: dict, workspace_id: int) -> str | None:
    """Generate AI-personalized email body for a sequence step."""
    try:
        from utils.db import get_setting
        context     = contact['context']     or ''
        designation = contact['designation'] or 'founder/executive'
        name        = contact['name']        or ''
        company     = contact['company']     or ''

        if not context:
            return None

        prompt = f"""Write a short outreach email for step {step['step_order']} of a sequence.

Contact: {name}, {designation} at {company}
Context: {context}
Step type: {step['step_type']}
Base template: {step['body'][:300] if step['body'] else 'Write a follow-up email'}

Rules:
- Very short (3-4 sentences max)
- Personalize using the context
- Casual, direct tone
- End with a simple CTA
- Output as HTML with <p> tags only"""

        keys_str = get_setting('groq_api_keys') or ''
        keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        if not keys:
            return None

        import requests
        r = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {keys[0]}',
                     'Content-Type': 'application/json'},
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 400,
            },
            timeout=30
        )
        if r.status_code == 200:
            return r.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.warning(f'[SEQ] AI generation failed: {e}')
    return None


def _send_email(contact, subject: str, body: str,
                campaign_id: int, workspace_id: int) -> tuple:
    """
    Send email via SMTP rotation.
    Returns (success: bool, error: str | None)
    """
    try:
        from services.smtp_rotation import (
            get_next_smtp_account, mark_send_success, mark_send_failure
        )
        from utils.db import get_setting

        account = get_next_smtp_account()
        if account:
            smtp_server  = account['smtp_server']
            smtp_port    = account['smtp_port']
            smtp_user    = account['email']
            smtp_pass    = account['password']
            from_email   = account['email']
            from_name    = account.get('from_name') or get_setting('from_name')
            account_id   = account['id']
            reply_to     = account.get('reply_to') or get_setting('reply_to')
            bcc          = account.get('bcc_emails') or get_setting('bcc_emails')
            signature    = account.get('signature', '')
        else:
            smtp_server  = get_setting('smtp_server')
            smtp_port    = int(get_setting('smtp_port') or 587)
            smtp_user    = get_setting('smtp_username')
            smtp_pass    = get_setting('smtp_password')
            from_email   = get_setting('from_email') or smtp_user
            from_name    = get_setting('from_name')
            account_id   = None
            reply_to     = get_setting('reply_to')
            bcc          = get_setting('bcc_emails')
            signature    = ''

        if not smtp_server or not smtp_user or not smtp_pass:
            return False, 'SMTP not configured'

        # Duplicate prevention
        from services.sequence_engine import is_duplicate_sequence_send
        if is_duplicate_sequence_send(contact['id'], campaign_id, subject):
            return False, 'duplicate_prevented'

        tracking_id = str(uuid.uuid4())

        # Inject tracking pixel (after signature)
        from app import inject_tracking_pixel
        from services.smtp_rotation import append_signature
        body_with_sig = append_signature(body, signature)
        tracked_body = inject_tracking_pixel(
            body_with_sig, tracking_id,
            contact_id=contact['id'],
            campaign_id=campaign_id,
            workspace_id=workspace_id
        )

        reply_to_val = reply_to or get_setting('reply_to')
        bcc_val      = bcc or get_setting('bcc_emails')

        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()
        server.login(smtp_user, smtp_pass)

        msg = EmailMessage()
        msg['Subject']  = subject
        msg['From']     = formataddr((from_name, from_email))
        msg['To']       = contact['email']
        if reply_to_val and reply_to_val.strip():
            msg['Reply-To'] = reply_to_val
        if bcc_val and bcc_val.strip():
            msg['Bcc'] = bcc_val
        msg.add_alternative(tracked_body, subtype='html')
        if reply_to and reply_to.strip():
            msg['Reply-To'] = reply_to
        if bcc and bcc.strip():
            msg['Bcc'] = bcc

        server.send_message(msg)
        server.quit()

        # Log to emails_sent
        conn = get_db()
        conn.execute("""
            INSERT INTO emails_sent
              (campaign_id, contact_id, email, subject, body,
               status, tracking_id, sent_at, workspace_id)
            VALUES (?,?,?,?,?,'sent',?,?,?)
        """, (campaign_id, contact['id'], contact['email'],
              subject, body, tracking_id, datetime.now(), workspace_id))
        conn.execute(
            "UPDATE contacts SET status='sent' WHERE id=?", (contact['id'],)
        )
        conn.commit()
        conn.close()

        if account_id:
            mark_send_success(account_id)

        # Log to thread system
        try:
            from services.inbox_service import get_or_create_thread, insert_message
            thread_id = get_or_create_thread(
                contact['id'], campaign_id, subject
            )
            insert_message(
                thread_id=thread_id, direction='outgoing',
                sender_email=from_email,
                recipient_email=contact['email'],
                subject=subject, body=body, message_id=tracking_id
            )
        except Exception:
            pass

        return True, None

    except smtplib.SMTPRecipientsRefused as e:
        # Log bounce
        _log_bounce(contact, subject, body, campaign_id, workspace_id, str(e))
        from services.sequence_engine import mark_stopped
        mark_stopped(contact['id'], campaign_id, 'bounced')
        if account_id:
            mark_send_failure(account_id)
        return False, f'bounce: {str(e)[:100]}'

    except Exception as e:
        return False, str(e)[:200]


def _log_bounce(contact, subject: str, body: str,
                campaign_id: int, workspace_id: int, reason: str):
    """Log a bounce to emails_sent."""
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO emails_sent
              (campaign_id, contact_id, email, subject, body,
               status, bounce_reason, sent_at, workspace_id)
            VALUES (?,?,?,?,?,'bounced',?,?,?)
        """, (campaign_id, contact['id'], contact['email'],
              subject, body, reason[:200], datetime.now(), workspace_id))
        conn.commit()
        conn.close()
    except Exception:
        pass
