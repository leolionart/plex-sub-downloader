"""
Service settings model cho Web UI configuration.
"""

from typing import Literal
from pydantic import BaseModel, Field


DEFAULT_TRANSLATION_SYSTEM_PROMPT_TEMPLATE = (
    "You are a professional subtitle translator from {from_lang} to {to_lang}.\n\n"
    "STRICT RULES:\n"
    "1. Output ONLY the {to_lang} translation — NEVER include the original {from_lang} text\n"
    "2. Return exactly {count} numbered translations matching input order\n"
    "3. Keep translations concise and natural for subtitle display\n"
    "4. Preserve line breaks within each entry (use the same number of lines)\n"
    "5. Do NOT merge, combine, or skip any entries\n"
    "6. Do NOT add explanations, notes, or extra text\n\n"
    "Response format (one translation per number):\n"
    "[1] translated text\n"
    "[2] translated text"
)

DEFAULT_SYNC_SYSTEM_PROMPT_TEMPLATE = (
    "You are a subtitle alignment tool. Match Vietnamese subtitle entries "
    "to their corresponding English subtitle entries based on meaning/content.\n\n"
    "Rules:\n"
    "- Each Vietnamese entry should match exactly one English entry\n"
    "- Match by semantic meaning, not by position\n"
    "- If no good match exists, skip that entry\n"
    "- Return ONLY a JSON array of matches\n"
    "- Format: [{\"vi\": <VI-index>, \"en\": <EN-index>}, ...]\n"
    "- Use the exact index numbers shown in brackets"
)


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
        description="Tự động sync timing sau khi download subtitle"
    )

    # Translation
    translation_enabled: bool = Field(
        default=False,
        description="Bật/tắt dịch subtitle (EN → VI) khi không tìm thấy Vietsub"
    )
    auto_translate_if_no_vi: bool = Field(
        default=False,
        description="Chủ động dịch Eng→Viet khi có Engsub nhưng không có Vietsub"
    )
    translation_batch_concurrency: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Số batch dịch song song tối đa (1=tuần tự, cao hơn=nhanh hơn nhưng tốn rate limit)",
    )
    translation_system_prompt_template: str = Field(
        default=DEFAULT_TRANSLATION_SYSTEM_PROMPT_TEMPLATE,
        description="System prompt template cho AI dịch subtitle. Hỗ trợ placeholders: {from_lang}, {to_lang}, {count}",
    )
    sync_system_prompt_template: str = Field(
        default=DEFAULT_SYNC_SYSTEM_PROMPT_TEMPLATE,
        description="System prompt template cho AI sync timing",
    )

    # Webhook processing delay
    new_media_delay_seconds: int = Field(
        default=30,
        ge=0,
        le=300,
        description="Seconds to wait after library.new event before processing (allows Plex to finish indexing metadata)",
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
