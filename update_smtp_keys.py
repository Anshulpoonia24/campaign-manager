import sys
import psycopg2
import psycopg2.extras
from urllib.parse import quote_plus

pw = quote_plus('Anshul@12334')
DATABASE_URL = f'postgresql://postgres.ygbwqhxxmfdvrenbpcnw:{pw}@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres'

# ── SET YOUR BREVO API KEYS HERE ──────────────────────────────
# Get from: Brevo Dashboard → Settings → SMTP & API → SMTP tab
UPDATES = {
    1: {  # ananya@shikshahq.com
        'password': 'PASTE_BREVO_SMTP_KEY_HERE',   # xkeysib-...
        'email': 'ananya@shikshahq.com',
        'smtp_server': 'smtp-relay.brevo.com',
        'smtp_port': 587,
        'login_username': 'PASTE_BREVO_LOGIN_EMAIL_HERE',  # your Brevo account email
    },
    2: {  # ananya@shikshapartnerscom
        'password': 'PASTE_BREVO_SMTP_KEY_HERE',
        'email': 'ananya@shikshainfotech.com',      # fix typo in email if needed
        'smtp_server': 'smtp-relay.brevo.com',
        'smtp_port': 587,
        'login_username': 'PASTE_BREVO_LOGIN_EMAIL_HERE',
    },
}
# ─────────────────────────────────────────────────────────────

conn = psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=15)
conn.autocommit = True
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

for account_id, data in UPDATES.items():
    if 'PASTE_' in data['password']:
        sys.stdout.write(f'SKIP ID={account_id} — fill in the password first\n')
        continue
    cur.execute("""
        UPDATE smtp_accounts
        SET password=%s, email=%s, smtp_server=%s, smtp_port=%s, login_username=%s
        WHERE id=%s
    """, (data['password'], data['email'], data['smtp_server'],
          data['smtp_port'], data['login_username'], account_id))
    sys.stdout.write(f'Updated ID={account_id} ({data["email"]})\n')

conn.close()
sys.stdout.write('Done!\n')
