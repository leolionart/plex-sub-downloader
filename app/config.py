"""
Configuration module using Pydantic Settings.
Đọc cấu hình từ environment variables với validation tự động.
"""

from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Plex Configuration
    plex_url: str = Field(
        ...,
        description="Plex server URL (e.g., http://192.168.1.100:32400)",
        examples=["http://localhost:32400"]
    )
    plex_token: str = Field(
        ...,
        description="Plex authentication token",
        min_length=20
    )

    # Subsource Configuration
    subsource_api_key: str = Field(
        ...,
        description="Subsource API key from https://subsource.net/api-docs",
        min_length=10
    )
    subsource_base_url: str = Field(
        default="https://api.subsource.net/api",
        description="Subsource API base URL"
    )

    # Subtitle Preferences
    default_language: str = Field(
        default="vi",
        description="Default subtitle language (ISO 639-1 code)",
        pattern=r"^[a-z]{2}$"
    )
    subtitle_priority: list[str] = Field(
        default=["retail", "translated", "ai"],
        description="Subtitle quality priority (retail > translated > AI)"
    )

    # Webhook Security
    webhook_secret: str | None = Field(
        default=None,
        description="Optional secret for webhook authentication"
    )

    # Application Settings
    app_host: str = Field(default="0.0.0.0", description="FastAPI bind host")
    app_port: int = Field(default=9000, ge=1, le=65535, description="FastAPI bind port")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level"
    )

    # Retry Configuration
    max_retries: int = Field(default=3, ge=1, le=10, description="Max retries for API calls")
    retry_delay: int = Field(default=2, ge=1, description="Initial retry delay in seconds")

    # Temporary Storage
    temp_dir: str = Field(
        default="/tmp/plex-subtitles",
        description="Temporary directory for downloaded subtitles"
    )

    @field_validator("plex_url")
    @classmethod
    def validate_plex_url(cls, v: str) -> str:
        """Ensure Plex URL doesn't end with slash."""
        return v.rstrip("/")

    @field_validator("subsource_base_url")
    @classmethod
    def validate_subsource_url(cls, v: str) -> str:
        """Ensure Subsource URL doesn't end with slash."""
        return v.rstrip("/")


# Global settings instance
settings = Settings()
