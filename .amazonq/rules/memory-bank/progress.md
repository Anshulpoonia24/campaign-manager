# OutreachOS — Living Development Document
> Last updated: 2026-06-03 | Read this FIRST in every new session

---

## ⚡ AGENT INSTRUCTIONS (MANDATORY — READ FIRST)

1. **Har commit ke saath `progress.md` update karna hai** — bina user ke bolne ke
2. **Session notes** mein kya kiya add karna hai (date + bullet points)
3. **Completed features** mein naya kaam add karna hai
4. **Known issues** update karne hain jab fix ho
5. **Ye document khud se maintain karna hai** — user ko remind nahi karna
6. **Naya session start ho toh ye file pehle padho** — poora context yahan hai
7. **User ko baar baar explain nahi karna** — jo pehle hua woh yahan documented hai

---

## 🚀 WHAT IS THIS PROJECT

**OutreachOS** — AI-powered B2B cold email outreach platform for Shiksha Infotech.

- **Live URL:** `https://ertyui.online`
- **Hosting:** Render (Web Service)
- **Database:** PostgreSQL via Supabase
- **Repo:** `https://github.com/Anshulpoonia24/campaign-manager`
- **Branch:** `main` (auto-deploys to Render on push)
- **Dev branch:** `devvvvvvvvvv` (merge to main to deploy)

---

## 🔐 CREDENTIALS & ACCESS

### App Logins
| Role | Username | Password | URL |
|---|---|---|---|
| Tenant Admin | `admin` | `admin123` | `/login` |
| Super Admin | `superadmin` | `OutreachOS@2025` | `/admin/login` |

> **Super Admin manages all API keys, SMTP, IMAP, Groq keys from `/admin` panel**

### Supabase (Database + Google OAuth)
- **Project URL:** `https://ygbwqhxxmfdvrenbpcnw.supabase.co`
- **Anon Key:** in `.env.local` of `auth-system/`
- **Google OAuth:** configured via Supabase → Auth → Providers → Google
- **Google Client ID:** `470085624373-eb5p9ff4np4abeuqh35hgkfv7r1euln8.apps.googleusercontent.com`

### SMTP Accounts (managed via Settings UI)
| Email | SMTP Server | Port | Purpose |
|---|---|---|---|
| `outreach@apnagang.com` | `smtp.hostinger.com` | 587 | Primary outbound |
| `anshul.shiksha@apnagang.com` | `smtp.hostinger.com` | 587 | Secondary outbound |

### IMAP (Reply Detection)
| Setting | Value |
|---|---|
| Server | `imap.hostinger.com` |
| Port | `993` |
| Username | `replies@apnagang.com` |
| Reply-To | `replies@apnagang.com` |
| Check interval | 180s (3 min) |

### Render Environment Variables
All set in Render dashboard. Key ones:
- `DATABASE_URL` — Supabase PostgreSQL (Transaction pooler)
- `SECRET_KEY` — Flask session secret
- `TRACKING_HOST` — `https://ertyui.online`
- `SUPABASE_URL` + `SUPABASE_ANON_KEY` — Google OAuth
- `GROQ_API_KEYS` — AI generation
- `IMAP_*` — Reply detection

---

## 🏗️ ARCHITECTURE

### Tech Stack
- **Backend:** Flask 3.1.3, Python 3.12, Gunicorn
- **Database:** SQLite (local dev) / PostgreSQL Supabase (production)
- **Queue:** Celery + Redis (fallback: threading)
- **AI:** Groq (llama-3.3-70b) primary → Gemini fallback
- **Email:** smtplib (send) + imaplib (receive)
- **Auth:** flask-login + Google OAuth via Supabase
- **Hosting:** Render (1 worker, 8 threads, gthread)

### File Structure
```
app.py                    # Main Flask app (~1028 lines) - core only
routes/                   # 12 blueprints
  auth.py                 # Login, logout, Google OAuth
  campaigns.py            # Campaign CRUD + execution
  contacts.py             # Contact management + intelligence
  settings.py             # SMTP, IMAP, AI config
  inbox.py                # Thread/reply management
  tracking.py             # Open/click tracking pixels
  analytics.py            # Dashboard metrics
  automations.py          # Automation rules
  sequences.py            # Multi-step sequence engine
  dashboard.py            # Main dashboard
  admin.py (routes/)      # Super admin panel
  copilot.py              # AI copilot (if enabled)
services/
  campaign_executor.py    # Backend campaign execution (browser-independent)
  sequence_engine.py      # Multi-step sequence logic
  inbox_service.py        # IMAP thread matching
  tracking.py             # Token generation, open/click processing
  smtp_rotation.py        # SMTP rotation + sender identity
  industry_detector.py    # AI industry detection
  lead_scoring.py         # Lead score calculation
  workspace_service.py    # Multi-tenant isolation
  automation_service.py   # Automation rules engine
tasks/                    # Celery async tasks
  email_tasks.py          # Campaign sending
  inbox_tasks.py          # IMAP sync
  enrichment_tasks.py     # Contact enrichment
  sequence_tasks.py       # Sequence processor
utils/
  db.py                   # PostgreSQL + SQLite abstraction
  init_db.py              # DB schema + migrations
  logger.py               # Rotating file loggers
  backup.py               # SQLite backup
templates/                # Jinja2 HTML (all pages)
static/style.css          # Full app styles
auth-system/              # Next.js Supabase auth (separate, not used in Flask)
```

---

## ✅ COMPLETED FEATURES

### Core Platform
- [x] Multi-tenant workspaces (workspace_id isolation on all tables)
- [x] Flask-login session auth + Google OAuth via Supabase
- [x] Super admin panel (`/admin`) — tenant management, API key management
- [x] Settings UI — SMTP, IMAP, AI, tracking, prompts

### Campaign System
- [x] Campaign creation wizard (6-step)
- [x] Template + AI-personalized sending
- [x] Backend execution (browser-independent, survives logout)
- [x] Campaign execution dashboard (live progress, activity feed, contact table)
- [x] Pause / Resume / Cancel campaigns
- [x] SMTP rotation with full sender identity (from_name, reply_to, bcc, signature)
- [x] Warmup stages (5 levels) + daily limits
- [x] Duplicate send prevention

### Sequence Engine
- [x] Multi-step sequences (Day 1 → Day 3 → Day 7 → etc.)
- [x] Sequence Builder UI (3-panel)
- [x] Contact progression (each contact moves independently)
- [x] Stop conditions (reply, bounce, unsubscribe, manual pause)
- [x] Smart delay (reduces delay if contact opened/clicked)
- [x] AI personalization per step

### Tracking
- [x] Open tracking pixel (`/track/TOKEN.png`)
- [x] Click tracking (`/click/TOKEN?url=...`)
- [x] HMAC-signed tokens (tamper-proof)
- [x] Bot filtering
- [x] tracking_events table + lead score updates
- [x] tracking_host = `https://ertyui.online` (production)

### Inbox & Replies
- [x] IMAP sync every 3 minutes
- [x] Thread matching via Message-ID / In-Reply-To
- [x] AI reply categorization (interested/meeting/ooo/etc.)
- [x] 3-panel inbox UI (Gmail-style)
- [x] AI reply drafts
- [x] Lead scoring on reply

### Contact Intelligence
- [x] Industry detection (26 industries)
- [x] AI company enrichment (website scrape + Groq)
- [x] ICP scoring
- [x] Advanced filters (industry, country, score, enrichment status)
- [x] Contact profile drawer

### Analytics
- [x] Dashboard metrics (open/click/reply/bounce rates)
- [x] Per-campaign analytics
- [x] Hot leads leaderboard
- [x] AI usage tracking

### Infrastructure
- [x] PostgreSQL + SQLite abstraction (auto-detects DATABASE_URL)
- [x] Celery + Redis queues (6 isolated queues)
- [x] Threading fallback when Redis unavailable
- [x] Rate limiting (flask-limiter)
- [x] `/api/diagnostics` endpoint for health monitoring

---

## 🐛 KNOWN ISSUES / TO-DO

### Active Issues
- [ ] Old sent emails (2 emails) have `localhost:5000` tracking URLs — can't fix retroactively
- [ ] IMAP credentials need to be re-entered after settings wipe (save them in Render env vars)

### Settings Save Bug (FIXED)
- `/api/settings/save` now protects `imap_password`, `smtp_password`, `groq_api_keys` from being wiped with empty values

### Auth
- Google OAuth implemented via Supabase — requires redirect URL `https://ertyui.online/auth/google/callback` in Supabase dashboard

---

## 🔄 DEPLOYMENT WORKFLOW

```bash
# Local dev
python app.py                    # runs on localhost:8000

# Deploy to production
git add -A
git commit -m "your message"
git push origin main             # triggers Render auto-deploy
```

### Render Deploy Settings
- Build: `pip install -r requirements.txt`
- Start: `gunicorn --bind 0.0.0.0:$PORT --timeout 600 --workers 1 --threads 8 --worker-class gthread app:app`
- Workers=1 to prevent duplicate IMAP checkers

---

## 📊 DATABASE

### Key Tables
| Table | Purpose |
|---|---|
| `users` | Tenant users (workspace_id) |
| `workspaces` | Multi-tenant isolation |
| `contacts` | Contact records + intelligence |
| `campaigns` | Campaign records + execution state |
| `emails_sent` | All sent email records + tracking |
| `threads` | Inbox conversation threads |
| `messages` | Individual messages in threads |
| `smtp_accounts` | SMTP rotation accounts (full identity) |
| `sequence_steps` | Multi-step sequence definitions |
| `contact_sequence_state` | Per-contact sequence progress |
| `tracking_events` | Open/click event log |
| `campaign_logs` | Campaign execution activity log |
| `lead_intelligence` | AI company enrichment data |
| `settings` | Per-workspace config |
| `automation_settings` | Automation rules config |

### Important Columns Added
- `smtp_accounts`: `reply_to`, `bcc_emails`, `signature`
- `campaigns`: `job_status`, `send_mode`, `total_contacts`, `attachment_path`
- `contacts`: `industry`, `company_size`, `country`, `enrichment_status`, `lead_score`

---

## 🎨 DESIGN SYSTEM

- **Theme:** Light glassmorphism, white/gray backgrounds
- **Primary:** Indigo `#6366f1` / Violet `#8b5cf6`
- **Green:** `#10b981` (success)
- **Font:** Inter
- **Background:** `#f8f9ff`
- **Cards:** white + `border: 1px solid #e5e7eb` + soft shadow
- **Sidebar:** `rgba(255,255,255,0.82)` + `backdrop-filter: blur(20px)`

---

## 🔑 HOW TRACKING WORKS

```
Send email
→ inject_tracking_pixel() adds:
   - <img src="https://ertyui.online/track/TOKEN.png">  ← open tracking
   - rewrites all href links to /click/TOKEN?url=...     ← click tracking
   - adds /unsubscribe/TOKEN link

Recipient opens email:
→ GET /track/TOKEN.png → process_open() → emails_sent.opened=1 → lead_score +2

Recipient clicks link:
→ GET /click/TOKEN → process_click() → email_clicks table → lead_score +10 → redirect

Recipient replies:
→ Goes to replies@apnagang.com (Reply-To header)
→ IMAP checker picks up every 3 min
→ Matches thread via In-Reply-To: <tracking_id@outreachos>
→ AI categorizes → inbox updated → lead_score +25
```

---

## 💡 IMPORTANT PATTERNS

### Settings are workspace-scoped
```python
get_setting('groq_api_keys')  # reads from current user's workspace
```
Never use empty string to clear passwords — `api/settings/save` skips empty for protected fields.

### Reply-To must = IMAP username
```
reply_to = replies@apnagang.com = imap_username
```
This is how replies get back to our monitoring inbox.

### Campaign execution is browser-independent
```python
launch_campaign() → threading.Thread(daemon=False) or Celery
# daemon=False means thread survives browser close
```

### All DB queries are workspace-scoped
```python
wid = get_wid()  # from current_user.workspace_id
conn.execute("SELECT * FROM contacts WHERE workspace_id=?", (wid,))
```

---

## 📝 SESSION NOTES

### 2026-06-03 (Latest)
- Fixed auth routes `url_for` for blueprint architecture
- Implemented Google OAuth via Supabase (`/auth/google`, `/auth/google/callback`)
- Added `render.yaml` for Render deployment
- Fixed `startup.sh` for Render (uses `$PORT`)
- Fixed tracking pixel URL (was `localhost:5000`, now `ertyui.online`)
- Fixed `Message-ID` header on outbound emails for IMAP reply matching
- Fixed `inject_tracking_pixel` click rewriting
- Fixed `api/settings/save` — never wipes passwords with empty values
- Added `/api/diagnostics` endpoint
- Added industry detection (`services/industry_detector.py`)
- Added `contact_sequence_state`, `campaign_logs`, `lead_intelligence` tables
- App refactored from 3000+ line monolith to 12 blueprints (~1028 lines)

### Previous Sessions
- Multi-step sequence engine (Phase 1-10)
- Campaign execution system (backend-driven, browser-independent)
- SMTP full sender identity (reply_to, bcc, signature per inbox)
- Inbox redesign (Gmail-style 3-panel)
- Tracking infrastructure (HMAC tokens, bot filtering, temperature engine)
- Multi-tenant workspaces
- Admin panel (separate session system)
- UI redesign (OutreachOS branding, light glassmorphism)
- Contact intelligence (industry detection, ICP scoring)
- Sequence builder UI (3-panel visual builder)
- Production audit + bug fixes (reply rate, click rate, tracking host)
