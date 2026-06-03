"""
utils/init_db.py — Database Initialization & Migrations
==========================================================
Schema creation, safe migrations, seed data.
"""
import os
from werkzeug.security import generate_password_hash


def init_db(get_db, DEFAULT_SETTINGS):
    from utils.db import USE_POSTGRES
    _hash_pw = generate_password_hash
    conn = get_db()
    is_pg = USE_POSTGRES and hasattr(conn, 'raw')
    if is_pg:
        from utils.pg_schema import init_pg
        init_pg(conn)
        existing_user = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        if not existing_user:
            default_hash = _hash_pw('admin123')
            conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                         ('admin', default_hash, 'admin'))
            conn.commit()
        sa_user = conn.execute("SELECT id FROM users WHERE username=?", ('superadmin',)).fetchone()
        if not sa_user:
            sa_hash = _hash_pw(os.getenv('ADMIN_PASSWORD', 'OutreachOS@2025'))
            conn.execute("INSERT INTO users (username, password_hash, role, workspace_id) VALUES (?,?,?,?)",
                         ('superadmin', sa_hash, 'admin', 1))
            conn.commit()
        if not conn.execute("SELECT id FROM workspaces WHERE id=1").fetchone():
            conn.execute("INSERT INTO workspaces (id, name, slug, plan) VALUES (1, 'Default Workspace', 'default', 'free')")
            conn.commit()
        for k, v in DEFAULT_SETTINGS.items():
            existing = conn.execute("SELECT key FROM settings WHERE key=?", (k,)).fetchone()
            if not existing:
                conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", (k, v))
        conn.commit()
        for rule_key, enabled, delay_days, max_followups in [('no_reply_followup',1,2,3),('opened_multiple_times',1,1,2),('interested_pause',1,0,0),('ooo_retry',1,7,1),('bounce_pause',1,0,0)]:
            existing = conn.execute("SELECT id FROM automation_settings WHERE rule_key=?", (rule_key,)).fetchone()
            if not existing:
                conn.execute("INSERT INTO automation_settings (rule_key, enabled, delay_days, max_followups) VALUES (?,?,?,?)", (rule_key, enabled, delay_days, max_followups))
        conn.commit()
        conn.close()
        return

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, company TEXT, email TEXT UNIQUE NOT NULL,
            designation TEXT, priority TEXT, status TEXT DEFAULT 'new',
            email_valid INTEGER DEFAULT -1, validation_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'draft'
        );
        CREATE TABLE IF NOT EXISTS emails_sent (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER, contact_id INTEGER, email TEXT NOT NULL,
            subject TEXT, body TEXT, status TEXT DEFAULT 'pending',
            bounce_reason TEXT, opened INTEGER DEFAULT 0, replied INTEGER DEFAULT 0,
            tracking_id TEXT, sent_at TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        );
        CREATE TABLE IF NOT EXISTS follow_ups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER, email TEXT, name TEXT, company TEXT,
            replied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, notes TEXT,
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL, value TEXT, workspace_id INTEGER DEFAULT 1,
            UNIQUE(key, workspace_id)
        );
        CREATE TABLE IF NOT EXISTS ai_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL, purpose TEXT DEFAULT 'email',
            success INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS unsubscribes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL, reason TEXT DEFAULT '',
            unsubscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS smtp_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
            smtp_server TEXT DEFAULT 'smtp.hostinger.com', smtp_port INTEGER DEFAULT 587,
            from_name TEXT DEFAULT '', daily_limit INTEGER DEFAULT 50,
            sent_today INTEGER DEFAULT 0, health_score INTEGER DEFAULT 100,
            warmup_stage INTEGER DEFAULT 1, active INTEGER DEFAULT 1,
            last_used TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER, campaign_id INTEGER, subject TEXT,
            status TEXT DEFAULT 'active', unread_count INTEGER DEFAULT 0,
            last_message_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(id),
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER, direction TEXT, sender_email TEXT,
            recipient_email TEXT, subject TEXT, body TEXT,
            message_id TEXT, in_reply_to TEXT, ai_category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (thread_id) REFERENCES threads(id)
        );
        CREATE TABLE IF NOT EXISTS automation_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_key TEXT UNIQUE NOT NULL, enabled INTEGER DEFAULT 1,
            delay_days INTEGER DEFAULT 2, max_followups INTEGER DEFAULT 3,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS email_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_sent_id INTEGER, thread_id INTEGER, contact_id INTEGER,
            clicked_url TEXT, token TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lead_intelligence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER DEFAULT 1, contact_id INTEGER NOT NULL,
            company_summary TEXT DEFAULT '', industry TEXT DEFAULT '',
            employee_size TEXT DEFAULT '', tech_stack TEXT DEFAULT '',
            pain_points TEXT DEFAULT '', icp_score INTEGER DEFAULT 0,
            buying_signals TEXT DEFAULT '', outreach_angles TEXT DEFAULT '',
            ai_summary TEXT DEFAULT '', enrichment_status TEXT DEFAULT 'pending',
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        );
        CREATE TABLE IF NOT EXISTS company_intelligence_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE NOT NULL, company_name TEXT DEFAULT '',
            company_summary TEXT DEFAULT '', industry TEXT DEFAULT '',
            employee_size TEXT DEFAULT '', tech_stack TEXT DEFAULT '',
            pain_points TEXT DEFAULT '', buying_signals TEXT DEFAULT '',
            raw_website_text TEXT DEFAULT '',
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    for tbl_sql in [
        """CREATE TABLE IF NOT EXISTS sequence_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id INTEGER DEFAULT 1,
            campaign_id INTEGER NOT NULL, step_order INTEGER NOT NULL DEFAULT 1,
            step_type TEXT NOT NULL DEFAULT 'email', delay_days INTEGER NOT NULL DEFAULT 1,
            subject TEXT DEFAULT '', body TEXT DEFAULT '', ai_enabled INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id))""",
        """CREATE TABLE IF NOT EXISTS contact_sequence_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id INTEGER DEFAULT 1,
            contact_id INTEGER NOT NULL, campaign_id INTEGER NOT NULL,
            current_step INTEGER DEFAULT 1, status TEXT DEFAULT 'active',
            next_run_at TIMESTAMP, last_sent_at TIMESTAMP, completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(id),
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id))""",
    ]:
        try:
            conn.execute(tbl_sql)
            conn.commit()
        except Exception:
            pass

    conn.commit()

    existing_user = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
    if not existing_user:
        default_hash = _hash_pw('admin123')
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                     ('admin', default_hash, 'admin'))
        conn.commit()
        print("[AUTH] Default admin created -- username: admin, password: admin123")

    sa_user = conn.execute("SELECT id FROM users WHERE username=?", ('superadmin',)).fetchone()
    if not sa_user:
        sa_hash = _hash_pw(os.getenv('ADMIN_PASSWORD', 'OutreachOS@2025'))
        conn.execute("INSERT INTO users (username, password_hash, role, workspace_id) VALUES (?,?,?,?)",
                     ('superadmin', sa_hash, 'admin', 1))
        conn.commit()
        print("[AUTH] superadmin user created")

    for k, v in DEFAULT_SETTINGS.items():
        existing = conn.execute("SELECT key FROM settings WHERE key=?", (k,)).fetchone()
        if not existing:
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
    conn.commit()

    # Safe migrations
    migrations = [
        "ALTER TABLE contacts ADD COLUMN lead_score INTEGER DEFAULT 0",
        "ALTER TABLE contacts ADD COLUMN website TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN context TEXT DEFAULT ''",
        "CREATE TABLE IF NOT EXISTS workspaces (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL, plan TEXT DEFAULT 'free', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "ALTER TABLE users ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE contacts ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE campaigns ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE smtp_accounts ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE threads ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE follow_ups ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE automation_settings ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE email_clicks ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE emails_sent ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "ALTER TABLE ai_usage ADD COLUMN workspace_id INTEGER DEFAULT 1",
        "CREATE INDEX IF NOT EXISTS idx_seq_steps_campaign ON sequence_steps(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_seq_steps_order ON sequence_steps(campaign_id, step_order)",
        "CREATE INDEX IF NOT EXISTS idx_css_contact ON contact_sequence_state(contact_id)",
        "CREATE INDEX IF NOT EXISTS idx_css_campaign ON contact_sequence_state(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_css_next_run ON contact_sequence_state(next_run_at)",
        "CREATE INDEX IF NOT EXISTS idx_css_status ON contact_sequence_state(status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_css_unique ON contact_sequence_state(contact_id, campaign_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_li_contact ON lead_intelligence(contact_id)",
        "CREATE INDEX IF NOT EXISTS idx_li_workspace ON lead_intelligence(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_li_icp ON lead_intelligence(icp_score)",
        "CREATE INDEX IF NOT EXISTS idx_li_status ON lead_intelligence(enrichment_status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cic_domain ON company_intelligence_cache(domain)",
        "ALTER TABLE smtp_accounts ADD COLUMN reply_to TEXT DEFAULT ''",
        "ALTER TABLE smtp_accounts ADD COLUMN bcc_emails TEXT DEFAULT ''",
        "ALTER TABLE smtp_accounts ADD COLUMN signature TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN full_name TEXT DEFAULT ''",
        # Blogs table
        """CREATE TABLE IF NOT EXISTS blogs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            summary TEXT DEFAULT '',
            content TEXT DEFAULT '',
            cover_image TEXT DEFAULT '',
            author TEXT DEFAULT 'OutreachOS Team',
            category TEXT DEFAULT 'General',
            tags TEXT DEFAULT '',
            published INTEGER DEFAULT 0,
            featured INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
        "ALTER TABLE smtp_accounts ADD COLUMN login_username TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN industry TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN company_size TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN country TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN linkedin_url TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN linkedin_company_url TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN company_description TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN technologies TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN employee_range TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN founded_year TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN lead_source TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN enrichment_status TEXT DEFAULT 'pending'",
        "ALTER TABLE contacts ADD COLUMN last_enriched_at TIMESTAMP",
        "CREATE INDEX IF NOT EXISTS idx_contacts_industry ON contacts(industry)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_country ON contacts(country)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_enrichment ON contacts(enrichment_status)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_company_size ON contacts(company_size)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_lead_score ON contacts(lead_score)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_workspace_industry ON contacts(workspace_id, industry)",
        "ALTER TABLE campaigns ADD COLUMN last_heartbeat TIMESTAMP",
        "ALTER TABLE campaigns ADD COLUMN job_status TEXT DEFAULT 'draft'",
        "ALTER TABLE campaigns ADD COLUMN started_at TIMESTAMP",
        "ALTER TABLE campaigns ADD COLUMN completed_at TIMESTAMP",
        "ALTER TABLE campaigns ADD COLUMN send_mode TEXT DEFAULT 'template'",
        "ALTER TABLE campaigns ADD COLUMN total_contacts INTEGER DEFAULT 0",
        "ALTER TABLE campaigns ADD COLUMN sent_count INTEGER DEFAULT 0",
        "ALTER TABLE campaigns ADD COLUMN failed_count INTEGER DEFAULT 0",
        "ALTER TABLE campaigns ADD COLUMN subject_template TEXT DEFAULT ''",
        "ALTER TABLE campaigns ADD COLUMN body_template TEXT DEFAULT ''",
        "ALTER TABLE campaigns ADD COLUMN attachment_path TEXT DEFAULT ''",
        """CREATE TABLE IF NOT EXISTS campaign_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL,
            workspace_id INTEGER DEFAULT 1, contact_id INTEGER,
            level TEXT DEFAULT 'info', message TEXT NOT NULL,
            smtp_email TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        "CREATE INDEX IF NOT EXISTS idx_cl_campaign ON campaign_logs(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_cl_created ON campaign_logs(created_at DESC)",
        """CREATE TABLE IF NOT EXISTS copilot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id INTEGER DEFAULT 1,
            user_id INTEGER, page_type TEXT, page_id INTEGER,
            user_message TEXT, ai_response TEXT, action_taken TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    ]
    for migration in migrations:
        try:
            conn.execute(migration)
            conn.commit()
        except Exception:
            pass

    if not conn.execute("SELECT id FROM workspaces WHERE id=1").fetchone():
        conn.execute("INSERT OR IGNORE INTO workspaces (id, name, slug, plan) VALUES (1, 'Default Workspace', 'default', 'free')")
        conn.commit()

    for table in ['users','contacts','campaigns','smtp_accounts','threads','follow_ups',
                  'automation_settings','email_clicks','emails_sent','ai_usage','settings']:
        try:
            conn.execute(f"UPDATE {table} SET workspace_id=1 WHERE workspace_id IS NULL")
        except Exception:
            pass
    conn.commit()

    for rule_key, enabled, delay_days, max_followups in [
        ('no_reply_followup',1,2,3), ('opened_multiple_times',1,1,2),
        ('interested_pause',1,0,0), ('ooo_retry',1,7,1), ('bounce_pause',1,0,0),
    ]:
        existing = conn.execute("SELECT id FROM automation_settings WHERE rule_key=?", (rule_key,)).fetchone()
        if not existing:
            conn.execute("INSERT OR IGNORE INTO automation_settings (rule_key, enabled, delay_days, max_followups) VALUES (?,?,?,?)",
                         (rule_key, enabled, delay_days, max_followups))
    conn.commit()

    conn.execute("""CREATE TABLE IF NOT EXISTS send_reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id INTEGER NOT NULL DEFAULT 1,
        contact_id INTEGER NOT NULL, campaign_id INTEGER NOT NULL,
        send_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'sending',
        reserved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (workspace_id, contact_id, campaign_id, send_key))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sr_lookup ON send_reservations (workspace_id, contact_id, campaign_id, send_key)")
    conn.commit()
    conn.close()
