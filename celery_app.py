"""
celery_app.py — OutreachOS Isolated Queue Architecture
=======================================================
6 dedicated queues, each with independent workers and scaling.

Priority order (highest → lowest):
  1. imap_sync_queue      — reply detection, NEVER blocked
  2. send_email_queue     — campaign delivery
  3. automation_queue     — follow-up rules
  4. tracking_queue       — open/click events
  5. ai_generation_queue  — personalization (slow, expensive)
  6. enrichment_queue     — background research (lowest priority)
"""
import os
from celery import Celery
from celery.utils.log import get_task_logger
from kombu import Queue, Exchange
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# ── EXCHANGES ─────────────────────────────────────────────────
# Direct exchange per queue — no cross-routing
ex_email      = Exchange('send_email_queue',     type='direct')
ex_ai         = Exchange('ai_generation_queue',  type='direct')
ex_enrichment = Exchange('enrichment_queue',     type='direct')
ex_imap       = Exchange('imap_sync_queue',      type='direct')
ex_automation = Exchange('automation_queue',     type='direct')
ex_tracking   = Exchange('tracking_queue',       type='direct')

# ── CELERY APP ────────────────────────────────────────────────
celery = Celery(
    'outreachos',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        'tasks.email_tasks',
        'tasks.ai_tasks',
        'tasks.enrichment_tasks',
        'tasks.inbox_tasks',
        'tasks.automation_tasks',
        'tasks.tracking_tasks',
        'tasks.verification_tasks',
        'tasks.sequence_tasks',
    ]
)

celery.conf.update(
    # ── Serialization ──────────────────────────────────────────
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,

    # ── Reliability ────────────────────────────────────────────
    task_acks_late=True,               # Ack only after task completes
    task_reject_on_worker_lost=True,   # Re-queue if worker dies mid-task
    worker_prefetch_multiplier=1,      # Fair dispatch — one task at a time
    task_track_started=True,
    task_store_errors_even_if_ignored=True,

    # ── Results ────────────────────────────────────────────────
    result_expires=3600,               # 1 hour TTL on results

    # ── Default queue ──────────────────────────────────────────
    task_default_queue='send_email_queue',
    task_default_exchange='send_email_queue',
    task_default_routing_key='send_email_queue',

    # ── Queue definitions ──────────────────────────────────────
    task_queues=(
        # Priority 1 — IMAP sync (fastest, never blocked)
        Queue(
            'imap_sync_queue',
            exchange=ex_imap,
            routing_key='imap_sync_queue',
            queue_arguments={'x-max-priority': 10},
        ),
        # Priority 2 — Email sending
        Queue(
            'send_email_queue',
            exchange=ex_email,
            routing_key='send_email_queue',
            queue_arguments={'x-max-priority': 9},
        ),
        # Priority 3 — Automation rules
        Queue(
            'automation_queue',
            exchange=ex_automation,
            routing_key='automation_queue',
            queue_arguments={'x-max-priority': 8},
        ),
        # Priority 4 — Tracking events
        Queue(
            'tracking_queue',
            exchange=ex_tracking,
            routing_key='tracking_queue',
            queue_arguments={'x-max-priority': 6},
        ),
        # Priority 5 — AI generation (slow, expensive)
        Queue(
            'ai_generation_queue',
            exchange=ex_ai,
            routing_key='ai_generation_queue',
            queue_arguments={'x-max-priority': 4},
        ),
        # Priority 6 — Enrichment (background, lowest)
        Queue(
            'enrichment_queue',
            exchange=ex_enrichment,
            routing_key='enrichment_queue',
            queue_arguments={'x-max-priority': 2},
        ),
    ),

    # ── Task routing ───────────────────────────────────────────
    task_routes={
        # Email sending — highest operational priority
        'tasks.email_tasks.send_single_email':      {'queue': 'send_email_queue'},
        'tasks.email_tasks.send_campaign_async':    {'queue': 'send_email_queue'},
        'tasks.email_tasks.send_campaign_ai_async': {'queue': 'send_email_queue'},
        'tasks.email_tasks.execute_campaign_task':  {'queue': 'send_email_queue'},
        'tasks.email_tasks.daily_smtp_reset_task':  {'queue': 'send_email_queue'},

        # AI generation — medium priority, isolated from sending
        'tasks.ai_tasks.generate_ai_email_task':    {'queue': 'ai_generation_queue'},
        'tasks.ai_tasks.enrich_all_contacts':       {'queue': 'ai_generation_queue'},

        # Enrichment — background, lowest priority
        'tasks.enrichment_tasks.*':                 {'queue': 'enrichment_queue'},

        # IMAP sync — highest priority, never blocked
        'tasks.inbox_tasks.check_replies_task':     {'queue': 'imap_sync_queue'},

        # Automation rules
        'tasks.automation_tasks.*':                 {'queue': 'automation_queue'},
        # Sequence engine
        'tasks.sequence_tasks.*':                   {'queue': 'automation_queue'},

        # Tracking events
        'tasks.tracking_tasks.*':                   {'queue': 'tracking_queue'},

        # Verification — uses enrichment queue (low priority background)
        'tasks.verification_tasks.*':               {'queue': 'enrichment_queue'},
    },

    # ── Rate limits (protect SMTP + AI quotas) ─────────────────
    task_annotations={
        'tasks.email_tasks.send_single_email':         {'rate_limit': '12/m'},
        'tasks.ai_tasks.generate_ai_email_task':       {'rate_limit': '30/m'},
        'tasks.enrichment_tasks.enrich_single_contact':{'rate_limit': '20/m'},
    },

    # ── Beat schedule (replaces all daemon threads) ────────────
    beat_schedule={
        # IMAP: every 3 minutes
        'imap-sync-every-3min': {
            'task': 'tasks.inbox_tasks.check_replies_task',
            'schedule': 180.0,
            'options': {'queue': 'imap_sync_queue', 'priority': 10},
        },
        # Automation: every 30 minutes
        'automation-rules-every-30min': {
            'task': 'tasks.automation_tasks.run_automation_rules_task',
            'schedule': 1800.0,
            'options': {'queue': 'automation_queue', 'priority': 8},
        },
        # SMTP daily reset: every 24 hours
        'smtp-daily-reset': {
            'task': 'tasks.email_tasks.daily_smtp_reset_task',
            'schedule': 86400.0,
            'options': {'queue': 'send_email_queue', 'priority': 9},
        },
        # Sequence engine: every 15 minutes
        'sequence-processor-every-15min': {
            'task': 'tasks.sequence_tasks.process_sequences_task',
            'schedule': 900.0,
            'options': {'queue': 'automation_queue', 'priority': 8},
        },
    },
)


def is_redis_available():
    """Check Redis connectivity. Used for graceful fallback in app.py."""
    try:
        import redis as _redis
        r = _redis.from_url(REDIS_URL, socket_connect_timeout=2)
        r.ping()
        return True
    except Exception:
        return False


logger = get_task_logger(__name__)
