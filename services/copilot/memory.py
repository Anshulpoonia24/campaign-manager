"""
services/copilot/memory.py — Conversation Memory + User Preferences
====================================================================
Maintains multi-turn conversation context (in-memory + DB persistence)
and tracks user preferences to personalize copilot responses.
"""
import json
from datetime import datetime
from collections import defaultdict
from utils.db import get_db


# ── IN-MEMORY CONVERSATION BUFFER ─────────────────────────────
# {session_key: [messages]} — last N turns per session
_conversations = defaultdict(list)
_MAX_TURNS = 10


def _session_key(workspace_id: int, user_id: int) -> str:
    return f"{workspace_id}:{user_id}"


def add_turn(workspace_id: int, user_id: int, role: str, content: str):
    """Add a conversation turn to memory."""
    key = _session_key(workspace_id, user_id)
    _conversations[key].append({
        'role': role,
        'content': content[:500],
        'ts': datetime.now().isoformat(),
    })
    # Keep only last N turns
    if len(_conversations[key]) > _MAX_TURNS:
        _conversations[key] = _conversations[key][-_MAX_TURNS:]


def get_history(workspace_id: int, user_id: int, last_n: int = 5) -> list:
    """Get recent conversation turns for context injection."""
    key = _session_key(workspace_id, user_id)
    return _conversations[key][-last_n:]


def clear_history(workspace_id: int, user_id: int):
    """Clear conversation history."""
    key = _session_key(workspace_id, user_id)
    _conversations[key] = []


def get_history_prompt(workspace_id: int, user_id: int) -> str:
    """Format conversation history for system prompt injection."""
    history = get_history(workspace_id, user_id, last_n=4)
    if not history:
        return ''
    lines = []
    for turn in history:
        prefix = 'USER' if turn['role'] == 'user' else 'ASSISTANT'
        lines.append(f"{prefix}: {turn['content'][:200]}")
    return "CONVERSATION HISTORY:\n" + "\n".join(lines)


# ── USER PREFERENCES (DB-backed) ─────────────────────────────

def get_preference(workspace_id: int, user_id: int, key: str, default=''):
    """Get a stored copilot preference."""
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT value FROM copilot_memory
            WHERE workspace_id=? AND user_id=? AND key=?
        """, (workspace_id, user_id, key)).fetchone()
        return row[0] if row else default
    except Exception:
        return default
    finally:
        conn.close()


def set_preference(workspace_id: int, user_id: int, key: str, value: str):
    """Store a copilot preference."""
    conn = get_db()
    try:
        existing = conn.execute("""
            SELECT id FROM copilot_memory
            WHERE workspace_id=? AND user_id=? AND key=?
        """, (workspace_id, user_id, key)).fetchone()
        if existing:
            conn.execute("""
                UPDATE copilot_memory SET value=?, updated_at=?
                WHERE workspace_id=? AND user_id=? AND key=?
            """, (value, datetime.now(), workspace_id, user_id, key))
        else:
            conn.execute("""
                INSERT INTO copilot_memory (workspace_id, user_id, key, value, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
            """, (workspace_id, user_id, key, value, datetime.now(), datetime.now()))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def get_user_context(workspace_id: int, user_id: int) -> str:
    """Build user preference context string for prompt injection."""
    prefs = {}
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT key, value FROM copilot_memory
            WHERE workspace_id=? AND user_id=?
        """, (workspace_id, user_id)).fetchall()
        for r in rows:
            prefs[r[0]] = r[1]
    except Exception:
        pass
    finally:
        conn.close()

    if not prefs:
        return ''
    parts = [f"- {k}: {v}" for k, v in prefs.items() if v]
    return "USER PREFERENCES:\n" + "\n".join(parts[:5]) if parts else ''
