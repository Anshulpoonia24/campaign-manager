"""
migrate_copilot.py — Create copilot AI SDR tables
===================================================
Run once: python migrate_copilot.py
Safe to run multiple times (IF NOT EXISTS).
"""
from utils.db import get_db, is_postgres

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS copilot_conversations (
    id {pk},
    workspace_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    page_type TEXT,
    page_id INTEGER,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    intent TEXT,
    actions_taken TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS copilot_memory (
    id {pk},
    workspace_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    memory_type TEXT DEFAULT 'preference',
    category TEXT DEFAULT 'general',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    source TEXT,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(workspace_id, user_id, key)
);

CREATE TABLE IF NOT EXISTS copilot_actions (
    id {pk},
    workspace_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    session_id TEXT,
    action_type TEXT NOT NULL,
    action_params TEXT DEFAULT '{{}}',
    intent TEXT,
    risk_level TEXT,
    status TEXT DEFAULT 'pending',
    result TEXT DEFAULT '{{}}',
    error_message TEXT,
    execution_time_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id {pk},
    workspace_id INTEGER NOT NULL,
    agent_type TEXT NOT NULL,
    task_type TEXT NOT NULL,
    priority INTEGER DEFAULT 5,
    status TEXT DEFAULT 'queued',
    input_data TEXT DEFAULT '{{}}',
    output_data TEXT DEFAULT '{{}}',
    parent_task_id INTEGER,
    max_retries INTEGER DEFAULT 3,
    retry_count INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS copilot_alerts (
    id {pk},
    workspace_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT DEFAULT 'info',
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT DEFAULT '{{}}',
    suggested_action TEXT,
    dismissed INTEGER DEFAULT 0,
    dismissed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ab_tests (
    id {pk},
    workspace_id INTEGER NOT NULL,
    campaign_id INTEGER NOT NULL,
    test_type TEXT NOT NULL,
    variants TEXT DEFAULT '[]',
    split_pct INTEGER DEFAULT 50,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ab_test_events (
    id {pk},
    test_id INTEGER NOT NULL,
    variant_index INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS team_invites (
    id {pk},
    workspace_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    invited_by INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_log (
    id {pk},
    workspace_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_cc_session ON copilot_conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_cc_workspace ON copilot_conversations(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cm_workspace ON copilot_memory(workspace_id, category);
CREATE INDEX IF NOT EXISTS idx_ca_workspace ON copilot_actions(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ca_status ON copilot_actions(status);
CREATE INDEX IF NOT EXISTS idx_at_workspace ON agent_tasks(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_at_agent ON agent_tasks(agent_type, status);
CREATE INDEX IF NOT EXISTS idx_alerts_workspace ON copilot_alerts(workspace_id, dismissed, created_at DESC);
"""


def migrate():
    pg = is_postgres()
    pk = 'SERIAL PRIMARY KEY' if pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    sql = TABLES_SQL.format(pk=pk)

    conn = get_db()
    for stmt in sql.split(';'):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception as e:
                print(f'  [SKIP] {str(e)[:80]}')
    conn.commit()

    for stmt in INDEXES_SQL.split(';'):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass
    conn.commit()
    conn.close()
    print('[OK] Copilot tables created/verified')


if __name__ == '__main__':
    migrate()
