import os
os.environ['PYTHONIOENCODING'] = 'utf-8'
from app import app
with app.test_client() as c:
    c.post('/login', data={'username':'admin','password':'admin123'})
    r = c.get('/campaign/new')
    html = r.data.decode('utf-8')
    # Find wizard section
    idx = html.find('wizard-progress')
    print(html[idx-10:idx+600])
