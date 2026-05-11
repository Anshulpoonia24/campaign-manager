import sqlite3

c = sqlite3.connect('campaigns.db')

# Create table
c.execute('''CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    purpose TEXT DEFAULT 'email',
    success INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
c.commit()
print("Table created")

# Count existing data
sent = c.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
ctx = c.execute("SELECT COUNT(*) FROM contacts WHERE context IS NOT NULL AND context != ''").fetchone()[0]
print(f"Sent: {sent}, Context: {ctx}")

# Add entries
for i in range(sent):
    c.execute("INSERT INTO ai_usage (provider,purpose,success,created_at) VALUES ('gemini','email',1,'2026-04-30 14:00:00')")

for i in range(ctx):
    c.execute("INSERT INTO ai_usage (provider,purpose,success,created_at) VALUES ('gemini','research',1,'2026-05-01 13:00:00')")

c.commit()

total = c.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0]
print(f"Total ai_usage: {total}")

rows = c.execute("SELECT provider, purpose, COUNT(*) FROM ai_usage GROUP BY provider, purpose").fetchall()
for r in rows:
    print(f"  {r[0]} | {r[1]} | {r[2]}")

c.close()
