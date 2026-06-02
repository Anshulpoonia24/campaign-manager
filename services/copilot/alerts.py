"""
services/copilot/alerts.py — Proactive Alert Engine
=====================================================
Detects workspace issues and surfaces them to the copilot UI.
Runs on-demand (when copilot opens) — no background thread needed.
"""
from datetime import datetime
from utils.db import get_db


def generate_alerts(workspace_id: int) -> list:
    """Generate all active alerts for a workspace. Called on copilot load."""
    alerts = []
    conn = get_db()
    try:
        # 1. High bounce rate
        total = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status='sent'",
            (workspace_id,)
        ).fetchone()[0]
        bounced = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status IN ('bounced','failed')",
            (workspace_id,)
        ).fetchone()[0]
        if total > 10:
            rate = bounced / (total + bounced) * 100
            if rate > 5:
                alerts.append({
                    'id': 'high_bounce_rate',
                    'severity': 'high' if rate > 10 else 'medium',
                    'message': f'Bounce rate at {rate:.1f}% — deliverability at risk',
                    'action': {'type': 'diagnose_smtp', 'label': 'Diagnose SMTP'},
                })

        # 2. Sick SMTP accounts (health < 50)
        sick = conn.execute(
            "SELECT COUNT(*) FROM smtp_accounts WHERE workspace_id=? AND active=1 AND health_score < 50",
            (workspace_id,)
        ).fetchone()[0]
        if sick > 0:
            alerts.append({
                'id': 'sick_smtp',
                'severity': 'high',
                'message': f'{sick} SMTP account(s) with health below 50%',
                'action': {'type': 'diagnose_smtp', 'label': 'View SMTP Health'},
            })

        # 3. Stalled campaigns (running but no sends in 30min)
        stalled = conn.execute("""
            SELECT COUNT(*) FROM campaigns
            WHERE workspace_id=? AND job_status='running'
            AND last_heartbeat < datetime('now', '-30 minutes')
        """, (workspace_id,)).fetchone()[0]
        if stalled > 0:
            alerts.append({
                'id': 'stalled_campaigns',
                'severity': 'medium',
                'message': f'{stalled} campaign(s) appear stalled — no activity for 30min',
                'action': {'type': 'diagnose_campaign', 'label': 'Check Campaigns'},
            })

        # 4. Hot leads waiting (interested/meeting threads with no outgoing reply)
        hot_waiting = conn.execute("""
            SELECT COUNT(*) FROM threads t
            WHERE t.workspace_id=? AND t.status IN ('interested','meeting')
            AND NOT EXISTS (
                SELECT 1 FROM messages m
                WHERE m.thread_id = t.id AND m.direction='outgoing'
                AND m.created_at > (
                    SELECT MAX(m2.created_at) FROM messages m2
                    WHERE m2.thread_id = t.id AND m2.direction='incoming'
                )
            )
        """, (workspace_id,)).fetchone()[0]
        if hot_waiting > 0:
            alerts.append({
                'id': 'hot_leads_waiting',
                'severity': 'high',
                'message': f'{hot_waiting} hot lead(s) waiting for reply',
                'action': {'type': 'navigate', 'label': 'Go to Inbox', 'params': {'url': '/inbox?status=interested'}},
            })

        # 5. No SMTP accounts configured
        smtp_count = conn.execute(
            "SELECT COUNT(*) FROM smtp_accounts WHERE workspace_id=?",
            (workspace_id,)
        ).fetchone()[0]
        if smtp_count == 0:
            alerts.append({
                'id': 'no_smtp',
                'severity': 'critical',
                'message': 'No SMTP accounts configured — cannot send emails',
                'action': {'type': 'navigate', 'label': 'Add SMTP', 'params': {'url': '/settings'}},
            })

        # 6. Daily send limit almost reached
        capacity = conn.execute("""
            SELECT COALESCE(SUM(daily_limit - sent_today), 0)
            FROM smtp_accounts WHERE workspace_id=? AND active=1
        """, (workspace_id,)).fetchone()[0]
        if smtp_count > 0 and capacity < 10:
            alerts.append({
                'id': 'low_capacity',
                'severity': 'medium',
                'message': f'Only {capacity} emails remaining in daily send quota',
                'action': None,
            })

    except Exception:
        pass
    finally:
        conn.close()

    return alerts


def get_alert_count(workspace_id: int) -> int:
    """Quick count of active alerts (for badge)."""
    return len(generate_alerts(workspace_id))
