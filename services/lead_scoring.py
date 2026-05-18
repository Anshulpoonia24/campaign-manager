"""
Lead Scoring Service
Tracks engagement signals and calculates lead priority.
"""
from utils.db import get_db
from utils.logger import app_logger, error_logger

# Scoring rules
SCORE_RULES = {
    'open':             5,
    'multiple_opens':   10,
    'click':            20,
    'reply':            40,
    'interested':       70,
    'meeting':          100,
    'bounce':           -50,
}

PRIORITY_THRESHOLDS = {
    'hot':  50,
    'warm': 20,
    'cold': 0,
}


def update_lead_score(contact_id, event, cap=500):
    """Add score for an event. Cap at max value."""
    delta = SCORE_RULES.get(event, 0)
    if delta == 0:
        return
    try:
        conn = get_db()
        conn.execute("""
            UPDATE contacts
            SET lead_score = MAX(0, MIN(?, COALESCE(lead_score, 0) + ?))
            WHERE id = ?
        """, (cap, delta, contact_id))
        conn.commit()
        conn.close()
        app_logger.info(f'[LEAD SCORE] contact={contact_id} event={event} delta={delta:+d}')
    except Exception as e:
        error_logger.error(f'[LEAD SCORE] update failed: {str(e)}')


def calculate_priority(score):
    """Return priority label based on score."""
    if score >= PRIORITY_THRESHOLDS['hot']:
        return 'hot'
    elif score >= PRIORITY_THRESHOLDS['warm']:
        return 'warm'
    return 'cold'


def get_hot_leads(limit=20):
    """Get top leads sorted by score."""
    conn = get_db()
    leads = conn.execute("""
        SELECT c.id, c.name, c.company, c.email,
               COALESCE(c.lead_score, 0) as lead_score,
               c.status,
               MAX(es.sent_at) as last_activity,
               t.status as thread_status,
               t.id as thread_id
        FROM contacts c
        LEFT JOIN emails_sent es ON es.contact_id = c.id AND es.status = 'sent'
        LEFT JOIN threads t ON t.contact_id = c.id
        WHERE COALESCE(c.lead_score, 0) > 0
        GROUP BY c.id
        ORDER BY c.lead_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return leads


def get_click_analytics():
    """Get click analytics summary."""
    conn = get_db()
    total_clicks = conn.execute("SELECT COUNT(*) FROM email_clicks").fetchone()[0]
    total_sent = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE status='sent'").fetchone()[0]
    ctr = round((total_clicks / total_sent * 100), 1) if total_sent > 0 else 0

    top_urls = conn.execute("""
        SELECT clicked_url, COUNT(*) as clicks
        FROM email_clicks
        GROUP BY clicked_url
        ORDER BY clicks DESC
        LIMIT 10
    """).fetchall()

    top_contacts = conn.execute("""
        SELECT c.name, c.company, c.email, COUNT(ec.id) as clicks,
               COALESCE(c.lead_score, 0) as lead_score
        FROM email_clicks ec
        JOIN contacts c ON ec.contact_id = c.id
        GROUP BY ec.contact_id
        ORDER BY clicks DESC
        LIMIT 10
    """).fetchall()

    conn.close()
    return {
        'total_clicks': total_clicks,
        'total_sent': total_sent,
        'ctr': ctr,
        'top_urls': [dict(r) for r in top_urls],
        'top_contacts': [dict(r) for r in top_contacts],
    }
