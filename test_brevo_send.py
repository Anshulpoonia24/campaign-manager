import psycopg2, psycopg2.extras, requests

DSN = 'postgresql://postgres.ygbwqhxxmfdvrenbpcnw:Anshul%4012334@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres'
conn = psycopg2.connect(DSN, sslmode='require')
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute('SELECT * FROM smtp_accounts WHERE id=1')
acc = dict(cur.fetchone())
conn.close()

print(f"Sender email: {acc['email']}")
print(f"API key starts: {acc['password'][:15]}...")

# Test Brevo API send
payload = {
    'sender': {'name': acc.get('from_name') or 'Ananya', 'email': acc['email']},
    'to': [{'email': 'pooniaanshul24@gmail.com'}],
    'subject': 'Test from OutreachOS',
    'htmlContent': '<p>This is a test email from OutreachOS to verify Brevo API is working.</p>',
}
r = requests.post(
    'https://api.brevo.com/v3/smtp/email',
    headers={'api-key': acc['password'], 'Content-Type': 'application/json'},
    json=payload,
    timeout=20,
)
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")
