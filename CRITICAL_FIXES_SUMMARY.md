# PRODUCTION READINESS - QUICK FIX GUIDE

## Critical Issues - Fix Now (Before Any Deployment)

### 1. Multi-Tenant Data Leakage

**Problem:** Admin queries and settings don't filter by workspace_id, exposing all tenant data.

**Quick Fix:**
```python
# ALL workspace-level queries MUST have workspace_id filter
# routes/admin.py - WRONG:
stats = {
    'total_contacts': conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
}
# CORRECT:
stats = {
    'total_contacts': conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE workspace_id=?", (wid,)
    ).fetchone()[0]
}

# Audit: Run this grep to find all unfiltered queries:
# grep -r "SELECT.*FROM" --include="*.py" | grep -v "workspace_id"
```

---

### 2. Campaign Duplicate Sends

**Problem:** In-memory locks don't prevent concurrent duplicate sends.

**Quick Fix:**
```python
# services/campaign_executor.py
def send_email_safe(campaign_id, contact_id, ...):
    conn = get_db()
    
    # Use database-level lock
    conn.execute("BEGIN TRANSACTION")
    try:
        # Check AND lock in single operation
        already = conn.execute("""
            SELECT id FROM emails_sent 
            WHERE contact_id=? AND campaign_id=? AND status='sent'
            FOR UPDATE SKIP LOCKED
        """, (contact_id, campaign_id)).fetchone()
        
        if already:
            conn.rollback()
            return False  # Already sent
        
        # SEND EMAIL
        send_via_smtp(...)
        
        # Record send ATOMICALLY
        conn.execute("""
            INSERT INTO emails_sent (campaign_id, contact_id, ...)
            VALUES (?, ?, ...)
        """, (...))
        
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise
```

---

### 3. Plaintext Passwords in Database

**Problem:** SMTP passwords stored plaintext; database dump = credential compromise.

**Quick Fix:**
```python
# utils/db.py - Add encryption layer
from cryptography.fernet import Fernet

# Initialize once at startup (from Secrets Manager)
CIPHER_KEY = os.getenv('ENCRYPTION_KEY')  # 32-byte base64 key
cipher = Fernet(CIPHER_KEY)

def encrypt_credential(plaintext):
    return cipher.encrypt(plaintext.encode()).decode()

def decrypt_credential(ciphertext):
    return cipher.decrypt(ciphertext.encode()).decode()

# Usage:
# On save:
encrypted_pw = encrypt_credential(smtp_password)
conn.execute("INSERT INTO smtp_accounts (password) VALUES (?)", (encrypted_pw,))

# On read:
row = conn.execute("SELECT password FROM smtp_accounts WHERE id=?", (id,)).fetchone()
plaintext_pw = decrypt_credential(row['password'])
```

**Generate key:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Output: AbCdEfGhIjKlMnOpQrStUvWxYz_1234567890ABCDE=
# Store in: AWS Secrets Manager / Vault, NOT in .env
```

---

### 4. Session Hijacking

**Problem:** OAuth callback doesn't verify CSRF state; attacker steals token, logs in.

**Quick Fix:**
```python
# routes/auth.py - Add CSRF protection
import secrets

@auth_bp.route('/auth/google')
def google_login():
    # Generate state token (CSRF protection)
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state
    session.permanent = True
    
    redirect_url = _get_redirect_url()
    oauth_url = (
        f'{SUPABASE_URL}/auth/v1/authorize'
        f'?provider=google'
        f'&redirect_to={redirect_url}'
        f'&state={state}'  # Add state
    )
    return redirect(oauth_url)

@auth_bp.route('/auth/google/callback')
def google_callback():
    # Verify state token
    received_state = request.args.get('state')
    session_state = session.pop('oauth_state', None)
    
    if not received_state or received_state != session_state:
        flash('OAuth validation failed - potential attack', 'error')
        return redirect(url_for('auth.login'))
    
    # Only now proceed with token handling
    access_token = request.args.get('access_token')
    return _login_with_supabase_token(access_token)
```

---

### 5. Connection Pool Exhaustion

**Problem:** Every request creates new DB connection; under load, connections exhaust.

**Quick Fix:**
```python
# utils/db.py - Add connection pooling
import psycopg2.pool
from psycopg2 import OperationalError

# Create pool at module load (once)
_pg_pool = None

def _get_pg_pool():
    global _pg_pool
    if _pg_pool:
        return _pg_pool
    
    kwargs = _parse_pg_url()
    _pg_pool = psycopg2.pool.SimpleConnectionPool(
        minconn=5,      # Minimum 5 connections
        maxconn=20,     # Maximum 20 connections
        **kwargs
    )
    return _pg_pool

def get_db():
    pool = _get_pg_pool()
    try:
        conn = pool.getconn(timeout=10)  # Wait max 10s for available connection
        # Wrap to auto-return to pool
        return ConnectionWrapper(conn, pool)
    except pool.PoolError:
        raise OperationalError("Database connection pool exhausted")

class ConnectionWrapper:
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._conn.set_session(autocommit=False)  # Use transactions
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()
    
    def close(self):
        try:
            self._conn.close()
        finally:
            self._pool.putconn(self._conn)
    
    # Delegate other methods to wrapped connection
    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)
```

---

### 6. Rate Limiting Auth Endpoints

**Problem:** Admin login can be brute-forced; no per-IP rate limit.

**Quick Fix:**
```python
# routes/auth.py + routes/admin.py
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    get_remote_address,
    default_limits=["200 per hour"],
    storage_uri="redis://localhost:6379",  # Must use Redis (not memory://)
)

# Admin login - 5 attempts per minute per IP
@admin_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def admin_login():
    # ... existing code ...

# Regular login - 10 attempts per minute per IP
@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    # ... existing code ...

# Register - prevent spam signup
@auth_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("3 per hour")
def register():
    # ... existing code ...
```

---

### 7. Foreign Key Constraints

**Problem:** SQLite foreign keys disabled by default; orphaned records possible.

**Quick Fix:**
```python
# utils/db.py - Enable FK constraints for SQLite
def get_db():
    if USE_POSTGRES:
        # PostgreSQL - FKs enforced by default
        return _connect_pg()
    else:
        # SQLite - must enable FK
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys=ON")  # CRITICAL
        conn.row_factory = sqlite3.Row
        return conn

# Verify it's enabled:
# SELECT pragma_foreign_keys;  -- Should return 1 (ON)
```

---

### 8. Workspace Validation on Every Query

**Problem:** Can't trust current_user.workspace_id; attacker may have modified it.

**Quick Fix:**
```python
# services/workspace_service.py - Add validation wrapper
from functools import wraps
from flask import abort

def validate_workspace_access(f):
    """Decorator to verify user has access to requested workspace."""
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask_login import current_user
        
        # Get workspace_id from request context
        workspace_id = kwargs.get('workspace_id') or request.args.get('workspace_id') or get_wid()
        
        # Verify user belongs to this workspace
        conn = get_db()
        row = conn.execute("""
            SELECT workspace_id FROM users WHERE id=? AND workspace_id=?
        """, (current_user.id, workspace_id)).fetchone()
        conn.close()
        
        if not row:
            abort(403)  # Forbidden - user not in this workspace
        
        return f(*args, **kwargs)
    return decorated

# Usage:
@campaigns_bp.route('/campaigns/<int:workspace_id>')
@login_required
@validate_workspace_access
def list_campaigns(workspace_id):
    # Now guaranteed: current_user belongs to workspace_id
    campaigns = ws_campaigns(workspace_id)
    ...
```

---

## Mid-Priority Fixes (Week 2-3)

### Database Indexes
```sql
-- Add these immediately
CREATE INDEX idx_emails_sent_tracking_id ON emails_sent(tracking_id);
CREATE INDEX idx_contacts_email_wid ON contacts(email, workspace_id);
CREATE INDEX idx_threads_contact_campaign ON threads(contact_id, campaign_id);
CREATE INDEX idx_campaign_logs_campaign_id ON campaign_logs(campaign_id, created_at DESC);
CREATE INDEX idx_messages_thread_created ON messages(thread_id, created_at DESC);
```

### SMTP Connection Cleanup
```python
# tasks/email_tasks.py
def send_single_email(self, ...):
    server = None
    try:
        server = smtplib.SMTP(smtp_creds['server'], smtp_creds['port'], timeout=30)
        server.starttls()
        server.login(...)
        server.send_message(msg)
        return {'success': True, ...}
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        if server:
            try:
                server.quit()
            except:
                pass  # Best effort cleanup
```

### Remove Credentials from Logs
```python
# All logging locations
import logging

# BAD:
logger.error(f'SMTP error: {exception}')  # May contain password

# GOOD:
logger.error(f'SMTP error (key masked): {str(exception)[:50]}')

# BAD:
logger.warning(f'Groq API: {r.headers}')  # Headers may have key

# GOOD:
logger.warning('Groq API rate limit')
```

---

## Testing Checklist

Before any production deployment:

```python
# tests/test_multitenancy.py
def test_workspace_isolation():
    """Verify Tenant A cannot see Tenant B's data."""
    # Login as Tenant A, verify B's campaigns not visible
    # Login as Tenant B, verify A's contacts not accessible
    # Admin can see all, but non-admin cannot cross workspaces

def test_duplicate_sends():
    """Verify concurrent sends don't duplicate."""
    # Simulate 10 workers sending same campaign
    # Verify contact gets email exactly once

def test_campaign_locking():
    """Verify race conditions handled."""
    # Start campaign, immediately update status
    # Verify final state is consistent

# tests/test_security.py
def test_csrf_protection():
    """POST without CSRF token should fail."""
    response = client.post('/campaign/new', data={...})
    assert response.status_code == 403

def test_session_hijacking():
    """Can't use stolen OAuth token."""
    # Get valid token, steal it
    # Verify state validation prevents reuse
    
def test_rate_limiting():
    """Brute force attempts are blocked."""
    for i in range(20):
        response = client.post('/login', data={'username': 'admin', 'password': 'wrong'})
    assert response.status_code == 429  # Too Many Requests
```

---

## Monitoring & Alerting Setup

```python
# Create alerts for production
# 1. Campaign duplicate sends detected
# 2. Database connection pool exhaustion
# 3. Celery task failures
# 4. IMAP sync stalled (no heartbeat)
# 5. SMTP bounce rate > 5%
# 6. SQL query > 5 seconds
# 7. Error rate > 1%

# Example using Sentry:
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.celery import CeleryIntegration

sentry_sdk.init(
    dsn="https://xxx@sentry.io/yyy",
    integrations=[FlaskIntegration(), CeleryIntegration()],
    traces_sample_rate=0.1,
    environment="production",
)

# Automatically catches exceptions and sends alerts
```

---

## Deployment Runbook

```bash
# 1. Create database backup
pg_dump production_db > backup_$(date +%s).sql

# 2. Run migrations (in transaction)
BEGIN;
  ALTER TABLE smtp_accounts ADD COLUMN encrypted_password TEXT;
  -- ... all migrations
COMMIT;

# 3. Deploy app code
git pull
pip install -r requirements.txt
docker build -t app:v2 .

# 4. Run smoke tests
pytest tests/ -m production

# 5. Canary deploy (10% traffic)
kubectl set image deployment/app app=app:v2 --record

# 6. Monitor metrics for 30 minutes
# - Error rate < 0.1%
# - Response time p99 < 1s
# - No connection pool exhaustion

# 7. Full rollout
kubectl set image deployment/app app=app:v2 --all

# 8. Verify:
curl https://production.app/health
# Should return: {"status": "ok", "db": "connected"}
```

---

## Summary

**CRITICAL FIXES (DO IMMEDIATELY):**
1. Add workspace_id filter to all queries ✓
2. Database-level locking for campaigns ✓
3. Encrypt SMTP passwords ✓
4. CSRF state validation for OAuth ✓
5. Connection pooling ✓

**HIGH PRIORITY (This Week):**
6. Rate limiting on auth endpoints ✓
7. Enable foreign key constraints ✓
8. Add database indexes ✓
9. SMTP connection cleanup ✓
10. Remove credentials from logs ✓

**BEFORE GOING LIVE:**
- [ ] Security audit (external contractor)
- [ ] Load test (500+ concurrent users)
- [ ] Data backup & recovery tested
- [ ] Monitoring & alerting configured
- [ ] On-call runbook prepared
- [ ] Incident response plan ready

**Estimated Timeline:**
- Phase 1 (Critical): 1 week
- Phase 2 (High): 1 week  
- Phase 3 (Testing/Deploy): 1 week
- **Total: 3 weeks minimum before production**

---

**Questions?** Review the full audit in `PRODUCTION_READINESS_AUDIT.md`
