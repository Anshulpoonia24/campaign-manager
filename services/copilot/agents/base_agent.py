"""
services/copilot/agents/base_agent.py — Base Agent Class
=========================================================
All specialized agents inherit from this. Provides:
- AI call with agent-specific system prompt
- Task logging to agent_tasks table
- Inter-agent communication via task delegation
"""
import json
import time
from datetime import datetime
from utils.db import get_db

try:
    from utils.logger import app_logger, error_logger
except Exception:
    import logging
    app_logger = logging.getLogger('campaign')
    error_logger = logging.getLogger('errors')


class BaseAgent:
    """Base class for all AI SDR agents."""

    agent_type = 'base'
    description = 'Base agent'
    capabilities = []

    def __init__(self, workspace_id: int):
        self.wid = workspace_id

    def run(self, task_type: str, input_data: dict) -> dict:
        """Execute a task and log it."""
        start = time.time()
        task_id = self._log_task_start(task_type, input_data)

        try:
            result = self._execute(task_type, input_data)
            elapsed = int((time.time() - start) * 1000)
            self._log_task_complete(task_id, result, elapsed)
            app_logger.info(f'[AGENT:{self.agent_type}] {task_type} done in {elapsed}ms')
            return {'success': True, **result}
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            self._log_task_failed(task_id, str(e), elapsed)
            error_logger.error(f'[AGENT:{self.agent_type}] {task_type} failed: {e}')
            return {'success': False, 'error': str(e)[:200]}

    def _execute(self, task_type: str, input_data: dict) -> dict:
        """Override in subclass."""
        raise NotImplementedError

    def analyze(self, input_data: dict) -> dict:
        """Quick analysis without full AI call — override in subclass."""
        return {}

    def call_ai(self, system_prompt: str, user_prompt: str) -> str:
        """Shared AI call method for all agents."""
        import requests as http_requests

        def _get_setting(key):
            conn = get_db()
            try:
                row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                return row[0] if row else ''
            finally:
                conn.close()

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
                            {'role': 'user', 'content': user_prompt},
                        ],
                        'max_tokens': 800,
                        'temperature': 0.2,
                        'response_format': {'type': 'json_object'},
                    },
                    timeout=30,
                )
                if r.status_code == 200:
                    return r.json()['choices'][0]['message']['content'].strip()
                elif r.status_code == 429:
                    continue
            except Exception:
                continue

        # Gemini fallback
        gemini_key = _get('gemini_key')
        if gemini_key:
            model = _get_setting('copilot_model_gemini') or 'gemini-2.0-flash'
            try:
                full = f"{system_prompt}\n\n{user_prompt}\n\nRespond with valid JSON only."
                r = http_requests.post(
                    f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}',
                    json={'contents': [{'parts': [{'text': full}]}]},
                    timeout=30,
                )
                if r.status_code == 200:
                    return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            except Exception:
                pass

        return json.dumps({'error': 'AI unavailable'})

    def delegate(self, agent_type: str, task_type: str, input_data: dict) -> dict:
        """Delegate a task to another agent."""
        from services.copilot.agents.router import get_agent
        agent = get_agent(agent_type, self.wid)
        if agent:
            return agent.run(task_type, input_data)
        return {'success': False, 'error': f'Agent {agent_type} not found'}

    # ── Task logging ──────────────────────────────────────────

    def _log_task_start(self, task_type: str, input_data: dict) -> int:
        try:
            conn = get_db()
            from utils.db import is_postgres
            if is_postgres():
                row = conn.execute("""
                    INSERT INTO agent_tasks (workspace_id, agent_type, task_type, status, input_data, started_at)
                    VALUES (?,?,?,?,?,?) RETURNING id
                """, (self.wid, self.agent_type, task_type, 'running',
                      json.dumps(input_data)[:2000], datetime.now())).fetchone()
                conn.commit()
                conn.close()
                return row[0] if row else 0
            else:
                conn.execute("""
                    INSERT INTO agent_tasks (workspace_id, agent_type, task_type, status, input_data, started_at)
                    VALUES (?,?,?,?,?,?)
                """, (self.wid, self.agent_type, task_type, 'running',
                      json.dumps(input_data)[:2000], datetime.now()))
                conn.commit()
                tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.close()
                return tid
        except Exception:
            return 0

    def _log_task_complete(self, task_id: int, result: dict, elapsed_ms: int):
        if not task_id:
            return
        try:
            conn = get_db()
            conn.execute("""
                UPDATE agent_tasks SET status='completed', output_data=?, completed_at=?
                WHERE id=?
            """, (json.dumps(result)[:2000], datetime.now(), task_id))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _log_task_failed(self, task_id: int, error: str, elapsed_ms: int):
        if not task_id:
            return
        try:
            conn = get_db()
            conn.execute("""
                UPDATE agent_tasks SET status='failed', output_data=?, completed_at=?
                WHERE id=?
            """, (json.dumps({'error': error})[:2000], datetime.now(), task_id))
            conn.commit()
            conn.close()
        except Exception:
            pass
