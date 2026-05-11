#!/bin/bash
# Azure App Service startup script

# Create persistent directories
mkdir -p /home/data
mkdir -p /home/logs
mkdir -p /home/uploads

# Start gunicorn
gunicorn --bind=0.0.0.0:8000 --timeout=600 --workers=2 --threads=4 --access-logfile=/home/logs/access.log app:app
