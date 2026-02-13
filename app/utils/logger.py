"""
Centralized logging configuration với structured logging.
"""

import logging
import sys
from typing import Any

from app.config import settings
from app.utils.log_buffer import MemoryLogHandler

# Global log buffer instance - shared with routes for SSE streaming
log_buffer = MemoryLogHandler(maxlen=2000)


class CustomFormatter(logging.Formatter):
    """Custom formatter với colors cho terminal output."""

    grey = "\x1b[38;21m"
    blue = "\x1b[38;5;39m"
    yellow = "\x1b[38;5;226m"
    red = "\x1b[38;5;196m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"

    FORMATS = {
        logging.DEBUG: grey + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.INFO: blue + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.WARNING: yellow + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.ERROR: red + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.CRITICAL: bold_red + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
    }

    def format(self, record: logging.LogRecord) -> str:
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


def setup_logging() -> None:
    """Configure application-wide logging."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler with custom formatter
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CustomFormatter())
    root_logger.addHandler(console_handler)

    # Memory buffer handler for web UI log viewer
    root_logger.addHandler(log_buffer)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a specific module."""
    return logging.getLogger(name)


# Context logger cho request tracing
class RequestContextLogger:
    """Logger wrapper với request context."""

    def __init__(self, logger: logging.Logger, request_id: str | None = None):
        self.logger = logger
        self.request_id = request_id

    def _format_message(self, msg: str, **kwargs: Any) -> str:
        """Format message with request ID and extra context."""
        prefix = f"[{self.request_id}] " if self.request_id else ""
        extra = " | ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        return f"{prefix}{msg}" + (f" | {extra}" if extra else "")

    def debug(self, msg: str, **kwargs: Any) -> None:
        self.logger.debug(self._format_message(msg, **kwargs))

    def info(self, msg: str, **kwargs: Any) -> None:
        self.logger.info(self._format_message(msg, **kwargs))

    def warning(self, msg: str, **kwargs: Any) -> None:
        self.logger.warning(self._format_message(msg, **kwargs))

    def error(self, msg: str, **kwargs: Any) -> None:
        self.logger.error(self._format_message(msg, **kwargs))

    def critical(self, msg: str, **kwargs: Any) -> None:
        self.logger.critical(self._format_message(msg, **kwargs))
