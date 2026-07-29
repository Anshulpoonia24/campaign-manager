import sys
import psycopg2
import psycopg2.extras
from urllib.parse import quote_plus

pw = quote_plus('Anshul@12334')
DATABASE_URL = f'postgresql://postgres.ygbwqhxxmfdvrenbpcnw:{pw}@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres'

sys.stdout.write('Connecting to Supabase...\n')
sys.stdout.flush()

try:
    conn = psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=15)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    sys.stdout.write('Connected!\n')
    sys.stdout.flush()

    cur.execute('SELECT id, email, smtp_server, smtp_port, LEFT(password,60) as pw_preview, LENGTH(password) as pw_len FROM smtp_accounts ORDER BY id')
    rows = cur.fetchall()
    sys.stdout.write(f'Found {len(rows)} SMTP accounts:\n')
    for a in rows:
        sys.stdout.write(f"  ID={a['id']} | {a['email']} | {a['smtp_server']}:{a['smtp_port']} | pw_len={a['pw_len']} | pw_start={a['pw_preview']}\n")
    sys.stdout.flush()
    conn.close()
except Exception as e:
    sys.stdout.write(f'ERROR: {e}\n')
    sys.stdout.flush()
