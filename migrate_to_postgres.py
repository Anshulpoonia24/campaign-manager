"""
migrate_to_postgres.py — OutreachOS SQLite → PostgreSQL Migration
==================================================================
Usage:
    python migrate_to_postgres.py --check      # Verify connection only
    python migrate_to_postgres.py --schema     # Create schema only
    python migrate_to_postgres.py --migrate    # Full data migration
    python migrate_to_postgres.py --indexes    # Add indexes only

Requires: DATABASE_URL or DB_HOST/DB_NAME/DB_USER/DB_PASSWORD in .env
"""
import os
import sys
import sqlite3
import argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────
SQLITE_PATH = os.path.join(
    os.getenv('DATA_DIR', '/home/data') if os.path.isdir(os.getenv('DATA_DIR', '/home/data'))
    else os.path.join(os.path.dirname(__file__), 'data'),
    'campaigns.db'
)

DATABASE_URL = os.getenv('DATABASE_URL', '')
if not DATABASE_URL:
    DATABASE_URL = (
        f"postgresql://{os.getenv('DB_USER','postgres')}:"
        f"{os.getenv('DB_PASSWORD','')}@"
        f"{os.getenv('DB_HOST','localhost')}:"
        f"{os.getenv('DB_PORT','5432')}/"
        f"{os.getenv('DB_NAME','outreachos')}"
    )

# ── POSTGRESQL SCHEMA ─────────────────────────────────────────
PG_SCHEMA = """
-- Users
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT DEFAULT 'admin',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Contacts
CREATE TABLE IF NOT EXISTS contacts (
    id                 SERIAL PRIMARY KEY,
    name               TEXT NOT NULL,
    company            TEXT,
    email              TEXT UNIQUE NOT NULL,
    designation        TEXT,
    priority           TEXT,
    status             TEXT DEFAULT 'new',
    email_valid        INTEGER DEFAULT -1,
    validation_reason  TEXT,
    lead_score         INTEGER DEFAULT 0,
    website            TEXT DEFAULT '',
    context            TEXT DEFAULT '',
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Campaigns
CREATE TABLE IF NOT EXISTS campaigns (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    status      TEXT DEFAULT 'draft',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Emails sent
CREATE TABLE IF NOT EXISTS emails_sent (
    id          SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES campaigns(id),
    contact_id  INTEGER REFERENCES contacts(id),
    email       TEXT NOT NULL,
    subject     TEXT,
    body        TEXT,
    status      TEXT DEFAULT 'pending',
    bounce_reason TEXT,
    opened      INTEGER DEFAULT 0,
    replied     INTEGER DEFAULT 0,
    tracking_id TEXT,
    sent_at     TIMESTAMP
);

-- Follow-ups
CREATE TABLE IF NOT EXISTS follow_ups (
    id          SERIAL PRIMARY KEY,
    contact_id  INTEGER REFERENCES contacts(id),
    email       TEXT,
    name        TEXT,
    company     TEXT,
    replied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes       TEXT
);

-- Settings
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- AI usage
CREATE TABLE IF NOT EXISTS ai_usage (
    id         SERIAL PRIMARY KEY,
    provider   TEXT NOT NULL,
    purpose    TEXT DEFAULT 'email',
    success    INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Unsubscribes
CREATE TABLE IF NOT EXISTS unsubscribes (
    id              SERIAL PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    reason          TEXT DEFAULT '',
    unsubscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- SMTP accounts
CREATE TABLE IF NOT EXISTS smtp_accounts (
    id           SERIAL PRIMARY KEY,
    email        TEXT UNIQUE NOT NULL,
    password     TEXT NOT NULL,
    smtp_server  TEXT DEFAULT 'smtp.hostinger.com',
    smtp_port    INTEGER DEFAULT 587,
    from_name    TEXT DEFAULT '',
    daily_limit  INTEGER DEFAULT 50,
    sent_today   INTEGER DEFAULT 0,
    health_score INTEGER DEFAULT 100,
    warmup_stage INTEGER DEFAULT 1,
    active       INTEGER DEFAULT 1,
    last_used    TIMESTAMP,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Threads
CREATE TABLE IF NOT EXISTS threads (
    id              SERIAL PRIMARY KEY,
    contact_id      INTEGER REFERENCES contacts(id),
    campaign_id     INTEGER REFERENCES campaigns(id),
    subject         TEXT,
    status          TEXT DEFAULT 'active',
    unread_count    INTEGER DEFAULT 0,
    last_message_at TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
    id              SERIAL PRIMARY KEY,
    thread_id       INTEGER REFERENCES threads(id),
    direction       TEXT,
    sender_email    TEXT,
    recipient_email TEXT,
    subject         TEXT,
    body            TEXT,
    message_id      TEXT,
    in_reply_to     TEXT,
    ai_category     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Automation settings
CREATE TABLE IF NOT EXISTS automation_settings (
    id            SERIAL PRIMARY KEY,
    rule_key      TEXT UNIQUE NOT NULL,
    enabled       INTEGER DEFAULT 1,
    delay_days    INTEGER DEFAULT 2,
    max_followups INTEGER DEFAULT 3,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Email clicks
CREATE TABLE IF NOT EXISTS email_clicks (
    id            SERIAL PRIMARY KEY,
    email_sent_id INTEGER,
    thread_id     INTEGER,
    contact_id    INTEGER,
    clicked_url   TEXT,
    token         TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ── INDEXES ───────────────────────────────────────────────────
PG_INDEXES = """
-- Contacts
CREATE INDEX IF NOT EXISTS idx_contacts_email       ON contacts(email);
CREATE INDEX IF NOT EXISTS idx_contacts_company     ON contacts(company);
CREATE INDEX IF NOT EXISTS idx_contacts_status      ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_contacts_lead_score  ON contacts(lead_score DESC);
CREATE INDEX IF NOT EXISTS idx_contacts_email_valid ON contacts(email_valid);

-- Emails sent
CREATE INDEX IF NOT EXISTS idx_emails_campaign   ON emails_sent(campaign_id);
CREATE INDEX IF NOT EXISTS idx_emails_contact    ON emails_sent(contact_id);
CREATE INDEX IF NOT EXISTS idx_emails_status     ON emails_sent(status);
CREATE INDEX IF NOT EXISTS idx_emails_tracking   ON emails_sent(tracking_id);
CREATE INDEX IF NOT EXISTS idx_emails_sent_at    ON emails_sent(sent_at DESC);

-- Threads
CREATE INDEX IF NOT EXISTS idx_threads_contact    ON threads(contact_id);
CREATE INDEX IF NOT EXISTS idx_threads_campaign   ON threads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_threads_status     ON threads(status);
CREATE INDEX IF NOT EXISTS idx_threads_last_msg   ON threads(last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_threads_unread     ON threads(unread_count) WHERE unread_count > 0;

-- Messages
CREATE INDEX IF NOT EXISTS idx_messages_thread     ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_created    ON messages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_msg_id     ON messages(message_id);
CREATE INDEX IF NOT EXISTS idx_messages_direction  ON messages(direction);
CREATE INDEX IF NOT EXISTS idx_messages_category   ON messages(ai_category);

-- Email clicks
CREATE INDEX IF NOT EXISTS idx_clicks_contact   ON email_clicks(contact_id);
CREATE INDEX IF NOT EXISTS idx_clicks_campaign  ON email_clicks(email_sent_id);
CREATE INDEX IF NOT EXISTS idx_clicks_created   ON email_clicks(created_at DESC);

-- SMTP accounts
CREATE INDEX IF NOT EXISTS idx_smtp_active  ON smtp_accounts(active);
CREATE INDEX IF NOT EXISTS idx_smtp_health  ON smtp_accounts(health_score DESC);

-- AI usage
CREATE INDEX IF NOT EXISTS idx_ai_usage_provider ON ai_usage(provider);
CREATE INDEX IF NOT EXISTS idx_ai_usage_date     ON ai_usage(created_at DESC);
"""

# ── TABLES TO MIGRATE ─────────────────────────────────────────
TABLES = [
    'users', 'contacts', 'campaigns', 'emails_sent', 'follow_ups',
    'settings', 'ai_usage', 'unsubscribes', 'smtp_accounts',
    'threads', 'messages', 'automation_settings', 'email_clicks',
]


def get_pg_conn():
    import psycopg2
    url = DATABASE_URL
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    return psycopg2.connect(url)


def get_sqlite_conn():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def check_connection():
    print(f'\n[CHECK] Testing PostgreSQL connection...')
    print(f'  URL: {DATABASE_URL[:40]}...')
    try:
        conn = get_pg_conn()
        cur = conn.cursor()
        cur.execute('SELECT version()')
        version = cur.fetchone()[0]
        print(f'  ✓ Connected: {version[:50]}')
        conn.close()
        return True
    except Exception as e:
        print(f'  ✗ Failed: {e}')
        return False


def create_schema():
    print('\n[SCHEMA] Creating PostgreSQL schema...')
    conn = get_pg_conn()
    cur = conn.cursor()
    try:
        for stmt in PG_SCHEMA.split(';'):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        conn.commit()
        print('  ✓ Schema created')
    except Exception as e:
        conn.rollback()
        print(f'  ✗ Schema error: {e}')
        raise
    finally:
        conn.close()


def create_indexes():
    print('\n[INDEXES] Creating performance indexes...')
    conn = get_pg_conn()
    cur = conn.cursor()
    created = 0
    for stmt in PG_INDEXES.split(';'):
        stmt = stmt.strip()
        if stmt:
            try:
                cur.execute(stmt)
                conn.commit()
                created += 1
            except Exception as e:
                conn.rollback()
                print(f'  ⚠ Index warning: {e}')
    print(f'  ✓ {created} indexes created')
    conn.close()


def migrate_table(table, sqlite_conn, pg_conn):
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    # Get rows from SQLite
    try:
        sqlite_cur.execute(f'SELECT * FROM {table}')
    except sqlite3.OperationalError:
        print(f'  ⚠ Table {table} not found in SQLite — skipping')
        return 0

    rows = sqlite_cur.fetchall()
    if not rows:
        print(f'  - {table}: 0 rows (empty)')
        return 0

    cols = [d[0] for d in sqlite_cur.description]
    placeholders = ', '.join(['%s'] * len(cols))
    col_names = ', '.join(cols)

    inserted = 0
    skipped = 0
    for row in rows:
        try:
            pg_cur.execute(
                f'INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING',
                list(row)
            )
            inserted += 1
        except Exception as e:
            skipped += 1
            if skipped <= 3:
                print(f'    ⚠ Row skip: {str(e)[:80]}')

    pg_conn.commit()
    print(f'  ✓ {table}: {inserted} rows migrated, {skipped} skipped')
    return inserted


def migrate_data():
    print('\n[MIGRATE] Starting data migration...')
    print(f'  Source: {SQLITE_PATH}')

    if not os.path.exists(SQLITE_PATH):
        print(f'  ✗ SQLite file not found: {SQLITE_PATH}')
        return

    sqlite_conn = get_sqlite_conn()
    pg_conn = get_pg_conn()

    # Disable FK checks during migration
    pg_conn.cursor().execute('SET session_replication_role = replica')
    pg_conn.commit()

    total = 0
    for table in TABLES:
        total += migrate_table(table, sqlite_conn, pg_conn)

    # Re-enable FK checks
    pg_conn.cursor().execute('SET session_replication_role = DEFAULT')
    pg_conn.commit()

    # Reset sequences
    print('\n[SEQUENCES] Resetting auto-increment sequences...')
    pg_cur = pg_conn.cursor()
    for table in TABLES:
        try:
            pg_cur.execute(f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1)
                )
            """)
            pg_conn.commit()
        except Exception:
            pg_conn.rollback()

    sqlite_conn.close()
    pg_conn.close()
    print(f'\n[DONE] Migration complete. {total} total rows migrated.')


def verify_migration():
    print('\n[VERIFY] Comparing row counts...')
    sqlite_conn = get_sqlite_conn()
    pg_conn = get_pg_conn()
    pg_cur = pg_conn.cursor()

    all_ok = True
    for table in TABLES:
        try:
            sqlite_count = sqlite_conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            pg_cur.execute(f'SELECT COUNT(*) FROM {table}')
            pg_count = pg_cur.fetchone()[0]
            status = '✓' if sqlite_count == pg_count else '✗'
            if sqlite_count != pg_count:
                all_ok = False
            print(f'  {status} {table}: SQLite={sqlite_count} PG={pg_count}')
        except Exception as e:
            print(f'  ⚠ {table}: {e}')

    sqlite_conn.close()
    pg_conn.close()
    return all_ok


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='OutreachOS SQLite → PostgreSQL Migration')
    parser.add_argument('--check',   action='store_true', help='Test PostgreSQL connection')
    parser.add_argument('--schema',  action='store_true', help='Create schema only')
    parser.add_argument('--indexes', action='store_true', help='Create indexes only')
    parser.add_argument('--migrate', action='store_true', help='Full data migration')
    parser.add_argument('--verify',  action='store_true', help='Verify row counts')
    parser.add_argument('--all',     action='store_true', help='Run full migration (schema + data + indexes + verify)')
    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        sys.exit(0)

    print('=' * 50)
    print(' OutreachOS PostgreSQL Migration')
    print(f' {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 50)

    if args.check or args.all:
        if not check_connection():
            sys.exit(1)

    if args.schema or args.all:
        create_schema()

    if args.migrate or args.all:
        migrate_data()

    if args.indexes or args.all:
        create_indexes()

    if args.verify or args.all:
        ok = verify_migration()
        if not ok:
            print('\n⚠ Row count mismatch detected. Check migration logs.')
            sys.exit(1)

    print('\n✓ All done.')
