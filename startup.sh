#!/bin/bash
# OutreachOS — Startup script (Render + Azure compatible)

echo "[STARTUP] Starting OutreachOS..."

# Create data directories — Render uses /opt/render/project/src/data
# Azure uses /home/data — app.py _setup_paths() handles this automatically
mkdir -p /opt/render/project/src/data 2>/dev/null || true
mkdir -p /opt/render/project/src/logs 2>/dev/null || true
mkdir -p /opt/render/project/src/uploads 2>/dev/null || true
mkdir -p /home/data 2>/dev/null || true
mkdir -p /home/logs 2>/dev/null || true
mkdir -p /home/uploads 2>/dev/null || true

echo "[STARTUP] Starting gunicorn..."

# PORT is set by Render automatically
# workers=1 prevents duplicate IMAP checkers (background threads)
# threads=8 for concurrent requests
gunicorn \
  --bind 0.0.0.0:${PORT:-8000} \
  --timeout 600 \
  --workers 1 \
  --threads 8 \
  --worker-class gthread \
  --log-level info \
  app:app
