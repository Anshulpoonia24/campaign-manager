import sqlite3

prompt = """Write a cold outreach email to {name}, {designation} at {company}.

STRICT RULES:
1. MUST start with: <p>Hi {name},</p>

2. FIRST LINE AFTER GREETING: Start with ONE punchy, specific fact about {company} — max 10 words. No fluff. Example: "Flipkart just crossed 500M users — that's wild scale."

3. SECOND LINE: 1 crisp line connecting their growth stage to engineering hiring pain. Make it feel like you understand THEIR specific problem — not generic "tech talent is important."

4. THIRD: Include this EXACT HTML block unchanged:
   <b>Shiksha Infotech (Est. 2009) | 400+ engineers | Founded by alumni of top Indian engineering schools | Offices in US and India | We place pre-vetted AI/ML engineers at $30-55/hr (vs $100-150/hr US rates), onboarded in 2-3 weeks.</b>

5. FOURTH LINE: One simple CTA — "Open to a 15-min call this week?"

6. FORMAT: HTML only. Use <p> tags. Max 4 paragraphs. Each paragraph = 1-2 sentences MAX.

7. TONE: Casual, direct, founder-to-founder. Like a smart friend texting you — not a sales email.

8. NEVER USE: impressive, innovative, trajectory, remarkable, truly, genuinely, incredible, "I've been following", "I hope this finds you", "knowing how critical", "I wanted to reach out"

9. DO NOT write a subject line.

10. END with EXACTLY this block — copy as-is, zero changes:
<p>Best regards,</p>
<p>Anshul<br><b>Shiksha Infotech</b> | Est. 2009<br><a href="https://shikshainfotech.com">shikshainfotech.com</a></p>"""

conn = sqlite3.connect('campaigns.db')
conn.execute("UPDATE settings SET value=? WHERE key='email_prompt'", (prompt,))
conn.commit()

row = conn.execute("SELECT value FROM settings WHERE key='email_prompt'").fetchone()
print("Updated! Starts with:")
print(row[0][:100])
conn.close()
