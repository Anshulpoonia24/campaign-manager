"""
tasks/automation_tasks.py — Automation Rule Tasks
==================================================
Queue: automation_queue  (Priority 3 — HIGH)
Handles: follow-up rules, OOO retry, bounce pause, sequence progression
Beat: every 30 minutes
"""
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

QUEUE = 'automation_queue'


@shared_task(
    bind=True,
    name='tasks.automation_tasks.run_automation_rules_task',
    queue=QUEUE,
    max_retries=1,
    acks_late=True,
    priority=8,
)
def run_automation_rules_task(self):
    """
    Run all enabled automation rules. Runs every 30 minutes via Beat.
    Isolated from email sending and IMAP sync.
    """
    try:
        from services.automation_service import process_automation_rules
        stats = process_automation_rules()
        logger.info(f'Automation rules complete: {stats}')
        return {'success': True, 'stats': stats}
    except Exception as exc:
        logger.error(f'Automation rules error: {exc}')
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {'success': False, 'error': str(exc)}
