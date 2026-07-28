"""
routes/admin.py — Single Admin Panel
=====================================
Simplified admin panel for single admin application.
No tenant management - all settings are global.
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
        admin_user = os.getenv('ADMIN_USERNAME', 'admin')
        admin_pass = os.getenv('ADMIN_PASSWORD', 'admin123')
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


# ── ADMIN DASHBOARD ───────────────────────────────────────────
@admin_bp.route('/')
@admin_required
def admin_dashboard():
    """Single Admin Dashboard — application overview."""
    conn = get_db()
    stats = {
        'total_contacts': conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
        'total_sent': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0],
        'total_failed': conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('failed','bounced')").fetchone()[0],
        'total_campaigns': conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],
        'active_campaigns': conn.execute("SELECT COUNT(*) FROM campaigns WHERE job_status IN ('running','queued')").fetchone()[0],
    }
    # SMTP health
    smtp_accounts = conn.execute("SELECT email, health_score, active, sent_today, daily_limit FROM smtp_accounts ORDER BY health_score ASC").fetchall()
    stats['smtp_total'] = len(smtp_accounts)
    stats['smtp_active'] = sum(1 for s in smtp_accounts if s['active'])
    stats['smtp_at_risk'] = sum(1 for s in smtp_accounts if s['health_score'] < 50 and s['active'])
    stats['avg_health'] = round(sum(s['health_score'] for s in smtp_accounts) / len(smtp_accounts), 0) if smtp_accounts else 0

    # Global bounce rate
    total_all = stats['total_sent'] + stats['total_failed']
    stats['bounce_rate'] = round(stats['total_failed'] / total_all * 100, 1) if total_all else 0

    # Recent failed jobs
    failed_campaigns = conn.execute("""
        SELECT id, name, job_status, failed_count
        FROM campaigns
        WHERE job_status IN ('failed','cancelled') OR failed_count > 5
        ORDER BY started_at DESC LIMIT 10
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
        from celery_app import is_redis_available
        from app import has_active_workers
        infra['redis'] = is_redis_available()
        infra['celery_workers'] = 1 if has_active_workers() else 0
    except Exception:
        pass

    return render_template('admin/dashboard.html',
        stats=stats, smtp_accounts=smtp_accounts,
        failed_campaigns=failed_campaigns,
        sys_logs=sys_logs, infra=infra)


# ── AI CONFIG (Global) ────────────────────────────────────────
@admin_bp.route('/ai-config', methods=['GET', 'POST'])
@admin_required
def ai_config():
    """Global AI Configuration - single admin only."""
    conn = get_db()
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        # Groq API keys
        groq_keys = data.get('groq_api_keys', '').strip()
        if groq_keys:
            existing = conn.execute("SELECT key FROM settings WHERE key=?", ('groq_api_keys',)).fetchone()
            if existing:
                conn.execute("UPDATE settings SET value=? WHERE key=?", (groq_keys, 'groq_api_keys'))
            else:
                conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", ('groq_api_keys', groq_keys))
        
        # Gemini API key
        gemini_key = data.get('gemini_api_key', '').strip()
        if gemini_key:
            existing = conn.execute("SELECT key FROM settings WHERE key=?", ('gemini_api_key',)).fetchone()
            if existing:
                conn.execute("UPDATE settings SET value=? WHERE key=?", (gemini_key, 'gemini_api_key'))
            else:
                conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", ('gemini_api_key', gemini_key))
        
        # AI priority
        ai_priority = data.get('ai_priority', 'groq,gemini').strip()
        existing = conn.execute("SELECT key FROM settings WHERE key=?", ('ai_priority',)).fetchone()
        if existing:
            conn.execute("UPDATE settings SET value=? WHERE key=?", (ai_priority, 'ai_priority'))
        else:
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", ('ai_priority', ai_priority))
        
        conn.commit()
        conn.close()
        if request.is_json:
            return jsonify({'success': True})
        flash('AI Configuration saved.', 'success')
        return redirect(url_for('admin.ai_config'))
    
    # GET - load current settings
    groq_keys = conn.execute("SELECT value FROM settings WHERE key=?", ('groq_api_keys',)).fetchone()
    gemini_key = conn.execute("SELECT value FROM settings WHERE key=?", ('gemini_api_key',)).fetchone()
    ai_priority = conn.execute("SELECT value FROM settings WHERE key=?", ('ai_priority',)).fetchone()
    conn.close()
    
    settings = {
        'groq_api_keys': groq_keys[0] if groq_keys else '',
        'gemini_api_key': gemini_key[0] if gemini_key else '',
        'ai_priority': ai_priority[0] if ai_priority else 'groq,gemini',
    }
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


# ── BLOG MANAGEMENT ───────────────────────────────────────────
import re as _re

def _slugify(text):
    text = text.lower().strip()
    text = _re.sub(r'[^\w\s-]', '', text)
    text = _re.sub(r'[\s_-]+', '-', text)
    return text[:80]


@admin_bp.route('/blogs')
@admin_required
def blogs_list():
    conn = get_db()
    blogs = conn.execute("SELECT * FROM blogs ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template('admin/blogs.html', blogs=blogs)


@admin_bp.route('/blogs/new', methods=['GET', 'POST'])
@admin_required
def blog_new():
    if request.method == 'POST':
        title    = request.form.get('title', '').strip()
        summary  = request.form.get('summary', '').strip()
        content  = request.form.get('content', '').strip()
        cover    = request.form.get('cover_image', '').strip()
        author   = request.form.get('author', 'OutreachOS Team').strip()
        category = request.form.get('category', 'General').strip()
        tags     = request.form.get('tags', '').strip()
        published = 1 if request.form.get('published') else 0
        featured  = 1 if request.form.get('featured') else 0
        if not title:
            flash('Title is required.', 'error')
            return render_template('admin/blog_form.html', blog=None)
        slug = _slugify(title)
        conn = get_db()
        base_slug = slug; i = 1
        while conn.execute("SELECT id FROM blogs WHERE slug=?", (slug,)).fetchone():
            slug = f"{base_slug}-{i}"; i += 1
        conn.execute("""
            INSERT INTO blogs (title,slug,summary,content,cover_image,author,category,tags,published,featured)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (title, slug, summary, content, cover, author, category, tags, published, featured))
        conn.commit(); conn.close()
        flash(f'Blog "{title}" created.', 'success')
        return redirect(url_for('admin.blogs_list'))
    return render_template('admin/blog_form.html', blog=None)


@admin_bp.route('/blogs/<int:blog_id>/edit', methods=['GET', 'POST'])
@admin_required
def blog_edit(blog_id):
    conn = get_db()
    blog = conn.execute("SELECT * FROM blogs WHERE id=?", (blog_id,)).fetchone()
    if not blog:
        conn.close()
        flash('Blog not found.', 'error')
        return redirect(url_for('admin.blogs_list'))
    if request.method == 'POST':
        title    = request.form.get('title', '').strip()
        summary  = request.form.get('summary', '').strip()
        content  = request.form.get('content', '').strip()
        cover    = request.form.get('cover_image', '').strip()
        author   = request.form.get('author', 'OutreachOS Team').strip()
        category = request.form.get('category', 'General').strip()
        tags     = request.form.get('tags', '').strip()
        published = 1 if request.form.get('published') else 0
        featured  = 1 if request.form.get('featured') else 0
        conn.execute("""
            UPDATE blogs SET title=?,summary=?,content=?,cover_image=?,
                author=?,category=?,tags=?,published=?,featured=?,updated_at=?
            WHERE id=?
        """, (title, summary, content, cover, author, category, tags,
              published, featured, datetime.now(), blog_id))
        conn.commit(); conn.close()
        flash('Blog updated.', 'success')
        return redirect(url_for('admin.blogs_list'))
    conn.close()
    return render_template('admin/blog_form.html', blog=blog)


@admin_bp.route('/blogs/<int:blog_id>/delete', methods=['POST'])
@admin_required
def blog_delete(blog_id):
    conn = get_db()
    conn.execute("DELETE FROM blogs WHERE id=?", (blog_id,))
    conn.commit(); conn.close()
    flash('Blog deleted.', 'success')
    return redirect(url_for('admin.blogs_list'))


@admin_bp.route('/blogs/<int:blog_id>/toggle', methods=['POST'])
@admin_required
def blog_toggle(blog_id):
    conn = get_db()
    blog = conn.execute("SELECT published FROM blogs WHERE id=?", (blog_id,)).fetchone()
    if blog:
        conn.execute("UPDATE blogs SET published=? WHERE id=?", (0 if blog['published'] else 1, blog_id))
        conn.commit()
    conn.close()
    return jsonify({'success': True})
