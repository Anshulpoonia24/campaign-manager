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


# ── CAMPAIGN LIST ─────────────────────────────────────────────────
@campaigns_bp.route('/campaigns')
@login_required
def campaigns_list():
    from app import get_db
    from services.workspace_service import get_wid, ws_campaigns
    wid = get_wid()
    campaigns = ws_campaigns(wid)
    conn = get_db()
    try:
        meetings = {}
        for camp in campaigns:
            row = conn.execute(
                "SELECT COUNT(*) FROM threads WHERE campaign_id=? AND status='meeting' AND workspace_id=?",
                (camp['id'], wid)
            ).fetchone()
            meetings[camp['id']] = row[0] if row else 0
        return render_template('campaigns.html', campaigns=campaigns, meetings=meetings)
    finally:
        conn.close()


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
        try:
            if is_postgres():
                row = conn.execute(
                    "INSERT INTO campaigns (name, description, workspace_id) VALUES (?,?,?) RETURNING id",
                    (name, description, wid)
                ).fetchone()
                campaign_id = row[0] if row else None
                conn.commit()
            else:
                conn.execute("INSERT INTO campaigns (name, description, workspace_id) VALUES (?,?,?)", (name, description, wid))
                conn.commit()
                campaign_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()
        if campaign_id:
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
    try:
        conn.execute("UPDATE campaigns SET name=?, description=? WHERE id=?", (name, description, campaign_id))
        conn.commit()
    finally:
        conn.close()
    flash('Campaign updated!', 'success')
    return redirect(url_for('campaigns.campaigns_list'))


@campaigns_bp.route('/campaign/<int:campaign_id>')
@login_required
def campaign_detail(campaign_id):
    from app import get_db
    conn = get_db()
    try:
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
    finally:
        conn.close()
    return render_template('campaign_detail.html', campaign=campaign, emails=emails, available=available)


# ── TEMPLATE SEND ─────────────────────────────────────────────────
@campaigns_bp.route('/campaign/<int:campaign_id>/send', methods=['POST'])
@login_required
def send_campaign(campaign_id):
    from app import (get_db, get_setting, inject_tracking_pixel, is_unsubscribed,
                     _get_reply_to, app_logger, smtp_logger, error_logger,
                     UPLOAD_DIR, _get_campaign_lock)
    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account, append_signature, mark_send_success, mark_send_failure
    from services.inbox_service import get_or_create_thread, insert_message
    from services.lead_scoring import update_lead_score

    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    body_template    = request.form.get('body', '')
    contact_ids      = request.form.getlist('contact_ids')

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
        flash('No contacts selected.', 'error')
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))

    wid  = get_wid()
    conn = get_db()
    sent = failed = 0

    def _get_creds():
        account = get_next_smtp_account(workspace_id=wid)
        if account:
            return account
        return {
            'smtp_server':   get_setting('smtp_server'),
            'smtp_port':     int(get_setting('smtp_port') or 587),
            'login_username': get_setting('smtp_username'),
            'password':      get_setting('smtp_password'),
            'from_email':    get_setting('from_email') or get_setting('smtp_username'),
            'from_name':     get_setting('from_name'),
            'reply_to':      get_setting('reply_to'),
            'bcc_emails':    get_setting('bcc_emails'),
            'signature':     '',
            'account_id':    None,
        }

    try:
        app_logger.info(f'Campaign {campaign_id} send | {len(contact_ids)} contacts | {current_user.username}')
        for cid in contact_ids:
            creds       = _get_creds()
            smtp_server = creds.get('smtp_server') or ''
            smtp_port   = int(creds.get('smtp_port') or 587)
            smtp_login  = creds.get('login_username') or creds.get('email') or ''
            smtp_pw     = creds.get('password') or ''
            from_email  = creds.get('from_email') or smtp_login
            from_name   = creds.get('from_name') or ''
            account_id  = creds.get('account_id') or creds.get('id')
            reply_to    = creds.get('reply_to') or _get_reply_to()
            bcc         = creds.get('bcc_emails') or ''
            signature   = creds.get('signature') or ''

            if not smtp_server or not smtp_login or not smtp_pw:
                error_logger.error(f'[CAMPAIGN] Missing SMTP config for campaign {campaign_id}')
                failed += 1
                continue

            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
            if not contact or is_unsubscribed(contact['email']):
                continue

            with _get_campaign_lock(campaign_id):
                if conn.execute(
                    "SELECT id FROM emails_sent WHERE contact_id=? AND campaign_id=? AND status='sent'",
                    (cid, campaign_id)
                ).fetchone():
                    continue

                subject = subject_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')
                body    = body_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')

                try:
                    server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
                    server.starttls()
                    server.login(smtp_login, smtp_pw)
                    tracking_id  = str(uuid.uuid4())
                    tracked_body = inject_tracking_pixel(append_signature(body, signature), tracking_id)
                    msg = EmailMessage()
                    msg['Subject']    = subject
                    msg['From']       = formataddr((from_name, from_email))
                    msg['To']         = contact['email']
                    msg['Message-ID'] = f'<{tracking_id}@outreachos>'
                    if reply_to: msg['Reply-To'] = reply_to
                    if bcc:      msg['Bcc']      = bcc
                    msg.add_alternative(tracked_body, subtype='html')
                    if attachment_filename:
                        fp = os.path.join(UPLOAD_DIR, attachment_filename)
                        if os.path.exists(fp):
                            mt, _ = mimetypes.guess_type(fp)
                            mt_main, mt_sub = (mt.split('/', 1) if mt else ('application', 'octet-stream'))
                            with open(fp, 'rb') as f:
                                msg.add_attachment(f.read(), maintype=mt_main, subtype=mt_sub, filename=os.path.basename(fp))
                    server.send_message(msg)
                    server.quit()
                    conn.execute("""
                        INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,tracking_id,sent_at,workspace_id)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (campaign_id, cid, contact['email'], subject, body, 'sent', tracking_id, datetime.now(), wid))
                    conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (cid,))
                    conn.commit()
                    sent += 1
                    try:
                        tid = get_or_create_thread(cid, campaign_id, subject)
                        insert_message(thread_id=tid, direction='outgoing', sender_email=from_email,
                                       recipient_email=contact['email'], subject=subject, body=body, message_id=tracking_id)
                    except Exception:
                        pass
                    if account_id: mark_send_success(account_id)
                    smtp_logger.info(f'SENT | {contact["email"]} | campaign={campaign_id}')
                    time.sleep(5)

                except smtplib.SMTPRecipientsRefused as e:
                    conn.execute("""INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at)
                        VALUES (?,?,?,?,?,?,?,?)""", (campaign_id, cid, contact['email'], subject, body, 'bounced', str(e)[:200], datetime.now()))
                    conn.commit()
                    failed += 1
                    if account_id: mark_send_failure(account_id)
                    try: update_lead_score(cid, 'bounce')
                    except Exception: pass

                except Exception as e:
                    error_logger.exception(f'[CAMPAIGN] Send failed {contact["email"]}: {e}')
                    conn.execute("""INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at)
                        VALUES (?,?,?,?,?,?,?,?)""", (campaign_id, cid, contact['email'], subject, body, 'failed', str(e)[:200], datetime.now()))
                    conn.commit()
                    failed += 1
                    if account_id: mark_send_failure(account_id)

        if sent > 0:
            conn.execute("UPDATE campaigns SET status='sent' WHERE id=?", (campaign_id,))
            conn.commit()

    except Exception as e:
        error_logger.exception(f'[CAMPAIGN] Outer error campaign={campaign_id}: {e}')
        flash(f'Error: {str(e)[:100]}', 'error')
    finally:
        conn.close()

    flash(f'Sent: {sent}, Failed: {failed}', 'success')
    return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))


# ── RETRY ─────────────────────────────────────────────────────────
@campaigns_bp.route('/retry/<int:email_id>', methods=['POST'])
@login_required
def retry_email(email_id):
    from app import get_db, get_setting, _get_reply_to, error_logger
    from utils.ownership import owns_email_sent
    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account

    record = owns_email_sent(email_id)
    if not record:
        flash('Email record not found', 'error')
        return redirect(url_for('dash.dashboard'))

    conn = get_db()
    try:
        if conn.execute(
            "SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent' AND id!=?",
            (record['email'], record['campaign_id'], email_id)
        ).fetchone():
            flash(f'{record["email"]} already sent!', 'error')
            return redirect(url_for('campaigns.campaign_detail', campaign_id=record['campaign_id']))

        wid     = get_wid()
        account = get_next_smtp_account(workspace_id=wid)
        if account:
            smtp_server = account['smtp_server']
            smtp_port   = int(account['smtp_port'] or 587)
            smtp_login  = account.get('login_username') or account['email']
            smtp_pw     = account['password']
            from_email  = account.get('from_email') or account['email']
            from_name   = account.get('from_name', '')
            reply_to    = account.get('reply_to') or _get_reply_to()
            bcc         = account.get('bcc_emails', '')
        else:
            smtp_server = get_setting('smtp_server')
            smtp_port   = int(get_setting('smtp_port') or 587)
            smtp_login  = get_setting('smtp_username')
            smtp_pw     = get_setting('smtp_password')
            from_email  = get_setting('from_email') or smtp_login
            from_name   = get_setting('from_name') or ''
            reply_to    = get_setting('reply_to') or from_email
            bcc         = get_setting('bcc_emails') or ''

        try:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            server.starttls()
            server.login(smtp_login, smtp_pw)
            msg = EmailMessage()
            msg['Subject'] = record['subject']
            msg['From']    = formataddr((from_name, from_email))
            msg['To']      = record['email']
            if reply_to: msg['Reply-To'] = reply_to
            if bcc:      msg['Bcc']      = bcc
            msg.add_alternative(record['body'], subtype='html')
            server.send_message(msg)
            server.quit()
            conn.execute("UPDATE emails_sent SET status='sent', bounce_reason=NULL, sent_at=? WHERE id=?",
                         (datetime.now(), email_id))
            conn.commit()
            flash(f'Retry successful! Sent to {record["email"]}', 'success')
        except Exception as e:
            error_logger.exception(f'[RETRY] Failed {record["email"]}: {e}')
            conn.execute("UPDATE emails_sent SET bounce_reason=? WHERE id=?", (str(e)[:200], email_id))
            conn.commit()
            flash(f'Retry failed: {str(e)[:100]}', 'error')
    finally:
        conn.close()
    return redirect(url_for('campaigns.campaign_detail', campaign_id=record['campaign_id']))


@campaigns_bp.route('/api/retry/<int:email_id>', methods=['POST'])
@login_required
def api_retry_email(email_id):
    from app import get_db, get_setting, _get_reply_to, error_logger
    from utils.ownership import owns_email_sent
    from services.workspace_service import get_wid
    from services.smtp_rotation import get_next_smtp_account

    record = owns_email_sent(email_id)
    if not record:
        return jsonify({'success': False, 'error': 'Not found'}), 404

    conn = get_db()
    try:
        if conn.execute(
            "SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent' AND id!=?",
            (record['email'], record['campaign_id'], email_id)
        ).fetchone():
            return jsonify({'success': False, 'error': 'Already sent'})

        wid     = get_wid()
        account = get_next_smtp_account(workspace_id=wid)
        if account:
            smtp_server = account['smtp_server']
            smtp_port   = int(account['smtp_port'] or 587)
            smtp_login  = account.get('login_username') or account['email']
            smtp_pw     = account['password']
            from_email  = account.get('from_email') or account['email']
            from_name   = account.get('from_name', '')
            reply_to    = account.get('reply_to') or _get_reply_to()
            bcc         = account.get('bcc_emails', '')
        else:
            smtp_server = get_setting('smtp_server')
            smtp_port   = int(get_setting('smtp_port') or 587)
            smtp_login  = get_setting('smtp_username')
            smtp_pw     = get_setting('smtp_password')
            from_email  = get_setting('from_email') or smtp_login
            from_name   = get_setting('from_name') or ''
            reply_to    = get_setting('reply_to') or from_email
            bcc         = get_setting('bcc_emails') or ''

        try:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            server.starttls()
            server.login(smtp_login, smtp_pw)
            msg = EmailMessage()
            msg['Subject'] = record['subject']
            msg['From']    = formataddr((from_name, from_email))
            msg['To']      = record['email']
            if reply_to: msg['Reply-To'] = reply_to
            if bcc:      msg['Bcc']      = bcc
            msg.add_alternative(record['body'], subtype='html')
            server.send_message(msg)
            server.quit()
            conn.execute("UPDATE emails_sent SET status='sent', bounce_reason=NULL, sent_at=? WHERE id=?",
                         (datetime.now(), email_id))
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            error_logger.exception(f'[API RETRY] {record["email"]}: {e}')
            conn.execute("UPDATE emails_sent SET bounce_reason=? WHERE id=?", (str(e)[:200], email_id))
            conn.commit()
            return jsonify({'success': False, 'error': str(e)[:100]})
    finally:
        conn.close()


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

    uid  = current_user.id
    prog = _get_send_progress(uid)
    if prog['running']:
        flash('Sending already in progress!', 'error')
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))

    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    attachment       = request.form.get('attachment', '')
    contact_ids      = request.form.getlist('contact_ids')

    if not contact_ids:
        flash('No contacts selected.', 'error')
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))

    def run_send_ai():
        prog = {'running': True, 'total': len(contact_ids), 'done': 0, 'sent': 0, 'failed': 0, 'current': '', 'campaign_id': campaign_id}
        _set_send_progress(uid, prog)
        prompt_template = get_setting('email_prompt')
        wid  = get_wid()
        conn = get_db()

        def _get_creds():
            account = get_next_smtp_account(workspace_id=wid)
            if account:
                return account
            return {
                'smtp_server':    get_setting('smtp_server'),
                'smtp_port':      int(get_setting('smtp_port') or 587),
                'login_username': get_setting('smtp_username'),
                'password':       get_setting('smtp_password'),
                'from_email':     get_setting('from_email') or get_setting('smtp_username'),
                'from_name':      get_setting('from_name'),
                'reply_to':       get_setting('reply_to'),
                'bcc_emails':     get_setting('bcc_emails'),
                'signature':      '',
                'account_id':     None,
            }

        creds          = _get_creds()
        smtp_addr      = creds.get('smtp_server') or ''
        smtp_port_num  = int(creds.get('smtp_port') or 587)
        smtp_login     = creds.get('login_username') or creds.get('email') or ''
        smtp_pw        = creds.get('password') or ''
        from_email     = creds.get('from_email') or smtp_login
        from_name      = creds.get('from_name') or ''
        reply_to       = creds.get('reply_to') or _get_reply_to()
        bcc            = creds.get('bcc_emails') or ''
        signature      = creds.get('signature') or ''
        account_id     = creds.get('account_id') or creds.get('id')

        server = None
        try:
            server = smtplib.SMTP(smtp_addr, smtp_port_num, timeout=10)
            server.starttls()
            server.login(smtp_login, smtp_pw)
        except Exception as e:
            error_logger.exception(f'[AI SEND] SMTP login failed: {e}')
            prog['running'] = False
            _set_send_progress(uid, prog)
            if conn:
                try: conn.close()
                except Exception: pass
            return

        try:
            for i, cid in enumerate(contact_ids):
                # Rotate SMTP every 10 emails
                if i > 0 and i % 10 == 0:
                    try: server.quit()
                    except Exception: pass
                    try:
                        creds      = _get_creds()
                        smtp_addr  = creds.get('smtp_server') or smtp_addr
                        smtp_port_num = int(creds.get('smtp_port') or smtp_port_num)
                        smtp_login = creds.get('login_username') or creds.get('email') or smtp_login
                        smtp_pw    = creds.get('password') or smtp_pw
                        from_email = creds.get('from_email') or smtp_login
                        from_name  = creds.get('from_name') or from_name
                        reply_to   = creds.get('reply_to') or _get_reply_to()
                        bcc        = creds.get('bcc_emails') or ''
                        signature  = creds.get('signature') or ''
                        account_id = creds.get('account_id') or creds.get('id')
                        server = smtplib.SMTP(smtp_addr, smtp_port_num, timeout=10)
                        server.starttls()
                        server.login(smtp_login, smtp_pw)
                    except Exception as e:
                        error_logger.exception(f'[AI SEND] SMTP rotate failed: {e}')

                contact = conn.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
                if not contact or is_unsubscribed(contact['email']):
                    prog['done'] += 1
                    _set_send_progress(uid, prog)
                    continue

                if conn.execute("SELECT id FROM emails_sent WHERE email=? AND campaign_id=? AND status='sent'",
                                (contact['email'], campaign_id)).fetchone():
                    prog['done'] += 1
                    _set_send_progress(uid, prog)
                    continue

                prog['current'] = f"{contact['name']} ({contact['email']})"
                _set_send_progress(uid, prog)
                subject = subject_template.replace('{company}', contact['company'] or '').replace('{name}', contact['name'] or '')

                if str(cid) in ai_generated_cache:
                    body = ai_generated_cache.pop(str(cid))
                else:
                    context     = (contact['context'] if 'context' in contact.keys() else '') or ''
                    designation = (contact['designation'] if 'designation' in contact.keys() else '') or ''
                    if not context:
                        conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                            (campaign_id, cid, contact['email'], subject, '', 'failed', 'No context', datetime.now(), wid))
                        conn.commit()
                        prog['done'] += 1; prog['failed'] += 1
                        _set_send_progress(uid, prog)
                        continue
                    body, err = generate_ai_email(contact['name'], contact['company'], prompt_template, context, designation)
                    if not body:
                        conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                            (campaign_id, cid, contact['email'], subject, '', 'failed', f'AI: {err}', datetime.now(), wid))
                        conn.commit()
                        prog['done'] += 1; prog['failed'] += 1
                        _set_send_progress(uid, prog)
                        continue

                try:
                    tracking_id  = str(uuid.uuid4())
                    tracked_body = inject_tracking_pixel(append_signature(body, signature), tracking_id)
                    msg = EmailMessage()
                    msg['Subject'] = subject
                    msg['From']    = formataddr((from_name, from_email))
                    msg['To']      = contact['email']
                    if reply_to: msg['Reply-To'] = reply_to
                    if bcc:      msg['Bcc']      = bcc
                    msg.add_alternative(tracked_body, subtype='html')
                    if attachment:
                        fp = os.path.join(UPLOAD_DIR, attachment)
                        if os.path.exists(fp):
                            mt, _ = mimetypes.guess_type(fp)
                            mt_main, mt_sub = (mt.split('/', 1) if mt else ('application', 'octet-stream'))
                            with open(fp, 'rb') as f:
                                msg.add_attachment(f.read(), maintype=mt_main, subtype=mt_sub, filename=os.path.basename(fp))
                    server.send_message(msg)
                    conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,tracking_id,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                        (campaign_id, cid, contact['email'], subject, body, 'sent', tracking_id, datetime.now(), wid))
                    conn.execute("UPDATE contacts SET status='sent' WHERE id=?", (cid,))
                    conn.commit()
                    prog['sent'] += 1
                    if account_id: mark_send_success(account_id)
                except Exception as e:
                    error_logger.exception(f'[AI SEND] {contact["email"]}: {e}')
                    conn.execute("INSERT INTO emails_sent (campaign_id,contact_id,email,subject,body,status,bounce_reason,sent_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
                        (campaign_id, cid, contact['email'], subject, body, 'failed', str(e)[:200], datetime.now(), wid))
                    conn.commit()
                    prog['failed'] += 1
                    if account_id: mark_send_failure(account_id)

                prog['done'] += 1
                _set_send_progress(uid, prog)
                time.sleep(5)

            if prog['sent'] > 0:
                conn.execute("UPDATE campaigns SET status='sent' WHERE id=?", (campaign_id,))
                conn.commit()
        finally:
            try: server.quit()
            except Exception: pass
            conn.close()
            prog['running'] = False
            prog['current'] = ''
            _set_send_progress(uid, prog)

    threading.Thread(target=run_send_ai, daemon=False).start()
    return redirect(url_for('campaigns.send_progress_page', campaign_id=campaign_id))


# ── SEND STATUS ───────────────────────────────────────────────────
@campaigns_bp.route('/api/send_status')
@login_required
def api_send_status():
    from app import get_db, _get_send_progress
    prog = _get_send_progress(current_user.id)
    conn = get_db()
    try:
        recent = []
        if prog['campaign_id']:
            rows = conn.execute("""
                SELECT es.email, es.status, es.bounce_reason, c.name, c.company
                FROM emails_sent es JOIN contacts c ON es.contact_id=c.id
                WHERE es.campaign_id=? ORDER BY es.sent_at DESC LIMIT 50
            """, (prog['campaign_id'],)).fetchall()
            recent = [{'name': r['name'], 'company': r['company'], 'email': r['email'],
                       'status': r['status'], 'reason': r['bounce_reason'] or ''} for r in rows]
    finally:
        conn.close()
    return jsonify({'running': prog['running'], 'total': prog['total'], 'done': prog['done'],
                    'sent': prog['sent'], 'failed': prog['failed'], 'current': prog['current'], 'recent': recent})


# ── LAUNCH / PAUSE / RESUME / CANCEL ─────────────────────────────
@campaigns_bp.route('/campaign/<int:campaign_id>/launch', methods=['POST'])
@login_required
def launch_campaign_route(campaign_id):
    from app import get_db, app_logger, UPLOAD_DIR
    from services.campaign_executor import launch_campaign
    from services.workspace_service import get_wid

    wid              = get_wid()
    subject_template = request.form.get('subject', 'Helping {company} scale engineering faster')
    body_template    = request.form.get('body', '')
    send_mode        = request.form.get('send_mode', 'template')
    contact_ids      = [int(x) for x in request.form.getlist('contact_ids') if x.isdigit()]

    attachment_path = ''
    uploaded = request.files.get('attachment_file')
    if uploaded and uploaded.filename:
        from werkzeug.utils import secure_filename
        fname = secure_filename(uploaded.filename)
        attachment_path = os.path.join(UPLOAD_DIR, fname)
        uploaded.save(attachment_path)
    elif request.form.get('attachment'):
        from werkzeug.utils import secure_filename
        safe_name = secure_filename(request.form.get('attachment', ''))
        if safe_name:
            attachment_path = os.path.join(UPLOAD_DIR, safe_name)
            # Path traversal guard
            if not os.path.abspath(attachment_path).startswith(os.path.abspath(UPLOAD_DIR)):
                attachment_path = ''

    if not contact_ids:
        flash('No contacts selected.', 'error')
        return redirect(url_for('campaigns.campaign_detail', campaign_id=campaign_id))

    result = launch_campaign(campaign_id, contact_ids, subject_template, body_template, send_mode, wid, attachment_path)
    app_logger.info(f'Campaign {campaign_id} launched | {len(contact_ids)} contacts | mode={send_mode} | {result.get("mode")}')
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
    wid  = get_wid()
    conn = get_db()
    try:
        camp = conn.execute('SELECT id FROM campaigns WHERE id=? AND workspace_id=?', (campaign_id, wid)).fetchone()
    finally:
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
    wid  = get_wid()
    conn = get_db()
    try:
        camp = conn.execute('SELECT id FROM campaigns WHERE id=? AND workspace_id=?', (campaign_id, wid)).fetchone()
    finally:
        conn.close()
    if not camp:
        return jsonify({'success': False, 'error': 'Campaign not found'}), 404
    return jsonify({'success': bool(resume_campaign(campaign_id, wid))})


@campaigns_bp.route('/api/campaign/<int:campaign_id>/cancel', methods=['POST'])
@login_required
def api_cancel_campaign(campaign_id):
    from app import get_db
    from services.campaign_executor import cancel_campaign
    from services.workspace_service import get_wid
    wid  = get_wid()
    conn = get_db()
    try:
        camp = conn.execute('SELECT id FROM campaigns WHERE id=? AND workspace_id=?', (campaign_id, wid)).fetchone()
    finally:
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
    try:
        campaign = conn.execute('SELECT * FROM campaigns WHERE id=?', (campaign_id,)).fetchone()
    finally:
        conn.close()
    return render_template('send_progress.html', campaign_id=campaign_id, campaign=campaign)


@campaigns_bp.route('/campaign/<int:campaign_id>/status')
@login_required
def campaign_status_page(campaign_id):
    from app import get_db
    conn = get_db()
    try:
        campaign = conn.execute('SELECT * FROM campaigns WHERE id=?', (campaign_id,)).fetchone()
    finally:
        conn.close()
    if not campaign:
        flash('Campaign not found', 'error')
        return redirect(url_for('campaigns.campaigns_list'))
    return render_template('campaign_status.html', campaign_id=campaign_id, campaign=campaign)
