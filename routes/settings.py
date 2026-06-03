"""
routes/settings.py - Settings & SMTP Account Routes
=====================================================
App settings, SMTP accounts CRUD, diagnostics, Groq usage.
"""
import os
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

settings_bp = Blueprint("settings_routes", __name__)


@settings_bp.route('/api/smtp_test')
@login_required
def api_smtp_test():
    """Test SMTP connection — tries rotation account first, then fallback settings."""
    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account
    wid = get_wid()
    tracking_host = get_setting('tracking_host')

    # Try rotation account first
    account = get_next_smtp_account(workspace_id=wid)
    if account:
        smtp_server = account['smtp_server']
        smtp_port = str(account['smtp_port'])
        smtp_login = account['login_username'] or account['email']
        smtp_password = account['password']
        from_email = account['from_email'] or account['email']
    else:
        smtp_server = get_setting('smtp_server')
        smtp_port = get_setting('smtp_port')
        smtp_login = get_setting('smtp_username')
        smtp_password = get_setting('smtp_password')
        from_email = get_setting('from_email') or smtp_login

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
    """Check Celery task status."""
    if not CELERY_AVAILABLE:
        return jsonify({'status': 'unavailable', 'message': 'Celery not configured'})
    try:
        from celery.result import AsyncResult
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
    """Check Celery + Redis health."""
    return jsonify({
        'celery_available': CELERY_AVAILABLE,
        'redis_url': os.getenv('REDIS_URL', 'redis://localhost:6379/0').replace(':' + os.getenv('REDIS_URL', '').split(':')[-1] if '@' not in os.getenv('REDIS_URL', '') else '', '***'),
        'mode': 'async' if CELERY_AVAILABLE else 'threading_fallback'
    })


@settings_bp.route('/api/diagnostics')
@login_required
def api_diagnostics():
    """System diagnostics — tracking, IMAP, queue status."""
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()

    # IMAP status
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

    # Tracking stats
    total_sent    = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    with_tracking = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent' AND tracking_id IS NOT NULL AND tracking_id != ''").fetchone()[0]
    total_opens   = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1").fetchone()[0]
    total_replies = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE replied=1").fetchone()[0]
    total_clicks  = conn.execute("SELECT COUNT(DISTINCT contact_id) FROM email_clicks WHERE contact_id IS NOT NULL").fetchone()[0]
    te_opens      = conn.execute("SELECT COUNT(*) FROM tracking_events WHERE event_type='email_open'").fetchone()[0]
    te_clicks     = conn.execute("SELECT COUNT(*) FROM tracking_events WHERE event_type='link_click'").fetchone()[0]

    # Recent logs
    recent_logs = conn.execute("""
        SELECT level, message, created_at FROM campaign_logs
        ORDER BY created_at DESC LIMIT 10
    """).fetchall() if _table_exists(conn, 'campaign_logs') else []

    conn.close()

    return jsonify({
        'imap': {
            'configured': bool(imap_server and imap_username),
            'connected': imap_ok,
            'message': imap_msg,
            'server': imap_server or 'Not set',
            'username': imap_username or 'Not set',
            'reply_to': reply_to or 'Not set',
        },
        'tracking': {
            'host': tracking_host or 'Not set',
            'host_ok': bool(tracking_host and 'localhost' not in tracking_host),
            'total_sent': total_sent,
            'with_tracking_id': with_tracking,
            'opens': total_opens,
            'replies': total_replies,
            'clicks': total_clicks,
            'tracking_events_opens': te_opens,
            'tracking_events_clicks': te_clicks,
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
    """One-time fix: set tracking_host to production URL"""
    set_setting('tracking_host', 'https://ertyui.online')
    return jsonify({'success': True, 'tracking_host': 'https://ertyui.online'})









































# ==============================
@settings_bp.route('/api/smtp_accounts', methods=['GET'])
@login_required
def api_get_smtp_accounts():
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
        # Mask password but show if it's set
        row['password'] = ('***' + row['password'][-4:]) if row.get('password') and len(row['password']) > 4 else '(empty)'
        result.append(row)
    return jsonify({'accounts': result})


@settings_bp.route('/api/smtp_accounts/add', methods=['POST'])
@login_required
def api_add_smtp_account():
    data = request.json
    email       = data.get('email', '').strip().lower()
    password    = data.get('password', '').strip()
    smtp_server = data.get('smtp_server', 'smtp.hostinger.com').strip()
    smtp_port   = int(data.get('smtp_port', 587))
    from_name   = data.get('from_name', '').strip()
    daily_limit = int(data.get('daily_limit', 50))
    reply_to    = data.get('reply_to', '').strip()
    bcc_emails  = data.get('bcc_emails', '').strip()
    signature   = data.get('signature', '').strip()
    if not email or not password:
        return jsonify({'success': False, 'error': 'Email and password required'})
    # Validate
    if '@' not in email:
        return jsonify({'success': False, 'error': 'Invalid email format'})
    if not str(smtp_port).isdigit() or int(smtp_port) <= 0:
        return jsonify({'success': False, 'error': 'SMTP port must be a positive number'})
    if daily_limit <= 0:
        return jsonify({'success': False, 'error': 'Daily limit must be > 0'})
    try:
        conn = get_db()
        from services.workspace_service import get_wid
        wid = get_wid()
        login_username = data.get('login_username', '').strip()
        # Check duplicate before insert
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
        inserted = 1  # INSERT OR IGNORE handles duplicates
        conn.commit()
        conn.close()
        if inserted == 0:
            return jsonify({'success': False, 'error': 'An inbox with this email already exists'})
        app_logger.info(f'SMTP account added: {email}')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})


@settings_bp.route('/api/smtp_accounts/<int:account_id>/update', methods=['POST'])
@login_required
def api_update_smtp_account(account_id):
    """Update full sender identity for an existing SMTP account."""
    from utils.ownership import owns_smtp_account
    if not owns_smtp_account(account_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    data = request.json or {}
    conn = get_db()
    acc = conn.execute('SELECT id FROM smtp_accounts WHERE id=?', (account_id,)).fetchone()
    if not acc:
        conn.close()
        return jsonify({'success': False, 'error': 'Not found'})
    fields = []
    params = []
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
    reset_daily_counts()
    return jsonify({'success': True, 'message': 'Daily counts reset'})





@settings_bp.route('/api/settings/save', methods=['POST'])
@login_required
def api_save_setting():
    """Save one or more settings without wiping unrelated keys.
    Skips empty values for password/credential fields to prevent accidental wipe.
    """
    from services.workspace_service import get_wid
    data = request.json or {}
    wid = get_wid()
    # Fields that should never be overwritten with empty string
    PROTECTED_FIELDS = {'imap_password', 'smtp_password', 'groq_api_keys',
                        'gemini_api_key', 'imap_username', 'imap_server'}
    # AI keys are admin-only
    ADMIN_ONLY_KEYS = {'groq_api_keys', 'gemini_api_key'}
    conn = get_db()
    saved = []
    for key, val in data.items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key in ADMIN_ONLY_KEYS and getattr(current_user, 'role', '') != 'admin':
            continue
        # Skip empty values for protected fields
        if key in PROTECTED_FIELDS and not str(val).strip():
            continue
        existing = conn.execute(
            "SELECT key FROM settings WHERE key=? AND workspace_id=?", (key, wid)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE settings SET value=? WHERE key=? AND workspace_id=?",
                (val, key, wid)
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, workspace_id) VALUES (?,?,?)",
                (key, val, wid)
            )
        saved.append(key)
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'saved': saved})


@settings_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_page():
    from services.workspace_service import get_wid
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
        return redirect(url_for('settings_page'))
    current = {}
    for key in DEFAULT_SETTINGS.keys():
        current[key] = get_setting(key)
    # Hide AI keys from non-admin users
    if getattr(current_user, 'role', '') != 'admin':
        current['groq_api_keys'] = ''
        current['gemini_api_key'] = ''
    return render_template('settings.html', settings=current)


import requests as http_requests
from services.smtp_rotation import get_next_smtp_account, mark_send_success, mark_send_failure, reset_daily_counts, check_warmup_upgrade

# ==============================
# CELERY INTEGRATION (graceful fallback)
# ==============================
try:
    from celery_app import celery, is_redis_available, has_active_workers
    CELERY_AVAILABLE = is_redis_available()
    if CELERY_AVAILABLE:
        print('[CELERY] Redis connected — async task queue active')
    else:
        print('[CELERY] Redis not available — using threading fallback')
except Exception as _ce:
    CELERY_AVAILABLE = False
    print(f'[CELERY] Not configured ({_ce}) — using threading fallback')
    def has_active_workers(): return False


def queue_send_campaign(campaign_id, contact_ids, subject_template, body_template):
    """Route campaign send to Celery or threading fallback."""
    if CELERY_AVAILABLE and has_active_workers():
        from tasks.email_tasks import send_campaign_async
        result = send_campaign_async.apply_async(
            args=[campaign_id, contact_ids, subject_template, body_template],
            queue='email'
        )
        app_logger.info(f'[CELERY] Campaign {campaign_id} queued | task_id={result.id}')
        return result.id
    return None  # Caller handles threading fallback


def queue_send_campaign_ai(campaign_id, contact_ids, subject_template):
    """Route AI campaign send to Celery or threading fallback."""
    if CELERY_AVAILABLE and has_active_workers():
        from tasks.email_tasks import send_campaign_ai_async
        result = send_campaign_ai_async.apply_async(
            args=[campaign_id, contact_ids, subject_template],
            queue='email'
        )
        app_logger.info(f'[CELERY] AI Campaign {campaign_id} queued | task_id={result.id}')
        return result.id
    return None


def queue_enrich_all(force=False):
    """Route enrichment to Celery or threading fallback."""
    if CELERY_AVAILABLE and has_active_workers():
        from tasks.ai_tasks import enrich_all_contacts
        result = enrich_all_contacts.apply_async(args=[force], queue='ai')
        app_logger.info(f'[CELERY] Enrich all queued | task_id={result.id}')
        return result.id
    return None


def queue_check_replies():
    """Route IMAP check to Celery or direct call."""
    if CELERY_AVAILABLE and has_active_workers():
        from tasks.inbox_tasks import check_replies_task
        result = check_replies_task.apply_async(queue='inbox')
        return result.id
    return None


def queue_verify_all(reverify=False):
    """Route email verification to Celery or threading fallback."""
    if CELERY_AVAILABLE and has_active_workers():
        from tasks.verification_tasks import verify_all_contacts
        result = verify_all_contacts.apply_async(args=[reverify], queue='default')
        app_logger.info(f'[CELERY] Verify all queued | task_id={result.id}')
        return result.id
    return None

# Groq key rotation
groq_key_index = 0


def call_ollama(prompt):
    """Ollama disabled in production (no GPU on Azure). Returns None."""
    app_logger.info('Ollama skipped — disabled in production')
    return None, 'Ollama disabled in production'


# Store latest rate limit info per Groq key
groq_rate_limits = {}

def call_groq(prompt, max_retries=2):
    global groq_key_index, groq_rate_limits
    keys_str = get_setting('groq_api_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    if not keys:
        return None, 'No Groq keys configured'

    for i in range(len(keys)):
        key = keys[(groq_key_index + i) % len(keys)]
        for attempt in range(max_retries):
            try:
                r = http_requests.post('https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                    json={'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 1000},
                    timeout=45)
                groq_rate_limits[key[-8:]] = {
                    'limit_requests': r.headers.get('x-ratelimit-limit-requests', '?'),
                    'remaining_requests': r.headers.get('x-ratelimit-remaining-requests', '?'),
                    'limit_tokens': r.headers.get('x-ratelimit-limit-tokens', '?'),
                    'remaining_tokens': r.headers.get('x-ratelimit-remaining-tokens', '?'),
                    'reset_requests': r.headers.get('x-ratelimit-reset-requests', ''),
                    'reset_tokens': r.headers.get('x-ratelimit-reset-tokens', ''),
                    'last_checked': datetime.now().strftime('%H:%M:%S'),
                }
                if r.status_code == 200:
                    groq_key_index = (groq_key_index + i + 1) % len(keys)
                    return r.json()['choices'][0]['message']['content'].strip(), None
                elif r.status_code == 429:
                    app_logger.warning(f'Groq rate limited key ...{key[-8:]}, trying next')
                    break  # Try next key
                elif r.status_code >= 500:
                    app_logger.warning(f'Groq server error {r.status_code}, retry {attempt+1}')
                    time.sleep(2)
                    continue  # Retry same key
                else:
                    error_logger.error(f'Groq unexpected {r.status_code}: {r.text[:200]}')
                    break
            except http_requests.exceptions.Timeout:
                app_logger.warning(f'Groq timeout attempt {attempt+1} key ...{key[-8:]}')
                time.sleep(1)
                continue
            except Exception as e:
                error_logger.error(f'Groq exception: {str(e)}')
                break
    return None, 'All Groq keys exhausted'


def call_gemini(prompt, max_retries=2):
    api_key = get_setting('gemini_api_key')
    if not api_key:
        return None, 'No Gemini key configured'
    for attempt in range(max_retries):
        try:
            r = http_requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}',
                json={'contents': [{'parts': [{'text': prompt}]}]}, timeout=45)
            if r.status_code == 200:
                return r.json()['candidates'][0]['content']['parts'][0]['text'].strip(), None
            elif r.status_code >= 500:
                app_logger.warning(f'Gemini server error {r.status_code}, retry {attempt+1}')
                time.sleep(2)
                continue
            else:
                app_logger.warning(f'Gemini returned {r.status_code}')
                return None, f'Gemini {r.status_code}'
        except http_requests.exceptions.Timeout:
            app_logger.warning(f'Gemini timeout attempt {attempt+1}')
            time.sleep(1)
            continue
        except Exception as e:
            error_logger.error(f'Gemini error: {str(e)}')
            return None, f'Gemini error: {str(e)[:50]}'
    return None, 'Gemini failed after retries'


def generate_ai_email(name, company, prompt_template, context='', designation=''):
    prompt = prompt_template.replace('{name}', name or '').replace('{company}', company or '').replace('{designation}', designation or 'founder/executive')
    if context:
        prompt = f"""CONTEXT ABOUT {company} (USE THIS to personalize the email):
{context}

USE the above context to write a SPECIFIC opening line. Do NOT write generic emails.

""" + prompt
    
    priority = (get_setting('ai_priority') or 'ollama,groq,gemini').split(',')
    
    for provider in priority:
        provider = provider.strip().lower()
        if provider == 'ollama':
            body, err = call_ollama(prompt)
        elif provider == 'groq':
            body, err = call_groq(prompt)
        elif provider == 'gemini':
            body, err = call_gemini(prompt)
        else:
            continue
        
        # Track usage
        try:
            conn = get_db()
            conn.execute("INSERT INTO ai_usage (provider, purpose, success) VALUES (?,?,?)",
                (provider, 'email', 1 if body else 0))
            conn.commit()
            conn.close()
        except: pass
        
        if body:
            return body, None
        print(f'  [{provider}] failed: {err}')
    
    return None, 'All AI providers failed'


@settings_bp.route('/api/groq_usage')
@login_required
def api_groq_usage():
    """Check Groq rate limits for all keys"""
    keys_str = get_setting('groq_api_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    if not keys:
        return jsonify({'keys': [], 'error': 'No Groq keys configured'})

    results = []
    for idx, key in enumerate(keys):
        key_short = key[-8:]
        # If we have cached info, use it; otherwise do a lightweight call
        if key_short in groq_rate_limits:
            info = groq_rate_limits[key_short]
        else:
            # Make a minimal request to get headers
            try:
                r = http_requests.post('https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                    json={'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 1},
                    timeout=10)
                info = {
                    'limit_requests': r.headers.get('x-ratelimit-limit-requests', '?'),
                    'remaining_requests': r.headers.get('x-ratelimit-remaining-requests', '?'),
                    'limit_tokens': r.headers.get('x-ratelimit-limit-tokens', '?'),
                    'remaining_tokens': r.headers.get('x-ratelimit-remaining-tokens', '?'),
                    'reset_requests': r.headers.get('x-ratelimit-reset-requests', ''),
                    'reset_tokens': r.headers.get('x-ratelimit-reset-tokens', ''),
                    'last_checked': datetime.now().strftime('%H:%M:%S'),
                }
                groq_rate_limits[key_short] = info
                if r.status_code == 401:
                    info = {'error': 'Invalid key'}
            except Exception as e:
                info = {'error': str(e)[:50]}

        # Get usage from ai_usage table
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














