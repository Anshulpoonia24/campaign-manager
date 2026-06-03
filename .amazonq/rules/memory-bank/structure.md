# Project Structure

## Root Directory Layout
```
campaign_manager/
├── app.py                    # Main Flask app — routes, auth, DB init, AI calls
├── celery_app.py             # Celery + Redis configuration
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (secrets)
├── campaigns.db              # Local SQLite DB (dev fallback)
├── data/campaigns.db         # Persistent SQLite DB
├── auth-system/              # Separate Next.js frontend auth system (Supabase-based)
├── routes/                   # Flask blueprints
├── services/                 # Business logic layer
├── tasks/                    # Celery async tasks
├── templates/                # Jinja2 HTML templates
├── static/                   # CSS, JS assets
├── utils/                    # Utilities (DB, logging, backup, constants)
├── logs/                     # Rotating log files
└── attachments/              # Email attachment uploads
```

## Core Components

### app.py (Main Application)
- Flask application factory with all route definitions (~3000+ lines)
- Handles: auth, campaigns, contacts, inbox, analytics, SMTP, tracking, sequences
- Contains inline DB initialization (init_db) with migration logic
- AI providers: call_groq(), call_gemini(), generate_ai_email()
- Background threads: IMAP checker, automation worker, daily reset
- Celery integration with threading fallback

### routes/
- `admin.py` — Admin blueprint: tenant management, AI config, user listing
- `copilot.py` — AI SDR Copilot blueprint: SSE streaming, action dispatch
- `__init__.py` — Package init

### services/
- `ai_service.py` — AI provider abstraction
- `automation_service.py` — Automation rules engine (RULE_META, process_automation_rules)
- `campaign_executor.py` — Background campaign execution with JobStatus tracking
- `copilot_service.py` — Copilot orchestration entry point
- `inbox_service.py` — Thread/message CRUD, AI reply categorization
- `industry_detector.py` — Contact intelligence enrichment, industry classification
- `lead_scoring.py` — Score computation, hot leads, click analytics
- `sequence_engine.py` — Multi-step email sequence state machine
- `smtp_rotation.py` — SMTP account selection, health tracking, warmup
- `smtp_service.py` — SMTP send abstraction
- `tracking.py` — Token generation, open/click processing, timeline
- `verification_service.py` — Email verification orchestration
- `workspace_service.py` — Workspace CRUD, workspace-scoped DB queries

### services/copilot/ (AI Copilot Subsystem)
- `agents/` — Specialized agents (analytics, campaign, deliverability, inbox, research, router)
  - `base_agent.py` — BaseAgent class all agents inherit from
  - `router.py` — Routes user intent to appropriate agent
- `handlers/` — Action handlers (analytics, campaign, contacts, inbox, navigation, sequence, smtp)
- `action_registry.py` — Registry of all executable copilot actions with schemas
- `intent_detector.py` — NLP-based intent classification
- `context_builder.py` — Builds context payload for AI from current page/state
- `executor.py` — Executes detected actions
- `orchestrator.py` — Top-level orchestration: intent → context → execute → respond
- `memory.py` — Conversation history and session memory
- `learning.py` — Usage pattern learning
- `observability.py` — Logging and metrics for copilot
- `rbac.py` — Role-based access control for copilot actions
- `autonomous.py` — Autonomous action execution
- `ab_testing.py` — A/B test support
- `alerts.py` — Alert triggers
- `function_caller.py` — LLM function-calling interface

### tasks/ (Celery Tasks)
- `email_tasks.py` — send_campaign_async, send_campaign_ai_async
- `ai_tasks.py` — enrich_all_contacts
- `automation_tasks.py` — Automation rule processing
- `enrichment_tasks.py` — enrich_single_contact
- `inbox_tasks.py` — check_replies_task
- `sequence_tasks.py` — enroll_contacts_task, process_sequences_task
- `tracking_tasks.py` — Tracking event processing
- `verification_tasks.py` — verify_all_contacts
- `_db.py` — DB helper shared across tasks

### utils/
- `db.py` — DB abstraction: SQLite (default) or PostgreSQL (USE_POSTGRES env var)
- `logger.py` — Centralized logger setup
- `backup.py` — SQLite backup with TTL guard
- `constants.py` — CATCHALL_DOMAINS and other constants
- `ownership.py` — owns_contact, owns_campaign, owns_smtp_account checks
- `pg_schema.py` — PostgreSQL schema initialization

### templates/
- Jinja2 HTML templates for all pages
- `admin/` — Admin-only pages
- Main pages: dashboard, campaigns, contacts, inbox, analytics, deliverability, settings, etc.

### static/
- `copilot.js` — Frontend JS for copilot chat widget
- `style.css` — Main app styles
- `landing.css` — Landing page styles

### auth-system/ (Next.js)
- Separate Next.js 15 app with Supabase authentication
- Route groups: `(auth)/` (login, signup, verify-email, etc.) and `(protected)/` (dashboard, admin, billing, settings)
- Middleware-based route protection via Supabase session
- Not directly coupled to Flask backend — standalone auth portal

## Architectural Patterns

### Multi-Tenant Isolation
- All user-facing tables have `workspace_id` column
- `workspace_service.get_wid()` extracts workspace from `current_user`
- `owns_*` helpers in `utils/ownership.py` guard all mutations

### Database Abstraction
- `utils/db.py` switches between SQLite (local) and PostgreSQL (production) via `USE_POSTGRES` env
- All queries use positional parameters (`?` for SQLite, `%s` for PostgreSQL via wrapper)
- `get_db()` returns a connection with dict-row factory

### Background Processing
- Primary: Celery workers with Redis broker (queues: email, ai, inbox, automation_queue, enrichment_queue)
- Fallback: Python `threading.Thread` when Celery/Redis unavailable
- `has_active_workers()` check before any Celery dispatch

### Storage Strategy
- Azure: `/home/data` (persistent), `/home/logs`, `/home/uploads`
- Render: `/opt/render/project/src/data`
- Local: `./data`, `./logs`, `./attachments`
- Auto-detection in `_setup_paths()` at startup

### API Design
- REST-style JSON APIs under `/api/` prefix
- `@login_required` on all protected routes
- Rate limiting via `flask-limiter` (200/hour default, tighter on sensitive endpoints)
- JSON responses for `/api/` paths, HTML flash redirects for form posts
