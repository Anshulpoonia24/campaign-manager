"""
tasks/inbox_tasks.py — IMAP Sync Tasks
=======================================
Queue: imap_sync_queue  (Priority 1 — HIGHEST)
Handles: reply detection, thread categorization, lead scoring
Beat: every 3 minutes
CRITICAL: This queue must NEVER wait behind AI or enrichment tasks.
"""
import os
from celery import shared_task
from celery.utils.log import get_task_logger
from tasks._db import get_db, get_setting

logger = get_task_logger(__name__)

QUEUE = 'imap_sync_queue'


# ── CHECK IMAP REPLIES ────────────────────────────────────────
@shared_task(
    bind=True,
    name='tasks.inbox_tasks.check_replies_task',
    queue=QUEUE,
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
    priority=10,
)
def check_replies_task(self):
    """
    Check IMAP inbox for new replies. Runs every 3 minutes via Beat.
    Highest priority — reply detection must be instant.
    """
    import imaplib
    import email as email_lib
    from email.header import decode_header as _decode_header

    imap_server   = get_setting('imap_server')
    imap_port     = int(get_setting('imap_port') or 993)
    imap_username = get_setting('imap_username')
    imap_password = get_setting('imap_password')

    if not all([imap_server, imap_username, imap_password]):
        return {'logged': 0, 'reason': 'imap_not_configured'}

    def _decode(h):
        if not h: return ''
        parts = []
        for part, charset in _decode_header(h):
            if isinstance(part, bytes):
                parts.append(part.decode(charset or 'utf-8', errors='ignore'))
            else:
                parts.append(part)
        return ' '.join(parts)

    def _extract_email(from_h):
        if '<' in from_h and '>' in from_h:
            return from_h.split('<')[1].split('>')[0].strip().lower()
        return from_h.strip().lower()

    try:
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
        mail.login(imap_username, imap_password)
        mail.select('INBOX')

        status, messages = mail.search(None, 'UNSEEN')
        if status != 'OK' or not messages[0]:
            mail.logout()
            return {'logged': 0}

        email_ids = messages[0].split()
        logged = 0
        conn = get_db()

        from services.inbox_service import find_thread_by_email, insert_message, categorize_reply_with_ai

        for eid in email_ids:
            try:
                status, msg_data = mail.fetch(eid, '(RFC822)')
                if status != 'OK':
                    continue

                msg          = email_lib.message_from_bytes(msg_data[0][1])
                from_header  = _decode(msg.get('From', ''))
                sender_email = _extract_email(from_header)
                subject      = _decode(msg.get('Subject', ''))
                message_id   = msg.get('Message-ID', '').strip()
                in_reply_to  = msg.get('In-Reply-To', '').strip()

                body_text = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == 'text/plain':
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_text = payload.decode('utf-8', errors='ignore')[:1000]
                                break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode('utf-8', errors='ignore')[:1000]

                if message_id:
                    if conn.execute('SELECT id FROM messages WHERE message_id=?', (message_id,)).fetchone():
                        continue

                ai_category = categorize_reply_with_ai(body_text, subject)
                thread_id   = find_thread_by_email(sender_email, subject, in_reply_to or None)
                insert_message(
                    thread_id=thread_id, direction='incoming',
                    sender_email=sender_email, recipient_email=imap_username,
                    subject=subject, body=body_text,
                    message_id=message_id, in_reply_to=in_reply_to,
                    ai_category=ai_category
                )

                if ai_category in ('interested', 'meeting'):
                    conn.execute('UPDATE threads SET status=? WHERE id=?', (ai_category, thread_id))

                contact = conn.execute('SELECT * FROM contacts WHERE email=?', (sender_email,)).fetchone()
                if contact:
                    from services.lead_scoring import update_lead_score
                    score_event = ai_category if ai_category in ('interested', 'meeting') else 'reply'
                    update_lead_score(contact['id'], score_event)

                    notes = f"Subject: {subject}\n{body_text[:300]}"
                    if not conn.execute(
                        "SELECT id FROM follow_ups WHERE email=? AND notes LIKE ?",
                        (sender_email, f'%{subject[:50]}%')
                    ).fetchone():
                        conn.execute("""
                            INSERT INTO follow_ups (contact_id, email, name, company, notes)
                            VALUES (?,?,?,?,?)
                        """, (contact['id'], sender_email, contact['name'], contact['company'], notes))
                    # Update most recent sent email as replied (not all)
                    conn.execute("""
                        UPDATE emails_sent SET replied=1
                        WHERE contact_id=? AND status='sent'
                        AND id = (SELECT id FROM emails_sent WHERE contact_id=? AND status='sent'
                                  ORDER BY sent_at DESC LIMIT 1)
                    """, (contact['id'], contact['id']))
                    conn.execute("UPDATE contacts SET status='replied' WHERE id=?", (contact['id'],))

                conn.commit()
                logged += 1
                logger.info(f'Reply logged | from={sender_email} category={ai_category}')

            except Exception as e:
                logger.error(f'IMAP parse error for email {eid}: {e}')
                continue

        conn.close()
        mail.logout()
        logger.info(f'IMAP sync complete: {logged} new replies')
        return {'logged': logged}

    except imaplib.IMAP4.error as exc:
        logger.error(f'IMAP auth error: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {'logged': 0, 'error': str(exc)}
    except Exception as exc:
        logger.error(f'IMAP task error: {exc}')
        return {'logged': 0, 'error': str(exc)}
