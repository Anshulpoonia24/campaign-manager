"""
utils/db.py — OutreachOS Database Layer
========================================
PostgreSQL via psycopg2 — simple query protocol, Supabase pooler compatible.
SQLite fallback for local dev.
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


# ── POSTGRES CONNECTION ───────────────────────────────────────
def _connect_pg():
    """Open a psycopg2 connection with RealDictCursor and autocommit."""
    import psycopg2
    import psycopg2.extras
    dsn = DATABASE_URL.strip()
    if dsn.startswith('postgres://'):
        dsn = 'postgresql://' + dsn[len('postgres://'):]
    conn = psycopg2.connect(dsn, sslmode='require', connect_timeout=10)
    conn.autocommit = True
    return conn


# ── SQL CONVERSION ────────────────────────────────────────────
def _convert_sql(sql):
    """Convert SQLite SQL to PostgreSQL-compatible SQL (%s params)."""
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


# ── ROW WRAPPER ───────────────────────────────────────────────
class PgRow:
    __slots__ = ('_data', '_keys')

    def __init__(self, keys, row):
        self._keys = list(keys)
        self._data = dict(zip(self._keys, row)) if not isinstance(row, dict) else row
        if isinstance(row, dict):
            self._keys = list(row.keys())

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


# ── CONNECTION WRAPPER ────────────────────────────────────────
class PgConnection:
    """
    Wraps psycopg2 connection with RealDictCursor to match sqlite3 interface.
    autocommit=True — no transaction state issues with Supabase pooler.
    """
    def __init__(self, raw_conn):
        self._conn   = raw_conn
        self._cursor = None

    @property
    def raw(self):
        return self._conn

    def _cur(self):
        import psycopg2.extras
        if self._cursor is None or self._cursor.closed:
            self._cursor = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self._cursor

    def execute(self, sql, params=None):
        converted = _convert_sql(sql)
        cur = self._cur()
        cur.execute(converted, list(params) if params else None)
        return self

    def executemany(self, sql, seq):
        converted = _convert_sql(sql)
        cur = self._cur()
        for params in seq:
            cur.execute(converted, list(params))
        return self

    def executescript(self, script):
        """DDL: run each statement individually, ignore errors (safe migrations)."""
        # Set session-level timeout so no single DDL can hang startup
        try:
            cur = self._conn.cursor()
            cur.execute('SET statement_timeout = 8000')
            cur.close()
        except Exception:
            pass
        for stmt in script.split(';'):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                cur = self._conn.cursor()
                cur.execute(stmt)
                cur.close()
            except Exception:
                pass  # Column/table already exists, or timeout — skip
        self._cursor = None
        return self

    def fetchone(self):
        if self._cursor is None:
            return None
        row = self._cursor.fetchone()
        if row is None:
            return None
        return PgRow(list(row.keys()), row)

    def fetchall(self):
        if self._cursor is None:
            return []
        rows = self._cursor.fetchall()
        return [PgRow(list(r.keys()), r) for r in rows]

    def commit(self):
        pass  # autocommit=True

    def rollback(self):
        pass  # autocommit=True

    def close(self):
        try:
            if self._cursor and not self._cursor.closed:
                self._cursor.close()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ── PUBLIC API ────────────────────────────────────────────────
def get_db():
    if USE_POSTGRES:
        try:
            return PgConnection(_connect_pg())
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


def _build_pg_dsn():
    return DATABASE_URL


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
        return re.sub(r':([^@]+)@', ':***@', DATABASE_URL or '')
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
