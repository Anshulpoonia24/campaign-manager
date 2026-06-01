"""
PostgreSQL schema for OutreachOS.
Called by init_db() when DATABASE_URL is set.
"""

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'admin',
    workspace_id INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contacts (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    company TEXT,
    email TEXT UNIQUE NOT NULL,
    designation TEXT,
    priority TEXT,
    status TEXT DEFAULT 'new',
    email_valid INTEGER DEFAULT -1,
    validation_reason TEXT,
    lead_score INTEGER DEFAULT 0,
    website TEXT DEFAULT '',
    context TEXT DEFAULT '',
    workspace_id INTEGER DEFAULT 1,
    industry TEXT DEFAULT '',
    company_size TEXT DEFAULT '',
    country TEXT DEFAULT '',
    linkedin_url TEXT DEFAULT '',
    linkedin_company_url TEXT DEFAULT '',
    company_description TEXT DEFAULT '',
    technologies TEXT DEFAULT '',
    employee_range TEXT DEFAULT '',
    founded_year TEXT DEFAULT '',
    lead_source TEXT DEFAULT '',
    enrichment_status TEXT DEFAULT 'pending',
    last_enriched_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaigns (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'draft',
    workspace_id INTEGER DEFAULT 1,
    last_heartbeat TIMESTAMP,
    job_status TEXT DEFAULT 'draft',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    send_mode TEXT DEFAULT 'template',
    total_contacts INTEGER DEFAULT 0,
    sent_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    subject_template TEXT DEFAULT '',
    body_template TEXT DEFAULT '',
    attachment_path TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS emails_sent (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES campaigns(id),
    contact_id INTEGER REFERENCES contacts(id),
    email TEXT NOT NULL,
    subject TEXT,
    body TEXT,
    status TEXT DEFAULT 'pending',
    bounce_reason TEXT,
    opened INTEGER DEFAULT 0,
    replied INTEGER DEFAULT 0,
    tracking_id TEXT,
    sent_at TIMESTAMP,
    workspace_id INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS follow_ups (
    id SERIAL PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id),
    email TEXT,
    name TEXT,
    company TEXT,
    replied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    workspace_id INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    id SERIAL PRIMARY KEY,
    key TEXT NOT NULL,
    value TEXT,
    workspace_id INTEGER DEFAULT 1,
    UNIQUE(key, workspace_id)
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    purpose TEXT DEFAULT 'email',
    success INTEGER DEFAULT 1,
    workspace_id INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS unsubscribes (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    reason TEXT DEFAULT '',
    unsubscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS smtp_accounts (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    smtp_server TEXT DEFAULT 'smtp.hostinger.com',
    smtp_port INTEGER DEFAULT 587,
    from_name TEXT DEFAULT '',
    daily_limit INTEGER DEFAULT 50,
    sent_today INTEGER DEFAULT 0,
    health_score INTEGER DEFAULT 100,
    warmup_stage INTEGER DEFAULT 1,
    active INTEGER DEFAULT 1,
    last_used TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    workspace_id INTEGER DEFAULT 1,
    reply_to TEXT DEFAULT '',
    bcc_emails TEXT DEFAULT '',
    signature TEXT DEFAULT '',
    login_username TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS threads (
    id SERIAL PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id),
    campaign_id INTEGER REFERENCES campaigns(id),
    subject TEXT,
    status TEXT DEFAULT 'active',
    unread_count INTEGER DEFAULT 0,
    last_message_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    workspace_id INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    thread_id INTEGER REFERENCES threads(id),
    direction TEXT,
    sender_email TEXT,
    recipient_email TEXT,
    subject TEXT,
    body TEXT,
    message_id TEXT,
    in_reply_to TEXT,
    ai_category TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_settings (
    id SERIAL PRIMARY KEY,
    rule_key TEXT UNIQUE NOT NULL,
    enabled INTEGER DEFAULT 1,
    delay_days INTEGER DEFAULT 2,
    max_followups INTEGER DEFAULT 3,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    workspace_id INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS email_clicks (
    id SERIAL PRIMARY KEY,
    email_sent_id INTEGER,
    thread_id INTEGER,
    contact_id INTEGER,
    clicked_url TEXT,
    token TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    workspace_id INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lead_intelligence (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    company_summary TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    employee_size TEXT DEFAULT '',
    tech_stack TEXT DEFAULT '',
    pain_points TEXT DEFAULT '',
    icp_score INTEGER DEFAULT 0,
    buying_signals TEXT DEFAULT '',
    outreach_angles TEXT DEFAULT '',
    ai_summary TEXT DEFAULT '',
    enrichment_status TEXT DEFAULT 'pending',
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS company_intelligence_cache (
    id SERIAL PRIMARY KEY,
    domain TEXT UNIQUE NOT NULL,
    company_name TEXT DEFAULT '',
    company_summary TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    employee_size TEXT DEFAULT '',
    tech_stack TEXT DEFAULT '',
    pain_points TEXT DEFAULT '',
    buying_signals TEXT DEFAULT '',
    raw_website_text TEXT DEFAULT '',
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspaces (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    plan TEXT DEFAULT 'free',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sequence_steps (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    step_order INTEGER NOT NULL DEFAULT 1,
    step_type TEXT NOT NULL DEFAULT 'email',
    delay_days INTEGER NOT NULL DEFAULT 1,
    subject TEXT DEFAULT '',
    body TEXT DEFAULT '',
    ai_enabled INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contact_sequence_state (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    current_step INTEGER DEFAULT 1,
    status TEXT DEFAULT 'active',
    next_run_at TIMESTAMP,
    last_sent_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaign_logs (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    workspace_id INTEGER DEFAULT 1,
    contact_id INTEGER,
    level TEXT DEFAULT 'info',
    message TEXT NOT NULL,
    smtp_email TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS copilot_logs (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1,
    user_id INTEGER,
    page_type TEXT,
    page_id INTEGER,
    user_message TEXT,
    ai_response TEXT,
    action_taken TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS send_reservations (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL DEFAULT 1,
    contact_id INTEGER NOT NULL,
    campaign_id INTEGER NOT NULL,
    send_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sending',
    reserved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, contact_id, campaign_id, send_key)
);
"""

PG_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_seq_steps_campaign ON sequence_steps(campaign_id);
CREATE INDEX IF NOT EXISTS idx_seq_steps_order ON sequence_steps(campaign_id, step_order);
CREATE INDEX IF NOT EXISTS idx_css_contact ON contact_sequence_state(contact_id);
CREATE INDEX IF NOT EXISTS idx_css_campaign ON contact_sequence_state(campaign_id);
CREATE INDEX IF NOT EXISTS idx_css_next_run ON contact_sequence_state(next_run_at);
CREATE INDEX IF NOT EXISTS idx_css_status ON contact_sequence_state(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_css_unique ON contact_sequence_state(contact_id, campaign_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_li_contact ON lead_intelligence(contact_id);
CREATE INDEX IF NOT EXISTS idx_li_workspace ON lead_intelligence(workspace_id);
CREATE INDEX IF NOT EXISTS idx_li_icp ON lead_intelligence(icp_score);
CREATE INDEX IF NOT EXISTS idx_li_status ON lead_intelligence(enrichment_status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cic_domain ON company_intelligence_cache(domain);
CREATE INDEX IF NOT EXISTS idx_contacts_industry ON contacts(industry);
CREATE INDEX IF NOT EXISTS idx_contacts_country ON contacts(country);
CREATE INDEX IF NOT EXISTS idx_contacts_enrichment ON contacts(enrichment_status);
CREATE INDEX IF NOT EXISTS idx_contacts_company_size ON contacts(company_size);
CREATE INDEX IF NOT EXISTS idx_contacts_lead_score ON contacts(lead_score);
CREATE INDEX IF NOT EXISTS idx_contacts_workspace_industry ON contacts(workspace_id, industry);
CREATE INDEX IF NOT EXISTS idx_cl_campaign ON campaign_logs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_cl_created ON campaign_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sr_lookup ON send_reservations(workspace_id, contact_id, campaign_id, send_key);
"""


def init_pg(conn):
    """Initialize PostgreSQL schema — all tables + indexes + defaults."""
    # Create tables
    for stmt in PG_SCHEMA.split(';'):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass
    conn.commit()

    # Create indexes
    for stmt in PG_INDEXES.split(';'):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass
    conn.commit()
