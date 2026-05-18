import os, sys, json
os.environ['PYTHONIOENCODING'] = 'utf-8'

out = open('test_results.txt', 'w', encoding='utf-8')

def log(msg):
    print(msg)
    out.write(msg + '\n')
    out.flush()

from app import app, get_db

c = app.test_client()
c.post('/login', data={'username': 'admin', 'password': 'admin123'})

passed = 0
failed_list = []

def chk(name, r, exp=200):
    global passed
    ok = r.status_code == exp
    status = 'OK  ' if ok else 'FAIL'
    log(f'{status} {r.status_code} {name}')
    if ok:
        passed += 1
    else:
        failed_list.append(f'{name} -> {r.status_code} (expected {exp})')
    return ok

def chk_json(name, r, key=None):
    global passed
    try:
        data = r.get_json()
        ok = r.status_code == 200 and data is not None
        if key:
            ok = ok and key in data
        status = 'OK  ' if ok else 'FAIL'
        log(f'{status} {r.status_code} {name}' + (f' [{key}={data.get(key) if data else "?"}]' if key else ''))
        if ok:
            passed += 1
        else:
            failed_list.append(f'{name} -> JSON missing {key}')
    except Exception as e:
        log(f'FAIL {r.status_code} {name} [exception: {e}]')
        failed_list.append(f'{name} -> exception')

total_tests = 0

# ── SETUP: create test data ──
log('\n=== SETUP ===')
conn = get_db()
# Clean test contact
conn.execute("DELETE FROM contacts WHERE email='t99@testcorp.com'")
conn.commit()
conn.close()

# ── 1. AUTH ──
log('\n=== AUTH ===')
total_tests += 2
chk('Login page GET (redirects when logged in)', c.get('/login'), 302)
chk('Logout redirect', c.get('/logout'), 302)
c.post('/login', data={'username': 'admin', 'password': 'admin123'})

# ── 2. CONTACTS ──
log('\n=== CONTACTS ===')
total_tests += 12
chk('Add contact', c.post('/add_contact', data={'name': 'Test User', 'company': 'Test Corp', 'email': 't99@testcorp.com', 'website': 'https://testcorp.com', 'designation': 'CEO'}, follow_redirects=True))
chk('Add duplicate contact (should flash error)', c.post('/add_contact', data={'name': 'Test User', 'company': 'Test Corp', 'email': 't99@testcorp.com', 'website': '', 'designation': ''}, follow_redirects=True))
chk('Contacts list', c.get('/contacts'))
chk('Contacts filter all', c.get('/contacts?filter=all'))
chk('Contacts filter valid', c.get('/contacts?filter=valid'))
chk('Contacts filter invalid', c.get('/contacts?filter=invalid'))
chk('Contacts filter new', c.get('/contacts?filter=new'))
chk('Contacts filter sent', c.get('/contacts?filter=sent'))

conn = get_db()
contact = conn.execute("SELECT id FROM contacts WHERE email='t99@testcorp.com'").fetchone()
cid = contact['id'] if contact else None
conn.close()

if cid:
    chk('API get contact', c.get(f'/api/contact/{cid}'))
    chk('Edit contact', c.post(f'/contact/edit/{cid}', data={'name': 'Test User', 'company': 'Test Corp', 'email': 't99@testcorp.com', 'designation': 'CEO', 'context': 'SaaS company context', 'website': 'https://testcorp.com'}, follow_redirects=True))
    chk('Verify single', c.get(f'/api/verify_single/{cid}'))
    chk('Verify status API', c.get('/api/verify_status'))
else:
    log('SKIP contact-specific tests (no contact found)')

# ── 3. CAMPAIGNS ──
log('\n=== CAMPAIGNS ===')
total_tests += 6
chk('Campaign new page GET', c.get('/campaign/new'))
chk('Create campaign POST', c.post('/campaign/new', data={'campaign_name': 'Test Campaign', 'description': 'Test desc'}, follow_redirects=True))
chk('Campaigns list', c.get('/campaigns'))

conn = get_db()
camp = conn.execute("SELECT id FROM campaigns WHERE name='Test Campaign' LIMIT 1").fetchone()
camp_id = camp['id'] if camp else None
conn.close()

if camp_id:
    chk('Campaign detail', c.get(f'/campaign/{camp_id}'))
    chk('Edit campaign', c.post(f'/campaign/edit/{camp_id}', data={'name': 'Test Campaign Updated', 'description': 'Updated'}, follow_redirects=True))
    chk('Send campaign no contacts (should redirect)', c.post(f'/campaign/{camp_id}/send', data={'subject': 'Test', 'body': 'Test body'}, follow_redirects=True))
else:
    log('SKIP campaign-specific tests')

# ── 4. INBOX ──
log('\n=== INBOX ===')
total_tests += 8
chk('Inbox page', c.get('/inbox'))
chk('Inbox filter interested', c.get('/inbox?status=interested'))
chk('Inbox filter meeting', c.get('/inbox?status=meeting'))
chk('Inbox filter booked', c.get('/inbox?status=booked'))
chk('Inbox filter closed', c.get('/inbox?status=closed'))
chk('Inbox filter ignored', c.get('/inbox?status=ignored'))
chk_json('Inbox stats API', c.get('/api/inbox/stats'), 'total')
chk_json('Check replies API', c.post('/api/check_replies'), 'success')
total_tests += 2

# Thread APIs with non-existent IDs
chk('Thread data 404', c.get('/api/inbox/thread_data/99999'), 404)
chk('Contact by thread 404', c.get('/api/contact_by_thread/99999'), 404)
total_tests += 2

# ── 5. AUTOMATIONS ──
log('\n=== AUTOMATIONS ===')
total_tests += 5
chk('Automations page', c.get('/automations'))
chk_json('Automation stats', c.get('/api/automations/stats'), 'active_rules')
chk_json('Save automation rule', c.post('/api/automations/save', json={'rule_key': 'no_reply_followup', 'enabled': True, 'delay_days': 2, 'max_followups': 3}), 'success')
chk_json('Run automations', c.post('/api/automations/run'), 'success')
chk_json('Followup draft', c.post('/api/automations/followup_draft', json={'contact_name': 'John', 'company': 'Acme', 'context': 'SaaS company', 'previous_subject': 'Test'}), 'success')

# ── 6. SMTP ──
log('\n=== SMTP ===')
total_tests += 6
chk_json('SMTP accounts list', c.get('/api/smtp_accounts'), 'accounts')
chk_json('Add SMTP account', c.post('/api/smtp_accounts/add', json={'email': 'smtp_test99@test.com', 'password': 'pass123', 'smtp_server': 'smtp.test.com', 'smtp_port': 587, 'from_name': 'Test', 'daily_limit': 50}), 'success')
chk_json('SMTP test', c.get('/api/smtp_test'), 'smtp_server')
chk_json('Reset SMTP today', c.post('/api/smtp_accounts/reset_today'), 'success')

conn = get_db()
smtp = conn.execute("SELECT id FROM smtp_accounts WHERE email='smtp_test99@test.com'").fetchone()
smtp_id = smtp['id'] if smtp else None
conn.close()

if smtp_id:
    chk_json('Toggle SMTP account', c.post(f'/api/smtp_accounts/{smtp_id}/toggle'), 'success')
    chk_json('Delete SMTP account', c.delete(f'/api/smtp_accounts/{smtp_id}/delete'), 'success')
else:
    log('SKIP SMTP toggle/delete')

# ── 7. ANALYTICS & DELIVERABILITY ──
log('\n=== ANALYTICS & DELIVERABILITY ===')
total_tests += 6
chk('Analytics page', c.get('/analytics'))
chk('Deliverability page', c.get('/deliverability'))
chk_json('AI usage API', c.get('/api/ai_usage'), 'by_provider')
chk_json('Hot leads API', c.get('/api/hot_leads'), 'leads')
chk_json('Click analytics API', c.get('/api/click_analytics'), 'total_clicks')
chk_json('Groq usage API', c.get('/api/groq_usage'), 'keys')

# ── 8. SETTINGS ──
log('\n=== SETTINGS ===')
total_tests += 3
chk('Settings GET', c.get('/settings'))
chk('Settings POST save', c.post('/settings', data={
    'smtp_server': 'smtp.hostinger.com', 'smtp_port': '587',
    'smtp_username': 'test@test.com', 'smtp_password': 'pass',
    'from_email': 'test@test.com', 'from_name': 'Test',
    'reply_to': '', 'bcc_emails': '',
    'tracking_host': 'https://ertyui.online',
    'imap_server': '', 'imap_port': '993',
    'imap_username': '', 'imap_password': '',
    'imap_check_interval': '180',
    'ai_priority': 'groq,gemini',
    'groq_api_keys': '', 'gemini_api_key': '',
    'email_prompt': 'Write email to {name} at {company}.'
}, follow_redirects=True))
chk_json('IMAP status API', c.get('/api/imap_status'), 'configured')

# ── 9. DASHBOARD ──
log('\n=== DASHBOARD ===')
total_tests += 1
chk('Dashboard', c.get('/'))

# ── 10. UPLOAD ──
log('\n=== UPLOAD ===')
total_tests += 1
chk('Upload page', c.get('/upload'))

# ── 11. EXPORT ──
log('\n=== EXPORT ===')
total_tests += 4
chk('Export all contacts', c.get('/export/all'))
chk('Export sent emails', c.get('/export/sent'))
chk('Export invalid', c.get('/export/invalid'))
chk('Export bounced', c.get('/export/bounced'))

# ── 12. LOGS ──
log('\n=== LOGS ===')
total_tests += 3
chk('Logs page', c.get('/logs'))
chk('Follow ups page', c.get('/follow_ups'))
chk('Bounced page', c.get('/bounced'))

# ── 13. LEAD SCORING ──
log('\n=== LEAD SCORING ===')
total_tests += 5
from services.lead_scoring import update_lead_score, calculate_priority, get_hot_leads, get_click_analytics
if cid:
    update_lead_score(cid, 'open')
    update_lead_score(cid, 'click')
    update_lead_score(cid, 'reply')
    update_lead_score(cid, 'interested')
conn = get_db()
row = conn.execute('SELECT lead_score FROM contacts WHERE id=?', (cid,)).fetchone() if cid else None
conn.close()
score = row['lead_score'] if row else 0
ok = score > 0
log(f'{"OK  " if ok else "FAIL"} Lead score updated: {score}')
if ok: passed += 1
else: failed_list.append('Lead score not updated')

ok = calculate_priority(80) == 'hot'
log(f'{"OK  " if ok else "FAIL"} Priority hot (score=80): {calculate_priority(80)}')
if ok: passed += 1
else: failed_list.append('Priority hot wrong')

ok = calculate_priority(30) == 'warm'
log(f'{"OK  " if ok else "FAIL"} Priority warm (score=30): {calculate_priority(30)}')
if ok: passed += 1
else: failed_list.append('Priority warm wrong')

ok = calculate_priority(5) == 'cold'
log(f'{"OK  " if ok else "FAIL"} Priority cold (score=5): {calculate_priority(5)}')
if ok: passed += 1
else: failed_list.append('Priority cold wrong')

leads = get_hot_leads(limit=5)
ok = isinstance(leads, list)
log(f'{"OK  " if ok else "FAIL"} get_hot_leads returns list: {len(leads)} leads')
if ok: passed += 1
else: failed_list.append('get_hot_leads failed')

# ── 14. UNSUBSCRIBES ──
log('\n=== UNSUBSCRIBES ===')
total_tests += 1
chk_json('Unsubscribes API', c.get('/api/unsubscribes'), 'unsubscribes')

# ── 15. TRACKING ──
log('\n=== TRACKING ===')
total_tests += 2
chk('Fix tracking host', c.get('/api/fix_tracking_host'))
chk('Track open pixel', c.get('/track/nonexistent-id.png'))

# ── CLEANUP ──
log('\n=== CLEANUP ===')
if cid:
    r = c.delete(f'/api/contact/delete/{cid}')
    log(f'Cleanup test contact: {r.status_code}')
if camp_id:
    r = c.delete(f'/api/campaign/delete/{camp_id}')
    log(f'Cleanup test campaign: {r.status_code}')

# ── SUMMARY ──
log(f'\n{"="*50}')
log(f'TOTAL: {passed}/{total_tests} passed')
if failed_list:
    log(f'\nFAILED ({len(failed_list)}):')
    for f in failed_list:
        log(f'  - {f}')
else:
    log('ALL TESTS PASSED')

out.close()
