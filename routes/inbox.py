"""
routes/inbox.py — Inbox & Thread Routes
=========================================
Thread listing, messages, reply sending, AI reply draft.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required

inbox_bp = Blueprint('inbox_routes', __name__)


@inbox_bp.route('/inbox', endpoint='inbox')
@login_required
def inbox():
    from services.workspace_service import get_wid, ws_threads
    wid = get_wid()
    status_filter = request.args.get('status', None)
    threads = ws_threads(wid, status_filter)
    return render_template('inbox.html', threads=threads, status_filter=status_filter)


@inbox_bp.route('/inbox/<int:thread_id>', endpoint='inbox_thread')
@login_required
def inbox_thread(thread_id):
    from services.inbox_service import get_thread_messages, mark_thread_read
    from app import get_db
    conn = get_db()
    thread = conn.execute("""
        SELECT t.*, c.name as contact_name, c.company as contact_company,
               c.email as contact_email, c.context as contact_context,
               camp.name as campaign_name
        FROM threads t
        LEFT JOIN contacts c ON t.contact_id = c.id
        LEFT JOIN campaigns camp ON t.campaign_id = camp.id
        WHERE t.id = ?
    """, (thread_id,)).fetchone()
    conn.close()
    if not thread:
        flash('Thread not found', 'error')
        return redirect(url_for('inbox_routes.inbox'))
    messages = get_thread_messages(thread_id)
    mark_thread_read(thread_id)
    return render_template('inbox_thread.html', thread=thread, messages=messages)


@inbox_bp.route('/api/inbox/thread/<int:thread_id>/status', methods=['POST'])
@login_required
def api_update_thread_status(thread_id):
    from services.inbox_service import update_thread_status
    status = request.json.get('status')
    if status not in ['active', 'interested', 'meeting', 'closed', 'booked', 'ignored']:
        return jsonify({'success': False, 'error': 'Invalid status'})
    update_thread_status(thread_id, status)
    return jsonify({'success': True})


@inbox_bp.route('/api/inbox/thread/<int:thread_id>/ai_reply', methods=['POST'])
@login_required
def api_generate_inbox_reply(thread_id):
    from services.inbox_service import generate_ai_reply_draft
    from services.workspace_service import get_wid
    from app import get_db
    wid = get_wid()
    conn = get_db()
    thread = conn.execute("""
        SELECT t.*, c.name as contact_name, c.company as contact_company, c.context as contact_context
        FROM threads t LEFT JOIN contacts c ON t.contact_id = c.id WHERE t.id = ? AND t.workspace_id=?
    """, (thread_id, wid)).fetchone()
    conn.close()
    if not thread:
        return jsonify({'success': False, 'error': 'Thread not found'}), 404
    draft = generate_ai_reply_draft(
        thread_id,
        thread['contact_name'] or 'there',
        thread['contact_company'] or '',
        thread['contact_context'] or ''
    )
    if draft:
        return jsonify({'success': True, 'draft': draft})
    return jsonify({'success': False, 'error': 'AI generation failed'})


@inbox_bp.route('/api/inbox/thread_data/<int:thread_id>')
@login_required
def api_thread_data(thread_id):
    from services.inbox_service import get_thread_messages, mark_thread_read
    from app import get_db
    conn = get_db()
    thread = conn.execute("""
        SELECT t.*, c.name as contact_name, c.company as contact_company,
               c.email as contact_email, c.context as contact_context
        FROM threads t LEFT JOIN contacts c ON t.contact_id = c.id WHERE t.id=?
    """, (thread_id,)).fetchone()
    conn.close()
    if not thread:
        return jsonify({'error': 'Not found'}), 404
    messages = get_thread_messages(thread_id)
    mark_thread_read(thread_id)
    return jsonify({
        'thread': dict(thread),
        'messages': [dict(m) for m in messages],
        'thread_status': thread['status']
    })


@inbox_bp.route('/api/contact_by_thread/<int:thread_id>')
@login_required
def api_contact_by_thread(thread_id):
    from app import get_db
    conn = get_db()
    thread = conn.execute('SELECT * FROM threads WHERE id=?', (thread_id,)).fetchone()
    if not thread:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    contact = conn.execute('SELECT * FROM contacts WHERE id=?', (thread['contact_id'],)).fetchone() if thread['contact_id'] else None
    timeline = []
    emails = conn.execute("""
        SELECT status, sent_at, opened, replied FROM emails_sent
        WHERE contact_id=? ORDER BY sent_at DESC LIMIT 5
    """, (thread['contact_id'],)).fetchall() if thread['contact_id'] else []
    for e in emails:
        def _fmt(dt):
            if not dt: return ''
            return dt[:16] if isinstance(dt, str) else str(dt)[:16]
        if e['replied']:
            timeline.append({'text': 'Reply received', 'color': '#10b981', 'time': _fmt(e['sent_at'])})
        if e['opened']:
            timeline.append({'text': 'Email opened', 'color': '#6366f1', 'time': _fmt(e['sent_at'])})
        if e['status'] == 'sent':
            timeline.append({'text': 'Email sent', 'color': '#9CA3AF', 'time': _fmt(e['sent_at'])})
    conn.close()
    return jsonify({
        'contact': dict(contact) if contact else None,
        'thread_status': thread['status'],
        'timeline': timeline[:6]
    })


@inbox_bp.route('/api/inbox/thread/<int:thread_id>/mark_read', methods=['POST'])
@login_required
def api_mark_thread_read(thread_id):
    from services.inbox_service import mark_thread_read
    mark_thread_read(thread_id)
    return jsonify({'success': True})


@inbox_bp.route('/api/inbox/thread/<int:thread_id>/send', methods=['POST'])
@login_required
def api_send_reply(thread_id):
    """Send a reply email from inbox via SMTP."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from app import get_db
    body = request.json.get('body', '').strip()
    if not body:
        return jsonify({'success': False, 'error': 'Empty reply'})
    conn = get_db()
    thread = conn.execute("""
        SELECT t.*, c.email as contact_email, c.name as contact_name
        FROM threads t LEFT JOIN contacts c ON t.contact_id = c.id WHERE t.id=?
    """, (thread_id,)).fetchone()
    if not thread:
        conn.close()
        return jsonify({'success': False, 'error': 'Thread not found'})
    to_email = thread['contact_email']
    subject = thread['subject'] or '(no subject)'
    if not subject.lower().startswith('re:'):
        subject = 'Re: ' + subject
    smtp_row = conn.execute("SELECT * FROM smtp_accounts WHERE active=1 ORDER BY id LIMIT 1").fetchone()
    if not smtp_row:
        conn.close()
        return jsonify({'success': False, 'error': 'No active SMTP account'})
    full_body = body
    smtp_keys = smtp_row.keys()
    sig = smtp_row['signature'] if 'signature' in smtp_keys else ''
    if sig:
        full_body += '\n\n' + sig
    from_name = smtp_row['from_name'] if 'from_name' in smtp_keys else ''
    smtp_email = smtp_row['email']
    login_user_smtp = smtp_row['login_username'] if 'login_username' in smtp_keys and smtp_row['login_username'] else smtp_email
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{from_name} <{smtp_email}>" if from_name else smtp_email
        msg['To'] = to_email
        msg['Subject'] = subject
        html_body = full_body.replace('\n', '<br>')
        msg.attach(MIMEText(html_body, 'html'))
        server = smtplib.SMTP(smtp_row['smtp_server'], int(smtp_row['smtp_port']))
        server.starttls()
        server.login(login_user_smtp, smtp_row['password'])
        server.sendmail(smtp_email, to_email, msg.as_string())
        server.quit()
        conn.execute("""
            INSERT INTO messages (thread_id, direction, body, sender_email, created_at)
            VALUES (?, 'outgoing', ?, ?, CURRENT_TIMESTAMP)
        """, (thread_id, full_body, smtp_email))
        conn.execute("UPDATE threads SET last_message_at=CURRENT_TIMESTAMP WHERE id=?", (thread_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)[:150]})


@inbox_bp.route('/api/inbox/stats')
@login_required
def api_inbox_stats():
    from app import get_db
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    unread = conn.execute("SELECT COUNT(*) FROM threads WHERE unread_count > 0").fetchone()[0]
    interested = conn.execute("SELECT COUNT(*) FROM threads WHERE status='interested'").fetchone()[0]
    meeting = conn.execute("SELECT COUNT(*) FROM threads WHERE status='meeting'").fetchone()[0]
    conn.close()
    return jsonify({'total': total, 'unread': unread, 'interested': interested, 'meeting': meeting})


@inbox_bp.route('/follow_ups', endpoint='follow_ups')
@login_required
def follow_ups():
    from app import get_db
    conn = get_db()
    rows = conn.execute("SELECT * FROM follow_ups ORDER BY replied_at DESC").fetchall()
    conn.close()
    return render_template('follow_ups.html', follow_ups=rows)


@inbox_bp.route('/follow_up/add', methods=['POST'])
@login_required
def add_follow_up():
    from app import get_db
    email = request.form.get('email', '').strip().lower()
    notes = request.form.get('notes', '')
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE email=?", (email,)).fetchone()
    if contact:
        conn.execute("""
            INSERT INTO follow_ups (contact_id, email, name, company, notes)
            VALUES (?,?,?,?,?)
        """, (contact['id'], email, contact['name'], contact['company'], notes))
        conn.execute("UPDATE emails_sent SET replied=1 WHERE contact_id=?", (contact['id'],))
    else:
        conn.execute("""
            INSERT INTO follow_ups (contact_id, email, name, company, notes)
            VALUES (?,?,?,?,?)
        """, (0, email, 'Unknown', 'Unknown', notes))
    conn.commit()
    conn.close()
    flash(f'Follow-up added for {email}', 'success')
    return redirect(url_for('inbox_routes.follow_ups'))


@inbox_bp.route('/api/check_replies', methods=['POST'])
@login_required
def api_check_replies():
    """Manually trigger IMAP reply check."""
    try:
        from app import queue_check_replies, check_replies
        task_id = queue_check_replies()
        if task_id:
            return jsonify({'success': True, 'logged': 0, 'queued': True, 'task_id': task_id})
        logged = check_replies()
        return jsonify({'success': True, 'logged': logged})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})


@inbox_bp.route('/api/imap_status')
@login_required
def api_imap_status():
    from app import get_setting, imap_checker_running
    imap_server = get_setting('imap_server')
    imap_username = get_setting('imap_username')
    configured = bool(imap_server and imap_username)
    return jsonify({
        'configured': configured,
        'running': imap_checker_running,
        'server': imap_server or 'Not set',
        'username': imap_username or 'Not set',
        'interval': get_setting('imap_check_interval') or '180'
    })
