"""
services/copilot/orchestrator.py — Main AI Orchestration Layer (Phase 2)
=========================================================================
Routes user messages through:
  Intent Detection → Memory Recall → Context Build → Prompt Compose → AI Call → Parse → Execute → Memory Store
"""
import json
import time
from datetime import datetime
from utils.db import get_db
from services.copilot.context_builder import ContextBuilder
from services.copilot.action_registry import get_tools_json
from services.copilot.executor import ActionExecutor
from services.copilot.intent_detector import detect_intent
from services.copilot.memory import add_turn, get_history_prompt, get_user_context
from services.copilot.alerts import generate_alerts
from services.copilot.function_caller import should_auto_execute, auto_execute_action, parse_schedule_time, schedule_action
from services.copilot.learning import track_message, track_action, get_personalization_prompt, get_personalized_suggestions

try:
    from utils.logger import app_logger, error_logger
except Exception:
    import logging
    app_logger = logging.getLogger('campaign')
    error_logger = logging.getLogger('errors')


# ── SYSTEM PROMPT ─────────────────────────────────────────────

SYSTEM_PROMPT_BASE = """You are the OutreachOS AI SDR Copilot — an enterprise sales development assistant embedded in a cold email campaign platform.

IDENTITY:
- Concise, action-oriented, data-driven
- You help SDRs run campaigns, manage leads, draft replies, and optimize deliverability
- You are proactive — surface issues before they escalate

CURRENT PAGE: {page_type}
{history_block}
{preferences_block}

PAGE CONTEXT:
{context_json}

WORKSPACE STATE:
{workspace_json}

ACTIVE ALERTS:
{alerts_json}

DETECTED INTENT: {detected_intent} (confidence: {intent_confidence})

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
- Use DETECTED INTENT to focus your response appropriately
- Reference CONVERSATION HISTORY to maintain continuity
"""


# ── FAST-PATH RESPONSES (no AI call needed) ───────────────────

FAST_RESPONSES = {
    'greeting': {
        'message': "Hey! I'm your OutreachOS copilot. I can help with campaigns, leads, inbox, deliverability — what do you need?",
        'actions': [],
    },
    'help': {
        'message': "I can: **pause/resume campaigns**, **draft replies**, **diagnose deliverability**, **enrich contacts**, **generate reports**, and more. Just ask naturally!",
        'actions': [],
    },
}


class CopilotOrchestrator:
    def __init__(self, workspace_id: int, user_id: int, role: str = 'admin'):
        self.wid = workspace_id
        self.uid = user_id
        self.role = role

    def chat(self, message: str, page_type: str, page_id: int, session_id: str = '') -> dict:
        """Main entry point for copilot chat."""
        start = time.time()

        # 1. Detect intent locally (fast)
        intent_result = detect_intent(message, page_type)
        intent_name = intent_result['intent']
        confidence = intent_result['confidence']

        # 2. Fast-path for simple intents (no AI call)
        if intent_name in FAST_RESPONSES and confidence >= 0.8:
            resp = FAST_RESPONSES[intent_name]
            add_turn(self.wid, self.uid, 'user', message)
            add_turn(self.wid, self.uid, 'assistant', resp['message'])
            self._log_conversation(message, resp, page_type, page_id, session_id)
            return {'success': True, **resp}

        # 2.5 Phase 6: Check for scheduled actions
        schedule_time = parse_schedule_time(message)
        if schedule_time and intent_name in ('pause_campaign', 'resume_campaign', 'generate_report'):
            params = intent_result.get('entities', {})
            result = schedule_action(self.wid, self.uid, intent_name, params, schedule_time)
            add_turn(self.wid, self.uid, 'user', message)
            add_turn(self.wid, self.uid, 'assistant', result['message'])
            return {'success': True, 'message': result['message'], 'actions': [], 'intent': intent_name}

        # 2.6 Phase 6: Auto-execute safe actions when user confirms
        if should_auto_execute(message, intent_name, confidence):
            from services.copilot.function_caller import SAFE_AUTO_EXECUTE
            # Map intent to action
            intent_action_map = {
                'smtp_diagnose': 'diagnose_deliverability',
                'diagnose_campaign': 'diagnose_campaign',
                'report': 'generate_report',
                'best_send_time': 'predict_best_send_time',
            }
            action_type = intent_action_map.get(intent_name)
            if action_type and action_type in SAFE_AUTO_EXECUTE:
                params = intent_result.get('entities', {})
                result = auto_execute_action(self.wid, self.uid, action_type, params)
                if result.get('auto_executed') and result.get('success'):
                    msg = result.get('message', 'Done')
                    add_turn(self.wid, self.uid, 'user', message)
                    add_turn(self.wid, self.uid, 'assistant', msg)
                    return {'success': True, 'message': msg, 'actions': [], 'intent': intent_name, 'auto_executed': True}

        # 2.7 Phase 7: Track user behavior
        track_message(self.wid, self.uid, message, page_type)

        # 3. Build context
        builder = ContextBuilder(self.wid, self.uid)
        ctx = builder.build(page_type, page_id)

        # 3.5 Multi-agent routing for high-confidence known intents
        agent_context = ''
        if confidence >= 0.8 and intent_name in ('diagnose_campaign', 'smtp_diagnose', 'report', 'best_send_time'):
            from services.copilot.agents.router import route_to_agent
            agent_result = route_to_agent(intent_name, self.wid, intent_result.get('entities', {}))
            if agent_result.get('success') or agent_result.get('multi_agent'):
                agent_context = f"\nAGENT ANALYSIS:\n{json.dumps(agent_result, default=str)[:1500]}"

        # 4. Get conversation history + preferences + personalization
        history_block = get_history_prompt(self.wid, self.uid)
        preferences_block = get_user_context(self.wid, self.uid)
        personalization_block = get_personalization_prompt(self.wid, self.uid)

        # 5. Build prompt
        tools = get_tools_json(page_type)
        system_prompt = SYSTEM_PROMPT_BASE.format(
            page_type=page_type,
            history_block=history_block + agent_context + personalization_block,
            preferences_block=preferences_block,
            context_json=json.dumps(ctx.get('page', {}), default=str, indent=2)[:2000],
            workspace_json=json.dumps(ctx.get('workspace', {}), default=str),
            alerts_json=json.dumps(ctx.get('alerts', []), default=str),
            detected_intent=intent_name,
            intent_confidence=f"{confidence:.0%}",
            tools_json=json.dumps(tools, indent=2)[:3000],
        )

        # 6. Call AI
        response = self._call_ai(system_prompt, message)

        # 7. Parse response
        parsed = self._parse_response(response)

        # 8. Store in memory
        add_turn(self.wid, self.uid, 'user', message)
        add_turn(self.wid, self.uid, 'assistant', parsed.get('message', ''))

        # 9. Log conversation
        self._log_conversation(message, parsed, page_type, page_id, session_id)

        elapsed = int((time.time() - start) * 1000)
        app_logger.info(f'[COPILOT] Chat in {elapsed}ms | intent={intent_name} conf={confidence:.0%} | page={page_type}')

        return {
            'success': True,
            'message': parsed.get('message', ''),
            'actions': parsed.get('actions', []),
            'intent': intent_name,
        }

    def execute_action(self, action_type: str, params: dict, session_id: str = '') -> dict:
        """Execute a confirmed action."""
        executor = ActionExecutor(self.wid, self.uid, self.role)
        return executor.execute(action_type, params, session_id)

    def get_suggestions(self, page_type: str, page_id: int) -> list:
        """Get proactive suggestions without user asking."""
        suggestions = []

        # Get alerts
        alerts = generate_alerts(self.wid)
        for alert in alerts[:3]:
            suggestions.append({
                'type': 'alert',
                'severity': alert.get('severity', 'info'),
                'message': alert.get('message', ''),
                'action': alert.get('action'),
            })

        # Page-specific suggestions
        builder = ContextBuilder(self.wid, self.uid)
        ctx = builder.build(page_type, page_id)
        page_ctx = ctx.get('page', {})

        if page_type == 'contacts':
            total = page_ctx.get('total', 0)
            enriched = page_ctx.get('enriched', 0)
            if total > 0 and enriched < total * 0.5:
                suggestions.append({
                    'type': 'tip',
                    'message': f'{total - enriched} contacts unenriched — bulk enrich for better AI emails',
                    'action': {'type': 'bulk_enrich', 'label': 'Enrich All', 'params': {}},
                })
        elif page_type == 'campaign_status':
            camp = page_ctx.get('campaign', {})
            if camp.get('failed_count', 0) > 5:
                suggestions.append({
                    'type': 'tip',
                    'message': f'{camp["failed_count"]} emails failed — diagnose SMTP or retry',
                    'action': {'type': 'diagnose_campaign', 'label': 'Diagnose', 'params': {'campaign_id': camp.get('id', 0)}},
                })
        elif page_type == 'inbox':
            unread = page_ctx.get('unread', 0)
            if unread > 5:
                suggestions.append({
                    'type': 'tip',
                    'message': f'{unread} unread threads — want me to summarize the important ones?',
                    'action': {'type': 'summarize_thread', 'label': 'Summarize', 'params': {}},
                })

        return suggestions

    def get_alerts(self) -> list:
        """Get all active alerts for workspace."""
        return generate_alerts(self.wid)

    # ── AI CALL ───────────────────────────────────────────────

    def _call_ai(self, system_prompt: str, user_message: str) -> str:
        """Call Groq/Gemini with the assembled prompt."""
        import requests as http_requests
        from utils.db import get_db

        def _get_setting(key):
            conn = get_db()
            try:
                row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                return row[0] if row else ''
            finally:
                conn.close()

        # Try copilot-specific keys first, then email keys as fallback
        def _get(key):
            val = _get_setting(f'copilot_{key}')
            return val if val else _get_setting(f'email_{key}')

        # Groq
        keys_str = _get('groq_keys') or ''
        keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        model = _get_setting('copilot_model_groq') or 'llama-3.3-70b-versatile'

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
            gemini_model = _get_setting('copilot_model_gemini') or 'gemini-2.0-flash'
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
