"""
In-memory log buffer with pub/sub for SSE streaming.

Captures Python log records into a ring buffer and broadcasts
new entries to SSE subscribers via asyncio Queues.
"""

import asyncio
import logging
import traceback
from collections import deque
from datetime import datetime
from typing import Any


class LogEntry:
    """Structured log entry."""

    __slots__ = ("timestamp", "level", "source", "message")

    def __init__(self, timestamp: str, level: str, source: str, message: str):
        self.timestamp = timestamp
        self.level = level
        self.source = source
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "source": self.source,
            "message": self.message,
        }


class MemoryLogHandler(logging.Handler):
    """
    Custom logging handler that stores records in a ring buffer
    and pushes to SSE subscriber queues.
    """

    def __init__(self, maxlen: int = 2000) -> None:
        super().__init__()
        self._buffer: deque[LogEntry] = deque(maxlen=maxlen)
        self._subscribers: set[asyncio.Queue[LogEntry]] = set()

    def emit(self, record: logging.LogRecord) -> None:
        """Capture log record into buffer and notify subscribers."""
        try:
            # Build message including exception traceback when available
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                tb = "".join(traceback.format_exception(*record.exc_info))
                message = f"{message}\n{tb.rstrip()}"

            entry = LogEntry(
                timestamp=datetime.fromtimestamp(record.created).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                level=record.levelname,
                source=record.name,
                message=message,
            )
            self._buffer.append(entry)

            # Fan-out to all SSE subscribers (non-blocking)
            dead: list[asyncio.Queue[LogEntry]] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(entry)
                except asyncio.QueueFull:
                    # Drop entry for slow consumers
                    pass
                except Exception:
                    dead.append(queue)
            # Cleanup dead queues
            for q in dead:
                self._subscribers.discard(q)

        except Exception:
            self.handleError(record)

    def get_entries(
        self,
        limit: int = 200,
        level: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, str]]:
        """Get buffered log entries with optional filters."""
        level_threshold = getattr(logging, level.upper(), 0) if level else 0
        level_map = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

        entries = []
        for entry in self._buffer:
            entry_level = level_map.get(entry.level, 0)
            if entry_level < level_threshold:
                continue
            if search and search.lower() not in entry.message.lower():
                continue
            entries.append(entry.to_dict())

        # Return the most recent `limit` entries
        return entries[-limit:]

    def subscribe(self) -> asyncio.Queue[LogEntry]:
        """Create a new subscriber queue for SSE streaming."""
        queue: asyncio.Queue[LogEntry] = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[LogEntry]) -> None:
        """Remove a subscriber queue."""
        self._subscribers.discard(queue)

    def clear(self) -> None:
        """Clear the log buffer."""
        self._buffer.clear()

    @property
    def entry_count(self) -> int:
        return len(self._buffer)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
