"""
routes/dashboard.py — Dashboard & Landing Pages
==================================================
Dashboard, landing, solutions, blogs, contact pages.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

dashboard_bp = Blueprint('dash', __name__)


@dashboard_bp.route('/')
def landing_page():
    if current_user.is_authenticated:
        return redirect(url_for('dash.dashboard'))
    from app import get_db
    conn = get_db()
    latest_blogs = conn.execute(
        "SELECT id, title, slug, summary, cover_image, category, author, created_at FROM blogs WHERE published=1 ORDER BY featured DESC, created_at DESC LIMIT 3"
    ).fetchall()
    conn.close()
    return render_template('landing.html', latest_blogs=latest_blogs)


@dashboard_bp.route('/solutions')
def solutions_page():
    return render_template('solutions.html')


@dashboard_bp.route('/blogs')
def blogs_page():
    from app import get_db
    conn = get_db()
    blogs = conn.execute(
        "SELECT * FROM blogs WHERE published=1 ORDER BY featured DESC, created_at DESC"
    ).fetchall()
    conn.close()
    return render_template('blogs.html', blogs=blogs)


@dashboard_bp.route('/blogs/<slug>')
def blog_post(slug):
    from app import get_db
    conn = get_db()
    blog = conn.execute(
        "SELECT * FROM blogs WHERE slug=? AND published=1", (slug,)
    ).fetchone()
    conn.close()
    if not blog:
        from flask import abort
        abort(404)
    return render_template('blog_post.html', blog=blog)


@dashboard_bp.route('/contact', methods=['GET', 'POST'])
def contact_page():
    if request.method == 'POST':
        flash('Message sent! We\'ll get back to you soon.', 'success')
        return redirect(url_for('dash.contact_page'))
    return render_template('contact.html')


@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    from app import get_db, error_logger
    try:
        return _dashboard_inner()
    except Exception as e:
        error_logger.error(f'Dashboard crash: {e}')
        return f'''<html><body style="font-family:sans-serif;padding:40px;">
        <h2>Dashboard Error</h2>
        <p style="color:red;">{str(e)[:200]}</p>
        <p>The app started but dashboard has an error. Try:</p>
        <ul>
        <li><a href="/settings">Settings</a></li>
        <li><a href="/campaigns">Campaigns</a></li>
        <li><a href="/live-logs">Live Logs</a></li>
        <li><a href="/logout">Logout</a></li>
        </ul>
        </body></html>''', 500


def _dashboard_inner():
    from app import get_db
    from services.lead_scoring import get_hot_leads, calculate_priority
    conn = get_db()
    total_sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    total_bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed')").fetchone()[0]
    total_opened = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1").fetchone()[0]
    total_replied = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE replied=1").fetchone()[0]
    total_clicks = conn.execute("SELECT COUNT(DISTINCT contact_id) FROM email_clicks WHERE contact_id IS NOT NULL").fetchone()[0]
    total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

    open_rate = round(total_opened / total_sent * 100, 1) if total_sent else 0
    reply_rate = round(total_replied / total_sent * 100, 1) if total_sent else 0
    click_rate = round(total_clicks / total_sent * 100, 1) if total_sent else 0
    bounce_rate = round(total_bounced / total_sent * 100, 1) if total_sent else 0
    meetings_detected = conn.execute("SELECT COUNT(*) FROM threads WHERE status='meeting'").fetchone()[0]

    attention_threads = conn.execute("""
        SELECT t.id, t.status, t.unread_count, t.last_message_at,
               c.name as contact_name, c.company as contact_company, c.email as contact_email,
               (SELECT m2.ai_category FROM messages m2 WHERE m2.thread_id = t.id AND m2.direction='incoming' ORDER BY m2.created_at DESC LIMIT 1) as ai_category
        FROM threads t LEFT JOIN contacts c ON t.contact_id = c.id
        WHERE t.status IN ('interested','meeting') OR t.unread_count > 0
        ORDER BY t.last_message_at DESC LIMIT 8
    """).fetchall()

    campaigns = conn.execute("""
        SELECT c.*,
            COUNT(CASE WHEN es.status='sent' THEN 1 END) as sent_count,
            COUNT(CASE WHEN es.opened=1 THEN 1 END) as opened_count,
            COUNT(CASE WHEN es.replied=1 THEN 1 END) as replied_count,
            COUNT(CASE WHEN es.status IN ('bounced','failed') THEN 1 END) as bounce_count
        FROM campaigns c LEFT JOIN emails_sent es ON es.campaign_id = c.id
        GROUP BY c.id ORDER BY c.created_at DESC LIMIT 6
    """).fetchall()

    smtp_accounts = conn.execute("""
        SELECT id, email, health_score, warmup_stage, active, sent_today, daily_limit
        FROM smtp_accounts ORDER BY active DESC, health_score DESC
    """).fetchall()
    smtp_active = sum(1 for a in smtp_accounts if a['active'])
    smtp_at_risk = sum(1 for a in smtp_accounts if a['health_score'] < 50 and a['active'])
    avg_health = round(sum(a['health_score'] for a in smtp_accounts) / len(smtp_accounts), 0) if smtp_accounts else 0

    activity_feed = []
    recent_replies = conn.execute("""
        SELECT m.created_at, m.ai_category, m.sender_email,
               c.name as contact_name, c.company, t.id as thread_id
        FROM messages m JOIN threads t ON m.thread_id = t.id
        LEFT JOIN contacts c ON t.contact_id = c.id
        WHERE m.direction='incoming' ORDER BY m.created_at DESC LIMIT 5
    """).fetchall()
    for r in recent_replies:
        activity_feed.append({'type': 'reply', 'time': r['created_at'], 'text': f"{r['contact_name'] or r['sender_email']} replied", 'sub': r['ai_category'] or 'reply', 'link': f"/inbox/{r['thread_id']}", 'company': r['company'] or ''})

    recent_sends = conn.execute("""
        SELECT es.sent_at, es.status, c.name, c.company, es.campaign_id
        FROM emails_sent es JOIN contacts c ON es.contact_id=c.id
        ORDER BY es.sent_at DESC LIMIT 5
    """).fetchall()
    for s in recent_sends:
        activity_feed.append({'type': 'send' if s['status']=='sent' else 'bounce', 'time': s['sent_at'], 'text': f"Email {'sent to' if s['status']=='sent' else 'bounced for'} {s['name']}", 'sub': s['company'] or '', 'link': f"/campaign/{s['campaign_id']}", 'company': s['company'] or ''})

    recent_clicks = conn.execute("""
        SELECT ec.created_at, c.name, c.company, ec.thread_id
        FROM email_clicks ec LEFT JOIN contacts c ON ec.contact_id=c.id
        ORDER BY ec.created_at DESC LIMIT 3
    """).fetchall()
    for cl in recent_clicks:
        activity_feed.append({'type': 'click', 'time': cl['created_at'], 'text': f"{cl['name'] or 'Someone'} clicked a link", 'sub': cl['company'] or '', 'link': f"/inbox/{cl['thread_id']}" if cl['thread_id'] else '#', 'company': cl['company'] or ''})

    activity_feed.sort(key=lambda x: x['time'] or '', reverse=True)
    activity_feed = activity_feed[:12]

    setup_steps = [
        {'done': bool(smtp_accounts), 'label': 'Add SMTP account', 'link': '/settings'},
        {'done': total_contacts > 0, 'label': 'Upload contacts', 'link': '/upload'},
        {'done': total_sent > 0, 'label': 'Launch first campaign', 'link': '/campaigns'},
    ]

    conn.close()
    hot_leads = get_hot_leads(limit=8)
    hot_leads_count = len([l for l in hot_leads if calculate_priority(l['lead_score']) == 'hot'])
    unread_count = sum(1 for t in attention_threads if t['unread_count'] > 0)

    from datetime import datetime as _dt
    now_hour = _dt.now().hour

    return render_template('dashboard.html',
        now_hour=now_hour,
        total_sent=total_sent, total_opened=total_opened, total_replied=total_replied,
        total_clicks=total_clicks, total_contacts=total_contacts, total_bounced=total_bounced,
        open_rate=open_rate, reply_rate=reply_rate, click_rate=click_rate, bounce_rate=bounce_rate,
        meetings_detected=meetings_detected, hot_leads_count=hot_leads_count,
        attention_threads=attention_threads, campaigns=campaigns,
        smtp_accounts=smtp_accounts, smtp_active=smtp_active, smtp_at_risk=smtp_at_risk, avg_health=avg_health,
        activity_feed=activity_feed, hot_leads=hot_leads,
        calculate_priority=calculate_priority, setup_steps=setup_steps,
        unread_count=unread_count)
