# QUICK REFERENCE: TOP 20 MOST CRITICAL 500 ERRORS

## MUST-FIX CRITICAL ISSUES

### 🔴 TIER 1: PRODUCTION BLOCKING (Fix First)

1. **Email Send Blocking in Request Handler** [routes/campaigns.py:158-200]
   - Problem: Sends all emails synchronously in HTTP request
   - Impact: 100 emails = 500+ second hang, hits 30s timeout, returns 500
   - Frequency: EVERY campaign send with 100+ contacts
   - Fix: Move to Celery task (already defined in tasks/email_tasks.py)

2. **No Database Connection Pooling** [app.py:280]
   - Problem: Creates new connection per request, no reuse, no limits
   - Impact: 100 concurrent users = 100 connections, PostgreSQL limit = 100, user 101 gets 500
   - Frequency: HIGH under any production traffic
   - Fix: Implement pg8000 connection pool or use SQLAlchemy

3. **Unprotected SMTP Connection Timeout** [services/smtp_service.py:30]
   - Problem: No timeout on SMTP connect/starttls/login
   - Impact: SMTP server down = 30+ second hang per email, then 500
   - Frequency: Every SMTP outage (2-3x per year for most providers)
   - Fix: Add timeout=10, wrap in try/except, return error to caller

4. **Missing Try/Except Around Database Queries** [routes/campaigns.py:108-200]
   - Problem: Direct database access, no error handling
   - Impact: Database locked = 500, connection pool exhausted = 500
   - Frequency: HIGH during maintenance or concurrent loads
   - Fix: Wrap all database calls in try/except, return user-friendly error

5. **Broad Exception Handling That Hides Errors** [routes/auth.py:87-159]
   - Problem: `except Exception` without logging the exception
   - Impact: Real error never logged, user sees 500, you can't debug
   - Frequency: EVERY failure uses this pattern
   - Fix: Always log with `error_logger.exception(e)` not just `app_logger.info(...)`

---

### 🟡 TIER 2: HIGH IMPACT (Fix This Week)

6. **Large File Upload Without Size Validation** [routes/contacts.py:105]
   - Problem: Loads entire CSV into pandas DataFrame
   - Impact: 500MB file = MemoryError, crashes server
   - Frequency: HIGH with real users
   - Fix: Add MAX_FILE_SIZE check, stream processing

7. **No Timeout on External API Calls** [services/ai_service.py:32-70]
   - Problem: Groq/Gemini/Ollama calls with no timeout
   - Impact: LLM provider slow = user waits forever, browser timeout = 500
   - Frequency: MEDIUM, depends on provider stability
   - Fix: Reduce timeout to 15s, add retry logic, fail gracefully

8. **Type Conversion Without Validation** [routes/settings.py:80]
   - Problem: `int(get_setting('smtp_port'))` with no null/type check
   - Impact: Setting is NULL or "abc" = TypeError = 500
   - Frequency: HIGH when settings incomplete
   - Fix: `int(get_setting('smtp_port') or '587')` with try/except

9. **Query Result Access Without Validation** [services/campaign_executor.py:140]
   - Problem: `.fetchone()[0]` without checking if result is None
   - Impact: No rows returned = IndexError = 500
   - Frequency: MEDIUM on edge case data
   - Fix: Use `fetchone() or {}`; catch IndexError

10. **Connection Not Closed on Exception** [routes/campaigns.py:175]
    - Problem: `conn = get_db()` ... exception ... `conn.close()` never called
    - Impact: Connection leak, pool exhaustion, eventual 500s
    - Frequency: HIGH over time
    - Fix: Use try/finally, or context manager: `with get_db() as conn:`

11. **Missing Null Checks Before Method Calls** [routes/contacts.py:170]
    - Problem: `str(row.get(col, '')).strip()` but row.get() returns None
    - Impact: None.strip() = AttributeError = 500
    - Frequency: HIGH with malformed CSV files
    - Fix: `(row.get(col) or '').strip()`

12. **Race Condition in GET+UPDATE** [routes/campaigns.py:155-170]
    - Problem: Check if email sent, then insert (not atomic)
    - Impact: Two concurrent sends = duplicate insert = constraint violation = 500
    - Frequency: HIGH with concurrent users
    - Fix: Use database UNIQUE constraint + INSERT...ON CONFLICT

13. **IMAP Connection Not Handled** [tasks/inbox_tasks.py:40-80]
    - Problem: IMAP4_SSL() with no try/except, no timeout
    - Impact: IMAP provider down = task crashes, no replies tracked
    - Frequency: MEDIUM on provider outages
    - Fix: Add timeout=30, wrap in try/except, log errors

14. **Dict Key Access Without .get()** [routes/campaigns.py:160]
    - Problem: `creds['password']` instead of `creds.get('password')`
    - Impact: Missing key = KeyError = 500
    - Frequency: MEDIUM when SMTP settings corrupt
    - Fix: Use .get() with default, or validate structure

15. **Index Out of Bounds on List Access** [services/ai_service.py:45]
    - Problem: `r.json()['choices'][0]` but choices could be empty list
    - Impact: Empty list = IndexError = 500
    - Frequency: MEDIUM when LLM returns unexpected response
    - Fix: Check `if r.json().get('choices')` before accessing [0]

---

### 🟠 TIER 3: MEDIUM IMPACT (Fix This Sprint)

16. **Query Loads All Rows Into Memory** [routes/analytics.py:195-230]
    - Problem: `SELECT ... FROM emails_sent` (might be 1M+ rows)
    - Impact: Memory spike, eventual OOM = 500 or crash
    - Frequency: MEDIUM when loading logs page with old data
    - Fix: Add LIMIT, pagination, or async export

17. **Malformed Email Header Parsing** [tasks/inbox_tasks.py:75]
    - Problem: `from_h.split('<')[1].split('>')[0]` but > might not exist
    - Impact: Malformed email = IndexError = 500
    - Frequency: LOW but happens with certain email clients
    - Fix: Check `if '<' in from_h and '>' in from_h` first

18. **Missing Environment Variable Fallback** [app.py:230]
    - Problem: `get_setting('smtp_server')` returns '' not None
    - Impact: Later code expects string but gets empty string
    - Frequency: HIGH on first deploy
    - Fix: Validate all required settings on startup, fail fast

19. **Database Column Doesn't Exist** [routes/dashboard.py:80]
    - Problem: Query joins on column that doesn't exist in schema
    - Impact: OperationalError = 500
    - Frequency: HIGH if migration not run
    - Fix: Run migrations before app start, check schema on startup

20. **Two Concurrent Admin Operations** [routes/admin.py:70]
    - Problem: Admin dashboard queries without workspace_id filter
    - Impact: Admin sees data from all workspaces, concurrent ops corrupt
    - Frequency: MEDIUM in multi-tenant setup
    - Fix: Ensure all queries filter by workspace_id

---

## QUICK FIXES (5 Minutes Each)

```python
# FIX 1: Add timeout to SMTP
server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)

# FIX 2: Add try/except around external calls
try:
    r = http_requests.post(..., timeout=15)
except (requests.Timeout, requests.ConnectionError) as e:
    error_logger.exception(e)
    return None, f'Service timeout: {str(e)[:50]}'

# FIX 3: Validate type conversions
smtp_port = int(get_setting('smtp_port') or '587')

# FIX 4: Check null before method call
value = (row.get('column') or '').strip()

# FIX 5: Use finally to ensure cleanup
try:
    conn = get_db()
    # ... operations ...
finally:
    conn.close()
```

---

## ESTIMATED IMPACT IN PRODUCTION

**With 100 daily active users:**
- 2-5% of campaign sends fail (SMTP/API)
- 1-2% of logins fail (Supabase down)
- 5-10% of bulk uploads fail (memory)
- 1-3% random 500s from connection exhaustion

**With 1000 daily active users:**
- 10-15% of operations fail
- Frequent connection limit 500s
- Database slowness cascades
- User experience severely degraded

---

## TESTING CHECKLIST

- [ ] Load test: 100 concurrent requests to /dashboard
- [ ] SMTP server offline → no 500, graceful error
- [ ] 100MB CSV upload → no crash, rejected cleanly
- [ ] Campaign send 1000+ contacts → uses Celery, not blocked
- [ ] Missing SMTP_SERVER env → app starts, shows config error
- [ ] Database connection limit → error message, not 500
- [ ] Groq API down → 500 not returned, fallback to Gemini
- [ ] Malformed email header → task continues, error logged

---

See full audit in: `500_ERROR_AUDIT.md`
