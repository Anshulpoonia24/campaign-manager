"""
routes/sequences.py — Sequence Engine Routes
===============================================
Step CRUD, enrollment, pause/resume, trigger, analytics.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required

sequences_bp = Blueprint('sequences', __name__)


@sequences_bp.route('/campaign/<int:campaign_id>/sequence')
@login_required
def sequence_builder(campaign_id):
    from app import get_db
    conn = get_db()
    campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    conn.close()
    if not campaign:
        flash('Campaign not found', 'error')
        return redirect(url_for('campaigns.campaigns_list'))
    return render_template('sequence_builder.html', campaign=campaign)


# ── STEP CRUD ─────────────────────────────────────────────────────
@sequences_bp.route('/api/sequence/<int:campaign_id>/steps')
@login_required
def api_sequence_steps(campaign_id):
    from services.sequence_engine import get_all_steps
    steps = get_all_steps(campaign_id)
    return jsonify({'steps': steps})


@sequences_bp.route('/api/sequence/<int:campaign_id>/steps/add', methods=['POST'])
@login_required
def api_sequence_add_step(campaign_id):
    from app import app_logger
    from services.sequence_engine import add_step, get_all_steps
    from services.workspace_service import get_wid
    data = request.json or {}
    wid = get_wid()
    steps = get_all_steps(campaign_id)
    next_order = max((s['step_order'] for s in steps), default=0) + 1
    step_id = add_step(
        campaign_id=campaign_id, workspace_id=wid,
        step_order=int(data.get('step_order', next_order)),
        step_type=data.get('step_type', 'email'),
        delay_days=int(data.get('delay_days', 3)),
        subject=data.get('subject', ''), body=data.get('body', ''),
        ai_enabled=bool(data.get('ai_enabled', False)),
    )
    app_logger.info(f'[SEQ] Step added: campaign {campaign_id} step_id {step_id}')
    return jsonify({'success': True, 'step_id': step_id})


@sequences_bp.route('/api/sequence/step/<int:step_id>/update', methods=['POST'])
@login_required
def api_sequence_update_step(step_id):
    from services.sequence_engine import update_step
    data = request.json or {}
    update_step(
        step_id=step_id, step_order=int(data.get('step_order', 1)),
        step_type=data.get('step_type', 'email'), delay_days=int(data.get('delay_days', 3)),
        subject=data.get('subject', ''), body=data.get('body', ''),
        ai_enabled=bool(data.get('ai_enabled', False)), active=bool(data.get('active', True)),
    )
    return jsonify({'success': True})


@sequences_bp.route('/api/sequence/step/<int:step_id>/delete', methods=['DELETE'])
@login_required
def api_sequence_delete_step(step_id):
    from services.sequence_engine import delete_step
    delete_step(step_id)
    return jsonify({'success': True})


@sequences_bp.route('/api/sequence/<int:campaign_id>/steps/reorder', methods=['POST'])
@login_required
def api_sequence_reorder_steps(campaign_id):
    from services.sequence_engine import reorder_steps
    ordered_ids = (request.json or {}).get('ordered_ids', [])
    if not ordered_ids:
        return jsonify({'success': False, 'error': 'ordered_ids required'})
    reorder_steps(campaign_id, ordered_ids)
    return jsonify({'success': True})


# ── ENROLLMENT & STATE ────────────────────────────────────────────
@sequences_bp.route('/api/sequence/<int:campaign_id>/enroll', methods=['POST'])
@login_required
def api_sequence_enroll(campaign_id):
    from app import get_db, app_logger, CELERY_AVAILABLE, has_active_workers
    from services.sequence_engine import enroll_contacts_bulk, get_steps
    from services.workspace_service import get_wid
    data = request.json or {}
    wid = get_wid()

    steps = get_steps(campaign_id)
    if not steps:
        return jsonify({'success': False, 'error': 'No active steps in this sequence. Add steps first.'})

    if data.get('enroll_all'):
        conn = get_db()
        rows = conn.execute("SELECT id FROM contacts WHERE workspace_id=? AND email_valid=1", (wid,)).fetchall()
        conn.close()
        contact_ids = [r['id'] for r in rows]
    else:
        contact_ids = [int(i) for i in data.get('contact_ids', [])]

    if not contact_ids:
        return jsonify({'success': False, 'error': 'No contacts provided'})

    if CELERY_AVAILABLE and has_active_workers():
        from tasks.sequence_tasks import enroll_contacts_task
        result = enroll_contacts_task.apply_async(args=[contact_ids, campaign_id, wid], queue='automation_queue')
        app_logger.info(f'[SEQ] Enroll queued | campaign {campaign_id} | {len(contact_ids)} contacts | task {result.id}')
        return jsonify({'success': True, 'queued': True, 'task_id': result.id, 'total': len(contact_ids)})

    result = enroll_contacts_bulk(contact_ids, campaign_id, wid)
    app_logger.info(f'[SEQ] Enrolled sync | campaign {campaign_id} | {result}')
    return jsonify({'success': True, 'queued': False, **result})


@sequences_bp.route('/api/sequence/<int:campaign_id>/pause/<int:contact_id>', methods=['POST'])
@login_required
def api_sequence_pause(campaign_id, contact_id):
    from services.sequence_engine import pause_contact
    pause_contact(contact_id, campaign_id)
    return jsonify({'success': True, 'status': 'paused'})


@sequences_bp.route('/api/sequence/<int:campaign_id>/resume/<int:contact_id>', methods=['POST'])
@login_required
def api_sequence_resume(campaign_id, contact_id):
    from services.sequence_engine import resume_contact
    resume_contact(contact_id, campaign_id)
    return jsonify({'success': True, 'status': 'active'})


@sequences_bp.route('/api/sequence/<int:campaign_id>/contacts')
@login_required
def api_sequence_contacts(campaign_id):
    from services.sequence_engine import get_campaign_contacts_state
    contacts = get_campaign_contacts_state(campaign_id)
    for c in contacts:
        for k in ('next_run_at', 'last_sent_at', 'completed_at', 'created_at'):
            if c.get(k) and not isinstance(c[k], str):
                c[k] = c[k].isoformat()
    return jsonify({'contacts': contacts})


@sequences_bp.route('/api/sequence/<int:campaign_id>/stats')
@login_required
def api_sequence_stats(campaign_id):
    from services.sequence_engine import get_sequence_stats
    stats = get_sequence_stats(campaign_id)
    return jsonify(stats)


@sequences_bp.route('/api/sequence/<int:campaign_id>/contact/<int:contact_id>/history')
@login_required
def api_sequence_contact_history(campaign_id, contact_id):
    from services.sequence_engine import get_contact_sequence_history, get_contact_state
    history = get_contact_sequence_history(contact_id, campaign_id)
    state = get_contact_state(contact_id, campaign_id)
    if state:
        for k in ('next_run_at', 'last_sent_at', 'completed_at', 'created_at'):
            if state.get(k) and not isinstance(state[k], str):
                state[k] = state[k].isoformat()
    return jsonify({'history': history, 'state': state})


@sequences_bp.route('/api/sequence/<int:campaign_id>/trigger', methods=['POST'])
@login_required
def api_sequence_trigger(campaign_id):
    from app import CELERY_AVAILABLE, has_active_workers
    from services.workspace_service import get_wid
    wid = get_wid()

    if CELERY_AVAILABLE and has_active_workers():
        from tasks.sequence_tasks import process_sequences_task
        result = process_sequences_task.apply_async(queue='automation_queue')
        return jsonify({'success': True, 'queued': True, 'task_id': result.id})

    from services.sequence_engine import get_due_contacts, check_stop_conditions, mark_stopped
    due = get_due_contacts(wid, limit=50)
    due = [d for d in due if d['campaign_id'] == campaign_id]
    processed = 0
    for cs in due:
        should_stop, reason = check_stop_conditions(cs['contact_id'], campaign_id)
        if should_stop:
            mark_stopped(cs['contact_id'], campaign_id, reason)
        processed += 1
    return jsonify({'success': True, 'queued': False, 'processed': processed})


@sequences_bp.route('/api/sequence/<int:campaign_id>/analytics')
@login_required
def api_sequence_analytics(campaign_id):
    from app import get_db
    from services.sequence_engine import get_steps
    conn = get_db()
    steps = get_steps(campaign_id)
    result = []
    prev_sent = None
    for s in steps:
        sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status='sent'", (campaign_id,)).fetchone()[0]
        opened = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND opened=1", (campaign_id,)).fetchone()[0]
        replied = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND replied=1", (campaign_id,)).fetchone()[0]
        bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status='bounced'", (campaign_id,)).fetchone()[0]
        dropoff = round((1 - sent / prev_sent) * 100, 1) if prev_sent and prev_sent > 0 else 0
        prev_sent = sent
        result.append({
            'step_id': s['id'], 'step_order': s['step_order'], 'step_type': s['step_type'],
            'subject': s['subject'], 'delay_days': s['delay_days'],
            'sent': sent, 'opened': opened, 'replied': replied, 'bounced': bounced,
            'open_rate': round(opened / sent * 100, 1) if sent else 0,
            'reply_rate': round(replied / sent * 100, 1) if sent else 0,
            'bounce_rate': round(bounced / sent * 100, 1) if sent else 0,
            'dropoff': dropoff,
        })
    conn.close()
    conn2 = get_db()
    total_enrolled = conn2.execute("SELECT COUNT(*) FROM contact_sequence_state WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
    conn2.close()
    return jsonify({'steps': result, 'total_enrolled': total_enrolled})
