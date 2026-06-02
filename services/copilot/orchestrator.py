"""
services/copilot/orchestrator.py — Main AI Orchestration Layer
================================================================
Routes user messages through:
  Intent Detection → Context Build → Prompt Compose → AI Call → Parse → Execute
"""
import json
import time
from datetime import datetime
from utils.db import get_db
from utils.logger import app_logger, error_logger
from services.copilot.context_builder import ContextBuilder
from services.copilot.action_registry import get_tools_json
from services.copilot.executor import ActionExecutor


# ── SYSTEM PROMPT ─────────────────────────────────────────────

SYSTEM_PROMPT_BASE = """You are the OutreachOS AI SDR Copilot — an enterprise sales development assistant embedded in a cold email campaign platform.

IDENTITY:
- Concise, action-oriented, data-driven
- You help SDRs run campaigns, manage leads, draft replies, and optimize deliverability
- You are proactive — surface issues before they escalate

CURRENT PAGE: {page_type}

PAGE CONTEXT:
{context_json}

WORKSPACE STATE:
{workspace_json}

ACTIVE ALERTS:
{alerts_json}

AVAILABLE ACTIONS (suggest these when appropriate):
{tools_json}

RESPONSE FORMAT (strict JSON, no markdown wrapping):
{{
  "message": "Your response (2-5 sentences, use **bold** for emphasis)",
  "actions": [
    {{"type": "action_name", "label": "Button Text", "params": {{}}, "risk": "safe|medium|high"}}
  ]
}}

RULES:
- Ground every answer in DATA from context — never hallucinate numbers
- If you don't have data for something, say so clearly
- For dangerous actions (send, cancel, delete), explain consequences first
- Prioritize by severity when multiple issues exist
- Be concise — the user is a busy SDR
- If the user asks something unrelated to OutreachOS, politely redirect
- Return EMPTY actions array if no action is needed
- Never suggest actions not in AVAILABLE ACTIONS list
"""


class CopilotOrchestrator:
    def __init__(self, workspace_id: int, user_id: int, role: str = 'admin'):
        self.wid = workspace_id
        self.uid = user_id
        self.role = role

    def chat(self, message: str, page_type: str, page_id: int, session_id: str = '') -> dict:
        """Main entry point for copilot chat."""
        start = time.time()

        # 1. Build context
        builder = ContextBuilder(self.wid, self.uid)
        ctx = builder.build(page_type, page_id)

        # 2. Build prompt
        tools = get_tools_json(page_type)
        system_prompt = SYSTEM_PROMPT_BASE.format(
            page_type=page_type,
            context_json=json.dumps(ctx.get('page', {}), default=str, indent=2)[:2000],
            workspace_json=json.dumps(ctx.get('workspace', {}), default=str),
            alerts_json=json.dumps(ctx.get('alerts', []), default=str),
            tools_json=json.dumps(tools, indent=2)[:3000],
        )

        # 3. Call AI
        response = self._call_ai(system_prompt, message)

        # 4. Parse response
        parsed = self._parse_response(response)

        # 5. Log conversation
        self._log_conversation(message, parsed, page_type, page_id, session_id)

        elapsed = int((time.time() - start) * 1000)
        app_logger.info(f'[COPILOT] Chat processed in {elapsed}ms | page={page_type}')

        return {
            'success': True,
            'message': parsed.get('message', ''),
            'actions': parsed.get('actions', []),
        }

    def execute_action(self, action_type: str, params: dict, session_id: str = '') -> dict:
        """Execute a confirmed action."""
        executor = ActionExecutor(self.wid, self.uid, self.role)
        return executor.execute(action_type, params, session_id)

    def get_suggestions(self, page_type: str, page_id: int) -> list:
        """Get proactive suggestions without user asking."""
        builder = ContextBuilder(self.wid, self.uid)
        ctx = builder.build(page_type, page_id)
        suggestions = []

        alerts = ctx.get('alerts', [])
        for alert in alerts[:3]:
            suggestions.append({
                'type': 'alert',
                'severity': alert.get('severity', 'info'),
                'message': alert.get('message', ''),
            })

        # Page-specific suggestions
        page_ctx = ctx.get('page', {})
        if page_type == 'contacts':
            enriched = page_ctx.get('enriched', 0)
            total = page_ctx.get('total', 0)
            if total > 0 and enriched < total * 0.5:
                suggestions.append({
                    'type': 'tip',
                    'message': f'{total - enriched} contacts unenriched — bulk enrich for better AI emails',
                    'action': {'type': 'bulk_enrich', 'label': 'Enrich All', 'params': {}}
                })
        elif page_type == 'campaign_status':
            camp = page_ctx.get('campaign', {})
            if camp.get('failed_count', 0) > 5:
                suggestions.append({
                    'type': 'tip',
                    'message': f'{camp["failed_count"]} emails failed — check SMTP or retry',
                    'action': {'type': 'diagnose_campaign', 'label': 'Diagnose',
                               'params': {'campaign_id': camp.get('id', 0)}}
                })

        return suggestions

    # ── AI CALL ───────────────────────────────────────────────

    def _call_ai(self, system_prompt: str, user_message: str) -> str:
        """Call Groq/Gemini with the assembled prompt."""
        import requests as http_requests
        from utils.db import get_setting

        # Try copilot-specific keys first, then email keys as fallback
        def _get(key):
            val = get_setting(f'copilot_{key}')
            return val if val else get_setting(f'email_{key}')

        # Groq
        keys_str = _get('groq_keys') or ''
        keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        model = get_setting('copilot_model_groq') or 'llama-3.3-70b-versatile'

        for key in keys:
            try:
                r = http_requests.post(
                    'https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                    json={
                        'model': model,
                        'messages': [
                            {'role': 'system', 'content': system_prompt},
                            {'role': 'user', 'content': user_message},
                        ],
                        'max_tokens': 600,
                        'temperature': 0.3,
                        'response_format': {'type': 'json_object'},
                    },
                    timeout=30,
                )
                if r.status_code == 200:
                    return r.json()['choices'][0]['message']['content'].strip()
                elif r.status_code == 429:
                    continue
            except Exception as e:
                error_logger.error(f'[COPILOT] Groq error: {e}')
                continue

        # Gemini fallback
        gemini_key = _get('gemini_key')
        if gemini_key:
            gemini_model = get_setting('copilot_model_gemini') or 'gemini-2.0-flash'
            try:
                full_prompt = f"{system_prompt}\n\nUSER: {user_message}\n\nRespond with valid JSON only."
                r = http_requests.post(
                    f'https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}',
                    json={'contents': [{'parts': [{'text': full_prompt}]}]},
                    timeout=30,
                )
                if r.status_code == 200:
                    return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            except Exception as e:
                error_logger.error(f'[COPILOT] Gemini error: {e}')

        return json.dumps({'message': 'AI temporarily unavailable. Please try again.', 'actions': []})

    # ── RESPONSE PARSING ──────────────────────────────────────

    def _parse_response(self, text: str) -> dict:
        """Parse AI response — handles JSON with/without markdown fences."""
        if not text:
            return {'message': 'No response from AI', 'actions': []}

        # Strip markdown code fences
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text[3:]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
            msg = data.get('message', '')
            actions = []
            for a in data.get('actions', []):
                if isinstance(a, dict) and 'type' in a and 'label' in a:
                    actions.append(a)
            return {'message': msg, 'actions': actions}
        except (json.JSONDecodeError, KeyError):
            return {'message': text[:500], 'actions': []}

    # ── LOGGING ───────────────────────────────────────────────

    def _log_conversation(self, user_msg: str, ai_response: dict,
                          page_type: str, page_id: int, session_id: str):
        try:
            conn = get_db()
            conn.execute("""
                INSERT INTO copilot_logs
                  (workspace_id, user_id, page_type, page_id, user_message,
                   ai_response, action_taken, created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (self.wid, self.uid, page_type, page_id,
                  user_msg[:500], json.dumps(ai_response)[:1000], '', datetime.now()))
            conn.commit()
            conn.close()
        except Exception:
            pass
