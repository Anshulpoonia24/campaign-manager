"""
routes/admin.py — Tenant Management Admin Panel
================================================
Completely separate from tenant login.
Admin login: /admin/login  (username: admin, password: admin123)
Tenant login: /login
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from functools import wraps
from utils.db import get_db
from werkzeug.security import generate_password_hash
from datetime import datetime
import os

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

ADMIN_SESSION_KEY = 'admin_logged_in'


def admin_required(f):
    """Check admin session — completely separate from Flask-Login."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get(ADMIN_SESSION_KEY):
            from flask import current_app
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated


# ── ADMIN LOGIN ───────────────────────────────────────────────
@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    if session.get(ADMIN_SESSION_KEY):
        return redirect(url_for('admin.admin_dashboard'))
    error = None
    if request.method == 'POST':
        # Basic rate limit: track failed attempts in session
        fail_count = session.get('_admin_fails', 0)
        if fail_count >= 5:
            error = 'Too many failed attempts. Wait and try again.'
            return render_template('admin/login.html', error=error)
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        # Check ONLY against .env — no DB, no tenant access
        admin_user = os.getenv('ADMIN_USERNAME', 'superadmin')
        admin_pass = os.getenv('ADMIN_PASSWORD', 'OutreachOS@2025')
        if username == admin_user and password == admin_pass:
            session[ADMIN_SESSION_KEY] = True
            session['admin_username'] = username
            session.pop('_admin_fails', None)
            return redirect(url_for('admin.admin_dashboard'))
        session['_admin_fails'] = fail_count + 1
        error = 'Invalid admin credentials.'
    return render_template('admin/login.html', error=error)


# ── ADMIN LOGOUT ──────────────────────────────────────────────
@admin_bp.route('/logout')
def admin_logout():
    session.pop(ADMIN_SESSION_KEY, None)
    session.pop('admin_username', None)
    return redirect(url_for('admin.admin_login'))


# ── TENANT LIST ───────────────────────────────────────────────
@admin_bp.route('/')
@admin_required
def admin_dashboard():
    """Platform Super Admin Dashboard — infrastructure overview."""
    conn = get_db()
    stats = {
        'total_workspaces': conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0],
        'total_users': conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        'total_contacts': conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
        'total_sent': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0],
        'total_failed': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('failed','bounced')").fetchone()[0],
        'total_campaigns': conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],
        'active_campaigns': conn.execute("SELECT COUNT(*) FROM campaigns WHERE job_status IN ('running','queued')").fetchone()[0],
    }
    # SMTP health across all tenants
    smtp_accounts = conn.execute("SELECT email, health_score, active, sent_today, daily_limit, workspace_id FROM smtp_accounts ORDER BY health_score ASC").fetchall()
    stats['smtp_total'] = len(smtp_accounts)
    stats['smtp_active'] = sum(1 for s in smtp_accounts if s['active'])
    stats['smtp_at_risk'] = sum(1 for s in smtp_accounts if s['health_score'] < 50 and s['active'])
    stats['avg_health'] = round(sum(s['health_score'] for s in smtp_accounts) / len(smtp_accounts), 0) if smtp_accounts else 0

    # Global bounce rate
    total_all = stats['total_sent'] + stats['total_failed']
    stats['bounce_rate'] = round(stats['total_failed'] / total_all * 100, 1) if total_all else 0

    # Recent failed jobs
    failed_campaigns = conn.execute("""
        SELECT c.id, c.name, c.job_status, c.failed_count, w.name as workspace_name
        FROM campaigns c LEFT JOIN workspaces w ON c.workspace_id = w.id
        WHERE c.job_status IN ('failed','cancelled') OR c.failed_count > 5
        ORDER BY c.started_at DESC LIMIT 10
    """).fetchall()

    # Top workspaces by volume
    top_workspaces = conn.execute("""
        SELECT w.id, w.name, w.plan,
            COUNT(DISTINCT es.id) as send_count,
            COUNT(DISTINCT c.id) as contact_count
        FROM workspaces w
        LEFT JOIN emails_sent es ON es.workspace_id = w.id
        LEFT JOIN contacts c ON c.workspace_id = w.id
        GROUP BY w.id ORDER BY send_count DESC LIMIT 10
    """).fetchall()

    # System logs (last 20)
    try:
        sys_logs = conn.execute("""
            SELECT level, message, created_at FROM campaign_logs
            ORDER BY created_at DESC LIMIT 20
        """).fetchall()
    except Exception:
        sys_logs = []

    conn.close()

    # Redis/Celery status
    infra = {'redis': False, 'celery_workers': 0}
    try:
        from celery_app import is_redis_available, has_active_workers
        infra['redis'] = is_redis_available()
        infra['celery_workers'] = 1 if has_active_workers() else 0
    except Exception:
        pass

    return render_template('admin/dashboard.html',
        stats=stats, smtp_accounts=smtp_accounts,
        failed_campaigns=failed_campaigns, top_workspaces=top_workspaces,
        sys_logs=sys_logs, infra=infra)


@admin_bp.route('/workspaces')
@admin_required
def tenant_list():
    conn = get_db()
    workspaces = conn.execute("""
        SELECT w.*,
            COUNT(DISTINCT u.id)  as user_count,
            COUNT(DISTINCT c.id)  as contact_count,
            COUNT(DISTINCT ca.id) as campaign_count,
            COUNT(DISTINCT s.id)  as smtp_count
        FROM workspaces w
        LEFT JOIN users u         ON u.workspace_id  = w.id
        LEFT JOIN contacts c      ON c.workspace_id  = w.id
        LEFT JOIN campaigns ca    ON ca.workspace_id = w.id
        LEFT JOIN smtp_accounts s ON s.workspace_id  = w.id
        GROUP BY w.id
        ORDER BY w.created_at DESC
    """).fetchall()
    conn.close()
    return render_template('admin/tenants.html', workspaces=workspaces)


# ── CREATE TENANT ─────────────────────────────────────────────
@admin_bp.route('/create', methods=['GET', 'POST'])
@admin_required
def create_tenant():
    if request.method == 'POST':
        workspace_name = request.form.get('workspace_name', '').strip()
        username       = request.form.get('username', '').strip()
        password       = request.form.get('password', '').strip()
        plan           = request.form.get('plan', 'free')

        if not workspace_name or not username or not password:
            flash('All fields required.', 'error')
            return render_template('admin/create_tenant.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('admin/create_tenant.html')

        conn = get_db()

        # Check username unique
        if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            conn.close()
            flash(f'Username "{username}" already exists.', 'error')
            return render_template('admin/create_tenant.html')

        # Create workspace
        from services.workspace_service import create_workspace
        import importlib, sys
        # Re-import to get fresh module
        wid = create_workspace(workspace_name)

        # Update plan
        conn.execute("UPDATE workspaces SET plan=? WHERE id=?", (plan, wid))

        # Create user
        conn.execute(
            "INSERT INTO users (username, password_hash, role, workspace_id) VALUES (?,?,?,?)",
            (username, generate_password_hash(password), 'admin', wid)
        )

        # Copy default settings
        from app import DEFAULT_SETTINGS
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT INTO settings (key, value, workspace_id) VALUES (?,?,?)", (k, v, wid))

        # Copy default automation rules
        for rule_key, enabled, delay_days, max_followups in [
            ('no_reply_followup', 1, 2, 3),
            ('opened_multiple_times', 1, 1, 2),
            ('interested_pause', 1, 0, 0),
            ('ooo_retry', 1, 7, 1),
            ('bounce_pause', 1, 0, 0),
        ]:
            conn.execute(
                "INSERT INTO automation_settings (rule_key,enabled,delay_days,max_followups,workspace_id) VALUES (?,?,?,?,?)",
                (rule_key, enabled, delay_days, max_followups, wid)
            )

        conn.commit()
        conn.close()

        flash(f'Tenant "{workspace_name}" created. Login: {username} / {password}', 'success')
        return redirect(url_for('admin.tenant_list'))

    return render_template('admin/create_tenant.html')


# ── TENANT DETAIL ─────────────────────────────────────────────
@admin_bp.route('/tenant/<int:wid>')
@admin_required
def tenant_detail(wid):
    conn = get_db()
    workspace = conn.execute("SELECT * FROM workspaces WHERE id=?", (wid,)).fetchone()
    if not workspace:
        conn.close()
        flash('Workspace not found.', 'error')
        return redirect(url_for('admin.tenant_list'))

    users     = conn.execute("SELECT id, username, role, created_at FROM users WHERE workspace_id=?", (wid,)).fetchall()
    campaigns = conn.execute("SELECT id, name, status, created_at FROM campaigns WHERE workspace_id=? ORDER BY created_at DESC LIMIT 10", (wid,)).fetchall()
    smtp_accs = conn.execute("SELECT id, email, health_score, warmup_stage, active, sent_today FROM smtp_accounts WHERE workspace_id=?", (wid,)).fetchall()

    stats = {
        'contacts':  conn.execute("SELECT COUNT(*) FROM contacts  WHERE workspace_id=?", (wid,)).fetchone()[0],
        'campaigns': conn.execute("SELECT COUNT(*) FROM campaigns WHERE workspace_id=?", (wid,)).fetchone()[0],
        'sent':      conn.execute("SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status='sent'", (wid,)).fetchone()[0],
        'threads':   conn.execute("SELECT COUNT(*) FROM threads WHERE workspace_id=?", (wid,)).fetchone()[0],
    }
    conn.close()
    return render_template('admin/tenant_detail.html',
        workspace=workspace, users=users, campaigns=campaigns,
        smtp_accs=smtp_accs, stats=stats)


# ── RESET PASSWORD ────────────────────────────────────────────
@admin_bp.route('/tenant/<int:wid>/reset_password/<int:user_id>', methods=['POST'])
@admin_required
def reset_password(wid, user_id):
    new_pw = request.form.get('new_password', '').strip()
    if not new_pw or len(new_pw) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('admin.tenant_detail', wid=wid))
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=? AND workspace_id=?",
                 (generate_password_hash(new_pw), user_id, wid))
    conn.commit()
    conn.close()
    flash('Password reset successfully.', 'success')
    return redirect(url_for('admin.tenant_detail', wid=wid))


# ── TOGGLE TENANT PLAN ────────────────────────────────────────
@admin_bp.route('/tenant/<int:wid>/plan', methods=['POST'])
@admin_required
def update_plan(wid):
    plan = request.form.get('plan', 'free')
    conn = get_db()
    conn.execute("UPDATE workspaces SET plan=?, updated_at=? WHERE id=?", (plan, datetime.now(), wid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'plan': plan})


# ── DELETE TENANT ─────────────────────────────────────────────
@admin_bp.route('/tenant/<int:wid>/delete', methods=['POST'])
@admin_required
def delete_tenant(wid):
    if wid == 1:
        flash('Cannot delete Default Workspace.', 'error')
        return redirect(url_for('admin.tenant_list'))
    conn = get_db()
    # Cascade delete all tenant data
    for table in ['email_clicks', 'emails_sent', 'messages', 'threads',
                  'follow_ups', 'automation_settings', 'ai_usage',
                  'smtp_accounts', 'campaigns', 'contacts', 'settings', 'users']:
        try:
            conn.execute(f"DELETE FROM {table} WHERE workspace_id=?", (wid,))
        except Exception:
            pass
    conn.execute("DELETE FROM workspaces WHERE id=?", (wid,))
    conn.commit()
    conn.close()
    flash('Tenant deleted.', 'success')
    return redirect(url_for('admin.tenant_list'))


# ── AI CONFIG (Global) ────────────────────────────────────────
@admin_bp.route('/ai-config', methods=['GET', 'POST'])
@admin_required
def ai_config():
    conn = get_db()
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        for key in ('groq_api_keys', 'gemini_api_key', 'ai_priority'):
            val = data.get(key)
            if val is not None:
                existing = conn.execute("SELECT id FROM settings WHERE key=? AND workspace_id=1", (key,)).fetchone()
                if existing:
                    conn.execute("UPDATE settings SET value=? WHERE key=? AND workspace_id=1", (val.strip(), key))
                else:
                    conn.execute("INSERT INTO settings (key, value, workspace_id) VALUES (?,?,1)", (key, val.strip()))
        conn.commit()
        conn.close()
        if request.is_json:
            return jsonify({'success': True})
        flash('AI Config saved.', 'success')
        return redirect(url_for('admin.ai_config'))
    # GET
    settings = {}
    for key in ('groq_api_keys', 'gemini_api_key', 'ai_priority'):
        row = conn.execute("SELECT value FROM settings WHERE key=? AND workspace_id=1", (key,)).fetchone()
        settings[key] = row[0] if row else ''
    conn.close()
    return render_template('admin/ai_config.html', settings=settings)


@admin_bp.route('/ai-config/test', methods=['POST'])
@admin_required
def ai_config_test():
    """Test AI keys from admin panel."""
    import requests as http_requests
    data = request.get_json()
    provider = data.get('provider', 'groq')
    key = data.get('key', '').strip()
    if not key:
        return jsonify({'success': False, 'error': 'No key provided'})
    if provider == 'groq':
        try:
            r = http_requests.post('https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'user', 'content': 'Say OK'}], 'max_tokens': 5},
                timeout=15)
            if r.status_code == 200:
                return jsonify({'success': True, 'message': 'Groq API working ✓'})
            return jsonify({'success': False, 'error': f'Status {r.status_code}: {r.text[:100]}'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)[:100]})
    elif provider == 'gemini':
        try:
            r = http_requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}',
                json={'contents': [{'parts': [{'text': 'Say OK'}]}]}, timeout=15)
            if r.status_code == 200:
                return jsonify({'success': True, 'message': 'Gemini API working ✓'})
            return jsonify({'success': False, 'error': f'Status {r.status_code}: {r.text[:100]}'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)[:100]})
    return jsonify({'success': False, 'error': 'Unknown provider'})


# ── API: TENANT STATS ─────────────────────────────────────────
@admin_bp.route('/api/stats')
@admin_required
def api_stats():
    conn = get_db()
    total_workspaces = conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
    total_users      = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_contacts   = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    total_sent       = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    conn.close()
    return jsonify({
        'workspaces': total_workspaces,
        'users': total_users,
        'contacts': total_contacts,
        'emails_sent': total_sent,
    })
