"""
services/copilot/agents/analytics_agent.py — Analytics & Reporting Agent
=========================================================================
Performance reporting, trend detection, send time optimization.
"""
import json
from utils.db import get_db
from services.copilot.agents.base_agent import BaseAgent


class AnalyticsAgent(BaseAgent):
    agent_type = 'analytics'
    description = 'Performance reporting, trend detection, optimization insights'
    capabilities = ['generate_report', 'detect_trends', 'best_send_time', 'forecast']

    SYSTEM_PROMPT = """You are the Analytics Agent for OutreachOS.
You analyze outreach data and provide actionable insights.

RESPOND IN JSON:
{
  "summary": "2-3 sentence performance summary",
  "key_metrics": {"metric": value},
  "trends": ["trend1", "trend2"],
  "insights": ["insight1", "insight2"],
  "recommendations": ["rec1"]
}"""

    def _execute(self, task_type: str, input_data: dict) -> dict:
        dispatch = {
            'generate_report': self._report,
            'detect_trends': self._trends,
            'best_send_time': self._best_time,
            'forecast': self._forecast,
        }
        handler = dispatch.get(task_type, self._report)
        return handler(input_data)

    def analyze(self, input_data: dict = None) -> dict:
        """Quick metrics snapshot."""
        conn = get_db()
        sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status='sent'", (self.wid,)).fetchone()[0]
        opened = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND opened=1", (self.wid,)).fetchone()[0]
        replied = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND replied=1", (self.wid,)).fetchone()[0]
        meetings = conn.execute("SELECT COUNT(*) FROM threads WHERE workspace_id=? AND status='meeting'", (self.wid,)).fetchone()[0]
        conn.close()
        return {
            'total_sent': sent,
            'open_rate': round(opened / max(1, sent) * 100, 1),
            'reply_rate': round(replied / max(1, sent) * 100, 1),
            'meetings_booked': meetings,
        }

    def _report(self, input_data: dict) -> dict:
        """Generate a performance report."""
        days = input_data.get('days', 7)
        conn = get_db()
        sent = conn.execute("""
            SELECT COUNT(*) FROM emails_sent
            WHERE workspace_id=? AND status='sent' AND sent_at >= datetime('now', ?)
        """, (self.wid, f'-{days} days')).fetchone()[0]
        opened = conn.execute("""
            SELECT COUNT(*) FROM emails_sent
            WHERE workspace_id=? AND opened=1 AND sent_at >= datetime('now', ?)
        """, (self.wid, f'-{days} days')).fetchone()[0]
        replied = conn.execute("""
            SELECT COUNT(*) FROM emails_sent
            WHERE workspace_id=? AND replied=1 AND sent_at >= datetime('now', ?)
        """, (self.wid, f'-{days} days')).fetchone()[0]
        bounced = conn.execute("""
            SELECT COUNT(*) FROM emails_sent
            WHERE workspace_id=? AND status IN ('bounced','failed') AND sent_at >= datetime('now', ?)
        """, (self.wid, f'-{days} days')).fetchone()[0]
        conn.close()

        return {
            'period_days': days,
            'sent': sent,
            'opened': opened,
            'replied': replied,
            'bounced': bounced,
            'open_rate': round(opened / max(1, sent) * 100, 1),
            'reply_rate': round(replied / max(1, sent) * 100, 1),
            'bounce_rate': round(bounced / max(1, sent + bounced) * 100, 1),
        }

    def _trends(self, input_data: dict) -> dict:
        """Detect trends in metrics over time."""
        conn = get_db()
        # Last 7 days vs previous 7 days
        recent = conn.execute("""
            SELECT COUNT(*) as sent,
                   SUM(CASE WHEN opened=1 THEN 1 ELSE 0 END) as opened,
                   SUM(CASE WHEN replied=1 THEN 1 ELSE 0 END) as replied
            FROM emails_sent WHERE workspace_id=? AND sent_at >= datetime('now', '-7 days')
        """, (self.wid,)).fetchone()
        previous = conn.execute("""
            SELECT COUNT(*) as sent,
                   SUM(CASE WHEN opened=1 THEN 1 ELSE 0 END) as opened,
                   SUM(CASE WHEN replied=1 THEN 1 ELSE 0 END) as replied
            FROM emails_sent WHERE workspace_id=? AND sent_at >= datetime('now', '-14 days') AND sent_at < datetime('now', '-7 days')
        """, (self.wid,)).fetchone()
        conn.close()

        trends = []
        r_sent, p_sent = recent['sent'] or 0, previous['sent'] or 0
        if p_sent > 0:
            change = round((r_sent - p_sent) / p_sent * 100, 1)
            trends.append(f"Send volume {'up' if change > 0 else 'down'} {abs(change)}% vs last week")

        r_open = round((recent['opened'] or 0) / max(1, r_sent) * 100, 1)
        p_open = round((previous['opened'] or 0) / max(1, p_sent) * 100, 1)
        if p_open > 0:
            diff = round(r_open - p_open, 1)
            trends.append(f"Open rate {'improved' if diff > 0 else 'dropped'} by {abs(diff)}pp ({r_open}% vs {p_open}%)")

        return {'trends': trends, 'this_week': {'sent': r_sent}, 'last_week': {'sent': p_sent}}

    def _best_time(self, input_data: dict) -> dict:
        """Find best send time based on open rates by hour."""
        conn = get_db()
        # SQLite: extract hour from sent_at
        hours = conn.execute("""
            SELECT CAST(SUBSTR(sent_at, 12, 2) AS INTEGER) as hour,
                   COUNT(*) as sent,
                   SUM(CASE WHEN opened=1 THEN 1 ELSE 0 END) as opened
            FROM emails_sent
            WHERE workspace_id=? AND status='sent' AND sent_at IS NOT NULL
            GROUP BY hour
            HAVING sent >= 3
            ORDER BY hour
        """, (self.wid,)).fetchall()
        conn.close()

        if not hours:
            return {'message': 'Not enough data yet', 'best_hours': []}

        by_hour = []
        for h in hours:
            rate = round((h['opened'] or 0) / max(1, h['sent']) * 100, 1)
            by_hour.append({'hour': h['hour'], 'sent': h['sent'], 'open_rate': rate})

        by_hour.sort(key=lambda x: x['open_rate'], reverse=True)
        best = by_hour[:3]

        return {
            'best_hours': [h['hour'] for h in best],
            'best_open_rates': [h['open_rate'] for h in best],
            'recommendation': f"Best time to send: {best[0]['hour']}:00 ({best[0]['open_rate']}% open rate)" if best else '',
            'all_hours': by_hour,
        }

    def _forecast(self, input_data: dict) -> dict:
        """Simple linear forecast based on recent trend."""
        snapshot = self.analyze()
        # Very simple — just project current rates
        return {
            'current_metrics': snapshot,
            'forecast': f"At current rate, expecting ~{snapshot['reply_rate']}% reply rate next week",
            'confidence': 'low' if snapshot['total_sent'] < 50 else 'medium',
        }
