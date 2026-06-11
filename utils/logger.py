"""
FundAiBot — Centralised logging.
Call get_logger(__name__) in every module.

On Railway (production): console-only logging (stdout is captured by Railway's
log aggregator — no file needed and the filesystem is ephemeral).

In development: console + rotating file in logs/ directory.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

# Check for Railway environment to decide logging mode
_IS_RAILWAY = bool(
    os.getenv("RAILWAY_ENVIRONMENT") or
    os.getenv("RAILWAY_SERVICE_NAME") or
    os.getenv("RAILWAY_PROJECT_ID") or
    os.getenv("RAILWAY_SERVICE_ID")
)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
_LOG_FILE = os.path.join(LOGS_DIR, "fundaibot.log")
_FMT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    # Console handler — always active, primary output on Railway
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler — development only (Railway filesystem is ephemeral)
    if not _IS_RAILWAY:
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            fh = RotatingFileHandler(
                _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            pass  # Log directory not writable — console only

    return logger
