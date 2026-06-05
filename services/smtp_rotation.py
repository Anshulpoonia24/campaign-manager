"""
services/smtp_rotation.py — Full Sender Identity Rotation
==========================================================
Each inbox is an independent sender identity with:
- unique from_name, reply_to, bcc_emails, signature
- independent warmup stage and health score
- round-robin rotation by last_used
"""
import os
from datetime import datetime
from utils.db import get_db

# Warmup stage daily limits
WARMUP_LIMITS = {1: 10, 2: 20, 3: 35, 4: 50, 5: 100}


def get_next_smtp_account(workspace_id=1):
    """
    Pick the best available SMTP account using atomic UPDATE+SELECT.
    Prevents race condition where 2 workers pick the same account.
    """
    conn = get_db()
    from utils.db import USE_POSTGRES
    if USE_POSTGRES and hasattr(conn, 'raw'):
        # Atomic: update sent_today and return the account in one query
        account = conn.execute("""
            UPDATE smtp_accounts
            SET sent_today = sent_today + 1, last_used = ?
            WHERE id = (
                SELECT id FROM smtp_accounts
                WHERE active = 1
                AND sent_today < daily_limit
                AND health_score > 20
                AND workspace_id = ?
                ORDER BY last_used ASC NULLS FIRST
                LIMIT 1
            )
            RETURNING *
        """, (datetime.now(), workspace_id)).fetchone()
    else:
        # SQLite: non-atomic fallback (single worker thread, acceptable)
        account = conn.execute("""
            SELECT * FROM smtp_accounts
            WHERE active = 1
            AND sent_today < daily_limit
            AND health_score > 20
            AND workspace_id = ?
            ORDER BY CASE WHEN last_used IS NULL THEN 0 ELSE 1 END, last_used ASC
            LIMIT 1
        """, (workspace_id,)).fetchone()
        if account:
            conn.execute("""
                UPDATE smtp_accounts SET last_used=?, sent_today=sent_today+1 WHERE id=?
            """, (datetime.now(), account['id']))
            conn.commit()

    conn.close()

    if not account:
        return None

    # Build full sender identity — safe defaults for old rows
    email = account['email']
    return {
        # SMTP credentials
        'id':             account['id'],
        'email':          email,
        'password':       account['password'],
        'smtp_server':    account['smtp_server'],
        'smtp_port':      account['smtp_port'],
        # Brevo / custom login username (may differ from from_email)
        'login_username': _col(account, 'login_username') or email,
        # Sender identity
        'from_name':      _col(account, 'from_name') or email,
        'reply_to':       _col(account, 'reply_to')  or '',
        'bcc_emails':     _col(account, 'bcc_emails') or '',
        'signature':      _col(account, 'signature')  or '',
        # Stats
        'daily_limit':    account['daily_limit'],
        'sent_today':     account['sent_today'],
        'health_score':   account['health_score'],
        'warmup_stage':   account['warmup_stage'],
        'active':         account['active'],
        # Alias for backward compat
        'account_id':     account['id'],
        'from_email':     email,
    }


def _col(row, key):
    """Safely get column — returns None if column doesn't exist yet."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def append_signature(body: str, signature: str) -> str:
    """
    Append inbox signature to email body.
    Supports HTML signatures. Inserts before </body> if present,
    else appends at end.
    """
    if not signature or not signature.strip():
        return body

    # Wrap plain-text signatures in a styled div
    if not signature.strip().startswith('<'):
        sig_html = (
            '<div style="margin-top:24px;padding-top:12px;'
            'border-top:1px solid #e2e8f0;font-size:12px;'
            'color:#6B7280;line-height:1.6;">'
            + signature.replace('\n', '<br>')
            + '</div>'
        )
    else:
        sig_html = (
            '<div style="margin-top:24px;padding-top:12px;'
            'border-top:1px solid #e2e8f0;">'
            + signature
            + '</div>'
        )

    if '</body>' in body.lower():
        return body.replace('</body>', sig_html + '</body>')
    return body + sig_html


def mark_send_success(account_id):
    """Increment health score on successful send (max 100)."""
    conn = get_db()
    conn.execute("""
        UPDATE smtp_accounts
        SET health_score = MIN(100, health_score + 1)
        WHERE id = ?
    """, (account_id,))
    conn.commit()
    conn.close()


def mark_send_failure(account_id):
    """Decrement health score on failure. Auto-deactivate if health = 0."""
    conn = get_db()
    conn.execute("""
        UPDATE smtp_accounts
        SET health_score = MAX(0, health_score - 10)
        WHERE id = ?
    """, (account_id,))
    conn.execute("""
        UPDATE smtp_accounts
        SET active = 0
        WHERE id = ? AND health_score = 0
    """, (account_id,))
    conn.commit()
    conn.close()


def reset_daily_counts():
    """Reset sent_today for all accounts. Run at midnight."""
    conn = get_db()
    conn.execute("UPDATE smtp_accounts SET sent_today = 0")
    conn.commit()
    conn.close()


def check_warmup_upgrade():
    """Auto-upgrade warmup stage if health is good."""
    conn = get_db()
    accounts = conn.execute("""
        SELECT id, warmup_stage, health_score, daily_limit
        FROM smtp_accounts
        WHERE active = 1 AND warmup_stage < 5
    """).fetchall()

    for acc in accounts:
        if acc['health_score'] >= 80:
            new_stage = min(5, acc['warmup_stage'] + 1)
            new_limit = WARMUP_LIMITS[new_stage]
            conn.execute("""
                UPDATE smtp_accounts
                SET warmup_stage = ?, daily_limit = ?
                WHERE id = ?
            """, (new_stage, new_limit, acc['id']))

    conn.commit()
    conn.close()


def get_all_accounts():
    """Get all SMTP accounts with full identity."""
    conn = get_db()
    accounts = conn.execute("""
        SELECT * FROM smtp_accounts ORDER BY active DESC, health_score DESC
    """).fetchall()
    conn.close()
    return accounts
