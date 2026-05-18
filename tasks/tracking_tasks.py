"""
tasks/tracking_tasks.py — Async Tracking Event Tasks
=====================================================
Queue: tracking_queue  (Priority 4 — MEDIUM)
Handles: async event processing, score aggregation, analytics recalc
"""
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

QUEUE = 'tracking_queue'


@shared_task(
    name='tasks.tracking_tasks.process_open_event',
    queue=QUEUE, acks_late=True, priority=6,
)
def process_open_event(token: str, ip: str, user_agent: str):
    """Async open event processing."""
    try:
        from services.tracking import process_open
        result = process_open(token, ip, user_agent)
        logger.info(f'Open processed: token={token[:20]} bot_filtered={not result}')
        return {'success': True, 'logged': result}
    except Exception as exc:
        logger.error(f'process_open_event error: {exc}')
        return {'success': False, 'error': str(exc)}


@shared_task(
    name='tasks.tracking_tasks.process_click_event',
    queue=QUEUE, acks_late=True, priority=6,
)
def process_click_event(click_token: str, original_url: str,
                        tracking_id: str, ip: str, user_agent: str):
    """Async click event processing."""
    try:
        from services.tracking import process_click
        redirect_url = process_click(click_token, original_url, tracking_id, ip, user_agent)
        logger.info(f'Click processed: url={original_url[:60]}')
        return {'success': True, 'redirect_url': redirect_url}
    except Exception as exc:
        logger.error(f'process_click_event error: {exc}')
        return {'success': False, 'error': str(exc)}


@shared_task(
    name='tasks.tracking_tasks.log_tracking_event',
    queue=QUEUE, acks_late=True, priority=5,
)
def log_tracking_event(event_type: str, workspace_id: int, contact_id: int = None,
                       campaign_id: int = None, thread_id: int = None,
                       email_sent_id: int = None, metadata: dict = None):
    """Generic async event logger — called from email sending tasks."""
    try:
        from services.tracking import log_event
        event_id = log_event(
            event_type=event_type,
            workspace_id=workspace_id,
            contact_id=contact_id,
            campaign_id=campaign_id,
            thread_id=thread_id,
            email_sent_id=email_sent_id,
            metadata=metadata or {},
        )
        return {'success': True, 'event_id': event_id}
    except Exception as exc:
        logger.error(f'log_tracking_event error: {exc}')
        return {'success': False, 'error': str(exc)}


@shared_task(
    name='tasks.tracking_tasks.recalculate_workspace_scores',
    queue=QUEUE, acks_late=True, priority=3,
)
def recalculate_workspace_scores(workspace_id: int):
    """
    Recalculate lead scores for all contacts in a workspace
    based on tracking_events history.
    """
    from tasks._db import get_db
    from services.tracking import SCORE_WEIGHTS
    conn = get_db()
    try:
        # Get all contacts in workspace
        contacts = conn.execute(
            "SELECT id FROM contacts WHERE workspace_id=?", (workspace_id,)
        ).fetchall()

        updated = 0
        for c in contacts:
            cid = c['id']
            # Sum all event scores for this contact
            events = conn.execute("""
                SELECT event_type, COUNT(*) as cnt
                FROM tracking_events
                WHERE contact_id=? AND workspace_id=?
                GROUP BY event_type
            """, (cid, workspace_id)).fetchall()

            total_score = 0
            for e in events:
                weight = SCORE_WEIGHTS.get(e['event_type'], 0)
                total_score += weight * e['cnt']

            total_score = max(0, min(500, total_score))
            conn.execute(
                "UPDATE contacts SET lead_score=? WHERE id=?",
                (total_score, cid)
            )
            updated += 1

        conn.commit()
        logger.info(f'Recalculated scores for {updated} contacts in workspace {workspace_id}')
        return {'success': True, 'updated': updated}
    except Exception as exc:
        logger.error(f'recalculate_workspace_scores error: {exc}')
        return {'success': False, 'error': str(exc)}
    finally:
        conn.close()
