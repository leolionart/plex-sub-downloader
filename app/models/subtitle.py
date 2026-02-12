"""
Pydantic models cho subtitle search results.
"""

from typing import Literal
from pydantic import BaseModel, Field, HttpUrl


class SubtitleResult(BaseModel):
    """
    Kết quả search subtitle từ Subsource API.

    TODO: Cập nhật fields dựa trên actual response từ Subsource API
    Docs: https://subsource.net/api-docs
    """

    # Basic info
    id: str = Field(..., description="Subtitle ID")
    name: str = Field(..., description="Subtitle filename")
    language: str = Field(..., description="Language code (vi, en, etc.)")

    # Download
    download_url: HttpUrl | str = Field(..., description="URL để download subtitle")

    # Quality indicators
    release_info: str | None = Field(None, description="Release info (WEB-DL, BluRay, etc.)")
    uploader: str | None = Field(None, description="Uploader name")
    rating: float | None = Field(None, ge=0, le=10, description="Rating (0-10)")
    downloads: int | None = Field(None, ge=0, description="Download count")

    # Quality type (để priority ranking)
    quality_type: Literal["retail", "translated", "ai", "unknown"] = Field(
        default="unknown",
        description="Subtitle quality category"
    )

    # Metadata matching
    imdb_id: str | None = None
    tmdb_id: str | None = None
    season: int | None = None
    episode: int | None = None

    @property
    def priority_score(self) -> int:
        """
        Tính điểm ưu tiên để sort subtitles.
        Higher is better.
        """
        score = 0

        # Quality type priority
        quality_scores = {"retail": 1000, "translated": 500, "ai": 100, "unknown": 0}
        score += quality_scores.get(self.quality_type, 0)

        # Rating bonus
        if self.rating:
            score += int(self.rating * 10)

        # Download count bonus (logarithmic để tránh quá lệch)
        if self.downloads and self.downloads > 0:
            import math
            score += int(math.log10(self.downloads) * 20)

        return score

    def __lt__(self, other: "SubtitleResult") -> bool:
        """Sort by priority score (descending)."""
        return self.priority_score > other.priority_score


class SubtitleSearchParams(BaseModel):
    """Parameters for subtitle search."""

    language: str = Field(default="vi", description="Target language")
    title: str | None = None
    year: int | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None
    season: int | None = None
    episode: int | None = None

    @property
    def has_external_id(self) -> bool:
        """Check if we have IMDb or TMDb ID."""
        return bool(self.imdb_id or self.tmdb_id)

    def __str__(self) -> str:
        if self.season and self.episode:
            return f"{self.title} S{self.season:02d}E{self.episode:02d}"
        return f"{self.title} ({self.year})"
