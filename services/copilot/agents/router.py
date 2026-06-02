"""
services/copilot/agents/router.py — Multi-Agent Router
========================================================
Routes tasks to the correct specialized agent.
Supports multi-agent queries (e.g., bounce issue → deliverability + campaign agents).
"""
from services.copilot.agents.base_agent import BaseAgent

# Agent registry — lazy-loaded
_AGENT_CLASSES = {
    'deliverability': 'services.copilot.agents.deliverability_agent.DeliverabilityAgent',
    'campaign': 'services.copilot.agents.campaign_agent.CampaignAgent',
    'inbox': 'services.copilot.agents.inbox_agent.InboxAgent',
    'research': 'services.copilot.agents.research_agent.ResearchAgent',
    'analytics': 'services.copilot.agents.analytics_agent.AnalyticsAgent',
}

# Intent → Agent mapping
INTENT_AGENT_MAP = {
    # Campaign
    'pause_campaign': ('campaign', 'analyze_campaign'),
    'resume_campaign': ('campaign', 'analyze_campaign'),
    'cancel_campaign': ('campaign', 'analyze_campaign'),
    'diagnose_campaign': ('campaign', 'diagnose_failures'),
    'campaign_stats': ('campaign', 'analyze_campaign'),

    # Deliverability
    'smtp_diagnose': ('deliverability', 'diagnose_smtp'),
    'smtp_test': ('deliverability', 'health_check'),

    # Inbox
    'draft_reply': ('inbox', 'draft_reply'),
    'summarize_thread': ('inbox', 'summarize_threads'),
    'mark_status': ('inbox', 'prioritize_inbox'),

    # Research
    'enrich_contact': ('research', 'enrich_lead'),
    'bulk_enrich': ('research', 'enrich_lead'),

    # Analytics
    'report': ('analytics', 'generate_report'),
    'best_send_time': ('analytics', 'best_send_time'),
    'compare': ('campaign', 'compare_campaigns'),
}

# Multi-agent scenarios (intent triggers multiple agents)
MULTI_AGENT_MAP = {
    'diagnose_campaign': [
        ('campaign', 'diagnose_failures'),
        ('deliverability', 'health_check'),
    ],
    'smtp_diagnose': [
        ('deliverability', 'diagnose_smtp'),
        ('campaign', 'analyze_campaign'),
    ],
}


def get_agent(agent_type: str, workspace_id: int) -> BaseAgent:
    """Instantiate an agent by type."""
    class_path = _AGENT_CLASSES.get(agent_type)
    if not class_path:
        return None

    module_path, class_name = class_path.rsplit('.', 1)
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls(workspace_id)


def route_to_agent(intent: str, workspace_id: int, input_data: dict) -> dict:
    """Route an intent to the appropriate agent(s) and return combined result."""

    # Check multi-agent scenario first
    if intent in MULTI_AGENT_MAP:
        results = {}
        for agent_type, task_type in MULTI_AGENT_MAP[intent]:
            agent = get_agent(agent_type, workspace_id)
            if agent:
                result = agent.run(task_type, input_data)
                results[agent_type] = result
        return {'multi_agent': True, 'results': results}

    # Single agent
    mapping = INTENT_AGENT_MAP.get(intent)
    if not mapping:
        return {'routed': False, 'reason': f'No agent for intent: {intent}'}

    agent_type, task_type = mapping
    agent = get_agent(agent_type, workspace_id)
    if not agent:
        return {'routed': False, 'reason': f'Agent {agent_type} not found'}

    result = agent.run(task_type, input_data)
    return {'routed': True, 'agent': agent_type, 'task': task_type, **result}


def get_workspace_health(workspace_id: int) -> dict:
    """Get health snapshot from all agents (for dashboard copilot)."""
    health = {}
    for agent_type in ('deliverability', 'campaign', 'inbox', 'research', 'analytics'):
        agent = get_agent(agent_type, workspace_id)
        if agent:
            try:
                health[agent_type] = agent.analyze()
            except Exception as e:
                health[agent_type] = {'error': str(e)[:100]}
    return health


def list_agents() -> list:
    """List all available agents with their capabilities."""
    agents = []
    for agent_type, class_path in _AGENT_CLASSES.items():
        module_path, class_name = class_path.rsplit('.', 1)
        import importlib
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            agents.append({
                'type': agent_type,
                'description': cls.description,
                'capabilities': cls.capabilities,
            })
        except Exception:
            agents.append({'type': agent_type, 'description': 'Error loading', 'capabilities': []})
    return agents
