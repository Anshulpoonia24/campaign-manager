import os
from datetime import datetime
from utils.db import get_db, get_setting
from utils.logger import app_logger, error_logger

# AI categories
AI_CATEGORIES = ['interested', 'not_interested', 'later', 'meeting', 'out_of_office', 'spam']


def _insert_and_get_id(conn, sql, params):
    """Insert a row and return its ID — works for both SQLite and PostgreSQL."""
    from utils.db import is_postgres
    if is_postgres():
        # PostgreSQL: use RETURNING id
        returning_sql = sql.rstrip().rstrip(')').rstrip()
        # Append RETURNING id to INSERT ... VALUES (...)
        row = conn.execute(sql + ' RETURNING id', params).fetchone()
        conn.commit()
        return row[0]
    else:
        conn.execute(sql, params)
        conn.commit()
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]


def get_or_create_thread(contact_id, campaign_id, subject):
    """Find existing thread or create new one for this contact+campaign."""
    conn = get_db()
    thread = conn.execute("""
        SELECT * FROM threads
        WHERE contact_id = ? AND campaign_id = ?
        ORDER BY last_message_at DESC
        LIMIT 1
    """, (contact_id, campaign_id)).fetchone()

    if thread:
        conn.close()
        return thread['id']

    thread_id = _insert_and_get_id(conn,
        "INSERT INTO threads (contact_id, campaign_id, subject, last_message_at, workspace_id) VALUES (?, ?, ?, ?, ?)",
        (contact_id, campaign_id, subject, datetime.now(), 1)
    )
    conn.close()
    return thread_id


def find_thread_by_email(sender_email, subject, in_reply_to=None):
    """Find thread for an incoming reply by matching sender email or In-Reply-To header."""
    conn = get_db()

    # 1. Match by In-Reply-To message_id (most accurate)
    if in_reply_to:
        msg = conn.execute("""
            SELECT thread_id FROM messages WHERE message_id = ? LIMIT 1
        """, (in_reply_to,)).fetchone()
        if msg:
            conn.close()
            return msg['thread_id']
        # Also try matching tracking_id in emails_sent (our sent emails use tracking_id as message_id)
        clean_reply_to = in_reply_to.strip('<>').replace('@outreachos', '')
        sent = conn.execute("""
            SELECT es.contact_id, es.campaign_id, es.subject
            FROM emails_sent es
            WHERE (es.tracking_id = ? OR es.tracking_id = ?)
            AND es.status = 'sent'
            LIMIT 1
        """, (clean_reply_to, in_reply_to.strip('<>'))).fetchone()
        if sent:
            thread = conn.execute("""
                SELECT id FROM threads
                WHERE contact_id = ? AND campaign_id = ?
                ORDER BY last_message_at DESC LIMIT 1
            """, (sent['contact_id'], sent['campaign_id'])).fetchone()
            if thread:
                conn.close()
                return thread['id']

    # 2. Match by sender email → contact → most recent thread
    contact = conn.execute(
        "SELECT id FROM contacts WHERE email = ?", (sender_email.lower(),)
    ).fetchone()

    if contact:
        thread = conn.execute("""
            SELECT id FROM threads WHERE contact_id = ?
            ORDER BY last_message_at DESC LIMIT 1
        """, (contact['id'],)).fetchone()
        if thread:
            conn.close()
            return thread['id']
        # Contact exists but no thread — create one linked to contact
        thread_id = _insert_and_get_id(conn,
            "INSERT INTO threads (contact_id, campaign_id, subject, last_message_at, workspace_id) VALUES (?, ?, ?, ?, ?)",
            (contact['id'], None, subject, datetime.now(), 1)
        )
        conn.close()
        return thread_id

    # 3. Unknown sender — create orphan thread (will show in inbox as unknown)
    # Try to get workspace_id from settings
    try:
        from utils.db import get_setting as _gs
        wid = 1  # default workspace
    except Exception:
        wid = 1
    thread_id = _insert_and_get_id(conn,
        "INSERT INTO threads (contact_id, campaign_id, subject, last_message_at, workspace_id) VALUES (?, ?, ?, ?, ?)",
        (None, None, subject, datetime.now(), wid)
    )
    conn.close()
    return thread_id


def insert_message(thread_id, direction, sender_email, recipient_email,
                   subject, body, message_id=None, in_reply_to=None, ai_category=None):
    """Insert a message into a thread and update thread metadata."""
    conn = get_db()
    conn.execute("""
        INSERT INTO messages (thread_id, direction, sender_email, recipient_email,
                              subject, body, message_id, in_reply_to, ai_category)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (thread_id, direction, sender_email, recipient_email,
          subject, body, message_id, in_reply_to, ai_category))

    # Update thread last_message_at
    conn.execute("""
        UPDATE threads SET last_message_at = ?
        WHERE id = ?
    """, (datetime.now(), thread_id))

    # Increment unread count for incoming messages
    if direction == 'incoming':
        conn.execute("""
            UPDATE threads SET unread_count = unread_count + 1
            WHERE id = ?
        """, (thread_id,))

    conn.commit()
    conn.close()


def mark_thread_read(thread_id):
    """Mark all messages in thread as read."""
    conn = get_db()
    conn.execute("UPDATE threads SET unread_count = 0 WHERE id = ?", (thread_id,))
    conn.commit()
    conn.close()


def update_thread_status(thread_id, status):
    """Update thread status: active/interested/closed/booked/ignored."""
    conn = get_db()
    conn.execute("UPDATE threads SET status = ? WHERE id = ?", (status, thread_id))
    conn.commit()
    conn.close()


def get_thread_messages(thread_id):
    """Get all messages in a thread ordered by time."""
    conn = get_db()
    messages = conn.execute("""
        SELECT * FROM messages
        WHERE thread_id = ?
        ORDER BY created_at ASC
    """, (thread_id,)).fetchall()
    conn.close()
    return messages


def get_all_threads(status_filter=None):
    """Get all threads with contact info, ordered by last message."""
    conn = get_db()
    query = """
        SELECT t.*,
               c.name as contact_name,
               c.company as contact_company,
               c.email as contact_email,
               camp.name as campaign_name
        FROM threads t
        LEFT JOIN contacts c ON t.contact_id = c.id
        LEFT JOIN campaigns camp ON t.campaign_id = camp.id
    """
    if status_filter:
        query += " WHERE t.status = ?"
        threads = conn.execute(query + " ORDER BY t.last_message_at DESC", (status_filter,)).fetchall()
    else:
        threads = conn.execute(query + " ORDER BY t.last_message_at DESC").fetchall()
    conn.close()
    return threads


def categorize_reply_with_ai(body, subject=''):
    """Use AI to categorize an incoming reply."""
    try:
        import requests as http_requests
        from utils.db import get_setting
        prompt = f"""Classify this email reply into exactly ONE category.

Subject: {subject}
Body: {body[:500]}

Categories:
- interested: wants to learn more, positive response, asking questions
- not_interested: not interested, unsubscribe, stop emailing
- later: busy now, follow up later, not right time
- meeting: wants to schedule a call/meeting
- out_of_office: auto-reply, vacation, out of office
- spam: spam, irrelevant, wrong person

Reply with ONLY the category word, nothing else."""

        # Try Groq first
        groq_keys = get_setting('groq_api_keys') or ''
        keys = [k.strip() for k in groq_keys.split(',') if k.strip()]
        if keys:
            r = http_requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {keys[0]}', 'Content-Type': 'application/json'},
                json={'model': 'llama-3.3-70b-versatile',
                      'messages': [{'role': 'user', 'content': prompt}],
                      'max_tokens': 10},
                timeout=15
            )
            if r.status_code == 200:
                category = r.json()['choices'][0]['message']['content'].strip().lower()
                if category in AI_CATEGORIES:
                    return category

        # Try Gemini fallback
        gemini_key = get_setting('gemini_api_key') or ''
        if gemini_key:
            r = http_requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}',
                json={'contents': [{'parts': [{'text': prompt}]}]},
                timeout=15
            )
            if r.status_code == 200:
                category = r.json()['candidates'][0]['content']['parts'][0]['text'].strip().lower()
                if category in AI_CATEGORIES:
                    return category

    except Exception as e:
        error_logger.error(f'AI categorization failed: {str(e)}')

    return None


def generate_ai_reply_draft(thread_id, contact_name, company, context=''):
    """Generate an AI reply draft for a thread."""
    try:
        import requests as http_requests
        from utils.db import get_setting

        messages = get_thread_messages(thread_id)
        conversation = ''
        for msg in messages[-5:]:  # Last 5 messages for context
            direction = 'You' if msg['direction'] == 'outgoing' else contact_name
            conversation += f"\n{direction}: {msg['body'][:300]}\n"

        prompt = f"""You are writing a reply email on behalf of Anshul from Shiksha Infotech.

Contact: {contact_name} from {company}
{f'Context: {context}' if context else ''}

Recent conversation:
{conversation}

Write a short, professional reply (2-3 sentences max). 
- Be warm and direct
- Move conversation forward
- Output as plain text only, no HTML"""

        groq_keys = get_setting('groq_api_keys') or ''
        keys = [k.strip() for k in groq_keys.split(',') if k.strip()]
        if keys:
            r = http_requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {keys[0]}', 'Content-Type': 'application/json'},
                json={'model': 'llama-3.3-70b-versatile',
                      'messages': [{'role': 'user', 'content': prompt}],
                      'max_tokens': 200},
                timeout=20
            )
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content'].strip()

    except Exception as e:
        error_logger.error(f'AI reply draft failed: {str(e)}')

    return None
