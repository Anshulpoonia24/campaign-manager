"""
routes/auth.py — Authentication Routes
========================================
Login, register, logout, change password.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Self-serve signup — creates user + workspace automatically."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
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
        return redirect(url_for('dashboard'))
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
            return redirect(next_page or url_for('dashboard'))
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
        return redirect(url_for('settings_page'))
    if new_pw != confirm_pw:
        flash('New passwords do not match!', 'error')
        return redirect(url_for('settings_page'))
    if len(new_pw) < 6:
        flash('Password must be at least 6 characters!', 'error')
        return redirect(url_for('settings_page'))
    from app import get_db
    conn = get_db()
    user_row = conn.execute("SELECT * FROM users WHERE id=?", (current_user.id,)).fetchone()
    if not check_password_hash(user_row['password_hash'], current_pw):
        flash('Current password is wrong!', 'error')
        conn.close()
        return redirect(url_for('settings_page'))
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(new_pw), current_user.id))
    conn.commit()
    conn.close()
    flash('Password changed successfully! 🔒', 'success')
    return redirect(url_for('settings_page'))
