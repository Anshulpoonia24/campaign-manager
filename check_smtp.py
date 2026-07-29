import psycopg2, psycopg2.extras

DSN = 'postgresql://postgres.ygbwqhxxmfdvrenbpcnw:Anshul%4012334@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres'

conn = psycopg2.connect(DSN, sslmode='require')
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=== SMTP ACCOUNTS ===")
cur.execute('SELECT id, email, active, health_score, warmup_stage, daily_limit, sent_today, smtp_server, smtp_port, login_username FROM smtp_accounts ORDER BY id')
for r in cur.fetchall():
    print(dict(r))

print("\n=== EMAILS_SENT STATUS ===")
cur.execute("SELECT status, COUNT(*) as cnt FROM emails_sent GROUP BY status")
for r in cur.fetchall():
    print(dict(r))

print("\n=== RECENT CAMPAIGN LOGS ===")
cur.execute("SELECT campaign_id, level, message, created_at FROM campaign_logs ORDER BY created_at DESC LIMIT 20")
for r in cur.fetchall():
    print(dict(r))

conn.close()
