"""
services/copilot/handlers/inbox.py — Inbox Action Handlers
"""
from utils.db import get_db


def draft_reply(workspace_id: int, user_id: int, thread_id: int, **_) -> dict:
    from services.inbox_service import generate_ai_reply_draft
    conn = get_db()
    thread = conn.execute("""
        SELECT t.*, c.name as contact_name, c.company as contact_company,
               c.context as contact_context
        FROM threads t LEFT JOIN contacts c ON t.contact_id = c.id
        WHERE t.id=? AND t.workspace_id=?
    """, (thread_id, workspace_id)).fetchone()
    conn.close()
    if not thread:
        raise ValueError('Thread not found')
    draft = generate_ai_reply_draft(
        thread_id,
        thread['contact_name'] or 'there',
        thread['contact_company'] or '',
        thread['contact_context'] or ''
    )
    if draft:
        return {'message': 'Draft generated', 'draft': draft}
    raise ValueError('AI generation failed')


def send_reply(workspace_id: int, user_id: int, thread_id: int, body: str, **_) -> dict:
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from datetime import datetime

    if not body or not body.strip():
        raise ValueError('Empty reply body')

    conn = get_db()
    thread = conn.execute("""
        SELECT t.*, c.email as contact_email, c.name as contact_name
        FROM threads t LEFT JOIN contacts c ON t.contact_id = c.id
        WHERE t.id=? AND t.workspace_id=?
    """, (thread_id, workspace_id)).fetchone()
    if not thread:
        conn.close()
        raise ValueError('Thread not found')

    smtp_row = conn.execute(
        "SELECT * FROM smtp_accounts WHERE active=1 AND workspace_id=? ORDER BY health_score DESC LIMIT 1",
        (workspace_id,)
    ).fetchone()
    if not smtp_row:
        conn.close()
        raise ValueError('No active SMTP account')

    to_email = thread['contact_email']
    subject = thread['subject'] or '(no subject)'
    if not subject.lower().startswith('re:'):
        subject = 'Re: ' + subject

    smtp_keys = smtp_row.keys()
    from_name = smtp_row['from_name'] if 'from_name' in smtp_keys else ''
    smtp_email = smtp_row['email']
    login_user = smtp_row['login_username'] if 'login_username' in smtp_keys and smtp_row['login_username'] else smtp_email

    msg = MIMEMultipart('alternative')
    msg['From'] = f"{from_name} <{smtp_email}>" if from_name else smtp_email
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body.replace('\n', '<br>'), 'html'))

    server = smtplib.SMTP(smtp_row['smtp_server'], int(smtp_row['smtp_port']))
    server.starttls()
    server.login(login_user, smtp_row['password'])
    server.sendmail(smtp_email, to_email, msg.as_string())
    server.quit()

    conn.execute("""
        INSERT INTO messages (thread_id, direction, body, sender_email, created_at)
        VALUES (?, 'outgoing', ?, ?, CURRENT_TIMESTAMP)
    """, (thread_id, body, smtp_email))
    conn.execute("UPDATE threads SET last_message_at=CURRENT_TIMESTAMP WHERE id=?", (thread_id,))
    conn.commit()
    conn.close()
    return {'message': f'Reply sent to {to_email}'}


def mark_status(workspace_id: int, user_id: int, thread_id: int, status: str, **_) -> dict:
    conn = get_db()
    thread = conn.execute("SELECT id FROM threads WHERE id=? AND workspace_id=?",
                          (thread_id, workspace_id)).fetchone()
    if not thread:
        conn.close()
        raise ValueError('Thread not found')
    conn.execute("UPDATE threads SET status=? WHERE id=?", (status, thread_id))
    conn.commit()
    conn.close()
    return {'message': f'Thread marked as {status}'}


def summarize_thread(workspace_id: int, user_id: int, thread_id: int, **_) -> dict:
    conn = get_db()
    thread = conn.execute("""
        SELECT t.subject, c.name, c.company
        FROM threads t LEFT JOIN contacts c ON t.contact_id=c.id
        WHERE t.id=? AND t.workspace_id=?
    """, (thread_id, workspace_id)).fetchone()
    if not thread:
        conn.close()
        raise ValueError('Thread not found')

    msgs = conn.execute("""
        SELECT direction, body, ai_category, created_at
        FROM messages WHERE thread_id=? ORDER BY created_at
    """, (thread_id,)).fetchall()
    conn.close()

    summary_parts = []
    for m in msgs:
        direction = '→ Sent' if m['direction'] == 'outgoing' else '← Received'
        body_preview = (m['body'] or '')[:150]
        cat = f" [{m['ai_category']}]" if m['ai_category'] else ''
        summary_parts.append(f"{direction}{cat}: {body_preview}")

    return {
        'message': f"Thread with {thread['name']} ({thread['company']}): {thread['subject']}\n\n" +
                   '\n'.join(summary_parts[-5:])
    }
