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

    # Development/Testing
    mock_mode: bool = Field(
        default=False,
        description="Mock mode for testing UI without real Plex/API connections"
    )


# Global settings instance
settings = Settings()
