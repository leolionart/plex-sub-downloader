"""
StatsStore: persistent JSON-backed statistics tracking.
Lưu các thống kê tổng hợp (downloads, translations, syncs) tồn tại qua restart.
"""

import json
from pathlib import Path
from threading import RLock
from typing import Any

from app.utils.logger import get_logger

logger = get_logger(__name__)


class StatsStore:
    """Thread-safe JSON-backed statistics store."""

    _DEFAULTS: dict[str, int] = {
        "total_downloads": 0,
        "total_skipped": 0,
        "total_translations": 0,
        "total_translation_lines": 0,
        "total_syncs": 0,
    }

    def __init__(self, stats_path: str | Path | None = None) -> None:
        self._path = Path(stats_path or Path("data") / "stats.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._data: dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        """Load stats from JSON file, seeding defaults for missing keys."""
        data = dict(self._DEFAULTS)
        try:
            if self._path.exists():
                saved = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    data.update(saved)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load stats, using defaults: {e}")
        return data

    def _save(self) -> None:
        """Persist current stats to disk."""
        try:
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error(f"Failed to save stats: {e}")

    def increment(self, key: str, amount: int = 1) -> int:
        """Increment a stat counter and persist. Returns new value."""
        with self._lock:
            self._data[key] = self._data.get(key, 0) + amount
            self._save()
            return self._data[key]

    def get(self, key: str) -> int:
        """Get a stat value."""
        with self._lock:
            return self._data.get(key, 0)

    def get_all(self) -> dict[str, Any]:
        """Get all stats as a dict."""
        with self._lock:
            data = dict(self._data)
        # Computed fields
        total = data["total_downloads"] + data["total_skipped"]
        data["success_rate"] = (
            round(data["total_downloads"] / total * 100) if total > 0 else 0
        )
        return data
