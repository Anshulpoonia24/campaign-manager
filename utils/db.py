"""
utils/db.py — OutreachOS Database Layer
========================================
PostgreSQL (primary) with SQLite fallback for local dev.
Python 3.12 + psycopg2-binary — fully compatible.
"""
import os
import sqlite3
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('campaign')

# ── DETECT BACKEND ────────────────────────────────────────────
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
DB_HOST      = os.getenv('DB_HOST', '').strip()
USE_POSTGRES = bool(DATABASE_URL or DB_HOST)
print(f'[DB] USE_POSTGRES={USE_POSTGRES} | DATABASE_URL={"set" if DATABASE_URL else "empty"} | DB_HOST={"set" if DB_HOST else "empty"}')

# ── SQLITE FALLBACK CONFIG ────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _resolve_data_dir():
    for path in ['/home/data', '/opt/render/project/src/data']:
        try:
            os.makedirs(path, exist_ok=True)
            test = os.path.join(path, '.db_write_test')
            open(test, 'w').close()
            os.remove(test)
            return path
        except (OSError, PermissionError):
            pass
    local = os.path.join(BASE_DIR, 'data')
    os.makedirs(local, exist_ok=True)
    return local

DATA_DIR = _resolve_data_dir()
DB_PATH  = os.path.join(DATA_DIR, 'campaigns.db')


# ── POSTGRES CONFIG ───────────────────────────────────────────
def _build_pg_dsn():
    if DATABASE_URL:
        url = DATABASE_URL.strip()
        if url.startswith('postgres://'):
            url = 'postgresql://' + url[len('postgres://'):]
        if 'sslmode' not in url:
            sep = '&' if '?' in url else '?'
            url += sep + 'sslmode=require'
        return url
    return (
        f"host={os.getenv('DB_HOST','localhost')} "
        f"port={os.getenv('DB_PORT','5432')} "
        f"dbname={os.getenv('DB_NAME','outreachos')} "
        f"user={os.getenv('DB_USER','postgres')} "
        f"password={os.getenv('DB_PASSWORD','')} "
        f"connect_timeout=10"
    )


def _connect_pg():
    """Create a fresh psycopg2 connection."""
    import psycopg2
    dsn = _build_pg_dsn()
    conn = psycopg2.connect(dsn, connect_timeout=10)
    return conn


# ── ROW WRAPPER ───────────────────────────────────────────────
class PgRow:
    __slots__ = ('_data', '_keys')

    def __init__(self, cursor, row):
        self._keys = [d[0] for d in cursor.description]
        self._data = dict(zip(self._keys, row))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._data[self._keys[key]]
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return self._keys

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __iter__(self):
        return iter(self._data.values())

    def __repr__(self):
        return f'PgRow({self._data})'


class PgCursor:
    def __init__(self, cursor):
        self._cur = cursor

    def _convert(self, sql):
        import re
        is_ignore = bool(re.search(r'INSERT\s+OR\s+IGNORE', sql, re.IGNORECASE))
        sql = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', sql, flags=re.IGNORECASE)
        sql = re.sub(r'INSERT\s+OR\s+REPLACE\s+INTO', 'INSERT INTO', sql, flags=re.IGNORECASE)
        sql = sql.replace('?', '%s')
        if is_ignore and 'ON CONFLICT' not in sql.upper():
            sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
        sql = re.sub(r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', 'SERIAL PRIMARY KEY', sql, flags=re.IGNORECASE)
        sql = re.sub(r"datetime\('now'\)", 'CURRENT_TIMESTAMP', sql, flags=re.IGNORECASE)
        sql = re.sub(r"DATE\('now'\)", 'CURRENT_DATE', sql, flags=re.IGNORECASE)
        return sql

    def execute(self, sql, params=None):
        converted = self._convert(sql)
        if params is None:
            self._cur.execute(converted)
        else:
            self._cur.execute(converted, tuple(params))
        return self

    def executemany(self, sql, seq):
        converted = self._convert(sql)
        self._cur.executemany(converted, [tuple(p) for p in seq])
        return self

    def executescript(self, script):
        for stmt in script.split(';'):
            stmt = stmt.strip()
            if stmt:
                try:
                    self._cur.execute(stmt)
                except Exception:
                    pass
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return PgRow(self._cur, row)

    def fetchall(self):
        rows = self._cur.fetchall()
        return [PgRow(self._cur, r) for r in rows]

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return self._cur.rowcount

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._cur.close()


class PgConnection:
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._conn.autocommit = False
        self.row_factory = None

    @property
    def raw(self):
        return self._conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        wrapped = PgCursor(cur)
        wrapped.execute(sql, params)
        return wrapped

    def executescript(self, script):
        cur = self._conn.cursor()
        wrapped = PgCursor(cur)
        wrapped.executescript(script)
        self._conn.commit()
        return wrapped

    def cursor(self):
        return PgCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


# ── PUBLIC API ────────────────────────────────────────────────
def get_db():
    if USE_POSTGRES:
        try:
            raw  = _connect_pg()
            return PgConnection(raw)
        except Exception as e:
            print(f'[DB] PostgreSQL FAILED: {e}')
            logger.error(f'[DB] PostgreSQL connection failed: {e}')
            if os.getenv('RENDER') or os.getenv('WEBSITE_HOSTNAME') or os.getenv('PORT'):
                raise RuntimeError(f'PostgreSQL unavailable: {e}')

    conn = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=60000')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=-8000')
    return conn


def is_postgres():
    return USE_POSTGRES


def _build_pg_dsn_export():
    return _build_pg_dsn()


def get_workspace_only_setting(key, workspace_id, default=''):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=? AND workspace_id=?",
            (key, workspace_id)
        ).fetchone()
        return row[0] if row else default
    except Exception:
        return default
    finally:
        conn.close()


def get_setting(key, default=''):
    conn = get_db()
    try:
        row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def is_unsubscribed(email):
    conn = get_db()
    try:
        row = conn.execute('SELECT id FROM unsubscribes WHERE email=?', (email.lower(),)).fetchone()
        return row is not None
    finally:
        conn.close()


def get_db_url():
    if USE_POSTGRES:
        import re
        url = DATABASE_URL or ''
        return re.sub(r':([^@]+)@', ':***@', url)
    return f'sqlite:///{DB_PATH}'


get_db_connection = get_db


# ── SEND RESERVATION HELPERS ─────────────────────────────────
def cleanup_stale_reservations(conn, campaign_id, workspace_id, stale_minutes=10):
    if USE_POSTGRES:
        conn.execute("""
            UPDATE send_reservations SET status='failed', updated_at=CURRENT_TIMESTAMP
            WHERE campaign_id=? AND workspace_id=? AND status='sending'
              AND reserved_at < NOW() - INTERVAL '1 minute' * ?
        """, (campaign_id, workspace_id, stale_minutes))
    else:
        conn.execute("""
            UPDATE send_reservations SET status='failed', updated_at=CURRENT_TIMESTAMP
            WHERE campaign_id=? AND workspace_id=? AND status='sending'
              AND reserved_at < datetime('now', ?)
        """, (campaign_id, workspace_id, f'-{stale_minutes} minutes'))
    conn.commit()


def claim_reservation(conn, workspace_id, contact_id, campaign_id, send_key):
    if USE_POSTGRES:
        conn.execute("""
            INSERT INTO send_reservations
                (workspace_id, contact_id, campaign_id, send_key, status, reserved_at, updated_at)
            VALUES (?, ?, ?, ?, 'sending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (workspace_id, contact_id, campaign_id, send_key) DO NOTHING
        """, (workspace_id, contact_id, campaign_id, send_key))
    else:
        conn.execute("""
            INSERT OR IGNORE INTO send_reservations
                (workspace_id, contact_id, campaign_id, send_key, status, reserved_at, updated_at)
            VALUES (?, ?, ?, ?, 'sending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (workspace_id, contact_id, campaign_id, send_key))

    row = conn.execute("""
        SELECT id, status FROM send_reservations
        WHERE workspace_id=? AND contact_id=? AND campaign_id=? AND send_key=?
    """, (workspace_id, contact_id, campaign_id, send_key)).fetchone()

    if not row:
        return 'in_progress'
    if row['status'] == 'sent':
        return 'skip'
    if row['status'] == 'sending':
        conn.commit()
        return 'claimed'
    if row['status'] == 'failed':
        conn.execute("""
            UPDATE send_reservations SET status='sending',
                reserved_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='failed'
        """, (row['id'],))
        conn.commit()
        return 'claimed'
    return 'in_progress'


def complete_reservation(conn, workspace_id, contact_id, campaign_id, send_key, success):
    status = 'sent' if success else 'failed'
    conn.execute("""
        UPDATE send_reservations SET status=?, updated_at=CURRENT_TIMESTAMP
        WHERE workspace_id=? AND contact_id=? AND campaign_id=? AND send_key=?
    """, (status, workspace_id, contact_id, campaign_id, send_key))
    conn.commit()
