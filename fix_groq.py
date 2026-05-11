import sqlite3

conn = sqlite3.connect('campaigns.db')

groq_key = "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"
priority = "groq,ollama,gemini"

conn.execute("UPDATE settings SET value=? WHERE key=?", (groq_key, 'groq_api_keys'))
conn.execute("UPDATE settings SET value=? WHERE key=?", (priority, 'ai_priority'))
conn.commit()

# Verify
r1 = conn.execute("SELECT value FROM settings WHERE key='groq_api_keys'").fetchone()
r2 = conn.execute("SELECT value FROM settings WHERE key='ai_priority'").fetchone()
print(f"Groq key saved: {r1[0][:15]}...")
print(f"Priority saved: {r2[0]}")
conn.close()
