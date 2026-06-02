"""
services/copilot/handlers/smtp.py — SMTP & Deliverability Handlers
"""
import smtplib
from utils.db import get_db


def toggle_account(workspace_id: int, user_id: int, account_id: int, **_) -> dict:
    conn = get_db()
    acc = conn.execute("SELECT id, email, active FROM smtp_accounts WHERE id=? AND workspace_id=?",
                       (account_id, workspace_id)).fetchone()
    if not acc:
        conn.close()
        raise ValueError('SMTP account not found')
    new_status = 0 if acc['active'] else 1
    conn.execute("UPDATE smtp_accounts SET active=? WHERE id=?", (new_status, account_id))
    conn.commit()
    conn.close()
    state = 'enabled' if new_status else 'disabled'
    return {'message': f'{acc["email"]} {state}'}


def diagnose(workspace_id: int, user_id: int, **_) -> dict:
    conn = get_db()
    accounts = conn.execute(
        "SELECT email, health_score, warmup_stage, sent_today, daily_limit, active "
        "FROM smtp_accounts WHERE workspace_id=? ORDER BY health_score",
        (workspace_id,)
    ).fetchall()
    total_sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent' AND workspace_id=?", (workspace_id,)).fetchone()[0]
    total_bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed') AND workspace_id=?", (workspace_id,)).fetchone()[0]
    bounce_rate = round(total_bounced / max(1, total_sent) * 100, 1)

    # Top bounce reasons
    reasons = conn.execute("""
        SELECT bounce_reason, COUNT(*) as cnt FROM emails_sent
        WHERE workspace_id=? AND status IN ('bounced','failed') AND bounce_reason IS NOT NULL
        GROUP BY bounce_reason ORDER BY cnt DESC LIMIT 5
    """, (workspace_id,)).fetchall()
    conn.close()

    return {
        'message': 'Deliverability diagnosis',
        'bounce_rate': bounce_rate,
        'accounts': [dict(a) for a in accounts],
        'top_bounce_reasons': [{'reason': r['bounce_reason'][:80], 'count': r['cnt']} for r in reasons],
        'recommendations': _get_recommendations(bounce_rate, accounts),
    }


def _get_recommendations(bounce_rate, accounts):
    recs = []
    if bounce_rate > 5:
        recs.append('Bounce rate is critical — pause campaigns and verify email list')
    if bounce_rate > 2:
        recs.append('Consider removing invalid contacts before next send')
    sick = [a for a in accounts if a['health_score'] < 40 and a['active']]
    for a in sick:
        recs.append(f'Disable {a["email"]} (health: {a["health_score"]}/100)')
    near_limit = [a for a in accounts if a['active'] and a['sent_today'] >= a['daily_limit'] * 0.9]
    for a in near_limit:
        recs.append(f'{a["email"]} at {a["sent_today"]}/{a["daily_limit"]} daily limit')
    if not recs:
        recs.append('All systems healthy')
    return recs


def test_connection(workspace_id: int, user_id: int, account_id: int, **_) -> dict:
    conn = get_db()
    acc = conn.execute("SELECT email, password, smtp_server, smtp_port, login_username FROM smtp_accounts WHERE id=? AND workspace_id=?",
                       (account_id, workspace_id)).fetchone()
    conn.close()
    if not acc:
        raise ValueError('SMTP account not found')
    login = acc['login_username'] if acc['login_username'] else acc['email']
    try:
        server = smtplib.SMTP(acc['smtp_server'], int(acc['smtp_port']), timeout=10)
        server.starttls()
        server.login(login, acc['password'])
        server.quit()
        return {'message': f'{acc["email"]} — connection successful ✓'}
    except Exception as e:
        return {'message': f'{acc["email"]} — connection FAILED: {str(e)[:100]}'}
