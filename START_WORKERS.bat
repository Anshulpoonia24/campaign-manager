@echo off
REM ============================================================
REM  OutreachOS — Isolated Queue Worker Startup (Windows)
REM ============================================================
REM  Requires Redis: docker run -d -p 6379:6379 redis:alpine
REM
REM  Queue priority:
REM    1. imap_sync_queue      (HIGHEST - reply detection)
REM    2. send_email_queue     (campaign delivery)
REM    3. automation_queue     (follow-up rules)
REM    4. tracking_queue       (open/click events)
REM    5. ai_generation_queue  (AI personalization)
REM    6. enrichment_queue     (LOWEST - background research)
REM ============================================================

echo ============================================
echo  OutreachOS Worker Startup (Windows)
echo  Redis: %REDIS_URL%
echo ============================================

REM 1. IMAP sync — highest priority, dedicated window
start "IMAP Worker [P1]" cmd /k "celery -A celery_app worker --queues=imap_sync_queue --concurrency=1 --loglevel=info --hostname=imap@%%h --prefetch-multiplier=1"

timeout /t 2 /nobreak >nul

REM 2. Email sending — high priority
start "Email Worker [P2]" cmd /k "celery -A celery_app worker --queues=send_email_queue --concurrency=2 --loglevel=info --hostname=email@%%h --prefetch-multiplier=1"

timeout /t 2 /nobreak >nul

REM 3. Automation rules
start "Automation Worker [P3]" cmd /k "celery -A celery_app worker --queues=automation_queue --concurrency=1 --loglevel=info --hostname=automation@%%h --prefetch-multiplier=1"

timeout /t 2 /nobreak >nul

REM 4. Tracking events
start "Tracking Worker [P4]" cmd /k "celery -A celery_app worker --queues=tracking_queue --concurrency=2 --loglevel=info --hostname=tracking@%%h --prefetch-multiplier=1"

timeout /t 2 /nobreak >nul

REM 5. AI generation — medium priority
start "AI Worker [P5]" cmd /k "celery -A celery_app worker --queues=ai_generation_queue --concurrency=2 --loglevel=info --hostname=ai@%%h --prefetch-multiplier=1"

timeout /t 2 /nobreak >nul

REM 6. Enrichment — lowest priority background
start "Enrichment Worker [P6]" cmd /k "celery -A celery_app worker --queues=enrichment_queue --concurrency=1 --loglevel=info --hostname=enrichment@%%h --prefetch-multiplier=1"

timeout /t 2 /nobreak >nul

REM Beat scheduler (replaces all daemon threads)
start "Beat Scheduler" cmd /k "celery -A celery_app beat --loglevel=info"

echo.
echo ============================================
echo  All 6 workers + beat scheduler started.
echo  Each queue runs in its own window.
echo.
echo  Monitor: celery -A celery_app flower
echo ============================================
pause
