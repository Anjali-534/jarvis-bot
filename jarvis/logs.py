"""Logging setup: a rotating log file always, plus the console when one exists."""
import logging
import sys
from logging.handlers import RotatingFileHandler

import config

LOG_FILE_NAME = "jarvis.log"
MAX_BYTES = 1_000_000   # 1 MB per file
BACKUP_COUNT = 5        # jarvis.log + jarvis.log.1 ... jarvis.log.5
FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def log_file():
    return config.LOG_DIR / LOG_FILE_NAME


def setup_logging(console=None, level=logging.INFO):
    """Configure the root logger. console=None means: only if there is a real stdout (not pythonw)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_jarvis", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_file(), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    file_handler._jarvis = True
    root.addHandler(file_handler)

    if console is None:
        console = sys.stdout is not None
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter("%(message)s"))  # looks like the old print() output
        console_handler._jarvis = True
        root.addHandler(console_handler)

    for noisy in ("urllib3", "PIL", "comtypes", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return file_handler
