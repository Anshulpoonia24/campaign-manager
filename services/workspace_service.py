"""
services/workspace_service.py — Workspace Isolation Layer
==========================================================
Simple, safe multi-tenant helpers.
Every query that touches tenant data goes through these helpers.

Usage in routes:
    from services.workspace_service import get_wid, ws_contacts, ws_campaigns

    wid = get_wid()                          # current user's workspace_id
    contacts = ws_contacts(wid)              # all contacts for this workspace
    campaigns = ws_campaigns(wid)            # all campaigns for this workspace
"""
from flask_login import current_user
from utils.db import get_db


# ── WORKSPACE ID HELPERS ──────────────────────────────────────

def get_wid():
    """
    Return the workspace_id for the currently logged-in user.
    Falls back to 1 (Default Workspace) if not set.
    Safe to call from any route.
    """
    try:
        if current_user and current_user.is_authenticated:
            return getattr(current_user, 'workspace_id', 1) or 1
    except Exception:
        pass
    return 1


def get_wid_for_user(user_id):
    """Return workspace_id for a specific user_id."""
    conn = get_db()
    try:
        row = conn.execute('SELECT workspace_id FROM users WHERE id=?', (user_id,)).fetchone()
        return row['workspace_id'] if row and row['workspace_id'] else 1
    finally:
        conn.close()


# ── WORKSPACE SCOPED QUERIES ──────────────────────────────────

def ws_contacts(wid, filter_type='all'):
    """Get all contacts for a workspace with optional filter."""
    conn = get_db()
    try:
        base = "SELECT * FROM contacts WHERE workspace_id=?"
        if filter_type == 'valid':
            return conn.execute(base + " AND email_valid=1 ORDER BY created_at DESC", (wid,)).fetchall()
        elif filter_type == 'invalid':
            return conn.execute(base + " AND email_valid=0 ORDER BY created_at DESC", (wid,)).fetchall()
        elif filter_type == 'new':
            return conn.execute(base + " AND status='new' ORDER BY created_at DESC", (wid,)).fetchall()
        elif filter_type == 'sent':
            return conn.execute(base + " AND status='sent' ORDER BY created_at DESC", (wid,)).fetchall()
        else:
            return conn.execute(base + " ORDER BY created_at DESC", (wid,)).fetchall()
    finally:
        conn.close()


def ws_campaigns(wid):
    """Get all campaigns for a workspace with send stats."""
    conn = get_db()
    try:
        return conn.execute("""
            SELECT c.*,
                COUNT(CASE WHEN es.status='sent'                  THEN 1 END) as sent_count,
                COUNT(CASE WHEN es.opened=1                       THEN 1 END) as opened_count,
                COUNT(CASE WHEN es.replied=1                      THEN 1 END) as replied_count,
                COUNT(CASE WHEN es.status IN ('bounced','failed') THEN 1 END) as bounce_count
            FROM campaigns c
            LEFT JOIN emails_sent es ON es.campaign_id = c.id AND es.workspace_id = ?
            WHERE c.workspace_id = ?
            GROUP BY c.id
            ORDER BY c.created_at DESC
        """, (wid, wid)).fetchall()
    finally:
        conn.close()


def ws_smtp_accounts(wid):
    """Get all SMTP accounts for a workspace."""
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM smtp_accounts WHERE workspace_id=? ORDER BY active DESC, health_score DESC",
            (wid,)
        ).fetchall()
    finally:
        conn.close()


def ws_threads(wid, status_filter=None):
    """Get all inbox threads for a workspace."""
    conn = get_db()
    try:
        base = """
            SELECT t.*,
                   c.name    as contact_name,
                   c.company as contact_company,
                   c.email   as contact_email,
                   camp.name as campaign_name
            FROM threads t
            LEFT JOIN contacts c    ON t.contact_id  = c.id
            LEFT JOIN campaigns camp ON t.campaign_id = camp.id
            WHERE (t.workspace_id = ? OR t.workspace_id IS NULL)
            AND t.status != 'ignored'
        """
        if status_filter:
            return conn.execute(base + " AND t.status=? ORDER BY t.last_message_at DESC",
                                (wid, status_filter)).fetchall()
        return conn.execute(base + " ORDER BY t.last_message_at DESC", (wid,)).fetchall()
    finally:
        conn.close()


def ws_settings(wid):
    """Get all settings for a workspace as a dict."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE workspace_id=?", (wid,)
        ).fetchall()
        return {r['key']: r['value'] for r in rows}
    finally:
        conn.close()


def ws_get_setting(wid, key, default=''):
    """Get a single setting for a workspace."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=? AND workspace_id=?", (key, wid)
        ).fetchone()
        return row['value'] if row else default
    finally:
        conn.close()


def ws_set_setting(wid, key, value):
    """Set a setting for a workspace (upsert)."""
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT key FROM settings WHERE key=? AND workspace_id=?", (key, wid)
        ).fetchone()
        if existing:
            conn.execute("UPDATE settings SET value=? WHERE key=? AND workspace_id=?",
                         (value, key, wid))
        else:
            conn.execute("INSERT INTO settings (key, value, workspace_id) VALUES (?,?,?)",
                         (key, value, wid))
        conn.commit()
    finally:
        conn.close()


def ws_stats(wid):
    """Get dashboard stats for a workspace."""
    conn = get_db()
    try:
        return {
            'total_contacts': conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE workspace_id=?", (wid,)).fetchone()[0],
            'total_sent': conn.execute(
                "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status='sent'", (wid,)).fetchone()[0],
            'total_bounced': conn.execute(
                "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND status IN ('bounced','failed')", (wid,)).fetchone()[0],
            'total_opened': conn.execute(
                "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND opened=1", (wid,)).fetchone()[0],
            'total_replied': conn.execute(
                "SELECT COUNT(*) FROM emails_sent WHERE workspace_id=? AND replied=1", (wid,)).fetchone()[0],
            'total_clicks': conn.execute(
                "SELECT COUNT(DISTINCT contact_id) FROM email_clicks WHERE contact_id IS NOT NULL AND workspace_id=?", (wid,)).fetchone()[0],
            'meetings_detected': conn.execute(
                "SELECT COUNT(*) FROM threads WHERE workspace_id=? AND status='meeting'", (wid,)).fetchone()[0],
        }
    finally:
        conn.close()


# ── WORKSPACE CREATION ────────────────────────────────────────

def create_workspace(name, slug=None):
    """Create a new workspace. Returns workspace_id."""
    import re
    from datetime import datetime
    if not slug:
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    conn = get_db()
    try:
        # Ensure unique slug
        base_slug = slug
        i = 1
        while conn.execute("SELECT id FROM workspaces WHERE slug=?", (slug,)).fetchone():
            slug = f"{base_slug}-{i}"
            i += 1
        conn.execute(
            "INSERT INTO workspaces (name, slug, plan, created_at) VALUES (?,?,?,?)",
            (name, slug, 'free', datetime.now())
        )
        conn.commit()
        row = conn.execute("SELECT id FROM workspaces WHERE slug=?", (slug,)).fetchone()
        return row['id']
    finally:
        conn.close()


def assign_user_workspace(user_id, workspace_id):
    """Assign a user to a workspace."""
    conn = get_db()
    try:
        conn.execute("UPDATE users SET workspace_id=? WHERE id=?", (workspace_id, user_id))
        conn.commit()
    finally:
        conn.close()


def get_workspace(workspace_id):
    """Get workspace details."""
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    finally:
        conn.close()
