"""
ConfigStore: lưu và tải RuntimeConfig từ JSON file.
Seed từ environment (.env) cho backward compatibility nếu JSON chưa tồn tại.
"""

import json
from pathlib import Path
from typing import Any
from threading import RLock

from pydantic import ValidationError

from app.config import settings
from app.models.runtime_config import RuntimeConfig


class ConfigStore:
    """Thread-safe JSON config store for RuntimeConfig."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path or Path("data") / "config.json")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def load(self) -> RuntimeConfig:
        """Load config from JSON; seed from env if missing."""
        with self._lock:
            if not self.config_path.exists():
                runtime = self._from_env()
                self._write(runtime)
                return runtime

            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                return RuntimeConfig(**data)
            except (json.JSONDecodeError, ValidationError):
                # If corrupted, fall back to env seed to avoid crash
                runtime = self._from_env()
                self._write(runtime)
                return runtime

    def _from_env(self) -> RuntimeConfig:
        """Seed RuntimeConfig from existing env-based settings for compatibility."""
        return RuntimeConfig(
            plex_url=getattr(settings, "plex_url", None),
            plex_token=getattr(settings, "plex_token", None),
            subsource_api_key=getattr(settings, "subsource_api_key", None),
            subsource_base_url=getattr(settings, "subsource_base_url", "https://api.subsource.net/api"),
            openai_api_key=getattr(settings, "openai_api_key", None),
            openai_base_url=getattr(settings, "openai_base_url", "https://api.openai.com/v1"),
            openai_model=getattr(settings, "openai_model", "gpt-4o-mini"),
            translation_enabled=getattr(settings, "translation_enabled", False),
            translation_requires_approval=getattr(settings, "translation_requires_approval", True),
            telegram_bot_token=getattr(settings, "telegram_bot_token", None),
            telegram_chat_id=getattr(settings, "telegram_chat_id", None),
            webhook_secret=getattr(settings, "webhook_secret", None),
            cache_enabled=getattr(settings, "cache_enabled", True),
            redis_url=getattr(settings, "redis_url", None),
            cache_ttl_seconds=getattr(settings, "cache_ttl_seconds", 3600),
            temp_dir=getattr(settings, "temp_dir", "/tmp/plex-subtitles"),
            default_language=getattr(settings, "default_language", "vi"),
        )

    def save(self, runtime_config: RuntimeConfig) -> None:
        """Persist provided RuntimeConfig to disk."""
        with self._lock:
            self._write(runtime_config)

    def update(self, **partial: Any) -> RuntimeConfig:
        """Apply partial update, persist, and return updated config."""
        with self._lock:
            current = self.load()
            updated = current.model_copy(update=partial)
            self._write(updated)
            return updated

    def _write(self, runtime_config: RuntimeConfig) -> None:
        self.config_path.write_text(runtime_config.model_dump_json(indent=2), encoding="utf-8")
