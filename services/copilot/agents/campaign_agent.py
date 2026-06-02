"""
services/copilot/agents/campaign_agent.py — Campaign Strategy Agent
====================================================================
Analyzes campaign performance, suggests optimizations, manages execution.
"""
import json
from utils.db import get_db
from services.copilot.agents.base_agent import BaseAgent


class CampaignAgent(BaseAgent):
    agent_type = 'campaign'
    description = 'Campaign performance analysis, strategy optimization, A/B recommendations'
    capabilities = ['analyze_campaign', 'suggest_strategy', 'compare_campaigns', 'diagnose_failures']

    SYSTEM_PROMPT = """You are the Campaign Strategy Agent for OutreachOS.
You analyze cold email campaign performance and provide data-driven optimization advice.

RESPOND IN JSON:
{
  "analysis": "Brief analysis (2-3 sentences)",
  "metrics": {"open_rate": 0, "reply_rate": 0, "bounce_rate": 0},
  "issues": ["issue1"],
  "recommendations": ["rec1", "rec2"],
  "priority_actions": [{"type": "action", "label": "text", "params": {}}]
}"""

    def _execute(self, task_type: str, input_data: dict) -> dict:
        dispatch = {
            'analyze_campaign': self._analyze_campaign,
            'suggest_strategy': self._suggest_strategy,
            'compare_campaigns': self._compare,
            'diagnose_failures': self._diagnose_failures,
        }
        handler = dispatch.get(task_type, self._analyze_campaign)
        return handler(input_data)

    def analyze(self, input_data: dict = None) -> dict:
        """Quick campaign health snapshot."""
        conn = get_db()
        active = conn.execute(
            "SELECT COUNT(*) FROM campaigns WHERE workspace_id=? AND job_status='running'", (self.wid,)
        ).fetchone()[0]
        total_sent = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status='sent'", (self.wid,)
        ).fetchone()[0]
        total_opened = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND opened=1", (self.wid,)
        ).fetchone()[0]
        total_replied = conn.execute(
            "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND replied=1", (self.wid,)
        ).fetchone()[0]
        conn.close()

        return {
            'active_campaigns': active,
            'total_sent': total_sent,
            'open_rate': round(total_opened / max(1, total_sent) * 100, 1),
            'reply_rate': round(total_replied / max(1, total_sent) * 100, 1),
        }

    def _analyze_campaign(self, input_data: dict) -> dict:
        """Deep analysis of a specific campaign."""
        campaign_id = input_data.get('campaign_id', 0)
        conn = get_db()
        camp = conn.execute("SELECT * FROM campaigns WHERE id=? AND workspace_id=?",
                           (campaign_id, self.wid)).fetchone()
        if not camp:
            conn.close()
            return {'error': 'Campaign not found'}

        sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status='sent'",
                           (campaign_id,)).fetchone()[0]
        opened = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND opened=1",
                             (campaign_id,)).fetchone()[0]
        replied = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND replied=1",
                              (campaign_id,)).fetchone()[0]
        bounced = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status IN ('bounced','failed')",
                              (campaign_id,)).fetchone()[0]
        conn.close()

        metrics = {
            'sent': sent, 'opened': opened, 'replied': replied, 'bounced': bounced,
            'open_rate': round(opened / max(1, sent) * 100, 1),
            'reply_rate': round(replied / max(1, sent) * 100, 1),
            'bounce_rate': round(bounced / max(1, sent + bounced) * 100, 1),
        }

        user_prompt = f"""Analyze campaign "{camp['name']}":
- Sent: {sent}, Opened: {opened} ({metrics['open_rate']}%), Replied: {replied} ({metrics['reply_rate']}%)
- Bounced: {bounced} ({metrics['bounce_rate']}%)
- Status: {camp['job_status'] or camp['status']}

Industry benchmarks: Open 40-60%, Reply 3-8%, Bounce <3%.
What's working, what needs improvement?"""

        ai_response = self.call_ai(self.SYSTEM_PROMPT, user_prompt)
        try:
            result = json.loads(ai_response)
        except Exception:
            result = {'analysis': ai_response[:300], 'issues': [], 'recommendations': []}

        result['metrics'] = metrics
        return result

    def _suggest_strategy(self, input_data: dict) -> dict:
        """Suggest next campaign strategy based on workspace performance."""
        snapshot = self.analyze()
        user_prompt = f"""Workspace performance:
- Total sent: {snapshot['total_sent']}
- Open rate: {snapshot['open_rate']}%
- Reply rate: {snapshot['reply_rate']}%
- Active campaigns: {snapshot['active_campaigns']}

Suggest 3 specific, actionable improvements for their next campaign."""

        ai_response = self.call_ai(self.SYSTEM_PROMPT, user_prompt)
        try:
            return json.loads(ai_response)
        except Exception:
            return {'analysis': ai_response[:300], 'recommendations': []}

    def _compare(self, input_data: dict) -> dict:
        """Compare two campaigns."""
        ids = input_data.get('campaign_ids', [])
        if len(ids) < 2:
            return {'error': 'Need at least 2 campaign IDs'}

        conn = get_db()
        results = []
        for cid in ids[:2]:
            camp = conn.execute("SELECT name FROM campaigns WHERE id=?", (cid,)).fetchone()
            sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND status='sent'", (cid,)).fetchone()[0]
            opened = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND opened=1", (cid,)).fetchone()[0]
            replied = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE campaign_id=? AND replied=1", (cid,)).fetchone()[0]
            results.append({
                'id': cid, 'name': camp['name'] if camp else f'Campaign {cid}',
                'sent': sent, 'open_rate': round(opened / max(1, sent) * 100, 1),
                'reply_rate': round(replied / max(1, sent) * 100, 1),
            })
        conn.close()

        winner = results[0] if results[0]['reply_rate'] >= results[1]['reply_rate'] else results[1]
        return {'campaigns': results, 'winner': winner['name'], 'reason': 'Higher reply rate'}

    def _diagnose_failures(self, input_data: dict) -> dict:
        """Diagnose why a campaign has high failure rate."""
        campaign_id = input_data.get('campaign_id', 0)
        conn = get_db()
        failures = conn.execute("""
            SELECT bounce_reason, COUNT(*) as cnt FROM emails_sent
            WHERE campaign_id=? AND status IN ('bounced','failed')
            GROUP BY bounce_reason ORDER BY cnt DESC LIMIT 5
        """, (campaign_id,)).fetchall()
        conn.close()

        reasons = [{'reason': f['bounce_reason'] or 'unknown', 'count': f['cnt']} for f in failures]
        return {'failure_reasons': reasons, 'total_failures': sum(f['cnt'] for f in reasons)}
