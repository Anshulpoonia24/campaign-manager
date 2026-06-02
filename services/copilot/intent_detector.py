"""
services/copilot/intent_detector.py — NLU Intent Classification
================================================================
Fast local intent detection (no AI call needed for common patterns).
Falls back to AI classification for ambiguous messages.
"""
import re

# ── INTENT CATALOG ────────────────────────────────────────────

INTENTS = {
    # Campaign intents
    'pause_campaign':    {'patterns': [r'\bpause\b.*campaign', r'\bstop\b.*campaign', r'\bhalt\b.*send'], 'category': 'campaign'},
    'resume_campaign':   {'patterns': [r'\bresume\b.*campaign', r'\brestart\b.*campaign', r'\bcontinue\b.*send'], 'category': 'campaign'},
    'cancel_campaign':   {'patterns': [r'\bcancel\b.*campaign', r'\babort\b.*campaign', r'\bkill\b.*campaign'], 'category': 'campaign'},
    'retry_failed':      {'patterns': [r'\bretry\b.*fail', r'\bresend\b.*bounce', r'\bretry\b.*bounce'], 'category': 'campaign'},
    'diagnose_campaign': {'patterns': [r'\bdiagnose\b', r'\bwhat.?s wrong\b', r'\bwhy.*fail', r'\bwhy.*bounce'], 'category': 'campaign'},
    'campaign_stats':    {'patterns': [r'\bhow.*campaign\b.*doing', r'\bcampaign\b.*stats', r'\bcampaign\b.*performance'], 'category': 'campaign'},

    # Inbox intents
    'draft_reply':       {'patterns': [r'\bdraft\b.*repl', r'\bwrite\b.*repl', r'\bgenerate\b.*repl', r'\breply\b.*draft'], 'category': 'inbox'},
    'send_reply':        {'patterns': [r'\bsend\b.*repl', r'\bsend\b.*this'], 'category': 'inbox'},
    'summarize_thread':  {'patterns': [r'\bsummar', r'\btl.?dr\b', r'\bwhat.*said'], 'category': 'inbox'},
    'mark_status':       {'patterns': [r'\bmark\b.*interest', r'\bmark\b.*meeting', r'\bmark\b.*close'], 'category': 'inbox'},

    # Contact intents
    'enrich_contact':    {'patterns': [r'\benrich\b', r'\bresearch\b.*contact', r'\bfind.*info'], 'category': 'contacts'},
    'bulk_enrich':       {'patterns': [r'\benrich\b.*all', r'\bbulk\b.*enrich'], 'category': 'contacts'},

    # SMTP intents
    'smtp_diagnose':     {'patterns': [r'\bsmtp\b.*issue', r'\bsmtp\b.*problem', r'\bdeliverability\b.*issue', r'\bemail.*not.*deliver'], 'category': 'smtp'},
    'smtp_test':         {'patterns': [r'\btest\b.*smtp', r'\btest\b.*connection', r'\bcheck\b.*smtp'], 'category': 'smtp'},

    # Analytics intents
    'report':            {'patterns': [r'\breport\b', r'\banalytics\b', r'\bhow.*doing\b', r'\bperformance\b'], 'category': 'analytics'},
    'best_send_time':    {'patterns': [r'\bbest.*time\b.*send', r'\bwhen.*send', r'\boptimal.*time'], 'category': 'analytics'},
    'compare':           {'patterns': [r'\bcompare\b.*campaign', r'\ba.?b\b.*test', r'\bwhich.*better'], 'category': 'analytics'},

    # Sequence intents
    'create_sequence':   {'patterns': [r'\bcreate\b.*sequence', r'\bnew\b.*sequence', r'\bbuild\b.*sequence'], 'category': 'sequence'},
    'generate_step':     {'patterns': [r'\bgenerate\b.*step', r'\bnew.*follow.?up', r'\badd.*step'], 'category': 'sequence'},

    # Navigation
    'navigate':          {'patterns': [r'\bgo\b.*to\b', r'\bopen\b', r'\bshow\b.*me\b', r'\btake\b.*me'], 'category': 'navigation'},

    # General
    'greeting':          {'patterns': [r'^(hi|hello|hey|sup|yo)\b', r'\bhow.*are\b.*you'], 'category': 'general'},
    'help':              {'patterns': [r'\bhelp\b', r'\bwhat.*can\b.*you', r'\bwhat.*do\b.*you'], 'category': 'general'},
}


def detect_intent(message: str, page_type: str = '') -> dict:
    """
    Classify user message into intent.
    Returns: {intent, confidence, category, entities}
    """
    msg = message.lower().strip()

    # Quick exact matches
    for intent_name, config in INTENTS.items():
        for pattern in config['patterns']:
            if re.search(pattern, msg, re.IGNORECASE):
                entities = _extract_entities(msg, intent_name)
                return {
                    'intent': intent_name,
                    'confidence': 0.85,
                    'category': config['category'],
                    'entities': entities,
                }

    # Context-based inference from page_type
    if page_type == 'campaign_status' and any(w in msg for w in ['why', 'issue', 'problem', 'wrong']):
        return {'intent': 'diagnose_campaign', 'confidence': 0.7, 'category': 'campaign', 'entities': {}}
    if page_type == 'inbox_thread' and any(w in msg for w in ['reply', 'respond', 'write']):
        return {'intent': 'draft_reply', 'confidence': 0.7, 'category': 'inbox', 'entities': {}}

    # Fallback — let AI decide
    return {'intent': 'unknown', 'confidence': 0.0, 'category': 'general', 'entities': {}}


def _extract_entities(msg: str, intent: str) -> dict:
    """Extract relevant entities from message based on intent."""
    entities = {}

    # Extract numbers (campaign_id, contact_id, days)
    numbers = re.findall(r'\b(\d+)\b', msg)
    if numbers:
        if 'campaign' in intent:
            entities['campaign_id'] = int(numbers[0])
        elif 'contact' in intent:
            entities['contact_id'] = int(numbers[0])
        else:
            entities['number'] = int(numbers[0])

    # Extract status values
    for status in ('interested', 'meeting', 'closed', 'booked', 'ignored'):
        if status in msg:
            entities['status'] = status
            break

    # Extract days for reports
    days_match = re.search(r'(\d+)\s*days?', msg)
    if days_match:
        entities['days'] = int(days_match.group(1))

    return entities
