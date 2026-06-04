"""
utils/db.py — OutreachOS Database Layer
========================================
PostgreSQL (primary) with SQLite fallback for local dev.
Uses pg8000 (pure Python) — fully compatible with Python 3.14.
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
def _parse_pg_url():
    """Parse DATABASE_URL into pg8000 connect kwargs."""
    url = DATABASE_URL
    if not url:
        return {
            'host':     os.getenv('DB_HOST', 'localhost'),
            'port':     int(os.getenv('DB_PORT', 5432)),
            'database': os.getenv('DB_NAME', 'outreachos'),
            'user':     os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD', ''),
            'ssl_context': True,
        }
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    # Manual parse to handle passwords with special chars like @
    # Format: postgresql://user:password@host:port/dbname
    # Split from the right on @ to get host part
    from urllib.parse import unquote
    # Remove scheme
    rest = url.split('://', 1)[1]  # user:password@host:port/dbname
    # Split on last @ to separate credentials from host
    at_idx = rest.rfind('@')
    credentials = rest[:at_idx]   # user:password
    hostpart    = rest[at_idx+1:] # host:port/dbname
    # Split credentials
    colon_idx = credentials.find(':')
    user     = unquote(credentials[:colon_idx])
    password = unquote(credentials[colon_idx+1:])
    # Split host and path
    slash_idx = hostpart.find('/')
    hostport  = hostpart[:slash_idx]
    database  = hostpart[slash_idx+1:]
    if ':' in hostport:
        host, port = hostport.rsplit(':', 1)
        port = int(port)
    else:
        host = hostport
        port = 5432
    return {
        'host':        host,
        'port':        port,
        'database':    database,
        'user':        user,
        'password':    password,
        'ssl_context': True,
    }


def _connect_pg():
    """Create a fresh pg8000 connection."""
    import pg8000.native
    kwargs = _parse_pg_url()
    # Single attempt — no retry loop (prevents circuit breaker hammering)
    conn = pg8000.native.Connection(**kwargs)
    return conn


# ── ROW WRAPPER ───────────────────────────────────────────────
class PgRow:
    __slots__ = ('_data', '_keys')

    def __init__(self, keys, row):
        self._keys = keys
        self._data = dict(zip(keys, row))

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
    Wraps pg8000.native.Connection to match sqlite3 interface.
    pg8000.native uses %s placeholders natively.
    """
    def __init__(self, raw_conn):
        self._conn         = raw_conn
        self._last_rows    = []
        self._last_columns = []

    @property
    def raw(self):
        return self._conn

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
        try:
            if params is None:
                rows = self._conn.run(converted)
            else:
                rows = self._conn.run(converted, *list(params))
            self._last_rows    = rows or []
            self._last_columns = [c['name'] for c in (self._conn.columns or [])]
        except Exception:
            self._last_rows    = []
            self._last_columns = []
            raise
        return self

    def executemany(self, sql, seq):
        converted = self._convert(sql)
        for params in seq:
            self._conn.run(converted, *list(params))
        self._last_rows    = []
        self._last_columns = []
        return self

    def executescript(self, script):
        for stmt in script.split(';'):
            stmt = stmt.strip()
            if stmt:
                try:
                    self._conn.run(stmt)
                except Exception:
                    pass
        return self

    def fetchone(self):
        if not self._last_rows:
            return None
        return PgRow(self._last_columns, self._last_rows[0])

    def fetchall(self):
        return [PgRow(self._last_columns, r) for r in self._last_rows]

    def commit(self):
        self._conn.run('COMMIT')
        self._conn.run('BEGIN')

    def rollback(self):
        try:
            self._conn.run('ROLLBACK')
            self._conn.run('BEGIN')
        except Exception:
            pass

    def close(self):
        try:
            self._conn.run('COMMIT')
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
            conn = PgConnection(raw)
            conn._conn.run('BEGIN')
            return conn
        except Exception as e:
            err_msg = str(e)
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


def _connect_pg_legacy():
    return _connect_pg()


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
