"""
tasks/email_tasks.py — Email Sending Tasks
==========================================
Queue: send_email_queue  (Priority 2 — HIGH)
Handles: campaign sends, single email, SMTP retry, daily reset
Rate limit: 12 emails/min to protect SMTP reputation
"""
import os
import uuid
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from celery import shared_task
from celery.utils.log import get_task_logger
from tasks._db import get_db, get_setting, is_unsubscribed

logger = get_task_logger(__name__)

QUEUE = 'send_email_queue'


def _inject_tracking(body, tracking_id, host):
    import re, urllib.parse
    pixel_tag = f'<img src="{host}/track/{tracking_id}.png" width="1" height="1" style="display:none" alt="">'
    unsub_tag = (
        f'<p style="font-size:11px;color:#94a3b8;margin-top:30px;">'
        f'If you no longer wish to receive these emails, '
        f'<a href="{host}/unsubscribe/{tracking_id}">unsubscribe here</a>.</p>'
    )
    def rewrite(m):
        url = m.group(1)
        if any(s in url for s in ['/track/', '/unsubscribe/', 'mailto:', '#', 'javascript:']):
            return m.group(0)
        token = str(uuid.uuid4())
        enc = urllib.parse.quote(url, safe='')
        return f'href="{host}/click/{token}?url={enc}&tid={tracking_id}"'
    body = re.sub(r'href="(https?://[^"]+)"', rewrite, body)
    body += unsub_tag + pixel_tag
    return body


# ── SEND SINGLE EMAIL ─────────────────────────────────────────
@shared_task(
    bind=True,
    name='tasks.email_tasks.send_single_email',
    queue=QUEUE,
    max_retries=3,
    default_retry_delay=120,
    rate_limit='12/m',
    acks_late=True,
)
def send_single_email(self, campaign_id, contact_id, subject, body, smtp_creds):
    """
    Send one email to one contact with tracking injection.
    Retries up to 3× on SMTP errors with 120s backoff.
    """
    conn = get_db()
    contact = None
    try:
        contact = conn.execute('SELECT * FROM contacts WHERE id=?', (contact_id,)).fetchone()
        if not contact:
            return {'success': False, 'reason': 'contact_not_found'}

        if is_unsubscribed(contact['email']):
            return {'success': False, 'reason': 'unsubscribed'}

        already = conn.execute(
            "SELECT id FROM emails_sent WHERE contact_id=? AND campaign_id=? AND status='sent'",
            (contact_id, campaign_id)
        ).fetchone()
        if already:
            return {'success': False, 'reason': 'duplicate'}

        # Resolve template vars
        resolved_subject = subject.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')
        resolved_body    = body.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')

        tracking_id  = str(uuid.uuid4())
        host         = get_setting('tracking_host') or 'http://localhost:5000'
        tracked_body = _inject_tracking(resolved_body, tracking_id, host)

        msg = EmailMessage()
        msg['Subject']    = resolved_subject
        msg['From']       = formataddr((smtp_creds.get('from_name', ''), smtp_creds['from_email']))
        msg['To']         = contact['email']
        msg['Message-ID'] = f'<{tracking_id}@outreachos>'
        reply_to = get_setting('reply_to') or get_setting('imap_username') or smtp_creds.get('from_email', '')
        if reply_to:
            msg['Reply-To'] = reply_to
        bcc = get_setting('bcc_emails')
        if bcc and bcc.strip():
            msg['Bcc'] = bcc
        msg.add_alternative(tracked_body, subtype='html')

        # Use login_username for Brevo/custom SMTP (may differ from from_email)
        smtp_login = smtp_creds.get('login_username') or smtp_creds['username']
        server = smtplib.SMTP(smtp_creds['server'], smtp_creds['port'], timeout=30)
        server.starttls()
        server.login(smtp_login, smtp_creds['password'])
        server.send_message(msg)
        server.quit()

        conn.execute("""
            INSERT INTO emails_sent
              (campaign_id, contact_id, email, subject, body, status, tracking_id, sent_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (campaign_id, contact_id, contact['email'], resolved_subject,
              resolved_body, 'sent', tracking_id, datetime.now()))
        conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (contact_id,))
        conn.commit()

        # Thread system (non-critical)
        try:
            from services.inbox_service import get_or_create_thread, insert_message
            tid = get_or_create_thread(contact_id, campaign_id, resolved_subject)
            insert_message(
                thread_id=tid, direction='outgoing',
                sender_email=smtp_creds['from_email'], recipient_email=contact['email'],
                subject=resolved_subject, body=resolved_body, message_id=tracking_id
            )
        except Exception:
            pass

        # SMTP health score
        if smtp_creds.get('account_id'):
            try:
                from services.smtp_rotation import mark_send_success
                mark_send_success(smtp_creds['account_id'])
            except Exception:
                pass

        logger.info(f'SENT | campaign={campaign_id} contact={contact_id} to={contact["email"]}')
        return {'success': True, 'tracking_id': tracking_id}

    except smtplib.SMTPRecipientsRefused as exc:
        email_addr = contact['email'] if contact else ''
        conn.execute("""
            INSERT INTO emails_sent
              (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (campaign_id, contact_id, email_addr, subject, body, 'bounced', str(exc)[:200], datetime.now()))
        conn.commit()
        if smtp_creds.get('account_id'):
            try:
                from services.smtp_rotation import mark_send_failure
                mark_send_failure(smtp_creds['account_id'])
            except Exception:
                pass
        try:
            from services.lead_scoring import update_lead_score
            update_lead_score(contact_id, 'bounce')
        except Exception:
            pass
        logger.warning(f'BOUNCED | contact={contact_id} | {str(exc)[:80]}')
        return {'success': False, 'reason': 'bounced', 'error': str(exc)[:200]}

    except (smtplib.SMTPException, ConnectionError, OSError) as exc:
        logger.error(f'SMTP error contact={contact_id}: {exc}')
        try:
            raise self.retry(exc=exc, countdown=120)
        except self.MaxRetriesExceededError:
            email_addr = contact['email'] if contact else ''
            conn.execute("""
                INSERT INTO emails_sent
                  (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (campaign_id, contact_id, email_addr, subject, body, 'failed', str(exc)[:200], datetime.now()))
            conn.commit()
            return {'success': False, 'reason': 'max_retries', 'error': str(exc)[:200]}

    except Exception as exc:
        logger.error(f'Unexpected error send_single_email contact={contact_id}: {exc}')
        return {'success': False, 'reason': 'error', 'error': str(exc)[:200]}

    finally:
        conn.close()


# ── SEND CAMPAIGN ORCHESTRATOR ────────────────────────────────
@shared_task(
    name='tasks.email_tasks.execute_campaign_task',
    queue=QUEUE,
    acks_late=True,
    bind=True,
    max_retries=0,
)
def execute_campaign_task(self, campaign_id, contact_ids, subject_template,
                          body_template, send_mode, workspace_id, attachment_path=''):
    """
    Browser-independent campaign executor.
    Runs entirely in Celery worker — survives logout/refresh/restart.
    """
    logger.info(
        f'[EXEC] Task RECEIVED | campaign={campaign_id} contacts={len(contact_ids)} '
        f'mode={send_mode} task_id={self.request.id}'
    )
    # Immediately mark running so frontend sees progress
    try:
        from services.campaign_executor import set_campaign_status, log as camp_log, JobStatus, _update_heartbeat
        set_campaign_status(campaign_id, JobStatus.RUNNING, started_at=datetime.now())
        _update_heartbeat(campaign_id)
        camp_log(campaign_id, f'Worker picked up task (id={self.request.id})', 'info', workspace_id=workspace_id)
        logger.info(f'[EXEC] Status set to RUNNING | campaign={campaign_id}')
    except Exception as e:
        logger.error(f'[EXEC] Failed to set RUNNING status: {e}')

    try:
        from services.campaign_executor import _run_campaign_sync
        _run_campaign_sync(
            campaign_id, contact_ids, subject_template,
            body_template, send_mode, workspace_id, attachment_path
        )
        logger.info(f'[EXEC] Task COMPLETED | campaign={campaign_id}')
        return {'success': True}
    except Exception as exc:
        logger.error(f'[EXEC] Task FAILED | campaign={campaign_id} error={exc}')
        from services.campaign_executor import set_campaign_status, log as camp_log, JobStatus
        set_campaign_status(campaign_id, JobStatus.FAILED)
        camp_log(campaign_id, f'Worker error: {str(exc)[:200]}', 'error', workspace_id=workspace_id)
        return {'success': False, 'error': str(exc)}


# ── SEND CAMPAIGN ORCHESTRATOR (legacy) ────────────────────────────────
@shared_task(
    name='tasks.email_tasks.send_campaign_async',
    queue=QUEUE,
    acks_late=True,
)
def send_campaign_async(campaign_id, contact_ids, subject_template, body_template):
    """
    Fan out send_single_email tasks for each contact.
    5-second stagger between emails to warm up SMTP.
    """
    from services.smtp_rotation import get_next_smtp_account
    queued = skipped = 0

    for i, contact_id in enumerate(contact_ids):
        account = get_next_smtp_account()
        if account:
            creds = {
                'server':         account['smtp_server'],
                'port':           account['smtp_port'],
                'username':       account['email'],
                'login_username': account.get('login_username') or account['email'],
                'password':       account['password'],
                'from_email':     account['email'],
                'from_name':      account.get('from_name') or get_setting('from_name'),
                'account_id':     account['id'],
            }
        else:
            creds = {
                'server':         get_setting('smtp_server'),
                'port':           int(get_setting('smtp_port') or 587),
                'username':       get_setting('smtp_username'),
                'login_username': get_setting('smtp_username'),
                'password':       get_setting('smtp_password'),
                'from_email':     get_setting('from_email') or get_setting('smtp_username'),
                'from_name':      get_setting('from_name'),
                'account_id':     None,
            }

        if not creds['server'] or not creds['username']:
            skipped += 1
            continue

        send_single_email.apply_async(
            args=[campaign_id, contact_id, subject_template, body_template, creds],
            countdown=i * 5,
            queue=QUEUE,
            priority=9,
        )
        queued += 1

    conn = get_db()
    conn.execute("UPDATE campaigns SET status='sent' WHERE id=?", (campaign_id,))
    conn.commit()
    conn.close()

    logger.info(f'Campaign {campaign_id} queued: {queued} emails, {skipped} skipped')
    return {'queued': queued, 'skipped': skipped}


# ── AI CAMPAIGN ORCHESTRATOR ──────────────────────────────────
@shared_task(
    name='tasks.email_tasks.send_campaign_ai_async',
    queue=QUEUE,
    acks_late=True,
)
def send_campaign_ai_async(campaign_id, contact_ids, subject_template):
    """
    Fan out AI generation tasks. Each contact: generate → send chain.
    8-second stagger to avoid API rate limits.
    """
    from tasks.ai_tasks import generate_ai_email_task
    queued = 0
    for i, contact_id in enumerate(contact_ids):
        generate_ai_email_task.apply_async(
            args=[campaign_id, contact_id, subject_template],
            countdown=i * 8,
            queue='ai_generation_queue',
            priority=4,
        )
        queued += 1
    logger.info(f'AI Campaign {campaign_id} queued: {queued} contacts')
    return {'queued': queued}


# ── DAILY SMTP RESET ──────────────────────────────────────────
@shared_task(
    name='tasks.email_tasks.daily_smtp_reset_task',
    queue=QUEUE,
    acks_late=True,
)
def daily_smtp_reset_task():
    """Reset sent_today counters + check warmup upgrades. Runs daily via Beat."""
    try:
        from services.smtp_rotation import reset_daily_counts, check_warmup_upgrade
        reset_daily_counts()
        check_warmup_upgrade()
        logger.info('Daily SMTP reset + warmup check complete')
        return {'success': True}
    except Exception as exc:
        logger.error(f'Daily reset failed: {exc}')
        return {'success': False, 'error': str(exc)}
