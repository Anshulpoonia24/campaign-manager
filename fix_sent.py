import sqlite3

c = sqlite3.connect('campaigns.db')

sent_emails = [
    ('Alexandr Wang', 'Scale AI', 'alex@scaleapi.com'),
    ('Daniel Yanisse', 'Checkr', 'daniel@checkr.com'),
    ('Gary Gao', 'Chert', 'gary@trychert.com'),
    ('Noah Song', 'Archal Labs', 'noah@archal.ai'),
    ('Aidan Tiruvan', 'Archal', 'aidan@archal.ai'),
    ('Sanjeev Mangesh', 'ReasonBlocks', 'sanjeevmangesh123@gmail.com'),
    ('Paul Haverland', 'Podium', 'jasherglobal@gmail.com'),
    ('Seb Poole', 'Modern', 'sebastianwpoole@gmail.com'),
]

added = 0
for name, company, email in sent_emails:
    ex = c.execute('SELECT id FROM contacts WHERE email=?', (email,)).fetchone()
    if not ex:
        c.execute('INSERT INTO contacts (name,company,email,status,email_valid) VALUES (?,?,?,?,?)',
                  (name, company, email, 'sent', 1))
        cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        print(f'  New contact: {name} ({email}) id={cid}')
    else:
        cid = ex[0]
        c.execute('UPDATE contacts SET status=? WHERE id=?', ('sent', cid))
        print(f'  Existing contact id={cid}: {email}')

    al = c.execute('SELECT id FROM emails_sent WHERE email=? AND campaign_id=1', (email,)).fetchone()
    if not al:
        c.execute('INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,sent_at) VALUES (?,?,?,?,?,?,?)',
                  (1, cid, email, f'Helping {company} scale engineering faster', 'Sent via script', 'sent', '2026-04-30 14:27:00'))
        added += 1
        print(f'  Logged sent: {email}')
    else:
        print(f'  Already logged: {email}')

c.commit()
total = c.execute('SELECT COUNT(*) FROM emails_sent WHERE campaign_id=1').fetchone()[0]
print(f'\nAdded {added}. Campaign 1 total sent: {total}')
c.close()
