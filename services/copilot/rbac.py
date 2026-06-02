"""
services/copilot/rbac.py — Enterprise RBAC & Team Collaboration (Phase 9)
==========================================================================
Role-based access control, team permissions, activity audit.
"""
import json
from datetime import datetime
from functools import wraps
from flask import request, jsonify
from flask_login import current_user
from utils.db import get_db


# ── ROLE DEFINITIONS ──────────────────────────────────────────
ROLES = {
    'owner': {
        'level': 100,
        'permissions': ['*'],
        'description': 'Full access — billing, team management, all features',
    },
    'admin': {
        'level': 80,
        'permissions': ['*'],
        'description': 'Full access except billing and owner transfer',
    },
    'manager': {
        'level': 60,
        'permissions': [
            'campaigns.*', 'contacts.*', 'inbox.*', 'smtp.*',
            'analytics.*', 'copilot.*', 'sequences.*', 'automations.*',
        ],
        'description': 'Manage campaigns, contacts, inbox — no team/billing',
    },
    'sdr': {
        'level': 40,
        'permissions': [
            'campaigns.view', 'campaigns.create', 'campaigns.send',
            'contacts.view', 'contacts.enrich',
            'inbox.view', 'inbox.reply', 'inbox.draft',
            'analytics.view', 'copilot.chat', 'copilot.actions',
            'sequences.view', 'sequences.create',
        ],
        'description': 'Send campaigns, manage inbox, use copilot',
    },
    'viewer': {
        'level': 20,
        'permissions': [
            'campaigns.view', 'contacts.view', 'inbox.view',
            'analytics.view', 'copilot.chat',
        ],
        'description': 'View-only access to all data',
    },
}


def check_permission(user_role: str, required_permission: str) -> bool:
    """Check if a role has a specific permission."""
    role_def = ROLES.get(user_role, ROLES['viewer'])
    perms = role_def['permissions']
    if '*' in perms:
        return True
    # Check exact match or wildcard
    for p in perms:
        if p == required_permission:
            return True
        if p.endswith('.*') and required_permission.startswith(p[:-2]):
            return True
    return False


def require_permission(permission: str):
    """Decorator to enforce permission on routes."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            role = getattr(current_user, 'role', 'viewer')
            if not check_permission(role, permission):
                return jsonify({'success': False, 'error': 'Permission denied'}), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ── TEAM MANAGEMENT ───────────────────────────────────────────

def invite_team_member(workspace_id: int, email: str, role: str, invited_by: int) -> dict:
    """Invite a user to the workspace."""
    if role not in ROLES:
        return {'success': False, 'error': f'Invalid role: {role}'}
    conn = get_db()
    # Check if already exists
    existing = conn.execute("SELECT id FROM users WHERE email=? AND workspace_id=?", (email, workspace_id)).fetchone()
    if existing:
        conn.close()
        return {'success': False, 'error': 'User already in workspace'}
    conn.execute("""
        INSERT INTO team_invites (workspace_id, email, role, invited_by, status, created_at)
        VALUES (?,?,?,?,?,?)
    """, (workspace_id, email, role, invited_by, 'pending', datetime.now()))
    conn.commit()
    conn.close()
    log_activity(workspace_id, invited_by, 'team_invite', f'Invited {email} as {role}')
    return {'success': True, 'message': f'Invitation sent to {email}'}


def update_member_role(workspace_id: int, user_id: int, new_role: str, changed_by: int) -> dict:
    """Change a team member's role."""
    if new_role not in ROLES:
        return {'success': False, 'error': f'Invalid role: {new_role}'}
    conn = get_db()
    user = conn.execute("SELECT id, username, role FROM users WHERE id=? AND workspace_id=?", (user_id, workspace_id)).fetchone()
    if not user:
        conn.close()
        return {'success': False, 'error': 'User not found'}
    if user['role'] == 'owner' and new_role != 'owner':
        conn.close()
        return {'success': False, 'error': 'Cannot demote owner'}
    conn.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
    conn.commit()
    conn.close()
    log_activity(workspace_id, changed_by, 'role_change', f'{user["username"]} → {new_role}')
    return {'success': True, 'message': f'Role updated to {new_role}'}


def remove_member(workspace_id: int, user_id: int, removed_by: int) -> dict:
    """Remove a team member from workspace."""
    conn = get_db()
    user = conn.execute("SELECT id, username, role FROM users WHERE id=? AND workspace_id=?", (user_id, workspace_id)).fetchone()
    if not user:
        conn.close()
        return {'success': False, 'error': 'User not found'}
    if user['role'] == 'owner':
        conn.close()
        return {'success': False, 'error': 'Cannot remove owner'}
    conn.execute("UPDATE users SET workspace_id=NULL, active=0 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    log_activity(workspace_id, removed_by, 'member_removed', f'Removed {user["username"]}')
    return {'success': True}


def get_team_members(workspace_id: int) -> list:
    """Get all team members."""
    conn = get_db()
    members = conn.execute("""
        SELECT id, username, email, role, last_login, created_at
        FROM users WHERE workspace_id=? AND active=1 ORDER BY role, username
    """, (workspace_id,)).fetchall()
    conn.close()
    return [dict(m) for m in members]


# ── ACTIVITY LOGGING ──────────────────────────────────────────

def log_activity(workspace_id: int, user_id: int, action: str, details: str = ''):
    """Log a team activity event."""
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO activity_log (workspace_id, user_id, action, details, created_at)
            VALUES (?,?,?,?,?)
        """, (workspace_id, user_id, action, details[:500], datetime.now()))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_activity_log(workspace_id: int, limit: int = 50) -> list:
    """Get recent activity log."""
    conn = get_db()
    rows = conn.execute("""
        SELECT a.*, u.username FROM activity_log a
        LEFT JOIN users u ON a.user_id = u.id
        WHERE a.workspace_id=? ORDER BY a.created_at DESC LIMIT ?
    """, (workspace_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_roles_list() -> list:
    """Get all available roles with descriptions."""
    return [{'name': k, **v} for k, v in ROLES.items()]
