"""
Pydantic models cho webhook payloads từ Plex/Tautulli.
"""

from typing import Literal
from pydantic import BaseModel, Field


class PlexWebhookPayload(BaseModel):
    """
    Webhook payload từ Plex Media Server.
    Docs: https://support.plex.tv/articles/115002267687-webhooks/
    """

    event: str = Field(..., description="Event type (e.g., library.new, media.play)")
    user: bool = Field(default=True)
    owner: bool = Field(default=True)
    Account: dict | None = None
    Server: dict | None = None
    Player: dict | None = None
    Metadata: dict = Field(..., description="Media metadata")

    @property
    def rating_key(self) -> str | None:
        """Extract ratingKey từ Metadata."""
        return self.Metadata.get("ratingKey")

    @property
    def media_type(self) -> str | None:
        """Extract type từ Metadata (movie, episode, etc.)."""
        return self.Metadata.get("type")


class TautulliWebhookPayload(BaseModel):
    """
    Webhook payload từ Tautulli.
    Flexible structure để hỗ trợ nhiều event types.
    """

    event: str = Field(..., description="Event type")
    rating_key: str | None = Field(None, alias="ratingKey")
    media_type: str | None = None
    # Tautulli gửi rất nhiều fields, ta chỉ lấy những gì cần
    title: str | None = None
    year: str | None = None

    class Config:
        populate_by_name = True  # Allow both snake_case and camelCase


class MediaMetadata(BaseModel):
    """
    Normalized metadata cho movie hoặc TV episode.
    Chuẩn hóa dữ liệu từ PlexAPI để dễ xử lý.
    """

    rating_key: str = Field(..., description="Plex ratingKey")
    media_type: Literal["movie", "episode"] = Field(..., description="Type of media")

    # Common fields
    title: str = Field(..., description="Title của movie/episode")
    year: int | None = Field(None, description="Release year")

    # External IDs (ưu tiên cho search)
    imdb_id: str | None = Field(None, description="IMDb ID (tt1234567)")
    tmdb_id: str | None = Field(None, description="TMDb ID")

    # TV Episode specific
    show_title: str | None = Field(None, description="Show title (nếu là episode)")
    season_number: int | None = Field(None, description="Season number")
    episode_number: int | None = Field(None, description="Episode number")

    # Language của subtitles hiện có
    existing_subtitle_languages: list[str] = Field(
        default_factory=list,
        description="Danh sách language codes của subs đã có"
    )

    @property
    def is_movie(self) -> bool:
        return self.media_type == "movie"

    @property
    def is_episode(self) -> bool:
        return self.media_type == "episode"

    @property
    def search_title(self) -> str:
        """Title để dùng cho search - show title nếu là episode."""
        return self.show_title if self.is_episode else self.title

    def __str__(self) -> str:
        if self.is_movie:
            return f"{self.title} ({self.year})"
        else:
            return f"{self.show_title} S{self.season_number:02d}E{self.episode_number:02d}"
