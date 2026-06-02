"""
services/copilot/agents/research_agent.py — Lead Research Agent
================================================================
Lead enrichment, company intelligence, ICP scoring.
"""
import json
from utils.db import get_db
from services.copilot.agents.base_agent import BaseAgent


class ResearchAgent(BaseAgent):
    agent_type = 'research'
    description = 'Lead enrichment, company research, ICP scoring'
    capabilities = ['enrich_lead', 'company_research', 'icp_score', 'find_decision_makers']

    SYSTEM_PROMPT = """You are the Lead Research Agent for OutreachOS.
You research companies and contacts to provide enrichment data for cold outreach.

RESPOND IN JSON:
{
  "company_summary": "What the company does (1-2 sentences)",
  "industry": "industry category",
  "employee_size": "range estimate",
  "tech_signals": ["tech1", "tech2"],
  "pain_points": ["pain1", "pain2"],
  "outreach_angle": "Best angle for cold email",
  "icp_score": 0-100
}"""

    def _execute(self, task_type: str, input_data: dict) -> dict:
        dispatch = {
            'enrich_lead': self._enrich_lead,
            'company_research': self._company_research,
            'icp_score': self._icp_score,
            'find_decision_makers': self._find_dms,
        }
        handler = dispatch.get(task_type, self._enrich_lead)
        return handler(input_data)

    def analyze(self, input_data: dict = None) -> dict:
        """Quick enrichment status."""
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM contacts WHERE workspace_id=?", (self.wid,)).fetchone()[0]
        enriched = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE workspace_id=? AND context IS NOT NULL AND context != ''",
            (self.wid,)
        ).fetchone()[0]
        conn.close()
        return {'total': total, 'enriched': enriched, 'unenriched': total - enriched,
                'enrichment_rate': round(enriched / max(1, total) * 100, 1)}

    def _enrich_lead(self, input_data: dict) -> dict:
        """Enrich a single contact with AI research."""
        contact_id = input_data.get('contact_id', 0)
        conn = get_db()
        contact = conn.execute("SELECT * FROM contacts WHERE id=? AND workspace_id=?",
                              (contact_id, self.wid)).fetchone()
        if not contact:
            conn.close()
            return {'error': 'Contact not found'}

        company = contact['company'] or ''
        domain = contact['email'].split('@')[1] if '@' in contact['email'] else ''
        conn.close()

        user_prompt = f"""Research this lead:
- Name: {contact['name']}
- Company: {company}
- Email domain: {domain}
- Designation: {contact.get('designation', '')}

Provide company intelligence and ICP scoring for a B2B tech staffing company selling engineering talent."""

        ai_response = self.call_ai(self.SYSTEM_PROMPT, user_prompt)
        try:
            result = json.loads(ai_response)
        except Exception:
            result = {'company_summary': ai_response[:200], 'icp_score': 50}

        # Save enrichment
        context = result.get('company_summary', '') or result.get('outreach_angle', '')
        if context:
            conn = get_db()
            conn.execute("UPDATE contacts SET context=? WHERE id=?", (context[:500], contact_id))
            conn.commit()
            conn.close()

        return result

    def _company_research(self, input_data: dict) -> dict:
        """Research a company by name/domain."""
        company = input_data.get('company', '')
        domain = input_data.get('domain', '')

        user_prompt = f"""Research this company:
Company: {company}
Domain: {domain}

What do they do? Industry? Size? Any recent funding or news?"""

        ai_response = self.call_ai(self.SYSTEM_PROMPT, user_prompt)
        try:
            return json.loads(ai_response)
        except Exception:
            return {'company_summary': ai_response[:300]}

    def _icp_score(self, input_data: dict) -> dict:
        """Score a contact against ICP criteria."""
        contact_id = input_data.get('contact_id', 0)
        conn = get_db()
        contact = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        conn.close()
        if not contact:
            return {'error': 'Contact not found'}

        # Simple rule-based scoring
        score = 50
        if contact.get('designation') and any(t in (contact['designation'] or '').lower() for t in ['cto', 'vp', 'head', 'director', 'founder', 'ceo']):
            score += 20
        if contact.get('lead_score') and contact['lead_score'] > 50:
            score += 15
        if contact.get('context'):
            score += 10
        if contact.get('email_valid') == 1:
            score += 5

        return {'contact_id': contact_id, 'icp_score': min(100, score)}

    def _find_dms(self, input_data: dict) -> dict:
        """Find decision makers in a company from existing contacts."""
        company = input_data.get('company', '')
        conn = get_db()
        contacts = conn.execute("""
            SELECT id, name, designation, email, lead_score FROM contacts
            WHERE workspace_id=? AND LOWER(company) LIKE ?
            ORDER BY COALESCE(lead_score, 0) DESC LIMIT 5
        """, (self.wid, f'%{company.lower()}%')).fetchall()
        conn.close()

        return {'company': company, 'decision_makers': [dict(c) for c in contacts]}
