"""
services/copilot/handlers/campaign.py — Campaign Action Handlers
"""
from utils.db import get_db
from utils.logger import app_logger


def pause_campaign(workspace_id: int, user_id: int, campaign_id: int, **_) -> dict:
    from services.campaign_executor import pause_campaign as _pause
    conn = get_db()
    camp = conn.execute("SELECT id, name, job_status FROM campaigns WHERE id=? AND workspace_id=?",
                        (campaign_id, workspace_id)).fetchone()
    conn.close()
    if not camp:
        raise ValueError('Campaign not found')
    if camp['job_status'] != 'running':
        return {'message': f'Campaign "{camp["name"]}" is not running (status: {camp["job_status"]})'}
    _pause(campaign_id, workspace_id)
    return {'message': f'Campaign "{camp["name"]}" paused'}


def resume_campaign(workspace_id: int, user_id: int, campaign_id: int, **_) -> dict:
    from services.campaign_executor import resume_campaign as _resume
    conn = get_db()
    camp = conn.execute("SELECT id, name, job_status FROM campaigns WHERE id=? AND workspace_id=?",
                        (campaign_id, workspace_id)).fetchone()
    conn.close()
    if not camp:
        raise ValueError('Campaign not found')
    if camp['job_status'] != 'paused':
        return {'message': f'Campaign "{camp["name"]}" is not paused (status: {camp["job_status"]})'}
    _resume(campaign_id, workspace_id)
    return {'message': f'Campaign "{camp["name"]}" resumed'}


def cancel_campaign(workspace_id: int, user_id: int, campaign_id: int, **_) -> dict:
    from services.campaign_executor import cancel_campaign as _cancel
    conn = get_db()
    camp = conn.execute("SELECT id, name, job_status FROM campaigns WHERE id=? AND workspace_id=?",
                        (campaign_id, workspace_id)).fetchone()
    conn.close()
    if not camp:
        raise ValueError('Campaign not found')
    if camp['job_status'] in ('completed', 'cancelled'):
        return {'message': f'Campaign "{camp["name"]}" already {camp["job_status"]}'}
    _cancel(campaign_id, workspace_id)
    return {'message': f'Campaign "{camp["name"]}" cancelled'}


def retry_failed(workspace_id: int, user_id: int, campaign_id: int, **_) -> dict:
    conn = get_db()
    camp = conn.execute("SELECT id, name FROM campaigns WHERE id=? AND workspace_id=?",
                        (campaign_id, workspace_id)).fetchone()
    if not camp:
        conn.close()
        raise ValueError('Campaign not found')
    failed = conn.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status IN ('failed','bounced')",
        (campaign_id,)
    ).fetchone()[0]
    conn.close()
    if failed == 0:
        return {'message': 'No failed emails to retry'}
    # Queue retry (use existing infrastructure)
    return {'message': f'{failed} failed emails found — use campaign retry page to re-send'}


def diagnose(workspace_id: int, user_id: int, campaign_id: int, **_) -> dict:
    conn = get_db()
    camp = conn.execute(
        "SELECT id, name, job_status, total_contacts, sent_count, failed_count FROM campaigns WHERE id=? AND workspace_id=?",
        (campaign_id, workspace_id)
    ).fetchone()
    if not camp:
        conn.close()
        raise ValueError('Campaign not found')

    # Analyze failure reasons
    reasons = conn.execute("""
        SELECT bounce_reason, COUNT(*) as cnt
        FROM emails_sent WHERE campaign_id=? AND status IN ('failed','bounced')
        AND bounce_reason IS NOT NULL
        GROUP BY bounce_reason ORDER BY cnt DESC LIMIT 5
    """, (campaign_id,)).fetchall()

    # SMTP accounts used
    smtp_health = conn.execute(
        "SELECT email, health_score, active FROM smtp_accounts WHERE workspace_id=?",
        (workspace_id,)
    ).fetchall()

    conn.close()

    diagnosis = {
        'message': f'Campaign "{camp["name"]}" diagnosis',
        'status': camp['job_status'],
        'sent': camp['sent_count'] or 0,
        'failed': camp['failed_count'] or 0,
        'failure_rate': round((camp['failed_count'] or 0) / max(1, (camp['total_contacts'] or 1)) * 100, 1),
        'top_failure_reasons': [{'reason': r['bounce_reason'][:100], 'count': r['cnt']} for r in reasons],
        'smtp_health': [{'email': s['email'], 'health': s['health_score'], 'active': bool(s['active'])} for s in smtp_health],
    }
    return diagnosis
