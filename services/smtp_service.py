import smtplib
import os
import uuid
import mimetypes
from email.message import EmailMessage
from email.utils import formataddr
from datetime import datetime
from utils.db import get_setting
from utils.logger import smtp_logger, error_logger

ATTACHMENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..')

TRACKING_PIXEL = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82'


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


def get_smtp_connection():
    """Create and return authenticated SMTP connection"""
    smtp_server = get_setting('smtp_server')
    smtp_port = int(get_setting('smtp_port'))
    smtp_username = get_setting('smtp_username')
    smtp_password = get_setting('smtp_password')
    server = smtplib.SMTP(smtp_server, smtp_port)
    server.starttls()
    server.login(smtp_username, smtp_password)
    return server


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
