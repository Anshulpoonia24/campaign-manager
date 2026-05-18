"""
add_workspaces.py — OutreachOS Multi-Tenant Migration
======================================================
Safe, incremental migration. Run once.

Steps:
  1. Create workspaces table
  2. Add workspace_id to users table
  3. Create Default Workspace
  4. Add workspace_id (nullable) to all tenant tables
  5. Backfill all existing rows → workspace_id = 1
  6. Verify

Usage:
    python add_workspaces.py
    python add_workspaces.py --verify
"""
import os, sys, sqlite3, argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── DB PATH ───────────────────────────────────────────────────
DATA_DIR = os.getenv('DATA_DIR', '/home/data')
if not os.path.isdir(DATA_DIR):
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
DB_PATH = os.path.join(DATA_DIR, 'campaigns.db')

# Tables that need workspace_id
TENANT_TABLES = [
    'contacts',
    'campaigns',
    'smtp_accounts',
    'threads',
    'follow_ups',
    'automation_settings',
    'email_clicks',
    'emails_sent',
    'ai_usage',
]


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def step(msg):
    print(f'  [{datetime.now().strftime("%H:%M:%S")}] {msg}')


def run_migration():
    print('=' * 55)
    print(' OutreachOS — Workspace Migration')
    print(f' DB: {DB_PATH}')
    print('=' * 55)

    conn = get_conn()

    # ── STEP 1: Create workspaces table ──────────────────────
    step('Creating workspaces table...')
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            slug       TEXT UNIQUE NOT NULL,
            plan       TEXT DEFAULT 'free',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    step('  workspaces table ready')

    # ── STEP 2: Add workspace_id to users ────────────────────
    step('Adding workspace_id to users...')
    users_cols = [r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()]
    if 'workspace_id' not in users_cols:
        conn.execute('ALTER TABLE users ADD COLUMN workspace_id INTEGER DEFAULT 1')
        conn.commit()
        step('  workspace_id added to users')
    else:
        step('  workspace_id already exists in users')

    # ── STEP 3: Create Default Workspace ─────────────────────
    step('Creating Default Workspace...')
    existing = conn.execute("SELECT id FROM workspaces WHERE id=1").fetchone()
    if not existing:
        conn.execute("""
            INSERT INTO workspaces (id, name, slug, plan, created_at)
            VALUES (1, 'Default Workspace', 'default', 'free', ?)
        """, (datetime.now(),))
        conn.commit()
        step('  Default Workspace created (id=1)')
    else:
        step('  Default Workspace already exists')

    # ── STEP 4: Add workspace_id to all tenant tables ────────
    step('Adding workspace_id to tenant tables...')
    for table in TENANT_TABLES:
        try:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
            if 'workspace_id' not in cols:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN workspace_id INTEGER DEFAULT 1')
                conn.commit()
                step(f'  {table}: workspace_id added')
            else:
                step(f'  {table}: workspace_id already exists')
        except Exception as e:
            step(f'  {table}: SKIP ({e})')

    # ── STEP 5: Backfill all existing rows ───────────────────
    step('Backfilling workspace_id = 1 for all existing rows...')
    for table in TENANT_TABLES:
        try:
            updated = conn.execute(
                f"UPDATE {table} SET workspace_id = 1 WHERE workspace_id IS NULL"
            ).rowcount
            conn.commit()
            if updated > 0:
                step(f'  {table}: {updated} rows backfilled')
        except Exception as e:
            step(f'  {table}: backfill skip ({e})')

    # Backfill users too
    conn.execute("UPDATE users SET workspace_id = 1 WHERE workspace_id IS NULL")
    conn.commit()
    step('  users: backfilled')

    # ── STEP 6: Add settings workspace_id ────────────────────
    # Settings table uses key/value — add workspace_id column
    step('Updating settings table...')
    settings_cols = [r[1] for r in conn.execute('PRAGMA table_info(settings)').fetchall()]
    if 'workspace_id' not in settings_cols:
        conn.execute('ALTER TABLE settings ADD COLUMN workspace_id INTEGER DEFAULT 1')
        conn.execute('UPDATE settings SET workspace_id = 1 WHERE workspace_id IS NULL')
        conn.commit()
        # Update PRIMARY KEY constraint isn't possible in SQLite ALTER TABLE
        # The key uniqueness is now (key, workspace_id) — enforced in app logic
        step('  settings: workspace_id added')
    else:
        step('  settings: workspace_id already exists')

    conn.close()

    print()
    print('=' * 55)
    print(' Migration complete!')
    print('=' * 55)


def verify():
    print('\n[VERIFY] Checking workspace isolation...')
    conn = get_conn()

    # Check workspaces table
    ws = conn.execute('SELECT * FROM workspaces').fetchall()
    print(f'  Workspaces: {len(ws)}')
    for w in ws:
        print(f'    id={w["id"]} name={w["name"]} slug={w["slug"]}')

    # Check workspace_id in each table
    for table in TENANT_TABLES + ['users', 'settings']:
        try:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
            has_ws = 'workspace_id' in cols
            if has_ws:
                total = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                assigned = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE workspace_id IS NOT NULL').fetchone()[0]
                print(f'  {table}: workspace_id={has_ws} total={total} assigned={assigned}')
            else:
                print(f'  {table}: NO workspace_id')
        except Exception as e:
            print(f'  {table}: ERROR {e}')

    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()

    if args.verify:
        verify()
    else:
        run_migration()
        verify()
