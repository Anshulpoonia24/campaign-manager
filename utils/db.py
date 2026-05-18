"""
utils/db.py — OutreachOS Database Layer
========================================
PostgreSQL (primary) with SQLite fallback for local dev.
Auto-detects based on DATABASE_URL or DB_HOST env vars.

Connection pooling via psycopg2 ThreadedConnectionPool.
Row objects behave like dicts in both backends.
"""
import os
import sqlite3
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('campaign')

# ── DETECT BACKEND ────────────────────────────────────────────
DATABASE_URL = os.getenv('DATABASE_URL', '')
DB_HOST      = os.getenv('DB_HOST', '')
USE_POSTGRES = bool(DATABASE_URL or DB_HOST)

# ── SQLITE FALLBACK CONFIG ────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR  = os.getenv('DATA_DIR', '/home/data')
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    test = os.path.join(DATA_DIR, '.write_test')
    open(test, 'w').close()
    os.remove(test)
except (OSError, PermissionError):
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, 'campaigns.db')

# ── POSTGRES CONFIG ───────────────────────────────────────────
_pg_pool = None

def _build_pg_dsn():
    if DATABASE_URL:
        # Handle Azure/Heroku style postgres:// URLs
        url = DATABASE_URL
        if url.startswith('postgres://'):
            url = 'postgresql://' + url[len('postgres://'):]
        return url
    return (
        f"host={os.getenv('DB_HOST','localhost')} "
        f"port={os.getenv('DB_PORT','5432')} "
        f"dbname={os.getenv('DB_NAME','outreachos')} "
        f"user={os.getenv('DB_USER','postgres')} "
        f"password={os.getenv('DB_PASSWORD','')} "
        f"connect_timeout=10 "
        f"application_name=outreachos"
    )


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        try:
            from psycopg2 import pool as pg_pool
            _pg_pool = pg_pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=20,
                dsn=_build_pg_dsn()
            )
            logger.info('[DB] PostgreSQL connection pool initialized (2–20 connections)')
        except Exception as e:
            logger.error(f'[DB] PostgreSQL pool init failed: {e}')
            raise
    return _pg_pool


# ── ROW WRAPPER ───────────────────────────────────────────────
class PgRow:
    """
    Wraps a psycopg2 row + column names to behave like sqlite3.Row.
    Supports: row['col'], row[0], dict(row), row.keys(), 'col' in row
    """
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
    """
    Wraps psycopg2 cursor to return PgRow objects and support
    sqlite3-style ? placeholders (auto-converted to %s).
    """
    def __init__(self, cursor):
        self._cur = cursor

    def _convert(self, sql):
        """Convert SQLite ? placeholders to PostgreSQL %s."""
        return sql.replace('?', '%s')

    def execute(self, sql, params=None):
        self._cur.execute(self._convert(sql), params or ())
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(self._convert(sql), seq)
        return self

    def executescript(self, script):
        """Execute a multi-statement script (used in init_db)."""
        # Split on ; and execute each statement
        for stmt in script.split(';'):
            stmt = stmt.strip()
            if stmt:
                try:
                    self._cur.execute(stmt)
                except Exception:
                    pass  # Ignore IF NOT EXISTS errors etc.
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
    """
    Wraps a psycopg2 connection to match sqlite3 interface.
    Supports: conn.execute(), conn.executescript(), conn.commit(),
              conn.close(), conn.row_factory (ignored — PgRow handles it)
    """
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._conn.autocommit = False
        self.row_factory = None  # Ignored — PgRow handles this

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
        """Return connection to pool instead of closing."""
        try:
            pool = _get_pg_pool()
            pool.putconn(self._conn)
        except Exception:
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
    """
    Return a database connection.
    PostgreSQL if DATABASE_URL or DB_HOST is set, else SQLite.
    Always returns an object with sqlite3-compatible interface.
    """
    if USE_POSTGRES:
        try:
            pool = _get_pg_pool()
            raw = pool.getconn()
            return PgConnection(raw)
        except Exception as e:
            logger.error(f'[DB] PostgreSQL connection failed, falling back to SQLite: {e}')
            # Fall through to SQLite

    # SQLite fallback
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def get_setting(key, default=''):
    """Read a single setting value from the DB."""
    conn = get_db()
    try:
        row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def is_unsubscribed(email):
    """Check if email is in suppression list."""
    conn = get_db()
    try:
        row = conn.execute('SELECT id FROM unsubscribes WHERE email=?', (email.lower(),)).fetchone()
        return row is not None
    finally:
        conn.close()


def is_postgres():
    """Returns True if running on PostgreSQL."""
    return USE_POSTGRES


def get_db_url():
    """Return the active database URL (for logging/monitoring)."""
    if USE_POSTGRES:
        url = DATABASE_URL or f"postgresql://{os.getenv('DB_USER')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
        # Mask password
        import re
        return re.sub(r':([^@]+)@', ':***@', url)
    return f'sqlite:///{DB_PATH}'


# Alias for backward compatibility
get_db_connection = get_db
