#!/bin/bash
# OutreachOS — Azure App Service startup script

echo "[STARTUP] Starting OutreachOS..."

# Create persistent directories
mkdir -p /home/data
mkdir -p /home/logs
mkdir -p /home/uploads

# Run data migration (only copies if production DB is empty)
python migrate_data.py

echo "[STARTUP] Starting gunicorn..."

# Start gunicorn
# --workers=1 to prevent duplicate IMAP checkers (background threads)
# --threads=4 for concurrent request handling
# --timeout=600 for long AI generation requests
gunicorn \
  --bind=0.0.0.0:8000 \
  --timeout=600 \
  --workers=1 \
  --threads=8 \
  --worker-class=gthread \
  --access-logfile=/home/logs/access.log \
  --error-logfile=/home/logs/error.log \
  --log-level=info \
  app:app
