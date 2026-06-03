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
    return redirect(url_for('dashboard')), 429


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
    return redirect(url_for('dashboard'))


@app.errorhandler(413)
def file_too_large(e):
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({'error': 'File too large (max 16MB)'}), 413
    flash('File too large! Maximum 16MB allowed.', 'error')
    return redirect(request.referrer or url_for('dashboard'))

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
    from utils.db import USE_POSTGRES
    _hash_pw = generate_password_hash  # bind to local to avoid scoping issues
    conn = get_db()
    # Check if we actually got a PG connection (get_db may fallback to SQLite)
    is_pg = USE_POSTGRES and hasattr(conn, 'raw')
    if is_pg:
        from utils.pg_schema import init_pg
        init_pg(conn)
        # PostgreSQL: seed defaults
        existing_user = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        if not existing_user:
            default_hash = _hash_pw('admin123')
            conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                         ('admin', default_hash, 'admin'))
            conn.commit()
        # Ensure default workspace
        if not conn.execute("SELECT id FROM workspaces WHERE id=1").fetchone():
            conn.execute("INSERT INTO workspaces (id, name, slug, plan) VALUES (1, 'Default Workspace', 'default', 'free')")
            conn.commit()
        # Insert default settings
        for k, v in DEFAULT_SETTINGS.items():
            existing = conn.execute("SELECT key FROM settings WHERE key=?", (k,)).fetchone()
            if not existing:
                conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", (k, v))
        conn.commit()
        # Insert default automation rules
        for rule_key, enabled, delay_days, max_followups in [('no_reply_followup',1,2,3),('opened_multiple_times',1,1,2),('interested_pause',1,0,0),('ooo_retry',1,7,1),('bounce_pause',1,0,0)]:
            existing = conn.execute("SELECT id FROM automation_settings WHERE rule_key=?", (rule_key,)).fetchone()
            if not existing:
                conn.execute("INSERT INTO automation_settings (rule_key, enabled, delay_days, max_followups) VALUES (?,?,?,?)", (rule_key, enabled, delay_days, max_followups))
        conn.commit()
        conn.close()
        return
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT,
            workspace_id INTEGER DEFAULT 1,
            UNIQUE(key, workspace_id)
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

        CREATE TABLE IF NOT EXISTS smtp_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            smtp_server TEXT DEFAULT 'smtp.hostinger.com',
            smtp_port INTEGER DEFAULT 587,
            from_name TEXT DEFAULT '',
            daily_limit INTEGER DEFAULT 50,
            sent_today INTEGER DEFAULT 0,
            health_score INTEGER DEFAULT 100,
            warmup_stage INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            last_used TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER,
            campaign_id INTEGER,
            subject TEXT,
            status TEXT DEFAULT 'active',
            unread_count INTEGER DEFAULT 0,
            last_message_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(id),
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER,
            direction TEXT,
            sender_email TEXT,
            recipient_email TEXT,
            subject TEXT,
            body TEXT,
            message_id TEXT,
            in_reply_to TEXT,
            ai_category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (thread_id) REFERENCES threads(id)
        );

        CREATE TABLE IF NOT EXISTS automation_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_key TEXT UNIQUE NOT NULL,
            enabled INTEGER DEFAULT 1,
            delay_days INTEGER DEFAULT 2,
            max_followups INTEGER DEFAULT 3,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS email_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_sent_id INTEGER,
            thread_id INTEGER,
            contact_id INTEGER,
            clicked_url TEXT,
            token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS lead_intelligence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER DEFAULT 1,
            contact_id INTEGER NOT NULL,
            company_summary TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            employee_size TEXT DEFAULT '',
            tech_stack TEXT DEFAULT '',
            pain_points TEXT DEFAULT '',
            icp_score INTEGER DEFAULT 0,
            buying_signals TEXT DEFAULT '',
            outreach_angles TEXT DEFAULT '',
            ai_summary TEXT DEFAULT '',
            enrichment_status TEXT DEFAULT 'pending',
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        );

        CREATE TABLE IF NOT EXISTS company_intelligence_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE NOT NULL,
            company_name TEXT DEFAULT '',
            company_summary TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            employee_size TEXT DEFAULT '',
            tech_stack TEXT DEFAULT '',
            pain_points TEXT DEFAULT '',
            buying_signals TEXT DEFAULT '',
            raw_website_text TEXT DEFAULT '',
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Create sequence + intelligence tables via safe migrations
    for tbl_sql in [
        """
        CREATE TABLE IF NOT EXISTS sequence_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER DEFAULT 1,
            campaign_id INTEGER NOT NULL,
            step_order INTEGER NOT NULL DEFAULT 1,
            step_type TEXT NOT NULL DEFAULT 'email',
            delay_days INTEGER NOT NULL DEFAULT 1,
            subject TEXT DEFAULT '',
            body TEXT DEFAULT '',
            ai_enabled INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        )""",
        """
        CREATE TABLE IF NOT EXISTS contact_sequence_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER DEFAULT 1,
            contact_id INTEGER NOT NULL,
            campaign_id INTEGER NOT NULL,
            current_step INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            next_run_at TIMESTAMP,
            last_sent_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(id),
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        )""",
    ]:
        try:
            conn.execute(tbl_sql)
            conn.commit()
        except Exception:
            pass

    conn.commit()

    # Create default admin user if no users exist
    existing_user = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
    if not existing_user:
        default_hash = _hash_pw('admin123')
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                     ('admin', default_hash, 'admin'))
        conn.commit()
        print("[AUTH] Default admin created -- username: admin, password: admin123")
        print("[AUTH] WARNING: CHANGE THIS PASSWORD from Settings after first login!")

    # Insert default settings for any missing keys
    for k, v in DEFAULT_SETTINGS.items():
        existing = conn.execute("SELECT key FROM settings WHERE key=?", (k,)).fetchone()
        if not existing:
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
    conn.commit()

    # Safe migrations: add missing columns if not exist
    for migration in [
        "ALTER TABLE contacts ADD COLUMN lead_score INTEGER DEFAULT 0",
        "ALTER TABLE contacts ADD COLUMN website TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN context TEXT DEFAULT ''",
        # Workspace migrations
        "CREATE TABLE IF NOT EXISTS workspaces (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL, plan TEXT DEFAULT 'free', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "ALTER TABLE users ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE contacts ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE campaigns ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE smtp_accounts ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE threads ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE follow_ups ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE automation_settings ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE email_clicks ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE emails_sent ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE ai_usage ADD COLUMN workspace_id INTEGER DEFAULT 1",
        # Sequence engine indexes
        "CREATE INDEX IF NOT EXISTS idx_seq_steps_campaign ON sequence_steps(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_seq_steps_order ON sequence_steps(campaign_id, step_order)",
        "CREATE INDEX IF NOT EXISTS idx_css_contact ON contact_sequence_state(contact_id)",
        "CREATE INDEX IF NOT EXISTS idx_css_campaign ON contact_sequence_state(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_css_next_run ON contact_sequence_state(next_run_at)",
        "CREATE INDEX IF NOT EXISTS idx_css_status ON contact_sequence_state(status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_css_unique ON contact_sequence_state(contact_id, campaign_id)",
        # Lead intelligence indexes
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_li_contact ON lead_intelligence(contact_id)",
        "CREATE INDEX IF NOT EXISTS idx_li_workspace ON lead_intelligence(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_li_icp ON lead_intelligence(icp_score)",
        "CREATE INDEX IF NOT EXISTS idx_li_status ON lead_intelligence(enrichment_status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cic_domain ON company_intelligence_cache(domain)",
        # SMTP full sender identity columns
        "ALTER TABLE smtp_accounts ADD COLUMN reply_to TEXT DEFAULT ''",
        "ALTER TABLE smtp_accounts ADD COLUMN bcc_emails TEXT DEFAULT ''",
        "ALTER TABLE smtp_accounts ADD COLUMN signature TEXT DEFAULT ''",
        "ALTER TABLE smtp_accounts ADD COLUMN login_username TEXT DEFAULT ''",
        # Contact intelligence columns
        "ALTER TABLE contacts ADD COLUMN industry TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN company_size TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN country TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN linkedin_url TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN linkedin_company_url TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN company_description TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN technologies TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN employee_range TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN founded_year TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN lead_source TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN enrichment_status TEXT DEFAULT 'pending'",
        "ALTER TABLE contacts ADD COLUMN last_enriched_at TIMESTAMP",
        # Indexes for fast filtering
        "CREATE INDEX IF NOT EXISTS idx_contacts_industry ON contacts(industry)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_country ON contacts(country)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_enrichment ON contacts(enrichment_status)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_company_size ON contacts(company_size)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_lead_score ON contacts(lead_score)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_workspace_industry ON contacts(workspace_id, industry)",
        # Campaign execution columns
        "ALTER TABLE campaigns ADD COLUMN last_heartbeat TIMESTAMP",
        "ALTER TABLE campaigns ADD COLUMN job_status TEXT DEFAULT 'draft'",
        "ALTER TABLE campaigns ADD COLUMN started_at TIMESTAMP",
        "ALTER TABLE campaigns ADD COLUMN completed_at TIMESTAMP",
        "ALTER TABLE campaigns ADD COLUMN send_mode TEXT DEFAULT 'template'",
        "ALTER TABLE campaigns ADD COLUMN total_contacts INTEGER DEFAULT 0",
        "ALTER TABLE campaigns ADD COLUMN sent_count INTEGER DEFAULT 0",
        "ALTER TABLE campaigns ADD COLUMN failed_count INTEGER DEFAULT 0",
        "ALTER TABLE campaigns ADD COLUMN subject_template TEXT DEFAULT ''",
        "ALTER TABLE campaigns ADD COLUMN body_template TEXT DEFAULT ''",
        "ALTER TABLE campaigns ADD COLUMN attachment_path TEXT DEFAULT ''",
        # Campaign logs table
        """CREATE TABLE IF NOT EXISTS campaign_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            workspace_id INTEGER DEFAULT 1,
            contact_id INTEGER,
            level TEXT DEFAULT 'info',
            message TEXT NOT NULL,
            smtp_email TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_cl_campaign ON campaign_logs(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_cl_created ON campaign_logs(created_at DESC)",
        # Copilot logs table
        """CREATE TABLE IF NOT EXISTS copilot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER DEFAULT 1,
            user_id INTEGER,
            page_type TEXT,
            page_id INTEGER,
            user_message TEXT,
            ai_response TEXT,
            action_taken TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]:
        try:
            conn.execute(migration)
            conn.commit()
        except Exception:
            pass  # Column already exists or table already exists

    # Ensure Default Workspace exists
    if not conn.execute("SELECT id FROM workspaces WHERE id=1").fetchone():
        conn.execute("INSERT OR IGNORE INTO workspaces (id, name, slug, plan) VALUES (1, 'Default Workspace', 'default', 'free')")
        conn.commit()

    # Backfill workspace_id=1 for all existing rows
    for table in ['users','contacts','campaigns','smtp_accounts','threads','follow_ups',
                  'automation_settings','email_clicks','emails_sent','ai_usage','settings']:
        try:
            conn.execute(f"UPDATE {table} SET workspace_id=1 WHERE workspace_id IS NULL")
        except Exception:
            pass
    conn.commit()

    # Insert default automation rules
    default_rules = [
        ('no_reply_followup',      1, 2, 3),
        ('opened_multiple_times',  1, 1, 2),
        ('interested_pause',       1, 0, 0),
        ('ooo_retry',              1, 7, 1),
        ('bounce_pause',           1, 0, 0),
    ]
    for rule_key, enabled, delay_days, max_followups in default_rules:
        existing = conn.execute("SELECT id FROM automation_settings WHERE rule_key=?", (rule_key,)).fetchone()
        if not existing:
            conn.execute("""
                INSERT OR IGNORE INTO automation_settings (rule_key, enabled, delay_days, max_followups)
                VALUES (?,?,?,?)
            """, (rule_key, enabled, delay_days, max_followups))
    conn.commit()

    # send_reservations — duplicate send protection
    conn.execute("""
        CREATE TABLE IF NOT EXISTS send_reservations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            contact_id   INTEGER NOT NULL,
            campaign_id  INTEGER NOT NULL,
            send_key     TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'sending',
            reserved_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (workspace_id, contact_id, campaign_id, send_key)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sr_lookup
        ON send_reservations (workspace_id, contact_id, campaign_id, send_key)
    """)
    conn.commit()

    conn.close()


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
            from utils.db import _get_pg_pool
            _get_pg_pool()
            print('[STARTUP] PostgreSQL pool created successfully')
        except Exception as pg_err:
            print(f'[STARTUP] PostgreSQL pool FAILED: {pg_err}')
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


# ==============================
# AUTH ROUTES (handled by routes/auth.py blueprint)
# ==============================












# ==============================
# ROUTES
# ==============================
@app.route('/')
def landing_page():
    """Marketing landing page — public."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')


@app.route('/solutions')
def solutions_page():
    return render_template('solutions.html')


@app.route('/blogs')
def blogs_page():
    return render_template('blogs.html')


@app.route('/contact', methods=['GET', 'POST'])
def contact_page():
    if request.method == 'POST':
        flash('Message sent! We\'ll get back to you soon.', 'success')
        return redirect(url_for('contact_page'))
    return render_template('contact.html')


@app.route('/dashboard')
@login_required
def dashboard():
    try:
        return _dashboard_inner()
    except Exception as e:
        error_logger.error(f'Dashboard crash: {e}')
        # Show a minimal working page instead of redirect loop
        return f'''<html><body style="font-family:sans-serif;padding:40px;">
        <h2>Dashboard Error</h2>
        <p style="color:red;">{str(e)[:200]}</p>
        <p>The app started but dashboard has an error. Try:</p>
        <ul>
        <li><a href="/settings">Settings</a></li>
        <li><a href="/campaigns">Campaigns</a></li>
        <li><a href="/live-logs">Live Logs</a></li>
        <li><a href="/logout">Logout</a></li>
        </ul>
        </body></html>''', 500


def _dashboard_inner():
    from services.lead_scoring import get_hot_leads, calculate_priority
    conn = get_db()
    total_sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    total_bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed')").fetchone()[0]
    total_opened = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1").fetchone()[0]
    total_replied = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE replied=1").fetchone()[0]
    total_clicks = conn.execute("SELECT COUNT(DISTINCT contact_id) FROM email_clicks WHERE contact_id IS NOT NULL").fetchone()[0]
    total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

    # Rates
    open_rate = round(total_opened / total_sent * 100, 1) if total_sent else 0
    reply_rate = round(total_replied / total_sent * 100, 1) if total_sent else 0
    click_rate = round(total_clicks / total_sent * 100, 1) if total_sent else 0
    bounce_rate = round(total_bounced / total_sent * 100, 1) if total_sent else 0

    # Meetings detected
    meetings_detected = conn.execute("SELECT COUNT(*) FROM threads WHERE status='meeting'").fetchone()[0]

    # Inbox attention queue — interested/meeting/unread threads
    attention_threads = conn.execute("""
        SELECT t.id, t.status, t.unread_count, t.last_message_at,
               c.name as contact_name, c.company as contact_company, c.email as contact_email,
               (SELECT m2.ai_category FROM messages m2 WHERE m2.thread_id = t.id AND m2.direction='incoming' ORDER BY m2.created_at DESC LIMIT 1) as ai_category
        FROM threads t
        LEFT JOIN contacts c ON t.contact_id = c.id
        WHERE t.status IN ('interested','meeting') OR t.unread_count > 0
        ORDER BY t.last_message_at DESC LIMIT 8
    """).fetchall()

    # Campaign performance
    campaigns = conn.execute("""
        SELECT c.*,
            COUNT(CASE WHEN es.status='sent' THEN 1 END) as sent_count,
            COUNT(CASE WHEN es.opened=1 THEN 1 END) as opened_count,
            COUNT(CASE WHEN es.replied=1 THEN 1 END) as replied_count,
            COUNT(CASE WHEN es.status IN ('bounced','failed') THEN 1 END) as bounce_count
        FROM campaigns c
        LEFT JOIN emails_sent es ON es.campaign_id = c.id
        GROUP BY c.id
        ORDER BY c.created_at DESC LIMIT 6
    """).fetchall()

    # SMTP health
    smtp_accounts = conn.execute("""
        SELECT id, email, health_score, warmup_stage, active, sent_today, daily_limit
        FROM smtp_accounts ORDER BY active DESC, health_score DESC
    """).fetchall()
    smtp_active = sum(1 for a in smtp_accounts if a['active'])
    smtp_at_risk = sum(1 for a in smtp_accounts if a['health_score'] < 50 and a['active'])
    avg_health = round(sum(a['health_score'] for a in smtp_accounts) / len(smtp_accounts), 0) if smtp_accounts else 0

    # Recent activity feed
    activity_feed = []
    # Recent replies
    recent_replies = conn.execute("""
        SELECT m.created_at, m.ai_category, m.sender_email,
               c.name as contact_name, c.company, t.id as thread_id
        FROM messages m
        JOIN threads t ON m.thread_id = t.id
        LEFT JOIN contacts c ON t.contact_id = c.id
        WHERE m.direction='incoming'
        ORDER BY m.created_at DESC LIMIT 5
    """).fetchall()
    for r in recent_replies:
        activity_feed.append({'type': 'reply', 'time': r['created_at'], 'text': f"{r['contact_name'] or r['sender_email']} replied", 'sub': r['ai_category'] or 'reply', 'link': f"/inbox/{r['thread_id']}", 'company': r['company'] or ''})

    # Recent sends
    recent_sends = conn.execute("""
        SELECT es.sent_at, es.status, c.name, c.company, es.campaign_id
        FROM emails_sent es JOIN contacts c ON es.contact_id=c.id
        ORDER BY es.sent_at DESC LIMIT 5
    """).fetchall()
    for s in recent_sends:
        activity_feed.append({'type': 'send' if s['status']=='sent' else 'bounce', 'time': s['sent_at'], 'text': f"Email {'sent to' if s['status']=='sent' else 'bounced for'} {s['name']}", 'sub': s['company'] or '', 'link': f"/campaign/{s['campaign_id']}", 'company': s['company'] or ''})

    # Recent clicks
    recent_clicks = conn.execute("""
        SELECT ec.created_at, c.name, c.company, ec.thread_id
        FROM email_clicks ec LEFT JOIN contacts c ON ec.contact_id=c.id
        ORDER BY ec.created_at DESC LIMIT 3
    """).fetchall()
    for cl in recent_clicks:
        activity_feed.append({'type': 'click', 'time': cl['created_at'], 'text': f"{cl['name'] or 'Someone'} clicked a link", 'sub': cl['company'] or '', 'link': f"/inbox/{cl['thread_id']}" if cl['thread_id'] else '#', 'company': cl['company'] or ''})

    activity_feed.sort(key=lambda x: x['time'] or '', reverse=True)
    activity_feed = activity_feed[:12]

    # Setup checklist for empty state
    setup_steps = [
        {'done': bool(smtp_accounts), 'label': 'Add SMTP account', 'link': '/settings'},
        {'done': total_contacts > 0, 'label': 'Upload contacts', 'link': '/upload'},
        {'done': total_sent > 0, 'label': 'Launch first campaign', 'link': '/campaigns'},
    ]

    conn.close()
    hot_leads = get_hot_leads(limit=8)
    hot_leads_count = len([l for l in hot_leads if calculate_priority(l['lead_score']) == 'hot'])
    unread_count = conn.execute("SELECT COUNT(*) FROM threads WHERE unread_count > 0").fetchone()[0] if False else sum(1 for t in attention_threads if t['unread_count'] > 0)

    return render_template('dashboard.html',
        total_sent=total_sent, total_opened=total_opened, total_replied=total_replied,
        total_clicks=total_clicks, total_contacts=total_contacts, total_bounced=total_bounced,
        open_rate=open_rate, reply_rate=reply_rate, click_rate=click_rate, bounce_rate=bounce_rate,
        meetings_detected=meetings_detected, hot_leads_count=hot_leads_count,
        attention_threads=attention_threads, campaigns=campaigns,
        smtp_accounts=smtp_accounts, smtp_active=smtp_active, smtp_at_risk=smtp_at_risk, avg_health=avg_health,
        activity_feed=activity_feed, hot_leads=hot_leads,
        calculate_priority=calculate_priority, setup_steps=setup_steps,
        unread_count=unread_count)@app.route('/campaigns')
@login_required
def campaigns_list():
    from services.workspace_service import get_wid, ws_campaigns
    wid = get_wid()
    campaigns = ws_campaigns(wid)
    conn = get_db()
    meetings = {}
    for camp in campaigns:
        m = conn.execute(
            "SELECT COUNT(*) FROM threads WHERE campaign_id=? AND status='meeting' AND workspace_id=?",
            (camp['id'], wid)
        ).fetchone()[0]
        meetings[camp['id']] = m
    conn.close()
    return render_template('campaigns.html', campaigns=campaigns, meetings=meetings)


@app.route('/campaign/new', methods=['GET', 'POST'])
@login_required
def new_campaign():
    if request.method == 'POST':
        name = request.form.get('campaign_name', 'Untitled Campaign')
        description = request.form.get('description', '')
        from services.workspace_service import get_wid
        wid = get_wid()
        conn = get_db()
        from utils.db import is_postgres
        if is_postgres():
            campaign_id = conn.execute(
                "INSERT INTO campaigns (name, description, workspace_id) VALUES (?,?,?) RETURNING id",
                (name, description, wid)
            ).fetchone()[0]
            conn.commit()
        else:
            conn.execute("INSERT INTO campaigns (name, description, workspace_id) VALUES (?,?,?)", (name, description, wid))
            conn.commit()
            campaign_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    return render_template('new_campaign.html')


@app.route('/campaign/edit/<int:campaign_id>', methods=['POST'])
@login_required
def edit_campaign(campaign_id):
    from utils.ownership import owns_campaign
    if not owns_campaign(campaign_id):
        flash('Not found.', 'error')
        return redirect(url_for('campaigns_list'))
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
        flash('No contacts selected. Please select at least one contact.', 'error')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    app_logger.info(f'Campaign {campaign_id} send started | {len(contact_ids)} contacts | by {current_user.username}')
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()
    sent = 0
    failed = 0

    # Try rotation first, fallback to settings
    def get_smtp_creds():
        from services.smtp_rotation import get_next_smtp_account, append_signature
        account = get_next_smtp_account(workspace_id=wid)
        if account:
            return account  # Full identity object
        # Fallback — backup SMTP only, no identity override
        return {
            'server':     get_setting('smtp_server'),
            'port':       int(get_setting('smtp_port') or 587),
            'username':   get_setting('smtp_username'),
            'password':   get_setting('smtp_password'),
            'from_email': get_setting('from_email') or get_setting('smtp_username'),
            'from_name':  get_setting('from_name'),
            'reply_to':   get_setting('reply_to'),
            'bcc_emails': get_setting('bcc_emails'),
            'signature':  '',
            'account_id': None,
            'email':      get_setting('from_email') or get_setting('smtp_username'),
            'smtp_server': get_setting('smtp_server'),
            'smtp_port':  int(get_setting('smtp_port') or 587),
        }

    try:
        for idx, cid in enumerate(contact_ids):
            # Get fresh SMTP creds per email (rotation)
            creds = get_smtp_creds()
            smtp_server  = creds.get('smtp_server') or creds.get('server')
            smtp_port    = creds.get('smtp_port')   or creds.get('port')
            # Use login_username for Brevo/custom SMTP (may differ from from_email)
            smtp_username = creds.get('login_username') or creds.get('email') or creds.get('username')
            smtp_password = creds['password']
            from_email   = creds.get('from_email')  or smtp_username
            from_name    = creds.get('from_name', '')
            account_id   = creds.get('account_id')  or creds.get('id')
            # Inbox-level identity (overrides global fallback)
            reply_to     = creds.get('reply_to') or _get_reply_to()
            bcc          = creds.get('bcc_emails')  or get_setting('bcc_emails')
            signature    = creds.get('signature', '')

            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
            if not contact:
                continue

            # Check suppression list
            if is_unsubscribed(contact['email']):
                continue

            # Atomic duplicate check — lock per campaign to prevent race condition
            with _get_campaign_lock(campaign_id):
                already = conn.execute(
                    "SELECT id FROM emails_sent WHERE contact_id=? AND campaign_id=? AND status='sent'",
                    (cid, campaign_id)
                ).fetchone()
                if already:
                    continue

                subject = subject_template.replace('{company}', contact['company'] or '')
                subject = subject.replace('{name}', contact['name'] or '')
                body = body_template.replace('{company}', contact['company'] or '')
                body = body.replace('{name}', contact['name'] or '')

                try:
                    server = smtplib.SMTP(smtp_server, smtp_port)
                    server.starttls()
                    server.login(smtp_username, smtp_password)

                    tracking_id = str(uuid.uuid4())
                    # Append inbox signature before tracking pixel
                    from services.smtp_rotation import append_signature
                    body_with_sig = append_signature(body, signature)
                    tracked_body = inject_tracking_pixel(body_with_sig, tracking_id)

                    msg = EmailMessage()
                    msg['Subject']    = subject
                    msg['From']       = formataddr((from_name, from_email))
                    msg['To']         = contact['email']
                    msg['Message-ID'] = f'<{tracking_id}@outreachos>'
                    if reply_to and reply_to.strip():
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
                    server.quit()
                    conn.execute("""
                        INSERT INTO emails_sent (campaign_id, contact_id, email, subject, body, status, tracking_id, sent_at, workspace_id)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (campaign_id, cid, contact['email'], subject, body, 'sent', tracking_id, datetime.now(), wid))
                    conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (cid,))
                    conn.commit()
                    sent += 1
                    # Log to thread system
                    try:
                        from services.inbox_service import get_or_create_thread, insert_message
                        thread_id = get_or_create_thread(cid, campaign_id, subject)
                        insert_message(
                            thread_id=thread_id, direction='outgoing',
                            sender_email=from_email, recipient_email=contact['email'],
                            subject=subject, body=body, message_id=tracking_id
                        )
                    except Exception:
                        pass
                    if account_id:
                        mark_send_success(account_id)
                    smtp_logger.info(f'SENT | Campaign {campaign_id} | To: {contact["email"]} | Subject: {subject[:50]}')
                    time.sleep(5)

                except smtplib.SMTPRecipientsRefused as e:
                    conn.execute("""
                        INSERT INTO emails_sent (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (campaign_id, cid, contact['email'], subject, body, 'bounced', str(e), datetime.now()))
                    conn.commit()
                    failed += 1
                    if account_id:
                        mark_send_failure(account_id)
                    smtp_logger.warning(f'BOUNCED | {contact["email"]} | {str(e)[:100]}')
                    # Lead score penalty for bounce
                    try:
                        from services.lead_scoring import update_lead_score
                        update_lead_score(cid, 'bounce')
                    except Exception:
                        pass

                except Exception as e:
                    conn.execute("""
                        INSERT INTO emails_sent (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (campaign_id, cid, contact['email'], subject, body, 'failed', str(e), datetime.now()))
                    conn.commit()
                    failed += 1
                    if account_id:
                        mark_send_failure(account_id)
                    smtp_logger.error(f'FAILED | {contact["email"]} | {str(e)[:100]}')
                    error_logger.error(f'Send failed for {contact["email"]}: {str(e)}')

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
    from utils.ownership import owns_email_sent
    conn = get_db()
    record = owns_email_sent(email_id)
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

    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account
    wid = get_wid()
    account = get_next_smtp_account(workspace_id=wid)
    if account:
        smtp_server = account['smtp_server']
        smtp_port = int(account['smtp_port'])
        smtp_login = account['login_username'] or account['email']
        smtp_password = account['password']
        from_email = account['from_email'] or account['email']
        from_name = account.get('from_name', '')
        reply_to = account.get('reply_to') or _get_reply_to()
        bcc = account.get('bcc_emails', '')
    else:
        smtp_server = get_setting('smtp_server')
        smtp_port = int(get_setting('smtp_port') or 587)
        smtp_login = get_setting('smtp_username')
        smtp_password = get_setting('smtp_password')
        from_email = get_setting('from_email') or smtp_login
        from_name = get_setting('from_name')
        reply_to = get_setting('reply_to') or from_email
        bcc = get_setting('bcc_emails')

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_login, smtp_password)

        msg = EmailMessage()
        msg['Subject'] = record['subject']
        msg['From'] = formataddr((from_name, from_email))
        msg['To'] = record['email']
        if reply_to and reply_to.strip(): msg['Reply-To'] = reply_to
        if bcc and bcc.strip(): msg['Bcc'] = bcc
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
    from utils.ownership import owns_email_sent
    record = owns_email_sent(email_id)
    if not record:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    conn = get_db()

    # Duplicate protection - don't resend if already sent in same campaign
    already_sent = conn.execute(
        "SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent' AND id!=?",
        (record['email'], record['campaign_id'], email_id)
    ).fetchone()
    if already_sent:
        conn.close()
        return jsonify({'success': False, 'error': 'Already sent in this campaign'})

    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account
    wid = get_wid()
    account = get_next_smtp_account(workspace_id=wid)
    if account:
        smtp_server = account['smtp_server']
        smtp_port = int(account['smtp_port'])
        smtp_login = account['login_username'] or account['email']
        smtp_password = account['password']
        from_email = account['from_email'] or account['email']
        from_name = account.get('from_name', '')
        reply_to = account.get('reply_to') or _get_reply_to()
        bcc = account.get('bcc_emails', '')
    else:
        smtp_server = get_setting('smtp_server')
        smtp_port = int(get_setting('smtp_port') or 587)
        smtp_login = get_setting('smtp_username')
        smtp_password = get_setting('smtp_password')
        from_email = get_setting('from_email') or smtp_login
        from_name = get_setting('from_name')
        reply_to = get_setting('reply_to') or from_email
        bcc = get_setting('bcc_emails')

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_login, smtp_password)

        msg = EmailMessage()
        msg['Subject'] = record['subject']
        msg['From'] = formataddr((from_name, from_email))
        msg['To'] = record['email']
        if reply_to and reply_to.strip(): msg['Reply-To'] = reply_to
        if bcc and bcc.strip(): msg['Bcc'] = bcc
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


# Per-campaign send lock — prevents race condition duplicate sends
import threading
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


@app.route('/api/send_status')
@login_required
def api_send_status():
    prog = _get_send_progress(current_user.id)
    conn = get_db()
    recent = []
    if prog['campaign_id']:
        rows = conn.execute("""
            SELECT es.email, es.status, es.bounce_reason, c.name, c.company 
            FROM emails_sent es JOIN contacts c ON es.contact_id=c.id 
            WHERE es.campaign_id=? ORDER BY es.sent_at DESC LIMIT 50
        """, (prog['campaign_id'],)).fetchall()
        recent = [{'name': r['name'], 'company': r['company'], 'email': r['email'], 'status': r['status'], 'reason': r['bounce_reason'] or ''} for r in rows]
    conn.close()
    return jsonify({
        'running': prog['running'],
        'total':   prog['total'],
        'done':    prog['done'],
        'sent':    prog['sent'],
        'failed':  prog['failed'],
        'current': prog['current'],
        'recent':  recent
    })


@app.route('/campaign/<int:campaign_id>/send_ai', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def send_campaign_ai(campaign_id):
    uid = current_user.id
    prog = _get_send_progress(uid)
    if prog['running']:
        flash('Sending already in progress!', 'error')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    attachment = request.form.get('attachment', '')
    contact_ids = request.form.getlist('contact_ids')
    
    if not contact_ids:
        flash('No contacts selected. Please select at least one contact.', 'error')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    def run_send_ai():
        prog = {'running': True, 'total': len(contact_ids), 'done': 0, 'sent': 0, 'failed': 0, 'current': '', 'campaign_id': campaign_id}
        _set_send_progress(uid, prog)
        prompt_template = get_setting('email_prompt')
        from services.workspace_service import get_wid
        from services.smtp_rotation import get_next_smtp_account, append_signature
        wid = get_wid()
        conn = get_db()

        # Get SMTP creds via rotation (handles Brevo login_username properly)
        def _get_smtp_creds():
            account = get_next_smtp_account(workspace_id=wid)
            if account:
                return account
            return {
                'smtp_server':    get_setting('smtp_server'),
                'smtp_port':      int(get_setting('smtp_port') or 587),
                'login_username': get_setting('smtp_username'),
                'password':       get_setting('smtp_password'),
                'from_email':     get_setting('from_email') or get_setting('smtp_username'),
                'from_name':      get_setting('from_name'),
                'reply_to':       get_setting('reply_to'),
                'bcc_emails':     get_setting('bcc_emails'),
                'signature':      '',
                'email':          get_setting('from_email') or get_setting('smtp_username'),
                'account_id':     None,
                'id':             None,
            }

        creds = _get_smtp_creds()
        smtp_server_addr = creds.get('smtp_server')
        smtp_port_num = int(creds.get('smtp_port') or 587)
        smtp_login = creds.get('login_username') or creds.get('email')
        smtp_password = creds['password']
        from_email = creds.get('from_email') or creds.get('email')
        from_name = creds.get('from_name', '')
        reply_to = creds.get('reply_to') or _get_reply_to()
        bcc = creds.get('bcc_emails') or get_setting('bcc_emails')
        signature = creds.get('signature', '')
        account_id = creds.get('account_id') or creds.get('id')

        server = None
        try:
            server = smtplib.SMTP(smtp_server_addr, smtp_port_num)
            server.starttls()
            server.login(smtp_login, smtp_password)
        except Exception as e:
            error_logger.error(f'[AI SEND] SMTP login failed: {smtp_login}@{smtp_server_addr}:{smtp_port_num} — {e}')
            prog['running'] = False
            _set_send_progress(uid, prog)
            return

        for i, cid in enumerate(contact_ids):
            if i > 0 and i % 10 == 0:
                try: server.quit()
                except Exception: pass
                try:
                    # Re-get creds (rotation may pick next account)
                    creds = _get_smtp_creds()
                    smtp_server_addr = creds.get('smtp_server')
                    smtp_port_num = int(creds.get('smtp_port') or 587)
                    smtp_login = creds.get('login_username') or creds.get('email')
                    smtp_password = creds['password']
                    from_email = creds.get('from_email') or creds.get('email')
                    from_name = creds.get('from_name', '')
                    reply_to = creds.get('reply_to') or _get_reply_to()
                    bcc = creds.get('bcc_emails') or get_setting('bcc_emails')
                    signature = creds.get('signature', '')
                    account_id = creds.get('account_id') or creds.get('id')
                    server = smtplib.SMTP(smtp_server_addr, smtp_port_num)
                    server.starttls()
                    server.login(smtp_login, smtp_password)
                except Exception: pass

            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
            if not contact: continue

            if is_unsubscribed(contact['email']):
                prog['done'] += 1
                _set_send_progress(uid, prog)
                continue

            already = conn.execute("SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent'", (contact['email'], campaign_id)).fetchone()
            if already:
                prog['done'] += 1
                _set_send_progress(uid, prog)
                continue

            prog['current'] = f"{contact['name']} ({contact['email']})"
            _set_send_progress(uid, prog)
            subject = subject_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')

            if str(cid) in ai_generated_cache:
                body = ai_generated_cache.pop(str(cid))
            else:
                context = contact['context'] if 'context' in contact.keys() else ''
                if not context:
                    conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                        (campaign_id, cid, contact['email'], subject, '', 'failed', 'No context - fetch context first', datetime.now(), wid))
                    conn.commit()
                    prog['done'] += 1
                    prog['failed'] += 1
                    _set_send_progress(uid, prog)
                    continue
                designation = contact['designation'] if 'designation' in contact.keys() else ''
                body, error = generate_ai_email(contact['name'], contact['company'], prompt_template, context, designation)
                if not body:
                    conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                        (campaign_id, cid, contact['email'], subject, '', 'failed', f'AI: {error}', datetime.now(), wid))
                    conn.commit()
                    prog['done'] += 1
                    prog['failed'] += 1
                    _set_send_progress(uid, prog)
                    continue

            try:
                tracking_id = str(uuid.uuid4())
                body_with_sig = append_signature(body, signature)
                tracked_body = inject_tracking_pixel(body_with_sig, tracking_id)

                msg = EmailMessage()
                msg['Subject'] = subject
                msg['From'] = formataddr((from_name, from_email))
                msg['To'] = contact['email']
                if reply_to: msg['Reply-To'] = reply_to
                if bcc and bcc.strip(): msg['Bcc'] = bcc
                msg.add_alternative(tracked_body, subtype='html')

                if attachment and os.path.exists(os.path.join(ATTACHMENT_DIR, attachment)):
                    filepath = os.path.join(ATTACHMENT_DIR, attachment)
                    mt, _ = mimetypes.guess_type(filepath)
                    maintype, subtype = (mt.split('/', 1) if mt else ('application', 'octet-stream'))
                    with open(filepath, 'rb') as f:
                        msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(filepath))

                server.send_message(msg)
                conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,tracking_id,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (campaign_id, cid, contact['email'], subject, body, 'sent', tracking_id, datetime.now(), wid))
                conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (cid,))
                conn.commit()
                prog['sent'] += 1
                if account_id:
                    mark_send_success(account_id)
            except Exception as e:
                conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (campaign_id, cid, contact['email'], subject, body if 'body' in dir() else '', 'failed', str(e)[:200], datetime.now(), wid))
                conn.commit()
                prog['failed'] += 1
                if account_id:
                    mark_send_failure(account_id)

            prog['done'] += 1
            _set_send_progress(uid, prog)
            time.sleep(5)

        try: server.quit()
        except Exception: pass
        if prog['sent'] > 0:
            conn.execute("UPDATE campaigns SET status='sent' WHERE id=?", (campaign_id,))
            conn.commit()
        conn.close()
        prog['running'] = False
        prog['current'] = ''
        _set_send_progress(uid, prog)
    t = threading.Thread(target=run_send_ai)
    t.start()
    return redirect(url_for('send_progress_page', campaign_id=campaign_id))


# ==============================
# CAMPAIGN EXECUTION API ROUTES
# ==============================

@app.route('/campaign/<int:campaign_id>/launch', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def launch_campaign_route(campaign_id):
    """Launch campaign — browser-independent backend execution."""
    from services.campaign_executor import launch_campaign, JobStatus
    from services.workspace_service import get_wid
    import os

    wid             = get_wid()
    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    body_template    = request.form.get('body', '')
    send_mode        = request.form.get('send_mode', 'template')  # 'template' or 'ai'
    contact_ids      = [int(x) for x in request.form.getlist('contact_ids') if x.isdigit()]

    # Handle attachment
    attachment_path = ''
    uploaded = request.files.get('attachment_file')
    if uploaded and uploaded.filename:
        from werkzeug.utils import secure_filename
        fname = secure_filename(uploaded.filename)
        attachment_path = os.path.join(ATTACHMENT_DIR, fname)
        uploaded.save(attachment_path)
    elif request.form.get('attachment'):
        attachment_path = os.path.join(ATTACHMENT_DIR, request.form.get('attachment'))

    if not contact_ids:
        flash('No contacts selected. Please select at least one contact.', 'error')
        return redirect(url_for('campaign_detail', campaign_id=campaign_id))

    result = launch_campaign(
        campaign_id, contact_ids, subject_template,
        body_template, send_mode, wid, attachment_path
    )

    app_logger.info(f'Campaign {campaign_id} launched | {len(contact_ids)} contacts | mode={send_mode} | {result["mode"]}')
    return redirect(url_for('send_progress_page', campaign_id=campaign_id))


@app.route('/api/campaign/<int:campaign_id>/status')
@login_required
def api_campaign_execution_status(campaign_id):
    """Poll campaign execution status — used by send_progress.html every 3s."""
    from services.campaign_executor import get_campaign_status
    return jsonify(get_campaign_status(campaign_id))


@app.route('/api/campaign/<int:campaign_id>/pause', methods=['POST'])
@login_required
def api_pause_campaign(campaign_id):
    from services.campaign_executor import pause_campaign
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()
    camp = conn.execute('SELECT id FROM campaigns WHERE id=? AND workspace_id=?', (campaign_id, wid)).fetchone()
    conn.close()
    if not camp:
        return jsonify({'success': False, 'error': 'Campaign not found'}), 404
    pause_campaign(campaign_id, wid)
    return jsonify({'success': True, 'status': 'paused'})


@app.route('/api/campaign/<int:campaign_id>/resume', methods=['POST'])
@login_required
def api_resume_campaign(campaign_id):
    from services.campaign_executor import resume_campaign
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()
    camp = conn.execute('SELECT id FROM campaigns WHERE id=? AND workspace_id=?', (campaign_id, wid)).fetchone()
    conn.close()
    if not camp:
        return jsonify({'success': False, 'error': 'Campaign not found'}), 404
    result = resume_campaign(campaign_id, wid)
    return jsonify({'success': bool(result)})


@app.route('/api/campaign/<int:campaign_id>/cancel', methods=['POST'])
@login_required
def api_cancel_campaign(campaign_id):
    from services.campaign_executor import cancel_campaign
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()
    camp = conn.execute('SELECT id FROM campaigns WHERE id=? AND workspace_id=?', (campaign_id, wid)).fetchone()
    conn.close()
    if not camp:
        return jsonify({'success': False, 'error': 'Campaign not found'}), 404
    cancel_campaign(campaign_id, wid)
    return jsonify({'success': True, 'status': 'cancelled'})


@app.route('/campaign/<int:campaign_id>/sending')
@login_required
def send_progress_page(campaign_id):
    conn = get_db()
    campaign = conn.execute('SELECT * FROM campaigns WHERE id=?', (campaign_id,)).fetchone()
    conn.close()
    return render_template('send_progress.html', campaign_id=campaign_id, campaign=campaign)


@app.route('/campaign/<int:campaign_id>/status')
@login_required
def campaign_status_page(campaign_id):
    """Dedicated live campaign execution status page."""
    conn = get_db()
    campaign = conn.execute('SELECT * FROM campaigns WHERE id=?', (campaign_id,)).fetchone()
    conn.close()
    if not campaign:
        flash('Campaign not found', 'error')
        return redirect(url_for('campaigns_list'))
    return render_template('campaign_status.html', campaign_id=campaign_id, campaign=campaign)


# ==============================
# LEGACY SEND ROUTES (kept for backward compat)
# ==============================@app.route('/api/sequence/<int:campaign_id>/analytics')
@login_required
def api_sequence_analytics(campaign_id):
    """Per-step open/reply rates + dropoff funnel."""
    from services.sequence_engine import get_steps
    conn = get_db()
    steps = get_steps(campaign_id)
    result = []
    prev_sent = None
    for s in steps:
        sent = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status='sent'",
            (campaign_id,)
        ).fetchone()[0]
        opened = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND opened=1",
            (campaign_id,)
        ).fetchone()[0]
        replied = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND replied=1",
            (campaign_id,)
        ).fetchone()[0]
        bounced = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status='bounced'",
            (campaign_id,)
        ).fetchone()[0]
        dropoff = round((1 - sent / prev_sent) * 100, 1) if prev_sent and prev_sent > 0 else 0
        prev_sent = sent
        result.append({
            'step_id':    s['id'],
            'step_order': s['step_order'],
            'step_type':  s['step_type'],
            'subject':    s['subject'],
            'delay_days': s['delay_days'],
            'sent':       sent,
            'opened':     opened,
            'replied':    replied,
            'bounced':    bounced,
            'open_rate':  round(opened  / sent * 100, 1) if sent else 0,
            'reply_rate': round(replied / sent * 100, 1) if sent else 0,
            'bounce_rate':round(bounced / sent * 100, 1) if sent else 0,
            'dropoff':    dropoff,
        })
    conn.close()
    total_enrolled = conn.execute(
        "SELECT COUNT(*) FROM contact_sequence_state WHERE campaign_id=?",
        (campaign_id,)
    ).fetchone()[0] if False else 0
    conn2 = get_db()
    total_enrolled = conn2.execute(
        "SELECT COUNT(*) FROM contact_sequence_state WHERE campaign_id=?",
        (campaign_id,)
    ).fetchone()[0]
    conn2.close()
    return jsonify({'steps': result, 'total_enrolled': total_enrolled})


# ==============================
# SEQUENCE ENGINE ROUTES — PART 2 (Enrollment + State)
# ==============================

@app.route('/campaign/<int:campaign_id>/sequence')
@login_required
def sequence_builder(campaign_id):
    """Sequence builder UI for a campaign."""
    conn = get_db()
    campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    conn.close()
    if not campaign:
        flash('Campaign not found', 'error')
        return redirect(url_for('campaigns_list'))
    return render_template('sequence_builder.html', campaign=campaign)


@app.route('/api/sequence/<int:campaign_id>/enroll', methods=['POST'])
@login_required
def api_sequence_enroll(campaign_id):
    """
    Enroll contacts into a sequence.
    Body: {contact_ids: [1,2,3]}  OR  {enroll_all: true}
    Uses Celery if available, else synchronous.
    """
    from services.sequence_engine import enroll_contacts_bulk, get_steps
    from services.workspace_service import get_wid
    data = request.json or {}
    wid  = get_wid()

    # Must have at least 1 step before enrolling
    steps = get_steps(campaign_id)
    if not steps:
        return jsonify({'success': False, 'error': 'No active steps in this sequence. Add steps first.'})

    # Resolve contact list
    if data.get('enroll_all'):
        conn = get_db()
        rows = conn.execute(
            "SELECT id FROM contacts WHERE workspace_id=? AND email_valid=1",
            (wid,)
        ).fetchall()
        conn.close()
        contact_ids = [r['id'] for r in rows]
    else:
        contact_ids = [int(i) for i in data.get('contact_ids', [])]

    if not contact_ids:
        return jsonify({'success': False, 'error': 'No contacts provided'})

    # Try Celery async first
    if CELERY_AVAILABLE and has_active_workers():
        from tasks.sequence_tasks import enroll_contacts_task
        result = enroll_contacts_task.apply_async(
            args=[contact_ids, campaign_id, wid],
            queue='automation_queue'
        )
        app_logger.info(f'[SEQ] Enroll queued | campaign {campaign_id} | {len(contact_ids)} contacts | task {result.id}')
        return jsonify({'success': True, 'queued': True, 'task_id': result.id, 'total': len(contact_ids)})

    # Fallback: synchronous
    result = enroll_contacts_bulk(contact_ids, campaign_id, wid)
    app_logger.info(f'[SEQ] Enrolled sync | campaign {campaign_id} | {result}')
    return jsonify({'success': True, 'queued': False, **result})


@app.route('/api/sequence/<int:campaign_id>/pause/<int:contact_id>', methods=['POST'])
@login_required
def api_sequence_pause(campaign_id, contact_id):
    """Manually pause a contact's sequence."""
    from services.sequence_engine import pause_contact
    pause_contact(contact_id, campaign_id)
    return jsonify({'success': True, 'status': 'paused'})


@app.route('/api/sequence/<int:campaign_id>/resume/<int:contact_id>', methods=['POST'])
@login_required
def api_sequence_resume(campaign_id, contact_id):
    """Resume a paused contact's sequence."""
    from services.sequence_engine import resume_contact
    resume_contact(contact_id, campaign_id)
    return jsonify({'success': True, 'status': 'active'})


@app.route('/api/sequence/<int:campaign_id>/contacts')
@login_required
def api_sequence_contacts(campaign_id):
    """Get all contacts with their current sequence state."""
    from services.sequence_engine import get_campaign_contacts_state
    contacts = get_campaign_contacts_state(campaign_id)
    # Serialize datetimes
    for c in contacts:
        for k in ('next_run_at', 'last_sent_at', 'completed_at', 'created_at'):
            if c.get(k) and not isinstance(c[k], str):
                c[k] = c[k].isoformat()
    return jsonify({'contacts': contacts})


@app.route('/api/sequence/<int:campaign_id>/stats')
@login_required
def api_sequence_stats(campaign_id):
    """Get sequence stats — totals + per-step rates."""
    from services.sequence_engine import get_sequence_stats
    stats = get_sequence_stats(campaign_id)
    return jsonify(stats)


@app.route('/api/sequence/<int:campaign_id>/contact/<int:contact_id>/history')
@login_required
def api_sequence_contact_history(campaign_id, contact_id):
    """Get email send history for a contact in this sequence."""
    from services.sequence_engine import get_contact_sequence_history, get_contact_state
    history = get_contact_sequence_history(contact_id, campaign_id)
    state   = get_contact_state(contact_id, campaign_id)
    if state:
        for k in ('next_run_at', 'last_sent_at', 'completed_at', 'created_at'):
            if state.get(k) and not isinstance(state[k], str):
                state[k] = state[k].isoformat()
    return jsonify({'history': history, 'state': state})


@app.route('/api/sequence/<int:campaign_id>/trigger', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def api_sequence_trigger(campaign_id):
    """
    Manually trigger sequence processing for a campaign.
    Useful for testing without waiting for Beat.
    """
    from services.workspace_service import get_wid
    wid = get_wid()

    if CELERY_AVAILABLE and has_active_workers():
        from tasks.sequence_tasks import process_sequences_task
        result = process_sequences_task.apply_async(queue='automation_queue')
        return jsonify({'success': True, 'queued': True, 'task_id': result.id})

    # Fallback: run synchronously for this campaign only
    from services.sequence_engine import get_due_contacts, check_stop_conditions, \
        get_steps, get_contact_state, advance_state, mark_completed, \
        mark_stopped, calculate_next_run, get_smart_delay

    due = get_due_contacts(wid, limit=50)
    due = [d for d in due if d['campaign_id'] == campaign_id]
    processed = 0
    for cs in due:
        should_stop, reason = check_stop_conditions(cs['contact_id'], campaign_id)
        if should_stop:
            mark_stopped(cs['contact_id'], campaign_id, reason)
        processed += 1

    return jsonify({'success': True, 'queued': False, 'processed': processed})


# ==============================
# SEQUENCE ENGINE ROUTES — PART 1 (Step CRUD)
# ==============================


@app.route('/api/sequence/<int:campaign_id>/steps')
@login_required
def api_sequence_steps(campaign_id):
    """Get all steps for a campaign."""
    from services.sequence_engine import get_all_steps
    steps = get_all_steps(campaign_id)
    return jsonify({'steps': steps})


@app.route('/api/sequence/<int:campaign_id>/steps/add', methods=['POST'])
@login_required
def api_sequence_add_step(campaign_id):
    """Add a new step to a campaign sequence."""
    from services.sequence_engine import add_step, get_all_steps
    from services.workspace_service import get_wid
    data       = request.json or {}
    wid        = get_wid()
    steps      = get_all_steps(campaign_id)
    next_order = max((s['step_order'] for s in steps), default=0) + 1
    step_id = add_step(
        campaign_id  = campaign_id,
        workspace_id = wid,
        step_order   = int(data.get('step_order', next_order)),
        step_type    = data.get('step_type', 'email'),
        delay_days   = int(data.get('delay_days', 3)),
        subject      = data.get('subject', ''),
        body         = data.get('body', ''),
        ai_enabled   = bool(data.get('ai_enabled', False)),
    )
    app_logger.info(f'[SEQ] Step added: campaign {campaign_id} step_id {step_id}')
    return jsonify({'success': True, 'step_id': step_id})


@app.route('/api/sequence/step/<int:step_id>/update', methods=['POST'])
@login_required
def api_sequence_update_step(step_id):
    """Update an existing step."""
    from services.sequence_engine import update_step
    data = request.json or {}
    update_step(
        step_id    = step_id,
        step_order = int(data.get('step_order', 1)),
        step_type  = data.get('step_type', 'email'),
        delay_days = int(data.get('delay_days', 3)),
        subject    = data.get('subject', ''),
        body       = data.get('body', ''),
        ai_enabled = bool(data.get('ai_enabled', False)),
        active     = bool(data.get('active', True)),
    )
    return jsonify({'success': True})


@app.route('/api/sequence/step/<int:step_id>/delete', methods=['DELETE'])
@login_required
def api_sequence_delete_step(step_id):
    """Delete a step."""
    from services.sequence_engine import delete_step
    delete_step(step_id)
    return jsonify({'success': True})


@app.route('/api/sequence/<int:campaign_id>/steps/reorder', methods=['POST'])
@login_required
def api_sequence_reorder_steps(campaign_id):
    """
    Reorder steps.
    Body: {ordered_ids: [1, 3, 2, 4]}
    """
    from services.sequence_engine import reorder_steps
    ordered_ids = (request.json or {}).get('ordered_ids', [])
    if not ordered_ids:
        return jsonify({'success': False, 'error': 'ordered_ids required'})
    reorder_steps(campaign_id, ordered_ids)
    return jsonify({'success': True})


# ==============================
# OUTREACH COPILOT
# ==============================

# Old copilot routes removed — now served by routes/copilot.py (copilot_bp)


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
