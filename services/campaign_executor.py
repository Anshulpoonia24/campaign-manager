"""
services/campaign_executor.py — Backend Campaign Execution Engine
=================================================================
Fully browser-independent. Survives logout, refresh, restart.
All state persisted in DB. Workers pick up from where they left off.

Job statuses: draft → queued → running → paused → completed → failed → cancelled
"""
import uuid
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from utils.db import get_db, is_unsubscribed
from utils.logger import app_logger, error_logger

# ── STATUS CONSTANTS ──────────────────────────────────────────
class JobStatus:
    DRAFT     = 'draft'
    QUEUED    = 'queued'
    RUNNING   = 'running'
    PAUSED    = 'paused'
    COMPLETED = 'completed'
    FAILED    = 'failed'
    CANCELLED = 'cancelled'

class ContactStatus:
    PENDING      = 'pending'
    GENERATING   = 'generating'
    SENDING      = 'sending'
    SENT         = 'sent'
    FAILED       = 'failed'
    BOUNCED      = 'bounced'
    SKIPPED      = 'skipped'
    UNSUBSCRIBED = 'unsubscribed'


# ── LOGGING ───────────────────────────────────────────────────

def log(campaign_id: int, message: str, level: str = 'info',
        contact_id: int = None, smtp_email: str = '', workspace_id: int = 1):
    """Persist a log entry to campaign_logs table."""
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO campaign_logs
              (campaign_id, workspace_id, contact_id, level, message, smtp_email)
            VALUES (?,?,?,?,?,?)
        """, (campaign_id, workspace_id, contact_id, level, message, smtp_email))
        conn.commit()
        conn.close()
    except Exception as e:
        error_logger.error(f'[EXEC] log write failed: {e}')


# ── CAMPAIGN STATE ────────────────────────────────────────────

def set_campaign_status(campaign_id: int, status: str,
                        started_at: datetime = None, completed_at: datetime = None):
    conn = get_db()
    if started_at:
        conn.execute(
            "UPDATE campaigns SET job_status=?, started_at=? WHERE id=?",
            (status, started_at, campaign_id)
        )
    elif completed_at:
        conn.execute(
            "UPDATE campaigns SET job_status=?, completed_at=? WHERE id=?",
            (status, completed_at, campaign_id)
        )
    else:
        conn.execute(
            "UPDATE campaigns SET job_status=? WHERE id=?",
            (status, campaign_id)
        )
    conn.commit()
    conn.close()


def update_campaign_counts(campaign_id: int):
    """Recalculate sent/failed counts from emails_sent."""
    conn = get_db()
    sent = conn.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status='sent'",
        (campaign_id,)
    ).fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status IN ('failed','bounced')",
        (campaign_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE campaigns SET sent_count=?, failed_count=? WHERE id=?",
        (sent, failed, campaign_id)
    )
    conn.commit()
    conn.close()


def get_campaign_status(campaign_id: int) -> dict:
    """Get full campaign execution status for UI polling."""
    conn = get_db()
    camp = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not camp:
        conn.close()
        return {}

    total    = camp['total_contacts'] or 0
    sent     = conn.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status='sent'",
        (campaign_id,)
    ).fetchone()[0]
    failed   = conn.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status IN ('failed','bounced')",
        (campaign_id,)
    ).fetchone()[0]
    opened   = conn.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND opened=1",
        (campaign_id,)
    ).fetchone()[0]
    replied  = conn.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND replied=1",
        (campaign_id,)
    ).fetchone()[0]
    done     = sent + failed
    pending  = max(0, total - done)
    pct      = round(done / total * 100) if total else 0

    # ETA calculation
    eta_str = '—'
    if camp['started_at'] and done > 0 and pending > 0:
        try:
            started = datetime.fromisoformat(str(camp['started_at'])[:19])
            elapsed = (datetime.now() - started).total_seconds()
            rate    = done / elapsed  # contacts per second
            eta_sec = int(pending / rate) if rate > 0 else 0
            if eta_sec < 60:
                eta_str = f'{eta_sec}s'
            elif eta_sec < 3600:
                eta_str = f'{eta_sec//60}m {eta_sec%60}s'
            else:
                eta_str = f'{eta_sec//3600}h {(eta_sec%3600)//60}m'
        except Exception:
            pass

    # Recent logs
    logs = conn.execute("""
        SELECT cl.level, cl.message, cl.smtp_email, cl.created_at,
               c.name as contact_name, c.email as contact_email
        FROM campaign_logs cl
        LEFT JOIN contacts c ON cl.contact_id = c.id
        WHERE cl.campaign_id = ?
        ORDER BY cl.created_at DESC
        LIMIT 50
    """, (campaign_id,)).fetchall()

    # Contact execution table
    contacts = conn.execute("""
        SELECT es.contact_id, es.email, es.status, es.opened, es.replied,
               es.bounce_reason, es.sent_at,
               c.name, c.company,
               sa.email as smtp_used
        FROM emails_sent es
        JOIN contacts c ON es.contact_id = c.id
        LEFT JOIN smtp_accounts sa ON sa.email = es.email
        WHERE es.campaign_id = ?
        ORDER BY es.sent_at DESC
        LIMIT 100
    """, (campaign_id,)).fetchall()

    # SMTP utilization
    smtp_accounts = conn.execute("""
        SELECT email, sent_today, daily_limit, health_score, warmup_stage, active
        FROM smtp_accounts WHERE active=1
    """).fetchall()

    conn.close()

    return {
        'campaign_id':  campaign_id,
        'name':         camp['name'],
        'job_status':   camp['job_status'] or 'draft',
        'send_mode':    camp['send_mode'] or 'template',
        'total':        total,
        'sent':         sent,
        'failed':       failed,
        'opened':       opened,
        'replied':      replied,
        'pending':      pending,
        'done':         done,
        'pct':          pct,
        'eta':          eta_str,
        'started_at':   str(camp['started_at']) if camp['started_at'] else None,
        'completed_at': str(camp['completed_at']) if camp['completed_at'] else None,
        'logs':         [dict(l) for l in logs],
        'contacts':     [dict(c) for c in contacts],
        'smtp_accounts':[dict(s) for s in smtp_accounts],
        'running':      camp['job_status'] == JobStatus.RUNNING,
        'completed':    camp['job_status'] in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED),
    }


# ── LAUNCH ────────────────────────────────────────────────────

def launch_campaign(campaign_id: int, contact_ids: list, subject_template: str,
                    body_template: str, send_mode: str, workspace_id: int,
                    attachment_path: str = '') -> dict:
    """
    Queue a campaign for backend execution.
    Returns immediately — worker handles everything.
    """
    conn = get_db()
    # Store execution params on campaign
    conn.execute("""
        UPDATE campaigns
        SET job_status=?, total_contacts=?, send_mode=?,
            subject_template=?, body_template=?, attachment_path=?, started_at=?,
            sent_count=0, failed_count=0
        WHERE id=?
    """, (JobStatus.QUEUED, len(contact_ids), send_mode,
          subject_template, body_template, attachment_path, datetime.now(), campaign_id))
    conn.commit()
    conn.close()

    log(campaign_id, f'Campaign queued — {len(contact_ids)} contacts, mode={send_mode}',
        'info', workspace_id=workspace_id)

    # Try Celery async first
    try:
        from celery_app import is_redis_available
        if is_redis_available():
            from tasks.email_tasks import execute_campaign_task
            result = execute_campaign_task.apply_async(
                args=[campaign_id, contact_ids, subject_template,
                      body_template, send_mode, workspace_id, attachment_path],
                queue='send_email_queue',
                priority=9,
            )
            log(campaign_id, f'Queued to Celery worker (task_id={result.id})',
                'info', workspace_id=workspace_id)
            return {'success': True, 'mode': 'celery', 'task_id': result.id}
    except Exception as e:
        error_logger.warning(f'[EXEC] Celery unavailable: {e}')

    # Fallback: threading (still browser-independent — daemon=False keeps it alive)
    import threading
    t = threading.Thread(
        target=_run_campaign_sync,
        args=(campaign_id, contact_ids, subject_template,
              body_template, send_mode, workspace_id, attachment_path),
        daemon=False,  # CRITICAL: daemon=False survives even if main thread exits
        name=f'campaign-{campaign_id}'
    )
    t.start()
    log(campaign_id, 'Running in background thread (Redis unavailable)',
        'info', workspace_id=workspace_id)
    return {'success': True, 'mode': 'thread'}


# ── SYNC EXECUTION (threading fallback) ──────────────────────

def _run_campaign_sync(campaign_id: int, contact_ids: list,
                       subject_template: str, body_template: str,
                       send_mode: str, workspace_id: int,
                       attachment_path: str = ''):
    """
    Execute campaign synchronously in a background thread.
    Fully resumable — checks DB status before each send.
    """
    from services.smtp_rotation import (
        get_next_smtp_account, mark_send_success,
        mark_send_failure, append_signature
    )
    from utils.db import get_setting

    set_campaign_status(campaign_id, JobStatus.RUNNING, started_at=datetime.now())
    log(campaign_id, f'Execution started — {len(contact_ids)} contacts',
        'success', workspace_id=workspace_id)

    sent = failed = skipped = 0

    for i, contact_id in enumerate(contact_ids):
        # Check if paused/cancelled before each send
        conn = get_db()
        camp = conn.execute(
            "SELECT job_status FROM campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
        conn.close()

        if not camp or camp['job_status'] in (JobStatus.PAUSED, JobStatus.CANCELLED):
            log(campaign_id,
                f'Execution {"paused" if camp and camp["job_status"]==JobStatus.PAUSED else "cancelled"} at contact {i+1}/{len(contact_ids)}',
                'warning', workspace_id=workspace_id)
            return

        conn = get_db()
        contact = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        conn.close()

        if not contact:
            skipped += 1
            continue

        # Suppression check
        if is_unsubscribed(contact['email']):
            log(campaign_id, f'Skipped {contact["email"]} — unsubscribed',
                'warning', contact_id=contact_id, workspace_id=workspace_id)
            skipped += 1
            continue

        # Duplicate check
        conn = get_db()
        already = conn.execute(
            "SELECT id FROM emails_sent WHERE contact_id=? AND campaign_id=? AND status='sent'",
            (contact_id, campaign_id)
        ).fetchone()
        conn.close()
        if already:
            skipped += 1
            continue

        # Get SMTP account
        account = get_next_smtp_account()
        if not account:
            log(campaign_id, 'No active SMTP accounts available — stopping',
                'error', workspace_id=workspace_id)
            set_campaign_status(campaign_id, JobStatus.FAILED)
            return

        smtp_email = account['email']

        # Build email content
        subject = subject_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')

        if send_mode == 'ai':
            log(campaign_id, f'Generating AI email for {contact["name"]}',
                'info', contact_id=contact_id, smtp_email=smtp_email, workspace_id=workspace_id)
            body = _generate_ai_body(contact, body_template, workspace_id)
            if not body:
                body = body_template  # fallback to template
        else:
            body = body_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')

        # Append signature
        body = append_signature(body, account.get('signature', ''))

        # Send
        log(campaign_id, f'Sending to {contact["email"]} via {smtp_email}',
            'info', contact_id=contact_id, smtp_email=smtp_email, workspace_id=workspace_id)

        success, error_msg = _send_one(
            contact, subject, body, campaign_id, workspace_id,
            account, attachment_path
        )

        if success:
            sent += 1
            mark_send_success(account['id'])
            log(campaign_id, f'Delivered to {contact["email"]}',
                'success', contact_id=contact_id, smtp_email=smtp_email, workspace_id=workspace_id)
        else:
            failed += 1
            mark_send_failure(account['id'])
            log(campaign_id, f'Failed {contact["email"]}: {error_msg}',
                'error', contact_id=contact_id, smtp_email=smtp_email, workspace_id=workspace_id)

        # Update counts
        update_campaign_counts(campaign_id)

        # Warmup delay (randomized 5–15s)
        import random
        delay = random.randint(5, 15)
        log(campaign_id, f'Warmup delay {delay}s',
            'info', workspace_id=workspace_id)
        time.sleep(delay)

    # Done
    set_campaign_status(campaign_id, JobStatus.COMPLETED, completed_at=datetime.now())
    log(campaign_id,
        f'Campaign completed — Sent: {sent}, Failed: {failed}, Skipped: {skipped}',
        'success', workspace_id=workspace_id)


def _generate_ai_body(contact, body_template: str, workspace_id: int) -> str:
    """Generate AI-personalized body. Falls back to template on failure."""
    try:
        from utils.db import get_setting
        import requests as _req

        context     = contact['context']     if 'context'     in contact.keys() else ''
        designation = contact['designation'] if 'designation' in contact.keys() else 'founder/executive'
        if not context:
            return None

        prompt = f"""Write a cold outreach email to {contact['name']}, {designation} at {contact['company']}.

Context: {context}

Base template: {body_template[:400]}

Rules:
- Personalize the opening using the context
- Keep it short (4-5 sentences max)
- Casual, direct tone
- Output as HTML with <p> tags"""

        keys_str = get_setting('groq_api_keys') or ''
        keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        if keys:
            r = _req.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {keys[0]}', 'Content-Type': 'application/json'},
                json={'model': 'llama-3.3-70b-versatile',
                      'messages': [{'role': 'user', 'content': prompt}],
                      'max_tokens': 500},
                timeout=30
            )
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        error_logger.warning(f'[EXEC] AI generation failed: {e}')
    return None


def _send_one(contact, subject: str, body: str, campaign_id: int,
              workspace_id: int, account: dict, attachment_path: str = '') -> tuple:
    """Send one email. Returns (success, error_msg)."""
    import uuid, mimetypes, os
    from utils.db import get_setting

    try:
        tracking_id = str(uuid.uuid4())

        # Inject tracking pixel
        try:
            from app import inject_tracking_pixel
            body = inject_tracking_pixel(
                body, tracking_id,
                contact_id=contact['id'],
                campaign_id=campaign_id,
                workspace_id=workspace_id
            )
        except Exception:
            pass

        reply_to = account.get('reply_to') or get_setting('reply_to') or get_setting('imap_username') or account.get('email', '')
        bcc      = account.get('bcc_emails') or get_setting('bcc_emails')

        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From']    = formataddr((account.get('from_name', ''), account['email']))
        msg['To']      = contact['email']
        if reply_to and reply_to.strip():
            msg['Reply-To'] = reply_to
        if bcc and bcc.strip():
            msg['Bcc'] = bcc
        msg.add_alternative(body, subtype='html')

        # Attachment
        if attachment_path and os.path.exists(attachment_path):
            mt, _ = mimetypes.guess_type(attachment_path)
            maintype, subtype = mt.split('/', 1) if mt else ('application', 'octet-stream')
            with open(attachment_path, 'rb') as f:
                msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                                   filename=os.path.basename(attachment_path))

        server = smtplib.SMTP(account['smtp_server'], account['smtp_port'], timeout=30)
        server.starttls()
        server.login(account['email'], account['password'])
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
        conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (contact['id'],))
        conn.commit()
        conn.close()

        # Thread system
        try:
            from services.inbox_service import get_or_create_thread, insert_message
            tid = get_or_create_thread(contact['id'], campaign_id, subject)
            insert_message(
                thread_id=tid, direction='outgoing',
                sender_email=account['email'], recipient_email=contact['email'],
                subject=subject, body=body, message_id=tracking_id
            )
        except Exception:
            pass

        return True, None

    except smtplib.SMTPRecipientsRefused as e:
        _log_bounce(contact, subject, body, campaign_id, workspace_id, str(e))
        return False, f'Bounced: {str(e)[:100]}'
    except smtplib.SMTPAuthenticationError:
        return False, 'SMTP Authentication Failed'
    except smtplib.SMTPConnectError:
        return False, 'SMTP Connection Failed'
    except Exception as e:
        _log_bounce(contact, subject, body, campaign_id, workspace_id, str(e), status='failed')
        return False, str(e)[:150]


def _log_bounce(contact, subject, body, campaign_id, workspace_id, reason, status='bounced'):
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO emails_sent
              (campaign_id, contact_id, email, subject, body,
               status, bounce_reason, sent_at, workspace_id)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (campaign_id, contact['id'], contact['email'],
              subject, body, status, reason[:200], datetime.now(), workspace_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── CONTROL ACTIONS ───────────────────────────────────────────

def pause_campaign(campaign_id: int, workspace_id: int):
    set_campaign_status(campaign_id, JobStatus.PAUSED)
    log(campaign_id, 'Campaign paused by user', 'warning', workspace_id=workspace_id)


def resume_campaign(campaign_id: int, workspace_id: int):
    """Resume a paused campaign — re-queue remaining contacts."""
    conn = get_db()
    camp = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not camp:
        conn.close()
        return False

    # Find contacts not yet sent
    sent_ids = [r['contact_id'] for r in conn.execute(
        "SELECT contact_id FROM emails_sent WHERE campaign_id=? AND status='sent'",
        (campaign_id,)
    ).fetchall()]
    conn.close()

    # Get original contact list from emails_sent (pending = not in sent)
    conn2 = get_db()
    all_contacts = conn2.execute(
        "SELECT DISTINCT contact_id FROM emails_sent WHERE campaign_id=?",
        (campaign_id,)
    ).fetchall()
    conn2.close()

    remaining = [r['contact_id'] for r in all_contacts if r['contact_id'] not in sent_ids]

    if not remaining:
        set_campaign_status(campaign_id, JobStatus.COMPLETED, completed_at=datetime.now())
        return True

    set_campaign_status(campaign_id, JobStatus.QUEUED)
    log(campaign_id, f'Resuming — {len(remaining)} contacts remaining', 'info', workspace_id=workspace_id)

    return launch_campaign(
        campaign_id, remaining,
        camp['subject_template'] or '',
        camp['body_template'] or '',
        camp['send_mode'] or 'template',
        workspace_id,
        camp['attachment_path'] if 'attachment_path' in camp.keys() else ''
    )


def cancel_campaign(campaign_id: int, workspace_id: int):
    set_campaign_status(campaign_id, JobStatus.CANCELLED, completed_at=datetime.now())
    log(campaign_id, 'Campaign cancelled by user', 'error', workspace_id=workspace_id)
