# Technology Stack

## Backend (Python / Flask)

### Core Framework
- Python 3.x
- Flask 3.1.3 — web framework
- Gunicorn 23.0.0 — WSGI server (production)
- Werkzeug 3.1.8 — WSGI utilities

### Authentication & Security
- flask-login 0.6.3 — session-based auth with UserMixin
- flask-limiter 4.1.1 — rate limiting (200/hour default)
- werkzeug.security — password hashing (generate_password_hash, check_password_hash)

### Database
- SQLite — default/local development (campaigns.db)
- PostgreSQL (Supabase) — production via psycopg2-binary 2.9.10
- DB abstraction in `utils/db.py` — switches via `USE_POSTGRES` env var

### Async Task Queue
- Celery 5.4.0 — distributed task queue
- Redis 5.2.1 — message broker
- Kombu 5.3.7 — messaging library (Celery dependency)
- Queues: `email`, `ai`, `inbox`, `automation_queue`, `enrichment_queue`, `default`

### AI / LLM
- Groq API — llama-3.3-70b-versatile model, primary AI provider
- Gemini 2.5 Flash (Google) — fallback AI provider
- Multi-key rotation for Groq with rate limit header tracking
- Configurable priority order (ai_priority setting): `groq,gemini`

### Email
- smtplib — SMTP sending (standard library)
- imaplib — IMAP reply polling (standard library)
- email.message.EmailMessage — email construction
- dnspython 2.7.0 — MX record lookups for email verification

### Data Processing
- pandas 2.2.3 — Excel/CSV import and export
- openpyxl 3.1.5 — Excel file handling
- beautifulsoup4 4.13.4 — website scraping for contact enrichment

### HTTP Client
- requests 2.32.3 — HTTP calls to Groq/Gemini APIs and website scraping

### Configuration
- python-dotenv 1.2.2 — .env file loading

## Frontend (Next.js Auth System)

### Framework
- Next.js 15 (App Router)
- React 18 + TypeScript
- Tailwind CSS

### Auth
- Supabase — authentication backend (OTP, magic link, OAuth)
- Supabase SSR client (middleware-based session management)

### Route Groups
- `(auth)/` — public auth pages: login, signup, forgot-password, reset-password, verify-email, verify-otp
- `(protected)/` — gated pages: dashboard, admin, billing, settings
- `api/auth/` — auth API routes: OTP send/verify/resend, OAuth callback

## Infrastructure & Deployment

### Cloud Platforms
- Microsoft Azure App Service (primary) — Linux, `/home` for persistent storage
- Render — alternative deployment, `/opt/render/project/src`

### CI/CD
- GitHub Actions (`.github/workflows/main_shiksha-outreach.yml`)

### Environment Variables
Key variables from `.env` / Azure App Settings:
```
SECRET_KEY           — Flask session secret
DATABASE_URL         — PostgreSQL connection string (enables USE_POSTGRES)
REDIS_URL            — Redis broker URL for Celery
GROQ_API_KEYS        — Comma-separated Groq API keys
GEMINI_API_KEY       — Google Gemini API key
AI_PRIORITY          — Provider order: groq,gemini
SMTP_SERVER          — Default SMTP server
SMTP_PORT            — Default SMTP port (587)
SMTP_USERNAME        — Default SMTP login
SMTP_PASSWORD        — Default SMTP password
FROM_EMAIL           — Default sender email
FROM_NAME            — Default sender name
REPLY_TO             — Reply-to address
BCC_EMAILS           — BCC recipients
TRACKING_HOST        — Public URL for tracking pixels/clicks (e.g. https://ertyui.online)
IMAP_SERVER          — IMAP server for reply polling
IMAP_PORT            — IMAP port (993)
IMAP_USERNAME        — IMAP login
IMAP_PASSWORD        — IMAP password
IMAP_CHECK_INTERVAL  — Polling interval in seconds (default 180)
```

## Development Commands

### Start Flask App (Windows)
```bat
START_SERVER.bat
```

### Start Celery Workers (Windows)
```bat
START_WORKERS.bat
```

### Start Workers (Linux/Mac)
```sh
./start_workers.sh
```

### Install Dependencies
```sh
pip install -r requirements.txt
```

### Database
- SQLite DB auto-initialized on first run via `init_db()` in app.py
- PostgreSQL schema via `utils/pg_schema.py` → `init_pg(conn)`
- Migrations run as safe ALTER TABLE statements inside `init_db()` (exception-ignored)

### Next.js Auth System
```sh
cd auth-system
npm install
npm run dev      # Development
npm run build    # Production build
npm start        # Production server
```

## Logging
- `logs/app.log` — general application events (RotatingFileHandler, 5MB × 3)
- `logs/smtp.log` — SMTP send/bounce events
- `logs/error.log` — errors with module:line info
- `logs/server.log` — server-level logs
- Named loggers: `campaign`, `smtp`, `errors`
- NullHandler fallback if log directory is not writable

## Key Version Constraints
- Flask 3.1.x — required for current route/blueprint patterns
- Celery 5.4.x — uses celery.result.AsyncResult pattern
- psycopg2-binary (not psycopg2) — avoids build dependencies on cloud
- pandas 2.2.x — f-string in `f'{', '.join(skipped_names[:10])}...'` requires Python 3.12+
