"""
services/copilot/autonomous.py — Autonomous Workflow Engine
=============================================================
Background workflows that run on schedule without user input.
Designed to work with Flask's threading (no Celery required).

Usage:
    from services.copilot.autonomous import start_autonomous_worker
    start_autonomous_worker()  # call once at app startup
"""
import time
import threading
from datetime import datetime
from utils.db import get_db

try:
    from utils.logger import app_logger, error_logger
except Exception:
    import logging
    app_logger = logging.getLogger('campaign')
    error_logger = logging.getLogger('errors')


# ── WORKFLOW REGISTRY ─────────────────────────────────────────

WORKFLOWS = {
    'auto_pause_sick_smtp': {
        'interval_minutes': 30,
        'description': 'Pause SMTP accounts with bounce rate > 8%',
        'enabled': True,
    },
    'hot_lead_alert': {
        'interval_minutes': 15,
        'description': 'Detect hot leads (3+ opens) and create alerts',
        'enabled': True,
    },
    'warmup_auto_upgrade': {
        'interval_minutes': 1440,  # daily
        'description': 'Upgrade warmup stage for healthy accounts',
        'enabled': True,
    },
    'dead_lead_cleanup': {
        'interval_minutes': 1440,  # daily
        'description': 'Mark contacts as cold after 5 emails with 0 opens',
        'enabled': True,
    },
    'daily_capacity_reset_check': {
        'interval_minutes': 60,
        'description': 'Alert when daily capacity is running low',
        'enabled': True,
    },
    'auto_enrich_new_contacts': {
        'interval_minutes': 60,
        'description': 'Auto-enrich contacts uploaded in last hour',
        'enabled': True,
    },
}

# Track last run time per workflow
_last_run = {}
_worker_running = False


def start_autonomous_worker():
    """Start the background autonomous worker thread. Call once at app startup."""
    global _worker_running
    if _worker_running:
        return
    _worker_running = True

    def _run():
        app_logger.info('[AUTONOMOUS] Worker started')
        time.sleep(30)  # Wait 30s after startup before first run
        while _worker_running:
            try:
                _tick()
            except Exception as e:
                error_logger.error(f'[AUTONOMOUS] Tick error: {e}')
            time.sleep(60)  # Check every 60 seconds

    t = threading.Thread(target=_run, daemon=True, name='autonomous-worker')
    t.start()


def stop_autonomous_worker():
    """Stop the background worker."""
    global _worker_running
    _worker_running = False


def _tick():
    """Single tick — check which workflows are due and run them."""
    now = datetime.now()

    # Get all workspaces
    conn = get_db()
    workspaces = conn.execute("SELECT id FROM workspaces").fetchall()
    conn.close()

    for ws in workspaces:
        wid = ws['id']
        for wf_name, config in WORKFLOWS.items():
            if not config['enabled']:
                continue

            key = f"{wid}:{wf_name}"
            last = _last_run.get(key)
            interval = config['interval_minutes'] * 60  # to seconds

            if last and (now - last).total_seconds() < interval:
                continue

            # Run workflow
            try:
                result = _execute_workflow(wf_name, wid)
                _last_run[key] = now
                if result.get('actions_taken', 0) > 0:
                    app_logger.info(f'[AUTONOMOUS] {wf_name} wid={wid}: {result}')
            except Exception as e:
                error_logger.error(f'[AUTONOMOUS] {wf_name} wid={wid} failed: {e}')


def _execute_workflow(name: str, workspace_id: int) -> dict:
    """Execute a specific workflow for a workspace."""
    dispatch = {
        'auto_pause_sick_smtp': _wf_auto_pause_smtp,
        'hot_lead_alert': _wf_hot_lead_alert,
        'warmup_auto_upgrade': _wf_warmup_upgrade,
        'dead_lead_cleanup': _wf_dead_lead_cleanup,
        'daily_capacity_reset_check': _wf_capacity_check,
        'auto_enrich_new_contacts': _wf_auto_enrich,
    }
    handler = dispatch.get(name)
    if not handler:
        return {'error': f'Unknown workflow: {name}'}
    return handler(workspace_id)


# ── WORKFLOW IMPLEMENTATIONS ──────────────────────────────────

def _wf_auto_pause_smtp(wid: int) -> dict:
    """Pause SMTP accounts with bounce rate > 8%."""
    conn = get_db()
    accounts = conn.execute("""
        SELECT id, email, health_score FROM smtp_accounts
        WHERE workspace_id=? AND active=1 AND health_score < 40
    """, (wid,)).fetchall()

    paused = 0
    for acc in accounts:
        conn.execute("UPDATE smtp_accounts SET active=0 WHERE id=?", (acc['id'],))
        paused += 1
        _create_alert(conn, wid, 'smtp_auto_paused', 'high',
                      f"SMTP {acc['email']} auto-paused",
                      f"Health score {acc['health_score']}% — paused to protect deliverability")

    if paused:
        conn.commit()
    conn.close()
    return {'actions_taken': paused}


def _wf_hot_lead_alert(wid: int) -> dict:
    """Detect contacts with 3+ opens and no reply sent yet."""
    conn = get_db()
    # Find contacts who opened 3+ times but we haven't replied
    hot = conn.execute("""
        SELECT c.id, c.name, c.company, c.email, COUNT(*) as open_count
        FROM tracking_events te
        JOIN contacts c ON te.contact_id = c.id
        WHERE te.workspace_id=? AND te.event_type='email_open'
        AND c.status != 'replied'
        GROUP BY c.id, c.name, c.company, c.email
        HAVING COUNT(*) >= 3
    """, (wid,)).fetchall()

    created = 0
    for lead in hot[:5]:  # max 5 alerts at once
        # Check if alert already exists
        existing = conn.execute("""
            SELECT id FROM copilot_alerts
            WHERE workspace_id=? AND alert_type='hot_lead' AND dismissed=0
            AND data LIKE ?
        """, (wid, f'%"contact_id": {lead["id"]}%')).fetchone()

        if not existing:
            import json
            _create_alert(conn, wid, 'hot_lead', 'high',
                          f"🔥 Hot lead: {lead['name']} ({lead['company']})",
                          f"Opened {lead['open_count']}x — high buying signal, reply ASAP",
                          json.dumps({'contact_id': lead['id'], 'email': lead['email']}))
            created += 1

    if created:
        conn.commit()
    conn.close()
    return {'actions_taken': created}


def _wf_warmup_upgrade(wid: int) -> dict:
    """Upgrade warmup stage for accounts with health > 90%."""
    conn = get_db()
    accounts = conn.execute("""
        SELECT id, email, warmup_stage, daily_limit, health_score
        FROM smtp_accounts
        WHERE workspace_id=? AND active=1 AND health_score >= 90 AND warmup_stage < 5
    """, (wid,)).fetchall()

    upgraded = 0
    for acc in accounts:
        new_stage = acc['warmup_stage'] + 1
        new_limit = min(acc['daily_limit'] + 10, 100)
        conn.execute("""
            UPDATE smtp_accounts SET warmup_stage=?, daily_limit=? WHERE id=?
        """, (new_stage, new_limit, acc['id']))
        upgraded += 1

    if upgraded:
        conn.commit()
    conn.close()
    return {'actions_taken': upgraded}


def _wf_dead_lead_cleanup(wid: int) -> dict:
    """Mark contacts as 'cold' if 5+ emails sent with 0 opens."""
    conn = get_db()
    dead = conn.execute("""
        SELECT c.id, c.name, COUNT(*) as sent_count
        FROM contacts c
        JOIN emails_sent es ON es.contact_id = c.id
        WHERE c.workspace_id=? AND es.status='sent' AND es.opened=0
        AND c.status NOT IN ('cold', 'replied', 'unsubscribed')
        GROUP BY c.id, c.name
        HAVING COUNT(*) >= 5
    """, (wid,)).fetchall()

    marked = 0
    for d in dead:
        conn.execute("UPDATE contacts SET status='cold' WHERE id=?", (d['id'],))
        marked += 1

    if marked:
        conn.commit()
    conn.close()
    return {'actions_taken': marked}


def _wf_capacity_check(wid: int) -> dict:
    """Alert if daily send capacity is below 10."""
    conn = get_db()
    capacity = conn.execute("""
        SELECT COALESCE(SUM(daily_limit - sent_today), 0)
        FROM smtp_accounts WHERE workspace_id=? AND active=1
    """, (wid,)).fetchone()[0]

    created = 0
    if capacity < 10:
        existing = conn.execute("""
            SELECT id FROM copilot_alerts
            WHERE workspace_id=? AND alert_type='low_capacity' AND dismissed=0
        """, (wid,)).fetchone()
        if not existing:
            _create_alert(conn, wid, 'low_capacity', 'medium',
                          'Daily send capacity running low',
                          f'Only {capacity} emails remaining in today\'s quota')
            created = 1
            conn.commit()

    conn.close()
    return {'actions_taken': created}


def _wf_auto_enrich(wid: int) -> dict:
    """Auto-enrich contacts added in the last hour without context."""
    conn = get_db()
    new_contacts = conn.execute("""
        SELECT id FROM contacts
        WHERE workspace_id=? AND (context IS NULL OR context='')
        AND created_at >= datetime('now', '-1 hour')
        LIMIT 10
    """, (wid,)).fetchall()
    conn.close()

    if not new_contacts:
        return {'actions_taken': 0}

    # Trigger enrichment in background thread
    contact_ids = [c['id'] for c in new_contacts]

    def _enrich():
        try:
            from services.industry_detector import enrich_contacts_bulk_intelligence
            enrich_contacts_bulk_intelligence(contact_ids, wid)
        except Exception:
            pass

    t = threading.Thread(target=_enrich, daemon=True)
    t.start()
    return {'actions_taken': len(contact_ids)}


# ── HELPERS ───────────────────────────────────────────────────

def _create_alert(conn, workspace_id: int, alert_type: str, severity: str,
                  title: str, message: str, data: str = '{}'):
    """Create a copilot alert in the DB."""
    try:
        conn.execute("""
            INSERT INTO copilot_alerts (workspace_id, alert_type, severity, title, message, data)
            VALUES (?,?,?,?,?,?)
        """, (workspace_id, alert_type, severity, title, message, data))
    except Exception:
        pass


# ── PUBLIC API ────────────────────────────────────────────────

def get_workflow_status() -> dict:
    """Get status of all workflows (for admin UI)."""
    status = {}
    for name, config in WORKFLOWS.items():
        status[name] = {
            'enabled': config['enabled'],
            'interval_minutes': config['interval_minutes'],
            'description': config['description'],
            'last_run': {k.split(':')[1]: v.isoformat() for k, v in _last_run.items() if k.endswith(name)},
        }
    return status


def toggle_workflow(name: str, enabled: bool):
    """Enable/disable a workflow."""
    if name in WORKFLOWS:
        WORKFLOWS[name]['enabled'] = enabled


def run_workflow_now(name: str, workspace_id: int) -> dict:
    """Manually trigger a workflow."""
    if name not in WORKFLOWS:
        return {'error': f'Unknown workflow: {name}'}
    return _execute_workflow(name, workspace_id)
