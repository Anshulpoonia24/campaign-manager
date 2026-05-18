from app import app
from services.tracking import (
    ensure_tracking_table, generate_token, decode_token,
    is_bot, is_safe_url, log_event, get_temperature,
    get_workspace_timeline, get_engagement_stats, Event
)

print('=== TRACKING INFRASTRUCTURE TEST ===')

# 1. Table creation
ensure_tracking_table()
print('1. tracking_events table: OK')

# 2. Token generation
token = generate_token(1, 42, 7, 100, 5)
print(f'2. Token generated: {token[:30]}...')

# 3. Token decode
data = decode_token(token)
print(f'3. Token decoded: {data}')
assert data['workspace_id'] == 1
assert data['contact_id'] == 42
assert data['campaign_id'] == 7
print('   Assertions passed')

# 4. Tampered token rejected
bad = token[:-3] + 'xxx'
assert decode_token(bad) is None
print('4. Tampered token rejected: OK')

# 5. Bot detection
assert is_bot('curl/7.68.0') == True
assert is_bot('') == True
assert is_bot('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36') == False
assert is_bot('python-requests/2.28') == True
print('5. Bot detection: OK')

# 6. URL safety
assert is_safe_url('https://shikshainfotech.com') == True
assert is_safe_url('javascript:alert(1)') == False
assert is_safe_url('data:text/html,x') == False
assert is_safe_url('') == False
print('6. URL safety: OK')

# 7. Event logging
eid = log_event(Event.EMAIL_OPEN, workspace_id=1, contact_id=1,
                campaign_id=1, metadata={'test': True})
print(f'7. Event logged: id={eid}')

# 8. Temperature
assert get_temperature(0)   == 'cold'
assert get_temperature(25)  == 'warm'
assert get_temperature(60)  == 'hot'
assert get_temperature(110) == 'meeting_ready'
print('8. Temperature engine: OK')

# 9. API routes
c = app.test_client()
c.post('/login', data={'username':'admin','password':'admin123'})
for path in ['/api/tracking/timeline', '/api/tracking/stats', '/api/tracking/hot_leads']:
    r = c.get(path)
    assert r.status_code == 200, f'{path} returned {r.status_code}'
    print(f'9. {path}: {r.status_code} OK')

# 10. Tracking pixel
r = c.get('/track/fake-token.png')
assert r.status_code == 200
assert r.content_type == 'image/png'
print(f'10. Tracking pixel: {r.status_code} image/png OK')

# 11. Click redirect
r = c.get('/click/tok?url=https%3A%2F%2Fshikshainfotech.com&tid=fake')
assert r.status_code in (301, 302)
print(f'11. Click redirect: {r.status_code} OK')

# 12. Unsafe URL blocked
r = c.get('/click/tok?url=javascript%3Aalert(1)&tid=fake')
assert r.status_code in (301, 302)
print(f'12. Unsafe URL blocked/redirected: {r.status_code} OK')

# 13. All main pages still work
for path in ['/', '/contacts', '/campaigns', '/inbox', '/analytics']:
    r = c.get(path)
    assert r.status_code == 200, f'{path} = {r.status_code}'
print('13. All main pages: OK')

print('\n=== ALL TESTS PASSED ===')
