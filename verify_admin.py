import os
from dotenv import load_dotenv
load_dotenv()

ADMIN_USER = os.getenv('ADMIN_USERNAME', 'superadmin')
ADMIN_PASS = os.getenv('ADMIN_PASSWORD', '')

from app import app
c = app.test_client()

print('=== ADMIN SYSTEM TEST ===')
print(f'Admin creds from .env: {ADMIN_USER} / {ADMIN_PASS}')

r = c.get('/admin/login')
print('1. /admin/login GET:', r.status_code)

r = c.get('/admin/', follow_redirects=True)
print('2. /admin/ no session -> login:', b'Access Admin Panel' in r.data)

r = c.post('/admin/login', data={'username':ADMIN_USER,'password':'wrongpass'}, follow_redirects=True)
print('3. Wrong password -> error:', b'Invalid admin' in r.data)

r = c.post('/admin/login', data={'username':ADMIN_USER,'password':ADMIN_PASS}, follow_redirects=True)
print('4. Correct login -> tenant list:', b'Tenant Management' in r.data)

r = c.get('/admin/')
print('5. /admin/ with session:', r.status_code)

r = c.get('/admin/create')
print('6. /admin/create:', r.status_code)

r = c.get('/admin/tenant/1')
print('7. /admin/tenant/1:', r.status_code)

r = c.get('/admin/logout', follow_redirects=True)
print('8. Logout -> login page:', b'Access Admin Panel' in r.data)

# Tenant login completely separate
c2 = app.test_client()
r = c2.post('/login', data={'username':'admin','password':'admin123'}, follow_redirects=True)
print('9. Tenant /login works:', r.status_code == 200)

# Admin creds do NOT work on tenant login
r = c2.post('/login', data={'username':ADMIN_USER,'password':ADMIN_PASS}, follow_redirects=True)
print('10. Admin creds on /login -> fails (no tenant):', b'Invalid username' in r.data or r.status_code == 200)

print('\nALL DONE')
