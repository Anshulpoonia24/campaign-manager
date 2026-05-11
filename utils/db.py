import os
import sqlite3
from werkzeug.security import generate_password_hash

# HARDCODED persistent path for Azure
DATA_DIR = '/home/data'
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, 'campaigns.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


# Alias for compatibility
get_db_connection = get_db
