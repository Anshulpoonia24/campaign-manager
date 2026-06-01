"""
services/tracking.py — OutreachOS Event Tracking Engine
=========================================================
The behavioral intelligence layer.

Handles:
- Signed token generation/verification
- Event logging to tracking_events table
- Bot/scanner filtering
- Lead scoring with weighted rules
- Temperature calculation
- Activity timeline
- Async Celery integration
"""
import os
import re
import json
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime, timedelta
from utils.db import get_db
from utils.logger import app_logger, error_logger

# ── SIGNING SECRET ────────────────────────────────────────────
_SECRET = os.getenv('SECRET_KEY', 'outreachos-tracking-secret').encode()

# ── EVENT TYPES ───────────────────────────────────────────────
class Event:
    EMAIL_OPEN      = 'email_open'
    LINK_CLICK      = 'link_click'
    REPLY_RECEIVED  = 'reply_received'
    FOLLOWUP_SENT   = 'followup_sent'
    UNSUBSCRIBE     = 'unsubscribe'
    BOUNCE          = 'bounce'
    SPAM_WARNING    = 'spam_warning'
    AI_CATEGORIZED  = 'ai_categorized'
    EMAIL_SENT      = 'email_sent'
    MEETING_BOOKED  = 'meeting_booked'

# ── SCORING WEIGHTS ───────────────────────────────────────────
SCORE_WEIGHTS = {
    Event.EMAIL_OPEN:     2,
    'multiple_opens':     5,
    Event.LINK_CLICK:     10,
    Event.REPLY_RECEIVED: 25,
    'interested':         40,
    'meeting':            60,
    Event.MEETING_BOOKED: 80,
    Event.BOUNCE:         -50,
    Event.SPAM_WARNING:   -100,
    Event.UNSUBSCRIBE:    -20,
}

# ── TEMPERATURE THRESHOLDS ────────────────────────────────────
TEMPERATURE = {
    'meeting_ready': 100,
    'hot':           50,
    'warm':          20,
    'cold':          0,
}

# ── BOT USER AGENT PATTERNS ───────────────────────────────────
BOT_PATTERNS = re.compile(
    r'(bot|crawler|spider|scan|preview|prefetch|apple.*mail|'
    r'googleimageproxy|yahoo.*mail|outlook.*preview|'
    r'thunderbird|postfix|sendgrid|mailchimp|'
    r'python-requests|curl|wget|httpx|aiohttp)',
    re.IGNORECASE
)

# ── ALLOWED REDIRECT DOMAINS (safety) ────────────────────────
BLOCKED_URL_PATTERNS = re.compile(
    r'(javascript:|data:|vbscript:|file://)',
    re.IGNORECASE
)


# ══════════════════════════════════════════════════════════════
# TOKEN SYSTEM
# ══════════════════════════════════════════════════════════════

def _sign(payload: str) -> str:
    """HMAC-SHA256 sign a payload."""
    return hmac.HMAC(_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:16]


def generate_token(workspace_id: int, contact_id: int, campaign_id: int,
                   email_sent_id: int = 0, thread_id: int = 0) -> str:
    """
    Generate a signed tracking token.
    Format: base64(wid:cid:camp:esid:tid:ts):signature
    """
    ts = int(datetime.now().timestamp())
    payload = f"{workspace_id}:{contact_id}:{campaign_id}:{email_sent_id}:{thread_id}:{ts}"
    sig = _sign(payload)
    token = base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode().rstrip('=')
    return token


def decode_token(token: str) -> dict | None:
    """
    Decode and verify a tracking token.
    Returns dict with workspace_id, contact_id, campaign_id, email_sent_id, thread_id
    or None if invalid/expired.
    """
    try:
        padded = token + '=' * (4 - len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        parts = decoded.split(':')
        if len(parts) != 7:
            return None
        wid, cid, camp, esid, tid, ts, sig = parts
        payload = f"{wid}:{cid}:{camp}:{esid}:{tid}:{ts}"
        expected_sig = _sign(payload)
        if not hmac.compare_digest(sig, expected_sig):
            return None
        # Tokens expire after 90 days
        if datetime.now().timestamp() - int(ts) > 90 * 86400:
            return None
        return {
            'workspace_id':  int(wid),
            'contact_id':    int(cid),
            'campaign_id':   int(camp),
            'email_sent_id': int(esid),
            'thread_id':     int(tid),
        }
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# BOT FILTERING
# ══════════════════════════════════════════════════════════════

def is_bot(user_agent: str, ip: str = '') -> bool:
    """
    Heuristic bot detection.
    Returns True if request looks like a bot/scanner.
    """
    if not user_agent:
        return True
    if BOT_PATTERNS.search(user_agent):
        return True
    # Very short UA strings are suspicious
    if len(user_agent) < 10:
        return True
    return False


def is_safe_url(url: str) -> bool:
    """Validate redirect URL is safe."""
    if not url:
        return False
    if BLOCKED_URL_PATTERNS.search(url):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ('http', 'https')
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
# EVENT LOGGING
# ══════════════════════════════════════════════════════════════

def log_event(event_type: str, workspace_id: int, contact_id: int = None,
              campaign_id: int = None, thread_id: int = None,
              email_sent_id: int = None, metadata: dict = None,
              ip_address: str = None, user_agent: str = None) -> int | None:
    """
    Log a tracking event to tracking_events table.
    Returns event id or None on failure.
    """
    conn = get_db()
    try:
        meta_json = json.dumps(metadata or {})
        conn.execute("""
            INSERT INTO tracking_events
              (workspace_id, contact_id, campaign_id, thread_id, email_sent_id,
               event_type, metadata, ip_address, user_agent, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (workspace_id, contact_id, campaign_id, thread_id, email_sent_id,
              event_type, meta_json, ip_address, user_agent, datetime.now()))
        conn.commit()
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        event_id = row[0] if row else None
        app_logger.info(f'[TRACK] {event_type} | workspace={workspace_id} contact={contact_id}')
        return event_id
    except Exception as e:
        error_logger.error(f'[TRACK] log_event failed: {e}')
        return None
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# OPEN TRACKING
# ══════════════════════════════════════════════════════════════

def process_open(token: str, ip: str, user_agent: str) -> bool:
    """
    Process an email open event.
    Returns True if logged (not a bot), False if filtered.
    """
    if is_bot(user_agent, ip):
        app_logger.info(f'[TRACK] Bot open filtered | ua={user_agent[:60]}')
        return False

    data = decode_token(token)
    if not data:
        # Fallback: try legacy tracking_id lookup
        return _process_legacy_open(token, ip, user_agent)

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, opened, contact_id FROM emails_sent WHERE id=?",
            (data['email_sent_id'],)
        ).fetchone() if data['email_sent_id'] else None

        is_first_open = not (row and row['opened']) if row else True

        # Update emails_sent
        if data['email_sent_id']:
            conn.execute("UPDATE emails_sent SET opened=1 WHERE id=?", (data['email_sent_id'],))
            conn.commit()

        # Log event
        event_type = Event.EMAIL_OPEN if is_first_open else 'multiple_opens'
        log_event(
            event_type=event_type,
            workspace_id=data['workspace_id'],
            contact_id=data['contact_id'],
            campaign_id=data['campaign_id'],
            thread_id=data['thread_id'],
            email_sent_id=data['email_sent_id'],
            metadata={'is_first_open': is_first_open, 'ip': ip},
            ip_address=ip,
            user_agent=user_agent
        )

        # Update lead score
        _update_score(data['contact_id'], event_type)
        return True

    except Exception as e:
        error_logger.error(f'[TRACK] process_open error: {e}')
        return False
    finally:
        conn.close()


def _process_legacy_open(tracking_id: str, ip: str, user_agent: str) -> bool:
    """Handle old-style UUID tracking IDs (backward compat)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, contact_id, opened, campaign_id, workspace_id FROM emails_sent WHERE tracking_id=?",
            (tracking_id,)
        ).fetchone()
        if not row:
            return False

        is_first = not row['opened']
        conn.execute("UPDATE emails_sent SET opened=1 WHERE tracking_id=?", (tracking_id,))
        conn.commit()

        wid = row['workspace_id'] if row['workspace_id'] else 1
        event_type = Event.EMAIL_OPEN if is_first else 'multiple_opens'
        log_event(
            event_type=event_type,
            workspace_id=wid,
            contact_id=row['contact_id'],
            campaign_id=row['campaign_id'],
            email_sent_id=row['id'],
            metadata={'legacy': True, 'ip': ip},
            ip_address=ip, user_agent=user_agent
        )
        _update_score(row['contact_id'], event_type)
        return True
    except Exception as e:
        error_logger.error(f'[TRACK] legacy open error: {e}')
        return False
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# CLICK TRACKING
# ══════════════════════════════════════════════════════════════

def process_click(click_token: str, original_url: str, tracking_id: str,
                  ip: str, user_agent: str) -> str | None:
    """
    Process a link click event.
    Returns the safe redirect URL or None if invalid.
    """
    if not is_safe_url(original_url):
        error_logger.warning(f'[TRACK] Unsafe URL blocked: {original_url[:100]}')
        return None

    decoded_url = urllib.parse.unquote(original_url)

    conn = get_db()
    try:
        # Find email record via tracking_id
        email_row = conn.execute(
            "SELECT id, contact_id, campaign_id, workspace_id FROM emails_sent WHERE tracking_id=?",
            (tracking_id,)
        ).fetchone() if tracking_id else None

        contact_id  = email_row['contact_id']  if email_row else None
        campaign_id = email_row['campaign_id'] if email_row else None
        wid         = email_row['workspace_id'] if email_row and email_row['workspace_id'] else 1
        esid        = email_row['id']           if email_row else None

        # Find thread
        thread_id = None
        if contact_id:
            t = conn.execute(
                "SELECT id FROM threads WHERE contact_id=? ORDER BY last_message_at DESC LIMIT 1",
                (contact_id,)
            ).fetchone()
            if t:
                thread_id = t['id']

        # Log to email_clicks (backward compat)
        conn.execute("""
            INSERT INTO email_clicks (email_sent_id, thread_id, contact_id, clicked_url, token, workspace_id)
            VALUES (?,?,?,?,?,?)
        """, (esid, thread_id, contact_id, decoded_url, click_token, wid))
        conn.commit()

        # Log to tracking_events
        log_event(
            event_type=Event.LINK_CLICK,
            workspace_id=wid,
            contact_id=contact_id,
            campaign_id=campaign_id,
            thread_id=thread_id,
            email_sent_id=esid,
            metadata={'url': decoded_url[:500], 'token': click_token},
            ip_address=ip,
            user_agent=user_agent
        )

        # Update lead score
        if contact_id:
            _update_score(contact_id, Event.LINK_CLICK)

        return decoded_url

    except Exception as e:
        error_logger.error(f'[TRACK] process_click error: {e}')
        return decoded_url  # Still redirect even if logging fails
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# LEAD SCORING
# ══════════════════════════════════════════════════════════════

def _update_score(contact_id: int, event_type: str, cap: int = 500):
    """Update contact lead score based on event."""
    if not contact_id:
        return
    delta = SCORE_WEIGHTS.get(event_type, 0)
    if delta == 0:
        return
    conn = get_db()
    try:
        conn.execute("""
            UPDATE contacts
            SET lead_score = MAX(0, MIN(?, COALESCE(lead_score, 0) + ?))
            WHERE id = ?
        """, (cap, delta, contact_id))
        conn.commit()
    except Exception as e:
        error_logger.error(f'[TRACK] score update failed: {e}')
    finally:
        conn.close()


def get_temperature(score: int) -> str:
    """Return lead temperature label."""
    if score >= TEMPERATURE['meeting_ready']:
        return 'meeting_ready'
    elif score >= TEMPERATURE['hot']:
        return 'hot'
    elif score >= TEMPERATURE['warm']:
        return 'warm'
    return 'cold'


def get_temperature_color(temp: str) -> str:
    return {
        'meeting_ready': '#7c3aed',
        'hot':           '#dc2626',
        'warm':          '#f59e0b',
        'cold':          '#64748b',
    }.get(temp, '#64748b')


# ══════════════════════════════════════════════════════════════
# ACTIVITY TIMELINE
# ══════════════════════════════════════════════════════════════

def get_contact_timeline(contact_id: int, workspace_id: int, limit: int = 20) -> list:
    """
    Get unified engagement timeline for a contact.
    Returns list of events sorted newest first.
    """
    conn = get_db()
    try:
        events = conn.execute("""
            SELECT event_type, metadata, ip_address, created_at
            FROM tracking_events
            WHERE contact_id = ? AND workspace_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (contact_id, workspace_id, limit)).fetchall()

        timeline = []
        for e in events:
            meta = {}
            try:
                meta = json.loads(e['metadata'] or '{}')
            except Exception:
                pass
            timeline.append({
                'event_type': e['event_type'],
                'metadata':   meta,
                'created_at': e['created_at'],
                'label':      _event_label(e['event_type'], meta),
                'icon':       _event_icon(e['event_type']),
                'color':      _event_color(e['event_type']),
            })
        return timeline
    except Exception as e:
        error_logger.error(f'[TRACK] timeline error: {e}')
        return []
    finally:
        conn.close()


def get_workspace_timeline(workspace_id: int, limit: int = 50) -> list:
    """Get recent events across entire workspace."""
    conn = get_db()
    try:
        events = conn.execute("""
            SELECT te.event_type, te.metadata, te.created_at,
                   c.name as contact_name, c.company,
                   te.contact_id, te.campaign_id
            FROM tracking_events te
            LEFT JOIN contacts c ON te.contact_id = c.id
            WHERE te.workspace_id = ?
            ORDER BY te.created_at DESC
            LIMIT ?
        """, (workspace_id, limit)).fetchall()

        timeline = []
        for e in events:
            meta = {}
            try:
                meta = json.loads(e['metadata'] or '{}')
            except Exception:
                pass
            timeline.append({
                'event_type':    e['event_type'],
                'contact_name':  e['contact_name'] or 'Unknown',
                'company':       e['company'] or '',
                'contact_id':    e['contact_id'],
                'campaign_id':   e['campaign_id'],
                'created_at':    e['created_at'],
                'label':         _event_label(e['event_type'], meta),
                'icon':          _event_icon(e['event_type']),
                'color':         _event_color(e['event_type']),
            })
        return timeline
    except Exception as e:
        error_logger.error(f'[TRACK] workspace timeline error: {e}')
        return []
    finally:
        conn.close()


def _event_label(event_type: str, meta: dict) -> str:
    labels = {
        Event.EMAIL_OPEN:      'Opened email',
        'multiple_opens':      'Opened email again',
        Event.LINK_CLICK:      f"Clicked {meta.get('url','link')[:40]}",
        Event.REPLY_RECEIVED:  'Replied',
        Event.FOLLOWUP_SENT:   'Follow-up sent',
        Event.UNSUBSCRIBE:     'Unsubscribed',
        Event.BOUNCE:          'Email bounced',
        Event.SPAM_WARNING:    'Marked as spam',
        Event.AI_CATEGORIZED:  f"AI: {meta.get('category','categorized')}",
        Event.EMAIL_SENT:      'Email sent',
        Event.MEETING_BOOKED:  'Meeting booked',
    }
    return labels.get(event_type, event_type.replace('_', ' ').title())


def _event_icon(event_type: str) -> str:
    icons = {
        Event.EMAIL_OPEN:     'fa-eye',
        'multiple_opens':     'fa-eye',
        Event.LINK_CLICK:     'fa-mouse-pointer',
        Event.REPLY_RECEIVED: 'fa-reply',
        Event.FOLLOWUP_SENT:  'fa-paper-plane',
        Event.UNSUBSCRIBE:    'fa-times-circle',
        Event.BOUNCE:         'fa-exclamation-triangle',
        Event.SPAM_WARNING:   'fa-shield-alt',
        Event.AI_CATEGORIZED: 'fa-robot',
        Event.EMAIL_SENT:     'fa-paper-plane',
        Event.MEETING_BOOKED: 'fa-calendar-check',
    }
    return icons.get(event_type, 'fa-circle')


def _event_color(event_type: str) -> str:
    colors = {
        Event.EMAIL_OPEN:     '#6366f1',
        'multiple_opens':     '#8b5cf6',
        Event.LINK_CLICK:     '#f59e0b',
        Event.REPLY_RECEIVED: '#10b981',
        Event.FOLLOWUP_SENT:  '#3b82f6',
        Event.UNSUBSCRIBE:    '#6B7280',
        Event.BOUNCE:         '#ef4444',
        Event.SPAM_WARNING:   '#dc2626',
        Event.AI_CATEGORIZED: '#6366f1',
        Event.EMAIL_SENT:     '#9CA3AF',
        Event.MEETING_BOOKED: '#7c3aed',
    }
    return colors.get(event_type, '#9CA3AF')


# ══════════════════════════════════════════════════════════════
# ANALYTICS AGGREGATION
# ══════════════════════════════════════════════════════════════

def get_engagement_stats(workspace_id: int, days: int = 30) -> dict:
    """Get engagement stats for a workspace over N days."""
    conn = get_db()
    try:
        since = datetime.now() - timedelta(days=days)
        rows = conn.execute("""
            SELECT event_type, COUNT(*) as count
            FROM tracking_events
            WHERE workspace_id = ? AND created_at >= ?
            GROUP BY event_type
        """, (workspace_id, since)).fetchall()

        stats = {r['event_type']: r['count'] for r in rows}
        total_sent = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status='sent'",
            (workspace_id,)
        ).fetchone()[0]

        opens  = stats.get(Event.EMAIL_OPEN, 0) + stats.get('multiple_opens', 0)
        clicks = stats.get(Event.LINK_CLICK, 0)
        replies = stats.get(Event.REPLY_RECEIVED, 0)

        return {
            'total_sent':  total_sent,
            'opens':       opens,
            'clicks':      clicks,
            'replies':     replies,
            'open_rate':   round(opens  / total_sent * 100, 1) if total_sent else 0,
            'click_rate':  round(clicks / total_sent * 100, 1) if total_sent else 0,
            'reply_rate':  round(replies / total_sent * 100, 1) if total_sent else 0,
            'by_type':     stats,
        }
    except Exception as e:
        error_logger.error(f'[TRACK] engagement_stats error: {e}')
        return {}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# DB MIGRATION HELPER
# ══════════════════════════════════════════════════════════════

def ensure_tracking_table():
    """Create tracking_events table if not exists. Called from init_db."""
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracking_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id  INTEGER DEFAULT 1,
                contact_id    INTEGER,
                campaign_id   INTEGER,
                thread_id     INTEGER,
                email_sent_id INTEGER,
                event_type    TEXT NOT NULL,
                metadata      TEXT DEFAULT '{}',
                ip_address    TEXT,
                user_agent    TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Indexes for fast queries
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_te_workspace  ON tracking_events(workspace_id)",
            "CREATE INDEX IF NOT EXISTS idx_te_contact    ON tracking_events(contact_id)",
            "CREATE INDEX IF NOT EXISTS idx_te_campaign   ON tracking_events(campaign_id)",
            "CREATE INDEX IF NOT EXISTS idx_te_event_type ON tracking_events(event_type)",
            "CREATE INDEX IF NOT EXISTS idx_te_created    ON tracking_events(created_at DESC)",
            # Add workspace_id to email_clicks if missing
            "ALTER TABLE email_clicks ADD COLUMN workspace_id INTEGER DEFAULT 1",
        ]:
            try:
                conn.execute(idx_sql)
            except Exception:
                pass
        conn.commit()
    except Exception as e:
        error_logger.error(f'[TRACK] ensure_tracking_table error: {e}')
    finally:
        conn.close()
