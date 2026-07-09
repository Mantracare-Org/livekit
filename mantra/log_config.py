import logging
import os
import sys

from pythonjsonlogger import jsonlogger


_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def setup_json_logger(name: str, level: str | None = None) -> logging.Logger:
    log_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()

    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    log_format = os.getenv("LOG_FORMAT", "json")
    if log_format == "json":
        formatter = jsonlogger.JsonFormatter(
            _LOG_FORMAT,
            timestamp=True,
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger