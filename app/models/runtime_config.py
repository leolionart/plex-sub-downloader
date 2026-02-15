"""
Runtime configuration model persisted in JSON store.
Represents dynamic secrets and feature flags managed via setup UI.
"""

from typing import Literal
from pydantic import BaseModel, Field, field_validator

from app.models.settings import SubtitleSettings


class RuntimeConfig(BaseModel):
    """Dynamic runtime configuration loaded from JSON store."""

    plex_url: str | None = Field(default=None, description="Plex server URL")
    plex_token: str | None = Field(default=None, description="Plex authentication token")

    subsource_api_key: str | None = Field(default=None, description="Subsource API key")
    subsource_base_url: str = Field(
        default="https://api.subsource.net/api",
        description="Subsource API base URL",
    )

    default_language: str = Field(
        default="vi",
        description="Default subtitle language (ISO 639-1)",
        pattern=r"^[a-z]{2}$",
    )

    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI-compatible base URL",
    )
    openai_model: str = Field(default="gpt-4o-mini", description="OpenAI model name")

    translation_enabled: bool = Field(default=False, description="Enable translation fallback")
    translation_requires_approval: bool = Field(
        default=True,
        description="Require approval before translating",
    )
    proactive_translation: bool = Field(
        default=False,
        description="Chủ động dịch sub khi có Engsub nhưng không có Vietsub (không chỉ fallback)",
    )

    sync_enabled: bool = Field(default=False, description="Enable AI subtitle timing sync")
    auto_sync_after_download: bool = Field(
        default=True,
        description="Tự động sync timing sau khi download Vietsub (nếu có Engsub reference)",
    )

    telegram_bot_token: str | None = Field(default=None, description="Telegram bot token")
    telegram_chat_id: str | None = Field(default=None, description="Telegram chat ID")

    webhook_secret: str | None = Field(default=None, description="Optional webhook secret header")

    cache_enabled: bool = Field(default=True, description="Enable caching")
    redis_url: str | None = Field(default=None, description="Redis connection URL")
    cache_ttl_seconds: int = Field(default=3600, ge=60, description="Cache TTL seconds")

    temp_dir: str = Field(default="/tmp/plex-subtitles", description="Temp directory for subtitles")

    subtitle_settings: SubtitleSettings = Field(
        default_factory=SubtitleSettings,
        description="User-facing subtitle behavior settings",
    )

    @field_validator("plex_url", mode="before")
    @classmethod
    def strip_trailing_slash(cls, v: str | None) -> str | None:
        if v:
            return v.rstrip("/")
        return v

    @field_validator("subsource_base_url", mode="before")
    @classmethod
    def strip_subsource_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if v else v

    @property
    def ai_available(self) -> bool:
        """Check if AI features (translation/sync) are available."""
        return bool(self.openai_api_key)

    def sanitized(self) -> "RuntimeConfig":
        """Return a copy with secrets masked for UI responses."""
        return self.model_copy(update={
            "plex_token": "***" if self.plex_token else None,
            "subsource_api_key": "***" if self.subsource_api_key else None,
            "openai_api_key": "***" if self.openai_api_key else None,
            "telegram_bot_token": "***" if self.telegram_bot_token else None,
            "webhook_secret": "***" if self.webhook_secret else None,
        })
