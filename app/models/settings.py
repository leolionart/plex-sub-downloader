"""
Service settings model cho Web UI configuration.
"""

from typing import Literal
from pydantic import BaseModel, Field


class SubtitleSettings(BaseModel):
    """Settings cho subtitle download behavior."""

    # Language configuration
    languages: list[str] = Field(
        default=["vi"],
        description="Danh sách language codes để tải subtitle (ví dụ: ['vi', 'en'])"
    )
    language_priority: list[str] = Field(
        default=["vi"],
        description="Thứ tự ưu tiên language (download theo thứ tự này)"
    )

    # Download conditions
    auto_download_on_add: bool = Field(
        default=True,
        description="Tự động download khi thêm media mới"
    )
    auto_download_on_play: bool = Field(
        default=False,
        description="Tự động download khi user bắt đầu xem (nếu chưa có sub)"
    )

    # Duplicate prevention
    skip_if_has_subtitle: bool = Field(
        default=True,
        description="Bỏ qua nếu đã có subtitle cùng language"
    )
    replace_existing: bool = Field(
        default=False,
        description="Replace subtitle đã có bằng subtitle mới tìm được (nếu quality tốt hơn)"
    )
    replace_only_if_better_quality: bool = Field(
        default=True,
        description="Chỉ replace nếu subtitle mới có quality cao hơn"
    )

    # Quality preferences
    min_quality_threshold: Literal["any", "translated", "retail"] = Field(
        default="translated",
        description="Chỉ download subtitle từ quality tối thiểu này trở lên"
    )

    # Advanced filters
    skip_forced_subtitles: bool = Field(
        default=True,
        description="Không download nếu đã có forced subtitle"
    )
    skip_if_embedded: bool = Field(
        default=True,
        description="Không download nếu video có embedded subtitle"
    )

    # AI Sync Timing
    auto_sync_timing: bool = Field(
        default=True,
        description="Bật/tắt sync timing Vietsub theo Engsub chuẩn kèm phim"
    )
    auto_sync_after_download: bool = Field(
        default=True,
        description="Tự động sync timing sau khi download Vietsub (nếu có Engsub reference)"
    )

    # Translation
    translation_enabled: bool = Field(
        default=False,
        description="Bật/tắt dịch subtitle (EN → VI) khi không tìm thấy Vietsub"
    )
    translation_requires_approval: bool = Field(
        default=True,
        description="Yêu cầu duyệt trước khi dịch tự động"
    )
    auto_translate_if_no_vi: bool = Field(
        default=False,
        description="Chủ động dịch Eng→Viet khi có Engsub nhưng không có Vietsub"
    )

    @property
    def primary_language(self) -> str:
        """Get primary language (first in priority list)."""
        return self.language_priority[0] if self.language_priority else "vi"

    def should_download_on_event(self, event: str) -> bool:
        """Check xem có nên download cho event này không."""
        if event == "library.new" and self.auto_download_on_add:
            return True
        if event == "media.play" and self.auto_download_on_play:
            return True
        return False


class ServiceConfig(BaseModel):
    """Runtime configuration cho service."""

    subtitle_settings: SubtitleSettings = Field(default_factory=SubtitleSettings)

    # Stats tracking
    total_downloads: int = Field(default=0, description="Tổng số subtitle đã download")
    total_skipped: int = Field(default=0, description="Tổng số lần skip")
    last_download: str | None = Field(default=None, description="Timestamp của download cuối")

    def increment_downloads(self) -> None:
        """Increment download counter."""
        self.total_downloads += 1

    def increment_skipped(self) -> None:
        """Increment skip counter."""
        self.total_skipped += 1
