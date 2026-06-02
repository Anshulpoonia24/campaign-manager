"""
routes/copilot.py — AI SDR Copilot API Endpoints
==================================================
Replaces the old /api/copilot/chat and /api/copilot/action in app.py.
Register: app.register_blueprint(copilot_bp)
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

copilot_bp = Blueprint('copilot', __name__)


def _get_wid():
    return getattr(current_user, 'workspace_id', 1)


def _get_role():
    return getattr(current_user, 'role', 'user')


# ── CHAT ──────────────────────────────────────────────────────

@copilot_bp.route('/api/copilot/chat', methods=['POST'])
@login_required
def copilot_chat():
    """Main conversational endpoint."""
    from services.copilot.orchestrator import CopilotOrchestrator
    data = request.json or {}
    message = data.get('message', '').strip()
    page_type = data.get('page_type', '')
    page_id = int(data.get('page_id', 0))

    if not message:
        return jsonify({'success': False, 'error': 'Empty message'})

    orchestrator = CopilotOrchestrator(
        workspace_id=_get_wid(),
        user_id=current_user.id,
        role=_get_role()
    )
    result = orchestrator.chat(message, page_type, page_id)
    return jsonify(result)


# ── ACTION EXECUTION ──────────────────────────────────────────

@copilot_bp.route('/api/copilot/action', methods=['POST'])
@login_required
def copilot_action():
    """Execute a confirmed copilot action."""
    from services.copilot.orchestrator import CopilotOrchestrator
    data = request.json or {}
    action_type = data.get('action_type', '')
    params = data.get('params', {})
    session_id = data.get('session_id', '')

    if not action_type:
        return jsonify({'success': False, 'error': 'No action_type'})

    orchestrator = CopilotOrchestrator(
        workspace_id=_get_wid(),
        user_id=current_user.id,
        role=_get_role()
    )
    result = orchestrator.execute_action(action_type, params, session_id)
    return jsonify(result)


# ── PROACTIVE SUGGESTIONS ────────────────────────────────────

@copilot_bp.route('/api/copilot/suggestions')
@login_required
def copilot_suggestions():
    """Get proactive suggestions for the current page."""
    from services.copilot.orchestrator import CopilotOrchestrator
    page_type = request.args.get('page_type', '')
    page_id = int(request.args.get('page_id', 0))

    orchestrator = CopilotOrchestrator(
        workspace_id=_get_wid(),
        user_id=current_user.id,
        role=_get_role()
    )
    suggestions = orchestrator.get_suggestions(page_type, page_id)
    return jsonify({'suggestions': suggestions})


# ── ALERTS ────────────────────────────────────────────────────

@copilot_bp.route('/api/copilot/alerts')
@login_required
def copilot_alerts():
    """Get active alerts for workspace."""
    from services.copilot.context_builder import ContextBuilder
    builder = ContextBuilder(_get_wid(), current_user.id)
    alerts = builder._active_alerts()
    return jsonify({'alerts': alerts})


@copilot_bp.route('/api/copilot/alerts/dismiss', methods=['POST'])
@login_required
def copilot_dismiss_alert():
    """Dismiss an alert."""
    from utils.db import get_db
    from datetime import datetime
    alert_id = (request.json or {}).get('alert_id')
    if alert_id:
        conn = get_db()
        conn.execute("UPDATE copilot_alerts SET dismissed=1, dismissed_at=? WHERE id=? AND workspace_id=?",
                     (datetime.now(), alert_id, _get_wid()))
        conn.commit()
        conn.close()
    return jsonify({'success': True})


# ── AUDIT LOG ─────────────────────────────────────────────────

@copilot_bp.route('/api/copilot/audit')
@login_required
def copilot_audit():
    """Get action audit log."""
    from utils.db import get_db
    limit = int(request.args.get('limit', 50))
    conn = get_db()
    rows = conn.execute("""
        SELECT id, action_type, action_params, risk_level, status,
               error_message, execution_time_ms, created_at
        FROM copilot_actions WHERE workspace_id=?
        ORDER BY created_at DESC LIMIT ?
    """, (_get_wid(), limit)).fetchall()
    conn.close()
    return jsonify({'actions': [dict(r) for r in rows]})


# ── USAGE STATS ───────────────────────────────────────────────

@copilot_bp.route('/api/copilot/usage')
@login_required
def copilot_usage():
    """AI usage stats for copilot."""
    from utils.db import get_db
    conn = get_db()
    total_chats = conn.execute(
        "SELECT COUNT(*) FROM copilot_logs WHERE workspace_id=?", (_get_wid(),)
    ).fetchone()[0]
    total_actions = conn.execute(
        "SELECT COUNT(*) FROM copilot_actions WHERE workspace_id=?", (_get_wid(),)
    ).fetchone()[0]
    successful = conn.execute(
        "SELECT COUNT(*) FROM copilot_actions WHERE workspace_id=? AND status='completed'", (_get_wid(),)
    ).fetchone()[0]
    conn.close()
    return jsonify({
        'total_chats': total_chats,
        'total_actions': total_actions,
        'successful_actions': successful,
        'success_rate': round(successful / max(1, total_actions) * 100, 1),
    })
