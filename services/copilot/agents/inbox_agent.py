"""
services/copilot/agents/inbox_agent.py — Inbox Intelligence Agent
==================================================================
Reply prioritization, auto-draft responses, thread summarization.
"""
import json
from utils.db import get_db
from services.copilot.agents.base_agent import BaseAgent


class InboxAgent(BaseAgent):
    agent_type = 'inbox'
    description = 'Reply prioritization, AI drafts, thread summarization'
    capabilities = ['prioritize_inbox', 'draft_reply', 'summarize_threads', 'classify_reply']

    SYSTEM_PROMPT = """You are the Inbox Intelligence Agent for OutreachOS.
You help SDRs manage their inbox efficiently — prioritize replies, draft responses, and summarize conversations.

RESPOND IN JSON:
{
  "message": "Your response",
  "draft": "HTML email draft if requested, else empty string",
  "priority_threads": [{"thread_id": 0, "reason": "why important", "urgency": "high|medium|low"}]
}"""

    def _execute(self, task_type: str, input_data: dict) -> dict:
        dispatch = {
            'prioritize_inbox': self._prioritize,
            'draft_reply': self._draft_reply,
            'summarize_threads': self._summarize,
            'classify_reply': self._classify,
        }
        handler = dispatch.get(task_type, self._prioritize)
        return handler(input_data)

    def analyze(self, input_data: dict = None) -> dict:
        """Quick inbox snapshot."""
        conn = get_db()
        unread = conn.execute(
            "SELECT COUNT(*) FROM threads WHERE workspace_id=? AND unread_count > 0", (self.wid,)
        ).fetchone()[0]
        interested = conn.execute(
            "SELECT COUNT(*) FROM threads WHERE workspace_id=? AND status='interested'", (self.wid,)
        ).fetchone()[0]
        meeting = conn.execute(
            "SELECT COUNT(*) FROM threads WHERE workspace_id=? AND status='meeting'", (self.wid,)
        ).fetchone()[0]
        conn.close()
        return {'unread': unread, 'interested': interested, 'meeting': meeting}

    def _prioritize(self, input_data: dict) -> dict:
        """Prioritize inbox threads by urgency."""
        conn = get_db()
        threads = conn.execute("""
            SELECT t.id, t.status, t.unread_count, t.last_message_at,
                   c.name, c.company, c.lead_score
            FROM threads t
            LEFT JOIN contacts c ON t.contact_id = c.id
            WHERE t.workspace_id=? AND (t.unread_count > 0 OR t.status IN ('interested','meeting'))
            ORDER BY
                CASE t.status WHEN 'meeting' THEN 1 WHEN 'interested' THEN 2 ELSE 3 END,
                COALESCE(c.lead_score, 0) DESC
            LIMIT 10
        """, (self.wid,)).fetchall()
        conn.close()

        priority_list = []
        for t in threads:
            urgency = 'high' if t['status'] in ('meeting', 'interested') else 'medium' if t['lead_score'] and t['lead_score'] > 50 else 'low'
            priority_list.append({
                'thread_id': t['id'],
                'contact': f"{t['name']} ({t['company']})" if t['name'] else 'Unknown',
                'status': t['status'],
                'lead_score': t['lead_score'] or 0,
                'urgency': urgency,
                'reason': f"{'Meeting request' if t['status']=='meeting' else 'Interested reply' if t['status']=='interested' else 'Unread'}",
            })

        return {'priority_threads': priority_list}

    def _draft_reply(self, input_data: dict) -> dict:
        """AI-draft a reply for a thread."""
        thread_id = input_data.get('thread_id', 0)
        conn = get_db()
        thread = conn.execute("""
            SELECT t.*, c.name as contact_name, c.company, c.context
            FROM threads t LEFT JOIN contacts c ON t.contact_id = c.id
            WHERE t.id=? AND t.workspace_id=?
        """, (thread_id, self.wid)).fetchone()

        if not thread:
            conn.close()
            return {'error': 'Thread not found'}

        messages = conn.execute("""
            SELECT direction, body, created_at FROM messages
            WHERE thread_id=? ORDER BY created_at DESC LIMIT 5
        """, (thread_id,)).fetchall()
        conn.close()

        convo = "\n".join([
            f"{'THEM' if m['direction']=='incoming' else 'US'}: {(m['body'] or '')[:200]}"
            for m in reversed(messages)
        ])

        user_prompt = f"""Draft a reply for this conversation:
Contact: {thread['contact_name']} at {thread['company']}
Context: {thread['context'] or 'No context'}
Thread status: {thread['status']}

Recent messages:
{convo}

Write a concise, professional reply. Keep it under 4 sentences. Match the tone of the conversation."""

        ai_response = self.call_ai(self.SYSTEM_PROMPT, user_prompt)
        try:
            result = json.loads(ai_response)
            return {'draft': result.get('draft', result.get('message', ''))}
        except Exception:
            return {'draft': ai_response[:500]}

    def _summarize(self, input_data: dict) -> dict:
        """Summarize unread threads."""
        conn = get_db()
        threads = conn.execute("""
            SELECT t.id, t.status, c.name, c.company,
                   (SELECT body FROM messages WHERE thread_id=t.id ORDER BY created_at DESC LIMIT 1) as last_msg
            FROM threads t
            LEFT JOIN contacts c ON t.contact_id = c.id
            WHERE t.workspace_id=? AND t.unread_count > 0
            ORDER BY t.last_message_at DESC LIMIT 5
        """, (self.wid,)).fetchall()
        conn.close()

        summaries = []
        for t in threads:
            summaries.append({
                'thread_id': t['id'],
                'from': f"{t['name']} ({t['company']})" if t['name'] else 'Unknown',
                'status': t['status'],
                'preview': (t['last_msg'] or '')[:100],
            })

        return {'summaries': summaries, 'total_unread': len(summaries)}

    def _classify(self, input_data: dict) -> dict:
        """Classify a reply's intent."""
        body = input_data.get('body', '')
        if not body:
            return {'category': 'unknown'}

        positive = ['interested', 'call', 'meeting', 'schedule', 'tell me more', 'available']
        negative = ['not interested', 'unsubscribe', 'remove', 'stop', 'no thanks']
        ooo = ['out of office', 'vacation', 'away', 'returning']

        body_lower = body.lower()
        if any(w in body_lower for w in ooo):
            return {'category': 'ooo'}
        if any(w in body_lower for w in negative):
            return {'category': 'not_interested'}
        if any(w in body_lower for w in positive):
            return {'category': 'interested'}
        return {'category': 'neutral'}
