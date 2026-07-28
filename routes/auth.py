"""
routes/auth.py — Authentication Routes
========================================
Single admin login/logout/change password.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

auth_bp = Blueprint('auth', __name__)


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
            full_name = user_row['full_name'] if 'full_name' in user_row.keys() else ''
            user = User(user_row['id'], user_row['username'], full_name)
            login_user(user, remember=True)
            app_logger.info(f'[AUTH] Login: {username}')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dash.dashboard'))
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
    current_pw  = request.form.get('current_password', '')
    new_pw      = request.form.get('new_password', '')
    confirm_pw  = request.form.get('confirm_password', '')
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
