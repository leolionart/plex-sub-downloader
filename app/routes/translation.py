"""
Translation routes cho Web UI.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.webhook import MediaMetadata

router = APIRouter(prefix="/api/translation", tags=["translation"])


class TranslationRequest(BaseModel):
    """Request để execute manual translation."""
    rating_key: str
    from_lang: str = "en"


class ImproveRequest(BaseModel):
    """Request để execute subtitle improve."""
    rating_key: str


def get_subtitle_service():
    """Get subtitle service instance từ main app."""
    from app.main import subtitle_service
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return subtitle_service


@router.post("/execute")
async def execute_translation(request: TranslationRequest):
    """
    Execute manual translation cho một media item.
    """
    service = get_subtitle_service()

    if not service.runtime_config.ai_available:
        raise HTTPException(status_code=400, detail="OpenAI API key required for translation")

    result = await service.execute_translate_for_media(
        rating_key=request.rating_key,
        from_lang=request.from_lang,
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return result


@router.post("/improve")
async def execute_improve(request: ImproveRequest):
    """
    Execute subtitle improve cho một media item.
    """
    service = get_subtitle_service()

    if not service.runtime_config.ai_available:
        raise HTTPException(status_code=400, detail="OpenAI API key required for translation")

    result = await service.execute_improve_for_media(
        rating_key=request.rating_key,
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return result


@router.get("/history")
async def get_translation_history(limit: int = 50):
    """
    Lấy lịch sử translation.

    Returns list of history entries, mới nhất trước.
    """
    subtitle_service = get_subtitle_service()

    history = subtitle_service.get_translation_history(limit=min(limit, 200))

    return {
        "count": len(history),
        "items": history,
    }


@router.get("/preview/{rating_key}")
async def preview_subtitle(rating_key: str, lang: str = "vi"):
    """
    Fetch nội dung subtitle từ Plex on-demand theo rating_key.

    Không lưu gì trên disk — chỉ stream từ Plex server.
    """
    import tempfile
    from pathlib import Path

    subtitle_service = get_subtitle_service()

    try:
        video = subtitle_service.plex_client.get_video(rating_key)
    except Exception:
        raise HTTPException(status_code=404, detail="Video not found on Plex")

    metadata = subtitle_service.plex_client.extract_metadata(video)

    # Download sub vào temp, đọc content, rồi xóa ngay
    with tempfile.TemporaryDirectory() as tmp:
        sub_path = subtitle_service.plex_client.download_existing_subtitle(
            video, lang, Path(tmp)
        )

        if not sub_path:
            raise HTTPException(
                status_code=404,
                detail=f"No {lang} subtitle found on Plex for this video",
            )

        content = sub_path.read_text(encoding="utf-8", errors="replace")

    # Parse SRT entries for structured display
    entries = _parse_srt(content)

    return {
        "rating_key": rating_key,
        "title": str(metadata),
        "language": lang,
        "total_lines": len(entries),
        "entries": entries[:500],  # Cap 500 entries để tránh response quá lớn
        "truncated": len(entries) > 500,
    }


def _parse_srt(content: str) -> list[dict]:
    """Parse SRT content thành list of {index, time, text}."""
    import re

    blocks = re.split(r"\n\s*\n", content.strip())
    entries = []

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        # Line 1: index, Line 2: timestamp, Line 3+: text
        time_match = re.search(
            r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})",
            lines[1] if len(lines) > 1 else "",
        )
        if not time_match:
            continue

        entries.append({
            "index": lines[0].strip(),
            "start": time_match.group(1).replace(",", "."),
            "end": time_match.group(2).replace(",", "."),
            "text": "\n".join(lines[2:]).strip(),
        })

    return entries


@router.get("/stats")
async def get_translation_stats():
    """
    Get translation statistics.

    Returns:
        - Total translations
        - Total cost
        - Average cost per translation
    """
    subtitle_service = get_subtitle_service()

    stats = subtitle_service.get_translation_stats()

    return stats
