"""
routes/contacts.py - Contact Management Routes
=================================================
Upload, add, edit, delete, verify, enrich, filter contacts.
"""
import time
import os
import threading
import requests as http_requests
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.db import get_db
import pandas as pd

contacts_bp = Blueprint("contacts_routes", __name__)


def _get_app_globals():
    """Lazy-load app-level globals to avoid circular imports."""
    from app import (
        app_logger, get_setting, call_groq, call_gemini,
        generate_ai_email, mx_cache, verify_email,
        CELERY_AVAILABLE, has_active_workers
    )
    return app_logger, get_setting, call_groq, call_gemini, generate_ai_email, mx_cache, verify_email, CELERY_AVAILABLE, has_active_workers


def _queue_enrich_all(force=False):
    try:
        from app import queue_enrich_all
        return queue_enrich_all(force)
    except Exception:
        return None


@contacts_bp.route('/add_contact', methods=['POST'])
@login_required
def add_contact():
    name = request.form.get('name', '').strip()
    company = request.form.get('company', '').strip()
    email = request.form.get('email', '').strip().lower()
    designation = request.form.get('designation', '').strip()
    website = request.form.get('website', '').strip()

    if not email or '@' not in email:
        flash('Please enter a valid email address.', 'error')
        return redirect(url_for('contacts_routes.upload_contacts'))

    if not name:
        name = email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
    else:
        name = name.strip().title()

    if not website:
        domain = email.split('@')[1]
        if domain not in ['gmail.com','yahoo.com','hotmail.com','outlook.com','live.com','icloud.com','protonmail.com','aol.com']:
            website = f'https://{domain}'

    conn = get_db()
    existing = conn.execute("SELECT id, name FROM contacts WHERE email=?", (email,)).fetchone()
    if existing:
        flash(f'{email} already exists as "{existing["name"]}"!', 'error')
    else:
        from services.workspace_service import get_wid
        wid = get_wid()
        conn.execute("INSERT OR IGNORE INTO contacts (name, company, email, designation, website, workspace_id) VALUES (?,?,?,?,?,?)",
                     (name, company, email, designation, website, wid))
        conn.commit()
        flash(f'{name} ({email}) added!', 'success')
    conn.close()
    return redirect(url_for('contacts_routes.contacts'))


@contacts_bp.route('/upload', methods=['GET', 'POST'])
@login_required
def upload_contacts():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not file.filename.endswith(('.xlsx', '.xls', '.csv')):
            flash('Please upload Excel or CSV file', 'error')
            return redirect(url_for('contacts_routes.upload_contacts'))

        # File size guard — 10MB max before pandas load
        file.seek(0, 2)
        size = file.tell()
        file.seek(0)
        if size > 10 * 1024 * 1024:
            flash('File too large. Maximum 10MB allowed.', 'error')
            return redirect(url_for('contacts_routes.upload_contacts'))

        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        # Smart column detection
        col_map = {}
        for col in df.columns:
            cl = col.lower().strip()
            # Name detection
            if not col_map.get('name'):
                if cl in ['name', 'full name', 'fullname', 'contact name', 'person name', 'founder name', 'ceo name', 'first name']:
                    col_map['name'] = col
                elif 'founder' in cl and 'co' not in cl:
                    col_map['name'] = col
                elif 'contact' in cl and 'name' in cl:
                    col_map['name'] = col
            # Company detection
            if not col_map.get('company'):
                if cl in ['company', 'company name', 'startup', 'startup name', 'organization', 'org', 'business', 'brand']:
                    col_map['company'] = col
                elif 'startup' in cl or 'company' in cl or 'organization' in cl or 'business' in cl:
                    col_map['company'] = col
            # Email detection
            if not col_map.get('email'):
                if 'email' in cl and 'secondary' not in cl and 'backup' not in cl and 'alternate' not in cl:
                    col_map['email'] = col
                elif cl in ['mail', 'e-mail', 'email id', 'email address']:
                    col_map['email'] = col
            # Designation detection
            if not col_map.get('designation'):
                if cl in ['designation', 'title', 'role', 'position', 'job title']:
                    col_map['designation'] = col
                elif 'designation' in cl or 'title' in cl or 'role' in cl or 'position' in cl:
                    col_map['designation'] = col
            # Priority detection
            if not col_map.get('priority'):
                if 'priority' in cl or 'importance' in cl or 'tier' in cl:
                    col_map['priority'] = col

        # Fallback: if no name found, check for any column with 'name' in it (but not company/startup)
        if 'name' not in col_map:
            for col in df.columns:
                cl = col.lower().strip()
                if 'name' in cl and 'company' not in cl and 'startup' not in cl and 'org' not in cl:
                    col_map['name'] = col
                    break

        # Fallback: if no email found, auto-detect by checking cell values
        if 'email' not in col_map:
            for col in df.columns:
                sample = df[col].dropna().astype(str).head(5)
                if sample.str.contains('@').any():
                    col_map['email'] = col
                    break

        # Fallback: if no company found, check for URL columns (website = company)
        if 'company' not in col_map:
            for col in df.columns:
                cl = col.lower().strip()
                if 'website' in cl or 'url' in cl or 'site' in cl:
                    col_map['company'] = col
                    break

        if 'email' not in col_map:
            flash('Email column not found! Please ensure your file has an email column.', 'error')
            return redirect(url_for('contacts_routes.upload_contacts'))

        # Show detected mapping
        mapping_info = ' | '.join([f"{k.upper()}: {v}" for k, v in col_map.items()])
        
        conn = get_db()
        added = 0
        skipped = 0
        skipped_names = []
        from services.workspace_service import get_wid
        wid = get_wid()
        for _, row in df.iterrows():
            name        = str(row.get(col_map.get('name', '')) or '').strip() if 'name' in col_map else ''
            email       = str(row.get(col_map['email']) or '').strip().lower()
            company     = str(row.get(col_map.get('company', '')) or '').strip() if 'company' in col_map else ''
            designation = str(row.get(col_map.get('designation', '')) or '').strip() if 'designation' in col_map else ''
            priority    = str(row.get(col_map.get('priority', '')) or '').strip() if 'priority' in col_map else ''

            # Fix nan values
            if name.lower() == 'nan': name = ''
            if company.lower() == 'nan': company = ''
            if designation.lower() == 'nan': designation = ''
            if priority.lower() == 'nan': priority = ''

            if not email or '@' not in email:
                skipped += 1
                continue

            # Handle multiple emails separated by ; or ,
            emails_list = [e.strip() for e in email.replace(',', ';').split(';') if '@' in e.strip()]
            if not emails_list:
                skipped += 1
                continue

            for single_email in emails_list:
                single_email = single_email.strip().lower()
                if not single_email or '@' not in single_email:
                    continue

                # If no name, extract from email
                contact_name = name if name else single_email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
                contact_name = contact_name.strip().title()

                existing = conn.execute("SELECT id, name FROM contacts WHERE email=?", (single_email,)).fetchone()
                if existing:
                    skipped += 1
                    skipped_names.append(f"{existing['name']} ({single_email})")
                    continue

                conn.execute(
                    "INSERT OR IGNORE INTO contacts (name, company, email, designation, priority, workspace_id) VALUES (?,?,?,?,?,?)",
                    (contact_name, company, single_email, designation, priority, wid)
                )
                added += 1

        conn.commit()
        conn.close()
        skip_info = ''
        if skipped_names:
            if len(skipped_names) <= 10:
                skip_info = ' | Duplicates: ' + ', '.join(skipped_names)
            else:
                skip_info = f' | Duplicates: {', '.join(skipped_names[:10])}... +{len(skipped_names)-10} more'
        flash(f'{added} contacts added, {skipped} skipped (duplicate/invalid){skip_info} | Detected: {mapping_info}', 'success')
        app_logger, *_ = _get_app_globals()
        app_logger.info(f'Upload: {added} added, {skipped} skipped | File: {file.filename} | by {current_user.username}')
        return redirect(url_for('contacts_routes.contacts'))

    return render_template('upload.html')


@contacts_bp.route('/api/contact/<int:contact_id>')
@login_required
def api_get_contact(contact_id):
    conn = get_db()
    c = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    conn.close()
    if not c:
        return jsonify({'error': 'Not found'})
    return jsonify({'name': c['name'], 'company': c['company'], 'email': c['email'], 'designation': c['designation'] or '', 'context': c['context'] if 'context' in c.keys() else '', 'website': c['website'] if 'website' in c.keys() else ''})


@contacts_bp.route('/contact/edit/<int:contact_id>', methods=['POST'])
@login_required
def edit_contact(contact_id):
    from utils.ownership import owns_contact
    if not owns_contact(contact_id):
        flash('Not found.', 'error')
        return redirect(url_for('contacts_routes.contacts'))
    name = request.form.get('name', '').strip().title()
    company = request.form.get('company', '').strip()
    email = request.form.get('email', '').strip().lower()
    designation = request.form.get('designation', '').strip()
    context = request.form.get('context', '').strip()
    website = request.form.get('website', '').strip()
    conn = get_db()
    conn.execute("UPDATE contacts SET name=?, company=?, email=?, designation=?, context=?, website=? WHERE id=?",
                 (name, company, email, designation, context, website, contact_id))
    conn.commit()
    conn.close()
    flash('Contact updated!', 'success')
    return redirect(url_for('contacts_routes.contacts'))


@contacts_bp.route('/api/contact/delete/<int:contact_id>', methods=['DELETE'])
@login_required
def delete_contact(contact_id):
    from utils.ownership import owns_contact
    if not owns_contact(contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    conn = get_db()
    conn.execute("DELETE FROM emails_sent WHERE contact_id=?", (contact_id,))
    conn.execute("DELETE FROM follow_ups WHERE contact_id=?", (contact_id,))
    conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@contacts_bp.route('/api/campaign/delete/<int:campaign_id>', methods=['DELETE'])
@login_required
def delete_campaign(campaign_id):
    from utils.ownership import owns_campaign
    if not owns_campaign(campaign_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    conn = get_db()
    conn.execute("DELETE FROM emails_sent WHERE campaign_id=?", (campaign_id,))
    conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ==============================
# CONTACT INTELLIGENCE API ROUTES
# ==============================

@contacts_bp.route('/api/contacts/filter')
@login_required
def api_contacts_filter():
    """Filter contacts with industry/country/size/score/enrichment filters."""
    from services.industry_detector import filter_contacts
    from services.workspace_service import get_wid
    wid = get_wid()
    filters = {
        'industry':    request.args.get('industry', ''),
        'country':     request.args.get('country', ''),
        'company_size':request.args.get('company_size', ''),
        'min_score':   request.args.get('min_score', ''),
        'enriched':    request.args.get('enriched', ''),
        'email_valid': request.args.get('email_valid', ''),
        'status':      request.args.get('status', ''),
        'search':      request.args.get('search', ''),
    }
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    result   = filter_contacts(wid, filters, page, per_page)
    # Serialize datetimes
    for c in result['contacts']:
        for k in ('created_at', 'last_enriched_at'):
            if c.get(k) and not isinstance(c[k], str):
                c[k] = str(c[k])
    return jsonify(result)


@contacts_bp.route('/api/contacts/industry_breakdown')
@login_required
def api_industry_breakdown():
    """Get contact count by industry."""
    from services.industry_detector import get_industry_breakdown
    from services.workspace_service import get_wid
    return jsonify({'breakdown': get_industry_breakdown(get_wid())})


@contacts_bp.route('/api/contacts/<int:contact_id>/enrich_intelligence', methods=['POST'])
@login_required
def api_enrich_intelligence(contact_id):
    from services.workspace_service import get_wid
    wid = get_wid()
    _, _, _, _, _, _, _, CELERY_AVAILABLE, has_active_workers = _get_app_globals()
    if CELERY_AVAILABLE and has_active_workers():
        from tasks.enrichment_tasks import enrich_single_contact
        result = enrich_single_contact.apply_async(
            args=[contact_id, True], queue='enrichment_queue'
        )
        return jsonify({'success': True, 'queued': True, 'task_id': result.id})
    # Sync fallback
    from services.industry_detector import enrich_contact_intelligence
    result = enrich_contact_intelligence(contact_id)
    return jsonify({'success': bool(result), 'data': result})


@contacts_bp.route('/api/contacts/<int:contact_id>/intelligence')
@login_required
def api_contact_intelligence(contact_id):
    """Get full intelligence profile for a contact."""
    conn = get_db()
    contact = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not contact:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    # Campaign history
    campaigns = conn.execute("""
        SELECT es.campaign_id, es.status, es.opened, es.replied,
               es.sent_at, c.name as campaign_name
        FROM emails_sent es
        LEFT JOIN campaigns c ON es.campaign_id = c.id
        WHERE es.contact_id=?
        ORDER BY es.sent_at DESC LIMIT 10
    """, (contact_id,)).fetchall()
    conn.close()
    from services.industry_detector import get_industry_style
    data = dict(contact)
    data['industry_style'] = get_industry_style(data.get('industry', ''))
    data['campaigns'] = [dict(c) for c in campaigns]
    for k in ('created_at', 'last_enriched_at'):
        if data.get(k) and not isinstance(data[k], str):
            data[k] = str(data[k])
    return jsonify(data)


@contacts_bp.route('/api/contacts/industries')
@login_required
def api_contact_industries():
    """Get distinct industries in workspace for filter dropdown."""
    from services.workspace_service import get_wid
    from services.industry_detector import INDUSTRIES
    wid = get_wid()
    conn = get_db()
    used = conn.execute("""
        SELECT DISTINCT industry FROM contacts
        WHERE workspace_id=? AND industry IS NOT NULL AND industry != ''
        ORDER BY industry
    """, (wid,)).fetchall()
    conn.close()
    used_list = [r['industry'] for r in used]
    return jsonify({'industries': used_list, 'all_industries': INDUSTRIES})


@contacts_bp.route('/api/contacts/bulk_enrich_intelligence', methods=['POST'])
@login_required
def api_bulk_enrich_intelligence():
    """Enrich all contacts with industry intelligence."""
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()
    contacts = conn.execute("""
        SELECT id FROM contacts
        WHERE workspace_id=? AND (enrichment_status='pending' OR enrichment_status IS NULL OR enrichment_status='')
        LIMIT 50
    """, (wid,)).fetchall()
    conn.close()
    contact_ids = [c['id'] for c in contacts]
    if not contact_ids:
        return jsonify({'success': True, 'message': 'All contacts already enriched', 'queued': 0})
    _, _, _, _, _, _, _, CELERY_AVAILABLE, has_active_workers = _get_app_globals()
    if CELERY_AVAILABLE and has_active_workers():
        from tasks.enrichment_tasks import enrich_single_contact
        for cid in contact_ids:
            enrich_single_contact.apply_async(args=[cid, False], queue='enrichment_queue')
        return jsonify({'success': True, 'queued': len(contact_ids)})
    # Sync fallback in thread
    import threading
    from services.industry_detector import enrich_contacts_bulk_intelligence
    t = threading.Thread(
        target=enrich_contacts_bulk_intelligence,
        args=[contact_ids, wid], daemon=False
    )
    t.start()
    return jsonify({'success': True, 'queued': len(contact_ids), 'mode': 'thread'})


@contacts_bp.route('/contacts')
@login_required
def contacts():
    from services.workspace_service import get_wid, ws_contacts
    wid = get_wid()
    filter_type = request.args.get('filter', 'all')
    rows = ws_contacts(wid, filter_type)
    return render_template('contacts.html', contacts=rows, filter_type=filter_type)


# Verification progress tracking
verify_progress = {'running': False, 'total': 0, 'done': 0, 'current_email': ''}



@contacts_bp.route('/verify_emails', methods=['POST'])
@login_required
def verify_emails_route():
    global verify_progress
    if verify_progress['running']:
        return redirect(url_for('contacts_routes.contacts'))

    reverify = request.form.get('reverify', '0')

    def run_verify(reverify_flag):
        global verify_progress
        _, _, _, _, _, mx_cache, verify_email, _, _ = _get_app_globals()
        mx_cache.clear()
        # Fetch contacts list with own connection
        conn = get_db()
        if reverify_flag == '1':
            contacts_list = [(r['id'], r['email']) for r in conn.execute("SELECT id, email FROM contacts").fetchall()]
        else:
            contacts_list = [(r['id'], r['email']) for r in conn.execute("SELECT id, email FROM contacts WHERE email_valid=-1").fetchall()]
        conn.close()
        verify_progress = {'running': True, 'total': len(contacts_list), 'done': 0, 'current_email': ''}

        def verify_one(item):
            cid, email = item
            valid, reason = verify_email(email)
            return cid, email, valid, reason

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(verify_one, item): item for item in contacts_list}
            for future in as_completed(futures):
                cid, email, valid, reason = future.result()
                verify_progress['current_email'] = email
                # Each write gets its own connection (PostgreSQL safe)
                wconn = get_db()
                try:
                    wconn.execute("UPDATE contacts SET email_valid=?, validation_reason=? WHERE id=?",
                                 (1 if valid else 0, reason, cid))
                    wconn.commit()
                finally:
                    wconn.close()
                verify_progress['done'] += 1

        verify_progress['running'] = False
        verify_progress['current_email'] = ''

    t = threading.Thread(target=run_verify, args=(reverify,))
    t.start()
    return redirect(url_for('contacts_routes.verify_progress_page'))


@contacts_bp.route('/verify_progress')
@login_required
def verify_progress_page():
    return render_template('verify_progress.html')


@contacts_bp.route('/api/verify_single/<int:contact_id>')
@login_required
def api_verify_single(contact_id):
    _, _, _, _, _, _, verify_email, _, _ = _get_app_globals()
    conn = get_db()
    contact = conn.execute("SELECT id, email FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not contact:
        conn.close()
        return jsonify({'valid': False, 'reason': 'Contact not found'})
    valid, reason = verify_email(contact['email'])
    conn.execute("UPDATE contacts SET email_valid=?, validation_reason=? WHERE id=?",
                 (1 if valid else 0, reason, contact['id']))
    conn.commit()
    conn.close()
    return jsonify({'valid': valid, 'reason': reason})


@contacts_bp.route('/api/fetch_context/<int:contact_id>')
@login_required
def api_fetch_context(contact_id):
    from utils.ownership import owns_contact
    contact = owns_contact(contact_id)  # returns row or None, handles its own conn
    if not contact:
        return jsonify({'success': False, 'error': 'Not found'})

    # Company-level cache: reuse context from same domain/company
    domain = contact['email'].split('@')[1] if contact['email'] and '@' in contact['email'] else ''
    company = (contact['company'] or '').strip()
    wid = getattr(current_user, 'workspace_id', 1)

    conn = get_db()
    if domain or company:
        existing = conn.execute("""
            SELECT context FROM contacts
            WHERE workspace_id=?
            AND (context IS NOT NULL AND context != '')
            AND (
                (? != '' AND email LIKE ?)
                OR (? != '' AND LOWER(company) = LOWER(?))
            )
            LIMIT 1
        """, (wid, domain, f'%@{domain}', company, company)).fetchone()
        if existing:
            conn.execute("UPDATE contacts SET context=? WHERE id=?", (existing['context'], contact_id))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'context': existing['context'], 'cached': True})

    prompt = f"""In 1-2 short bullet points, tell me the latest publicly known context about {contact['company']}.
Include: what they do, recent funding/news, tech stack, or growth stage.
Only use WELL KNOWN facts. If unsure, say what the company likely does based on name.
Keep it under 50 words. No fluff. Plain text, no markdown."""

    try:
        _, get_setting, call_groq, call_gemini, *_ = _get_app_globals()
        text, err = call_groq(prompt)
        if not text:
            text, err = call_gemini(prompt)
        if not text:
            conn.close()
            return jsonify({'success': False, 'error': err or 'AI generation failed'})

        text = text.strip()
        conn.execute("UPDATE contacts SET context=? WHERE id=?", (text, contact_id))
        conn.execute("INSERT INTO ai_usage (provider, purpose, success, workspace_id) VALUES ('groq','research',1,?)", (wid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'context': text})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)[:100]})


@contacts_bp.route('/api/fetch_all_context', methods=['POST'])
@login_required
def api_fetch_all_context():
    from services.workspace_service import get_wid
    contact_ids = request.json.get('contact_ids', [])
    wid = get_wid()
    results = []
    # company_cache: domain/company -> context already fetched this run
    company_cache = {}
    conn = get_db()
    for cid in contact_ids:
        contact = conn.execute("SELECT id, name, company, email FROM contacts WHERE id=? AND workspace_id=?", (cid, wid)).fetchone()
        if not contact:
            continue
        domain = contact['email'].split('@')[1] if contact['email'] and '@' in contact['email'] else ''
        company = (contact['company'] or '').strip().lower()
        cache_key = domain or company
        # Reuse if already fetched this run
        if cache_key and cache_key in company_cache:
            conn.execute("UPDATE contacts SET context=? WHERE id=?", (company_cache[cache_key], cid))
            conn.commit()
            results.append({'id': cid, 'context': company_cache[cache_key]})
            continue
        # Reuse if another contact in same workspace already has context
        if cache_key:
            existing = conn.execute("""
                SELECT context FROM contacts
                WHERE workspace_id=? AND (context IS NOT NULL AND context != '')
                AND (email LIKE ? OR LOWER(company)=?)
                AND id != ?
                LIMIT 1
            """, (wid, f'%@{domain}' if domain else '%', company, cid)).fetchone()
            if existing:
                company_cache[cache_key] = existing['context']
                conn.execute("UPDATE contacts SET context=? WHERE id=?", (existing['context'], cid))
                conn.commit()
                results.append({'id': cid, 'context': existing['context']})
                continue
        prompt = f"In 1-2 short bullet points (under 50 words), what does {contact['company']} do? Any recent funding or news? Only well-known facts. Plain text."
        try:
            _, _, call_groq, call_gemini, *_ = _get_app_globals()
            text, err = call_groq(prompt)
            if not text:
                text, err = call_gemini(prompt)
            if text:
                text = text.strip()
                if cache_key:
                    company_cache[cache_key] = text
                conn.execute("UPDATE contacts SET context=? WHERE id=?", (text, cid))
                conn.commit()
                results.append({'id': cid, 'context': text})
            time.sleep(0.5)
        except Exception:
            pass
    conn.close()
    return jsonify({'results': results})


def get_wid_safe():
    """Get workspace_id safely — works inside and outside request context."""
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return getattr(current_user, 'workspace_id', 1)
    except Exception:
        pass
    return 1


@contacts_bp.route('/api/enrich_all', methods=['POST'])
@login_required
def api_enrich_all():
    force = request.json.get('force', False) if request.json else False
    task_id = _queue_enrich_all(force)
    if task_id:
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM contacts WHERE email_valid=1").fetchone()[0]
        conn.close()
        return jsonify({'enriched': 0, 'failed': 0, 'total': total, 'queued': True, 'task_id': task_id})

    app_logger, get_setting, call_groq, call_gemini, *_ = _get_app_globals()
    conn = get_db()
    if force:
        contacts_list = conn.execute("SELECT id, name, company, email FROM contacts WHERE workspace_id=?", (get_wid_safe(),)).fetchall()
    else:
        contacts_list = conn.execute("SELECT id, name, company, email FROM contacts WHERE (context IS NULL OR context='') AND workspace_id=?", (get_wid_safe(),)).fetchall()
    enriched = 0
    failed = 0

    for contact in contacts_list:
        domain = contact['email'].split('@')[1] if '@' in contact['email'] else ''
        company = contact['company'] or domain
        website_text = ''
        if domain:
            try:
                r = http_requests.get(f'https://{domain}', timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
                if r.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(r.text, 'html.parser')
                    title = soup.title.string if soup.title else ''
                    meta_desc = ''
                    meta = soup.find('meta', attrs={'name': 'description'})
                    if meta:
                        meta_desc = meta.get('content', '')
                    paragraphs = ' '.join([p.get_text() for p in soup.find_all('p')[:5]])
                    website_text = f"Title: {title}. Description: {meta_desc}. Content: {paragraphs[:500]}"
            except:
                pass
        prompt = f"""In 2-3 bullet points (under 60 words), summarize what {company} does.
{'Website data: ' + website_text[:600] if website_text else 'Use only well-known public facts.'}
Include: what they do, any known funding/stage, tech focus. Plain text only."""
        try:
            priority = (get_setting('ai_priority') or 'groq,gemini').split(',')
            result_text = None
            for provider in priority:
                provider = provider.strip().lower()
                if provider == 'groq':
                    result_text, err = call_groq(prompt)
                elif provider == 'gemini':
                    result_text, err = call_gemini(prompt)
                if result_text:
                    break
            if result_text:
                conn.execute("UPDATE contacts SET context=? WHERE id=?", (result_text.strip(), contact['id']))
                conn.commit()
                enriched += 1
            else:
                failed += 1
        except:
            failed += 1
        time.sleep(1.5)

    conn.close()
    return jsonify({'enriched': enriched, 'failed': failed, 'total': len(contacts_list)})


# ── MEMORY: ai_generated_cache with TTL (max 500 entries, 30min expiry) ──
import time as _time
_cache_store = {}  # {key: (value, timestamp)}
_CACHE_TTL = 1800  # 30 minutes
_CACHE_MAX = 500

class _TTLCache:
    def __init__(self):
        self._d = {}
    def __setitem__(self, k, v):
        if len(self._d) >= _CACHE_MAX:
            # Evict oldest
            oldest = min(self._d, key=lambda x: self._d[x][1])
            del self._d[oldest]
        self._d[k] = (v, _time.time())
    def __getitem__(self, k):
        v, ts = self._d[k]
        if _time.time() - ts > _CACHE_TTL:
            del self._d[k]
            raise KeyError(k)
        return v
    def __contains__(self, k):
        try: self[k]; return True
        except KeyError: return False
    def get(self, k, default=None):
        try: return self[k]
        except KeyError: return default
    def pop(self, k, default=None):
        try:
            v = self[k]; del self._d[k]; return v
        except KeyError: return default
    def __str__(self): return str({k: v for k,(v,_) in self._d.items()})

ai_generated_cache = _TTLCache()


@contacts_bp.route('/api/generate_email', methods=['POST'])
@login_required
def api_generate_email():
    name = request.json.get('name', '')
    company = request.json.get('company', '')
    contact_id = request.json.get('contact_id', '')
    _, get_setting, call_groq, call_gemini, generate_ai_email, *_ = _get_app_globals()
    prompt_template = get_setting('email_prompt')
    context = ''
    designation = ''
    if contact_id:
        conn = get_db()
        row = conn.execute("SELECT context, designation FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if row:
            context = row['context'] or ''
            designation = row['designation'] or ''
        conn.close()
    if not context:
        return jsonify({'success': False, 'error': 'No context found. Please fetch context first.'})
    body, error = generate_ai_email(name, company, prompt_template, context, designation)
    if body:
        ai_generated_cache[str(contact_id)] = body
        return jsonify({'success': True, 'body': body})
    return jsonify({'success': False, 'error': error})


@contacts_bp.route('/api/generate_all', methods=['POST'])
@login_required
def api_generate_all():
    contact_ids = request.json.get('contact_ids', [])
    _, get_setting, _, _, generate_ai_email, *_ = _get_app_globals()
    prompt_template = get_setting('email_prompt')
    results = []
    conn = get_db()
    for cid in contact_ids:
        contact = conn.execute("SELECT name, company FROM contacts WHERE id=?", (cid,)).fetchone()
        if contact:
            body, error = generate_ai_email(contact['name'], contact['company'], prompt_template)
            results.append({'id': cid, 'name': contact['name'], 'body': body or f'Error: {error}'})
            import time; time.sleep(1)
    conn.close()
    return jsonify({'results': results})


@contacts_bp.route('/api/audience_count')
@login_required
def api_audience_count():
    """Return contact count matching campaign audience filters."""
    from services.workspace_service import get_wid
    wid       = get_wid()
    min_score = int(request.args.get('min_score', 0))
    valid_only = request.args.get('valid_only', '0') == '1'
    company   = request.args.get('company', '').strip().lower()

    conn = get_db()
    sql    = "SELECT COUNT(*) FROM contacts WHERE workspace_id=?"
    params = [wid]

    if valid_only:
        sql += " AND email_valid=1"
    if min_score > 0:
        sql += " AND COALESCE(lead_score,0) >= ?"
        params.append(min_score)
    if company:
        sql += " AND LOWER(company) LIKE ?"
        params.append(f'%{company}%')

    count = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return jsonify({'count': count})


@contacts_bp.route('/api/verify_status')
@login_required
def api_verify_status():
    from services.workspace_service import get_wid
    wid = get_wid()
    conn = get_db()
    all_contacts = conn.execute(
        "SELECT id, name, email, email_valid, validation_reason FROM contacts WHERE workspace_id=? ORDER BY id", (wid,)
    ).fetchall()
    conn.close()
    results = [{'id': r['id'], 'name': r['name'], 'email': r['email'], 'valid': r['email_valid'], 'reason': r['validation_reason'] or ''} for r in all_contacts]
    return jsonify({
        'running': verify_progress['running'],
        'total': verify_progress['total'],
        'done': verify_progress['done'],
        'current_email': verify_progress['current_email'],
        'results': results
    })





