import os
import sqlite3
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()

# PERSISTENT STORAGE: Azure uses /home, local uses project dir
if os.path.isdir('/home') and os.name != 'nt':
    DATA_DIR = '/home/data'
else:
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, 'campaigns.db')

DEFAULT_SETTINGS = {
    'gemini_api_key': os.getenv('GEMINI_API_KEY', ''),
    'groq_api_keys': os.getenv('GROQ_API_KEYS', ''),
    'ollama_url': os.getenv('OLLAMA_URL', 'http://localhost:11434'),
    'ollama_model': os.getenv('OLLAMA_MODEL', 'qwen2.5:7b'),
    'ai_priority': os.getenv('AI_PRIORITY', 'ollama,groq,gemini'),
    'smtp_server': os.getenv('SMTP_SERVER', 'smtp.hostinger.com'),
    'smtp_port': os.getenv('SMTP_PORT', '587'),
    'smtp_username': os.getenv('SMTP_USERNAME', ''),
    'smtp_password': os.getenv('SMTP_PASSWORD', ''),
    'from_email': os.getenv('FROM_EMAIL', ''),
    'from_name': os.getenv('FROM_NAME', ''),
    'reply_to': os.getenv('REPLY_TO', ''),
    'bcc_emails': os.getenv('BCC_EMAILS', ''),
    'tracking_host': os.getenv('TRACKING_HOST', 'http://localhost:5000'),
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


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


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


def is_unsubscribed(email):
    conn = get_db()
    row = conn.execute("SELECT id FROM unsubscribes WHERE email=?", (email.lower(),)).fetchone()
    conn.close()
    return row is not None


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

    existing_user = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
    if not existing_user:
        default_hash = generate_password_hash('admin123')
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                     ('admin', default_hash, 'admin'))
        conn.commit()
        print("[AUTH] Default admin created — username: admin, password: admin123")

    for k, v in DEFAULT_SETTINGS.items():
        existing = conn.execute("SELECT key FROM settings WHERE key=?", (k,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", (k, v))
    conn.commit()
    conn.close()
