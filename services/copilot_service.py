"""
services/copilot_service.py — Outreach Copilot Engine
======================================================
Page-aware AI assistant. Returns message + suggested actions.
No auto-execution. All actions require user confirmation.
"""
import json
import time
from datetime import datetime
from utils.db import get_db
from utils.logger import app_logger, error_logger

# ── ACTION TYPES (safe = info only, confirm = needs user click) ──
SAFE_ACTIONS = {'show_info', 'navigate', 'copy_text'}
CONFIRM_ACTIONS = {'retry_failed', 'pause_campaign', 'resume_campaign',
                   'cancel_campaign', 'draft_reply', 'send_reply',
                   'enrich_contact', 'bulk_enrich'}


def get_page_context(page_type: str, page_id: int, workspace_id: int) -> dict:
    """Build minimal, page-specific context for AI prompt."""
    conn = get_db()
    ctx = {'page': page_type, 'page_id': page_id}

    if page_type == 'campaign_status':
        camp = conn.execute("""
            SELECT id, name, job_status, send_mode, total_contacts,
                   sent_count, failed_count, started_at, completed_at
            FROM campaigns WHERE id=? AND workspace_id=?
        """, (page_id, workspace_id)).fetchone()
        if camp:
            ctx['campaign'] = dict(camp)
            # Recent failures
            failures = conn.execute("""
                SELECT c.name, c.email, es.bounce_reason
                FROM emails_sent es JOIN contacts c ON es.contact_id=c.id
                WHERE es.campaign_id=? AND es.status IN ('failed','bounced')
                ORDER BY es.sent_at DESC LIMIT 5
            """, (page_id,)).fetchall()
            ctx['recent_failures'] = [dict(f) for f in failures]
            # SMTP health
            smtp = conn.execute("""
                SELECT email, health_score, sent_today, daily_limit, active
                FROM smtp_accounts WHERE workspace_id=? AND active=1
            """, (workspace_id,)).fetchall()
            ctx['smtp_accounts'] = [dict(s) for s in smtp]
            # Recent logs
            logs = conn.execute("""
                SELECT level, message, created_at FROM campaign_logs
                WHERE campaign_id=? ORDER BY created_at DESC LIMIT 8
            """, (page_id,)).fetchall()
            ctx['recent_logs'] = [dict(l) for l in logs]

    elif page_type == 'inbox_thread':
        thread = conn.execute("""
            SELECT t.*, c.name as contact_name, c.company as contact_company,
                   c.email as contact_email, c.context as contact_context,
                   c.lead_score, c.status as contact_status
            FROM threads t
            LEFT JOIN contacts c ON t.contact_id = c.id
            WHERE t.id=? AND t.workspace_id=?
        """, (page_id, workspace_id)).fetchone()
        if thread:
            ctx['thread'] = {
                'id': thread['id'], 'status': thread['status'],
                'subject': thread['subject'],
                'contact_name': thread['contact_name'],
                'contact_company': thread['contact_company'],
                'contact_email': thread['contact_email'],
                'contact_context': thread['contact_context'],
                'lead_score': thread['lead_score'],
                'contact_status': thread['contact_status'],
            }
            # Last 5 messages (trimmed)
            msgs = conn.execute("""
                SELECT direction, body, ai_category, created_at
                FROM messages WHERE thread_id=?
                ORDER BY created_at DESC LIMIT 5
            """, (page_id,)).fetchall()
            ctx['messages'] = [
                {'direction': m['direction'],
                 'body': (m['body'] or '')[:300],
                 'ai_category': m['ai_category'],
                 'time': m['created_at']}
                for m in reversed(msgs)
            ]

    elif page_type == 'contacts':
        stats = {}
        stats['total'] = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()[0]
        stats['valid'] = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE workspace_id=? AND email_valid=1", (workspace_id,)
        ).fetchone()[0]
        stats['enriched'] = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE workspace_id=? AND enrichment_status='done'", (workspace_id,)
        ).fetchone()[0]
        ctx['stats'] = stats

    elif page_type == 'dashboard':
        ctx['total_sent'] = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent' AND workspace_id=?", (workspace_id,)).fetchone()[0]
        ctx['total_contacts'] = conn.execute("SELECT COUNT(*) FROM contacts WHERE workspace_id=?", (workspace_id,)).fetchone()[0]
        ctx['total_bounced'] = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed') AND workspace_id=?", (workspace_id,)).fetchone()[0]
        ctx['active_campaigns'] = conn.execute("SELECT COUNT(*) FROM campaigns WHERE status='sent' AND workspace_id=?", (workspace_id,)).fetchone()[0]

    conn.close()
    return ctx


def build_system_prompt(page_type: str) -> str:
    """Page-specific system prompt for the AI."""
    base = """You are Outreach Copilot — an AI assistant embedded in OutreachOS, an email campaign platform.
You help users understand campaign performance, diagnose issues, and suggest actions.

RULES:
- Be concise (2-4 sentences max unless asked for detail)
- When suggesting actions, return them in the "actions" field
- Never auto-execute dangerous actions
- If you suggest an action, explain WHY
- Use data from the page context to give specific answers
- If you don't know, say so — don't hallucinate

RESPONSE FORMAT (JSON):
{"message": "your response text", "actions": [{"type": "action_type", "label": "Button Label", "params": {}}]}

Action types available:
- retry_failed: Retry failed emails (params: {campaign_id})
- pause_campaign: Pause running campaign (params: {campaign_id})
- resume_campaign: Resume paused campaign (params: {campaign_id})
- cancel_campaign: Cancel campaign (params: {campaign_id})
- draft_reply: Generate AI reply draft (params: {thread_id})
- send_reply: Send reply (params: {thread_id, body})
- navigate: Go to a page (params: {url})
- copy_text: Copy text to clipboard (params: {text})
- show_info: Display additional info (params: {info})

Only suggest actions that make sense for the current situation. Return empty actions array if no action needed."""

    if page_type == 'campaign_status':
        base += """

PAGE: Campaign Status — user is watching a live/completed campaign execution.
You can help with:
- Diagnosing why emails are failing (SMTP auth, bounces, rate limits)
- Explaining campaign progress and ETA
- Suggesting pause/resume/cancel based on failure rate
- Identifying SMTP health issues
- Recommending retry for failed contacts"""

    elif page_type == 'inbox_thread':
        base += """

PAGE: Inbox Thread — user is reading a conversation with a contact.
You can help with:
- Drafting reply emails based on conversation context
- Analyzing the contact's intent (interested, not interested, meeting request)
- Suggesting next steps based on AI category
- Providing context about the contact/company
- Recommending thread status changes"""

    elif page_type == 'contacts':
        base += """

PAGE: Contacts — user is managing their contact list.
You can help with:
- Explaining contact stats (valid, enriched, etc.)
- Suggesting enrichment for unenriched contacts
- Filtering advice
- Lead scoring explanations"""

    elif page_type == 'dashboard':
        base += """

PAGE: Dashboard — user is viewing the command center overview.
You can help with:
- Summarizing overall performance
- Identifying issues (high bounce rate, low open rate)
- Suggesting next actions (new campaign, check replies, fix SMTP)
- Explaining metrics"""

    return base


def call_ai(system_prompt: str, user_msg: str, context: dict, workspace_id: int = 1) -> dict:
    """Call Groq/Gemini with copilot prompt. Returns parsed response."""
    import requests as http_requests
    from utils.db import get_workspace_only_setting, get_setting as _fallback_setting

    def _get_setting(key):
        val = get_workspace_only_setting(key, workspace_id)
        return val if val else _fallback_setting(key)

    # Build the full prompt
    context_str = json.dumps(context, default=str, indent=2)
    full_prompt = f"""{system_prompt}

PAGE CONTEXT:
{context_str}

USER QUESTION: {user_msg}

Respond with valid JSON only. No markdown wrapping."""

    # Try Groq first
    keys_str = _get_setting('groq_api_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]

    for key in keys:
        try:
            r = http_requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={
                    'model': 'llama-3.3-70b-versatile',
                    'messages': [
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': f"PAGE CONTEXT:\n{context_str}\n\nUSER: {user_msg}"}
                    ],
                    'max_tokens': 500,
                    'temperature': 0.3,
                    'response_format': {'type': 'json_object'}
                },
                timeout=30
            )
            if r.status_code == 200:
                text = r.json()['choices'][0]['message']['content'].strip()
                return _parse_response(text)
            elif r.status_code == 429:
                continue
        except Exception as e:
            error_logger.error(f'[COPILOT] Groq error: {e}')
            continue

    # Fallback: Gemini
    gemini_key = _get_setting('gemini_api_key')
    if gemini_key:
        try:
            r = http_requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}',
                json={'contents': [{'parts': [{'text': full_prompt}]}]},
                timeout=30
            )
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                return _parse_response(text)
        except Exception as e:
            error_logger.error(f'[COPILOT] Gemini error: {e}')

    return {'message': 'AI is temporarily unavailable. Please try again.', 'actions': []}


def _parse_response(text: str) -> dict:
    """Parse AI response — handles JSON with or without markdown wrapping."""
    # Strip markdown code fences if present
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
        msg = data.get('message', '')
        actions = data.get('actions', [])
        # Validate actions
        valid_actions = []
        for a in actions:
            if isinstance(a, dict) and 'type' in a and 'label' in a:
                valid_actions.append(a)
        return {'message': msg, 'actions': valid_actions}
    except (json.JSONDecodeError, KeyError):
        # If AI didn't return valid JSON, use raw text as message
        return {'message': text[:500], 'actions': []}


def log_copilot_action(workspace_id: int, user_id: int, page_type: str,
                       page_id: int, user_msg: str, ai_response: str,
                       action_taken: str = ''):
    """Log every copilot interaction for audit."""
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO copilot_logs
              (workspace_id, user_id, page_type, page_id, user_message,
               ai_response, action_taken, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (workspace_id, user_id, page_type, page_id,
              user_msg[:500], ai_response[:1000], action_taken, datetime.now()))
        conn.commit()
        conn.close()
    except Exception as e:
        # Table might not exist yet — silently fail
        error_logger.warning(f'[COPILOT] Log failed: {e}')


def execute_action(action_type: str, params: dict, workspace_id: int) -> dict:
    """Execute a confirmed action. Returns result dict."""
    if action_type not in CONFIRM_ACTIONS and action_type not in SAFE_ACTIONS:
        return {'success': False, 'error': 'Unknown action type'}

    if action_type == 'draft_reply':
        thread_id = params.get('thread_id')
        if not thread_id:
            return {'success': False, 'error': 'No thread_id'}
        conn = get_db()
        thread = conn.execute("""
            SELECT t.*, c.name as contact_name, c.company as contact_company,
                   c.context as contact_context
            FROM threads t LEFT JOIN contacts c ON t.contact_id = c.id
            WHERE t.id=? AND t.workspace_id=?
        """, (thread_id, workspace_id)).fetchone()
        conn.close()
        if not thread:
            return {'success': False, 'error': 'Thread not found'}
        from services.inbox_service import generate_ai_reply_draft
        draft = generate_ai_reply_draft(
            thread_id,
            thread['contact_name'] or 'there',
            thread['contact_company'] or '',
            thread['contact_context'] or ''
        )
        if draft:
            return {'success': True, 'draft': draft}
        return {'success': False, 'error': 'AI generation failed'}

    elif action_type == 'retry_failed':
        campaign_id = params.get('campaign_id')
        if not campaign_id:
            return {'success': False, 'error': 'No campaign_id'}
        conn = get_db()
        camp = conn.execute(
            'SELECT id FROM campaigns WHERE id=? AND workspace_id=?',
            (campaign_id, workspace_id)
        ).fetchone()
        if not camp:
            conn.close()
            return {'success': False, 'error': 'Campaign not found'}
        failed = conn.execute("""
            SELECT id FROM emails_sent
            WHERE campaign_id=? AND status IN ('failed','bounced')
        """, (campaign_id,)).fetchall()
        conn.close()
        count = len(failed)
        if count == 0:
            return {'success': True, 'message': 'No failed emails to retry'}
        return {'success': True, 'message': f'{count} failed emails found (use campaign retry to re-send)'}

    elif action_type in ('pause_campaign', 'resume_campaign', 'cancel_campaign'):
        campaign_id = params.get('campaign_id')
        if not campaign_id:
            return {'success': False, 'error': 'No campaign_id'}
        # Verify ownership
        conn = get_db()
        camp = conn.execute(
            "SELECT id FROM campaigns WHERE id=? AND workspace_id=?",
            (campaign_id, workspace_id)
        ).fetchone()
        conn.close()
        if not camp:
            return {'success': False, 'error': 'Campaign not found'}
        # Execute the action directly
        from services.campaign_executor import pause_campaign, resume_campaign, cancel_campaign
        if action_type == 'pause_campaign':
            pause_campaign(campaign_id, workspace_id)
            return {'success': True, 'message': 'Campaign paused'}
        elif action_type == 'resume_campaign':
            result = resume_campaign(campaign_id, workspace_id)
            return {'success': bool(result), 'message': 'Campaign resumed' if result else 'Nothing to resume'}
        else:
            cancel_campaign(campaign_id, workspace_id)
            return {'success': True, 'message': 'Campaign cancelled'}

    return {'success': False, 'error': 'Action not implemented'}
