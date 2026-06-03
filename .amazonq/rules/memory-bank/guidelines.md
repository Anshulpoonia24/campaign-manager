# Development Guidelines

## Code Quality Standards

### File Header Convention
Every non-trivial service module starts with a docstring block:
```python
"""
services/tracking.py — OutreachOS Event Tracking Engine
=========================================================
The behavioral intelligence layer.

Handles:
- Signed token generation/verification
- Event logging to tracking_events table
...
"""
```
- First line: `filename — Short Title`
- Second line: visual separator (=)
- Third line: one-sentence role description
- Bullet list of responsibilities

### Section Separators
Major logical sections within large files use visual banners:
```python
# ── SHORT LABEL ───────────────────────────────────────────────
# or
# ══════════════════════════════════════════════════════════════
# SECTION NAME
# ══════════════════════════════════════════════════════════════
```
Use `──` (em-dash) for subsections, `══` (double line) for major sections.

### Naming Conventions
- Module-level constants: `UPPER_SNAKE_CASE` (e.g. `SCORE_WEIGHTS`, `BOT_PATTERNS`, `BACKUP_INTERVAL_SECONDS`)
- Private helpers: `_leading_underscore` (e.g. `_sign()`, `_update_score()`, `_process_legacy_open()`)
- Classes used as namespaces/enums: `PascalCase` with string constants (e.g. `class Event`, `class ActionDef`)
- DB query functions: descriptive verbs — `get_`, `update_`, `ensure_`, `log_`, `process_`
- Handler paths in registry: dotted string `'module.submodule.function'` (lazy loaded)

### Type Hints
Functions in service modules use type hints consistently:
```python
def generate_token(workspace_id: int, contact_id: int, campaign_id: int,
                   email_sent_id: int = 0, thread_id: int = 0) -> str:

def decode_token(token: str) -> dict | None:

def backup_db(db_path: str, backup_dir: str) -> str | None:
```
Use `type | None` union syntax (Python 3.10+).

---

## Database Patterns

### Always Use try/finally for DB Connections
Every function that opens a DB connection must close it in a `finally` block:
```python
conn = get_db()
try:
    # ... DB operations ...
    conn.commit()
    return result
except Exception as e:
    error_logger.error(f'[MODULE] operation failed: {e}')
    return None
finally:
    conn.close()
```

### PostgreSQL vs SQLite Branching
When behavior differs between backends, branch on `USE_POSTGRES`:
```python
from utils.db import USE_POSTGRES
if USE_POSTGRES and hasattr(conn, 'raw'):
    row = conn.execute("... RETURNING id", params).fetchone()
    event_id = row[0] if row else None
else:
    conn.execute("INSERT ...", params)
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    event_id = row[0] if row else None
```

### Safe Migrations (Exception Silencing)
Schema migrations use bare `except Exception: pass` — intentional, not a bug:
```python
for migration in ["ALTER TABLE contacts ADD COLUMN lead_score INTEGER DEFAULT 0", ...]:
    try:
        conn.execute(migration)
        conn.commit()
    except Exception:
        pass  # Column already exists
```

### Index Creation Pattern
Indexes are always created with `IF NOT EXISTS`:
```python
"CREATE INDEX IF NOT EXISTS idx_te_workspace ON tracking_events(workspace_id)"
```

### Row Access
Rows are accessed by column name (dict-like), not position:
```python
row['contact_id']   # correct
row[0]              # only when using last_insert_rowid() or RETURNING
```

---

## Error Handling Patterns

### Structured Log Prefixes
Every log message uses a bracketed module prefix:
```python
app_logger.info(f'[TRACK] {event_type} | workspace={workspace_id} contact={contact_id}')
error_logger.error(f'[TRACK] log_event failed: {e}')
logger.info(f'[BACKUP] Created: {backup_path.name} ({size_kb} KB)')
```
Format: `[MODULE_NAME] action | key=value pairs`

### Return None on Failure
Service functions return `None` (or `False`) on failure rather than raising:
```python
def log_event(...) -> int | None:
    try:
        ...
        return event_id
    except Exception as e:
        error_logger.error(f'[TRACK] log_event failed: {e}')
        return None
    finally:
        conn.close()
```

### Graceful Degradation on Redirect
Click tracking still redirects even if logging fails:
```python
except Exception as e:
    error_logger.error(f'[TRACK] process_click error: {e}')
    return decoded_url  # Still redirect even if logging fails
```

---

## Service Layer Patterns

### Registry Pattern (action_registry.py)
Extensible action registry using `@dataclass` + dict:
```python
@dataclass
class ActionDef:
    name: str
    description: str
    category: str
    risk_level: str
    requires_confirmation: bool
    params_schema: dict
    handler_path: str       # lazy-loaded dotted path
    page_types: List[str] = field(default_factory=list)
    rollback_path: Optional[str] = None

ACTION_REGISTRY: Dict[str, ActionDef] = {}

def register(name: str, **kwargs):
    ACTION_REGISTRY[name] = ActionDef(name=name, **kwargs)
```

### Risk Level Constants
Define risk levels as module-level string constants (not enums):
```python
RISK_SAFE = 'safe'
RISK_LOW = 'low'
RISK_MEDIUM = 'medium'
RISK_HIGH = 'high'
RISK_CRITICAL = 'critical'
```

### Enum-as-Class Pattern
Use classes with string constants instead of Python enums for event types:
```python
class Event:
    EMAIL_OPEN      = 'email_open'
    LINK_CLICK      = 'link_click'
    REPLY_RECEIVED  = 'reply_received'
    BOUNCE          = 'bounce'
```
Allows direct DB string storage without `.value` calls.

### Scoring/Lookup Tables
Use module-level dicts for scoring weights, thresholds, labels, icons, colors:
```python
SCORE_WEIGHTS = {
    Event.EMAIL_OPEN:     2,
    'multiple_opens':     5,
    Event.LINK_CLICK:     10,
    Event.REPLY_RECEIVED: 25,
    'interested':         40,
    'meeting':            60,
}

TEMPERATURE = {
    'meeting_ready': 100,
    'hot':           50,
    'warm':          20,
    'cold':          0,
}
```

### Intent Detection Pattern
Fast local regex matching first, AI fallback for ambiguous cases:
```python
def detect_intent(message: str, page_type: str = '') -> dict:
    msg = message.lower().strip()
    # 1. Regex patterns (no AI call)
    for intent_name, config in INTENTS.items():
        for pattern in config['patterns']:
            if re.search(pattern, msg, re.IGNORECASE):
                return {'intent': intent_name, 'confidence': 0.85, ...}
    # 2. Context-based inference
    if page_type == 'campaign_status' and ...:
        return {'intent': 'diagnose_campaign', 'confidence': 0.7, ...}
    # 3. AI fallback
    return {'intent': 'unknown', 'confidence': 0.0, ...}
```

### Lazy Handler Loading
Copilot action handlers are referenced by dotted string path, not imported at module load:
```python
handler_path='services.copilot.handlers.campaign.pause_campaign'
```
This avoids circular imports and allows conditional handler availability.

---

## Security Patterns

### HMAC Token Signing
Tracking tokens use HMAC-SHA256 with a truncated signature:
```python
_SECRET = os.getenv('SECRET_KEY', 'outreachos-tracking-secret').encode()

def _sign(payload: str) -> str:
    return hmac.HMAC(_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:16]
```
Always use `hmac.compare_digest()` for signature verification (timing-safe).

### Token Expiry
Tokens include a timestamp and are validated for 90-day expiry:
```python
if datetime.now().timestamp() - int(ts) > 90 * 86400:
    return None
```

### URL Safety Validation
All redirect URLs are validated before use:
```python
BLOCKED_URL_PATTERNS = re.compile(r'(javascript:|data:|vbscript:|file://)', re.IGNORECASE)

def is_safe_url(url: str) -> bool:
    if not url: return False
    if BLOCKED_URL_PATTERNS.search(url): return False
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in ('http', 'https')
```

### Bot Filtering
Open/click tracking filters bots by User-Agent:
```python
BOT_PATTERNS = re.compile(
    r'(bot|crawler|spider|scan|preview|prefetch|apple.*mail|...)',
    re.IGNORECASE
)

def is_bot(user_agent: str, ip: str = '') -> bool:
    if not user_agent: return True
    if BOT_PATTERNS.search(user_agent): return True
    if len(user_agent) < 10: return True
    return False
```

### Ownership Guards
All mutations check workspace/user ownership before executing:
```python
from utils.ownership import owns_contact, owns_campaign, owns_smtp_account
if not owns_contact(contact_id):
    return jsonify({'success': False, 'error': 'Not found'}), 404
```

---

## Backup & Utility Patterns

### Time-Guard Pattern
Prevent redundant operations using mtime-based guards:
```python
existing = sorted(backup_dir.glob('campaigns_*.db'))
if existing:
    latest_mtime = existing[-1].stat().st_mtime
    age_seconds = time.time() - latest_mtime
    if age_seconds < BACKUP_INTERVAL_SECONDS:
        logger.info(f'[BACKUP] Last backup is {int(age_seconds/3600)}h old — skipping')
        return None
```

### Retention Cleanup
Manage retention by sorting and slicing:
```python
all_backups = sorted(backup_dir.glob('campaigns_*.db'))
to_delete = all_backups[:-BACKUP_RETENTION_COUNT]
for old in to_delete:
    old.unlink()
```

### Pathlib over os.path
Prefer `pathlib.Path` for file operations in utility modules:
```python
from pathlib import Path
backup_dir = Path(backup_dir)
backup_dir.mkdir(parents=True, exist_ok=True)
backup_path = backup_dir / f'campaigns_{ts}.db'
```

---

## API Route Patterns

### JSON vs HTML Response Branching
Routes that serve both browser and API clients branch on request type:
```python
@app.errorhandler(429)
def ratelimit_handler(e):
    if request.is_json or request.path.startswith('/api/'):
        return jsonify({'error': 'Too many requests. Slow down!'}), 429
    flash('Too many requests! Please slow down.', 'error')
    return redirect(url_for('dashboard')), 429
```

### Celery-with-Threading-Fallback
All async operations check Celery availability first:
```python
if CELERY_AVAILABLE and has_active_workers():
    result = some_task.apply_async(args=[...], queue='queue_name')
    return jsonify({'success': True, 'queued': True, 'task_id': result.id})
# Fallback: synchronous or threading
import threading
t = threading.Thread(target=sync_function, args=[...], daemon=False)
t.start()
return jsonify({'success': True, 'queued': False, 'mode': 'thread'})
```

### Workspace Scoping on Every Query
All workspace-sensitive queries include workspace_id:
```python
from services.workspace_service import get_wid
wid = get_wid()
conn.execute("SELECT * FROM contacts WHERE workspace_id=?", (wid,))
```

### Rate Limiting on Sensitive Endpoints
Use `@limiter.limit()` on mutation endpoints:
```python
@app.route('/verify_emails', methods=['POST'])
@login_required
@limiter.limit("3 per minute")
def verify_emails_route():
```

---

## Inline Caching Patterns

### TTL Cache Class
In-memory caches with TTL and max-size eviction use a consistent pattern:
```python
class _TTLCache:
    def __init__(self):
        self._d = {}
    def __setitem__(self, k, v):
        if len(self._d) >= _CACHE_MAX:
            oldest = min(self._d, key=lambda x: self._d[x][1])
            del self._d[oldest]
        self._d[k] = (v, _time.time())
    def __getitem__(self, k):
        v, ts = self._d[k]
        if _time.time() - ts > _CACHE_TTL:
            del self._d[k]
            raise KeyError(k)
        return v
    def __contains__(self, k):
        try: self[k]; return True
        except KeyError: return False
    def get(self, k, default=None):
        try: return self[k]
        except KeyError: return default
```
Two instances exist: `mx_cache` (MX DNS, 24h TTL, 1000 max) and `ai_generated_cache` (AI email bodies, 30min TTL, 500 max).

---

## Copilot Architecture Conventions

### Page-Type Scoping
Every action definition declares which pages it's available on:
```python
register('pause_campaign',
    page_types=['campaign_status', 'dashboard', 'campaigns'])

register('navigate',
    page_types=[])  # Empty = available everywhere
```

### Params Schema Format
Action parameters use a simple dict schema (not jsonschema):
```python
params_schema={
    'campaign_id': {'type': 'integer', 'required': True},
    'status': {'type': 'string', 'required': True,
               'enum': ['active', 'interested', 'meeting']},
    'days': {'type': 'integer', 'default': 7},
}
```

### Confidence-Based Intent Return
Intent detection always returns a confidence score dict:
```python
return {
    'intent': intent_name,
    'confidence': 0.85,   # pattern match
    'category': config['category'],
    'entities': entities,
}
# Unknown:
return {'intent': 'unknown', 'confidence': 0.0, 'category': 'general', 'entities': {}}
```
