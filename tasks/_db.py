"""
tasks/_db.py — Database helpers for Celery tasks
==================================================
Single source of truth: delegates to utils.db.
"""
from utils.db import get_db, get_setting, is_unsubscribed

__all__ = ['get_db', 'get_setting', 'is_unsubscribed']
