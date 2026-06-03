"""
routes/auth.py — Authentication Routes
========================================
Login, register, logout, change password.
Google OAuth via Supabase.
"""
import os
import requests as _http
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

auth_bp = Blueprint('auth', __name__)

# ── SUPABASE CONFIG ───────────────────────────────────────────
SUPABASE_URL    = os.getenv('SUPABASE_URL', 'https://ygbwqhxxmfdvrenbpcnw.supabase.co')
SUPABASE_ANON  = os.getenv('SUPABASE_ANON_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlnYndxaHh4bWZkdnJlbmJwY253Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODAzMjU4NTEsImV4cCI6MjA5NTkwMTg1MX0.ZfBEaKuPGSZrz4u0Duo6-rXbUsd3Vc_mgXaJaWAWDz0')


def _get_redirect_url():
    """Build OAuth callback URL — works on localhost and production."""
    tracking_host = os.getenv('TRACKING_HOST', '')
    if tracking_host and 'localhost' not in tracking_host:
        return f'{tracking_host.rstrip("/")}/auth/google/callback'
    return url_for('auth.google_callback', _external=True)


@auth_bp.route('/auth/google')
def google_login():
    """Redirect to Supabase Google OAuth."""
    redirect_url = _get_redirect_url()
    oauth_url = (
        f'{SUPABASE_URL}/auth/v1/authorize'
        f'?provider=google'
        f'&redirect_to={redirect_url}'
    )
    return redirect(oauth_url)


@auth_bp.route('/auth/google/callback')
def google_callback():
    """
    Supabase redirects here after Google auth.
    Supabase sends access_token + refresh_token as URL fragments (#).
    Since fragments don't reach server, we use a JS bridge page.
    """
    # Check if tokens are in query params (Supabase PKCE flow)
    access_token = request.args.get('access_token')
    code = request.args.get('code')

    if code:
        # Exchange code for token via Supabase
        resp = _http.post(
            f'{SUPABASE_URL}/auth/v1/token?grant_type=pkce',
            headers={
                'apikey': SUPABASE_ANON,
                'Content-Type': 'application/json'
            },
            json={'auth_code': code},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            access_token = data.get('access_token')

    if access_token:
        return _login_with_supabase_token(access_token)

    # Fragment-based flow — need JS to extract token from URL hash
    return render_template('auth_callback.html')


@auth_bp.route('/auth/google/token', methods=['POST'])
def google_token():
    """JS bridge posts the access_token from URL fragment."""
    access_token = request.json.get('access_token')
    if not access_token:
        return jsonify({'success': False, 'error': 'No token'}), 400
    result = _login_with_supabase_token(access_token)
    if isinstance(result, str) and 'dashboard' in result:
        return jsonify({'success': True, 'redirect': url_for('dash.dashboard')})
    return jsonify({'success': True, 'redirect': url_for('dash.dashboard')})


def _login_with_supabase_token(access_token: str):
    """Verify Supabase JWT and log user into Flask session."""
    try:
        # Get user info from Supabase
        resp = _http.get(
            f'{SUPABASE_URL}/auth/v1/user',
            headers={
                'apikey': SUPABASE_ANON,
                'Authorization': f'Bearer {access_token}'
            },
            timeout=10
        )
        if resp.status_code != 200:
            flash('Google authentication failed. Please try again.', 'error')
            return redirect(url_for('auth.login'))

        supabase_user = resp.json()
        email    = supabase_user.get('email', '')
        name     = supabase_user.get('user_metadata', {}).get('full_name', '') or \
                   supabase_user.get('user_metadata', {}).get('name', '') or \
                   email.split('@')[0]
        google_id = supabase_user.get('id', '')

        if not email:
            flash('Could not get email from Google. Please try again.', 'error')
            return redirect(url_for('auth.login'))

        # Find or create user in our DB
        from app import get_db, DEFAULT_SETTINGS, app_logger, User
        conn = get_db()

        # Look up by email first
        user_row = conn.execute(
            "SELECT * FROM users WHERE username=? OR username=?",
            (email, email.split('@')[0])
        ).fetchone()

        if not user_row:
            # Auto-create account for Google user
            from services.workspace_service import create_workspace
            wid = create_workspace(f"{name}'s Workspace")
            # Use email as username, random password (can't login with password)
            import secrets
            conn.execute(
                "INSERT INTO users (username, password_hash, role, workspace_id) VALUES (?,?,?,?)",
                (email, generate_password_hash(secrets.token_hex(32)), 'admin', wid)
            )
            conn.commit()
            # Setup default settings
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR IGNORE INTO settings (key, value, workspace_id) VALUES (?,?,?)", (k, v, wid))
            for rule_key, enabled, dd, mf in [
                ('no_reply_followup',1,2,3),('opened_multiple_times',1,1,2),
                ('interested_pause',1,0,0),('ooo_retry',1,7,1),('bounce_pause',1,0,0)
            ]:
                conn.execute(
                    "INSERT OR IGNORE INTO automation_settings (rule_key,enabled,delay_days,max_followups,workspace_id) VALUES (?,?,?,?,?)",
                    (rule_key, enabled, dd, mf, wid)
                )
            conn.commit()
            user_row = conn.execute("SELECT * FROM users WHERE username=?", (email,)).fetchone()
            app_logger.info(f'[AUTH] New user via Google: {email} workspace={wid}')

        conn.close()

        wid  = user_row['workspace_id'] if 'workspace_id' in user_row.keys() else 1
        role = user_row['role'] if 'role' in user_row.keys() else 'admin'
        user = User(user_row['id'], user_row['username'], role, wid)
        login_user(user, remember=True)
        app_logger.info(f'[AUTH] Google login: {email}')
        return redirect(url_for('dash.dashboard'))

    except Exception as e:
        from app import error_logger
        error_logger.error(f'[AUTH] Google callback error: {e}')
        flash('Authentication error. Please try again.', 'error')
        return redirect(url_for('auth.login'))


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Self-serve signup — creates user + workspace automatically."""
    if current_user.is_authenticated:
        return redirect(url_for('dash.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        workspace_n = request.form.get('workspace_name', '').strip() or f"{username}'s Workspace"
        if not username or not password:
            flash('Username and password required.', 'error')
            return render_template('register.html')
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('register.html')
        from app import get_db, DEFAULT_SETTINGS
        conn = get_db()
        if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            conn.close()
            flash('Username already taken.', 'error')
            return render_template('register.html')
        from services.workspace_service import create_workspace
        wid = create_workspace(workspace_n)
        conn.execute(
            "INSERT INTO users (username, password_hash, role, workspace_id) VALUES (?,?,?,?)",
            (username, generate_password_hash(password), 'admin', wid)
        )
        conn.commit()
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value, workspace_id) VALUES (?,?,?)", (k, v, wid))
        for rule_key, enabled, delay_days, max_followups in [
            ('no_reply_followup', 1, 2, 3), ('opened_multiple_times', 1, 1, 2),
            ('interested_pause', 1, 0, 0), ('ooo_retry', 1, 7, 1), ('bounce_pause', 1, 0, 0)
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO automation_settings (rule_key,enabled,delay_days,max_followups,workspace_id) VALUES (?,?,?,?,?)",
                (rule_key, enabled, delay_days, max_followups, wid)
            )
        conn.commit()
        conn.close()
        from app import app_logger
        app_logger.info(f'New user registered: {username} workspace_id={wid}')
        flash('Account created! Please log in.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dash.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        from app import get_db, User, app_logger
        conn = get_db()
        user_row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user_row and check_password_hash(user_row['password_hash'], password):
            wid = user_row['workspace_id'] if 'workspace_id' in user_row.keys() else 1
            role = user_row['role'] if 'role' in user_row.keys() else 'admin'
            user = User(user_row['id'], user_row['username'], role, wid)
            login_user(user, remember=True)
            app_logger.info(f'Login successful: {username}')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dash.dashboard'))
        app_logger.warning(f'Login failed: {username} from {request.remote_addr}')
        flash('Invalid username or password!', 'error')
    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/change_password', methods=['POST'])
@login_required
def change_password():
    current_pw = request.form.get('current_password', '')
    new_pw = request.form.get('new_password', '')
    confirm_pw = request.form.get('confirm_password', '')
    if not current_pw or not new_pw:
        flash('All fields required!', 'error')
        return redirect(url_for('settings_routes.settings_page'))
    if new_pw != confirm_pw:
        flash('New passwords do not match!', 'error')
        return redirect(url_for('settings_routes.settings_page'))
    if len(new_pw) < 6:
        flash('Password must be at least 6 characters!', 'error')
        return redirect(url_for('settings_routes.settings_page'))
    from app import get_db
    conn = get_db()
    user_row = conn.execute("SELECT * FROM users WHERE id=?", (current_user.id,)).fetchone()
    if not check_password_hash(user_row['password_hash'], current_pw):
        flash('Current password is wrong!', 'error')
        conn.close()
        return redirect(url_for('settings_routes.settings_page'))
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(new_pw), current_user.id))
    conn.commit()
    conn.close()
    flash('Password changed successfully!', 'success')
    return redirect(url_for('settings_routes.settings_page'))
