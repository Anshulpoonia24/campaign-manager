"""
services/copilot/handlers/analytics.py — Analytics Action Handlers
"""
from utils.db import get_db
from datetime import datetime, timedelta


def generate_report(workspace_id: int, user_id: int, days: int = 7, **_) -> dict:
    conn = get_db()
    since = datetime.now() - timedelta(days=days)

    sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent' AND workspace_id=? AND sent_at>=?", (workspace_id, since)).fetchone()[0]
    opened = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1 AND workspace_id=? AND sent_at>=?", (workspace_id, since)).fetchone()[0]
    replied = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE replied=1 AND workspace_id=? AND sent_at>=?", (workspace_id, since)).fetchone()[0]
    bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed') AND workspace_id=? AND sent_at>=?", (workspace_id, since)).fetchone()[0]
    meetings = conn.execute("SELECT COUNT(*) FROM threads WHERE status='meeting' AND workspace_id=?", (workspace_id,)).fetchone()[0]
    conn.close()

    open_rate = round(opened / max(1, sent) * 100, 1)
    reply_rate = round(replied / max(1, sent) * 100, 1)
    bounce_rate = round(bounced / max(1, sent) * 100, 1)

    return {
        'message': f"**{days}-day report:**\n"
                   f"• Sent: {sent} | Opened: {opened} ({open_rate}%) | Replied: {replied} ({reply_rate}%)\n"
                   f"• Bounced: {bounced} ({bounce_rate}%) | Meetings: {meetings}\n"
                   f"{'⚠️ Bounce rate high!' if bounce_rate > 5 else '✓ Deliverability healthy'}"
    }


def compare(workspace_id: int, user_id: int, campaign_a: int, campaign_b: int, **_) -> dict:
    conn = get_db()

    def _stats(cid):
        sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status='sent'", (cid,)).fetchone()[0]
        opened = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND opened=1", (cid,)).fetchone()[0]
        replied = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND replied=1", (cid,)).fetchone()[0]
        name = conn.execute("SELECT name FROM campaigns WHERE id=?", (cid,)).fetchone()
        return {
            'name': name['name'] if name else f'Campaign {cid}',
            'sent': sent, 'opened': opened, 'replied': replied,
            'open_rate': round(opened / max(1, sent) * 100, 1),
            'reply_rate': round(replied / max(1, sent) * 100, 1),
        }

    a = _stats(campaign_a)
    b = _stats(campaign_b)
    conn.close()

    winner_open = 'A' if a['open_rate'] > b['open_rate'] else 'B'
    winner_reply = 'A' if a['reply_rate'] > b['reply_rate'] else 'B'

    return {
        'message': f"**{a['name']} vs {b['name']}:**\n"
                   f"• Open rate: {a['open_rate']}% vs {b['open_rate']}% (winner: {winner_open})\n"
                   f"• Reply rate: {a['reply_rate']}% vs {b['reply_rate']}% (winner: {winner_reply})",
        'campaign_a': a,
        'campaign_b': b,
    }


def best_send_time(workspace_id: int, user_id: int, **_) -> dict:
    conn = get_db()
    # Analyze opens by hour
    rows = conn.execute("""
        SELECT sent_at, opened FROM emails_sent
        WHERE workspace_id=? AND status='sent' AND sent_at IS NOT NULL
    """, (workspace_id,)).fetchall()
    conn.close()

    if len(rows) < 20:
        return {'message': 'Not enough data yet (need 20+ sent emails to analyze patterns)'}

    hour_stats = {}
    for r in rows:
        try:
            h = int(str(r['sent_at'])[11:13])
            if h not in hour_stats:
                hour_stats[h] = {'sent': 0, 'opened': 0}
            hour_stats[h]['sent'] += 1
            if r['opened']:
                hour_stats[h]['opened'] += 1
        except (ValueError, IndexError):
            continue

    # Find best hours
    best = sorted(hour_stats.items(), key=lambda x: x[1]['opened'] / max(1, x[1]['sent']), reverse=True)[:3]

    lines = []
    for h, stats in best:
        rate = round(stats['opened'] / max(1, stats['sent']) * 100, 1)
        lines.append(f"• {h:02d}:00 — {rate}% open rate ({stats['sent']} sent)")

    return {'message': f"**Best send times (by open rate):**\n" + '\n'.join(lines)}
