import os
import sqlite3
import dns.resolver
import smtplib
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, Response
from email.message import EmailMessage
from email.utils import formataddr
import mimetypes
from datetime import datetime
import time
import threading
import uuid
import json
import logging
from logging.handlers import RotatingFileHandler
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Load .env file
load_dotenv()

# ==============================
# BASE PATHS (Azure/Linux compatible)
# ==============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# HARDCODED persistent path for Azure App Service
# /home is the ONLY persistent directory on Azure Linux App Service
DATA_DIR = '/home/data'
LOG_DIR = '/home/logs'
UPLOAD_DIR = '/home/uploads'

# Create directories (will work on Azure, may fail on Windows - that's OK)
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    # Test write permission
    test_file = os.path.join(DATA_DIR, '.write_test')
    with open(test_file, 'w') as f:
        f.write('ok')
    os.remove(test_file)
    print(f'[STARTUP] Using persistent path: {DATA_DIR} (writable)')
except (OSError, PermissionError) as e:
    # Fallback for local Windows dev OR if /home/data not writable
    print(f'[STARTUP] /home/data failed: {e}, falling back to local')
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    LOG_DIR = os.path.join(BASE_DIR, 'logs')
    UPLOAD_DIR = os.path.join(BASE_DIR, 'attachments')
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'CHANGE-ME-generate-with-python-secrets')
DB_PATH = os.path.join(DATA_DIR, 'campaigns.db')

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
    flash('Too many requests! Thoda ruko.', 'error')
    return redirect(url_for('dashboard')), 429


@app.errorhandler(500)
def internal_error(e):
    error_logger.error(f'500 Error: {request.path} - {str(e)}')
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    flash('Something went wrong! Check logs.', 'error')
    return redirect(url_for('dashboard'))


@app.errorhandler(404)
def not_found(e):
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    flash('Page not found!', 'error')
    return redirect(url_for('dashboard'))

# ==============================
# AUTHENTICATION
# ==============================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please login to access this page.'
login_manager.login_message_category = 'error'


class User(UserMixin):
    def __init__(self, id, username, role='admin'):
        self.id = id
        self.username = username
        self.role = role


@login_manager.user_loader
def load_user(user_id):
    try:
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
        if row:
            return User(row['id'], row['username'], row['role'])
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


def get_setting(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        return row[0]
    return DEFAULT_SETTINGS.get(key, '')


def set_setting(key, value):
    conn = get_db()
    existing = conn.execute("SELECT key FROM settings WHERE key=?", (key,)).fetchone()
    if existing:
        conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
    else:
        conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            company TEXT,
            email TEXT UNIQUE NOT NULL,
            designation TEXT,
            priority TEXT,
            status TEXT DEFAULT 'new',
            email_valid INTEGER DEFAULT -1,
            validation_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'draft'
        );

        CREATE TABLE IF NOT EXISTS emails_sent (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER,
            contact_id INTEGER,
            email TEXT NOT NULL,
            subject TEXT,
            body TEXT,
            status TEXT DEFAULT 'pending',
            bounce_reason TEXT,
            opened INTEGER DEFAULT 0,
            replied INTEGER DEFAULT 0,
            tracking_id TEXT,
            sent_at TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        );

        CREATE TABLE IF NOT EXISTS follow_ups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER,
            email TEXT,
            name TEXT,
            company TEXT,
            replied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS ai_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            purpose TEXT DEFAULT 'email',
            success INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS unsubscribes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            reason TEXT DEFAULT '',
            unsubscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()

    # Create default admin user if no users exist
    existing_user = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
    if not existing_user:
        default_hash = generate_password_hash('admin123')
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                     ('admin', default_hash, 'admin'))
        conn.commit()
        print("[AUTH] Default admin created -- username: admin, password: admin123")
        print("[AUTH] WARNING: CHANGE THIS PASSWORD from Settings after first login!")

    # Insert default settings for any missing keys
    for k, v in DEFAULT_SETTINGS.items():
        existing = conn.execute("SELECT key FROM settings WHERE key=?", (k,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", (k, v))
    conn.commit()

    conn.close()


# Initialize DB immediately at module load (before any request can hit load_user)
try:
    init_db()
    print(f'[STARTUP] DB initialized at: {DB_PATH}')
except Exception as e:
    print(f'[STARTUP] DB init failed: {e}')


# ==============================
# EMAIL VERIFICATION
# ==============================
from concurrent.futures import ThreadPoolExecutor, as_completed

# MX cache to avoid re-checking same domain
mx_cache = {}

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


def inject_tracking_pixel(body, tracking_id):
    """Inject invisible 1x1 tracking pixel + unsubscribe link at end of email body"""
    host = get_setting('tracking_host') or 'http://localhost:5000'
    pixel_url = f'{host}/track/{tracking_id}.png'
    pixel_tag = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="">'
    unsub_url = f'{host}/unsubscribe/{tracking_id}'
    unsub_tag = f'<p style="font-size:11px;color:#94a3b8;margin-top:30px;border-top:1px solid #e2e8f0;padding-top:10px;">If you no longer wish to receive these emails, <a href="{unsub_url}" style="color:#64748b;">unsubscribe here</a>.</p>'
    # Add unsubscribe footer
    if '</body>' in body.lower():
        body = body.replace('</body>', f'{unsub_tag}{pixel_tag}</body>')
    else:
        body += unsub_tag + pixel_tag
    return body


@app.route('/track/<tracking_id>.png')
def track_open(tracking_id):
    """1x1 transparent pixel - marks email as opened"""
    conn = get_db()
    conn.execute("UPDATE emails_sent SET opened=1 WHERE tracking_id=?", (tracking_id,))
    conn.commit()
    conn.close()
    return Response(TRACKING_PIXEL, mimetype='image/png', headers={'Cache-Control': 'no-cache, no-store, must-revalidate', 'Pragma': 'no-cache'})


@app.route('/unsubscribe/<tracking_id>', methods=['GET', 'POST'])
def unsubscribe(tracking_id):
    """Public unsubscribe page — no login needed"""
    conn = get_db()
    record = conn.execute("SELECT email FROM emails_sent WHERE tracking_id=?", (tracking_id,)).fetchone()
    if not record:
        conn.close()
        return render_template('unsubscribe.html', success=False, error='Invalid link')

    email = record['email']

    if request.method == 'POST':
        reason = request.form.get('reason', '')
        existing = conn.execute("SELECT id FROM unsubscribes WHERE email=?", (email,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO unsubscribes (email, reason) VALUES (?,?)", (email, reason))
            conn.commit()
        conn.close()
        return render_template('unsubscribe.html', success=True, email=email)

    conn.close()
    return render_template('unsubscribe.html', success=None, email=email, tracking_id=tracking_id)


@app.route('/api/unsubscribes')
@login_required
def api_unsubscribes():
    conn = get_db()
    rows = conn.execute("SELECT * FROM unsubscribes ORDER BY unsubscribed_at DESC").fetchall()
    conn.close()
    return jsonify({'unsubscribes': [{'email': r['email'], 'reason': r['reason'], 'date': r['unsubscribed_at']} for r in rows]})


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
    """Check IMAP inbox for new replies and auto-log them"""
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

        # Search for UNSEEN emails
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
                date_str = msg.get('Date', '')

                # Extract body
                body_text = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == 'text/plain':
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_text = payload.decode('utf-8', errors='ignore')[:500]
                                break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode('utf-8', errors='ignore')[:500]

                # Match sender to our contacts
                contact = conn.execute("SELECT * FROM contacts WHERE email=?", (sender_email,)).fetchone()

                # Check if already logged (avoid duplicates)
                already_logged = conn.execute(
                    "SELECT id FROM follow_ups WHERE email=? AND notes LIKE ?",
                    (sender_email, f'%{subject[:50]}%')
                ).fetchone()
                if already_logged:
                    continue

                # Log the reply
                notes = f"Subject: {subject}\n{body_text[:300]}"
                if contact:
                    conn.execute("""
                        INSERT INTO follow_ups (contact_id, email, name, company, notes)
                        VALUES (?,?,?,?,?)
                    """, (contact['id'], sender_email, contact['name'], contact['company'], notes))
                    conn.execute("UPDATE emails_sent SET replied=1 WHERE contact_id=?", (contact['id'],))
                    conn.execute("UPDATE contacts SET status='replied' WHERE id=?", (contact['id'],))
                else:
                    conn.execute("""
                        INSERT INTO follow_ups (contact_id, email, name, company, notes)
                        VALUES (?,?,?,?,?)
                    """, (0, sender_email, from_header.split('<')[0].strip() or sender_email, 'Unknown', notes))

                conn.commit()
                logged += 1
                app_logger.info(f'REPLY AUTO-LOGGED | From: {sender_email} | Subject: {subject[:50]}')

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


# ==============================
# AUTH ROUTES
# ==============================
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        user_row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user_row and check_password_hash(user_row['password_hash'], password):
            user = User(user_row['id'], user_row['username'], user_row['role'])
            login_user(user, remember=True)
            app_logger.info(f'Login successful: {username}')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        app_logger.warning(f'Login failed: {username} from {request.remote_addr}')
        flash('Invalid username or password!', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('login'))


@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    current_pw = request.form.get('current_password', '')
    new_pw = request.form.get('new_password', '')
    confirm_pw = request.form.get('confirm_password', '')
    if not current_pw or not new_pw:
        flash('All fields required!', 'error')
        return redirect(url_for('settings_page'))
    if new_pw != confirm_pw:
        flash('New passwords do not match!', 'error')
        return redirect(url_for('settings_page'))
    if len(new_pw) < 6:
        flash('Password must be at least 6 characters!', 'error')
        return redirect(url_for('settings_page'))
    conn = get_db()
    user_row = conn.execute("SELECT * FROM users WHERE id=?", (current_user.id,)).fetchone()
    if not check_password_hash(user_row['password_hash'], current_pw):
        flash('Current password is wrong!', 'error')
        conn.close()
        return redirect(url_for('settings_page'))
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(new_pw), current_user.id))
    conn.commit()
    conn.close()
    flash('Password changed successfully! 🔒', 'success')
    return redirect(url_for('settings_page'))


# ==============================
# ROUTES
# ==============================
@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    total_sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    total_bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed')").fetchone()[0]
    total_opened = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1").fetchone()[0]
    total_replied = conn.execute("SELECT COUNT(*) FROM follow_ups").fetchone()[0]
    total_campaigns = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
    invalid_emails = conn.execute("SELECT COUNT(*) FROM contacts WHERE email_valid=0").fetchone()[0]
    valid_emails = conn.execute("SELECT COUNT(*) FROM contacts WHERE email_valid=1").fetchone()[0]

    recent_sent = conn.execute("""
        SELECT es.*, c.name, c.company FROM emails_sent es
        JOIN contacts c ON es.contact_id = c.id
        ORDER BY es.sent_at DESC LIMIT 10
    """).fetchall()

    campaigns = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
    conn.close()

    return render_template('dashboard.html',
        total_contacts=total_contacts, total_sent=total_sent,
        total_bounced=total_bounced, total_opened=total_opened,
        total_replied=total_replied, total_campaigns=total_campaigns,
        invalid_emails=invalid_emails, valid_emails=valid_emails,
        recent_sent=recent_sent, campaigns=campaigns)


@app.route('/add_contact', methods=['POST'])
@login_required
def add_contact():
    name = request.form.get('name', '').strip()
    company = request.form.get('company', '').strip()
    email = request.form.get('email', '').strip().lower()
    designation = request.form.get('designation', '').strip()
    website = request.form.get('website', '').strip()

    if not email or '@' not in email:
        flash('Valid email daalo', 'error')
        return redirect(url_for('upload_contacts'))

    if not name:
        name = email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
    else:
        name = name.strip().title()

    # Auto-generate website from email domain if not provided
    if not website:
        domain = email.split('@')[1]
        if domain not in ['gmail.com','yahoo.com','hotmail.com','outlook.com','live.com','icloud.com','protonmail.com','aol.com']:
            website = f'https://{domain}'

    conn = get_db()
    existing = conn.execute("SELECT id, name FROM contacts WHERE email=?", (email,)).fetchone()
    if existing:
        flash(f'{email} already exists as "{existing["name"]}"!', 'error')
    else:
        conn.execute("INSERT INTO contacts (name, company, email, designation, website) VALUES (?,?,?,?,?)",
                     (name, company, email, designation, website))
        conn.commit()
        flash(f'{name} ({email}) added!', 'success')
    conn.close()
    return redirect(url_for('contacts'))


@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload_contacts():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not file.filename.endswith(('.xlsx', '.xls', '.csv')):
            flash('Please upload Excel or CSV file', 'error')
            return redirect(url_for('upload_contacts'))

        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        # Smart column detection
        col_map = {}
        for col in df.columns:
            cl = col.lower().strip()
            # Name detection
            if not col_map.get('name'):
                if cl in ['name', 'full name', 'fullname', 'contact name', 'person name', 'founder name', 'ceo name', 'first name']:
                    col_map['name'] = col
                elif 'founder' in cl and 'co' not in cl:
                    col_map['name'] = col
                elif 'contact' in cl and 'name' in cl:
                    col_map['name'] = col
            # Company detection
            if not col_map.get('company'):
                if cl in ['company', 'company name', 'startup', 'startup name', 'organization', 'org', 'business', 'brand']:
                    col_map['company'] = col
                elif 'startup' in cl or 'company' in cl or 'organization' in cl or 'business' in cl:
                    col_map['company'] = col
            # Email detection
            if not col_map.get('email'):
                if 'email' in cl and 'secondary' not in cl and 'backup' not in cl and 'alternate' not in cl:
                    col_map['email'] = col
                elif cl in ['mail', 'e-mail', 'email id', 'email address']:
                    col_map['email'] = col
            # Designation detection
            if not col_map.get('designation'):
                if cl in ['designation', 'title', 'role', 'position', 'job title']:
                    col_map['designation'] = col
                elif 'designation' in cl or 'title' in cl or 'role' in cl or 'position' in cl:
                    col_map['designation'] = col
            # Priority detection
            if not col_map.get('priority'):
                if 'priority' in cl or 'importance' in cl or 'tier' in cl:
                    col_map['priority'] = col

        # Fallback: if no name found, check for any column with 'name' in it (but not company/startup)
        if 'name' not in col_map:
            for col in df.columns:
                cl = col.lower().strip()
                if 'name' in cl and 'company' not in cl and 'startup' not in cl and 'org' not in cl:
                    col_map['name'] = col
                    break

        # Fallback: if no email found, auto-detect by checking cell values
        if 'email' not in col_map:
            for col in df.columns:
                sample = df[col].dropna().astype(str).head(5)
                if sample.str.contains('@').any():
                    col_map['email'] = col
                    break

        # Fallback: if no company found, check for URL columns (website = company)
        if 'company' not in col_map:
            for col in df.columns:
                cl = col.lower().strip()
                if 'website' in cl or 'url' in cl or 'site' in cl:
                    col_map['company'] = col
                    break

        if 'email' not in col_map:
            flash('Email column nahi mila! Excel mein email column hona chahiye.', 'error')
            return redirect(url_for('upload_contacts'))

        # Show detected mapping
        mapping_info = ' | '.join([f"{k.upper()}: {v}" for k, v in col_map.items()])
        
        conn = get_db()
        added = 0
        skipped = 0
        skipped_names = []
        for _, row in df.iterrows():
            name = str(row.get(col_map.get('name', ''), '')).strip() if 'name' in col_map else ''
            email = str(row.get(col_map['email'], '')).strip().lower()
            company = str(row.get(col_map.get('company', ''), '')).strip() if 'company' in col_map else ''
            designation = str(row.get(col_map.get('designation', ''), '')).strip() if 'designation' in col_map else ''
            priority = str(row.get(col_map.get('priority', ''), '')).strip() if 'priority' in col_map else ''

            # Fix nan values
            if name.lower() == 'nan': name = ''
            if company.lower() == 'nan': company = ''
            if designation.lower() == 'nan': designation = ''
            if priority.lower() == 'nan': priority = ''

            if not email or '@' not in email:
                skipped += 1
                continue

            # Handle multiple emails separated by ; or ,
            emails_list = [e.strip() for e in email.replace(',', ';').split(';') if '@' in e.strip()]
            if not emails_list:
                skipped += 1
                continue

            for single_email in emails_list:
                single_email = single_email.strip().lower()
                if not single_email or '@' not in single_email:
                    continue

                # If no name, extract from email
                contact_name = name if name else single_email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
                contact_name = contact_name.strip().title()

                existing = conn.execute("SELECT id, name FROM contacts WHERE email=?", (single_email,)).fetchone()
                if existing:
                    skipped += 1
                    skipped_names.append(f"{existing['name']} ({single_email})")
                    continue

                conn.execute(
                    "INSERT INTO contacts (name, company, email, designation, priority) VALUES (?,?,?,?,?)",
                    (contact_name, company, single_email, designation, priority)
                )
                added += 1

        conn.commit()
        conn.close()
        skip_info = ''
        if skipped_names:
            if len(skipped_names) <= 10:
                skip_info = ' | Duplicates: ' + ', '.join(skipped_names)
            else:
                skip_info = f' | Duplicates: {', '.join(skipped_names[:10])}... +{len(skipped_names)-10} more'
        flash(f'{added} contacts added, {skipped} skipped (duplicate/invalid){skip_info} | Detected: {mapping_info}', 'success')
        app_logger.info(f'Upload: {added} added, {skipped} skipped | File: {file.filename} | by {current_user.username}')
        return redirect(url_for('contacts'))

    return render_template('upload.html')


@app.route('/api/contact/<int:contact_id>')
@login_required
def api_get_contact(contact_id):
    conn = get_db()
    c = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    conn.close()
    if not c:
        return jsonify({'error': 'Not found'})
    return jsonify({'name': c['name'], 'company': c['company'], 'email': c['email'], 'designation': c['designation'] or '', 'context': c['context'] if 'context' in c.keys() else '', 'website': c['website'] if 'website' in c.keys() else ''})


@app.route('/contact/edit/<int:contact_id>', methods=['POST'])
@login_required
def edit_contact(contact_id):
    name = request.form.get('name', '').strip().title()
    company = request.form.get('company', '').strip()
    email = request.form.get('email', '').strip().lower()
    designation = request.form.get('designation', '').strip()
    context = request.form.get('context', '').strip()
    website = request.form.get('website', '').strip()
    conn = get_db()
    conn.execute("UPDATE contacts SET name=?, company=?, email=?, designation=?, context=?, website=? WHERE id=?",
                 (name, company, email, designation, context, website, contact_id))
    conn.commit()
    conn.close()
    flash('Contact updated!', 'success')
    return redirect(url_for('contacts'))


@app.route('/api/contact/delete/<int:contact_id>', methods=['DELETE'])
@login_required
def delete_contact(contact_id):
    conn = get_db()
    conn.execute("DELETE FROM emails_sent WHERE contact_id=?", (contact_id,))
    conn.execute("DELETE FROM follow_ups WHERE contact_id=?", (contact_id,))
    conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/campaign/delete/<int:campaign_id>', methods=['DELETE'])
@login_required
def delete_campaign(campaign_id):
    conn = get_db()
    conn.execute("DELETE FROM emails_sent WHERE campaign_id=?", (campaign_id,))
    conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/contacts')
@login_required
def contacts():
    conn = get_db()
    filter_type = request.args.get('filter', 'all')

    if filter_type == 'valid':
        rows = conn.execute("SELECT * FROM contacts WHERE email_valid=1 ORDER BY created_at DESC").fetchall()
    elif filter_type == 'invalid':
        rows = conn.execute("SELECT * FROM contacts WHERE email_valid=0 ORDER BY created_at DESC").fetchall()
    elif filter_type == 'new':
        rows = conn.execute("SELECT * FROM contacts WHERE status='new' ORDER BY created_at DESC").fetchall()
    elif filter_type == 'sent':
        rows = conn.execute("SELECT * FROM contacts WHERE status='sent' ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM contacts ORDER BY created_at DESC").fetchall()

    conn.close()
    return render_template('contacts.html', contacts=rows, filter_type=filter_type)


# Verification progress tracking
verify_progress = {'running': False, 'total': 0, 'done': 0, 'current_email': ''}

ATTACHMENT_DIR = UPLOAD_DIR  # already set above


@app.route('/verify_emails', methods=['POST'])
@login_required
@limiter.limit("3 per minute")
def verify_emails_route():
    global verify_progress
    if verify_progress['running']:
        return redirect(url_for('contacts'))

    reverify = request.form.get('reverify', '0')

    def run_verify(reverify_flag):
        global verify_progress, mx_cache
        mx_cache = {}  # Reset cache
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")

        if reverify_flag == '1':
            contacts_list = conn.execute("SELECT id, email FROM contacts").fetchall()
        else:
            contacts_list = conn.execute("SELECT id, email FROM contacts WHERE email_valid=-1").fetchall()

        verify_progress = {'running': True, 'total': len(contacts_list), 'done': 0, 'current_email': ''}

        def verify_one(contact):
            valid, reason = verify_email(contact['email'])
            return contact['id'], contact['email'], valid, reason

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(verify_one, c): c for c in contacts_list}
            for future in as_completed(futures):
                cid, email, valid, reason = future.result()
                verify_progress['current_email'] = email
                conn.execute("UPDATE contacts SET email_valid=?, validation_reason=? WHERE id=?",
                             (1 if valid else 0, reason, cid))
                conn.commit()
                verify_progress['done'] += 1

        conn.close()
        verify_progress['running'] = False
        verify_progress['current_email'] = ''

    t = threading.Thread(target=run_verify, args=(reverify,))
    t.start()
    return redirect(url_for('verify_progress_page'))


@app.route('/verify_progress')
@login_required
def verify_progress_page():
    return render_template('verify_progress.html')


@app.route('/api/verify_single/<int:contact_id>')
@login_required
def api_verify_single(contact_id):
    conn = get_db()
    contact = conn.execute("SELECT id, email FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not contact:
        conn.close()
        return jsonify({'valid': False, 'reason': 'Contact not found'})
    valid, reason = verify_email(contact['email'])
    conn.execute("UPDATE contacts SET email_valid=?, validation_reason=? WHERE id=?",
                 (1 if valid else 0, reason, contact['id']))
    conn.commit()
    conn.close()
    return jsonify({'valid': valid, 'reason': reason})


@app.route('/api/fetch_context/<int:contact_id>')
@login_required
def api_fetch_context(contact_id):
    conn = get_db()
    contact = conn.execute("SELECT id, name, company FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not contact:
        conn.close()
        return jsonify({'success': False, 'error': 'Not found'})

    api_key = get_setting('gemini_api_key')
    if not api_key:
        conn.close()
        return jsonify({'success': False, 'error': 'No API key'})

    prompt = f"""In 1-2 short bullet points, tell me the latest publicly known context about {contact['company']}.
Include: what they do, recent funding/news, tech stack, or growth stage.
Only use WELL KNOWN facts. If unsure, say what the company likely does based on name.
Keep it under 50 words. No fluff. Plain text, no markdown."""

    try:
        r = http_requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}',
            json={'contents': [{'parts': [{'text': prompt}]}]},
            timeout=15
        )
        if r.status_code == 200:
            text = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            conn.execute("UPDATE contacts SET context=? WHERE id=?", (text, contact_id))
            # Track AI usage
            conn.execute("INSERT INTO ai_usage (provider, purpose, success) VALUES ('gemini','research',1)")
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'context': text})
        else:
            conn.close()
            return jsonify({'success': False, 'error': f'API {r.status_code}'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)[:50]})


@app.route('/api/fetch_all_context', methods=['POST'])
@login_required
def api_fetch_all_context():
    contact_ids = request.json.get('contact_ids', [])
    results = []
    api_key = get_setting('gemini_api_key')
    if not api_key:
        return jsonify({'results': [], 'error': 'No API key'})

    conn = get_db()
    for cid in contact_ids:
        contact = conn.execute("SELECT name, company FROM contacts WHERE id=?", (cid,)).fetchone()
        if not contact:
            continue

        prompt = f"In 1-2 short bullet points (under 50 words), what does {contact['company']} do? Any recent funding or news? Only well-known facts. Plain text."
        try:
            r = http_requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}',
                json={'contents': [{'parts': [{'text': prompt}]}]},
                timeout=15
            )
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                conn.execute("UPDATE contacts SET context=? WHERE id=?", (text, cid))
                conn.commit()
                results.append({'id': cid, 'context': text})
            time.sleep(1)
        except:
            pass
    conn.close()
    return jsonify({'results': results})


@app.route('/api/enrich_all', methods=['POST'])
@login_required
@limiter.limit("3 per minute")
def api_enrich_all():
    """Enrich contacts - scrape website + AI summarize"""
    force = request.json.get('force', False) if request.json else False
    conn = get_db()
    if force:
        contacts_list = conn.execute("SELECT id, name, company, email FROM contacts WHERE email_valid=1").fetchall()
    else:
        contacts_list = conn.execute("SELECT id, name, company, email FROM contacts WHERE (context IS NULL OR context='') AND email_valid=1").fetchall()
    enriched = 0
    failed = 0

    for contact in contacts_list:
        domain = contact['email'].split('@')[1] if '@' in contact['email'] else ''
        company = contact['company'] or domain

        # Step 1: Try to scrape website
        website_text = ''
        if domain:
            try:
                r = http_requests.get(f'https://{domain}', timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
                if r.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(r.text, 'html.parser')
                    # Get meta description + title + first few paragraphs
                    title = soup.title.string if soup.title else ''
                    meta_desc = ''
                    meta = soup.find('meta', attrs={'name': 'description'})
                    if meta:
                        meta_desc = meta.get('content', '')
                    paragraphs = ' '.join([p.get_text() for p in soup.find_all('p')[:5]])
                    website_text = f"Title: {title}. Description: {meta_desc}. Content: {paragraphs[:500]}"
            except:
                pass

        # Step 2: AI summarize
        prompt = f"""In 2-3 bullet points (under 60 words), summarize what {company} does.
{'Website data: ' + website_text[:600] if website_text else 'Use only well-known public facts.'}
Include: what they do, any known funding/stage, tech focus. Plain text only."""

        body, error = generate_ai_email.__wrapped__(contact['name'], company, prompt) if False else (None, None)
        # Use AI priority chain
        try:
            priority = (get_setting('ai_priority') or 'ollama,groq,gemini').split(',')
            result_text = None
            for provider in priority:
                provider = provider.strip().lower()
                if provider == 'ollama':
                    result_text, err = call_ollama(prompt)
                elif provider == 'groq':
                    result_text, err = call_groq(prompt)
                elif provider == 'gemini':
                    result_text, err = call_gemini(prompt)
                if result_text:
                    break

            if result_text:
                conn.execute("UPDATE contacts SET context=? WHERE id=?", (result_text.strip(), contact['id']))
                conn.commit()
                enriched += 1
            else:
                failed += 1
        except:
            failed += 1

        time.sleep(1.5)

    conn.close()
    return jsonify({'enriched': enriched, 'failed': failed, 'total': len(contacts_list)})


# Store AI generated emails for preview->send flow
ai_generated_cache = {}


@app.route('/api/generate_email', methods=['POST'])
@login_required
def api_generate_email():
    name = request.json.get('name', '')
    company = request.json.get('company', '')
    contact_id = request.json.get('contact_id', '')
    prompt_template = get_setting('email_prompt')
    
    context = ''
    designation = ''
    if contact_id:
        conn = get_db()
        row = conn.execute("SELECT context, designation FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if row:
            context = row['context'] or ''
            designation = row['designation'] or ''
        conn.close()
    
    if not context:
        return jsonify({'success': False, 'error': 'Context nahi hai! Pehle "Fetch Context" karo.'})
    
    body, error = generate_ai_email(name, company, prompt_template, context, designation)
    if body:
        # Cache for later send
        ai_generated_cache[str(contact_id)] = body
        return jsonify({'success': True, 'body': body})
    return jsonify({'success': False, 'error': error})


@app.route('/api/generate_all', methods=['POST'])
@login_required
def api_generate_all():
    contact_ids = request.json.get('contact_ids', [])
    prompt_template = get_setting('email_prompt')
    results = []
    conn = get_db()
    for cid in contact_ids:
        contact = conn.execute("SELECT name, company FROM contacts WHERE id=?", (cid,)).fetchone()
        if contact:
            body, error = generate_ai_email(contact['name'], contact['company'], prompt_template)
            results.append({'id': cid, 'name': contact['name'], 'body': body or f'Error: {error}'})
            import time; time.sleep(1)
    conn.close()
    return jsonify({'results': results})


@app.route('/api/verify_status')
@login_required
def api_verify_status():
    conn = get_db()
    all_contacts = conn.execute(
        "SELECT id, name, email, email_valid, validation_reason FROM contacts ORDER BY id"
    ).fetchall()
    conn.close()
    results = [{'id': r['id'], 'name': r['name'], 'email': r['email'], 'valid': r['email_valid'], 'reason': r['validation_reason'] or ''} for r in all_contacts]
    return jsonify({
        'running': verify_progress['running'],
        'total': verify_progress['total'],
        'done': verify_progress['done'],
        'current_email': verify_progress['current_email'],
        'results': results
    })


@app.route('/api/ai_usage')
@login_required
def api_ai_usage():
    conn = get_db()
    # By provider
    by_provider = conn.execute("""
        SELECT provider, COUNT(*) as total, SUM(success) as success 
        FROM ai_usage GROUP BY provider
    """).fetchall()
    # By date
    by_date = conn.execute("""
        SELECT DATE(created_at) as day, provider, COUNT(*) as total 
        FROM ai_usage GROUP BY day, provider ORDER BY day
    """).fetchall()
    conn.close()
    return jsonify({
        'by_provider': [{'provider': r['provider'], 'total': r['total'], 'success': r['success']} for r in by_provider],
        'by_date': [{'day': r['day'], 'provider': r['provider'], 'total': r['total']} for r in by_date]
    })


@app.route('/campaigns')
@login_required
def campaigns_list():
    conn = get_db()
    campaigns = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template('campaigns.html', campaigns=campaigns)


@app.route('/campaign/new', methods=['GET', 'POST'])
@login_required
def new_campaign():
    if request.method == 'POST':
        name = request.form.get('campaign_name', 'Untitled Campaign')
        description = request.form.get('description', '')
        conn = get_db()
        conn.execute("INSERT INTO campaigns (name, description) VALUES (?,?)", (name, description))
        conn.commit()
        campaign_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    return render_template('new_campaign.html')


@app.route('/campaign/edit/<int:campaign_id>', methods=['POST'])
@login_required
def edit_campaign(campaign_id):
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    conn = get_db()
    conn.execute("UPDATE campaigns SET name=?, description=? WHERE id=?", (name, description, campaign_id))
    conn.commit()
    conn.close()
    flash('Campaign updated!', 'success')
    return redirect(url_for('campaigns_list'))


@app.route('/campaign/<int:campaign_id>')
@login_required
def campaign_detail(campaign_id):
    conn = get_db()
    campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    emails = conn.execute("""
        SELECT es.*, c.name, c.company FROM emails_sent es
        JOIN contacts c ON es.contact_id = c.id
        WHERE es.campaign_id=? ORDER BY es.sent_at DESC
    """, (campaign_id,)).fetchall()

    # Get contacts not yet successfully sent in THIS campaign
    available = conn.execute("""
        SELECT * FROM contacts WHERE email_valid=1
        AND id NOT IN (SELECT contact_id FROM emails_sent WHERE campaign_id=? AND status='sent')
    """, (campaign_id,)).fetchall()

    conn.close()
    return render_template('campaign_detail.html', campaign=campaign, emails=emails, available=available)


@app.route('/campaign/<int:campaign_id>/send', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def send_campaign(campaign_id):
    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    body_template = request.form.get('body', '')
    contact_ids = request.form.getlist('contact_ids')

    # Handle file upload
    attachment_filename = ''
    uploaded_file = request.files.get('attachment_file')
    if uploaded_file and uploaded_file.filename:
        from werkzeug.utils import secure_filename
        filename = secure_filename(uploaded_file.filename)
        filepath = os.path.join(ATTACHMENT_DIR, filename)
        uploaded_file.save(filepath)
        attachment_filename = filename
    else:
        attachment_filename = request.form.get('attachment', '')

    print(f'[SEND] Campaign {campaign_id} | Subject: {subject_template} | Contacts: {contact_ids} | Body length: {len(body_template)}')

    if not contact_ids:
        flash('Koi contact select nahi kiya! Pehle checkbox tick karo.', 'error')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    app_logger.info(f'Campaign {campaign_id} send started | {len(contact_ids)} contacts | by {current_user.username}')
    conn = get_db()
    sent = 0
    failed = 0

    # Load SMTP settings from DB
    smtp_server = get_setting('smtp_server')
    smtp_port = int(get_setting('smtp_port') or 587)
    smtp_username = get_setting('smtp_username')
    smtp_password = get_setting('smtp_password')
    from_email = get_setting('from_email') or smtp_username
    from_name = get_setting('from_name')
    reply_to = get_setting('reply_to')
    bcc = get_setting('bcc_emails')

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)

        for idx, cid in enumerate(contact_ids):
            if idx > 0 and idx % 10 == 0:
                try: server.quit()
                except: pass
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
                server.login(smtp_username, smtp_password)

            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
            if not contact:
                continue

            # Check suppression list
            if is_unsubscribed(contact['email']):
                continue

            # Check duplicate in THIS campaign only
            already = conn.execute(
                "SELECT id FROM emails_sent WHERE contact_id=? AND campaign_id=? AND status='sent'", (cid, campaign_id)
            ).fetchone()
            if already:
                continue

            subject = subject_template.replace('{company}', contact['company'] or '')
            subject = subject.replace('{name}', contact['name'] or '')
            body = body_template.replace('{company}', contact['company'] or '')
            body = body.replace('{name}', contact['name'] or '')

            try:
                tracking_id = str(uuid.uuid4())
                tracked_body = inject_tracking_pixel(body, tracking_id)

                msg = EmailMessage()
                msg['Subject'] = subject
                msg['From'] = formataddr((from_name, from_email))
                msg['To'] = contact['email']
                msg['Reply-To'] = reply_to
                if bcc and bcc.strip():
                    msg['Bcc'] = bcc
                msg.add_alternative(tracked_body, subtype='html')

                if attachment_filename and os.path.exists(os.path.join(ATTACHMENT_DIR, attachment_filename)):
                    filepath = os.path.join(ATTACHMENT_DIR, attachment_filename)
                    mime_type, _ = mimetypes.guess_type(filepath)
                    if mime_type:
                        maintype, subtype = mime_type.split('/', 1)
                    else:
                        maintype, subtype = 'application', 'octet-stream'
                    with open(filepath, 'rb') as f:
                        msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                                         filename=os.path.basename(filepath))

                server.send_message(msg)
                conn.execute("""
                    INSERT INTO emails_sent (campaign_id, contact_id, email, subject, body, status, tracking_id, sent_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (campaign_id, cid, contact['email'], subject, body, 'sent', tracking_id, datetime.now()))
                conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (cid,))
                conn.commit()
                sent += 1
                smtp_logger.info(f'SENT | Campaign {campaign_id} | To: {contact["email"]} | Subject: {subject[:50]}')
                time.sleep(5)

            except smtplib.SMTPRecipientsRefused as e:
                conn.execute("""
                    INSERT INTO emails_sent (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (campaign_id, cid, contact['email'], subject, body, 'bounced', str(e), datetime.now()))
                conn.commit()
                failed += 1
                smtp_logger.warning(f'BOUNCED | {contact["email"]} | {str(e)[:100]}')

            except Exception as e:
                conn.execute("""
                    INSERT INTO emails_sent (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (campaign_id, cid, contact['email'], subject, body, 'failed', str(e), datetime.now()))
                conn.commit()
                failed += 1
                smtp_logger.error(f'FAILED | {contact["email"]} | {str(e)[:100]}')
                error_logger.error(f'Send failed for {contact["email"]}: {str(e)}')

        try: server.quit()
        except: pass
    except Exception as e:
        flash(f'SMTP Error: {e}', 'error')
        error_logger.error(f'SMTP connection error in campaign {campaign_id}: {str(e)}')
        conn.close()
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    if sent > 0:
        conn.execute("UPDATE campaigns SET status='sent' WHERE id=?", (campaign_id,))
    conn.commit()
    conn.close()
    flash(f'Sent: {sent}, Failed: {failed}', 'success')
    return redirect(url_for('campaign_detail', campaign_id=campaign_id))


@app.route('/retry/<int:email_id>', methods=['POST'])
@login_required
def retry_email(email_id):
    conn = get_db()
    record = conn.execute("SELECT * FROM emails_sent WHERE id=?", (email_id,)).fetchone()
    if not record:
        flash('Email record not found', 'error')
        conn.close()
        return redirect(url_for('dashboard'))

    # Check if already sent successfully in same campaign
    already_sent = conn.execute(
        "SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent' AND id!=?",
        (record['email'], record['campaign_id'], email_id)
    ).fetchone()
    if already_sent:
        flash(f'{record["email"]} already sent in this campaign!', 'error')
        conn.close()
        return redirect(url_for('campaign_detail', campaign_id=record['campaign_id']))

    smtp_server = get_setting('smtp_server')
    smtp_port = int(get_setting('smtp_port'))
    smtp_username = get_setting('smtp_username')
    smtp_password = get_setting('smtp_password')
    from_email = get_setting('from_email')
    from_name = get_setting('from_name')
    reply_to = get_setting('reply_to')
    bcc = get_setting('bcc_emails')

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)

        msg = EmailMessage()
        msg['Subject'] = record['subject']
        msg['From'] = formataddr((from_name, from_email))
        msg['To'] = record['email']
        msg['Reply-To'] = reply_to
        msg['Bcc'] = bcc
        msg.add_alternative(record['body'], subtype='html')

        server.send_message(msg)
        server.quit()

        conn.execute("UPDATE emails_sent SET status='sent', bounce_reason=NULL, sent_at=? WHERE id=?",
                     (datetime.now(), email_id))
        conn.commit()
        flash(f'Retry successful! Email sent to {record["email"]}', 'success')
    except Exception as e:
        conn.execute("UPDATE emails_sent SET bounce_reason=? WHERE id=?", (str(e), email_id))
        conn.commit()
        flash(f'Retry failed: {str(e)[:100]}', 'error')

    conn.close()
    return redirect(url_for('campaign_detail', campaign_id=record['campaign_id']))


@app.route('/api/retry/<int:email_id>', methods=['POST'])
@login_required
def api_retry_email(email_id):
    """AJAX retry - returns JSON with loader support"""
    conn = get_db()
    record = conn.execute("SELECT * FROM emails_sent WHERE id=?", (email_id,)).fetchone()
    if not record:
        conn.close()
        return jsonify({'success': False, 'error': 'Not found'})

    # Duplicate protection - don't resend if already sent in same campaign
    already_sent = conn.execute(
        "SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent' AND id!=?",
        (record['email'], record['campaign_id'], email_id)
    ).fetchone()
    if already_sent:
        conn.close()
        return jsonify({'success': False, 'error': 'Already sent in this campaign'})

    smtp_server = get_setting('smtp_server')
    smtp_port = int(get_setting('smtp_port'))
    smtp_username = get_setting('smtp_username')
    smtp_password = get_setting('smtp_password')
    from_email = get_setting('from_email')
    from_name = get_setting('from_name')
    reply_to = get_setting('reply_to')
    bcc = get_setting('bcc_emails')

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)

        msg = EmailMessage()
        msg['Subject'] = record['subject']
        msg['From'] = formataddr((from_name, from_email))
        msg['To'] = record['email']
        msg['Reply-To'] = reply_to
        msg['Bcc'] = bcc
        msg.add_alternative(record['body'], subtype='html')

        server.send_message(msg)
        server.quit()

        conn.execute("UPDATE emails_sent SET status='sent', bounce_reason=NULL, sent_at=? WHERE id=?",
                     (datetime.now(), email_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        conn.execute("UPDATE emails_sent SET bounce_reason=? WHERE id=?", (str(e)[:200], email_id))
        conn.commit()
        conn.close()
        return jsonify({'success': False, 'error': str(e)[:100]})


# Send progress tracking
send_progress = {'running': False, 'total': 0, 'done': 0, 'sent': 0, 'failed': 0, 'current': '', 'campaign_id': 0}


@app.route('/api/send_status')
@login_required
def api_send_status():
    conn = get_db()
    recent = []
    if send_progress['campaign_id']:
        rows = conn.execute("""
            SELECT es.email, es.status, es.bounce_reason, c.name, c.company 
            FROM emails_sent es JOIN contacts c ON es.contact_id=c.id 
            WHERE es.campaign_id=? ORDER BY es.sent_at DESC LIMIT 50
        """, (send_progress['campaign_id'],)).fetchall()
        recent = [{'name': r['name'], 'company': r['company'], 'email': r['email'], 'status': r['status'], 'reason': r['bounce_reason'] or ''} for r in rows]
    conn.close()
    return jsonify({
        'running': send_progress['running'],
        'total': send_progress['total'],
        'done': send_progress['done'],
        'sent': send_progress['sent'],
        'failed': send_progress['failed'],
        'current': send_progress['current'],
        'recent': recent
    })


@app.route('/campaign/<int:campaign_id>/send_ai', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def send_campaign_ai(campaign_id):
    global send_progress
    if send_progress['running']:
        flash('Sending already in progress!', 'error')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    attachment = request.form.get('attachment', '')
    contact_ids = request.form.getlist('contact_ids')
    
    if not contact_ids:
        flash('Koi contact select nahi kiya!', 'error')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    def run_send_ai():
        global send_progress
        prompt_template = get_setting('email_prompt')
        smtp_server = get_setting('smtp_server')
        smtp_port = int(get_setting('smtp_port'))
        smtp_username = get_setting('smtp_username')
        smtp_password = get_setting('smtp_password')
        from_email = get_setting('from_email')
        from_name = get_setting('from_name')
        reply_to = get_setting('reply_to')
        bcc = get_setting('bcc_emails')

        send_progress = {'running': True, 'total': len(contact_ids), 'done': 0, 'sent': 0, 'failed': 0, 'current': '', 'campaign_id': campaign_id}
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")

        server = None
        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(smtp_username, smtp_password)
        except Exception as e:
            send_progress['running'] = False
            return

        for idx, cid in enumerate(contact_ids):
            if idx > 0 and idx % 10 == 0:
                try: server.quit()
                except: pass
                try:
                    server = smtplib.SMTP(smtp_server, smtp_port)
                    server.starttls()
                    server.login(smtp_username, smtp_password)
                except: pass

            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
            if not contact: continue

            # Check suppression list
            if is_unsubscribed(contact['email']):
                send_progress['done'] += 1
                continue

            already = conn.execute("SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent'", (contact['email'], campaign_id)).fetchone()
            if already:
                send_progress['done'] += 1
                continue

            send_progress['current'] = f"{contact['name']} ({contact['email']})"
            subject = subject_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')

            if str(cid) in ai_generated_cache:
                body = ai_generated_cache.pop(str(cid))
            else:
                context = contact['context'] if 'context' in contact.keys() else ''
                if not context:
                    conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at) VALUES (?,?,?,?,?,?,?,?)",
                        (campaign_id, cid, contact['email'], subject, '', 'failed', 'No context - fetch context first', datetime.now()))
                    conn.commit()
                    send_progress['done'] += 1
                    send_progress['failed'] += 1
                    continue
                designation = contact['designation'] if 'designation' in contact.keys() else ''
                body, error = generate_ai_email(contact['name'], contact['company'], prompt_template, context, designation)
                if not body:
                    conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at) VALUES (?,?,?,?,?,?,?,?)",
                        (campaign_id, cid, contact['email'], subject, '', 'failed', f'AI: {error}', datetime.now()))
                    conn.commit()
                    send_progress['done'] += 1
                    send_progress['failed'] += 1
                    continue

            try:
                tracking_id = str(uuid.uuid4())
                tracked_body = inject_tracking_pixel(body, tracking_id)

                msg = EmailMessage()
                msg['Subject'] = subject
                msg['From'] = formataddr((from_name, from_email))
                msg['To'] = contact['email']
                msg['Reply-To'] = reply_to
                msg['Bcc'] = bcc
                msg.add_alternative(tracked_body, subtype='html')

                if attachment and os.path.exists(os.path.join(ATTACHMENT_DIR, attachment)):
                    filepath = os.path.join(ATTACHMENT_DIR, attachment)
                    mt, _ = mimetypes.guess_type(filepath)
                    maintype, subtype = (mt.split('/', 1) if mt else ('application', 'octet-stream'))
                    with open(filepath, 'rb') as f:
                        msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(filepath))

                server.send_message(msg)
                conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,tracking_id,sent_at) VALUES (?,?,?,?,?,?,?,?)",
                    (campaign_id, cid, contact['email'], subject, body, 'sent', tracking_id, datetime.now()))
                conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (cid,))
                conn.commit()
                send_progress['sent'] += 1
            except Exception as e:
                conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at) VALUES (?,?,?,?,?,?,?,?)",
                    (campaign_id, cid, contact['email'], subject, body, 'failed', str(e)[:200], datetime.now()))
                conn.commit()
                send_progress['failed'] += 1

            send_progress['done'] += 1
            time.sleep(5)

        try: server.quit()
        except: pass
        if send_progress['sent'] > 0:
            conn.execute("UPDATE campaigns SET status='sent' WHERE id=?", (campaign_id,))
            conn.commit()
        conn.close()
        send_progress['running'] = False
        send_progress['current'] = ''

    t = threading.Thread(target=run_send_ai)
    t.start()
    return redirect(url_for('send_progress_page', campaign_id=campaign_id))


@app.route('/campaign/<int:campaign_id>/sending')
@login_required
def send_progress_page(campaign_id):
    return render_template('send_progress.html', campaign_id=campaign_id)


@app.route('/follow_ups')
@login_required
def follow_ups():
    conn = get_db()
    rows = conn.execute("SELECT * FROM follow_ups ORDER BY replied_at DESC").fetchall()
    conn.close()
    return render_template('follow_ups.html', follow_ups=rows)


@app.route('/api/check_replies', methods=['POST'])
@login_required
@limiter.limit("3 per minute")
def api_check_replies():
    """Manually trigger IMAP reply check"""
    try:
        logged = check_replies()
        return jsonify({'success': True, 'logged': logged})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})


@app.route('/api/imap_status')
@login_required
def api_imap_status():
    """Check if IMAP checker is configured and running"""
    imap_server = get_setting('imap_server')
    imap_username = get_setting('imap_username')
    configured = bool(imap_server and imap_username)
    return jsonify({
        'configured': configured,
        'running': imap_checker_running,
        'server': imap_server or 'Not set',
        'username': imap_username or 'Not set',
        'interval': get_setting('imap_check_interval') or '180'
    })


@app.route('/api/smtp_test')
@login_required
def api_smtp_test():
    """Test SMTP connection and show current settings (for debugging)"""
    smtp_server = get_setting('smtp_server')
    smtp_port = get_setting('smtp_port')
    smtp_username = get_setting('smtp_username')
    smtp_password = get_setting('smtp_password')
    from_email = get_setting('from_email')
    tracking_host = get_setting('tracking_host')

    result = {
        'smtp_server': smtp_server or 'NOT SET',
        'smtp_port': smtp_port or 'NOT SET',
        'smtp_username': smtp_username or 'NOT SET',
        'smtp_password_set': bool(smtp_password),
        'from_email': from_email or 'NOT SET',
        'tracking_host': tracking_host or 'NOT SET',
        'db_path': DB_PATH,
        'connection_test': None
    }

    if not all([smtp_server, smtp_port, smtp_username, smtp_password]):
        result['connection_test'] = 'FAILED - Missing SMTP settings'
        return jsonify(result)

    try:
        server = smtplib.SMTP(smtp_server, int(smtp_port), timeout=10)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.quit()
        result['connection_test'] = 'SUCCESS - Connected and authenticated'
    except Exception as e:
        result['connection_test'] = f'FAILED - {str(e)[:200]}'
        error_logger.error(f'SMTP test failed: {str(e)}')

    return jsonify(result)


@app.route('/api/fix_tracking_host')
@login_required
def fix_tracking_host():
    """One-time fix: set tracking_host to production URL"""
    set_setting('tracking_host', 'https://ertyui.online')
    return jsonify({'success': True, 'tracking_host': 'https://ertyui.online'})


@app.route('/follow_up/add', methods=['POST'])
@login_required
def add_follow_up():
    email = request.form.get('email', '').strip().lower()
    notes = request.form.get('notes', '')
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE email=?", (email,)).fetchone()

    if contact:
        conn.execute("""
            INSERT INTO follow_ups (contact_id, email, name, company, notes)
            VALUES (?,?,?,?,?)
        """, (contact['id'], email, contact['name'], contact['company'], notes))
        conn.execute("UPDATE emails_sent SET replied=1 WHERE contact_id=?", (contact['id'],))
    else:
        conn.execute("""
            INSERT INTO follow_ups (contact_id, email, name, company, notes)
            VALUES (?,?,?,?,?)
        """, (0, email, 'Unknown', 'Unknown', notes))

    conn.commit()
    conn.close()
    flash(f'Follow-up added for {email}', 'success')
    return redirect(url_for('follow_ups'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_page():
    if request.method == 'POST':
        for key in DEFAULT_SETTINGS.keys():
            val = request.form.get(key, '')
            set_setting(key, val)
        flash('Settings saved!', 'success')
        return redirect(url_for('settings_page'))

    current = {}
    for key in DEFAULT_SETTINGS.keys():
        current[key] = get_setting(key)
    return render_template('settings.html', settings=current)


import requests as http_requests

# Groq key rotation
groq_key_index = 0


def call_ollama(prompt):
    """Ollama disabled in production (no GPU on Azure). Returns None."""
    app_logger.info('Ollama skipped — disabled in production')
    return None, 'Ollama disabled in production'


# Store latest rate limit info per Groq key
groq_rate_limits = {}

def call_groq(prompt, max_retries=2):
    global groq_key_index, groq_rate_limits
    keys_str = get_setting('groq_api_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    if not keys:
        return None, 'No Groq keys configured'

    for i in range(len(keys)):
        key = keys[(groq_key_index + i) % len(keys)]
        for attempt in range(max_retries):
            try:
                r = http_requests.post('https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                    json={'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 1000},
                    timeout=45)
                groq_rate_limits[key[-8:]] = {
                    'limit_requests': r.headers.get('x-ratelimit-limit-requests', '?'),
                    'remaining_requests': r.headers.get('x-ratelimit-remaining-requests', '?'),
                    'limit_tokens': r.headers.get('x-ratelimit-limit-tokens', '?'),
                    'remaining_tokens': r.headers.get('x-ratelimit-remaining-tokens', '?'),
                    'reset_requests': r.headers.get('x-ratelimit-reset-requests', ''),
                    'reset_tokens': r.headers.get('x-ratelimit-reset-tokens', ''),
                    'last_checked': datetime.now().strftime('%H:%M:%S'),
                }
                if r.status_code == 200:
                    groq_key_index = (groq_key_index + i + 1) % len(keys)
                    return r.json()['choices'][0]['message']['content'].strip(), None
                elif r.status_code == 429:
                    app_logger.warning(f'Groq rate limited key ...{key[-8:]}, trying next')
                    break  # Try next key
                elif r.status_code >= 500:
                    app_logger.warning(f'Groq server error {r.status_code}, retry {attempt+1}')
                    time.sleep(2)
                    continue  # Retry same key
                else:
                    error_logger.error(f'Groq unexpected {r.status_code}: {r.text[:200]}')
                    break
            except http_requests.exceptions.Timeout:
                app_logger.warning(f'Groq timeout attempt {attempt+1} key ...{key[-8:]}')
                time.sleep(1)
                continue
            except Exception as e:
                error_logger.error(f'Groq exception: {str(e)}')
                break
    return None, 'All Groq keys exhausted'


def call_gemini(prompt, max_retries=2):
    api_key = get_setting('gemini_api_key')
    if not api_key:
        return None, 'No Gemini key configured'
    for attempt in range(max_retries):
        try:
            r = http_requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}',
                json={'contents': [{'parts': [{'text': prompt}]}]}, timeout=45)
            if r.status_code == 200:
                return r.json()['candidates'][0]['content']['parts'][0]['text'].strip(), None
            elif r.status_code >= 500:
                app_logger.warning(f'Gemini server error {r.status_code}, retry {attempt+1}')
                time.sleep(2)
                continue
            else:
                app_logger.warning(f'Gemini returned {r.status_code}')
                return None, f'Gemini {r.status_code}'
        except http_requests.exceptions.Timeout:
            app_logger.warning(f'Gemini timeout attempt {attempt+1}')
            time.sleep(1)
            continue
        except Exception as e:
            error_logger.error(f'Gemini error: {str(e)}')
            return None, f'Gemini error: {str(e)[:50]}'
    return None, 'Gemini failed after retries'


def generate_ai_email(name, company, prompt_template, context='', designation=''):
    prompt = prompt_template.replace('{name}', name or '').replace('{company}', company or '').replace('{designation}', designation or 'founder/executive')
    if context:
        prompt = f"""CONTEXT ABOUT {company} (USE THIS to personalize the email):
{context}

USE the above context to write a SPECIFIC opening line. Do NOT write generic emails.

""" + prompt
    
    priority = (get_setting('ai_priority') or 'ollama,groq,gemini').split(',')
    
    for provider in priority:
        provider = provider.strip().lower()
        if provider == 'ollama':
            body, err = call_ollama(prompt)
        elif provider == 'groq':
            body, err = call_groq(prompt)
        elif provider == 'gemini':
            body, err = call_gemini(prompt)
        else:
            continue
        
        # Track usage
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
            conn.execute("INSERT INTO ai_usage (provider, purpose, success) VALUES (?,?,?)",
                (provider, 'email', 1 if body else 0))
            conn.commit()
            conn.close()
        except: pass
        
        if body:
            return body, None
        print(f'  [{provider}] failed: {err}')
    
    return None, 'All AI providers failed'


@app.route('/api/groq_usage')
@login_required
def api_groq_usage():
    """Check Groq rate limits for all keys"""
    keys_str = get_setting('groq_api_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    if not keys:
        return jsonify({'keys': [], 'error': 'No Groq keys configured'})

    results = []
    for idx, key in enumerate(keys):
        key_short = key[-8:]
        # If we have cached info, use it; otherwise do a lightweight call
        if key_short in groq_rate_limits:
            info = groq_rate_limits[key_short]
        else:
            # Make a minimal request to get headers
            try:
                r = http_requests.post('https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                    json={'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 1},
                    timeout=10)
                info = {
                    'limit_requests': r.headers.get('x-ratelimit-limit-requests', '?'),
                    'remaining_requests': r.headers.get('x-ratelimit-remaining-requests', '?'),
                    'limit_tokens': r.headers.get('x-ratelimit-limit-tokens', '?'),
                    'remaining_tokens': r.headers.get('x-ratelimit-remaining-tokens', '?'),
                    'reset_requests': r.headers.get('x-ratelimit-reset-requests', ''),
                    'reset_tokens': r.headers.get('x-ratelimit-reset-tokens', ''),
                    'last_checked': datetime.now().strftime('%H:%M:%S'),
                }
                groq_rate_limits[key_short] = info
                if r.status_code == 401:
                    info = {'error': 'Invalid key'}
            except Exception as e:
                info = {'error': str(e)[:50]}

        # Get usage from ai_usage table
        conn = get_db()
        total_used = conn.execute("SELECT COUNT(*) FROM ai_usage WHERE provider='groq'").fetchone()[0]
        today_used = conn.execute("SELECT COUNT(*) FROM ai_usage WHERE provider='groq' AND DATE(created_at)=DATE('now')").fetchone()[0]
        conn.close()

        results.append({
            'key_index': idx + 1,
            'key_hint': f'...{key_short}',
            'info': info,
            'total_used_db': total_used,
            'today_used_db': today_used,
        })

    return jsonify({'keys': results})


@app.route('/logs')
@login_required
def logs_page():
    conn = get_db()
    logs = conn.execute("""
        SELECT es.*, c.name, c.company, camp.name as campaign_name
        FROM emails_sent es
        JOIN contacts c ON es.contact_id = c.id
        JOIN campaigns camp ON es.campaign_id = camp.id
        ORDER BY es.sent_at DESC
    """).fetchall()

    sent = sum(1 for l in logs if l['status'] == 'sent')
    failed = sum(1 for l in logs if l['status'] == 'failed')
    bounced = sum(1 for l in logs if l['status'] == 'bounced')
    opened = sum(1 for l in logs if l['opened'])
    not_opened = sent - opened
    total = len(logs)
    campaigns_count = len(set(l['campaign_id'] for l in logs))
    success_rate = (sent / total * 100) if total > 0 else 0

    stats = {'sent': sent, 'failed': failed, 'bounced': bounced, 'opened': opened, 'not_opened': not_opened, 'total': total, 'campaigns': campaigns_count, 'success_rate': success_rate}

    # Time data for chart
    from collections import defaultdict
    by_date_sent = defaultdict(int)
    by_date_failed = defaultdict(int)
    for l in logs:
        if l['sent_at']:
            day = l['sent_at'][:10]
            if l['status'] == 'sent':
                by_date_sent[day] += 1
            else:
                by_date_failed[day] += 1
    all_days = sorted(set(list(by_date_sent.keys()) + list(by_date_failed.keys())))
    time_data = {'labels': all_days, 'sent': [by_date_sent[d] for d in all_days], 'failed': [by_date_failed[d] for d in all_days]}

    conn.close()
    return render_template('logs.html', logs=logs, stats=stats, stats_json=json.dumps(stats), time_data_json=json.dumps(time_data))


@app.route('/bounced')
@login_required
def bounced():
    conn = get_db()
    rows = conn.execute("""
        SELECT es.*, c.name, c.company FROM emails_sent es
        JOIN contacts c ON es.contact_id = c.id
        WHERE es.status IN ('bounced', 'failed')
        ORDER BY es.sent_at DESC
    """).fetchall()
    conn.close()
    return render_template('bounced.html', bounced=rows)


@app.route('/export/<string:export_type>')
@login_required
def export_data(export_type):
    conn = get_db()
    if export_type == 'sent':
        df = pd.read_sql("SELECT c.name, c.company, es.email, es.subject, es.status, es.sent_at FROM emails_sent es JOIN contacts c ON es.contact_id=c.id WHERE es.status='sent'", conn)
    elif export_type == 'bounced':
        df = pd.read_sql("SELECT c.name, c.company, es.email, es.bounce_reason, es.sent_at FROM emails_sent es JOIN contacts c ON es.contact_id=c.id WHERE es.status IN ('bounced','failed')", conn)
    elif export_type == 'follow_ups':
        df = pd.read_sql("SELECT * FROM follow_ups", conn)
    elif export_type == 'invalid':
        df = pd.read_sql("SELECT name, company, email, validation_reason FROM contacts WHERE email_valid=0", conn)
    else:
        df = pd.read_sql("SELECT * FROM contacts", conn)

    conn.close()
    filepath = f"export_{export_type}.xlsx"
    df.to_excel(filepath, index=False)
    return send_file(filepath, as_attachment=True)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', '0') == '1'
    start_imap_checker()
    print(f"\n=== Email Campaign Manager ===")
    print(f"Open: http://localhost:{port}")
    print(f"Debug: {debug}")
    print("==============================\n")
    app.run(debug=debug, host='0.0.0.0', port=port)
else:
    # Gunicorn / production WSGI entry
    # Don't start IMAP checker in production (can cause issues with fresh DB)
    pass
