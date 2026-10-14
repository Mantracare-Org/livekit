"""Centralized logging configuration for the entire application."""

import logging
import sys
from typing import Optional

_FORMAT = "%(asctime)s INFO %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_root_configured = False


def configure_root_logger(level: int = logging.INFO) -> None:
    """Configure the root logger with a single stdout handler.

    Call this once at application startup (e.g., in ui_server.py lifespan or agent.py).
    """
    global _root_configured
    if _root_configured:
        return

    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root.addHandler(handler)

    # Silence noisy third-party loggers
    logging.getLogger("opentelemetry").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance that inherits from root configuration.

    Usage:
        from mantra.logging_config import get_logger
        logger = get_logger(__name__)
    """
    if not _root_configured:
        configure_root_logger()
    return logging.getLogger(name)


def set_level(level: int) -> None:
    """Set log level for root logger and all existing loggers."""
    root = logging.getLogger()
    root.setLevel(level)
    for logger_name in logging.Logger.manager.loggerDict:
        logging.getLogger(logger_name).setLevel(level)


def suppress_logger(name: str, level: int = logging.ERROR) -> None:
    """Suppress a specific noisy logger."""
    logging.getLogger(name).setLevel(level)