import os
import dns.resolver
import smtplib
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime
import time
import threading
import uuid
import logging
from logging.handlers import RotatingFileHandler
from flask_login import LoginManager, UserMixin, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from utils.constants import CATCHALL_DOMAINS

# Load .env file
load_dotenv()

# ==============================
# BASE PATHS (Azure/Linux compatible)
# ==============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# /home is the ONLY persistent directory on Azure Linux App Service
# App runs from /tmp — so we copy DB from /home/data on startup
PERSISTENT_DIR = '/home/data'
DATA_DIR = '/home/data'
LOG_DIR = '/home/logs'
UPLOAD_DIR = '/home/uploads'

def _setup_paths():
    """Ensure persistent dirs exist and DB is accessible from app working dir."""
    global DATA_DIR, LOG_DIR, UPLOAD_DIR
    # Try /home/data (Azure)
    try:
        os.makedirs(PERSISTENT_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        test_file = os.path.join(PERSISTENT_DIR, '.write_test')
        with open(test_file, 'w') as f:
            f.write('ok')
        os.remove(test_file)
        DATA_DIR = PERSISTENT_DIR
        print(f'[STARTUP] Persistent storage: {DATA_DIR} (writable)')
    except (OSError, PermissionError) as e:
        # Try Render path
        render_data = '/opt/render/project/src/data'
        render_logs = '/opt/render/project/src/logs'
        render_uploads = '/opt/render/project/src/uploads'
        try:
            os.makedirs(render_data, exist_ok=True)
            os.makedirs(render_logs, exist_ok=True)
            os.makedirs(render_uploads, exist_ok=True)
            test_file = os.path.join(render_data, '.write_test')
            with open(test_file, 'w') as f:
                f.write('ok')
            os.remove(test_file)
            DATA_DIR = render_data
            LOG_DIR = render_logs
            UPLOAD_DIR = render_uploads
            print(f'[STARTUP] Render storage: {DATA_DIR} (writable)')
            return
        except (OSError, PermissionError):
            pass
        # Local Windows dev fallback
        print(f'[STARTUP] /home/data not writable ({e}), using local data/')
        DATA_DIR = os.path.join(BASE_DIR, 'data')
        LOG_DIR = os.path.join(BASE_DIR, 'logs')
        UPLOAD_DIR = os.path.join(BASE_DIR, 'attachments')
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        return

    # If app is running from /tmp (Azure), ensure DB exists in DATA_DIR
    app_cwd = os.getcwd()
    if app_cwd.startswith('/tmp'):
        cwd_db = os.path.join(app_cwd, 'campaigns.db')
        persistent_db = os.path.join(PERSISTENT_DIR, 'campaigns.db')
        if os.path.exists(cwd_db) and not os.path.exists(persistent_db):
            import shutil
            shutil.copy2(cwd_db, persistent_db)
            print(f'[STARTUP] Copied blank DB to persistent storage')
        elif os.path.exists(persistent_db):
            print(f'[STARTUP] Using persistent DB: {persistent_db} ({os.path.getsize(persistent_db)} bytes)')

_setup_paths()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'CHANGE-ME-generate-with-python-secrets')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB upload limit
DB_PATH = os.path.join(DATA_DIR, 'campaigns.db')

# Register blueprints
from routes.admin import admin_bp
app.register_blueprint(admin_bp)

from routes.copilot import copilot_bp
app.register_blueprint(copilot_bp)

from routes.auth import auth_bp
app.register_blueprint(auth_bp)

from routes.tracking import tracking_bp
app.register_blueprint(tracking_bp)

from routes.inbox import inbox_bp
app.register_blueprint(inbox_bp)

from routes.automations import automations_bp
app.register_blueprint(automations_bp)

from routes.analytics import analytics_bp
app.register_blueprint(analytics_bp)

from routes.contacts import contacts_bp
app.register_blueprint(contacts_bp)

from routes.settings import settings_bp
app.register_blueprint(settings_bp)

from routes.campaigns import campaigns_bp
app.register_blueprint(campaigns_bp)

from routes.sequences import sequences_bp
app.register_blueprint(sequences_bp)

from routes.dashboard import dashboard_bp
app.register_blueprint(dashboard_bp)

# ==============================
# LOGGING (production-safe, rotating)
# ==============================
try:
    app_logger = logging.getLogger('campaign')
    app_logger.setLevel(logging.INFO)
    app_handler = RotatingFileHandler(os.path.join(LOG_DIR, 'app.log'), maxBytes=5*1024*1024, backupCount=3)
    app_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    app_logger.addHandler(app_handler)

    smtp_logger = logging.getLogger('smtp')
    smtp_logger.setLevel(logging.INFO)
    smtp_handler = RotatingFileHandler(os.path.join(LOG_DIR, 'smtp.log'), maxBytes=5*1024*1024, backupCount=3)
    smtp_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    smtp_logger.addHandler(smtp_handler)

    error_logger = logging.getLogger('errors')
    error_logger.setLevel(logging.ERROR)
    error_handler = RotatingFileHandler(os.path.join(LOG_DIR, 'error.log'), maxBytes=5*1024*1024, backupCount=3)
    error_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(module)s:%(lineno)d - %(message)s'))
    error_logger.addHandler(error_handler)
except Exception:
    # If log files can't be created, use NullHandler (app won't crash)
    app_logger = logging.getLogger('campaign')
    smtp_logger = logging.getLogger('smtp')
    error_logger = logging.getLogger('errors')
    app_logger.addHandler(logging.NullHandler())
    smtp_logger.addHandler(logging.NullHandler())
    error_logger.addHandler(logging.NullHandler())

# ==============================
# RATE LIMITING
# ==============================
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per hour"],
    storage_uri="memory://"
)

# ==============================
# GLOBAL ERROR HANDLERS
# ==============================
@app.errorhandler(429)
def ratelimit_handler(e):
    error_logger.warning(f'Rate limit hit: {request.remote_addr} -> {request.path}')
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({'error': 'Too many requests. Slow down!'}), 429
    flash('Too many requests! Please slow down.', 'error')
    return redirect(url_for('dash.dashboard')), 429


@app.errorhandler(500)
def internal_error(e):
    error_logger.error(f'500 Error: {request.path} - {str(e)}')
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return f'''<html><body style="font-family:sans-serif;padding:40px;">
    <h2>500 — Internal Error</h2>
    <p style="color:red;">{str(e)[:200]}</p>
    <a href="/">Dashboard</a> | <a href="/settings">Settings</a> | <a href="/logout">Logout</a>
    </body></html>''', 500


@app.errorhandler(404)
def not_found(e):
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    flash('Page not found!', 'error')
    return redirect(url_for('dash.dashboard'))


@app.errorhandler(413)
def file_too_large(e):
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({'error': 'File too large (max 16MB)'}), 413
    flash('File too large! Maximum 16MB allowed.', 'error')
    return redirect(request.referrer or url_for('dash.dashboard'))

# ==============================
# AUTHENTICATION
# ==============================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please login to access this page.'
login_manager.login_message_category = 'error'


@login_manager.unauthorized_handler
def unauthorized_api():
    """Return JSON 401 for API requests instead of redirect."""
    from flask import request as req, jsonify, redirect, url_for
    if req.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    return redirect(url_for('auth.login', next=req.path))


class User(UserMixin):
    def __init__(self, id, username, role='admin', workspace_id=1):
        self.id = id
        self.username = username
        self.role = role
        self.workspace_id = workspace_id or 1


@login_manager.user_loader
def load_user(user_id):
    try:
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
        if row:
            wid = row['workspace_id'] if 'workspace_id' in row.keys() else 1
            role = row['role'] if 'role' in row.keys() else 'admin'
            return User(row['id'], row['username'], role, wid)
    except Exception:
        pass
    return None

# ==============================
# CONFIGURABLE SETTINGS (stored in DB)
# ==============================
DEFAULT_SETTINGS = {
    'gemini_api_key': os.getenv('GEMINI_API_KEY', ''),
    'groq_api_keys': os.getenv('GROQ_API_KEYS', ''),
    'ai_priority': os.getenv('AI_PRIORITY', 'groq,gemini'),
    'smtp_server': os.getenv('SMTP_SERVER', ''),
    'smtp_port': os.getenv('SMTP_PORT', '587'),
    'smtp_username': os.getenv('SMTP_USERNAME', ''),
    'smtp_password': os.getenv('SMTP_PASSWORD', ''),
    'from_email': os.getenv('FROM_EMAIL', ''),
    'from_name': os.getenv('FROM_NAME', ''),
    'reply_to': os.getenv('REPLY_TO', ''),
    'bcc_emails': os.getenv('BCC_EMAILS', ''),
    'tracking_host': os.getenv('TRACKING_HOST', ''),
    'imap_server': os.getenv('IMAP_SERVER', ''),
    'imap_port': os.getenv('IMAP_PORT', '993'),
    'imap_username': os.getenv('IMAP_USERNAME', ''),
    'imap_password': os.getenv('IMAP_PASSWORD', ''),
    'imap_check_interval': os.getenv('IMAP_CHECK_INTERVAL', '180'),
    'email_prompt': """Write a cold outreach email to {name}, founder/executive at {company}.

RULES:
1. Open with ONE specific fact about {company}. Use only WELL KNOWN facts.
2. In 1 line connect why they need engineering talent.
3. Pitch Shiksha Infotech using this EXACT HTML block:
   <b>Shiksha Infotech (Est. 2009) | 400+ engineers | Founded by alumni of top Indian engineering schools | Offices in US and India | We place pre-vetted AI/ML engineers at $30-55/hr (vs $100-150/hr US rates), onboarded in 2-3 weeks.</b>
4. End with simple CTA - 15 min call.
5. MAX 4-5 sentences. Very short.
6. Casual, founder-to-founder tone.
7. Do NOT use: impressive, innovative, trajectory, remarkable, truly, genuinely, incredible.
8. Do NOT start with: Ive been following.
9. No subject line in body. Output as HTML with <p> tags.
10. MUST end with EXACTLY this signature block - copy paste it as-is, do not change anything:

<p>Best regards,</p>
<p>Anshul<br><b>Shiksha Infotech</b> | Est. 2009<br><a href="https://shikshainfotech.com">shikshainfotech.com</a></p>"""
}


def _get_reply_to():
    """Get reply_to — always prefer IMAP inbox so replies are tracked."""
    # Priority: explicit reply_to setting → imap_username → empty
    reply_to = get_setting('reply_to')
    if reply_to and reply_to.strip():
        return reply_to.strip()
    imap_user = get_setting('imap_username')
    if imap_user and imap_user.strip():
        return imap_user.strip()
    return ''


def get_setting(key):
    """Get setting for current workspace (falls back to global)."""
    try:
        from flask_login import current_user
        wid = getattr(current_user, 'workspace_id', 1) if current_user and current_user.is_authenticated else 1
    except Exception:
        wid = 1
    conn = get_db()
    # Try workspace-specific first
    row = conn.execute("SELECT value FROM settings WHERE key=? AND workspace_id=?", (key, wid)).fetchone()
    if not row:
        # Fall back to global (workspace_id=1 or NULL)
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        return row[0]
    return DEFAULT_SETTINGS.get(key, '')


def set_setting(key, value):
    conn = get_db()
    try:
        from flask_login import current_user
        wid = getattr(current_user, 'workspace_id', 1) if current_user and current_user.is_authenticated else 1
    except Exception:
        wid = 1
    existing = conn.execute("SELECT key FROM settings WHERE key=? AND workspace_id=?", (key, wid)).fetchone()
    if existing:
        conn.execute("UPDATE settings SET value=? WHERE key=? AND workspace_id=?", (value, key, wid))
    else:
        conn.execute("INSERT OR IGNORE INTO settings (key, value, workspace_id) VALUES (?,?,?)", (key, value, wid))
    conn.commit()
    conn.close()


def get_db():
    from utils.db import get_db as _utils_get_db
    return _utils_get_db()


def _table_exists(conn, table_name):
    from utils.db import USE_POSTGRES
    if USE_POSTGRES:
        row = conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name=%s", (table_name,)).fetchone()
    else:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
    return row is not None

def init_db():
    from utils.init_db import init_db as _init
    _init(get_db, DEFAULT_SETTINGS)


# Initialize DB immediately at module load (before any request can hit load_user)
try:
    # Backup before init — guard inside backup_db prevents duplicate runs
    from utils.backup import backup_db
    backup_db(DB_PATH, os.path.join(os.path.dirname(DB_PATH), 'backups'))
except Exception as _be:
    print(f'[STARTUP] Backup skipped: {_be}')

try:
    from utils.db import USE_POSTGRES, DATABASE_URL, _build_pg_dsn
    print(f'[STARTUP] USE_POSTGRES={USE_POSTGRES}')
    print(f'[STARTUP] DATABASE_URL set={bool(DATABASE_URL)} len={len(DATABASE_URL)}')
    if USE_POSTGRES:
        # Test PG connection directly to surface errors
        try:
            raw = _connect_pg()
            raw.close()
            print('[STARTUP] PostgreSQL connection test OK')
        except Exception as pg_err:
            print(f'[STARTUP] PostgreSQL connection FAILED: {pg_err}')
    init_db()
    # Ensure tracking_events table exists
    from services.tracking import ensure_tracking_table
    ensure_tracking_table()
    if USE_POSTGRES:
        print('[STARTUP] DB initialized: PostgreSQL (Supabase)')
    else:
        print(f'[STARTUP] DB initialized at: {DB_PATH}')
except Exception as e:
    import traceback
    print(f'[STARTUP] DB init failed: {e}')
    traceback.print_exc()


# ==============================
# EMAIL VERIFICATION
# ==============================
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── MX cache with TTL (24h expiry, 1000 domain max) ──
import time as _time

class _MXCache:
    def __init__(self):
        self._d = {}
        self._TTL = 86400  # 24 hours
    def __setitem__(self, k, v):
        if len(self._d) >= 1000:
            oldest = min(self._d, key=lambda x: self._d[x][1])
            del self._d[oldest]
        self._d[k] = (v, _time.time())
    def __getitem__(self, k):
        v, ts = self._d[k]
        if _time.time() - ts > self._TTL:
            del self._d[k]
            raise KeyError(k)
        return v
    def __contains__(self, k):
        try: self[k]; return True
        except KeyError: return False
    def get(self, k, default=None):
        try: return self[k]
        except KeyError: return default
    def clear(self):
        self._d = {}

mx_cache = _MXCache()

def verify_email(email):
    # Handle multiple emails - verify first valid one
    if ';' in email or ',' in email:
        parts = [e.strip().lower() for e in email.replace(',',';').split(';') if '@' in e.strip()]
        if not parts:
            return False, "No valid email found"
        email = parts[0]
    
    try:
        domain = email.split('@')[1]
        
        # Step 1: MX record check (cached)
        if domain in mx_cache:
            mx_valid, mx_reason = mx_cache[domain]
            if not mx_valid:
                return False, mx_reason
        else:
            try:
                mx_records = dns.resolver.resolve(domain, 'MX', lifetime=5)
                mx_hosts = sorted(mx_records, key=lambda x: x.preference)
                mx_cache[domain] = (True, str(mx_hosts[0].exchange).rstrip('.'))
            except dns.resolver.NXDOMAIN:
                mx_cache[domain] = (False, "Domain does not exist")
                return False, "Domain does not exist"
            except dns.resolver.NoAnswer:
                mx_cache[domain] = (False, "No MX record")
                return False, "No MX record"
            except dns.resolver.LifetimeTimeout:
                mx_cache[domain] = (True, domain)
                return True, "Valid - DNS timeout but domain likely exists"
            except Exception as e:
                mx_cache[domain] = (False, f"DNS error: {str(e)[:40]}")
                return False, f"DNS error: {str(e)[:40]}"

        # Step 2: SMTP handshake verification
        mx_host = mx_cache[domain][1]
        
        # Skip SMTP check for known catch-all providers
        catchall_domains = ['gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'live.com', 'icloud.com', 'me.com', 'aol.com', 'protonmail.com', 'proton.me']
        if domain in catchall_domains:
            return True, f"Valid - {domain} (catch-all, unverifiable)"

        try:
            smtp = smtplib.SMTP(timeout=8)
            smtp.connect(mx_host, 25)
            smtp.helo('verify.local')
            smtp.mail('verify@verify.local')
            code, msg = smtp.rcpt(email)
            smtp.quit()
            
            if code == 250:
                return True, "Valid - mailbox exists (SMTP verified)"
            elif code == 550 or code == 551 or code == 553:
                return False, f"Mailbox does not exist ({code})"
            elif code == 452 or code == 421:
                return True, "Valid - server busy but domain OK"
            else:
                return True, f"Likely valid - server responded {code}"
        except smtplib.SMTPServerDisconnected:
            return True, "Valid - MX exists (SMTP blocked)"
        except smtplib.SMTPConnectError:
            return True, "Valid - MX exists (connection refused)"
        except (ConnectionRefusedError, OSError, TimeoutError):
            return True, "Valid - MX exists (port 25 blocked)"
        except Exception as e:
            return True, f"Valid - MX exists ({str(e)[:30]})"

    except Exception as e:
        return False, "Invalid email format"


# ==============================
# OPEN TRACKING
# ==============================
TRACKING_PIXEL = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82'


def inject_tracking_pixel(body, tracking_id, contact_id=None, campaign_id=None, workspace_id=1):
    """Inject tracking pixel + rewrite links. Uses signed tokens when contact/campaign known."""
    from services.tracking import generate_token
    host = get_setting('tracking_host') or 'https://ertyui.online'
    host = host.rstrip('/')

    # Use signed token if we have full context, else legacy UUID
    if contact_id and campaign_id:
        token = generate_token(workspace_id, contact_id, campaign_id, 0, 0)
        pixel_url = f'{host}/track/{token}.png'
    else:
        pixel_url = f'{host}/track/{tracking_id}.png'

    pixel_tag = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="">'
    unsub_url = f'{host}/unsubscribe/{tracking_id}'
    unsub_tag = (
        f'<p style="font-size:11px;color:#94a3b8;margin-top:30px;'
        f'border-top:1px solid #e2e8f0;padding-top:10px;">'
        f'If you no longer wish to receive these emails, '
        f'<a href="{unsub_url}" style="color:#64748b;">unsubscribe here</a>.</p>'
    )

    import re
    def rewrite_link(match):
        original_url = match.group(1)
        # Skip internal tracking/unsubscribe links and non-http links
        if any(skip in original_url for skip in [
            '/track/', '/unsubscribe/', '/click/', 'mailto:', '#', 'javascript:'
        ]):
            return match.group(0)
        click_token = str(uuid.uuid4())
        import urllib.parse
        encoded_url = urllib.parse.quote(original_url, safe='')
        tracked_url = f'{host}/click/{click_token}?url={encoded_url}&tid={tracking_id}'
        return f'href="{tracked_url}"'

    body = re.sub(r'href="(https?://[^"]+)"', rewrite_link, body)

    if '</body>' in body.lower():
        body = body.replace('</body>', f'{unsub_tag}{pixel_tag}</body>')
    else:
        body += unsub_tag + pixel_tag
    return body














def is_unsubscribed(email):
    """Check if email is in suppression list"""
    conn = get_db()
    row = conn.execute("SELECT id FROM unsubscribes WHERE email=?", (email.lower(),)).fetchone()
    conn.close()
    return row is not None


# ==============================
# IMAP REPLY CHECKER (Auto-detect replies)
# ==============================
import imaplib
import email as email_lib
from email.header import decode_header

imap_checker_running = False


def decode_email_header(header):
    """Decode email header (subject, from) to string"""
    if not header:
        return ''
    decoded = decode_header(header)
    parts = []
    for part, charset in decoded:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or 'utf-8', errors='ignore'))
        else:
            parts.append(part)
    return ' '.join(parts)


def extract_email_address(from_header):
    """Extract email from 'Name <email@domain.com>' format"""
    if '<' in from_header and '>' in from_header:
        return from_header.split('<')[1].split('>')[0].strip().lower()
    return from_header.strip().lower()


def check_replies():
    """Check IMAP inbox for new replies — logs to threads+messages AND follow_ups (backward compat)"""
    from services.inbox_service import find_thread_by_email, insert_message, categorize_reply_with_ai

    imap_server = get_setting('imap_server')
    imap_port = int(get_setting('imap_port') or 993)
    imap_username = get_setting('imap_username')
    imap_password = get_setting('imap_password')

    if not all([imap_server, imap_username, imap_password]):
        return 0

    try:
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
        mail.login(imap_username, imap_password)
        mail.select('INBOX')

        status, messages = mail.search(None, 'UNSEEN')
        if status != 'OK' or not messages[0]:
            mail.logout()
            return 0

        email_ids = messages[0].split()
        logged = 0
        conn = get_db()

        for eid in email_ids:
            try:
                status, msg_data = mail.fetch(eid, '(RFC822)')
                if status != 'OK':
                    continue

                msg = email_lib.message_from_bytes(msg_data[0][1])
                from_header = decode_email_header(msg.get('From', ''))
                sender_email = extract_email_address(from_header)
                subject = decode_email_header(msg.get('Subject', ''))
                message_id = msg.get('Message-ID', '').strip()
                in_reply_to = msg.get('In-Reply-To', '').strip()

                # Extract body
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

                # Duplicate check via message_id
                if message_id:
                    already = conn.execute(
                        "SELECT id FROM messages WHERE message_id=?", (message_id,)
                    ).fetchone()
                    if already:
                        continue

                # AI categorize
                ai_category = categorize_reply_with_ai(body_text, subject)

                # Thread system
                thread_id = find_thread_by_email(sender_email, subject, in_reply_to or None)
                insert_message(
                    thread_id=thread_id,
                    direction='incoming',
                    sender_email=sender_email,
                    recipient_email=imap_username,
                    subject=subject,
                    body=body_text,
                    message_id=message_id,
                    in_reply_to=in_reply_to,
                    ai_category=ai_category
                )

                # Update thread status based on AI category
                if ai_category in ('interested', 'meeting'):
                    conn.execute("UPDATE threads SET status=? WHERE id=?", (ai_category, thread_id))

                # Match contact
                contact = conn.execute("SELECT * FROM contacts WHERE email=?", (sender_email,)).fetchone()

                # Lead scoring for reply
                if contact:
                    from services.lead_scoring import update_lead_score
                    if ai_category == 'interested':
                        update_lead_score(contact['id'], 'interested')
                    elif ai_category == 'meeting':
                        update_lead_score(contact['id'], 'meeting')
                    else:
                        update_lead_score(contact['id'], 'reply')

                # Backward compat: also log to follow_ups
                notes = f"Subject: {subject}\n{body_text[:300]}"
                if contact:
                    already_fu = conn.execute(
                        "SELECT id FROM follow_ups WHERE email=? AND notes LIKE ?",
                        (sender_email, f'%{subject[:50]}%')
                    ).fetchone()
                    if not already_fu:
                        conn.execute("""
                            INSERT INTO follow_ups (contact_id, email, name, company, notes)
                            VALUES (?,?,?,?,?)
                        """, (contact['id'], sender_email, contact['name'], contact['company'], notes))
                    # Only mark most recent sent email as replied
                    conn.execute("""
                        UPDATE emails_sent SET replied=1
                        WHERE contact_id=? AND status='sent'
                        AND id = (SELECT id FROM emails_sent WHERE contact_id=? AND status='sent'
                                  ORDER BY sent_at DESC LIMIT 1)
                    """, (contact['id'], contact['id']))
                    conn.execute("UPDATE contacts SET status='replied' WHERE id=?", (contact['id'],))
                else:
                    conn.execute("""
                        INSERT INTO follow_ups (contact_id, email, name, company, notes)
                        VALUES (?,?,?,?,?)
                    """, (0, sender_email, from_header.split('<')[0].strip() or sender_email, 'Unknown', notes))

                conn.commit()
                logged += 1
                app_logger.info(f'REPLY THREADED | From: {sender_email} | Category: {ai_category} | Subject: {subject[:50]}')

            except Exception as e:
                error_logger.error(f'IMAP parse error for email {eid}: {str(e)}')
                continue

        conn.close()
        mail.logout()
        return logged

    except imaplib.IMAP4.error as e:
        error_logger.error(f'IMAP auth/connection error: {str(e)}')
        return 0
    except Exception as e:
        error_logger.error(f'IMAP checker error: {str(e)}')
        return 0


def start_daily_reset():
    """Background thread: reset sent_today at midnight + check warmup upgrades"""
    def run():
        import time as _time
        while True:
            now = datetime.now()
            seconds_until_midnight = ((24 - now.hour - 1) * 3600) + ((60 - now.minute - 1) * 60) + (60 - now.second)
            _time.sleep(seconds_until_midnight)
            reset_daily_counts()
            check_warmup_upgrade()
            app_logger.info('[SMTP ROTATION] Daily reset done + warmup checked')
    t = threading.Thread(target=run, daemon=True)
    t.start()


def start_automation_worker():
    """Background thread: runs automation rules every 30 minutes"""
    def run():
        import time as _time
        _time.sleep(60)  # Wait 1 min after startup before first run
        while True:
            try:
                from services.automation_service import process_automation_rules
                stats = process_automation_rules()
                if any(v > 0 for v in stats.values()):
                    app_logger.info(f'[AUTOMATION WORKER] {stats}')
            except Exception as e:
                error_logger.error(f'[AUTOMATION WORKER] Error: {str(e)}')
            _time.sleep(1800)  # Run every 30 minutes
    t = threading.Thread(target=run, daemon=True)
    t.start()


def start_imap_checker():
    """Background thread that checks for replies periodically"""
    global imap_checker_running
    if imap_checker_running:
        return
    imap_checker_running = True

    def run_checker():
        global imap_checker_running
        app_logger.info('IMAP reply checker started')
        while imap_checker_running:
            try:
                interval = int(get_setting('imap_check_interval') or 180)
                logged = check_replies()
                if logged > 0:
                    app_logger.info(f'IMAP checker: {logged} new replies logged')
            except Exception as e:
                error_logger.error(f'IMAP checker loop error: {str(e)}')
            time.sleep(interval)

    t = threading.Thread(target=run_checker, daemon=True)
    t.start()














# Per-campaign send lock — prevents race condition duplicate sends
_campaign_send_locks = {}
_campaign_send_locks_mutex = threading.Lock()

def _get_campaign_lock(campaign_id):
    with _campaign_send_locks_mutex:
        if campaign_id not in _campaign_send_locks:
            _campaign_send_locks[campaign_id] = threading.Lock()
        return _campaign_send_locks[campaign_id]


# Send progress tracking — keyed by user_id to prevent race conditions
_send_progress_store = {}  # {user_id: progress_dict}

def _get_send_progress(user_id):
    return _send_progress_store.get(str(user_id), {'running': False, 'total': 0, 'done': 0, 'sent': 0, 'failed': 0, 'current': '', 'campaign_id': 0})

def _set_send_progress(user_id, data):
    _send_progress_store[str(user_id)] = data

# Legacy global for backward compat with /api/send_status
send_progress = {'running': False, 'total': 0, 'done': 0, 'sent': 0, 'failed': 0, 'current': '', 'campaign_id': 0}


# ── AI Generated Email Cache (30min TTL, 500 max) ─────────────────
class _AICache:
    def __init__(self):
        self._d = {}
        self._TTL = 1800
    def __setitem__(self, k, v):
        if len(self._d) >= 500:
            oldest = min(self._d, key=lambda x: self._d[x][1])
            del self._d[oldest]
        self._d[k] = (v, _time.time())
    def __getitem__(self, k):
        v, ts = self._d[k]
        if _time.time() - ts > self._TTL:
            del self._d[k]
            raise KeyError(k)
        return v
    def __contains__(self, k):
        try: self[k]; return True
        except KeyError: return False
    def get(self, k, default=None):
        try: return self[k]
        except KeyError: return default
    def pop(self, k, default=None):
        try:
            v = self[k]
            del self._d[k]
            return v
        except KeyError:
            return default

ai_generated_cache = _AICache()


# ── CELERY INTEGRATION ────────────────────────────────────────────
try:
    from celery_app import celery_app as _celery
    CELERY_AVAILABLE = True
except Exception:
    CELERY_AVAILABLE = False

def has_active_workers():
    if not CELERY_AVAILABLE:
        return False
    try:
        insp = _celery.control.inspect(timeout=2)
        active = insp.active_queues()
        return bool(active)
    except Exception:
        return False


# ── AI EMAIL GENERATION ───────────────────────────────────────────
def generate_ai_email(name, company, prompt_template, context='', designation=''):
    """Generate AI email body. Returns (body, None) on success, (None, error) on failure."""
    import requests
    prompt = prompt_template.replace('{name}', name or '').replace('{company}', company or '')
    if context:
        prompt += f"\n\nCompany context: {context}"
    if designation:
        prompt += f"\nRecipient role: {designation}"

    ai_priority = get_setting('ai_priority') or 'groq,gemini'
    providers = [p.strip() for p in ai_priority.split(',')]

    for provider in providers:
        if provider == 'groq':
            body, err = call_groq(prompt)
            if body:
                _log_ai_usage('groq', True)
                return body, None
        elif provider == 'gemini':
            body, err = call_gemini(prompt)
            if body:
                _log_ai_usage('gemini', True)
                return body, None
    return None, 'All AI providers failed'


def call_groq(prompt):
    import requests
    keys_str = get_setting('groq_api_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    if not keys:
        return None, 'No Groq API keys configured'
    for key in keys:
        try:
            resp = requests.post('https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'user', 'content': prompt}],
                      'temperature': 0.7, 'max_tokens': 1000},
                timeout=30)
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content'], None
            if resp.status_code == 429:
                continue
            return None, f'Groq error {resp.status_code}'
        except Exception as e:
            continue
    return None, 'All Groq keys exhausted'


def call_gemini(prompt):
    import requests
    key = get_setting('gemini_api_key') or ''
    if not key:
        return None, 'No Gemini API key'
    try:
        resp = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}',
            json={'contents': [{'parts': [{'text': prompt}]}]},
            timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data['candidates'][0]['content']['parts'][0]['text'], None
        return None, f'Gemini error {resp.status_code}'
    except Exception as e:
        return None, str(e)


def _log_ai_usage(provider, success):
    try:
        conn = get_db()
        conn.execute("INSERT INTO ai_usage (provider, success) VALUES (?,?)", (provider, 1 if success else 0))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── SMTP ROTATION HELPERS (exposed for blueprints) ────────────────
def reset_daily_counts():
    conn = get_db()
    conn.execute("UPDATE smtp_accounts SET sent_today=0")
    conn.commit()
    conn.close()

def check_warmup_upgrade():
    conn = get_db()
    accounts = conn.execute("SELECT id, warmup_stage, health_score FROM smtp_accounts WHERE active=1").fetchall()
    for a in accounts:
        if a['health_score'] >= 90 and a['warmup_stage'] < 5:
            conn.execute("UPDATE smtp_accounts SET warmup_stage=warmup_stage+1 WHERE id=?", (a['id'],))
    conn.commit()
    conn.close()

def mark_send_success(account_id):
    conn = get_db()
    conn.execute("UPDATE smtp_accounts SET sent_today=sent_today+1, last_used=? WHERE id=?", (datetime.now(), account_id))
    conn.commit()
    conn.close()

def mark_send_failure(account_id):
    conn = get_db()
    conn.execute("UPDATE smtp_accounts SET health_score=MAX(0, health_score-5) WHERE id=?", (account_id,))
    conn.commit()
    conn.close()














if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', '0') == '1'
    start_imap_checker()
    start_daily_reset()
    start_automation_worker()
    from services.copilot.autonomous import start_autonomous_worker
    start_autonomous_worker()
    print(f"\n=== Email Campaign Manager ===")
    print(f"Open: http://localhost:{port}")
    print(f"Debug: {debug}")
    print("==============================\n")
    app.run(debug=debug, host='0.0.0.0', port=port)
else:
    # Gunicorn / production WSGI entry
    # Start background workers for production
    try:
        start_imap_checker()
        start_daily_reset()
        start_automation_worker()
        from services.copilot.autonomous import start_autonomous_worker
        start_autonomous_worker()
        print('[STARTUP] Background workers started (gunicorn mode)')
    except Exception as _e:
        print(f'[STARTUP] Background worker start failed: {_e}')
