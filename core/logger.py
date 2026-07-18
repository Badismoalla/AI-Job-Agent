"""
core/logger.py
--------------
Centralised logging system using loguru.

Why loguru instead of stdlib logging?
- Zero boilerplate (no handlers, formatters, propagation config)
- Structured JSON output for log files (machine-parseable)
- Colourised human-readable output for terminal
- Built-in rotation and retention
- Context binding (attach job_id, module, action to log records)

Usage anywhere in the codebase:
    from core.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Scraping {board}", board="Pracuj.pl")
    logger.bind(job_id="j-001").info("Application sent")
"""

import sys
from pathlib import Path

from loguru import logger as _loguru_logger


def setup_logging(log_level: str = "INFO", log_dir: Path | None = None) -> None:
    """
    Configure loguru with two sinks:
    1. stderr  — human-readable, colourised, for terminal use
    2. log file — JSON structured, rotated daily, retained 30 days

    Call once at application startup (main.py).
    """
    # Remove default loguru handler
    _loguru_logger.remove()

    # --- Terminal sink (human-readable) ---
    _loguru_logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    # --- File sink (JSON structured) ---
    if log_dir is None:
        log_dir = Path(__file__).parent.parent / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)

    _loguru_logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
        rotation="00:00",        # New file every midnight
        retention="30 days",     # Keep 30 days of logs
        compression="gz",        # Compress old logs
        backtrace=True,
        diagnose=True,
        enqueue=True,            # Async-safe logging
    )

    # --- Separate error log for quick triage ---
    _loguru_logger.add(
        log_dir / "errors_{time:YYYY-MM-DD}.log",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
        rotation="00:00",
        retention="60 days",
        compression="gz",
        backtrace=True,
        diagnose=True,
        enqueue=True,
    )

    _loguru_logger.info(
        "Logging initialised | level={level} | log_dir={log_dir}",
        level=log_level,
        log_dir=str(log_dir),
    )


def get_logger(name: str):
    """
    Return a loguru logger bound to a module name.

    Usage:
        logger = get_logger(__name__)
        logger.info("Starting scraper")
    """
    return _loguru_logger.bind(module=name)
