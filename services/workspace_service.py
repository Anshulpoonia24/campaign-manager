"""
services/workspace_service.py — Single-admin data helpers
===========================================================
Single admin app: all data belongs to the single admin.
All queries fetch all records (no workspace isolation).
"""
from utils.db import get_db


def get_wid():
    """Return 1 for single admin app. Kept for backward compatibility."""
    return 1


def get_wid_for_user(user_id):
    """Return 1 for single admin app. Kept for backward compatibility."""
    return 1


# ── DATA QUERIES ──────────────────────────────────────────────

def ws_contacts(wid=1, filter_type='all'):
    """Get contacts - no workspace filtering for single admin."""
    conn = get_db()
    try:
        base = "SELECT * FROM contacts"
        if filter_type == 'valid':
            return conn.execute(base + " WHERE email_valid=1 ORDER BY created_at DESC").fetchall()
        elif filter_type == 'invalid':
            return conn.execute(base + " WHERE email_valid=0 ORDER BY created_at DESC").fetchall()
        elif filter_type == 'new':
            return conn.execute(base + " WHERE status='new' ORDER BY created_at DESC").fetchall()
        elif filter_type == 'sent':
            return conn.execute(base + " WHERE status='sent' ORDER BY created_at DESC").fetchall()
        else:
            return conn.execute(base + " ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()


def ws_campaigns(wid=1):
    """Get campaigns - no workspace filtering for single admin."""
    conn = get_db()
    try:
        return conn.execute("""
            SELECT c.*,
                COUNT(CASE WHEN es.status='sent'                  THEN 1 END) as sent_count,
                COUNT(CASE WHEN es.opened=1                       THEN 1 END) as opened_count,
                COUNT(CASE WHEN es.replied=1                      THEN 1 END) as replied_count,
                COUNT(CASE WHEN es.status IN ('bounced','failed') THEN 1 END) as bounce_count
            FROM campaigns c
            LEFT JOIN emails_sent es ON es.campaign_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC
        """).fetchall()
    finally:
        conn.close()


def ws_smtp_accounts(wid=1):
    """Get SMTP accounts - no workspace filtering for single admin."""
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM smtp_accounts ORDER BY active DESC, health_score DESC"
        ).fetchall()
    finally:
        conn.close()


def ws_threads(wid=1, status_filter=None):
    """Get threads - no workspace filtering for single admin."""
    conn = get_db()
    try:
        base = """
            SELECT t.*,
                   c.name    as contact_name,
                   c.company as contact_company,
                   c.email   as contact_email,
                   camp.name as campaign_name
            FROM threads t
            LEFT JOIN contacts c     ON t.contact_id  = c.id
            LEFT JOIN campaigns camp ON t.campaign_id = camp.id
            WHERE t.status != 'ignored'
        """
        if status_filter:
            return conn.execute(base + " AND t.status=? ORDER BY t.last_message_at DESC",
                                (status_filter,)).fetchall()
        return conn.execute(base + " ORDER BY t.last_message_at DESC").fetchall()
    finally:
        conn.close()


def ws_settings(wid=1):
    """Get settings - no workspace filtering for single admin."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r['key']: r['value'] for r in rows}
    finally:
        conn.close()


def ws_get_setting(wid=1, key='', default=''):
    """Get setting - no workspace filtering for single admin."""
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default
    finally:
        conn.close()


def ws_set_setting(wid=1, key='', value=''):
    """Set setting - no workspace filtering for single admin."""
    conn = get_db()
    try:
        existing = conn.execute("SELECT key FROM settings WHERE key=?", (key,)).fetchone()
        if existing:
            conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
        else:
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def ws_stats(wid=1):
    """Get stats - no workspace filtering for single admin."""
    conn = get_db()
    try:
        return {
            'total_contacts': conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
            'total_sent':     conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0],
            'total_bounced':  conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status IN ('bounced','failed')").fetchone()[0],
            'total_opened':   conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened=1").fetchone()[0],
            'total_replied':  conn.execute("SELECT COUNT(*) FROM emails_sent WHERE replied=1").fetchone()[0],
            'total_clicks':   conn.execute("SELECT COUNT(DISTINCT contact_id) FROM email_clicks WHERE contact_id IS NOT NULL").fetchone()[0],
            'meetings_detected': conn.execute("SELECT COUNT(*) FROM threads WHERE status='meeting'").fetchone()[0],
        }
    finally:
        conn.close()


def create_workspace(name, slug=None):
    """No-op for single-admin app — always returns 1."""
    return 1


def assign_user_workspace(user_id, workspace_id):
    """No-op for single-admin app."""
    pass


def get_workspace(workspace_id):
    """Get workspace 1 for single-admin app."""
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM workspaces WHERE id=1").fetchone()
    finally:
        conn.close()
