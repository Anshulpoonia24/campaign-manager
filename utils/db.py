"""
utils/db.py — OutreachOS Database Layer
========================================
PostgreSQL via pg8000 (pure Python) — no C extension, no signal crash.
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


# ── POSTGRES URL PARSER ───────────────────────────────────────
def _parse_pg_url():
    """Parse DATABASE_URL into pg8000 kwargs. Handles @ in password via rfind."""
    url = DATABASE_URL.strip()
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]

    rest     = url.split('://', 1)[1]
    at_idx   = rest.rfind('@')
    creds    = rest[:at_idx]
    hostpart = rest[at_idx + 1:]

    colon_idx = creds.find(':')
    from urllib.parse import unquote
    user     = unquote(creds[:colon_idx])
    password = unquote(creds[colon_idx + 1:])

    slash_idx = hostpart.find('/')
    hostport  = hostpart[:slash_idx]
    database  = hostpart[slash_idx + 1:].split('?')[0]

    if ':' in hostport:
        host, port = hostport.rsplit(':', 1)
        port = int(port)
    else:
        host = hostport
        port = 5432

    # SSL context — disable cert verification for Supabase pooler
    import ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    return {'host': host, 'port': port, 'database': database,
            'user': user, 'password': password, 'ssl_context': ssl_ctx}


def _connect_pg():
    import pg8000.native
    return pg8000.native.Connection(**_parse_pg_url())


# ── SQL CONVERSION ────────────────────────────────────────────
def _convert_sql(sql):
    """Convert SQLite SQL to PostgreSQL-compatible SQL for pg8000.
    - ? → $1, $2, $3 (pg8000 uses numbered params)
    - INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    - SQLite-specific syntax → PostgreSQL
    """
    import re
    is_ignore = bool(re.search(r'INSERT\s+OR\s+IGNORE', sql, re.IGNORECASE))
    sql = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', sql, flags=re.IGNORECASE)
    sql = re.sub(r'INSERT\s+OR\s+REPLACE\s+INTO', 'INSERT INTO', sql, flags=re.IGNORECASE)
    # ? → $1, $2 ...
    counter = [0]
    def _repl(m):
        counter[0] += 1
        return f'${counter[0]}'
    sql = re.sub(r'\?', _repl, sql)
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


# ── CONNECTION WRAPPER ────────────────────────────────────────
class PgConnection:
    """
    Wraps pg8000.native.Connection to match sqlite3 interface.
    - execute(sql, params) — converts ? to $1,$2 and runs query
    - executescript(sql)   — DDL only, runs each stmt with autocommit
    - commit() / rollback() / close()
    """
    def __init__(self, raw_conn):
        self._conn         = raw_conn
        self._last_rows    = []
        self._last_columns = []
        self._in_tx        = False
        self._begin()

    def _begin(self):
        try:
            self._conn.run('BEGIN')
            self._in_tx = True
        except Exception:
            # Connection may be in aborted state — rollback and retry
            try:
                self._conn.run('ROLLBACK')
                self._conn.run('BEGIN')
            except Exception:
                pass
            self._in_tx = True

    @property
    def raw(self):
        return self._conn

    def execute(self, sql, params=None):
        converted = _convert_sql(sql)
        try:
            if params is None:
                rows = self._conn.run(converted)
            else:
                rows = self._conn.run(converted, *list(params))
            self._last_rows    = rows or []
            self._last_columns = [c['name'] for c in (self._conn.columns or [])]
        except Exception as e:
            # If connection is in aborted state, rollback and retry once
            err_str = str(e)
            if 'bind message supplies 0 parameters' in err_str or 'transaction is aborted' in err_str.lower():
                try:
                    self._conn.run('ROLLBACK')
                    self._conn.run('BEGIN')
                    self._in_tx = True
                    if params is None:
                        rows = self._conn.run(converted)
                    else:
                        rows = self._conn.run(converted, *list(params))
                    self._last_rows    = rows or []
                    self._last_columns = [c['name'] for c in (self._conn.columns or [])]
                    return self
                except Exception:
                    pass
            self._last_rows    = []
            self._last_columns = []
            raise
        return self

    def executemany(self, sql, seq):
        converted = _convert_sql(sql)
        for params in seq:
            self._conn.run(converted, *list(params))
        self._last_rows    = []
        self._last_columns = []
        return self

    def executescript(self, script):
        """DDL-safe: commits current tx, runs each stmt standalone, restarts tx."""
        # Commit any pending transaction first
        try:
            self._conn.run('COMMIT')
        except Exception:
            pass
        self._in_tx = False

        for stmt in script.split(';'):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                self._conn.run(stmt)
            except Exception:
                pass  # IF NOT EXISTS etc — safe to ignore

        # Restart transaction for subsequent DML
        self._begin()
        return self

    def fetchone(self):
        if not self._last_rows:
            return None
        return PgRow(self._last_columns, self._last_rows[0])

    def fetchall(self):
        return [PgRow(self._last_columns, r) for r in self._last_rows]

    def commit(self):
        try:
            self._conn.run('COMMIT')
        except Exception:
            pass
        self._begin()

    def rollback(self):
        try:
            self._conn.run('ROLLBACK')
        except Exception:
            pass
        self._begin()

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
