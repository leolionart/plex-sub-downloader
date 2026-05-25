"""
Configuration module using Pydantic Settings.
Đọc cấu hình từ environment variables với validation tự động.
"""

from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Infrastructure-only Settings (env)
    app_host: str = Field(default="0.0.0.0", description="FastAPI bind host")
    app_port: int = Field(default=8000, ge=1, le=65535, description="FastAPI bind port")
    external_url: str | None = Field(default=None, description="External base URL for webhook (e.g. http://172.16.0.101:8765)")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level"
    )

    # Config file path
    config_file: str = Field(
        default="data/config.json",
        description="Path to runtime config JSON file",
    )

    # Retry Configuration (for API clients)
    max_retries: int = Field(default=3, ge=1, le=10, description="Max retries for API calls")
    retry_delay: int = Field(default=2, ge=1, description="Initial retry delay in seconds")

    # Runtime config seed values (used only when data/config.json does not exist)
    plex_url: str | None = None
    plex_token: str | None = None
    subsource_api_key: str | None = None
    subsource_base_url: str = "https://api.subsource.net/api"
    opensubtitles_api_key: str | None = None
    opensubtitles_username: str | None = None
    opensubtitles_password: str | None = None
    opensubtitles_base_url: str = "https://api.opensubtitles.com/api/v1"
    subdl_api_key: str | None = None
    subdl_base_url: str = "https://api.subdl.com/api/v1"
    default_language: str = "vi"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_secret: str | None = None
    cache_enabled: bool = True
    redis_url: str | None = None
    cache_ttl_seconds: int = 3600
    temp_dir: str = "/tmp/plex-subtitles"

    # Development/Testing
    mock_mode: bool = Field(
        default=False,
        description="Mock mode for testing UI without real Plex/API connections"
    )


# Global settings instance
settings = Settings()
