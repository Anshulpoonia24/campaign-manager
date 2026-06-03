"""
routes/tracking.py — Email Tracking Routes
============================================
Open pixel, click redirect, unsubscribe.
"""
from flask import Blueprint, Response, request, redirect, render_template, jsonify
from flask_login import login_required

tracking_bp = Blueprint('tracking', __name__)

TRACKING_PIXEL = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82'


@tracking_bp.route('/track/<tracking_id>.png')
def track_open(tracking_id):
    """1x1 transparent pixel — marks email as opened, logs event, updates lead score."""
    from services.tracking import process_open
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    ua = request.headers.get('User-Agent', '')
    process_open(tracking_id, ip, ua)
    return Response(
        TRACKING_PIXEL, mimetype='image/png',
        headers={'Cache-Control': 'no-cache, no-store, must-revalidate', 'Pragma': 'no-cache'}
    )


@tracking_bp.route('/click/<token>')
def track_click(token):
    """Click tracking redirect — logs event, updates lead score, redirects safely."""
    from services.tracking import process_click
    original_url = request.args.get('url', '')
    tracking_id = request.args.get('tid', '')
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    ua = request.headers.get('User-Agent', '')

    if not original_url:
        return redirect('https://shikshainfotech.com')

    redirect_url = process_click(token, original_url, tracking_id, ip, ua)
    if redirect_url:
        return redirect(redirect_url)
    return redirect('https://shikshainfotech.com')


@tracking_bp.route('/unsubscribe/<tracking_id>', methods=['GET', 'POST'])
def unsubscribe(tracking_id):
    """Public unsubscribe page — no login needed"""
    from app import get_db
    conn = get_db()
    record = conn.execute("SELECT email FROM emails_sent WHERE tracking_id=?", (tracking_id,)).fetchone()
    if not record:
        conn.close()
        return render_template('unsubscribe.html', success=False, error='Invalid link')

    email = record['email']

    if request.method == 'POST':
        reason = request.form.get('reason', '')
        existing = conn.execute("SELECT id FROM unsubscribes WHERE email=?", (email,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO unsubscribes (email, reason) VALUES (?,?)", (email, reason))
            conn.commit()
        conn.close()
        return render_template('unsubscribe.html', success=True, email=email)

    conn.close()
    return render_template('unsubscribe.html', success=None, email=email, tracking_id=tracking_id)


@tracking_bp.route('/api/unsubscribes')
@login_required
def api_unsubscribes():
    from app import get_db
    conn = get_db()
    rows = conn.execute("SELECT * FROM unsubscribes ORDER BY unsubscribed_at DESC").fetchall()
    conn.close()
    return jsonify({'unsubscribes': [{'email': r['email'], 'reason': r['reason'], 'date': r['unsubscribed_at']} for r in rows]})


@tracking_bp.route('/api/tracking/timeline')
@login_required
def api_tracking_timeline():
    """Get workspace activity timeline."""
    from services.tracking import get_workspace_timeline
    from services.workspace_service import get_wid
    wid = get_wid()
    limit = int(request.args.get('limit', 50))
    timeline = get_workspace_timeline(wid, limit)
    return jsonify({'timeline': timeline})


@tracking_bp.route('/api/tracking/contact/<int:contact_id>')
@login_required
def api_contact_timeline(contact_id):
    """Get engagement timeline for a specific contact."""
    from services.tracking import get_contact_timeline
    from services.workspace_service import get_wid
    wid = get_wid()
    timeline = get_contact_timeline(contact_id, wid)
    return jsonify({'timeline': timeline})


@tracking_bp.route('/api/tracking/stats')
@login_required
def api_tracking_stats():
    """Get engagement stats for workspace."""
    from services.tracking import get_engagement_stats
    from services.workspace_service import get_wid
    wid = get_wid()
    days = int(request.args.get('days', 30))
    return jsonify(get_engagement_stats(wid, days))


@tracking_bp.route('/api/tracking/hot_leads')
@login_required
def api_tracking_hot_leads():
    """Get hot leads with temperature scores."""
    from services.tracking import get_temperature, get_temperature_color
    from services.workspace_service import get_wid
    from app import get_db
    wid = get_wid()
    conn = get_db()
    leads = conn.execute("""
        SELECT c.id, c.name, c.company, c.email,
               COALESCE(c.lead_score, 0) as lead_score, c.status,
               MAX(es.sent_at) as last_activity,
               (SELECT t2.status FROM threads t2 WHERE t2.contact_id = c.id ORDER BY t2.last_message_at DESC LIMIT 1) as thread_status,
               (SELECT t3.id FROM threads t3 WHERE t3.contact_id = c.id ORDER BY t3.last_message_at DESC LIMIT 1) as thread_id
        FROM contacts c
        LEFT JOIN emails_sent es ON es.contact_id = c.id AND es.status='sent'
        WHERE c.workspace_id = ? AND COALESCE(c.lead_score, 0) > 0
        GROUP BY c.id, c.name, c.company, c.email, c.lead_score, c.status
        ORDER BY c.lead_score DESC
        LIMIT 20
    """, (wid,)).fetchall()
    conn.close()
    result = []
    for l in leads:
        score = l['lead_score']
        temp = get_temperature(score)
        result.append({
            'id': l['id'], 'name': l['name'], 'company': l['company'],
            'email': l['email'], 'lead_score': score,
            'temperature': temp, 'temperature_color': get_temperature_color(temp),
            'status': l['status'], 'thread_id': l['thread_id'],
            'last_activity': l['last_activity'],
        })
    return jsonify({'leads': result})
