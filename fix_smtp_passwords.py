"""
fix_smtp_passwords.py — Fix SMTP account passwords in Supabase DB
Run: python fix_smtp_passwords.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL', '')
if not DATABASE_URL:
    print('ERROR: DATABASE_URL not set in .env')
    exit(1)

import psycopg2
import psycopg2.extras

dsn = DATABASE_URL.strip()
if dsn.startswith('postgres://'):
    dsn = 'postgresql://' + dsn[len('postgres://'):]

conn = psycopg2.connect(dsn, sslmode='require', connect_timeout=10)
conn.autocommit = True
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Show current SMTP accounts
cur.execute("SELECT id, email, smtp_server, LEFT(password,30) as pw_preview, LENGTH(password) as pw_len FROM smtp_accounts ORDER BY id")
accounts = cur.fetchall()
print("\n=== Current SMTP Accounts ===")
for a in accounts:
    print(f"  ID={a['id']} | {a['email']} | {a['smtp_server']} | pw_len={a['pw_len']} | pw_preview={a['pw_preview']}")

print("\nEnter new passwords for each account (leave blank to skip):")
for a in accounts:
    new_pw = input(f"\n  New password for {a['email']} (ID={a['id']}): ").strip()
    if new_pw:
        cur.execute("UPDATE smtp_accounts SET password=%s WHERE id=%s", (new_pw, a['id']))
        print(f"  ✓ Updated password for {a['email']}")
    else:
        print(f"  — Skipped {a['email']}")

# Also update smtp_server if needed
print("\n=== Update SMTP Server (optional) ===")
for a in accounts:
    new_server = input(f"  New smtp_server for {a['email']} (current: {a['smtp_server']}, blank=skip): ").strip()
    if new_server:
        cur.execute("UPDATE smtp_accounts SET smtp_server=%s WHERE id=%s", (new_server, a['id']))
        print(f"  ✓ Updated smtp_server for {a['email']}")

conn.close()
print("\n✓ Done!")
