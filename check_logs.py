import psycopg2, psycopg2.extras

DSN = 'postgresql://postgres.ygbwqhxxmfdvrenbpcnw:Anshul%4012334@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres'
conn = psycopg2.connect(DSN, sslmode='require')
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=== ALL CAMPAIGN LOGS ===")
cur.execute("SELECT campaign_id, level, message, created_at FROM campaign_logs ORDER BY created_at DESC LIMIT 30")
for r in cur.fetchall():
    print(f"[{r['level'].upper()}] {r['message']}")

print("\n=== EMAILS_SENT (all) ===")
cur.execute("SELECT id, campaign_id, email, status, bounce_reason, sent_at FROM emails_sent ORDER BY sent_at DESC LIMIT 20")
for r in cur.fetchall():
    print(dict(r))

print("\n=== CONTACTS (first 5) ===")
cur.execute("SELECT id, name, email, company, email_valid FROM contacts LIMIT 5")
for r in cur.fetchall():
    print(dict(r))

conn.close()
