"""
services/copilot/function_caller.py — Smart Function Calling Engine (Phase 6)
===============================================================================
Detects when to auto-execute actions vs suggest them.
Handles batch operations, scheduled actions, inline results.
"""
import re
import json
import threading
from datetime import datetime, timedelta
from utils.db import get_db

try:
    from utils.logger import app_logger
except Exception:
    import logging
    app_logger = logging.getLogger('campaign')


# ── AUTO-EXECUTE PATTERNS ─────────────────────────────────────
# These phrases mean "do it now, don't just suggest"
AUTO_EXECUTE_PHRASES = [
    r'\b(do it|go ahead|execute|run it|yes|confirm|proceed|kar do|karo|haan|sure)\b',
    r'\b(pause it|resume it|stop it|start it|send it|enrich them)\b',
    r'\b(check now|test now|diagnose now|generate now)\b',
]

# Actions safe to auto-execute without explicit confirmation
SAFE_AUTO_EXECUTE = [
    'diagnose_campaign', 'diagnose_deliverability', 'test_smtp_connection',
    'generate_report', 'compare_campaigns', 'predict_best_send_time',
    'summarize_thread', 'draft_reply', 'enrich_contact', 'fetch_context',
    'navigate', 'show_info',
]

# ── SCHEDULED ACTIONS ─────────────────────────────────────────
_scheduled_actions = []  # [{workspace_id, action, params, run_at, created_by}]


def should_auto_execute(message: str, intent: str, confidence: float) -> bool:
    """Determine if user wants immediate execution vs suggestion."""
    if confidence < 0.8:
        return False
    msg_lower = message.lower()
    for pattern in AUTO_EXECUTE_PHRASES:
        if re.search(pattern, msg_lower):
            return True
    return False


def auto_execute_action(workspace_id: int, user_id: int, action_type: str, params: dict) -> dict:
    """Execute an action inline and return result for embedding in chat response."""
    from services.copilot.executor import ActionExecutor
    if action_type not in SAFE_AUTO_EXECUTE:
        return {'auto_executed': False, 'reason': 'requires_confirmation'}
    executor = ActionExecutor(workspace_id, user_id, 'admin')
    result = executor.execute(action_type, params)
    result['auto_executed'] = True
    return result


# ── BATCH OPERATIONS ──────────────────────────────────────────

def batch_pause_campaigns(workspace_id: int, user_id: int, campaign_ids: list = None, **_) -> dict:
    """Pause multiple campaigns at once."""
    conn = get_db()
    if not campaign_ids:
        rows = conn.execute(
            "SELECT id, name FROM campaigns WHERE workspace_id=? AND job_status='running'",
            (workspace_id,)
        ).fetchall()
        campaign_ids = [r['id'] for r in rows]
    paused = 0
    for cid in campaign_ids:
        conn.execute("UPDATE campaigns SET job_status='paused' WHERE id=? AND workspace_id=? AND job_status='running'",
                     (cid, workspace_id))
        paused += conn.cursor().rowcount if hasattr(conn, 'cursor') else 1
    conn.commit()
    conn.close()
    return {'message': f'{paused} campaign(s) paused', 'paused_count': paused}


def batch_test_smtp(workspace_id: int, user_id: int, **_) -> dict:
    """Test all active SMTP connections."""
    import smtplib
    conn = get_db()
    accounts = conn.execute(
        "SELECT id, email, password, smtp_server, smtp_port, login_username FROM smtp_accounts WHERE workspace_id=? AND active=1",
        (workspace_id,)
    ).fetchall()
    conn.close()
    results = []
    for acc in accounts:
        login = acc['login_username'] if acc['login_username'] else acc['email']
        try:
            server = smtplib.SMTP(acc['smtp_server'], int(acc['smtp_port']), timeout=8)
            server.starttls()
            server.login(login, acc['password'])
            server.quit()
            results.append({'email': acc['email'], 'status': 'ok'})
        except Exception as e:
            results.append({'email': acc['email'], 'status': 'failed', 'error': str(e)[:60]})
    ok = sum(1 for r in results if r['status'] == 'ok')
    return {'message': f'{ok}/{len(results)} SMTP accounts connected successfully', 'results': results}


def batch_enrich_contacts(workspace_id: int, user_id: int, limit: int = 50, **_) -> dict:
    """Enrich batch of contacts in background."""
    conn = get_db()
    ids = [r['id'] for r in conn.execute("""
        SELECT id FROM contacts
        WHERE workspace_id=? AND (enrichment_status='pending' OR enrichment_status IS NULL OR enrichment_status='')
        LIMIT ?
    """, (workspace_id, limit)).fetchall()]
    conn.close()
    if not ids:
        return {'message': 'All contacts already enriched'}
    from services.industry_detector import enrich_contacts_bulk_intelligence
    t = threading.Thread(target=enrich_contacts_bulk_intelligence, args=[ids, workspace_id], daemon=True)
    t.start()
    return {'message': f'Enriching {len(ids)} contacts in background'}


def batch_mark_threads(workspace_id: int, user_id: int, thread_ids: list = None, status: str = 'closed', **_) -> dict:
    """Mark multiple threads with a status."""
    if not thread_ids:
        return {'message': 'No thread IDs provided'}
    conn = get_db()
    updated = 0
    for tid in thread_ids:
        conn.execute("UPDATE threads SET status=? WHERE id=? AND workspace_id=?", (status, tid, workspace_id))
        updated += 1
    conn.commit()
    conn.close()
    return {'message': f'{updated} thread(s) marked as {status}'}


# ── SCHEDULED ACTIONS ─────────────────────────────────────────

def schedule_action(workspace_id: int, user_id: int, action_type: str, params: dict, run_at: datetime) -> dict:
    """Schedule an action for future execution."""
    _scheduled_actions.append({
        'workspace_id': workspace_id,
        'user_id': user_id,
        'action_type': action_type,
        'params': params,
        'run_at': run_at,
        'status': 'scheduled',
        'created_at': datetime.now(),
    })
    delta = run_at - datetime.now()
    mins = int(delta.total_seconds() / 60)
    return {'message': f'Scheduled {action_type} in {mins} minutes', 'run_at': run_at.isoformat()}


def parse_schedule_time(message: str) -> datetime:
    """Extract schedule time from natural language."""
    now = datetime.now()
    msg = message.lower()
    # "in X minutes/hours"
    m = re.search(r'in\s+(\d+)\s+(min|minute|hour|hr)', msg)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        if 'hour' in unit or 'hr' in unit:
            return now + timedelta(hours=val)
        return now + timedelta(minutes=val)
    # "at HH:MM" or "at Xpm"
    m = re.search(r'at\s+(\d{1,2}):?(\d{2})?\s*(am|pm)?', msg)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == 'pm' and hour < 12:
            hour += 12
        if ampm == 'am' and hour == 12:
            hour = 0
        target = now.replace(hour=hour, minute=minute, second=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    return None


def check_scheduled_actions():
    """Run due scheduled actions — called by autonomous worker."""
    now = datetime.now()
    from services.copilot.executor import ActionExecutor
    due = [a for a in _scheduled_actions if a['status'] == 'scheduled' and a['run_at'] <= now]
    for action in due:
        action['status'] = 'executing'
        try:
            executor = ActionExecutor(action['workspace_id'], action['user_id'], 'admin')
            executor.execute(action['action_type'], action['params'])
            action['status'] = 'completed'
            app_logger.info(f"[SCHEDULED] Executed {action['action_type']}")
        except Exception as e:
            action['status'] = 'failed'
            app_logger.error(f"[SCHEDULED] Failed {action['action_type']}: {e}")


def get_scheduled_actions(workspace_id: int) -> list:
    """Get pending scheduled actions."""
    return [
        {'action': a['action_type'], 'params': a['params'],
         'run_at': a['run_at'].isoformat(), 'status': a['status']}
        for a in _scheduled_actions
        if a['workspace_id'] == workspace_id and a['status'] == 'scheduled'
    ]
