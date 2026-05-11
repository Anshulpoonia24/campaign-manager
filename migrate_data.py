"""
Migration Script: Copy local campaigns.db to production path
Run this ONCE after first Azure deployment via SSH:
    python migrate_data.py

Or include campaigns.db in your deployment and this runs automatically.
"""
import os
import shutil
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB = os.path.join(BASE_DIR, 'campaigns.db')
PROD_DATA_DIR = os.getenv('DATA_DIR', '/home/data')
PROD_DB = os.path.join(PROD_DATA_DIR, 'campaigns.db')

def migrate():
    # Create production data directory
    os.makedirs(PROD_DATA_DIR, exist_ok=True)

    # Check if production DB already has data
    if os.path.exists(PROD_DB):
        conn = sqlite3.connect(PROD_DB)
        count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        conn.close()
        if count > 0:
            print(f"[SKIP] Production DB already has {count} contacts. No migration needed.")
            return

    # Check if local DB exists
    if not os.path.exists(LOCAL_DB):
        print("[ERROR] No local campaigns.db found to migrate.")
        return

    # Copy local DB to production path
    shutil.copy2(LOCAL_DB, PROD_DB)
    print(f"[OK] Migrated {LOCAL_DB} -> {PROD_DB}")

    # Verify
    conn = sqlite3.connect(PROD_DB)
    contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    campaigns = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
    conn.close()
    print(f"[OK] Verified: {contacts} contacts, {campaigns} campaigns, {sent} emails sent")


if __name__ == '__main__':
    migrate()
