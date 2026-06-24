import os
import logging
from logging.handlers import RotatingFileHandler

def _resolve_log_dir():
    for path in ['/home/logs', '/opt/render/project/src/logs']:
        try:
            os.makedirs(path, exist_ok=True)
            test = os.path.join(path, '.log_write_test')
            open(test, 'w').close()
            os.remove(test)
            return path
        except (OSError, PermissionError):
            pass
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
    os.makedirs(local, exist_ok=True)
    return local

LOG_DIR = _resolve_log_dir()

def _make_logger(name, filename, level=logging.INFO, fmt=None):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        try:
            handler = RotatingFileHandler(os.path.join(LOG_DIR, filename), maxBytes=5*1024*1024, backupCount=3)
            handler.setFormatter(logging.Formatter(fmt or '%(asctime)s [%(levelname)s] %(message)s'))
            logger.addHandler(handler)
        except Exception:
            logger.addHandler(logging.NullHandler())
    return logger

app_logger   = _make_logger('campaign', 'app.log')
smtp_logger  = _make_logger('smtp', 'smtp.log')
error_logger = _make_logger('errors', 'error.log', logging.ERROR,
                            '%(asctime)s [%(levelname)s] %(module)s:%(lineno)d - %(message)s')
