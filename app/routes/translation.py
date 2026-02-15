"""
Translation approval routes cho Web UI.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.webhook import MediaMetadata

router = APIRouter(prefix="/api/translation", tags=["translation"])


class TranslationRequest(BaseModel):
    """Request để approve translation."""
    rating_key: str
    from_lang: str = "en"
    to_lang: str = "vi"


class TranslationEstimateResponse(BaseModel):
    """Response cho cost estimation."""
    rating_key: str
    title: str
    subtitle_entries: int
    total_characters: int
    estimated_tokens: int
    estimated_cost_usd: float
    model: str


def get_subtitle_service():
    """Get subtitle service instance từ main app."""
    from app.main import subtitle_service
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return subtitle_service


@router.get("/pending")
async def get_pending_translations():
    """
    Lấy danh sách pending translations cần approval.

    Returns list of videos waiting for translation approval.
    """
    subtitle_service = get_subtitle_service()

    # Get from pending queue
    pending = subtitle_service.get_pending_translations()

    return {
        "count": len(pending),
        "items": pending,
    }


@router.post("/estimate")
async def estimate_translation_cost(request: TranslationRequest):
    """
    Estimate translation cost trước khi approve.
    """
    subtitle_service = get_subtitle_service()

    if not subtitle_service.config.subtitle_settings.translation_enabled:
        raise HTTPException(status_code=400, detail="Translation disabled")

    try:
        # Get video from Plex
        video = subtitle_service.plex_client.get_video(request.rating_key)
        metadata = subtitle_service.plex_client.extract_metadata(video)

        # Search source language subtitle
        from app.models.subtitle import SubtitleSearchParams

        search_params = SubtitleSearchParams(
            language=request.from_lang,
            title=metadata.search_title,
            year=metadata.year,
            imdb_id=metadata.imdb_id,
            tmdb_id=metadata.tmdb_id,
            season=metadata.season_number,
            episode=metadata.episode_number,
        )

        results = await subtitle_service._search_subtitles_by_params(
            search_params,
            subtitle_service._get_logger(request.rating_key[:8])
        )

        if not results:
            raise HTTPException(
                status_code=404,
                detail=f"No {request.from_lang} subtitle found for translation"
            )

        # Download subtitle temporarily (try each candidate)
        import tempfile
        from pathlib import Path

        temp_dir = Path(tempfile.mkdtemp())
        log = subtitle_service._get_logger(request.rating_key[:8])
        downloaded = await subtitle_service._download_first_available(results, metadata, log)
        if not downloaded:
            raise HTTPException(
                status_code=502,
                detail=f"All {request.from_lang} subtitle downloads failed"
            )
        subtitle, subtitle_path = downloaded

        # Estimate cost
        estimate = await subtitle_service.translation_client.estimate_cost(subtitle_path)

        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

        return {
            "rating_key": request.rating_key,
            "title": str(metadata),
            "subtitle_name": subtitle.name,
            "from_lang": request.from_lang,
            "to_lang": request.to_lang,
            **estimate,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approve")
async def approve_translation(request: TranslationRequest):
    """Approve và execute translation."""
    subtitle_service = get_subtitle_service()

    if not subtitle_service.config.subtitle_settings.translation_enabled:
        raise HTTPException(status_code=400, detail="Translation disabled")

    try:
        # Get video
        video = subtitle_service.plex_client.get_video(request.rating_key)
        metadata = subtitle_service.plex_client.extract_metadata(video)

        log = subtitle_service._get_logger(request.rating_key[:8])

        # Execute translation (force bypass approval check)
        result = await subtitle_service._execute_translation(
            metadata=metadata,
            video=video,
            from_lang=request.from_lang,
            to_lang=request.to_lang,
            log=log,
        )

        if not result:
            raise HTTPException(status_code=500, detail="Translation failed")

        return {
            "status": "success",
            "message": result["message"],
            "details": result,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reject")
async def reject_translation(request: TranslationRequest):
    """
    Reject translation request.

    User decided not to translate.
    """
    subtitle_service = get_subtitle_service()

    # Lấy thông tin pending trước khi xóa (để ghi history)
    pending = subtitle_service._pending_translations.get(request.rating_key)
    title = pending["title"] if pending else f"ratingKey:{request.rating_key}"

    # Ghi history
    subtitle_service.add_history_entry(
        rating_key=request.rating_key,
        title=title,
        from_lang=request.from_lang,
        to_lang=request.to_lang,
        status="rejected",
    )

    # Remove from pending queue
    subtitle_service.remove_pending_translation(request.rating_key)

    return {
        "status": "rejected",
        "message": "Translation request rejected",
    }


@router.get("/history")
async def get_translation_history(limit: int = 50):
    """
    Lấy lịch sử translation (approved, auto_approved, rejected).

    Returns list of history entries, mới nhất trước.
    """
    subtitle_service = get_subtitle_service()

    history = subtitle_service.get_translation_history(limit=min(limit, 200))

    return {
        "count": len(history),
        "items": history,
    }


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
