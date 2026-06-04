"""
routes/settings.py - Settings & SMTP Account Routes
=====================================================
App settings, SMTP accounts CRUD, diagnostics, Groq usage.
"""
import os
import smtplib
import time
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from utils.db import get_db

settings_bp = Blueprint("settings_routes", __name__)


def _app():
    """Lazy-load app globals to avoid circular imports."""
    from app import (get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH,
                     error_logger, app_logger, CELERY_AVAILABLE, imap_checker_running,
                     _table_exists, reset_daily_counts)
    return (get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH,
            error_logger, app_logger, CELERY_AVAILABLE, imap_checker_running,
            _table_exists, reset_daily_counts)


@settings_bp.route('/api/smtp_test')
@login_required
def api_smtp_test():
    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account
    get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH, error_logger, app_logger, *_ = _app()
    wid = get_wid()
    tracking_host = get_setting('tracking_host')

    account = get_next_smtp_account(workspace_id=wid)
    if account:
        smtp_server  = account['smtp_server']
        smtp_port    = str(account['smtp_port'])
        smtp_login   = account['login_username'] or account['email']
        smtp_password = account['password']
        from_email   = account['from_email'] or account['email']
    else:
        smtp_server  = get_setting('smtp_server')
        smtp_port    = get_setting('smtp_port')
        smtp_login   = get_setting('smtp_username')
        smtp_password = get_setting('smtp_password')
        from_email   = get_setting('from_email') or smtp_login

    result = {
        'smtp_server': smtp_server or 'NOT SET',
        'smtp_port': smtp_port or 'NOT SET',
        'smtp_username': smtp_login or 'NOT SET',
        'smtp_password_set': bool(smtp_password),
        'from_email': from_email or 'NOT SET',
        'tracking_host': tracking_host or 'NOT SET',
        'db_path': DB_PATH,
        'connection_test': None
    }

    if not all([smtp_server, smtp_port, smtp_login, smtp_password]):
        result['connection_test'] = 'FAILED - Missing SMTP settings'
        return jsonify(result)

    try:
        server = smtplib.SMTP(smtp_server, int(smtp_port), timeout=10)
        server.starttls()
        server.login(smtp_login, smtp_password)
        server.quit()
        result['connection_test'] = 'SUCCESS - Connected and authenticated'
    except Exception as e:
        result['connection_test'] = f'FAILED - {str(e)[:200]}'
        error_logger.error(f'SMTP test failed: {str(e)}')

    return jsonify(result)


@settings_bp.route('/api/task_status/<task_id>')
@login_required
def api_task_status(task_id):
    get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH, error_logger, app_logger, CELERY_AVAILABLE, *_ = _app()
    if not CELERY_AVAILABLE:
        return jsonify({'status': 'unavailable', 'message': 'Celery not configured'})
    try:
        from celery.result import AsyncResult
        from celery_app import celery_app as celery
        result = AsyncResult(task_id, app=celery)
        return jsonify({
            'task_id': task_id,
            'status': result.status,
            'result': result.result if result.ready() and not isinstance(result.result, Exception) else None,
            'error': str(result.result) if result.failed() else None
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})


@settings_bp.route('/api/celery_status')
@login_required
def api_celery_status():
    get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH, error_logger, app_logger, CELERY_AVAILABLE, *_ = _app()
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    return jsonify({
        'celery_available': CELERY_AVAILABLE,
        'redis_url': redis_url[:20] + '***' if len(redis_url) > 20 else redis_url,
        'mode': 'async' if CELERY_AVAILABLE else 'threading_fallback'
    })


@settings_bp.route('/api/diagnostics')
@login_required
def api_diagnostics():
    from services.workspace_service import get_wid
    get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH, error_logger, app_logger, CELERY_AVAILABLE, imap_checker_running, _table_exists, *_ = _app()
    wid = get_wid()
    conn = get_db()

    imap_server   = get_setting('imap_server')
    imap_username = get_setting('imap_username')
    imap_password = get_setting('imap_password')
    tracking_host = get_setting('tracking_host')
    reply_to      = get_setting('reply_to')

    imap_ok = False
    imap_msg = 'Not configured'
    if imap_server and imap_username and imap_password:
        try:
            import imaplib
            mail = imaplib.IMAP4_SSL(imap_server.strip(), int(get_setting('imap_port') or 993))
            mail.login(imap_username.strip(), imap_password.strip())
            mail.select('INBOX')
            _, unseen = mail.search(None, 'UNSEEN')
            unseen_count = len(unseen[0].split()) if unseen[0] else 0
            mail.logout()
            imap_ok = True
            imap_msg = f'Connected — {unseen_count} unseen emails'
        except Exception as e:
            imap_msg = f'Failed: {str(e)[:100]}'

    total_sent    = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    with_tracking = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent' AND tracking_id IS NOT NULL AND tracking_id != ''").fetchone()[0]
    total_opens   = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1").fetchone()[0]
    total_replies = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE replied=1").fetchone()[0]
    total_clicks  = conn.execute("SELECT COUNT(DISTINCT contact_id) FROM email_clicks WHERE contact_id IS NOT NULL").fetchone()[0]
    te_opens = te_clicks = 0
    try:
        te_opens  = conn.execute("SELECT COUNT(*) FROM tracking_events WHERE event_type='email_open'").fetchone()[0]
        te_clicks = conn.execute("SELECT COUNT(*) FROM tracking_events WHERE event_type='link_click'").fetchone()[0]
    except Exception:
        pass

    recent_logs = []
    try:
        if _table_exists(conn, 'campaign_logs'):
            recent_logs = conn.execute(
                "SELECT level, message, created_at FROM campaign_logs ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
    except Exception:
        pass
    conn.close()

    return jsonify({
        'imap': {
            'configured': bool(imap_server and imap_username),
            'connected': imap_ok, 'message': imap_msg,
            'server': imap_server or 'Not set',
            'username': imap_username or 'Not set',
            'reply_to': reply_to or 'Not set',
        },
        'tracking': {
            'host': tracking_host or 'Not set',
            'host_ok': bool(tracking_host and 'localhost' not in tracking_host),
            'total_sent': total_sent, 'with_tracking_id': with_tracking,
            'opens': total_opens, 'replies': total_replies, 'clicks': total_clicks,
            'tracking_events_opens': te_opens, 'tracking_events_clicks': te_clicks,
        },
        'workers': {
            'celery_available': CELERY_AVAILABLE,
            'mode': 'celery' if CELERY_AVAILABLE else 'threading',
            'imap_checker_running': imap_checker_running,
        },
        'recent_logs': [dict(l) for l in recent_logs],
    })


@settings_bp.route('/api/fix_tracking_host')
@login_required
def fix_tracking_host():
    get_setting, set_setting, *_ = _app()
    set_setting('tracking_host', 'https://ertyui.online')
    return jsonify({'success': True, 'tracking_host': 'https://ertyui.online'})


# ── SMTP ACCOUNTS ────────────────────────────────────────────────

@settings_bp.route('/api/smtp_accounts', methods=['GET'])
@login_required
def api_get_smtp_accounts():
    get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH, error_logger, app_logger, *_ = _app()
    conn = get_db()
    accounts = conn.execute("SELECT * FROM smtp_accounts ORDER BY active DESC, health_score DESC").fetchall()
    conn.close()
    result = []
    for a in accounts:
        row = dict(a)
        row.setdefault('reply_to', '')
        row.setdefault('bcc_emails', '')
        row.setdefault('signature', '')
        row.setdefault('login_username', '')
        row['password'] = ('***' + row['password'][-4:]) if row.get('password') and len(row['password']) > 4 else '(empty)'
        result.append(row)
    return jsonify({'accounts': result})


@settings_bp.route('/api/smtp_accounts/add', methods=['POST'])
@login_required
def api_add_smtp_account():
    get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH, error_logger, app_logger, *_ = _app()
    data = request.json
    email        = data.get('email', '').strip().lower()
    password     = data.get('password', '').strip()
    smtp_server  = data.get('smtp_server', 'smtp.hostinger.com').strip()
    smtp_port    = int(data.get('smtp_port', 587))
    from_name    = data.get('from_name', '').strip()
    daily_limit  = int(data.get('daily_limit', 50))
    reply_to     = data.get('reply_to', '').strip()
    bcc_emails   = data.get('bcc_emails', '').strip()
    signature    = data.get('signature', '').strip()
    login_username = data.get('login_username', '').strip()

    if not email or not password:
        return jsonify({'success': False, 'error': 'Email and password required'})
    if '@' not in email:
        return jsonify({'success': False, 'error': 'Invalid email format'})
    if daily_limit <= 0:
        return jsonify({'success': False, 'error': 'Daily limit must be > 0'})

    try:
        conn = get_db()
        from services.workspace_service import get_wid
        wid = get_wid()
        if conn.execute("SELECT id FROM smtp_accounts WHERE email=?", (email,)).fetchone():
            conn.close()
            return jsonify({'success': False, 'error': 'An inbox with this email already exists'})
        conn.execute("""
            INSERT OR IGNORE INTO smtp_accounts
              (email, password, smtp_server, smtp_port, from_name,
               daily_limit, reply_to, bcc_emails, signature, login_username, workspace_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (email, password, smtp_server, smtp_port, from_name,
              daily_limit, reply_to, bcc_emails, signature, login_username, wid))
        conn.commit()
        conn.close()
        app_logger.info(f'SMTP account added: {email}')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})


@settings_bp.route('/api/smtp_accounts/<int:account_id>/update', methods=['POST'])
@login_required
def api_update_smtp_account(account_id):
    from utils.ownership import owns_smtp_account
    if not owns_smtp_account(account_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    data = request.json or {}
    conn = get_db()
    fields, params = [], []
    for col in ('email', 'from_name', 'reply_to', 'bcc_emails', 'signature', 'daily_limit', 'smtp_server', 'smtp_port', 'login_username'):
        if col in data:
            fields.append(f'{col}=?')
            params.append(data[col])
    if 'password' in data and data['password'].strip():
        fields.append('password=?')
        params.append(data['password'].strip())
    if not fields:
        conn.close()
        return jsonify({'success': False, 'error': 'No fields to update'})
    params.append(account_id)
    conn.execute(f"UPDATE smtp_accounts SET {', '.join(fields)} WHERE id=?", params)
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@settings_bp.route('/api/smtp_accounts/<int:account_id>/toggle', methods=['POST'])
@login_required
def api_toggle_smtp_account(account_id):
    from utils.ownership import owns_smtp_account
    acc = owns_smtp_account(account_id)
    if not acc:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    conn = get_db()
    new_status = 0 if acc['active'] else 1
    conn.execute("UPDATE smtp_accounts SET active=? WHERE id=?", (new_status, account_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'active': new_status})


@settings_bp.route('/api/smtp_accounts/<int:account_id>/delete', methods=['DELETE'])
@login_required
def api_delete_smtp_account(account_id):
    from utils.ownership import owns_smtp_account
    if not owns_smtp_account(account_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    conn = get_db()
    conn.execute("DELETE FROM smtp_accounts WHERE id=?", (account_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@settings_bp.route('/api/smtp_accounts/reset_today', methods=['POST'])
@login_required
def api_reset_smtp_today():
    get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH, error_logger, app_logger, CELERY_AVAILABLE, imap_checker_running, _table_exists, reset_daily_counts = _app()
    reset_daily_counts()
    return jsonify({'success': True, 'message': 'Daily counts reset'})


@settings_bp.route('/api/settings/save', methods=['POST'])
@login_required
def api_save_setting():
    from services.workspace_service import get_wid
    get_setting, set_setting, DEFAULT_SETTINGS, *_ = _app()
    data = request.json or {}
    wid = get_wid()
    PROTECTED_FIELDS = {'imap_password', 'smtp_password', 'groq_api_keys',
                        'gemini_api_key', 'imap_username', 'imap_server'}
    ADMIN_ONLY_KEYS = {'groq_api_keys', 'gemini_api_key'}
    conn = get_db()
    saved = []
    for key, val in data.items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key in ADMIN_ONLY_KEYS and getattr(current_user, 'role', '') != 'admin':
            continue
        if key in PROTECTED_FIELDS and not str(val).strip():
            continue
        existing = conn.execute(
            "SELECT key FROM settings WHERE key=? AND workspace_id=?", (key, wid)
        ).fetchone()
        if existing:
            conn.execute("UPDATE settings SET value=? WHERE key=? AND workspace_id=?", (val, key, wid))
        else:
            conn.execute("INSERT OR IGNORE INTO settings (key, value, workspace_id) VALUES (?,?,?)", (key, val, wid))
        saved.append(key)
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'saved': saved})


@settings_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_page():
    from services.workspace_service import get_wid
    get_setting, set_setting, DEFAULT_SETTINGS, *_ = _app()
    wid = get_wid()
    if request.method == 'POST':
        conn = get_db()
        admin_only = {'groq_api_keys', 'gemini_api_key'}
        for key in DEFAULT_SETTINGS.keys():
            if key in admin_only and getattr(current_user, 'role', '') != 'admin':
                continue
            val = request.form.get(key, '')
            existing = conn.execute("SELECT key FROM settings WHERE key=? AND workspace_id=?", (key, wid)).fetchone()
            if existing:
                conn.execute("UPDATE settings SET value=? WHERE key=? AND workspace_id=?", (val, key, wid))
            else:
                conn.execute("INSERT OR IGNORE INTO settings (key, value, workspace_id) VALUES (?,?,?)", (key, val, wid))
        conn.commit()
        conn.close()
        flash('Settings saved!', 'success')
        return redirect(url_for('settings_routes.settings_page'))
    current = {key: get_setting(key) for key in DEFAULT_SETTINGS.keys()}
    if getattr(current_user, 'role', '') != 'admin':
        current['groq_api_keys'] = ''
        current['gemini_api_key'] = ''
    return render_template('settings.html', settings=current)


@settings_bp.route('/api/groq_usage')
@login_required
def api_groq_usage():
    import requests as http_requests
    get_setting, set_setting, DEFAULT_SETTINGS, DB_PATH, error_logger, app_logger, *_ = _app()
    keys_str = get_setting('groq_api_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    if not keys:
        return jsonify({'keys': [], 'error': 'No Groq keys configured'})

    groq_rate_limits = {}
    results = []
    for idx, key in enumerate(keys):
        key_short = key[-8:]
        try:
            r = http_requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 1},
                timeout=10)
            info = {
                'limit_requests': r.headers.get('x-ratelimit-limit-requests', '?'),
                'remaining_requests': r.headers.get('x-ratelimit-remaining-requests', '?'),
                'limit_tokens': r.headers.get('x-ratelimit-limit-tokens', '?'),
                'remaining_tokens': r.headers.get('x-ratelimit-remaining-tokens', '?'),
                'last_checked': datetime.now().strftime('%H:%M:%S'),
            }
            if r.status_code == 401:
                info = {'error': 'Invalid key'}
        except Exception as e:
            info = {'error': str(e)[:50]}

        conn = get_db()
        total_used = conn.execute("SELECT COUNT(*) FROM ai_usage WHERE provider='groq'").fetchone()[0]
        today_used = conn.execute("SELECT COUNT(*) FROM ai_usage WHERE provider='groq' AND DATE(created_at)=CURRENT_DATE").fetchone()[0]
        conn.close()
        results.append({
            'key_index': idx + 1,
            'key_hint': f'...{key_short}',
            'info': info,
            'total_used_db': total_used,
            'today_used_db': today_used,
        })
    return jsonify({'keys': results})
