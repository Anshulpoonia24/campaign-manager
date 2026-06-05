"""
routes/analytics.py — Analytics, Logs, Exports, Deliverability
================================================================
Dashboard analytics, log viewer, data exports, deliverability page.
"""
import json
import os
import uuid
from collections import defaultdict
from flask import Blueprint, render_template, request, jsonify, send_file, redirect, url_for, flash
from flask_login import login_required, current_user

analytics_bp = Blueprint('analytics', __name__)


def _dt(val):
    """Safely stringify a datetime/string to YYYY-MM-DD HH:MM."""
    if not val:
        return ''
    return val[:10] if isinstance(val, str) else str(val)[:10]


@analytics_bp.route('/analytics', endpoint='analytics_page')
@login_required
def analytics_page():
    from app import get_db
    conn = get_db()
    total_sent    = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    total_opened  = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1").fetchone()[0]
    total_replied = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE replied=1").fetchone()[0]
    total_clicks  = conn.execute("SELECT COUNT(*) FROM email_clicks").fetchone()[0]
    total_bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed')").fetchone()[0]

    by_date_sent   = defaultdict(int)
    by_date_opened = defaultdict(int)
    logs = conn.execute("SELECT status, opened, sent_at FROM emails_sent ORDER BY sent_at").fetchall()
    for l in logs:
        if l['sent_at']:
            day = _dt(l['sent_at'])
            if l['status'] == 'sent':
                by_date_sent[day] += 1
            if l['opened']:
                by_date_opened[day] += 1
    all_days  = sorted(set(list(by_date_sent.keys()) + list(by_date_opened.keys())))
    time_data = {'labels': all_days, 'sent': [by_date_sent[d] for d in all_days], 'opened': [by_date_opened[d] for d in all_days]}

    by_provider = conn.execute("SELECT provider, COUNT(*) as total FROM ai_usage GROUP BY provider").fetchall()
    conn.close()

    open_rate   = round(total_opened  / total_sent * 100, 1) if total_sent else 0
    reply_rate  = round(total_replied / total_sent * 100, 1) if total_sent else 0
    click_rate  = round(total_clicks  / total_sent * 100, 1) if total_sent else 0
    bounce_rate = round(total_bounced / total_sent * 100, 1) if total_sent else 0

    return render_template('analytics.html',
        total_sent=total_sent, total_opened=total_opened, total_replied=total_replied,
        total_clicks=total_clicks, total_bounced=total_bounced,
        open_rate=open_rate, reply_rate=reply_rate, click_rate=click_rate, bounce_rate=bounce_rate,
        time_data=json.dumps(time_data),
        ai_providers=[dict(r) for r in by_provider])


@analytics_bp.route('/deliverability', endpoint='deliverability_page')
@login_required
def deliverability_page():
    from app import get_db
    conn = get_db()
    smtp_accounts = conn.execute("SELECT * FROM smtp_accounts ORDER BY active DESC, health_score DESC").fetchall()
    bounced = conn.execute("""
        SELECT es.*, c.name, c.company FROM emails_sent es
        JOIN contacts c ON es.contact_id = c.id
        WHERE es.status IN ('bounced', 'failed')
        ORDER BY es.sent_at DESC LIMIT 100
    """).fetchall()
    total_sent    = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    total_bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed')").fetchone()[0]
    bounce_rate   = round(total_bounced / total_sent * 100, 1) if total_sent else 0
    conn.close()
    return render_template('deliverability.html',
        smtp_accounts=smtp_accounts, bounced=bounced,
        total_bounced=total_bounced, bounce_rate=bounce_rate)


@analytics_bp.route('/api/click_analytics')
@login_required
def api_click_analytics():
    from services.lead_scoring import get_click_analytics
    return jsonify(get_click_analytics())


@analytics_bp.route('/api/hot_leads')
@login_required
def api_hot_leads():
    from services.lead_scoring import get_hot_leads, calculate_priority
    leads  = get_hot_leads(limit=20)
    result = []
    for l in leads:
        result.append({
            'id': l['id'], 'name': l['name'], 'company': l['company'],
            'email': l['email'], 'lead_score': l['lead_score'],
            'priority': calculate_priority(l['lead_score']),
            'status': l['status'], 'last_activity': l['last_activity'],
            'thread_id': l['thread_id'], 'thread_status': l['thread_status']
        })
    return jsonify({'leads': result})


@analytics_bp.route('/api/ai_usage')
@login_required
def api_ai_usage():
    from app import get_db
    conn = get_db()
    by_provider = conn.execute("""
        SELECT provider, COUNT(*) as total, SUM(success) as success
        FROM ai_usage GROUP BY provider
    """).fetchall()
    by_date = conn.execute("""
        SELECT DATE(created_at) as day, provider, COUNT(*) as total
        FROM ai_usage GROUP BY day, provider ORDER BY day
    """).fetchall()
    conn.close()
    return jsonify({
        'by_provider': [{'provider': r['provider'], 'total': r['total'], 'success': r['success']} for r in by_provider],
        'by_date':     [{'day': str(r['day']) if r['day'] else '', 'provider': r['provider'], 'total': r['total']} for r in by_date]
    })


@analytics_bp.route('/logs', endpoint='logs_page')
@login_required
def logs_page():
    from app import get_db
    conn = get_db()
    logs = conn.execute("""
        SELECT es.*, c.name, c.company, camp.name as campaign_name
        FROM emails_sent es
        JOIN contacts c ON es.contact_id = c.id
        JOIN campaigns camp ON es.campaign_id = camp.id
        ORDER BY es.sent_at DESC
    """).fetchall()

    sent     = sum(1 for l in logs if l['status'] == 'sent')
    failed   = sum(1 for l in logs if l['status'] == 'failed')
    bounced  = sum(1 for l in logs if l['status'] == 'bounced')
    opened   = sum(1 for l in logs if l['opened'])
    total    = len(logs)
    campaigns_count = len(set(l['campaign_id'] for l in logs))
    success_rate = (sent / total * 100) if total > 0 else 0

    stats = {'sent': sent, 'failed': failed, 'bounced': bounced, 'opened': opened,
             'not_opened': sent - opened, 'total': total,
             'campaigns': campaigns_count, 'success_rate': success_rate}

    by_date_sent   = defaultdict(int)
    by_date_failed = defaultdict(int)
    for l in logs:
        if l['sent_at']:
            day = _dt(l['sent_at'])
            if l['status'] == 'sent':
                by_date_sent[day] += 1
            else:
                by_date_failed[day] += 1
    all_days  = sorted(set(list(by_date_sent.keys()) + list(by_date_failed.keys())))
    time_data = {'labels': all_days, 'sent': [by_date_sent[d] for d in all_days], 'failed': [by_date_failed[d] for d in all_days]}

    conn.close()
    return render_template('logs.html', logs=logs, stats=stats,
                           stats_json=json.dumps(stats), time_data_json=json.dumps(time_data))


@analytics_bp.route('/bounced')
@login_required
def bounced():
    from app import get_db
    conn = get_db()
    rows = conn.execute("""
        SELECT es.*, c.name, c.company FROM emails_sent es
        JOIN contacts c ON es.contact_id = c.id
        WHERE es.status IN ('bounced', 'failed')
        ORDER BY es.sent_at DESC
    """).fetchall()
    conn.close()
    return render_template('bounced.html', bounced=rows)


@analytics_bp.route('/export/<string:export_type>')
@login_required
def export_data(export_type):
    import pandas as pd
    import tempfile
    from services.workspace_service import get_wid
    from utils.db import USE_POSTGRES
    from app import get_db
    wid     = get_wid()
    conn    = get_db()
    db_conn = conn.raw if hasattr(conn, 'raw') else conn
    ph      = '%s' if (USE_POSTGRES and hasattr(conn, 'raw')) else '?'
    # pd.read_sql needs native connection - use params as positional
    if export_type == 'sent':
        df = pd.read_sql(f"SELECT c.name, c.company, es.email, es.subject, es.status, es.sent_at FROM emails_sent es JOIN contacts c ON es.contact_id=c.id WHERE es.status='sent' AND es.workspace_id={ph}", db_conn, params=[wid])
    elif export_type == 'bounced':
        df = pd.read_sql(f"SELECT c.name, c.company, es.email, es.bounce_reason, es.sent_at FROM emails_sent es JOIN contacts c ON es.contact_id=c.id WHERE es.status IN ('bounced','failed') AND es.workspace_id={ph}", db_conn, params=[wid])
    elif export_type == 'follow_ups':
        df = pd.read_sql(f"SELECT * FROM follow_ups WHERE workspace_id={ph}", db_conn, params=[wid])
    elif export_type == 'invalid':
        df = pd.read_sql(f"SELECT name, company, email, validation_reason FROM contacts WHERE email_valid=0 AND workspace_id={ph}", db_conn, params=[wid])
    else:
        df = pd.read_sql(f"SELECT * FROM contacts WHERE workspace_id={ph}", db_conn, params=[wid])
    conn.close()
    filepath = os.path.join(tempfile.gettempdir(), f"export_{export_type}_{uuid.uuid4().hex[:8]}.xlsx")
    df.to_excel(filepath, index=False)
    return send_file(filepath, as_attachment=True, download_name=f"export_{export_type}.xlsx")


@analytics_bp.route('/live-logs', endpoint='live_logs_page')
@login_required
def live_logs_page():
    if getattr(current_user, 'role', '') != 'admin':
        flash('Access denied', 'error')
        return redirect(url_for('dash.dashboard'))
    return render_template('live_logs.html')


@analytics_bp.route('/api/live-logs')
@login_required
def api_live_logs():
    from app import get_db, LOG_DIR
    from services.workspace_service import get_wid
    if getattr(current_user, 'role', '') != 'admin':
        return jsonify({'logs': [], 'last_id': 0})
    tab      = request.args.get('tab', 'all')
    after_id = int(request.args.get('after', 0))
    wid      = get_wid()
    logs     = []

    if tab in ('all', 'campaign', 'smtp'):
        conn = get_db()
        rows = conn.execute("""
            SELECT id, campaign_id, level, message, smtp_email, created_at
            FROM campaign_logs WHERE id > ? AND workspace_id = ?
            ORDER BY created_at DESC LIMIT 100
        """, (after_id, wid)).fetchall()
        conn.close()
        for r in reversed(rows):
            if tab == 'smtp' and not r['smtp_email']:
                continue
            row = dict(r)
            if row.get('created_at') and not isinstance(row['created_at'], str):
                row['created_at'] = str(row['created_at'])
            logs.append(row)

    if tab in ('all', 'error'):
        err_path = os.path.join(LOG_DIR, 'error.log')
        if os.path.exists(err_path):
            try:
                with open(err_path, 'r') as f:
                    lines = f.readlines()[-50:]
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    ts  = line[:19] if len(line) > 19 else ''
                    msg = line[20:].strip() if len(line) > 20 else line
                    logs.append({'id': 0, 'level': 'error', 'message': msg, 'smtp_email': '', 'created_at': ts})
            except Exception:
                pass

    if tab == 'copilot':
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT id, page_type, user_message, action_taken, created_at
                FROM copilot_logs WHERE workspace_id = ?
                ORDER BY created_at DESC LIMIT 50
            """, (wid,)).fetchall()
            for r in reversed(rows):
                logs.append({
                    'id': r['id'], 'level': 'info',
                    'message': f"[{r['page_type']}] {r['user_message'][:80]}" + (f" → {r['action_taken']}" if r['action_taken'] else ''),
                    'smtp_email': '', 'created_at': str(r['created_at']) if r['created_at'] else ''
                })
        except Exception:
            pass
        conn.close()

    last_id = max((l.get('id', 0) for l in logs), default=after_id)
    return jsonify({'logs': logs, 'last_id': last_id})
