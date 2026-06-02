"""
services/copilot/handlers/sequence.py — Sequence Action Handlers
"""
from utils.db import get_db, get_setting


def create_sequence(workspace_id: int, user_id: int, campaign_id: int,
                    steps: int = 3, audience: str = '', **_) -> dict:
    from services.sequence_engine import add_step
    conn = get_db()
    camp = conn.execute("SELECT id, name FROM campaigns WHERE id=? AND workspace_id=?",
                        (campaign_id, workspace_id)).fetchone()
    if not camp:
        conn.close()
        raise ValueError('Campaign not found')
    # Check if steps already exist
    existing = conn.execute("SELECT COUNT(*) FROM sequence_steps WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
    conn.close()
    if existing > 0:
        return {'message': f'Campaign already has {existing} steps — edit existing or delete first'}

    # Create default multi-step sequence
    default_steps = [
        {'delay': 0, 'subject': 'Initial outreach', 'type': 'email'},
        {'delay': 3, 'subject': 'Follow-up if no reply', 'type': 'follow_up'},
        {'delay': 5, 'subject': 'Final check-in', 'type': 'follow_up'},
    ][:steps]

    for i, s in enumerate(default_steps, 1):
        add_step(
            campaign_id=campaign_id,
            workspace_id=workspace_id,
            step_order=i,
            step_type=s['type'],
            delay_days=s['delay'],
            subject=s['subject'],
            body='',
            ai_enabled=True,
        )

    return {'message': f'{steps}-step sequence created for "{camp["name"]}" — edit steps in Sequence Builder'}


def generate_step(workspace_id: int, user_id: int, campaign_id: int,
                  step_order: int = 1, step_type: str = 'follow_up', **_) -> dict:
    import requests as http_requests

    keys_str = get_setting('copilot_groq_keys') or get_setting('email_groq_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    if not keys:
        raise ValueError('No AI keys configured')

    prompt = f"""Write a short {step_type} email for step {step_order} of a cold outreach sequence.

Rules:
- If step 1: Initial outreach, reference company research
- If step 2+: Brief follow-up referencing previous email
- 2-3 sentences max
- Casual, direct tone
- End with simple CTA
- Output as HTML with <p> tags
- Use {{name}} and {{company}} as placeholders"""

    r = http_requests.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization': f'Bearer {keys[0]}', 'Content-Type': 'application/json'},
        json={'model': 'llama-3.3-70b-versatile',
              'messages': [{'role': 'user', 'content': prompt}],
              'max_tokens': 300},
        timeout=20
    )
    if r.status_code == 200:
        content = r.json()['choices'][0]['message']['content'].strip()
        return {'message': 'Step content generated', 'content': content}
    raise ValueError(f'AI returned {r.status_code}')
