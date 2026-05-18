"""
tasks/verification_tasks.py — Email Verification Tasks
=======================================================
Queue: enrichment_queue  (Priority 6 — LOWEST)
Handles: MX record check, SMTP handshake verification
Runs in enrichment_queue — background, never blocks sending or IMAP.
"""
from celery import shared_task, group
from celery.utils.log import get_task_logger
from tasks._db import get_db

logger = get_task_logger(__name__)

QUEUE = 'enrichment_queue'


@shared_task(
    bind=True,
    name='tasks.verification_tasks.verify_single_contact',
    queue=QUEUE,
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
    priority=2,
)
def verify_single_contact(self, contact_id):
    """Verify one contact's email via MX + SMTP handshake."""
    conn = get_db()
    try:
        contact = conn.execute('SELECT id, email FROM contacts WHERE id=?', (contact_id,)).fetchone()
        if not contact:
            return {'success': False, 'reason': 'not_found'}

        import dns.resolver
        import smtplib

        email  = contact['email']
        domain = email.split('@')[1]
        catchall = {
            'gmail.com','googlemail.com','outlook.com','hotmail.com',
            'yahoo.com','live.com','icloud.com','me.com','aol.com',
            'protonmail.com','proton.me'
        }

        try:
            mx_records = dns.resolver.resolve(domain, 'MX', lifetime=5)
            mx_host    = str(sorted(mx_records, key=lambda x: x.preference)[0].exchange).rstrip('.')

            if domain in catchall:
                valid, reason = True, f'Valid - {domain} (catch-all)'
            else:
                try:
                    smtp = smtplib.SMTP(timeout=8)
                    smtp.connect(mx_host, 25)
                    smtp.helo('verify.local')
                    smtp.mail('verify@verify.local')
                    code, _ = smtp.rcpt(email)
                    smtp.quit()
                    valid  = code == 250
                    reason = 'Valid - mailbox exists' if valid else f'Invalid ({code})'
                except Exception:
                    valid, reason = True, 'Valid - MX exists (SMTP blocked)'

        except dns.resolver.NXDOMAIN:
            valid, reason = False, 'Domain does not exist'
        except dns.resolver.NoAnswer:
            valid, reason = False, 'No MX record'
        except Exception as e:
            valid, reason = True, f'Valid - DNS timeout ({str(e)[:30]})'

        conn.execute(
            'UPDATE contacts SET email_valid=?, validation_reason=? WHERE id=?',
            (1 if valid else 0, reason, contact_id)
        )
        conn.commit()
        logger.info(f'Verified {email}: valid={valid}')
        return {'success': True, 'contact_id': contact_id, 'valid': valid, 'reason': reason}

    except Exception as exc:
        logger.error(f'verify_single_contact error: {exc}')
        return {'success': False, 'error': str(exc)}
    finally:
        conn.close()


@shared_task(
    name='tasks.verification_tasks.verify_all_contacts',
    queue=QUEUE,
    acks_late=True,
)
def verify_all_contacts(reverify=False):
    """Fan out verify_single_contact for all unverified contacts."""
    conn = get_db()
    if reverify:
        contacts = conn.execute('SELECT id FROM contacts').fetchall()
    else:
        contacts = conn.execute('SELECT id FROM contacts WHERE email_valid=-1').fetchall()
    conn.close()

    tasks = group(verify_single_contact.s(c['id']) for c in contacts)
    result = tasks.apply_async(queue=QUEUE)
    logger.info(f'Verification queued for {len(contacts)} contacts → {QUEUE}')
    return {'queued': len(contacts), 'group_id': str(result.id)}
