import os
import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

def _make_logger(name, filename, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = RotatingFileHandler(os.path.join(LOG_DIR, filename), maxBytes=5*1024*1024, backupCount=3)
        handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(handler)
    return logger

app_logger = _make_logger('campaign', 'app.log')
smtp_logger = _make_logger('smtp', 'smtp.log')
error_logger = _make_logger('errors', 'error.log', logging.ERROR)
error_logger.handlers[0].setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(module)s:%(lineno)d - %(message)s'))
