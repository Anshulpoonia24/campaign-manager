# AI SDR Operating System — Complete Architecture

## 1. HIGH-LEVEL ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND LAYER                            │
│  Copilot Panel │ Command Palette │ Inline Suggestions │ Toasts  │
└────────────────────────────┬────────────────────────────────────┘
                             │ WebSocket + REST
┌────────────────────────────▼────────────────────────────────────┐
│                     AI ORCHESTRATION LAYER                        │
│  Intent Router │ Context Builder │ Memory Manager │ Agent Pool   │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    ACTION EXECUTION ENGINE                        │
│  Function Registry │ Permission Gate │ Audit Logger │ Rollback   │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                      AGENT SERVICES                               │
│  SDR Agent │ Deliverability Agent │ Analytics Agent │ Inbox Agent│
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                     DATA & INFRA LAYER                            │
│  PostgreSQL │ Redis │ Celery │ SMTP Pool │ AI APIs (Groq/Gemini)│
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. CORE COPILOT CAPABILITIES

### 2.1 Real-Time SDR Assistant
- **Campaign Doctor**: Diagnose failing campaigns in real-time
- **Reply Coach**: Draft contextual replies based on thread history
- **Lead Prioritizer**: Surface hot leads needing immediate action
- **Deliverability Guardian**: Monitor SMTP health and warn proactively
- **Sequence Optimizer**: Suggest timing/content changes based on performance

### 2.2 Proactive Intelligence (Push Notifications)
- "Campaign X has 40% bounce rate — pause recommended"
- "3 hot leads replied in last hour — check inbox"
- "SMTP account xyz@domain.com health dropped to 30%"
- "Best send time for your audience: Tuesday 10am EST"
- "Contact ABC opened email 5x but didn't reply — nudge?"

### 2.3 Natural Language Actions
User says → System does:
- "Pause campaign 5" → Pauses campaign
- "Retry all failed emails in last campaign" → Queues retries
- "Draft a follow-up for Acme Corp" → Generates personalized follow-up
- "Show me contacts who opened but didn't reply" → Filters + displays
- "Create a 3-step sequence for SaaS founders" → Builds sequence

---

## 3. INTENT DETECTION SYSTEM

```python
# services/copilot/intent_detector.py

INTENT_CATEGORIES = {
    # QUERY intents (read-only, safe)
    'query_performance':    ['how am i doing', 'show stats', 'performance', 'metrics'],
    'query_leads':          ['hot leads', 'who should i follow up', 'priority contacts'],
    'query_health':         ['smtp health', 'deliverability', 'bounce rate'],
    'query_campaign':       ['campaign status', 'how is campaign', 'sending progress'],
    'query_contact':        ['tell me about', 'who is', 'contact info'],
    'query_inbox':          ['unread', 'new replies', 'inbox summary'],

    # ACTION intents (require confirmation for dangerous ones)
    'action_pause':         ['pause', 'stop sending', 'halt campaign'],
    'action_resume':        ['resume', 'restart', 'continue sending'],
    'action_retry':         ['retry', 'resend failed', 'try again'],
    'action_draft':         ['draft reply', 'write response', 'compose'],
    'action_sequence':      ['create sequence', 'build sequence', 'multi-step'],
    'action_enrich':        ['enrich', 'research company', 'get context'],
    'action_send':          ['send', 'launch', 'start campaign'],

    # ANALYSIS intents (AI-heavy, may take time)
    'analyze_campaign':     ['why failing', 'diagnose', 'what went wrong'],
    'analyze_timing':       ['best time', 'when to send', 'optimal timing'],
    'analyze_content':      ['improve subject', 'better email', 'optimize copy'],
    'analyze_audience':     ['segment', 'which contacts', 'target audience'],

    # AUTOMATION intents
    'auto_workflow':        ['automate', 'set up rule', 'trigger when'],
    'auto_schedule':        ['schedule', 'send later', 'queue for tomorrow'],
}

INTENT_RISK_LEVEL = {
    'query_*':           'safe',      # No confirmation needed
    'action_draft':      'safe',      # Just generates text
    'action_enrich':     'low',       # Costs AI credits
    'action_pause':      'medium',    # Reversible
    'action_resume':     'medium',    # Reversible
    'action_retry':      'high',      # Sends emails
    'action_send':       'critical',  # Sends emails to contacts
    'action_sequence':   'medium',    # Creates DB records
}
```

---

## 4. CONTEXT-AWARENESS SYSTEM

### 4.1 Context Layers (Priority Order)

```
Layer 1: PAGE CONTEXT    — What page is the user on? What entity is selected?
Layer 2: SESSION CONTEXT — Recent actions, previous copilot messages this session
Layer 3: WORKSPACE STATE — Campaign statuses, SMTP health, pending tasks
Layer 4: CONTACT CONTEXT — Selected contact's full profile, history, score
Layer 5: MEMORY CONTEXT  — Past interactions, user preferences, patterns
```

### 4.2 Context Builder

```python
# services/copilot/context_builder.py

class ContextBuilder:
    def build(self, page_type, page_id, workspace_id, user_id):
        ctx = {}
        ctx['page'] = self._page_context(page_type, page_id, workspace_id)
        ctx['workspace'] = self._workspace_snapshot(workspace_id)
        ctx['memory'] = self._user_memory(user_id, workspace_id)
        ctx['alerts'] = self._active_alerts(workspace_id)
        return ctx

    def _page_context(self, page_type, page_id, wid):
        """Deep context per page type."""
        # campaign_status → campaign details, logs, failures, SMTP health
        # inbox_thread → full conversation, contact profile, lead score
        # contacts → filter state, enrichment stats, segment info
        # dashboard → KPIs, trends, anomalies
        # sequence_builder → steps, enrollment stats, per-step rates
        # deliverability → SMTP accounts, bounce patterns, warmup status
        # analytics → date range, comparison data, funnel metrics

    def _workspace_snapshot(self, wid):
        """Lightweight workspace health summary."""
        return {
            'active_campaigns': ...,
            'smtp_health_avg': ...,
            'unread_inbox': ...,
            'hot_leads_count': ...,
            'bounce_rate_7d': ...,
            'daily_send_capacity_remaining': ...,
        }

    def _active_alerts(self, wid):
        """Proactive issues that need attention."""
        alerts = []
        # High bounce rate alert
        # SMTP account near limit alert
        # Stalled campaign alert
        # Hot lead waiting for reply alert
        return alerts
```

### 4.3 Page-Aware Behavior Matrix

| Page | AI Knows | Can Do | Proactive Suggestions |
|------|----------|--------|----------------------|
| Dashboard | All KPIs, trends, anomalies | Navigate, summarize, alert | "Bounce rate up 15% — check SMTP" |
| Campaign Detail | Contacts, send mode, subject | Launch, edit, select contacts | "23 contacts have no context — enrich first?" |
| Campaign Status | Live progress, failures, logs | Pause, resume, cancel, retry | "5 failures from same SMTP — switch account" |
| Inbox Thread | Full conversation, contact profile | Draft reply, change status, score | "This sounds interested — mark as hot?" |
| Contacts | List, filters, enrichment state | Enrich, filter, export, score | "142 contacts unenriched — bulk enrich?" |
| Sequence Builder | Steps, timing, enrollment | Add/edit/delete steps, enroll | "Step 2 has 0% reply rate — rewrite?" |
| Deliverability | SMTP accounts, bounce data | Toggle accounts, adjust limits | "Account X at 90% limit — reduce volume" |
| Analytics | All metrics, trends | Filter, export, compare | "Tuesday sends get 2x opens vs Friday" |
| Settings | SMTP config, IMAP, prompt | Test connections, validate | "IMAP not connected — replies won't track" |

---

## 5. AI MEMORY ARCHITECTURE

### 5.1 Memory Types

```
SHORT-TERM (Session):  Current conversation, last 5 interactions
MEDIUM-TERM (7 days):  Recent actions, campaign results, patterns
LONG-TERM (Forever):   User preferences, workspace insights, best practices learned
ENTITY MEMORY:         Per-contact interaction history, per-campaign learnings
```

### 5.2 Database Tables

```sql
-- Conversation memory (session-level)
CREATE TABLE copilot_conversations (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    page_type TEXT,
    page_id INTEGER,
    role TEXT NOT NULL,  -- 'user' | 'assistant' | 'system'
    content TEXT NOT NULL,
    intent TEXT,
    actions_taken TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_cc_session ON copilot_conversations(session_id);
CREATE INDEX idx_cc_workspace ON copilot_conversations(workspace_id, created_at DESC);

-- Long-term memory (workspace learnings)
CREATE TABLE copilot_memory (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL,
    memory_type TEXT NOT NULL,  -- 'preference', 'insight', 'pattern', 'rule'
    category TEXT NOT NULL,     -- 'send_timing', 'content_style', 'audience', 'smtp'
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    source TEXT,               -- 'user_explicit', 'ai_inferred', 'data_derived'
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(workspace_id, category, key)
);
CREATE INDEX idx_cm_workspace ON copilot_memory(workspace_id, category);

-- Action audit log
CREATE TABLE copilot_actions (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    session_id TEXT,
    action_type TEXT NOT NULL,
    action_params TEXT DEFAULT '{}',
    intent TEXT,
    risk_level TEXT,  -- 'safe', 'low', 'medium', 'high', 'critical'
    status TEXT DEFAULT 'pending',  -- 'pending', 'confirmed', 'executed', 'failed', 'rolled_back'
    result TEXT DEFAULT '{}',
    error_message TEXT,
    execution_time_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_ca_workspace ON copilot_actions(workspace_id, created_at DESC);
CREATE INDEX idx_ca_status ON copilot_actions(status);

-- AI agent tasks (autonomous)
CREATE TABLE agent_tasks (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL,
    agent_type TEXT NOT NULL,   -- 'sdr', 'deliverability', 'analytics', 'inbox'
    task_type TEXT NOT NULL,
    priority INTEGER DEFAULT 5,
    status TEXT DEFAULT 'queued',  -- 'queued', 'running', 'completed', 'failed', 'cancelled'
    input_data TEXT DEFAULT '{}',
    output_data TEXT DEFAULT '{}',
    parent_task_id INTEGER,
    max_retries INTEGER DEFAULT 3,
    retry_count INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_at_workspace ON agent_tasks(workspace_id, status);
CREATE INDEX idx_at_agent ON agent_tasks(agent_type, status);

-- Proactive alerts
CREATE TABLE copilot_alerts (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT DEFAULT 'info',  -- 'info', 'warning', 'critical'
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT DEFAULT '{}',
    suggested_action TEXT,
    dismissed INTEGER DEFAULT 0,
    dismissed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_alerts_workspace ON copilot_alerts(workspace_id, dismissed, created_at DESC);
```

---

## 6. FUNCTION CALLING / ACTION REGISTRY

### 6.1 Action Registry Architecture

```python
# services/copilot/action_registry.py

from dataclasses import dataclass
from typing import Callable, Dict, Any, List

@dataclass
class ActionDef:
    name: str
    description: str
    category: str          # 'campaign', 'inbox', 'contacts', 'smtp', 'sequence', 'analytics'
    risk_level: str        # 'safe', 'low', 'medium', 'high', 'critical'
    requires_confirmation: bool
    params_schema: dict    # JSON Schema for params
    handler: Callable
    rollback: Callable = None
    page_types: List[str] = None  # Pages where this action is relevant

ACTION_REGISTRY: Dict[str, ActionDef] = {}

def register_action(name, **kwargs):
    """Decorator to register copilot actions."""
    def decorator(fn):
        ACTION_REGISTRY[name] = ActionDef(name=name, handler=fn, **kwargs)
        return fn
    return decorator
```

### 6.2 Complete Action Catalog

```python
# ── CAMPAIGN ACTIONS ──────────────────────────────────────────
@register_action('pause_campaign',
    description='Pause a running campaign',
    category='campaign', risk_level='medium',
    requires_confirmation=True,
    params_schema={'campaign_id': 'int'},
    page_types=['campaign_status', 'dashboard'])

@register_action('resume_campaign', ...)
@register_action('cancel_campaign', ...)
@register_action('retry_failed_emails', ...)
@register_action('launch_campaign', ...)
@register_action('clone_campaign', ...)
@register_action('change_send_mode', ...)

# ── INBOX ACTIONS ─────────────────────────────────────────────
@register_action('draft_reply', ...)
@register_action('send_reply', ...)
@register_action('mark_thread_status', ...)
@register_action('snooze_thread', ...)
@register_action('assign_thread', ...)
@register_action('bulk_mark_read', ...)

# ── CONTACT ACTIONS ───────────────────────────────────────────
@register_action('enrich_contact', ...)
@register_action('bulk_enrich', ...)
@register_action('update_lead_score', ...)
@register_action('add_to_campaign', ...)
@register_action('remove_from_campaign', ...)
@register_action('export_segment', ...)
@register_action('fetch_context', ...)

# ── SEQUENCE ACTIONS ──────────────────────────────────────────
@register_action('create_sequence', ...)
@register_action('add_sequence_step', ...)
@register_action('edit_sequence_step', ...)
@register_action('enroll_contacts', ...)
@register_action('pause_contact_sequence', ...)
@register_action('generate_step_content', ...)

# ── SMTP/DELIVERABILITY ACTIONS ───────────────────────────────
@register_action('toggle_smtp_account', ...)
@register_action('adjust_daily_limit', ...)
@register_action('reset_daily_counts', ...)
@register_action('test_smtp_connection', ...)
@register_action('diagnose_bounces', ...)

# ── ANALYTICS ACTIONS ─────────────────────────────────────────
@register_action('generate_report', ...)
@register_action('compare_campaigns', ...)
@register_action('predict_best_send_time', ...)
@register_action('segment_audience', ...)

# ── AUTOMATION ACTIONS ────────────────────────────────────────
@register_action('create_automation_rule', ...)
@register_action('toggle_automation', ...)
@register_action('generate_workflow', ...)
```

---

## 7. ACTION EXECUTION ENGINE

```python
# services/copilot/executor.py

class ActionExecutor:
    def __init__(self, workspace_id, user_id):
        self.wid = workspace_id
        self.uid = user_id

    async def execute(self, action_name: str, params: dict, session_id: str) -> dict:
        action = ACTION_REGISTRY.get(action_name)
        if not action:
            return {'success': False, 'error': 'Unknown action'}

        # 1. Permission check
        if not self._check_permission(action):
            return {'success': False, 'error': 'Permission denied'}

        # 2. Param validation
        valid, error = self._validate_params(action, params)
        if not valid:
            return {'success': False, 'error': error}

        # 3. Rate limiting (per action type)
        if self._is_rate_limited(action_name):
            return {'success': False, 'error': 'Rate limited, try again shortly'}

        # 4. Log intent (before execution)
        audit_id = self._log_action_start(action, params, session_id)

        # 5. Execute
        start_time = time.time()
        try:
            result = action.handler(self.wid, self.uid, **params)
            elapsed = int((time.time() - start_time) * 1000)
            self._log_action_complete(audit_id, result, elapsed)
            return {'success': True, **result}
        except Exception as e:
            elapsed = int((time.time() - start_time) * 1000)
            self._log_action_failed(audit_id, str(e), elapsed)
            return {'success': False, 'error': str(e)[:200]}

    def _check_permission(self, action):
        """Workspace-level permission + role check."""
        # Admin can do everything
        # Regular user cannot: cancel_campaign, toggle_smtp, etc.
        pass

    def _is_rate_limited(self, action_name):
        """Per-action rate limits stored in Redis/memory."""
        LIMITS = {
            'send_reply': (5, 60),      # 5 per minute
            'launch_campaign': (2, 300), # 2 per 5 min
            'bulk_enrich': (1, 60),
        }
        pass
```

---

## 8. MULTI-AGENT ARCHITECTURE

### 8.1 Agent Types

```python
# services/copilot/agents/

class BaseAgent:
    """All agents inherit this."""
    name: str
    description: str
    capabilities: list
    schedule: str  # cron expression for autonomous runs

    def analyze(self, workspace_id) -> dict: ...
    def suggest(self, context) -> list: ...
    def execute(self, task) -> dict: ...
```

### 8.2 SDR Agent (`agents/sdr_agent.py`)

**Purpose**: Acts like a virtual SDR — finds opportunities, prioritizes follow-ups, optimizes outreach.

**Autonomous Tasks**:
- Every morning: Score all leads, surface top 5 to follow up
- After campaign completes: Analyze results, suggest improvements
- When hot lead detected: Alert immediately with suggested reply
- Weekly: Generate "outreach report" with recommendations

**Functions**:
```
- identify_hot_leads(workspace_id) → top leads needing action
- suggest_follow_up_timing(contact_id) → optimal next touch
- generate_personalized_sequence(contact_id) → multi-step plan
- recommend_audience_segment(campaign_id) → who to target next
- score_reply_urgency(thread_id) → how fast to respond
```

### 8.3 Deliverability Agent (`agents/deliverability_agent.py`)

**Purpose**: Monitors and optimizes email deliverability.

**Autonomous Tasks**:
- Every hour: Check SMTP health scores
- After each send batch: Analyze bounce patterns
- Daily: Calculate sending reputation trend
- When bounce rate spikes: Auto-pause problematic accounts

**Functions**:
```
- diagnose_bounces(campaign_id) → root cause analysis
- recommend_warmup_plan(smtp_id) → optimal warmup schedule
- detect_blacklist_risk(domain) → risk assessment
- optimize_send_volume(workspace_id) → daily limit recommendations
- analyze_bounce_patterns() → categorize hard/soft/gray bounces
```

### 8.4 Analytics Agent (`agents/analytics_agent.py`)

**Purpose**: Deep performance analysis and predictions.

**Functions**:
```
- predict_best_send_time(workspace_id) → hour/day recommendations
- compare_campaigns(ids) → A/B analysis
- forecast_reply_rate(campaign_id) → predicted outcome
- detect_anomalies(workspace_id) → unusual patterns
- generate_weekly_report(workspace_id) → full summary
```

### 8.5 Inbox Agent (`agents/inbox_agent.py`)

**Purpose**: Intelligent reply handling and conversation management.

**Functions**:
```
- categorize_reply(thread_id) → intent classification
- draft_contextual_reply(thread_id) → personalized response
- detect_buying_signals(thread_id) → opportunity indicators
- suggest_meeting_time(thread_id) → scheduling recommendation
- summarize_conversation(thread_id) → executive summary
```

---

## 9. API ARCHITECTURE

### 9.1 Copilot REST Endpoints

```python
# routes/copilot.py — Blueprint

# ── CHAT ──────────────────────────────────────────────────────
POST /api/copilot/chat              # Main chat endpoint
POST /api/copilot/action            # Execute confirmed action
GET  /api/copilot/suggestions       # Get proactive suggestions for current page
GET  /api/copilot/alerts            # Get active alerts

# ── MEMORY ────────────────────────────────────────────────────
GET  /api/copilot/memory            # Get workspace memory/preferences
POST /api/copilot/memory            # Store explicit preference
DELETE /api/copilot/memory/<id>     # Forget a memory

# ── AGENTS ────────────────────────────────────────────────────
GET  /api/copilot/agents/status     # All agent statuses
POST /api/copilot/agents/<type>/run # Trigger agent manually
GET  /api/copilot/agents/<type>/output # Get latest agent output

# ── CONVERSATIONS ─────────────────────────────────────────────
GET  /api/copilot/history           # Past conversations
GET  /api/copilot/history/<session> # Specific session

# ── ADMIN ─────────────────────────────────────────────────────
GET  /api/copilot/audit             # Action audit log
GET  /api/copilot/usage             # AI token usage stats
```

### 9.2 WebSocket Events (Future)

```
# Server → Client (push)
copilot:alert          — New proactive alert
copilot:suggestion     — Real-time suggestion based on user action
copilot:agent_update   — Agent completed a background task
copilot:typing         — Streaming AI response

# Client → Server
copilot:message        — User sends message
copilot:dismiss_alert  — User dismisses alert
copilot:feedback       — User rates response (thumbs up/down)
```

---

## 10. FRONTEND UX ARCHITECTURE

### 10.1 UI Components

```
1. FLOATING FAB (existing) — Entry point, shows unread alert count
2. CHAT PANEL (existing) — Conversational interface
3. COMMAND PALETTE (new) — Ctrl+K, search actions by name
4. INLINE SUGGESTIONS (new) — Contextual tips on each page
5. ALERT TOASTS (new) — Push notifications for critical events
6. AGENT DASHBOARD (new) — /copilot page showing all agent outputs
7. ACTION HISTORY (new) — Timeline of copilot actions taken
```

### 10.2 Command Palette (Ctrl+K)

```
┌──────────────────────────────────────────┐
│ 🔍 Type a command or ask AI...           │
├──────────────────────────────────────────┤
│ ⚡ Pause campaign "Enterprise Q3"        │
│ ✉️  Draft reply to Acme Corp thread      │
│ 📊 Show this week's performance          │
│ 🔄 Retry failed emails in Campaign 12    │
│ 🧠 Enrich all unenriched contacts        │
│ 📋 Create 3-step sequence for SaaS       │
├──────────────────────────────────────────┤
│ Recent: "Why is bounce rate high?"       │
│ Recent: "Pause campaign 5"              │
└──────────────────────────────────────────┘
```

### 10.3 Inline Page Suggestions

On Campaign Status page:
```
┌─ 🤖 Copilot Insight ─────────────────────┐
│ 4 emails failed due to SMTP auth error.  │
│ Account sales@domain.com may have wrong   │
│ password.                                 │
│ [Fix SMTP Settings] [Retry with other]   │
└───────────────────────────────────────────┘
```

---

## 11. AI ORCHESTRATION FLOW

```
User Message
    │
    ▼
┌─────────────────┐
│ Intent Detector  │ ← Classify: query / action / analysis / automation
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Context Builder  │ ← Gather: page + workspace + memory + alerts
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Prompt Composer  │ ← System prompt + context + user message + action tools
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ AI Provider      │ ← Groq (primary) → Gemini (fallback)
│ (Function Call)  │    Response format: {message, actions[], memory_updates[]}
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Response Parser  │ ← Validate JSON, sanitize, extract actions
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
[Display]  [Queue Actions]
    │         │
    ▼         ▼
[User]    [Executor]
           (if confirmed)
```

---

## 12. PROMPT ENGINEERING STRATEGY

### 12.1 System Prompt Structure

```
ROLE:       "You are the AI SDR Operating System..."
CONTEXT:    Injected page context + workspace state
TOOLS:      Available actions with schemas (function calling)
MEMORY:     Relevant past interactions and preferences
CONSTRAINTS: Safety rules, rate limits, what NOT to do
FORMAT:     JSON response schema
```

### 12.2 Dynamic System Prompt Template

```python
SYSTEM_PROMPT = """You are the OutreachOS AI SDR Copilot — an enterprise-grade sales development assistant.

IDENTITY:
- You are embedded in OutreachOS, a cold email campaign platform
- You help SDRs run campaigns, manage leads, and close deals
- You are proactive, concise, and action-oriented

CAPABILITIES:
- Execute backend actions (pause campaigns, retry emails, draft replies)
- Analyze campaign performance and diagnose issues
- Generate personalized email sequences
- Monitor deliverability and SMTP health
- Prioritize leads and suggest next best actions

CURRENT PAGE: {page_type}
PAGE CONTEXT: {context_json}
WORKSPACE STATE: {workspace_snapshot}
ACTIVE ALERTS: {alerts}
MEMORY: {relevant_memories}

AVAILABLE ACTIONS (call these when appropriate):
{action_tools_json}

RESPONSE FORMAT (strict JSON):
{{
  "message": "Your response to the user (markdown supported)",
  "actions": [
    {{"type": "action_name", "label": "Button Label", "params": {{...}}, "risk": "level"}}
  ],
  "memory_updates": [
    {{"key": "...", "value": "...", "category": "..."}}
  ],
  "follow_up_questions": ["Optional clarifying questions"]
}}

RULES:
- Be concise (2-5 sentences unless asked for detail)
- Always ground responses in DATA from the context
- Never hallucinate metrics — if you don't have data, say so
- For dangerous actions (send, delete, cancel), always explain consequences
- If multiple issues detected, prioritize by severity
- Use the user's timezone for time-based suggestions
- Remember: you're talking to a busy SDR — be efficient
"""
```

---

## 13. DELIVERABILITY AI

### 13.1 Capabilities

```python
class DeliverabilityAgent:
    """
    Monitors and optimizes email deliverability across all SMTP accounts.
    """

    def analyze_bounce_patterns(self, workspace_id, days=7):
        """
        Categorize bounces:
        - Hard bounce (invalid email) → remove from list
        - Soft bounce (mailbox full) → retry later
        - Auth failure → SMTP config issue
        - Rate limited → reduce volume
        - Blacklisted → switch domain/IP
        """

    def calculate_sender_reputation(self, smtp_account_id):
        """
        Score based on:
        - Bounce rate (< 2% = good)
        - Spam complaint rate
        - Open rate (proxy for inbox placement)
        - Warmup age
        - Daily volume vs limit ratio
        """

    def recommend_warmup_schedule(self, smtp_account_id):
        """
        Week 1: 5/day → Week 2: 15/day → Week 3: 30/day → Week 4: 50/day
        Adjust based on bounce rate at each stage.
        """

    def detect_sending_anomalies(self, workspace_id):
        """
        - Sudden bounce spike
        - Open rate drop (possible spam folder)
        - All emails to specific domain failing
        - SMTP timeout patterns
        """

    def suggest_domain_rotation(self, workspace_id):
        """
        If primary domain reputation drops:
        - Suggest secondary domains
        - Recommend volume distribution
        - Calculate safe daily volume per domain
        """
```

### 13.2 Proactive Alerts

```python
DELIVERABILITY_ALERTS = [
    {
        'condition': 'bounce_rate_7d > 5%',
        'severity': 'critical',
        'message': 'Bounce rate is {rate}% — risk of domain blacklisting',
        'action': 'pause_high_bounce_campaigns'
    },
    {
        'condition': 'smtp_health < 30',
        'severity': 'critical',
        'message': 'SMTP account {email} health critical ({score}/100)',
        'action': 'disable_smtp_account'
    },
    {
        'condition': 'sent_today >= daily_limit * 0.9',
        'severity': 'warning',
        'message': '{email} at 90% daily limit ({sent}/{limit})',
        'action': 'reduce_send_speed'
    },
    {
        'condition': 'open_rate_7d < 10% AND sent_7d > 50',
        'severity': 'warning',
        'message': 'Open rate dropped to {rate}% — possible spam folder issue',
        'action': 'review_content_and_sender'
    },
]
```

---

## 14. ANALYTICS AI

### 14.1 Predictive Models

```python
class AnalyticsAgent:
    def predict_best_send_time(self, workspace_id):
        """
        Analyze past opens/replies by:
        - Hour of day
        - Day of week
        - Time since last interaction
        Returns: optimal send windows
        """

    def predict_reply_probability(self, contact_id):
        """
        Features:
        - Lead score
        - Company size
        - Industry
        - Past open count
        - Email position in sequence
        - Days since last activity
        Returns: 0-100% probability
        """

    def detect_campaign_fatigue(self, workspace_id):
        """
        Detect when:
        - Open rates declining across campaigns
        - Unsubscribe rate increasing
        - Same contacts receiving too many emails
        """

    def recommend_audience_size(self, campaign_type):
        """
        Based on SMTP capacity, warmup stage, and historical bounce rates,
        recommend safe campaign size.
        """

    def a_b_test_analysis(self, campaign_a_id, campaign_b_id):
        """
        Statistical comparison:
        - Open rate difference + confidence interval
        - Reply rate difference
        - Click rate difference
        - Winner declaration with p-value
        """
```

---

## 15. AUTONOMOUS WORKFLOWS

### 15.1 Self-Optimizing Campaigns

```python
class SelfOptimizingCampaign:
    """
    AI automatically adjusts campaign parameters based on real-time results.
    """

    def optimize_during_execution(self, campaign_id):
        """
        Every 10 sends, check:
        1. If bounce rate > 10% → auto-pause, alert user
        2. If specific domain bouncing → skip that domain
        3. If SMTP account failing → switch to next
        4. If rate limited → increase delay between sends
        """

    def optimize_after_completion(self, campaign_id):
        """
        Post-campaign analysis:
        1. Which subject lines got best open rate?
        2. Which contacts are now hot leads?
        3. What time did most opens happen?
        4. Suggest follow-up campaign for non-responders
        5. Auto-generate "lessons learned" memory
        """
```

### 15.2 Autonomous SDR Workflows

```
WORKFLOW: "Morning Briefing" (runs daily at 9am)
─────────────────────────────────────────────
1. SDR Agent: Scan inbox for overnight replies
2. Inbox Agent: Categorize all new replies
3. SDR Agent: Identify top 5 leads needing response
4. SDR Agent: Draft reply suggestions for each
5. Deliverability Agent: Check SMTP health
6. Analytics Agent: Generate daily KPI summary
7. → Push notification: "Good morning! 3 hot leads waiting, SMTP healthy, 47 sends remaining"

WORKFLOW: "Hot Lead Response" (triggered by reply categorized as 'interested')
─────────────────────────────────────────────
1. Inbox Agent: Categorize reply as 'interested'
2. SDR Agent: Calculate response urgency
3. SDR Agent: Draft contextual reply using contact history
4. → Alert: "Hot lead! {name} from {company} is interested. Draft ready."
5. (Wait for user confirmation)
6. Action: Send reply
7. Memory: Log interaction pattern

WORKFLOW: "Campaign Health Check" (every 30 min during active campaign)
─────────────────────────────────────────────
1. Deliverability Agent: Check bounce rate
2. If bounce_rate > 10%: Auto-pause campaign
3. Analytics Agent: Compare current rate to historical
4. SDR Agent: Identify failing segments
5. → Alert with recommendations
```

---

## 16. SECURITY & PERMISSION SYSTEM

```python
# services/copilot/permissions.py

ROLE_PERMISSIONS = {
    'admin': ['*'],  # All actions
    'user': [
        'query_*',            # All queries
        'action_draft',       # Draft replies
        'action_enrich',      # Enrich contacts
        'action_pause',       # Pause own campaigns
        'action_resume',      # Resume own campaigns
        'action_retry',       # Retry failed (own campaigns)
        'action_sequence',    # Create sequences
        'analyze_*',          # All analysis
    ],
    'viewer': [
        'query_*',            # Read-only queries
        'analyze_*',          # Analysis (read-only)
    ]
}

# Multi-tenant isolation rules:
# 1. All queries scoped to workspace_id
# 2. Actions verify workspace ownership before execution
# 3. AI context NEVER leaks cross-workspace data
# 4. Memory is workspace-scoped
# 5. Agents only access their workspace's data
# 6. Rate limits are per-workspace
```

---

## 17. BACKEND SERVICE STRUCTURE

```
services/
├── copilot/
│   ├── __init__.py
│   ├── orchestrator.py       # Main entry point — routes messages
│   ├── intent_detector.py    # Classifies user intent
│   ├── context_builder.py    # Builds rich context per page
│   ├── prompt_composer.py    # Assembles final AI prompt
│   ├── response_parser.py    # Validates and extracts AI response
│   ├── executor.py           # Executes confirmed actions
│   ├── memory_manager.py     # Read/write conversation + long-term memory
│   ├── alert_engine.py       # Proactive alert detection
│   ├── action_registry.py    # All registered actions + schemas
│   ├── permissions.py        # Role-based access control
│   └── agents/
│       ├── __init__.py
│       ├── base_agent.py     # Abstract base class
│       ├── sdr_agent.py      # Lead prioritization, follow-up suggestions
│       ├── deliverability_agent.py  # SMTP monitoring, bounce analysis
│       ├── analytics_agent.py       # Performance insights, predictions
│       └── inbox_agent.py    # Reply categorization, draft generation
routes/
├── copilot.py                # Flask blueprint — all /api/copilot/* endpoints
```

---

## 18. IMPLEMENTATION PHASES

### Phase 1: Foundation (Week 1-2)
- [ ] Create `services/copilot/` directory structure
- [ ] Implement `action_registry.py` with all action definitions
- [ ] Implement `context_builder.py` (extend existing `get_page_context`)
- [ ] Implement `executor.py` with permission checks
- [ ] Create `copilot_conversations` and `copilot_actions` tables
- [ ] Migrate existing `/api/copilot/chat` to new orchestrator
- [ ] Add audit logging to all actions

### Phase 2: Intelligence (Week 3-4)
- [ ] Implement `intent_detector.py` (keyword + AI hybrid)
- [ ] Implement `memory_manager.py` (session + long-term)
- [ ] Implement `prompt_composer.py` with dynamic tool injection
- [ ] Add function calling format to AI requests
- [ ] Implement `alert_engine.py` with 5 core alerts
- [ ] Add `/api/copilot/suggestions` endpoint
- [ ] Add Command Palette (Ctrl+K) frontend

### Phase 3: Agents (Week 5-6)
- [ ] Implement `base_agent.py` abstract class
- [ ] Implement `sdr_agent.py` — lead scoring + follow-up suggestions
- [ ] Implement `deliverability_agent.py` — SMTP monitoring
- [ ] Implement `inbox_agent.py` — reply drafting enhancement
- [ ] Create `agent_tasks` table
- [ ] Add Celery Beat schedule for autonomous agent runs
- [ ] Add `/api/copilot/agents/*` endpoints

### Phase 4: Autonomy (Week 7-8)
- [ ] Implement autonomous workflows (morning briefing, hot lead response)
- [ ] Implement self-optimizing campaign logic
- [ ] Add WebSocket support for real-time push
- [ ] Implement `analytics_agent.py` — predictions + anomaly detection
- [ ] Add `copilot_alerts` with dismiss/action flow
- [ ] Inline page suggestions component
- [ ] Action rollback system

### Phase 5: Enterprise Polish (Week 9-10)
- [ ] A/B test analysis
- [ ] Predictive lead scoring model
- [ ] Multi-agent coordination (agents talking to each other)
- [ ] AI token usage tracking + budget controls
- [ ] Export conversation history
- [ ] Copilot preferences UI (tone, verbosity, auto-actions)
- [ ] Admin panel: copilot usage analytics

---

## 19. ENTERPRISE DIFFERENTIATORS

### vs Apollo/Instantly/Smartlead:
| Feature | Competitors | OutreachOS Copilot |
|---------|------------|-------------------|
| AI Reply Drafts | Basic templates | Context-aware, thread-history-based |
| Campaign Control | Manual pause/resume | AI auto-pauses on anomaly detection |
| Lead Scoring | Rule-based | Behavioral + AI predictive |
| Deliverability | Dashboard only | Autonomous monitoring + auto-fix |
| Sequences | Static timing | AI-optimized timing per contact |
| Reporting | Charts | Natural language "ask anything" |
| Actions | Click buttons | "Pause campaign 5" in natural language |
| Proactive | None | Push alerts before problems escalate |

### Unique Differentiators:
1. **Conversational Campaign Management** — "Launch a campaign targeting SaaS CTOs who opened my last email"
2. **Self-Healing Campaigns** — Auto-switches SMTP when one fails mid-campaign
3. **Predictive Reply Scoring** — "This contact has 78% chance of replying if you follow up Tuesday"
4. **AI Meeting Prep** — Before a booked meeting, auto-generates contact research brief
5. **Revenue Attribution** — Track which AI-generated emails led to meetings/revenue
6. **Cross-Campaign Learning** — AI remembers what worked and applies to new campaigns
7. **Autonomous Follow-Up Chains** — AI decides when/how to follow up without human input

---

## 20. LONG-TERM ROADMAP

### Q1: AI SDR Copilot v1
- Conversational interface with action execution
- 4 specialized agents (SDR, Deliverability, Analytics, Inbox)
- Proactive alerts
- Memory system

### Q2: Autonomous SDR
- Self-optimizing campaigns
- Autonomous follow-up workflows
- Predictive lead scoring
- A/B test automation

### Q3: Multi-Channel AI
- LinkedIn integration (AI drafts connection messages)
- Phone call scheduling suggestions
- Multi-channel sequence builder
- Cross-channel attribution

### Q4: Revenue Intelligence
- Pipeline prediction
- Revenue forecasting
- Deal stage automation
- CRM sync (HubSpot/Salesforce)
- Team performance benchmarking

### Year 2: AI SDR Platform
- Marketplace for AI agents (custom agent plugins)
- White-label copilot for agencies
- Enterprise SSO + RBAC
- Custom AI model fine-tuning per workspace
- Multi-language outreach (AI translates + localizes)
- Voice AI for phone follow-ups
