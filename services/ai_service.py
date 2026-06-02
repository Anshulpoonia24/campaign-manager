import sqlite3
from datetime import datetime
import requests as http_requests
from utils.db import get_setting, get_db, DB_PATH
from utils.logger import app_logger, error_logger

# Groq key rotation
groq_key_index = 0
groq_rate_limits = {}


def _email_setting(key):
    """Get email-specific AI setting, fallback to legacy keys."""
    val = get_setting(f'email_{key}')
    if val:
        return val
    # Fallback to old keys for backward compat
    legacy_map = {'groq_keys': 'groq_api_keys', 'gemini_key': 'gemini_api_key', 'ai_priority': 'ai_priority'}
    return get_setting(legacy_map.get(key, key))


def call_ollama(prompt):
    url = get_setting('ollama_url') or 'http://localhost:11434'
    model = get_setting('ollama_model') or 'phi3:mini'
    try:
        r = http_requests.post(f'{url}/api/generate', json={'model': model, 'prompt': prompt, 'stream': False}, timeout=180)
        if r.status_code == 200:
            return r.json().get('response', '').strip(), None
        app_logger.warning(f'Ollama returned {r.status_code}')
        return None, f'Ollama {r.status_code}'
    except Exception as e:
        error_logger.error(f'Ollama error: {str(e)}')
        return None, f'Ollama offline: {str(e)[:50]}'


def call_groq(prompt):
    global groq_key_index, groq_rate_limits
    keys_str = _email_setting('groq_keys') or ''
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    if not keys:
        return None, 'No Groq keys'

    model = get_setting('email_model_groq') or 'llama-3.3-70b-versatile'

    for i in range(len(keys)):
        key = keys[(groq_key_index + i) % len(keys)]
        try:
            r = http_requests.post('https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 1000},
                timeout=30)
            groq_rate_limits[key[-8:]] = {
                'limit_requests': r.headers.get('x-ratelimit-limit-requests', '?'),
                'remaining_requests': r.headers.get('x-ratelimit-remaining-requests', '?'),
                'limit_tokens': r.headers.get('x-ratelimit-limit-tokens', '?'),
                'remaining_tokens': r.headers.get('x-ratelimit-remaining-tokens', '?'),
                'reset_requests': r.headers.get('x-ratelimit-reset-requests', ''),
                'reset_tokens': r.headers.get('x-ratelimit-reset-tokens', ''),
                'last_checked': datetime.now().strftime('%H:%M:%S'),
            }
            if r.status_code == 200:
                groq_key_index = (groq_key_index + i + 1) % len(keys)
                return r.json()['choices'][0]['message']['content'].strip(), None
            elif r.status_code == 429:
                continue
            else:
                continue
        except:
            continue
    return None, 'All Groq keys exhausted'


def call_gemini(prompt):
    api_key = _email_setting('gemini_key')
    if not api_key:
        return None, 'No Gemini key'
    model = get_setting('email_model_gemini') or 'gemini-2.0-flash'
    try:
        r = http_requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}',
            json={'contents': [{'parts': [{'text': prompt}]}]}, timeout=30)
        if r.status_code == 200:
            return r.json()['candidates'][0]['content']['parts'][0]['text'].strip(), None
        app_logger.warning(f'Gemini returned {r.status_code}')
        return None, f'Gemini {r.status_code}'
    except Exception as e:
        error_logger.error(f'Gemini error: {str(e)}')
        return None, f'Gemini error: {str(e)[:50]}'


def generate_ai_email(name, company, prompt_template, context='', designation=''):
    prompt = prompt_template.replace('{name}', name or '').replace('{company}', company or '').replace('{designation}', designation or 'founder/executive')
    if context:
        prompt = f"""CONTEXT ABOUT {company} (USE THIS to personalize the email):
{context}

USE the above context to write a SPECIFIC opening line. Do NOT write generic emails.

""" + prompt

    priority = (_email_setting('ai_priority') or 'groq,gemini').split(',')

    for provider in priority:
        provider = provider.strip().lower()
        if provider == 'ollama':
            body, err = call_ollama(prompt)
        elif provider == 'groq':
            body, err = call_groq(prompt)
        elif provider == 'gemini':
            body, err = call_gemini(prompt)
        else:
            continue

        # Track usage
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.execute("INSERT INTO ai_usage (provider, purpose, success) VALUES (?,?,?)",
                (provider, 'email', 1 if body else 0))
            conn.commit()
            conn.close()
        except:
            pass

        if body:
            return body, None
        app_logger.info(f'  [{provider}] failed: {err}')

    return None, 'All AI providers failed'
