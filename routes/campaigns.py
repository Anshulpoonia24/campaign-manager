"""
routes/campaigns.py — Campaign Management Routes
===================================================
Campaign CRUD, template/AI sends, launch, pause/resume/cancel, progress.
"""
import os
import time
import uuid
import threading
import smtplib
import mimetypes
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

campaigns_bp = Blueprint('campaigns', __name__)


# ── HELPERS ──────────────────────────────────────────────────────
def _app_imports():
    from app import (get_db, get_setting, set_setting, inject_tracking_pixel,
                     is_unsubscribed, _get_reply_to, app_logger, error_logger,
                     smtp_logger, limiter, UPLOAD_DIR, _get_campaign_lock,
                     _get_send_progress, _set_send_progress, ai_generated_cache,
                     generate_ai_email, CELERY_AVAILABLE, has_active_workers)
    return locals()


def _mark_helpers():
    from services.smtp_rotation import mark_send_success, mark_send_failure
    return mark_send_success, mark_send_failure


# ── CAMPAIGN LIST ─────────────────────────────────────────────────
@campaigns_bp.route('/campaigns')
@login_required
def campaigns_list():
    from app import get_db
    from services.workspace_service import get_wid, ws_campaigns
    wid = get_wid()
    campaigns = ws_campaigns(wid)
    conn = get_db()
    meetings = {}
    for camp in campaigns:
        m = conn.execute(
            "SELECT COUNT(*) FROM threads WHERE campaign_id=? AND status='meeting' AND workspace_id=?",
            (camp['id'], wid)
        ).fetchone()[0]
        meetings[camp['id']] = m
    conn.close()
    return render_template('campaigns.html', campaigns=campaigns, meetings=meetings)


@campaigns_bp.route('/campaign/new', methods=['GET', 'POST'])
@login_required
def new_campaign():
    from app import get_db
    from utils.db import is_postgres
    from services.workspace_service import get_wid
    if request.method == 'POST':
        name = request.form.get('campaign_name', 'Untitled Campaign')
        description = request.form.get('description', '')
        wid = get_wid()
        conn = get_db()
        if is_postgres():
            campaign_id = conn.execute(
                "INSERT INTO campaigns (name, description, workspace_id) VALUES (?,?,?) RETURNING id",
                (name, description, wid)
            ).fetchone()[0]
            conn.commit()
        else:
            conn.execute("INSERT INTO campaigns (name, description, workspace_id) VALUES (?,?,?)", (name, description, wid))
            conn.commit()
            campaign_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))
    return render_template('new_campaign.html')


@campaigns_bp.route('/campaign/edit/<int:campaign_id>', methods=['POST'])
@login_required
def edit_campaign(campaign_id):
    from app import get_db
    from utils.ownership import owns_campaign
    if not owns_campaign(campaign_id):
        flash('Not found.', 'error')
        return redirect(url_for('campaigns.campaigns_list'))
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    conn = get_db()
    conn.execute("UPDATE campaigns SET name=?, description=? WHERE id=?", (name, description, campaign_id))
    conn.commit()
    conn.close()
    flash('Campaign updated!', 'success')
    return redirect(url_for('campaigns.campaigns_list'))


@campaigns_bp.route('/campaign/<int:campaign_id>')
@login_required
def campaign_detail(campaign_id):
    from app import get_db
    conn = get_db()
    campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    emails = conn.execute("""
        SELECT es.*, c.name, c.company FROM emails_sent es
        JOIN contacts c ON es.contact_id = c.id
        WHERE es.campaign_id=? ORDER BY es.sent_at DESC
    """, (campaign_id,)).fetchall()
    available = conn.execute("""
        SELECT * FROM contacts WHERE email_valid=1
        AND id NOT IN (SELECT contact_id FROM emails_sent WHERE campaign_id=? AND status='sent')
    """, (campaign_id,)).fetchall()
    conn.close()
    return render_template('campaign_detail.html', campaign=campaign, emails=emails, available=available)


# ── TEMPLATE SEND ─────────────────────────────────────────────────
@campaigns_bp.route('/campaign/<int:campaign_id>/send', methods=['POST'])
@login_required
def send_campaign(campaign_id):
    from app import (get_db, get_setting, inject_tracking_pixel, is_unsubscribed,
                     _get_reply_to, app_logger, smtp_logger, error_logger,
                     UPLOAD_DIR, _get_campaign_lock, limiter)
    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account, append_signature, mark_send_success, mark_send_failure
    from services.inbox_service import get_or_create_thread, insert_message
    from services.lead_scoring import update_lead_score

    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    body_template = request.form.get('body', '')
    contact_ids = request.form.getlist('contact_ids')

    # Handle file upload
    attachment_filename = ''
    uploaded_file = request.files.get('attachment_file')
    if uploaded_file and uploaded_file.filename:
        from werkzeug.utils import secure_filename
        filename = secure_filename(uploaded_file.filename)
        filepath = os.path.join(UPLOAD_DIR, filename)
        uploaded_file.save(filepath)
        attachment_filename = filename
    else:
        attachment_filename = request.form.get('attachment', '')

    if not contact_ids:
        flash('No contacts selected. Please select at least one contact.', 'error')
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))

    app_logger.info(f'Campaign {campaign_id} send started | {len(contact_ids)} contacts | by {current_user.username}')
    wid = get_wid()
    conn = get_db()
    sent = 0
    failed = 0

    def get_smtp_creds():
        account = get_next_smtp_account(workspace_id=wid)
        if account:
            return account
        return {
            'server': get_setting('smtp_server'), 'port': int(get_setting('smtp_port') or 587),
            'username': get_setting('smtp_username'), 'password': get_setting('smtp_password'),
            'from_email': get_setting('from_email') or get_setting('smtp_username'),
            'from_name': get_setting('from_name'), 'reply_to': get_setting('reply_to'),
            'bcc_emails': get_setting('bcc_emails'), 'signature': '',
            'account_id': None, 'email': get_setting('from_email') or get_setting('smtp_username'),
            'smtp_server': get_setting('smtp_server'), 'smtp_port': int(get_setting('smtp_port') or 587),
        }

    try:
        for idx, cid in enumerate(contact_ids):
            creds = get_smtp_creds()
            smtp_server = creds.get('smtp_server') or creds.get('server')
            smtp_port = creds.get('smtp_port') or creds.get('port')
            smtp_username = creds.get('login_username') or creds.get('email') or creds.get('username')
            smtp_password = creds['password']
            from_email = creds.get('from_email') or smtp_username
            from_name = creds.get('from_name', '')
            account_id = creds.get('account_id') or creds.get('id')
            reply_to = creds.get('reply_to') or _get_reply_to()
            bcc = creds.get('bcc_emails') or get_setting('bcc_emails')
            signature = creds.get('signature', '')

            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
            if not contact:
                continue
            if is_unsubscribed(contact['email']):
                continue

            with _get_campaign_lock(campaign_id):
                already = conn.execute(
                    "SELECT id FROM emails_sent WHERE contact_id=? AND campaign_id=? AND status='sent'",
                    (cid, campaign_id)
                ).fetchone()
                if already:
                    continue

                subject = subject_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')
                body = body_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')

                try:
                    server = smtplib.SMTP(smtp_server, smtp_port)
                    server.starttls()
                    server.login(smtp_username, smtp_password)

                    tracking_id = str(uuid.uuid4())
                    body_with_sig = append_signature(body, signature)
                    tracked_body = inject_tracking_pixel(body_with_sig, tracking_id)

                    msg = EmailMessage()
                    msg['Subject'] = subject
                    msg['From'] = formataddr((from_name, from_email))
                    msg['To'] = contact['email']
                    msg['Message-ID'] = f'<{tracking_id}@outreachos>'
                    if reply_to and reply_to.strip(): msg['Reply-To'] = reply_to
                    if bcc and bcc.strip(): msg['Bcc'] = bcc
                    msg.add_alternative(tracked_body, subtype='html')

                    if attachment_filename and os.path.exists(os.path.join(UPLOAD_DIR, attachment_filename)):
                        filepath = os.path.join(UPLOAD_DIR, attachment_filename)
                        mime_type, _ = mimetypes.guess_type(filepath)
                        maintype, subtype = (mime_type.split('/', 1) if mime_type else ('application', 'octet-stream'))
                        with open(filepath, 'rb') as f:
                            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(filepath))

                    server.send_message(msg)
                    server.quit()
                    conn.execute("""
                        INSERT INTO emails_sent (campaign_id, contact_id, email, subject, body, status, tracking_id, sent_at, workspace_id)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (campaign_id, cid, contact['email'], subject, body, 'sent', tracking_id, datetime.now(), wid))
                    conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (cid,))
                    conn.commit()
                    sent += 1
                    try:
                        thread_id = get_or_create_thread(cid, campaign_id, subject)
                        insert_message(thread_id=thread_id, direction='outgoing', sender_email=from_email,
                                       recipient_email=contact['email'], subject=subject, body=body, message_id=tracking_id)
                    except Exception:
                        pass
                    if account_id:
                        mark_send_success(account_id)
                    smtp_logger.info(f'SENT | Campaign {campaign_id} | To: {contact["email"]} | Subject: {subject[:50]}')
                    time.sleep(5)

                except smtplib.SMTPRecipientsRefused as e:
                    conn.execute("""
                        INSERT INTO emails_sent (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (campaign_id, cid, contact['email'], subject, body, 'bounced', str(e), datetime.now()))
                    conn.commit()
                    failed += 1
                    if account_id: mark_send_failure(account_id)
                    smtp_logger.warning(f'BOUNCED | {contact["email"]} | {str(e)[:100]}')
                    try: update_lead_score(cid, 'bounce')
                    except Exception: pass

                except Exception as e:
                    conn.execute("""
                        INSERT INTO emails_sent (campaign_id, contact_id, email, subject, body, status, bounce_reason, sent_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (campaign_id, cid, contact['email'], subject, body, 'failed', str(e), datetime.now()))
                    conn.commit()
                    failed += 1
                    if account_id: mark_send_failure(account_id)
                    smtp_logger.error(f'FAILED | {contact["email"]} | {str(e)[:100]}')
                    error_logger.error(f'Send failed for {contact["email"]}: {str(e)}')

    except Exception as e:
        flash(f'SMTP Error: {e}', 'error')
        error_logger.error(f'SMTP connection error in campaign {campaign_id}: {str(e)}')
        conn.close()
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))

    if sent > 0:
        conn.execute("UPDATE campaigns SET status='sent' WHERE id=?", (campaign_id,))
    conn.commit()
    conn.close()
    flash(f'Sent: {sent}, Failed: {failed}', 'success')
    return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))


# ── RETRY ─────────────────────────────────────────────────────────
@campaigns_bp.route('/retry/<int:email_id>', methods=['POST'])
@login_required
def retry_email(email_id):
    from app import get_db, get_setting, _get_reply_to
    from utils.ownership import owns_email_sent
    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account

    conn = get_db()
    record = owns_email_sent(email_id)
    if not record:
        flash('Email record not found', 'error')
        conn.close()
        return redirect(url_for('dash.dashboard'))

    already_sent = conn.execute(
        "SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent' AND id!=?",
        (record['email'], record['campaign_id'], email_id)
    ).fetchone()
    if already_sent:
        flash(f'{record["email"]} already sent in this campaign!', 'error')
        conn.close()
        return redirect(url_for('campaigns.campaign_detail', campaign_id=record['campaign_id']))

    wid = get_wid()
    account = get_next_smtp_account(workspace_id=wid)
    if account:
        smtp_server = account['smtp_server']
        smtp_port = int(account['smtp_port'])
        smtp_login = account['login_username'] or account['email']
        smtp_password = account['password']
        from_email = account['from_email'] or account['email']
        from_name = account.get('from_name', '')
        reply_to = account.get('reply_to') or _get_reply_to()
        bcc = account.get('bcc_emails', '')
    else:
        smtp_server = get_setting('smtp_server')
        smtp_port = int(get_setting('smtp_port') or 587)
        smtp_login = get_setting('smtp_username')
        smtp_password = get_setting('smtp_password')
        from_email = get_setting('from_email') or smtp_login
        from_name = get_setting('from_name')
        reply_to = get_setting('reply_to') or from_email
        bcc = get_setting('bcc_emails')

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_login, smtp_password)
        msg = EmailMessage()
        msg['Subject'] = record['subject']
        msg['From'] = formataddr((from_name, from_email))
        msg['To'] = record['email']
        if reply_to and reply_to.strip(): msg['Reply-To'] = reply_to
        if bcc and bcc.strip(): msg['Bcc'] = bcc
        msg.add_alternative(record['body'], subtype='html')
        server.send_message(msg)
        server.quit()
        conn.execute("UPDATE emails_sent SET status='sent', bounce_reason=NULL, sent_at=? WHERE id=?",
                     (datetime.now(), email_id))
        conn.commit()
        flash(f'Retry successful! Email sent to {record["email"]}', 'success')
    except Exception as e:
        conn.execute("UPDATE emails_sent SET bounce_reason=? WHERE id=?", (str(e), email_id))
        conn.commit()
        flash(f'Retry failed: {str(e)[:100]}', 'error')
    conn.close()
    return redirect(url_for('campaigns.campaign_detail', campaign_id=record['campaign_id']))


@campaigns_bp.route('/api/retry/<int:email_id>', methods=['POST'])
@login_required
def api_retry_email(email_id):
    from app import get_db, get_setting, _get_reply_to
    from utils.ownership import owns_email_sent
    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account

    record = owns_email_sent(email_id)
    if not record:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    conn = get_db()
    already_sent = conn.execute(
        "SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent' AND id!=?",
        (record['email'], record['campaign_id'], email_id)
    ).fetchone()
    if already_sent:
        conn.close()
        return jsonify({'success': False, 'error': 'Already sent in this campaign'})

    wid = get_wid()
    account = get_next_smtp_account(workspace_id=wid)
    if account:
        smtp_server, smtp_port = account['smtp_server'], int(account['smtp_port'])
        smtp_login = account['login_username'] or account['email']
        smtp_password, from_email = account['password'], account['from_email'] or account['email']
        from_name = account.get('from_name', '')
        reply_to, bcc = account.get('reply_to') or _get_reply_to(), account.get('bcc_emails', '')
    else:
        smtp_server = get_setting('smtp_server')
        smtp_port = int(get_setting('smtp_port') or 587)
        smtp_login = get_setting('smtp_username')
        smtp_password = get_setting('smtp_password')
        from_email = get_setting('from_email') or smtp_login
        from_name = get_setting('from_name')
        reply_to = get_setting('reply_to') or from_email
        bcc = get_setting('bcc_emails')

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_login, smtp_password)
        msg = EmailMessage()
        msg['Subject'] = record['subject']
        msg['From'] = formataddr((from_name, from_email))
        msg['To'] = record['email']
        if reply_to and reply_to.strip(): msg['Reply-To'] = reply_to
        if bcc and bcc.strip(): msg['Bcc'] = bcc
        msg.add_alternative(record['body'], subtype='html')
        server.send_message(msg)
        server.quit()
        conn.execute("UPDATE emails_sent SET status='sent', bounce_reason=NULL, sent_at=? WHERE id=?",
                     (datetime.now(), email_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        conn.execute("UPDATE emails_sent SET bounce_reason=? WHERE id=?", (str(e)[:200], email_id))
        conn.commit()
        conn.close()
        return jsonify({'success': False, 'error': str(e)[:100]})


# ── AI SEND ───────────────────────────────────────────────────────
@campaigns_bp.route('/campaign/<int:campaign_id>/send_ai', methods=['POST'])
@login_required
def send_campaign_ai(campaign_id):
    from app import (get_db, get_setting, inject_tracking_pixel, is_unsubscribed,
                     _get_reply_to, app_logger, error_logger, smtp_logger,
                     UPLOAD_DIR, _get_send_progress, _set_send_progress,
                     ai_generated_cache, generate_ai_email)
    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account, append_signature, mark_send_success, mark_send_failure

    uid = current_user.id
    prog = _get_send_progress(uid)
    if prog['running']:
        flash('Sending already in progress!', 'error')
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))

    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    attachment = request.form.get('attachment', '')
    contact_ids = request.form.getlist('contact_ids')

    if not contact_ids:
        flash('No contacts selected. Please select at least one contact.', 'error')
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))

    def run_send_ai():
        prog = {'running': True, 'total': len(contact_ids), 'done': 0, 'sent': 0, 'failed': 0, 'current': '', 'campaign_id': campaign_id}
        _set_send_progress(uid, prog)
        prompt_template = get_setting('email_prompt')
        wid = get_wid()
        conn = get_db()

        def _get_smtp_creds():
            account = get_next_smtp_account(workspace_id=wid)
            if account:
                return account
            return {
                'smtp_server': get_setting('smtp_server'), 'smtp_port': int(get_setting('smtp_port') or 587),
                'login_username': get_setting('smtp_username'), 'password': get_setting('smtp_password'),
                'from_email': get_setting('from_email') or get_setting('smtp_username'),
                'from_name': get_setting('from_name'), 'reply_to': get_setting('reply_to'),
                'bcc_emails': get_setting('bcc_emails'), 'signature': '', 'email': get_setting('from_email') or get_setting('smtp_username'),
                'account_id': None, 'id': None,
            }

        creds = _get_smtp_creds()
        smtp_server_addr = creds.get('smtp_server')
        smtp_port_num = int(creds.get('smtp_port') or 587)
        smtp_login = creds.get('login_username') or creds.get('email')
        smtp_password = creds['password']
        from_email = creds.get('from_email') or creds.get('email')
        from_name = creds.get('from_name', '')
        reply_to = creds.get('reply_to') or _get_reply_to()
        bcc = creds.get('bcc_emails') or get_setting('bcc_emails')
        signature = creds.get('signature', '')
        account_id = creds.get('account_id') or creds.get('id')

        server = None
        try:
            server = smtplib.SMTP(smtp_server_addr, smtp_port_num)
            server.starttls()
            server.login(smtp_login, smtp_password)
        except Exception as e:
            error_logger.error(f'[AI SEND] SMTP login failed: {smtp_login}@{smtp_server_addr}:{smtp_port_num} — {e}')
            prog['running'] = False
            _set_send_progress(uid, prog)
            return

        for i, cid in enumerate(contact_ids):
            if i > 0 and i % 10 == 0:
                try: server.quit()
                except Exception: pass
                try:
                    creds = _get_smtp_creds()
                    smtp_server_addr = creds.get('smtp_server')
                    smtp_port_num = int(creds.get('smtp_port') or 587)
                    smtp_login = creds.get('login_username') or creds.get('email')
                    smtp_password = creds['password']
                    from_email = creds.get('from_email') or creds.get('email')
                    from_name = creds.get('from_name', '')
                    reply_to = creds.get('reply_to') or _get_reply_to()
                    bcc = creds.get('bcc_emails') or get_setting('bcc_emails')
                    signature = creds.get('signature', '')
                    account_id = creds.get('account_id') or creds.get('id')
                    server = smtplib.SMTP(smtp_server_addr, smtp_port_num)
                    server.starttls()
                    server.login(smtp_login, smtp_password)
                except Exception: pass

            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
            if not contact: continue
            if is_unsubscribed(contact['email']):
                prog['done'] += 1
                _set_send_progress(uid, prog)
                continue
            already = conn.execute("SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent'", (contact['email'], campaign_id)).fetchone()
            if already:
                prog['done'] += 1
                _set_send_progress(uid, prog)
                continue

            prog['current'] = f"{contact['name']} ({contact['email']})"
            _set_send_progress(uid, prog)
            subject = subject_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')

            if str(cid) in ai_generated_cache:
                body = ai_generated_cache.pop(str(cid))
            else:
                context = contact['context'] if 'context' in contact.keys() else ''
                if not context:
                    conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                        (campaign_id, cid, contact['email'], subject, '', 'failed', 'No context - fetch context first', datetime.now(), wid))
                    conn.commit()
                    prog['done'] += 1
                    prog['failed'] += 1
                    _set_send_progress(uid, prog)
                    continue
                designation = contact['designation'] if 'designation' in contact.keys() else ''
                body, error = generate_ai_email(contact['name'], contact['company'], prompt_template, context, designation)
                if not body:
                    conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                        (campaign_id, cid, contact['email'], subject, '', 'failed', f'AI: {error}', datetime.now(), wid))
                    conn.commit()
                    prog['done'] += 1
                    prog['failed'] += 1
                    _set_send_progress(uid, prog)
                    continue

            try:
                tracking_id = str(uuid.uuid4())
                body_with_sig = append_signature(body, signature)
                tracked_body = inject_tracking_pixel(body_with_sig, tracking_id)

                msg = EmailMessage()
                msg['Subject'] = subject
                msg['From'] = formataddr((from_name, from_email))
                msg['To'] = contact['email']
                if reply_to: msg['Reply-To'] = reply_to
                if bcc and bcc.strip(): msg['Bcc'] = bcc
                msg.add_alternative(tracked_body, subtype='html')

                if attachment and os.path.exists(os.path.join(UPLOAD_DIR, attachment)):
                    filepath = os.path.join(UPLOAD_DIR, attachment)
                    mt, _ = mimetypes.guess_type(filepath)
                    maintype, subtype = (mt.split('/', 1) if mt else ('application', 'octet-stream'))
                    with open(filepath, 'rb') as f:
                        msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(filepath))

                server.send_message(msg)
                conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,tracking_id,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (campaign_id, cid, contact['email'], subject, body, 'sent', tracking_id, datetime.now(), wid))
                conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (cid,))
                conn.commit()
                prog['sent'] += 1
                if account_id: mark_send_success(account_id)
            except Exception as e:
                conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (campaign_id, cid, contact['email'], subject, body if 'body' in dir() else '', 'failed', str(e)[:200], datetime.now(), wid))
                conn.commit()
                prog['failed'] += 1
                if account_id: mark_send_failure(account_id)

            prog['done'] += 1
            _set_send_progress(uid, prog)
            time.sleep(5)

        try: server.quit()
        except Exception: pass
        if prog['sent'] > 0:
            conn.execute("UPDATE campaigns SET status='sent' WHERE id=?", (campaign_id,))
            conn.commit()
        conn.close()
        prog['running'] = False
        prog['current'] = ''
        _set_send_progress(uid, prog)

    t = threading.Thread(target=run_send_ai)
    t.start()
    return redirect(url_for('campaigns.send_progress_page', campaign_id=campaign_id))


# ── SEND STATUS ───────────────────────────────────────────────────
@campaigns_bp.route('/api/send_status')
@login_required
def api_send_status():
    from app import get_db, _get_send_progress
    prog = _get_send_progress(current_user.id)
    conn = get_db()
    recent = []
    if prog['campaign_id']:
        rows = conn.execute("""
            SELECT es.email, es.status, es.bounce_reason, c.name, c.company
            FROM emails_sent es JOIN contacts c ON es.contact_id=c.id
            WHERE es.campaign_id=? ORDER BY es.sent_at DESC LIMIT 50
        """, (prog['campaign_id'],)).fetchall()
        recent = [{'name': r['name'], 'company': r['company'], 'email': r['email'], 'status': r['status'], 'reason': r['bounce_reason'] or ''} for r in rows]
    conn.close()
    return jsonify({
        'running': prog['running'], 'total': prog['total'], 'done': prog['done'],
        'sent': prog['sent'], 'failed': prog['failed'], 'current': prog['current'], 'recent': recent
    })


# ── LAUNCH / PAUSE / RESUME / CANCEL ─────────────────────────────
@campaigns_bp.route('/campaign/<int:campaign_id>/launch', methods=['POST'])
@login_required
def launch_campaign_route(campaign_id):
    from app import get_db, app_logger, UPLOAD_DIR
    from services.campaign_executor import launch_campaign
    from services.workspace_service import get_wid

    wid = get_wid()
    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    body_template = request.form.get('body', '')
    send_mode = request.form.get('send_mode', 'template')
    contact_ids = [int(x) for x in request.form.getlist('contact_ids') if x.isdigit()]

    attachment_path = ''
    uploaded = request.files.get('attachment_file')
    if uploaded and uploaded.filename:
        from werkzeug.utils import secure_filename
        fname = secure_filename(uploaded.filename)
        attachment_path = os.path.join(UPLOAD_DIR, fname)
        uploaded.save(attachment_path)
    elif request.form.get('attachment'):
        attachment_path = os.path.join(UPLOAD_DIR, request.form.get('attachment'))

    if not contact_ids:
        flash('No contacts selected. Please select at least one contact.', 'error')
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))

    result = launch_campaign(campaign_id, contact_ids, subject_template, body_template, send_mode, wid, attachment_path)
    app_logger.info(f'Campaign {campaign_id} launched | {len(contact_ids)} contacts | mode={send_mode} | {result["mode"]}')
    return redirect(url_for('campaigns.send_progress_page', campaign_id=campaign_id))


@campaigns_bp.route('/api/campaign/<int:campaign_id>/status')
@login_required
def api_campaign_execution_status(campaign_id):
    from services.campaign_executor import get_campaign_status
    return jsonify(get_campaign_status(campaign_id))


@campaigns_bp.route('/api/campaign/<int:campaign_id>/pause', methods=['POST'])
@login_required
def api_pause_campaign(campaign_id):
    from app import get_db
    from services.campaign_executor import pause_campaign
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()
    camp = conn.execute('SELECT id FROM campaigns WHERE id=? AND workspace_id=?', (campaign_id, wid)).fetchone()
    conn.close()
    if not camp:
        return jsonify({'success': False, 'error': 'Campaign not found'}), 404
    pause_campaign(campaign_id, wid)
    return jsonify({'success': True, 'status': 'paused'})


@campaigns_bp.route('/api/campaign/<int:campaign_id>/resume', methods=['POST'])
@login_required
def api_resume_campaign(campaign_id):
    from app import get_db
    from services.campaign_executor import resume_campaign
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()
    camp = conn.execute('SELECT id FROM campaigns WHERE id=? AND workspace_id=?', (campaign_id, wid)).fetchone()
    conn.close()
    if not camp:
        return jsonify({'success': False, 'error': 'Campaign not found'}), 404
    result = resume_campaign(campaign_id, wid)
    return jsonify({'success': bool(result)})


@campaigns_bp.route('/api/campaign/<int:campaign_id>/cancel', methods=['POST'])
@login_required
def api_cancel_campaign(campaign_id):
    from app import get_db
    from services.campaign_executor import cancel_campaign
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()
    camp = conn.execute('SELECT id FROM campaigns WHERE id=? AND workspace_id=?', (campaign_id, wid)).fetchone()
    conn.close()
    if not camp:
        return jsonify({'success': False, 'error': 'Campaign not found'}), 404
    cancel_campaign(campaign_id, wid)
    return jsonify({'success': True, 'status': 'cancelled'})


@campaigns_bp.route('/campaign/<int:campaign_id>/sending')
@login_required
def send_progress_page(campaign_id):
    from app import get_db
    conn = get_db()
    campaign = conn.execute('SELECT * FROM campaigns WHERE id=?', (campaign_id,)).fetchone()
    conn.close()
    return render_template('send_progress.html', campaign_id=campaign_id, campaign=campaign)


@campaigns_bp.route('/campaign/<int:campaign_id>/status')
@login_required
def campaign_status_page(campaign_id):
    from app import get_db
    conn = get_db()
    campaign = conn.execute('SELECT * FROM campaigns WHERE id=?', (campaign_id,)).fetchone()
    conn.close()
    if not campaign:
        flash('Campaign not found', 'error')
        return redirect(url_for('campaigns.campaigns_list'))
    return render_template('campaign_status.html', campaign_id=campaign_id, campaign=campaign)
