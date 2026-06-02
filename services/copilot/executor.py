"""
services/copilot/executor.py — Action Execution Engine
========================================================
Handles permission checks, rate limiting, execution, and audit logging.
"""
import time
import json
import importlib
from datetime import datetime
from utils.db import get_db
from utils.logger import app_logger, error_logger
from services.copilot.action_registry import get_action, RISK_SAFE, RISK_LOW


# ── ROLE PERMISSIONS ──────────────────────────────────────────
ROLE_PERMISSIONS = {
    'admin': ['*'],
    'user': [
        'pause_campaign', 'resume_campaign', 'retry_failed_emails',
        'diagnose_campaign', 'draft_reply', 'send_reply',
        'mark_thread_status', 'summarize_thread',
        'enrich_contact', 'bulk_enrich', 'fetch_context',
        'create_sequence', 'generate_step_content',
        'diagnose_deliverability', 'test_smtp_connection',
        'generate_report', 'compare_campaigns', 'predict_best_send_time',
        'navigate', 'show_info',
    ],
    'viewer': [
        'diagnose_campaign', 'summarize_thread',
        'diagnose_deliverability', 'generate_report',
        'compare_campaigns', 'predict_best_send_time',
        'navigate', 'show_info',
    ],
}

# ── RATE LIMITS (action_name → (max_calls, window_seconds)) ──
RATE_LIMITS = {
    'send_reply': (5, 60),
    'retry_failed_emails': (3, 300),
    'bulk_enrich': (1, 120),
    'create_sequence': (5, 300),
}

# In-memory rate limit tracker (per workspace)
_rate_tracker = {}  # {workspace_id: {action: [(timestamp), ...]}}


class ActionExecutor:
    def __init__(self, workspace_id: int, user_id: int, role: str = 'admin'):
        self.wid = workspace_id
        self.uid = user_id
        self.role = role

    def execute(self, action_name: str, params: dict, session_id: str = '') -> dict:
        action = get_action(action_name)
        if not action:
            return {'success': False, 'error': f'Unknown action: {action_name}'}

        # 1. Permission check
        if not self._has_permission(action_name):
            return {'success': False, 'error': 'Permission denied'}

        # 2. Rate limit check
        if self._is_rate_limited(action_name):
            return {'success': False, 'error': 'Rate limited — try again shortly'}

        # 3. Log start
        audit_id = self._log_start(action, params, session_id)

        # 4. Resolve and call handler
        start = time.time()
        try:
            handler = self._resolve_handler(action.handler_path)
            result = handler(self.wid, self.uid, **params)
            elapsed_ms = int((time.time() - start) * 1000)
            self._log_complete(audit_id, result, elapsed_ms)
            app_logger.info(f'[COPILOT] Action {action_name} executed in {elapsed_ms}ms')
            # Phase 7: Track action usage
            try:
                from services.copilot.learning import track_action
                track_action(self.wid, self.uid, action_name)
            except Exception:
                pass
            return {'success': True, **(result or {})}
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            self._log_failed(audit_id, str(e), elapsed_ms)
            error_logger.error(f'[COPILOT] Action {action_name} failed: {e}')
            return {'success': False, 'error': str(e)[:200]}

    def _has_permission(self, action_name: str) -> bool:
        allowed = ROLE_PERMISSIONS.get(self.role, [])
        if '*' in allowed:
            return True
        return action_name in allowed

    def _is_rate_limited(self, action_name: str) -> bool:
        limit = RATE_LIMITS.get(action_name)
        if not limit:
            return False
        max_calls, window = limit
        key = f'{self.wid}:{action_name}'
        now = time.time()

        if key not in _rate_tracker:
            _rate_tracker[key] = []

        # Clean old entries
        _rate_tracker[key] = [t for t in _rate_tracker[key] if now - t < window]

        if len(_rate_tracker[key]) >= max_calls:
            return True

        _rate_tracker[key].append(now)
        return False

    def _resolve_handler(self, handler_path: str):
        """Dynamically import handler function from dotted path."""
        parts = handler_path.rsplit('.', 1)
        module_path, func_name = parts[0], parts[1]
        module = importlib.import_module(module_path)
        return getattr(module, func_name)

    def _log_start(self, action, params: dict, session_id: str) -> int:
        try:
            conn = get_db()
            from utils.db import is_postgres
            if is_postgres():
                row = conn.execute("""
                    INSERT INTO copilot_actions
                      (workspace_id, user_id, session_id, action_type, action_params,
                       risk_level, status, created_at)
                    VALUES (?,?,?,?,?,?,?,?) RETURNING id
                """, (self.wid, self.uid, session_id, action.name,
                      json.dumps(params), action.risk_level, 'executing',
                      datetime.now())).fetchone()
                conn.commit()
                audit_id = row[0] if row else 0
            else:
                conn.execute("""
                    INSERT INTO copilot_actions
                      (workspace_id, user_id, session_id, action_type, action_params,
                       risk_level, status, created_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (self.wid, self.uid, session_id, action.name,
                      json.dumps(params), action.risk_level, 'executing',
                      datetime.now()))
                conn.commit()
                audit_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.close()
            return audit_id
        except Exception:
            return 0

    def _log_complete(self, audit_id: int, result: dict, elapsed_ms: int):
        if not audit_id:
            return
        try:
            conn = get_db()
            conn.execute("""
                UPDATE copilot_actions
                SET status='completed', result=?, execution_time_ms=?
                WHERE id=?
            """, (json.dumps(result or {})[:2000], elapsed_ms, audit_id))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _log_failed(self, audit_id: int, error: str, elapsed_ms: int):
        if not audit_id:
            return
        try:
            conn = get_db()
            conn.execute("""
                UPDATE copilot_actions
                SET status='failed', error_message=?, execution_time_ms=?
                WHERE id=?
            """, (error[:500], elapsed_ms, audit_id))
            conn.commit()
            conn.close()
        except Exception:
            pass
