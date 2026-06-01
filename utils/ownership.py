"""
utils/ownership.py — Workspace ownership checks
=================================================
All write/delete/send operations must verify the resource
belongs to the current user's workspace before acting.
"""
from flask import jsonify, redirect, url_for, flash
from flask_login import current_user
from utils.db import get_db


def get_wid():
    try:
        return getattr(current_user, 'workspace_id', 1) or 1
    except Exception:
        return 1


def owns_contact(contact_id):
    """Return contact row if it belongs to current workspace, else None."""
    wid = get_wid()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM contacts WHERE id=? AND workspace_id=?", (contact_id, wid)
    ).fetchone()
    conn.close()
    return row


def owns_campaign(campaign_id):
    """Return campaign row if it belongs to current workspace, else None."""
    wid = get_wid()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM campaigns WHERE id=? AND workspace_id=?", (campaign_id, wid)
    ).fetchone()
    conn.close()
    return row


def owns_smtp_account(account_id):
    """Return smtp_account row if it belongs to current workspace, else None."""
    wid = get_wid()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM smtp_accounts WHERE id=? AND workspace_id=?", (account_id, wid)
    ).fetchone()
    conn.close()
    return row


def owns_email_sent(email_id):
    """Return emails_sent row if it belongs to current workspace, else None."""
    wid = get_wid()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM emails_sent WHERE id=? AND workspace_id=?", (email_id, wid)
    ).fetchone()
    conn.close()
    return row
