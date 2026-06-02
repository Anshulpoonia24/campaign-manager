"""
services/copilot/action_registry.py — Action Definition & Registry
===================================================================
All copilot-executable actions defined here with schemas, risk levels,
permission requirements, and handler references.
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# ── RISK LEVELS ───────────────────────────────────────────────
RISK_SAFE = 'safe'          # Read-only, no confirmation
RISK_LOW = 'low'            # Minor side effect (costs AI credits)
RISK_MEDIUM = 'medium'      # Reversible state change
RISK_HIGH = 'high'          # Sends emails or modifies data
RISK_CRITICAL = 'critical'  # Bulk send, delete, irreversible


@dataclass
class ActionDef:
    name: str
    description: str
    category: str
    risk_level: str
    requires_confirmation: bool
    params_schema: dict
    handler_path: str           # 'module.function' — lazy loaded
    page_types: List[str] = field(default_factory=list)
    rollback_path: Optional[str] = None


# ── GLOBAL REGISTRY ───────────────────────────────────────────
ACTION_REGISTRY: Dict[str, ActionDef] = {}


def register(name: str, **kwargs):
    """Register an action definition."""
    ACTION_REGISTRY[name] = ActionDef(name=name, **kwargs)


def get_action(name: str) -> Optional[ActionDef]:
    return ACTION_REGISTRY.get(name)


def get_actions_for_page(page_type: str) -> List[ActionDef]:
    """Return actions relevant to a page type."""
    return [a for a in ACTION_REGISTRY.values()
            if not a.page_types or page_type in a.page_types]


def get_tools_json(page_type: str) -> list:
    """Return action schemas formatted for AI function calling."""
    actions = get_actions_for_page(page_type)
    tools = []
    for a in actions:
        tools.append({
            'name': a.name,
            'description': a.description,
            'parameters': a.params_schema,
            'risk': a.risk_level,
        })
    return tools


# ══════════════════════════════════════════════════════════════
# ACTION DEFINITIONS
# ══════════════════════════════════════════════════════════════

# ── CAMPAIGN ACTIONS ──────────────────────────────────────────
register('pause_campaign',
    description='Pause a running campaign to stop further sends',
    category='campaign', risk_level=RISK_MEDIUM,
    requires_confirmation=True,
    params_schema={'campaign_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.campaign.pause_campaign',
    page_types=['campaign_status', 'dashboard', 'campaigns'])

register('resume_campaign',
    description='Resume a paused campaign',
    category='campaign', risk_level=RISK_MEDIUM,
    requires_confirmation=True,
    params_schema={'campaign_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.campaign.resume_campaign',
    page_types=['campaign_status', 'dashboard', 'campaigns'])

register('cancel_campaign',
    description='Cancel a campaign permanently — cannot be undone',
    category='campaign', risk_level=RISK_HIGH,
    requires_confirmation=True,
    params_schema={'campaign_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.campaign.cancel_campaign',
    page_types=['campaign_status'])

register('retry_failed_emails',
    description='Retry all failed/bounced emails in a campaign',
    category='campaign', risk_level=RISK_HIGH,
    requires_confirmation=True,
    params_schema={'campaign_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.campaign.retry_failed',
    page_types=['campaign_status', 'campaign_detail', 'deliverability'])

register('diagnose_campaign',
    description='Analyze why a campaign has failures and provide recommendations',
    category='campaign', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={'campaign_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.campaign.diagnose',
    page_types=['campaign_status', 'campaign_detail'])

# ── INBOX ACTIONS ─────────────────────────────────────────────
register('draft_reply',
    description='Generate an AI reply draft for a conversation thread',
    category='inbox', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={'thread_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.inbox.draft_reply',
    page_types=['inbox_thread'])

register('send_reply',
    description='Send a reply email in a thread',
    category='inbox', risk_level=RISK_HIGH,
    requires_confirmation=True,
    params_schema={
        'thread_id': {'type': 'integer', 'required': True},
        'body': {'type': 'string', 'required': True},
    },
    handler_path='services.copilot.handlers.inbox.send_reply',
    page_types=['inbox_thread'])

register('mark_thread_status',
    description='Change thread status (interested, meeting, closed, etc.)',
    category='inbox', risk_level=RISK_LOW,
    requires_confirmation=False,
    params_schema={
        'thread_id': {'type': 'integer', 'required': True},
        'status': {'type': 'string', 'required': True,
                   'enum': ['active', 'interested', 'meeting', 'closed', 'booked', 'ignored']},
    },
    handler_path='services.copilot.handlers.inbox.mark_status',
    page_types=['inbox_thread', 'inbox'])

register('summarize_thread',
    description='Generate a concise summary of a conversation thread',
    category='inbox', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={'thread_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.inbox.summarize_thread',
    page_types=['inbox_thread'])

# ── CONTACT ACTIONS ───────────────────────────────────────────
register('enrich_contact',
    description='Fetch company intelligence for a contact using AI',
    category='contacts', risk_level=RISK_LOW,
    requires_confirmation=False,
    params_schema={'contact_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.contacts.enrich_contact',
    page_types=['contacts', 'campaign_detail', 'inbox_thread'])

register('bulk_enrich',
    description='Enrich all unenriched contacts in workspace',
    category='contacts', risk_level=RISK_LOW,
    requires_confirmation=True,
    params_schema={},
    handler_path='services.copilot.handlers.contacts.bulk_enrich',
    page_types=['contacts', 'dashboard'])

register('fetch_context',
    description='AI-research a company and store context on the contact',
    category='contacts', risk_level=RISK_LOW,
    requires_confirmation=False,
    params_schema={'contact_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.contacts.fetch_context',
    page_types=['contacts', 'campaign_detail'])

# ── SEQUENCE ACTIONS ──────────────────────────────────────────
register('create_sequence',
    description='Generate a multi-step email sequence for a campaign',
    category='sequence', risk_level=RISK_MEDIUM,
    requires_confirmation=True,
    params_schema={
        'campaign_id': {'type': 'integer', 'required': True},
        'steps': {'type': 'integer', 'default': 3},
        'audience': {'type': 'string', 'default': ''},
    },
    handler_path='services.copilot.handlers.sequence.create_sequence',
    page_types=['sequence_builder', 'campaign_detail'])

register('generate_step_content',
    description='Generate AI content for a sequence step',
    category='sequence', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={
        'campaign_id': {'type': 'integer', 'required': True},
        'step_order': {'type': 'integer', 'required': True},
        'step_type': {'type': 'string', 'default': 'follow_up'},
    },
    handler_path='services.copilot.handlers.sequence.generate_step',
    page_types=['sequence_builder'])

# ── SMTP/DELIVERABILITY ACTIONS ───────────────────────────────
register('toggle_smtp_account',
    description='Enable or disable an SMTP account',
    category='smtp', risk_level=RISK_MEDIUM,
    requires_confirmation=True,
    params_schema={'account_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.smtp.toggle_account',
    page_types=['deliverability', 'settings'])

register('diagnose_deliverability',
    description='Analyze bounce patterns and SMTP health for the workspace',
    category='smtp', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={},
    handler_path='services.copilot.handlers.smtp.diagnose',
    page_types=['deliverability', 'dashboard'])

register('test_smtp_connection',
    description='Test SMTP connection for an account',
    category='smtp', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={'account_id': {'type': 'integer', 'required': True}},
    handler_path='services.copilot.handlers.smtp.test_connection',
    page_types=['deliverability', 'settings'])

# ── ANALYTICS ACTIONS ─────────────────────────────────────────
register('generate_report',
    description='Generate a performance summary for a time period',
    category='analytics', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={'days': {'type': 'integer', 'default': 7}},
    handler_path='services.copilot.handlers.analytics.generate_report',
    page_types=['analytics', 'dashboard'])

register('compare_campaigns',
    description='Compare performance of two campaigns',
    category='analytics', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={
        'campaign_a': {'type': 'integer', 'required': True},
        'campaign_b': {'type': 'integer', 'required': True},
    },
    handler_path='services.copilot.handlers.analytics.compare',
    page_types=['analytics', 'campaigns'])

register('predict_best_send_time',
    description='Analyze historical data to suggest optimal send times',
    category='analytics', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={},
    handler_path='services.copilot.handlers.analytics.best_send_time',
    page_types=['analytics', 'dashboard', 'campaign_detail'])

# ── NAVIGATION ACTIONS ────────────────────────────────────────
register('navigate',
    description='Navigate user to a specific page',
    category='navigation', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={'url': {'type': 'string', 'required': True}},
    handler_path='services.copilot.handlers.navigation.navigate',
    page_types=[])  # Available everywhere

register('show_info',
    description='Display additional information to the user',
    category='navigation', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={'info': {'type': 'string', 'required': True}},
    handler_path='services.copilot.handlers.navigation.show_info',
    page_types=[])

# ── BATCH OPERATIONS (Phase 6) ────────────────────────────────
register('batch_pause_campaigns',
    description='Pause all running campaigns at once',
    category='campaign', risk_level=RISK_HIGH,
    requires_confirmation=True,
    params_schema={'campaign_ids': {'type': 'array', 'required': False}},
    handler_path='services.copilot.function_caller.batch_pause_campaigns',
    page_types=['dashboard', 'campaigns'])

register('batch_test_smtp',
    description='Test connectivity of all active SMTP accounts',
    category='smtp', risk_level=RISK_SAFE,
    requires_confirmation=False,
    params_schema={},
    handler_path='services.copilot.function_caller.batch_test_smtp',
    page_types=['deliverability', 'dashboard', 'settings'])

register('batch_enrich',
    description='Enrich all unenriched contacts in bulk (up to 50)',
    category='contacts', risk_level=RISK_LOW,
    requires_confirmation=True,
    params_schema={'limit': {'type': 'integer', 'default': 50}},
    handler_path='services.copilot.function_caller.batch_enrich_contacts',
    page_types=['contacts', 'dashboard'])

register('batch_mark_threads',
    description='Mark multiple inbox threads with a status',
    category='inbox', risk_level=RISK_MEDIUM,
    requires_confirmation=True,
    params_schema={
        'thread_ids': {'type': 'array', 'required': True},
        'status': {'type': 'string', 'required': True},
    },
    handler_path='services.copilot.function_caller.batch_mark_threads',
    page_types=['inbox'])
