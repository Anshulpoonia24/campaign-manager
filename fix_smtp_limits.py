import psycopg2

DSN = 'postgresql://postgres.ygbwqhxxmfdvrenbpcnw:Anshul%4012334@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres'
conn = psycopg2.connect(DSN, sslmode='require')
cur = conn.cursor()

# Fix 1: daily_limit for warmup_stage=5 should be 100, not 10
cur.execute("UPDATE smtp_accounts SET daily_limit=100 WHERE warmup_stage=5")
print(f"Fixed daily_limit: {cur.rowcount} rows")

# Fix 2: reset sent_today so sends aren't blocked
cur.execute("UPDATE smtp_accounts SET sent_today=0")
print(f"Reset sent_today: {cur.rowcount} rows")

# Fix 3: fix typo in email 2
cur.execute("UPDATE smtp_accounts SET email='ananya@shikshapartners.com' WHERE id=2 AND email='ananya@shikshapartnerscom'")
print(f"Fixed email typo: {cur.rowcount} rows")

conn.commit()
conn.close()
print("Done.")
