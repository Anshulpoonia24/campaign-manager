"""
utils/backup.py — SQLite database backup
=========================================
- Uses SQLite .backup() API: safe hot-copy, no DB lock
- Guard: skips if last backup is less than 6 hours old
- Retention: keeps last 7 backups, deletes older ones
- Safe to call at app startup — Celery workers importing app.py
  will not create duplicate backups due to the time guard
"""
import os
import time
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger('campaign')

BACKUP_INTERVAL_SECONDS = 6 * 3600   # 6 hours
BACKUP_RETENTION_COUNT  = 7          # keep last 7 backups


def backup_db(db_path: str, backup_dir: str) -> str | None:
    """
    Create a timestamped backup of db_path into backup_dir.

    Returns:
        Path of backup file created, or None if skipped/failed.

    Guards:
        - Skips if db_path does not exist
        - Skips if last backup is less than BACKUP_INTERVAL_SECONDS old
        - Deletes oldest backups beyond BACKUP_RETENTION_COUNT
    """
    db_path = str(db_path)
    backup_dir = Path(backup_dir)

    # Guard: source DB must exist
    if not os.path.exists(db_path):
        logger.info(f'[BACKUP] Source DB not found: {db_path} — skipping')
        return None

    # Guard: create backup dir if needed
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f'[BACKUP] Cannot create backup dir {backup_dir}: {e}')
        return None

    # Guard: check last backup age
    existing = sorted(backup_dir.glob('campaigns_*.db'))
    if existing:
        latest_mtime = existing[-1].stat().st_mtime
        age_seconds = time.time() - latest_mtime
        if age_seconds < BACKUP_INTERVAL_SECONDS:
            logger.info(
                f'[BACKUP] Last backup {existing[-1].name} is '
                f'{int(age_seconds/3600)}h old — skipping'
            )
            return None

    # Create backup filename with timestamp
    ts = time.strftime('%Y%m%d_%H%M%S')
    backup_path = backup_dir / f'campaigns_{ts}.db'

    try:
        src  = sqlite3.connect(db_path)
        dest = sqlite3.connect(str(backup_path))
        src.backup(dest)          # SQLite hot-copy — safe while DB is in use
        dest.close()
        src.close()
        size_kb = backup_path.stat().st_size // 1024
        logger.info(f'[BACKUP] Created: {backup_path.name} ({size_kb} KB)')
    except Exception as e:
        logger.error(f'[BACKUP] Failed: {e}')
        # Remove partial backup file if it exists
        if backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                pass
        return None

    # Retention: delete oldest beyond BACKUP_RETENTION_COUNT
    all_backups = sorted(backup_dir.glob('campaigns_*.db'))
    to_delete = all_backups[:-BACKUP_RETENTION_COUNT]
    for old in to_delete:
        try:
            old.unlink()
            logger.info(f'[BACKUP] Deleted old backup: {old.name}')
        except OSError as e:
            logger.warning(f'[BACKUP] Could not delete {old.name}: {e}')

    return str(backup_path)
