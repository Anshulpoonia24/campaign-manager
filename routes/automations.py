"""
routes/automations.py — Automation Rules Routes
=================================================
Automation settings, run, stats.
"""
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

automations_bp = Blueprint('automations', __name__)


@automations_bp.route('/automations', endpoint='automations_page')
@login_required
def automations_page():
    from services.automation_service import get_rule_settings, get_automation_stats, RULE_META
    rules = get_rule_settings()
    stats = get_automation_stats()
    return render_template('automations.html', rules=rules, stats=stats, rule_meta=RULE_META)


@automations_bp.route('/api/automations/save', methods=['POST'])
@login_required
def api_save_automation():
    from services.automation_service import update_rule
    from app import app_logger
    data = request.json
    rule_key = data.get('rule_key')
    enabled = data.get('enabled', True)
    delay_days = int(data.get('delay_days', 2))
    max_followups = int(data.get('max_followups', 3))
    if not rule_key:
        return jsonify({'success': False, 'error': 'rule_key required'})
    update_rule(rule_key, enabled, delay_days, max_followups)
    app_logger.info(f'Automation rule updated: {rule_key} enabled={enabled}')
    return jsonify({'success': True})


@automations_bp.route('/api/automations/run', methods=['POST'])
@login_required
def api_run_automations():
    from services.automation_service import process_automation_rules
    stats = process_automation_rules()
    return jsonify({'success': True, 'stats': stats})


@automations_bp.route('/api/automations/stats')
@login_required
def api_automation_stats():
    from services.automation_service import get_automation_stats
    return jsonify(get_automation_stats())


@automations_bp.route('/api/automations/followup_draft', methods=['POST'])
@login_required
def api_followup_draft():
    from services.automation_service import generate_followup_email
    data = request.json
    draft = generate_followup_email(
        data.get('contact_name', ''),
        data.get('company', ''),
        data.get('context', ''),
        data.get('previous_subject', '')
    )
    if draft:
        return jsonify({'success': True, 'draft': draft})
    return jsonify({'success': False, 'error': 'AI generation failed'})
