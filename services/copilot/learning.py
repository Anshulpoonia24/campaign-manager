"""
services/copilot/learning.py — Learning & Personalization Engine (Phase 7)
===========================================================================
Tracks user behavior, learns preferences, adapts copilot personality.
"""
import re
import json
from datetime import datetime, timedelta
from collections import Counter
from utils.db import get_db

try:
    from utils.logger import app_logger
except Exception:
    import logging
    app_logger = logging.getLogger('campaign')


# ── IN-MEMORY BEHAVIOR TRACKER ────────────────────────────────
# {workspace_id:user_id: {actions: Counter, pages: Counter, feedback: [], msg_lengths: []}}
_user_profiles = {}


def _get_profile(wid: int, uid: int) -> dict:
    key = f"{wid}:{uid}"
    if key not in _user_profiles:
        _user_profiles[key] = {
            'actions': Counter(),
            'pages': Counter(),
            'feedback': [],
            'msg_lengths': [],
            'language': 'en',
            'tone': 'casual',
            'verbosity': 'concise',
            'last_active': None,
            'session_count': 0,
        }
    return _user_profiles[key]


# ── BEHAVIOR TRACKING ─────────────────────────────────────────

def track_message(wid: int, uid: int, message: str, page_type: str):
    """Track user message patterns."""
    profile = _get_profile(wid, uid)
    profile['pages'][page_type] += 1
    profile['msg_lengths'].append(len(message))
    # Keep last 50
    if len(profile['msg_lengths']) > 50:
        profile['msg_lengths'] = profile['msg_lengths'][-50:]
    profile['last_active'] = datetime.now()
    # Detect language
    profile['language'] = detect_language(message)
    # Detect tone
    profile['tone'] = detect_tone(message)


def track_action(wid: int, uid: int, action_type: str):
    """Track which actions user executes most."""
    profile = _get_profile(wid, uid)
    profile['actions'][action_type] += 1


def track_feedback(wid: int, uid: int, message_id: str, rating: str, response_text: str = ''):
    """Store thumbs up/down feedback on AI responses."""
    profile = _get_profile(wid, uid)
    profile['feedback'].append({
        'message_id': message_id,
        'rating': rating,  # 'up' or 'down'
        'response_length': len(response_text),
        'timestamp': datetime.now().isoformat(),
    })
    # Keep last 100
    if len(profile['feedback']) > 100:
        profile['feedback'] = profile['feedback'][-100:]
    # Also persist to DB
    _save_feedback_db(wid, uid, message_id, rating)
    # Adapt verbosity from feedback
    _adapt_from_feedback(profile)


def _save_feedback_db(wid: int, uid: int, message_id: str, rating: str):
    try:
        from services.copilot.memory import set_preference
        # Count recent ratings
        profile = _get_profile(wid, uid)
        recent = profile['feedback'][-20:]
        ups = sum(1 for f in recent if f['rating'] == 'up')
        downs = sum(1 for f in recent if f['rating'] == 'down')
        set_preference(wid, uid, 'feedback_score', f"{ups}/{ups+downs}")
    except Exception:
        pass


def _adapt_from_feedback(profile: dict):
    """Adapt response style based on feedback patterns."""
    recent = profile['feedback'][-20:]
    if len(recent) < 5:
        return
    # Check if user thumbs-down long responses
    down_lengths = [f['response_length'] for f in recent if f['rating'] == 'down']
    up_lengths = [f['response_length'] for f in recent if f['rating'] == 'up']
    if down_lengths and up_lengths:
        avg_down = sum(down_lengths) / len(down_lengths)
        avg_up = sum(up_lengths) / len(up_lengths)
        if avg_down > avg_up * 1.5:
            profile['verbosity'] = 'concise'
        elif avg_up > avg_down * 1.5:
            profile['verbosity'] = 'detailed'


# ── LANGUAGE & TONE DETECTION ─────────────────────────────────

HINDI_PATTERNS = re.compile(r'(karo|bhai|kya|hai|nahi|haan|kaise|batao|dikhao|kab|kyu|acha|theek|sahi|chal|hta|bna|dekh|bol)', re.I)
FORMAL_PATTERNS = re.compile(r'(please|could you|would you|kindly|i would like|appreciate|thank you)', re.I)
CASUAL_PATTERNS = re.compile(r'(yo|hey|sup|bro|dude|lol|nah|yep|cool|k\b|gonna|wanna)', re.I)


def detect_language(message: str) -> str:
    """Detect if user speaks Hindi/Hinglish or English."""
    hindi_matches = len(HINDI_PATTERNS.findall(message))
    if hindi_matches >= 2:
        return 'hinglish'
    if hindi_matches >= 1 and len(message.split()) <= 6:
        return 'hinglish'
    return 'en'


def detect_tone(message: str) -> str:
    """Detect formal vs casual tone."""
    if FORMAL_PATTERNS.search(message):
        return 'formal'
    if CASUAL_PATTERNS.search(message) or HINDI_PATTERNS.search(message):
        return 'casual'
    return 'neutral'


# ── PERSONALIZATION PROMPT INJECTION ──────────────────────────

def get_personalization_prompt(wid: int, uid: int) -> str:
    """Generate personalization context to inject into system prompt."""
    profile = _get_profile(wid, uid)
    parts = []

    # Language preference
    if profile['language'] == 'hinglish':
        parts.append("USER LANGUAGE: Hinglish (Hindi+English mix). Respond in same style — mix Hindi and English naturally.")
    
    # Tone
    if profile['tone'] == 'casual':
        parts.append("USER TONE: Casual. Keep responses informal, direct, no corporate fluff.")
    elif profile['tone'] == 'formal':
        parts.append("USER TONE: Formal. Be professional and polite.")

    # Verbosity
    if profile['verbosity'] == 'concise':
        parts.append("VERBOSITY: User prefers SHORT responses (2-3 sentences max). No bullet points unless asked.")
    elif profile['verbosity'] == 'detailed':
        parts.append("VERBOSITY: User prefers DETAILED responses with breakdowns and explanations.")

    # Favorite actions
    top_actions = profile['actions'].most_common(3)
    if top_actions:
        action_str = ', '.join(f"{a[0]}({a[1]}x)" for a in top_actions)
        parts.append(f"FREQUENT ACTIONS: {action_str}")

    # Favorite pages
    top_pages = profile['pages'].most_common(3)
    if top_pages:
        page_str = ', '.join(f"{p[0]}" for p in top_pages)
        parts.append(f"FREQUENT PAGES: {page_str}")

    # Message length preference
    if profile['msg_lengths']:
        avg_len = sum(profile['msg_lengths']) / len(profile['msg_lengths'])
        if avg_len < 20:
            parts.append("USER STYLE: Very short messages — match with brief responses.")

    if not parts:
        return ''
    return "\nPERSONALIZATION:\n" + "\n".join(f"- {p}" for p in parts)


# ── PERSONALIZED SUGGESTIONS ──────────────────────────────────

def get_personalized_suggestions(wid: int, uid: int, page_type: str) -> list:
    """Return suggestions based on user's behavior patterns."""
    profile = _get_profile(wid, uid)
    suggestions = []

    # Suggest most-used actions
    top_actions = profile['actions'].most_common(5)
    for action, count in top_actions:
        if count >= 3:
            suggestions.append({
                'text': _action_to_suggestion(action),
                'reason': f'You use this often ({count}x)',
                'action_type': action,
            })

    # Time-based suggestions
    now = datetime.now()
    if now.hour >= 9 and now.hour <= 10:
        suggestions.append({'text': 'Morning report', 'reason': 'Start of day check', 'action_type': 'generate_report'})
    elif now.hour >= 17:
        suggestions.append({'text': 'End-of-day summary', 'reason': 'Wrap up the day', 'action_type': 'generate_report'})

    # If user hasn't checked SMTP in a while
    if profile['pages'].get('deliverability', 0) == 0 and profile['session_count'] > 3:
        suggestions.append({'text': 'Check SMTP health', 'reason': "You haven't checked in a while", 'action_type': 'diagnose_deliverability'})

    return suggestions[:4]


def _action_to_suggestion(action_type: str) -> str:
    mapping = {
        'generate_report': '📊 Generate report',
        'diagnose_deliverability': '🛡️ SMTP health check',
        'diagnose_campaign': '📣 Campaign diagnosis',
        'draft_reply': '✍️ Draft a reply',
        'predict_best_send_time': '⏰ Best send time',
        'bulk_enrich': '🔍 Enrich contacts',
        'batch_test_smtp': '🔧 Test all SMTP',
    }
    return mapping.get(action_type, action_type.replace('_', ' ').title())


# ── SMART DEFAULTS ────────────────────────────────────────────

def get_smart_defaults(wid: int, uid: int) -> dict:
    """Return learned defaults for this user."""
    profile = _get_profile(wid, uid)
    defaults = {}

    # Preferred report period
    try:
        from services.copilot.memory import get_preference
        report_days = get_preference(wid, uid, 'report_days')
        if report_days:
            defaults['report_days'] = int(report_days)
    except Exception:
        pass

    # Preferred verbosity
    defaults['verbosity'] = profile['verbosity']
    defaults['language'] = profile['language']
    defaults['tone'] = profile['tone']

    return defaults


# ── USER PROFILE API ──────────────────────────────────────────

def get_user_learning_profile(wid: int, uid: int) -> dict:
    """Get full learning profile for API/debug."""
    profile = _get_profile(wid, uid)
    return {
        'language': profile['language'],
        'tone': profile['tone'],
        'verbosity': profile['verbosity'],
        'top_actions': profile['actions'].most_common(5),
        'top_pages': profile['pages'].most_common(5),
        'feedback_count': len(profile['feedback']),
        'avg_msg_length': round(sum(profile['msg_lengths']) / max(1, len(profile['msg_lengths'])), 1),
        'session_count': profile['session_count'],
        'last_active': profile['last_active'].isoformat() if profile['last_active'] else None,
    }


def reset_learning(wid: int, uid: int):
    """Reset learned preferences."""
    key = f"{wid}:{uid}"
    if key in _user_profiles:
        del _user_profiles[key]
