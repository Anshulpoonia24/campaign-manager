"""
services/copilot/agents/deliverability_agent.py — Deliverability AI Agent
==========================================================================
Monitors SMTP health, diagnoses bounce patterns, recommends fixes.
"""
import json
from utils.db import get_db
from services.copilot.agents.base_agent import BaseAgent


class DeliverabilityAgent(BaseAgent):
    agent_type = 'deliverability'
    description = 'SMTP health monitoring, bounce diagnosis, warmup management'
    capabilities = ['diagnose_smtp', 'health_check', 'warmup_recommend', 'bounce_analysis']

    SYSTEM_PROMPT = """You are the Deliverability Agent for OutreachOS — an expert in email deliverability.
You analyze SMTP health, bounce patterns, and provide actionable recommendations.

RESPOND IN JSON:
{
  "diagnosis": "Brief diagnosis (1-2 sentences)",
  "severity": "low|medium|high|critical",
  "issues": ["issue1", "issue2"],
  "recommendations": ["action1", "action2"],
  "auto_actions": [{"type": "action_type", "params": {}}]
}"""

    def _execute(self, task_type: str, input_data: dict) -> dict:
        dispatch = {
            'diagnose_smtp': self._diagnose,
            'health_check': self._health_check,
            'warmup_recommend': self._warmup_recommend,
            'bounce_analysis': self._bounce_analysis,
        }
        handler = dispatch.get(task_type, self._health_check)
        return handler(input_data)

    def analyze(self, input_data: dict = None) -> dict:
        """Quick health snapshot without AI call."""
        conn = get_db()
        accounts = conn.execute("""
            SELECT id, email, health_score, warmup_stage, active, sent_today, daily_limit
            FROM smtp_accounts WHERE workspace_id=?
        """, (self.wid,)).fetchall()

        total_sent = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status='sent'", (self.wid,)
        ).fetchone()[0]
        total_bounced = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status IN ('bounced','failed')", (self.wid,)
        ).fetchone()[0]
        conn.close()

        sick = [a for a in accounts if a['health_score'] < 50 and a['active']]
        active = [a for a in accounts if a['active']]
        capacity = sum(a['daily_limit'] - a['sent_today'] for a in active)
        bounce_rate = round(total_bounced / max(1, total_sent + total_bounced) * 100, 1)

        return {
            'total_accounts': len(accounts),
            'active': len(active),
            'sick': len(sick),
            'sick_emails': [a['email'] for a in sick],
            'capacity_remaining': capacity,
            'bounce_rate': bounce_rate,
            'health_status': 'critical' if bounce_rate > 10 else 'warning' if bounce_rate > 5 else 'healthy',
        }

    def _diagnose(self, input_data: dict) -> dict:
        """Full AI-powered SMTP diagnosis."""
        snapshot = self.analyze()
        conn = get_db()
        recent_bounces = conn.execute("""
            SELECT email, bounce_reason, sent_at FROM emails_sent
            WHERE workspace_id=? AND status IN ('bounced','failed')
            ORDER BY sent_at DESC LIMIT 10
        """, (self.wid,)).fetchall()
        conn.close()

        bounce_reasons = [r['bounce_reason'] or 'unknown' for r in recent_bounces]

        user_prompt = f"""Diagnose this workspace's deliverability:
- Bounce rate: {snapshot['bounce_rate']}%
- Sick accounts: {snapshot['sick_emails']}
- Active accounts: {snapshot['active']}/{snapshot['total_accounts']}
- Remaining capacity: {snapshot['capacity_remaining']}
- Recent bounce reasons: {bounce_reasons[:5]}

What's wrong and what should be done?"""

        ai_response = self.call_ai(self.SYSTEM_PROMPT, user_prompt)
        try:
            result = json.loads(ai_response)
        except Exception:
            result = {'diagnosis': ai_response[:300], 'severity': snapshot['health_status'],
                      'issues': [], 'recommendations': [], 'auto_actions': []}

        result['snapshot'] = snapshot
        return result

    def _health_check(self, input_data: dict) -> dict:
        """Quick health check — no AI call."""
        snapshot = self.analyze()
        issues = []
        recommendations = []

        if snapshot['bounce_rate'] > 10:
            issues.append(f"Critical bounce rate: {snapshot['bounce_rate']}%")
            recommendations.append("Pause sending immediately, verify contact list")
        elif snapshot['bounce_rate'] > 5:
            issues.append(f"High bounce rate: {snapshot['bounce_rate']}%")
            recommendations.append("Run email verification before next campaign")

        if snapshot['sick']:
            issues.append(f"{snapshot['sick']} sick SMTP account(s)")
            recommendations.append("Disable sick accounts and add fresh ones")

        if snapshot['capacity_remaining'] < 20:
            issues.append(f"Only {snapshot['capacity_remaining']} sends remaining today")
            recommendations.append("Wait for daily reset or add more accounts")

        return {
            'snapshot': snapshot,
            'issues': issues,
            'recommendations': recommendations,
            'severity': 'critical' if snapshot['bounce_rate'] > 10 else 'medium' if issues else 'low',
        }

    def _warmup_recommend(self, input_data: dict) -> dict:
        """Recommend warmup schedule adjustments."""
        conn = get_db()
        accounts = conn.execute("""
            SELECT id, email, warmup_stage, health_score, sent_today, daily_limit, created_at
            FROM smtp_accounts WHERE workspace_id=? AND active=1
        """, (self.wid,)).fetchall()
        conn.close()

        recommendations = []
        for a in accounts:
            if a['health_score'] >= 90 and a['warmup_stage'] < 5:
                recommendations.append({
                    'email': a['email'],
                    'action': 'upgrade_warmup',
                    'reason': f"Health {a['health_score']}% — safe to increase volume",
                })
            elif a['health_score'] < 60:
                recommendations.append({
                    'email': a['email'],
                    'action': 'reduce_volume',
                    'reason': f"Health {a['health_score']}% — reduce sending to recover",
                })

        return {'recommendations': recommendations}

    def _bounce_analysis(self, input_data: dict) -> dict:
        """Analyze bounce patterns."""
        conn = get_db()
        bounces = conn.execute("""
            SELECT bounce_reason, COUNT(*) as cnt
            FROM emails_sent
            WHERE workspace_id=? AND status IN ('bounced','failed')
            AND bounce_reason IS NOT NULL AND bounce_reason != ''
            GROUP BY bounce_reason ORDER BY cnt DESC LIMIT 10
        """, (self.wid,)).fetchall()
        conn.close()

        patterns = []
        for b in bounces:
            reason = b['bounce_reason'][:100]
            patterns.append({'reason': reason, 'count': b['cnt']})

        return {'patterns': patterns, 'total_patterns': len(patterns)}
