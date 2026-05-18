"""
services/sequence_engine.py — OutreachOS Multi-Step Sequence Engine
=====================================================================
Core logic for:
- Enrolling contacts into sequences
- Evaluating stop conditions (reply, bounce, unsubscribe, manual pause)
- Calculating next send time (delay_days, business days, working hours)
- Advancing contact state step by step
- Per-step analytics
"""
from datetime import datetime, timedelta
from utils.db import get_db
from utils.logger import app_logger, error_logger

# ── VALID STATUSES ────────────────────────────────────────────
STOP_STATUSES = {'replied', 'bounced', 'unsubscribed', 'paused', 'completed'}

STEP_TYPES = ('email', 'followup', 'wait', 'ai_reply', 'condition')

# Working hours window (24h format, local server time)
SEND_HOUR_START = 9
SEND_HOUR_END   = 17


# ══════════════════════════════════════════════════════════════
# STEP CRUD
# ══════════════════════════════════════════════════════════════

def get_steps(campaign_id: int) -> list:
    """Return all active steps for a campaign ordered by step_order."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM sequence_steps
            WHERE campaign_id = ? AND active = 1
            ORDER BY step_order ASC
        """, (campaign_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_steps(campaign_id: int) -> list:
    """Return all steps including inactive (for builder UI)."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM sequence_steps
            WHERE campaign_id = ?
            ORDER BY step_order ASC
        """, (campaign_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_step(campaign_id: int, workspace_id: int, step_order: int,
             step_type: str, delay_days: int, subject: str,
             body: str, ai_enabled: bool = False) -> int:
    """Insert a new sequence step. Returns new step id."""
    conn = get_db()
    try:
        from utils.db import is_postgres
        if is_postgres():
            row = conn.execute("""
                INSERT INTO sequence_steps
                  (campaign_id, workspace_id, step_order, step_type,
                   delay_days, subject, body, ai_enabled)
                VALUES (?,?,?,?,?,?,?,?) RETURNING id
            """, (campaign_id, workspace_id, step_order, step_type,
                  delay_days, subject, body, 1 if ai_enabled else 0)).fetchone()
            conn.commit()
            return row[0]
        else:
            conn.execute("""
                INSERT INTO sequence_steps
                  (campaign_id, workspace_id, step_order, step_type,
                   delay_days, subject, body, ai_enabled)
                VALUES (?,?,?,?,?,?,?,?)
            """, (campaign_id, workspace_id, step_order, step_type,
                  delay_days, subject, body, 1 if ai_enabled else 0))
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def update_step(step_id: int, step_order: int, step_type: str,
                delay_days: int, subject: str, body: str,
                ai_enabled: bool, active: bool):
    """Update an existing sequence step."""
    conn = get_db()
    try:
        conn.execute("""
            UPDATE sequence_steps
            SET step_order=?, step_type=?, delay_days=?,
                subject=?, body=?, ai_enabled=?, active=?
            WHERE id=?
        """, (step_order, step_type, delay_days, subject, body,
              1 if ai_enabled else 0, 1 if active else 0, step_id))
        conn.commit()
    finally:
        conn.close()


def delete_step(step_id: int):
    """Hard delete a step."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM sequence_steps WHERE id=?", (step_id,))
        conn.commit()
    finally:
        conn.close()


def reorder_steps(campaign_id: int, ordered_ids: list):
    """
    Reorder steps given a list of step ids in desired order.
    Sets step_order = position index + 1.
    """
    conn = get_db()
    try:
        for idx, sid in enumerate(ordered_ids):
            conn.execute(
                "UPDATE sequence_steps SET step_order=? WHERE id=? AND campaign_id=?",
                (idx + 1, sid, campaign_id)
            )
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# ENROLLMENT
# ══════════════════════════════════════════════════════════════

def enroll_contact(contact_id: int, campaign_id: int,
                   workspace_id: int) -> bool:
    """
    Enroll a contact into a campaign sequence.
    Idempotent — skips if already enrolled.
    Returns True if newly enrolled, False if already exists.
    """
    conn = get_db()
    try:
        existing = conn.execute("""
            SELECT id, status FROM contact_sequence_state
            WHERE contact_id=? AND campaign_id=?
        """, (contact_id, campaign_id)).fetchone()

        if existing:
            # Re-activate if previously completed/paused
            if existing['status'] in ('completed', 'paused'):
                conn.execute("""
                    UPDATE contact_sequence_state
                    SET status='active', current_step=1,
                        next_run_at=?, completed_at=NULL
                    WHERE contact_id=? AND campaign_id=?
                """, (datetime.now(), contact_id, campaign_id))
                conn.commit()
                app_logger.info(f'[SEQ] Re-enrolled contact {contact_id} in campaign {campaign_id}')
                return True
            return False

        # First enrollment — schedule first step immediately
        conn.execute("""
            INSERT INTO contact_sequence_state
              (workspace_id, contact_id, campaign_id, current_step,
               status, next_run_at)
            VALUES (?,?,?,1,'active',?)
        """, (workspace_id, contact_id, campaign_id, datetime.now()))
        conn.commit()
        app_logger.info(f'[SEQ] Enrolled contact {contact_id} in campaign {campaign_id}')
        return True
    finally:
        conn.close()


def enroll_contacts_bulk(contact_ids: list, campaign_id: int,
                         workspace_id: int) -> dict:
    """Enroll multiple contacts. Returns {enrolled, skipped}."""
    enrolled = 0
    skipped  = 0
    for cid in contact_ids:
        if enroll_contact(cid, campaign_id, workspace_id):
            enrolled += 1
        else:
            skipped += 1
    return {'enrolled': enrolled, 'skipped': skipped}


# ══════════════════════════════════════════════════════════════
# STOP CONDITIONS
# ══════════════════════════════════════════════════════════════

def check_stop_conditions(contact_id: int, campaign_id: int) -> tuple:
    """
    Check all stop conditions for a contact.
    Returns (should_stop: bool, reason: str)

    Stop if:
    - Contact replied to any email in this campaign
    - Contact bounced
    - Contact unsubscribed
    - Thread status is interested/meeting/booked
    - Contact status is replied/bounced
    """
    conn = get_db()
    try:
        # 1. Unsubscribe check
        contact = conn.execute(
            "SELECT email, status FROM contacts WHERE id=?", (contact_id,)
        ).fetchone()
        if not contact:
            return True, 'contact_not_found'

        unsub = conn.execute(
            "SELECT id FROM unsubscribes WHERE email=?",
            (contact['email'].lower(),)
        ).fetchone()
        if unsub:
            return True, 'unsubscribed'

        # 2. Contact status check
        if contact['status'] in ('replied', 'bounced', 'unsubscribed'):
            return True, contact['status']

        # 3. Reply in this campaign
        replied = conn.execute("""
            SELECT id FROM emails_sent
            WHERE contact_id=? AND campaign_id=? AND replied=1
        """, (contact_id, campaign_id)).fetchone()
        if replied:
            return True, 'replied'

        # 4. Bounce in this campaign
        bounced = conn.execute("""
            SELECT id FROM emails_sent
            WHERE contact_id=? AND campaign_id=? AND status='bounced'
        """, (contact_id, campaign_id)).fetchone()
        if bounced:
            return True, 'bounced'

        # 5. Thread status — interested/meeting/booked means human took over
        thread = conn.execute("""
            SELECT status FROM threads
            WHERE contact_id=? AND campaign_id=?
            ORDER BY last_message_at DESC LIMIT 1
        """, (contact_id, campaign_id)).fetchone()
        if thread and thread['status'] in ('interested', 'meeting', 'booked'):
            return True, f'thread_{thread["status"]}'

        return False, 'ok'
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# DELAY CALCULATION
# ══════════════════════════════════════════════════════════════

def calculate_next_run(delay_days: int, business_days_only: bool = True,
                       respect_hours: bool = True) -> datetime:
    """
    Calculate next send datetime from now.

    - delay_days: calendar days to wait
    - business_days_only: skip Saturday/Sunday
    - respect_hours: schedule within SEND_HOUR_START–SEND_HOUR_END
    """
    next_dt = datetime.now() + timedelta(days=delay_days)

    if business_days_only:
        # Skip weekends
        while next_dt.weekday() >= 5:  # 5=Sat, 6=Sun
            next_dt += timedelta(days=1)

    if respect_hours:
        if next_dt.hour < SEND_HOUR_START:
            next_dt = next_dt.replace(hour=SEND_HOUR_START, minute=0, second=0)
        elif next_dt.hour >= SEND_HOUR_END:
            # Push to next business day at start hour
            next_dt += timedelta(days=1)
            if business_days_only:
                while next_dt.weekday() >= 5:
                    next_dt += timedelta(days=1)
            next_dt = next_dt.replace(hour=SEND_HOUR_START, minute=0, second=0)

    return next_dt


# ══════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ══════════════════════════════════════════════════════════════

def get_contact_state(contact_id: int, campaign_id: int) -> dict | None:
    """Get current sequence state for a contact."""
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT * FROM contact_sequence_state
            WHERE contact_id=? AND campaign_id=?
        """, (contact_id, campaign_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def pause_contact(contact_id: int, campaign_id: int):
    """Manually pause a contact's sequence."""
    conn = get_db()
    try:
        conn.execute("""
            UPDATE contact_sequence_state SET status='paused'
            WHERE contact_id=? AND campaign_id=?
        """, (contact_id, campaign_id))
        conn.commit()
        app_logger.info(f'[SEQ] Paused contact {contact_id} campaign {campaign_id}')
    finally:
        conn.close()


def resume_contact(contact_id: int, campaign_id: int):
    """Resume a paused contact — schedules next run immediately."""
    conn = get_db()
    try:
        conn.execute("""
            UPDATE contact_sequence_state
            SET status='active', next_run_at=?
            WHERE contact_id=? AND campaign_id=? AND status='paused'
        """, (datetime.now(), contact_id, campaign_id))
        conn.commit()
        app_logger.info(f'[SEQ] Resumed contact {contact_id} campaign {campaign_id}')
    finally:
        conn.close()


def mark_stopped(contact_id: int, campaign_id: int, reason: str):
    """Mark a contact's sequence as stopped with a reason."""
    status_map = {
        'replied':       'replied',
        'bounced':       'bounced',
        'unsubscribed':  'unsubscribed',
        'completed':     'completed',
    }
    status = status_map.get(reason, 'paused')
    conn = get_db()
    try:
        conn.execute("""
            UPDATE contact_sequence_state
            SET status=?, completed_at=?
            WHERE contact_id=? AND campaign_id=?
        """, (status, datetime.now(), contact_id, campaign_id))
        conn.commit()
    finally:
        conn.close()


def advance_state(contact_id: int, campaign_id: int,
                  next_step: int, next_run_at: datetime):
    """Move contact to next step and set next_run_at."""
    conn = get_db()
    try:
        conn.execute("""
            UPDATE contact_sequence_state
            SET current_step=?, next_run_at=?, last_sent_at=?
            WHERE contact_id=? AND campaign_id=?
        """, (next_step, next_run_at, datetime.now(), contact_id, campaign_id))
        conn.commit()
    finally:
        conn.close()


def mark_completed(contact_id: int, campaign_id: int):
    """Mark sequence as fully completed for this contact."""
    conn = get_db()
    try:
        conn.execute("""
            UPDATE contact_sequence_state
            SET status='completed', completed_at=?
            WHERE contact_id=? AND campaign_id=?
        """, (datetime.now(), contact_id, campaign_id))
        conn.commit()
        app_logger.info(f'[SEQ] Completed contact {contact_id} campaign {campaign_id}')
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# DUE CONTACTS QUERY
# ══════════════════════════════════════════════════════════════

def get_due_contacts(workspace_id: int, limit: int = 100) -> list:
    """
    Return contacts whose next_run_at is due and status is active.
    Used by the Celery processor every N minutes.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT css.*, c.name, c.email, c.company,
                   c.context, c.designation, c.lead_score
            FROM contact_sequence_state css
            JOIN contacts c ON css.contact_id = c.id
            WHERE css.workspace_id = ?
              AND css.status = 'active'
              AND css.next_run_at <= ?
            ORDER BY css.next_run_at ASC
            LIMIT ?
        """, (workspace_id, datetime.now(), limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# SMART WAIT — tracking-influenced delays
# ══════════════════════════════════════════════════════════════

def get_smart_delay(contact_id: int, base_delay_days: int) -> int:
    """
    Adjust delay based on engagement signals.
    - Multiple opens → reduce delay by 1 day (they're interested)
    - Link click    → reduce delay by 1 day
    - No open       → keep base delay
    Returns adjusted delay in days (minimum 1).
    """
    conn = get_db()
    try:
        opens = conn.execute("""
            SELECT COUNT(*) FROM tracking_events
            WHERE contact_id=? AND event_type IN ('email_open','multiple_opens')
        """, (contact_id,)).fetchone()[0]

        clicks = conn.execute("""
            SELECT COUNT(*) FROM tracking_events
            WHERE contact_id=? AND event_type='link_click'
        """, (contact_id,)).fetchone()[0]

        reduction = 0
        if opens >= 2:
            reduction += 1
        if clicks >= 1:
            reduction += 1

        return max(1, base_delay_days - reduction)
    except Exception:
        return base_delay_days
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# ANALYTICS
# ══════════════════════════════════════════════════════════════

def get_sequence_stats(campaign_id: int) -> dict:
    """
    Per-campaign sequence stats:
    - total enrolled, active, completed, replied, bounced, paused
    - per-step open/reply rates
    """
    conn = get_db()
    try:
        totals = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='active'      THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN status='completed'   THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status='replied'     THEN 1 ELSE 0 END) as replied,
                SUM(CASE WHEN status='bounced'     THEN 1 ELSE 0 END) as bounced,
                SUM(CASE WHEN status='paused'      THEN 1 ELSE 0 END) as paused,
                SUM(CASE WHEN status='unsubscribed' THEN 1 ELSE 0 END) as unsubscribed
            FROM contact_sequence_state
            WHERE campaign_id=?
        """, (campaign_id,)).fetchone()

        steps = get_steps(campaign_id)
        step_stats = []
        for s in steps:
            sent = conn.execute("""
                SELECT COUNT(*) FROM emails_sent
                WHERE campaign_id=? AND status='sent'
            """, (campaign_id,)).fetchone()[0]
            opened = conn.execute("""
                SELECT COUNT(*) FROM emails_sent
                WHERE campaign_id=? AND opened=1
            """, (campaign_id,)).fetchone()[0]
            replied = conn.execute("""
                SELECT COUNT(*) FROM emails_sent
                WHERE campaign_id=? AND replied=1
            """, (campaign_id,)).fetchone()[0]
            step_stats.append({
                'step_id':    s['id'],
                'step_order': s['step_order'],
                'step_type':  s['step_type'],
                'subject':    s['subject'],
                'delay_days': s['delay_days'],
                'sent':       sent,
                'open_rate':  round(opened / sent * 100, 1) if sent else 0,
                'reply_rate': round(replied / sent * 100, 1) if sent else 0,
            })

        return {
            'total':        totals['total'] or 0,
            'active':       totals['active'] or 0,
            'completed':    totals['completed'] or 0,
            'replied':      totals['replied'] or 0,
            'bounced':      totals['bounced'] or 0,
            'paused':       totals['paused'] or 0,
            'unsubscribed': totals['unsubscribed'] or 0,
            'steps':        step_stats,
        }
    finally:
        conn.close()


def get_contact_sequence_history(contact_id: int, campaign_id: int) -> list:
    """Get email send history for a contact in a sequence."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT es.subject, es.status, es.opened, es.replied,
                   es.sent_at, es.bounce_reason
            FROM emails_sent es
            WHERE es.contact_id=? AND es.campaign_id=?
            ORDER BY es.sent_at ASC
        """, (contact_id, campaign_id)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_campaign_contacts_state(campaign_id: int) -> list:
    """Get all contacts with their current sequence state for a campaign."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT css.*, c.name, c.email, c.company,
                   COALESCE(c.lead_score, 0) as lead_score
            FROM contact_sequence_state css
            JOIN contacts c ON css.contact_id = c.id
            WHERE css.campaign_id=?
            ORDER BY css.status ASC, css.next_run_at ASC
        """, (campaign_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# SAFETY SYSTEM
# ══════════════════════════════════════════════════════════════

DAILY_SEQUENCE_CAP = 200  # max sequence emails per workspace per day


def is_sequence_cap_reached(workspace_id: int) -> bool:
    """Check if workspace has hit daily sequence sending cap."""
    conn = get_db()
    try:
        today_sent = conn.execute("""
            SELECT COUNT(*) FROM emails_sent es
            JOIN contact_sequence_state css
              ON es.contact_id = css.contact_id
             AND es.campaign_id = css.campaign_id
            WHERE es.workspace_id = ?
              AND DATE(es.sent_at) = DATE('now')
              AND es.status = 'sent'
        """, (workspace_id,)).fetchone()[0]
        return today_sent >= DAILY_SEQUENCE_CAP
    except Exception:
        return False
    finally:
        conn.close()


def is_duplicate_sequence_send(contact_id: int, campaign_id: int,
                                subject: str) -> bool:
    """Prevent sending same subject to same contact in same campaign."""
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT id FROM emails_sent
            WHERE contact_id=? AND campaign_id=? AND subject=? AND status='sent'
        """, (contact_id, campaign_id, subject)).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        conn.close()


def get_sequence_safety_status(workspace_id: int) -> dict:
    """Return safety system status for a workspace."""
    conn = get_db()
    try:
        today_sent = conn.execute("""
            SELECT COUNT(*) FROM emails_sent es
            JOIN contact_sequence_state css
              ON es.contact_id = css.contact_id
             AND es.campaign_id = css.campaign_id
            WHERE es.workspace_id = ?
              AND DATE(es.sent_at) = DATE('now')
              AND es.status = 'sent'
        """, (workspace_id,)).fetchone()[0]
        return {
            'today_sent':  today_sent,
            'daily_cap':   DAILY_SEQUENCE_CAP,
            'remaining':   max(0, DAILY_SEQUENCE_CAP - today_sent),
            'cap_reached': today_sent >= DAILY_SEQUENCE_CAP,
        }
    except Exception:
        return {'today_sent': 0, 'daily_cap': DAILY_SEQUENCE_CAP,
                'remaining': DAILY_SEQUENCE_CAP, 'cap_reached': False}
    finally:
        conn.close()
