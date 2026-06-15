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

    # Fields that moved from RuntimeConfig top-level to subtitle_settings
    _MIGRATED_FIELDS: dict[str, str] = {
        "sync_enabled": "auto_sync_timing",
        "auto_sync_after_download": "auto_sync_timing",
        "translation_enabled": "translation_enabled",
        "proactive_translation": "auto_translate_if_no_vi",
    }

    def load(self) -> RuntimeConfig:
        """Load config from JSON; seed from env if missing."""
        with self._lock:
            if not self.config_path.exists():
                runtime = self._from_env()
                self._write(runtime)
                return runtime

            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                migrated = self._migrate_settings(data)
                backfilled = self._backfill_provider_fields_from_env(data)
                runtime = RuntimeConfig(**data)
                if migrated or backfilled:
                    self._write(runtime)
                return runtime
            except (json.JSONDecodeError, ValidationError):
                # If corrupted, fall back to env seed to avoid crash
                runtime = self._from_env()
                self._write(runtime)
                return runtime

    def _migrate_settings(self, data: dict[str, Any]) -> bool:
        """Migrate old top-level feature toggles into subtitle_settings.

        Returns True if any migration was performed.
        """
        migrated = False
        sub_settings = data.setdefault("subtitle_settings", {})

        for old_key, new_key in self._MIGRATED_FIELDS.items():
            if old_key in data:
                # Only migrate if target not already set by user
                if new_key not in sub_settings:
                    sub_settings[new_key] = data[old_key]
                del data[old_key]
                migrated = True

        # Remove deprecated field from subtitle_settings
        if "auto_sync_after_download" in sub_settings:
            del sub_settings["auto_sync_after_download"]
            migrated = True

        return migrated

    def _backfill_provider_fields_from_env(self, data: dict[str, Any]) -> bool:
        """
        Backfill provider fields added after config.json already existed.

        ConfigStore seeds from env only on first run. Without this, adding
        OPENSUBTITLES_API_KEY or SUBDL_API_KEY to Docker/env after data/config.json
        exists will not enable those providers.
        """
        backfilled = False
        env_fields = [
            "opensubtitles_api_key",
            "opensubtitles_username",
            "opensubtitles_password",
            "opensubtitles_base_url",
            "subdl_api_key",
            "subdl_base_url",
        ]

        for field in env_fields:
            if data.get(field):
                continue
            value = getattr(settings, field, None)
            if value:
                data[field] = value
                backfilled = True

        return backfilled

    def _from_env(self) -> RuntimeConfig:
        """Seed RuntimeConfig from existing env-based settings for compatibility."""
        return RuntimeConfig(
            plex_url=getattr(settings, "plex_url", None),
            plex_token=getattr(settings, "plex_token", None),
            subsource_api_key=getattr(settings, "subsource_api_key", None),
            subsource_base_url=getattr(settings, "subsource_base_url", "https://api.subsource.net/api"),
            opensubtitles_api_key=getattr(settings, "opensubtitles_api_key", None),
            opensubtitles_username=getattr(settings, "opensubtitles_username", None),
            opensubtitles_password=getattr(settings, "opensubtitles_password", None),
            opensubtitles_base_url=getattr(settings, "opensubtitles_base_url", "https://api.opensubtitles.com/api/v1"),
            subdl_api_key=getattr(settings, "subdl_api_key", None),
            subdl_base_url=getattr(settings, "subdl_base_url", "https://api.subdl.com/api/v1"),
            openai_api_key=getattr(settings, "openai_api_key", None),
            openai_base_url=getattr(settings, "openai_base_url", "https://api.openai.com/v1"),
            openai_model=getattr(settings, "openai_model", "gpt-4o-mini"),
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
