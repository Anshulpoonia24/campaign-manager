#!/bin/bash
# ============================================================
# OutreachOS — Isolated Queue Worker Startup
# ============================================================
# 6 dedicated queues, each with independent workers.
#
# Priority order:
#   1. imap_sync_queue      — reply detection (HIGHEST)
#   2. send_email_queue     — campaign delivery
#   3. automation_queue     — follow-up rules
#   4. tracking_queue       — open/click events
#   5. ai_generation_queue  — AI personalization
#   6. enrichment_queue     — background research (LOWEST)
#
# USAGE:
#   ./start_workers.sh              # Start all workers + beat
#   ./start_workers.sh imap         # IMAP worker only
#   ./start_workers.sh email        # Email worker only
#   ./start_workers.sh ai           # AI worker only
#   ./start_workers.sh enrichment   # Enrichment worker only
#   ./start_workers.sh beat         # Beat scheduler only
#   ./start_workers.sh monitor      # Flower monitoring UI
#
# REQUIREMENTS:
#   Redis running at $REDIS_URL (default: redis://localhost:6379/0)
#   pip install celery redis kombu flower
# ============================================================

set -e

QUEUE=${1:-"all"}
LOG_DIR=${LOG_DIR:-"/home/logs"}
mkdir -p "$LOG_DIR"

REDIS=${REDIS_URL:-"redis://localhost:6379/0"}
echo "============================================"
echo " OutreachOS Worker Startup"
echo " Redis: $REDIS"
echo " Queue: $QUEUE"
echo " Logs:  $LOG_DIR"
echo "============================================"

start_imap_worker() {
    echo "[WORKER] Starting imap_sync_queue worker (concurrency=1, priority=10)..."
    celery -A celery_app worker \
        --queues=imap_sync_queue \
        --concurrency=1 \
        --loglevel=info \
        --logfile="$LOG_DIR/worker_imap.log" \
        --hostname=imap@%h \
        --prefetch-multiplier=1 &
    echo "[WORKER] imap_sync_queue started (PID $!)"
}

start_email_worker() {
    echo "[WORKER] Starting send_email_queue worker (concurrency=2, priority=9)..."
    celery -A celery_app worker \
        --queues=send_email_queue \
        --concurrency=2 \
        --loglevel=info \
        --logfile="$LOG_DIR/worker_email.log" \
        --hostname=email@%h \
        --prefetch-multiplier=1 &
    echo "[WORKER] send_email_queue started (PID $!)"
}

start_automation_worker() {
    echo "[WORKER] Starting automation_queue worker (concurrency=1, priority=8)..."
    celery -A celery_app worker \
        --queues=automation_queue \
        --concurrency=1 \
        --loglevel=info \
        --logfile="$LOG_DIR/worker_automation.log" \
        --hostname=automation@%h \
        --prefetch-multiplier=1 &
    echo "[WORKER] automation_queue started (PID $!)"
}

start_tracking_worker() {
    echo "[WORKER] Starting tracking_queue worker (concurrency=2, priority=6)..."
    celery -A celery_app worker \
        --queues=tracking_queue \
        --concurrency=2 \
        --loglevel=info \
        --logfile="$LOG_DIR/worker_tracking.log" \
        --hostname=tracking@%h \
        --prefetch-multiplier=1 &
    echo "[WORKER] tracking_queue started (PID $!)"
}

start_ai_worker() {
    echo "[WORKER] Starting ai_generation_queue worker (concurrency=2, priority=4)..."
    celery -A celery_app worker \
        --queues=ai_generation_queue \
        --concurrency=2 \
        --loglevel=info \
        --logfile="$LOG_DIR/worker_ai.log" \
        --hostname=ai@%h \
        --prefetch-multiplier=1 &
    echo "[WORKER] ai_generation_queue started (PID $!)"
}

start_enrichment_worker() {
    echo "[WORKER] Starting enrichment_queue worker (concurrency=1, priority=2)..."
    celery -A celery_app worker \
        --queues=enrichment_queue \
        --concurrency=1 \
        --loglevel=info \
        --logfile="$LOG_DIR/worker_enrichment.log" \
        --hostname=enrichment@%h \
        --prefetch-multiplier=1 &
    echo "[WORKER] enrichment_queue started (PID $!)"
}

start_beat() {
    echo "[BEAT] Starting Celery Beat scheduler..."
    celery -A celery_app beat \
        --loglevel=info \
        --logfile="$LOG_DIR/beat.log" &
    echo "[BEAT] Scheduler started (PID $!)"
}

case "$QUEUE" in
    "imap")        start_imap_worker ;;
    "email")       start_email_worker ;;
    "automation")  start_automation_worker ;;
    "tracking")    start_tracking_worker ;;
    "ai")          start_ai_worker ;;
    "enrichment")  start_enrichment_worker ;;
    "beat")        start_beat ;;
    "monitor")
        echo "[MONITOR] Starting Flower on http://0.0.0.0:5555 ..."
        celery -A celery_app flower \
            --port=5555 \
            --broker="$REDIS" \
            --persistent=True \
            --db="$LOG_DIR/flower.db" &
        ;;
    "all"|*)
        start_imap_worker
        sleep 1
        start_email_worker
        sleep 1
        start_automation_worker
        sleep 1
        start_tracking_worker
        sleep 1
        start_ai_worker
        sleep 1
        start_enrichment_worker
        sleep 1
        start_beat
        ;;
esac

echo ""
echo "============================================"
echo " All workers started."
echo " Logs: $LOG_DIR/"
echo " Monitor: celery -A celery_app flower"
echo "============================================"

wait
