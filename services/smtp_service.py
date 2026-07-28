import smtplib
import os
import uuid
import mimetypes
import base64
import requests as _http
from email.message import EmailMessage
from email.utils import formataddr
from datetime import datetime
from utils.db import get_setting
from utils.logger import smtp_logger, error_logger

ATTACHMENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..')

TRACKING_PIXEL = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

_BREVO_HOSTS = ('smtp-relay.brevo.com', 'smtp.sendinblue.com')


def is_brevo_account(account: dict) -> bool:
    return any(h in (account.get('smtp_server') or '').lower() for h in _BREVO_HOSTS)


def send_via_brevo_api(account: dict, to_email: str, subject: str, html_body: str,
                       reply_to: str = '', attachment_data: bytes = None,
                       attachment_name: str = None) -> tuple[bool, str]:
    """
    Send via Brevo HTTP API (port 443 — works on Render).
    account['password'] = Brevo API key (starts with 'xkeysib-').
    """
    api_key   = account.get('password', '')
    from_email = account.get('from_email') or account.get('email', '')
    from_name  = account.get('from_name', '')
    payload = {
        'sender':      {'name': from_name, 'email': from_email},
        'to':          [{'email': to_email}],
        'subject':     subject,
        'htmlContent': html_body,
    }
    if reply_to:
        payload['replyTo'] = {'email': reply_to}
    if attachment_data and attachment_name:
        payload['attachment'] = [{
            'name':    attachment_name,
            'content': base64.b64encode(attachment_data).decode(),
        }]
    try:
        r = _http.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={'api-key': api_key, 'Content-Type': 'application/json'},
            json=payload,
            timeout=20,
        )
        if r.status_code in (200, 201):
            smtp_logger.info(f'[BREVO API] SENT | To: {to_email} | Subject: {subject[:50]}')
            return True, ''
        err = f'Brevo API {r.status_code}: {r.text[:200]}'
        smtp_logger.error(f'[BREVO API] FAILED | {to_email} | {err}')
        return False, err
    except Exception as e:
        return False, str(e)[:200]


def inject_tracking_pixel(body, tracking_id):
    """Inject invisible 1x1 tracking pixel + unsubscribe link at end of email body"""
    host = get_setting('tracking_host') or 'http://localhost:5000'
    pixel_url = f'{host}/track/{tracking_id}.png'
    pixel_tag = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="">'
    unsub_url = f'{host}/unsubscribe/{tracking_id}'
    unsub_tag = f'<p style="font-size:11px;color:#94a3b8;margin-top:30px;border-top:1px solid #e2e8f0;padding-top:10px;">If you no longer wish to receive these emails, <a href="{unsub_url}" style="color:#64748b;">unsubscribe here</a>.</p>'
    if '</body>' in body.lower():
        body = body.replace('</body>', f'{unsub_tag}{pixel_tag}</body>')
    else:
        body += unsub_tag + pixel_tag
    return body


def smtp_connect(smtp_server: str, smtp_port: int, login: str, password: str,
                 timeout: int = 15) -> smtplib.SMTP:
    """
    Connect and authenticate to SMTP.
    Port 465 → SMTP_SSL (implicit TLS)
    Port 587 / anything else → SMTP + STARTTLS
    """
    if smtp_port == 465:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=timeout)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=timeout)
        server.starttls()
    server.login(login, password)
    return server


def get_smtp_connection():
    """Create and return authenticated SMTP connection with timeout."""
    smtp_server   = get_setting('smtp_server') or ''
    smtp_port     = int(get_setting('smtp_port') or '587')
    smtp_username = get_setting('smtp_username') or ''
    smtp_password = get_setting('smtp_password') or ''
    if not all([smtp_server, smtp_username, smtp_password]):
        raise ValueError('SMTP settings incomplete')
    return smtp_connect(smtp_server, smtp_port, smtp_username, smtp_password)


def send_single_email(server, to_email, subject, body, attachment=''):
    """Send a single email with tracking. Returns (tracking_id, None) on success or (None, error) on failure."""
    from_email = get_setting('from_email')
    from_name = get_setting('from_name')
    reply_to = get_setting('reply_to')
    bcc = get_setting('bcc_emails')

    tracking_id = str(uuid.uuid4())
    tracked_body = inject_tracking_pixel(body, tracking_id)

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = formataddr((from_name, from_email))
    msg['To'] = to_email
    msg['Reply-To'] = reply_to
    msg['Bcc'] = bcc
    msg.add_alternative(tracked_body, subtype='html')

    if attachment and os.path.exists(os.path.join(ATTACHMENT_DIR, attachment)):
        filepath = os.path.join(ATTACHMENT_DIR, attachment)
        mime_type, _ = mimetypes.guess_type(filepath)
        if mime_type:
            maintype, subtype = mime_type.split('/', 1)
        else:
            maintype, subtype = 'application', 'octet-stream'
        with open(filepath, 'rb') as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                             filename=os.path.basename(filepath))

    try:
        server.send_message(msg)
        smtp_logger.info(f'SENT | To: {to_email} | Subject: {subject[:50]}')
        return tracking_id, None
    except smtplib.SMTPRecipientsRefused as e:
        smtp_logger.warning(f'BOUNCED | {to_email} | {str(e)[:100]}')
        return None, ('bounced', str(e))
    except Exception as e:
        smtp_logger.error(f'FAILED | {to_email} | {str(e)[:100]}')
        error_logger.error(f'Send failed for {to_email}: {str(e)}')
        return None, ('failed', str(e))
