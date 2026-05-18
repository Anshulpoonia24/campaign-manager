"""
Automation Service — Semi-configurable automation rules.
Rules are predefined internally. Users can enable/disable and configure delays/limits.
"""
import os
from datetime import datetime, timedelta
from utils.db import get_db, get_setting
from utils.logger import app_logger, error_logger

# ==============================
# RULE METADATA (display info)
# ==============================
RULE_META = {
    'no_reply_followup': {
        'title': 'Auto Follow-up if No Reply',
        'description': 'Automatically send a follow-up email if contact has not replied after X days.',
        'icon': 'fa-paper-plane',
        'color': '#3b82f6',
        'show_delay': True,
        'show_max': True,
        'delay_label': 'Days after last email',
        'max_label': 'Max follow-ups',
    },
    'opened_multiple_times': {
        'title': 'Follow-up if Opened Multiple Times',
        'description': 'Send a follow-up when a contact opens the email more than once (high intent signal).',
        'icon': 'fa-eye',
        'color': '#8b5cf6',
        'show_delay': True,
        'show_max': True,
        'delay_label': 'Days after first open',
        'max_label': 'Max follow-ups',
    },
    'interested_pause': {
        'title': 'Pause Sequence if Interested Reply',
        'description': 'Automatically pause further automated emails when AI detects an interested reply.',
        'icon': 'fa-pause-circle',
        'color': '#22c55e',
        'show_delay': False,
        'show_max': False,
        'delay_label': '',
        'max_label': '',
    },
    'ooo_retry': {
        'title': 'Retry After Out-of-Office Reply',
        'description': 'Automatically retry sending after X days when an out-of-office reply is detected.',
        'icon': 'fa-clock',
        'color': '#f59e0b',
        'show_delay': True,
        'show_max': False,
        'delay_label': 'Retry after (days)',
        'max_label': '',
    },
    'bounce_pause': {
        'title': 'Pause SMTP Account on Bounce',
        'description': 'Automatically reduce health score and pause SMTP account if bounce rate is high.',
        'icon': 'fa-exclamation-triangle',
        'color': '#ef4444',
        'show_delay': False,
        'show_max': False,
        'delay_label': '',
        'max_label': '',
    },
}


def get_rule_settings():
    """Get all automation rules with their current settings."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM automation_settings ORDER BY id").fetchall()
    conn.close()
    rules = {}
    for row in rows:
        key = row['rule_key']
        rules[key] = {
            'id': row['id'],
            'rule_key': key,
            'enabled': bool(row['enabled']),
            'delay_days': row['delay_days'],
            'max_followups': row['max_followups'],
            'meta': RULE_META.get(key, {}),
        }
    return rules


def get_rule(rule_key):
    """Get a single rule's settings."""
    conn = get_db()
    row = conn.execute("SELECT * FROM automation_settings WHERE rule_key=?", (rule_key,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_rule(rule_key, enabled, delay_days, max_followups):
    """Update a rule's settings."""
    conn = get_db()
    conn.execute("""
        UPDATE automation_settings
        SET enabled=?, delay_days=?, max_followups=?
        WHERE rule_key=?
    """, (1 if enabled else 0, delay_days, max_followups, rule_key))
    conn.commit()
    conn.close()


def should_send_followup(contact_id, campaign_id):
    """
    Check if a follow-up should be sent for this contact+campaign.
    Returns (True, reason) or (False, reason).
    """
    rule = get_rule('no_reply_followup')
    if not rule or not rule['enabled']:
        return False, 'Rule disabled'

    conn = get_db()

    # Check if contact has replied
    thread = conn.execute("""
        SELECT t.status, t.id FROM threads t
        WHERE t.contact_id = ? AND t.campaign_id = ?
        ORDER BY t.last_message_at DESC LIMIT 1
    """, (contact_id, campaign_id)).fetchone()

    if thread and thread['status'] in ('interested', 'meeting', 'booked', 'closed'):
        conn.close()
        return False, f'Thread status: {thread["status"]}'

    # Check if contact replied
    replied = conn.execute(
        "SELECT id FROM emails_sent WHERE contact_id=? AND replied=1", (contact_id,)
    ).fetchone()
    if replied:
        conn.close()
        return False, 'Already replied'

    # Count existing follow-ups sent
    followup_count = conn.execute("""
        SELECT COUNT(*) FROM emails_sent
        WHERE contact_id=? AND campaign_id=? AND status='sent'
    """, (contact_id, campaign_id)).fetchone()[0]

    if followup_count > rule['max_followups']:
        conn.close()
        return False, f'Max follow-ups ({rule["max_followups"]}) reached'

    # Check last email sent date
    last_sent = conn.execute("""
        SELECT sent_at FROM emails_sent
        WHERE contact_id=? AND campaign_id=? AND status='sent'
        ORDER BY sent_at DESC LIMIT 1
    """, (contact_id, campaign_id)).fetchone()

    conn.close()

    if not last_sent or not last_sent['sent_at']:
        return False, 'No email sent yet'

    try:
        last_sent_dt = datetime.fromisoformat(str(last_sent['sent_at'])[:19])
        days_since = (datetime.now() - last_sent_dt).days
        if days_since >= rule['delay_days']:
            return True, f'{days_since} days since last email'
        return False, f'Only {days_since} days since last email (need {rule["delay_days"]})'
    except Exception:
        return False, 'Date parse error'


def generate_followup_email(contact_name, company, context='', previous_subject=''):
    """Generate an AI follow-up email draft."""
    try:
        import requests as http_requests
        from utils.db import get_setting

        prompt = f"""Write a short follow-up email for {contact_name} at {company}.

Previous email subject: {previous_subject}
{f'Company context: {context}' if context else ''}

Rules:
- Very short (2-3 sentences max)
- Casual, not pushy
- Reference previous email briefly
- Simple CTA (15 min call?)
- Output as HTML with <p> tags
- End with:
<p>Best,<br>Anshul<br><b>Shiksha Infotech</b></p>"""

        groq_keys = get_setting('groq_api_keys') or ''
        keys = [k.strip() for k in groq_keys.split(',') if k.strip()]
        if keys:
            r = http_requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {keys[0]}', 'Content-Type': 'application/json'},
                json={'model': 'llama-3.3-70b-versatile',
                      'messages': [{'role': 'user', 'content': prompt}],
                      'max_tokens': 300},
                timeout=20
            )
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        error_logger.error(f'Follow-up AI generation failed: {str(e)}')
    return None


def process_automation_rules():
    """
    Main automation processor — runs periodically.
    Checks all rules and takes appropriate actions.
    Returns dict with stats.
    """
    stats = {
        'followups_queued': 0,
        'threads_paused': 0,
        'ooo_retries_queued': 0,
        'errors': 0,
    }

    try:
        conn = get_db()

        # ── Rule 1: interested_pause ──────────────────────────────
        rule_pause = get_rule('interested_pause')
        if rule_pause and rule_pause['enabled']:
            # Find threads with interested/meeting AI category that are still active
            interested_threads = conn.execute("""
                SELECT DISTINCT m.thread_id
                FROM messages m
                JOIN threads t ON m.thread_id = t.id
                WHERE m.ai_category IN ('interested', 'meeting')
                AND m.direction = 'incoming'
                AND t.status = 'active'
            """).fetchall()

            for row in interested_threads:
                conn.execute(
                    "UPDATE threads SET status='interested' WHERE id=?",
                    (row['thread_id'],)
                )
                stats['threads_paused'] += 1
                app_logger.info(f'[AUTOMATION] Thread {row["thread_id"]} paused — interested reply detected')

        # ── Rule 2: ooo_retry ─────────────────────────────────────
        rule_ooo = get_rule('ooo_retry')
        if rule_ooo and rule_ooo['enabled']:
            retry_after = rule_ooo['delay_days']
            cutoff = datetime.now() - timedelta(days=retry_after)

            ooo_threads = conn.execute("""
                SELECT DISTINCT m.thread_id, t.contact_id, t.campaign_id
                FROM messages m
                JOIN threads t ON m.thread_id = t.id
                WHERE m.ai_category = 'out_of_office'
                AND m.direction = 'incoming'
                AND m.created_at <= ?
                AND t.status = 'active'
            """, (cutoff,)).fetchall()

            for row in ooo_threads:
                # Mark for retry (just log — actual send is manual or next campaign run)
                app_logger.info(f'[AUTOMATION] OOO retry ready: thread {row["thread_id"]}')
                stats['ooo_retries_queued'] += 1

        conn.commit()
        conn.close()

        # ── Rule 3: no_reply_followup ─────────────────────────────
        rule_fu = get_rule('no_reply_followup')
        if rule_fu and rule_fu['enabled']:
            conn2 = get_db()
            # Get all contacts with sent emails but no reply
            candidates = conn2.execute("""
                SELECT DISTINCT es.contact_id, es.campaign_id, c.name, c.company, c.context
                FROM emails_sent es
                JOIN contacts c ON es.contact_id = c.id
                WHERE es.status = 'sent'
                AND es.replied = 0
                AND c.status != 'replied'
            """).fetchall()
            conn2.close()

            for row in candidates:
                should, reason = should_send_followup(row['contact_id'], row['campaign_id'])
                if should:
                    stats['followups_queued'] += 1
                    app_logger.info(f'[AUTOMATION] Follow-up queued: {row["name"]} ({row["company"]}) — {reason}')

        app_logger.info(f'[AUTOMATION] Processed: {stats}')

    except Exception as e:
        error_logger.error(f'[AUTOMATION] process_automation_rules error: {str(e)}')
        stats['errors'] += 1

    return stats


def get_automation_stats():
    """Get current automation stats for dashboard."""
    conn = get_db()
    stats = {
        'total_threads': conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0],
        'interested_threads': conn.execute("SELECT COUNT(*) FROM threads WHERE status='interested'").fetchone()[0],
        'meeting_threads': conn.execute("SELECT COUNT(*) FROM threads WHERE status='meeting'").fetchone()[0],
        'booked_threads': conn.execute("SELECT COUNT(*) FROM threads WHERE status='booked'").fetchone()[0],
        'ooo_detected': conn.execute("SELECT COUNT(*) FROM messages WHERE ai_category='ooo'").fetchone()[0],
        'active_rules': conn.execute("SELECT COUNT(*) FROM automation_settings WHERE enabled=1").fetchone()[0],
    }
    conn.close()
    return stats
