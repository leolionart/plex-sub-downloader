"""
Translation approval routes cho Web UI.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.services.subtitle_service import subtitle_service
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


@router.get("/pending")
async def get_pending_translations():
    """
    Lấy danh sách pending translations cần approval.

    Returns list of videos waiting for translation approval.
    """
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

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

    Args:
        rating_key: Plex ratingKey
        from_lang: Source language (default: en)
        to_lang: Target language (default: vi)

    Returns:
        Cost estimation details
    """
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    if not settings.translation_enabled:
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

        subtitle = await subtitle_service._find_best_subtitle_by_params(
            search_params,
            subtitle_service._get_logger(request.rating_key[:8])
        )

        if not subtitle:
            raise HTTPException(
                status_code=404,
                detail=f"No {request.from_lang} subtitle found for translation"
            )

        # Download subtitle temporarily
        import tempfile
        from pathlib import Path

        temp_dir = Path(tempfile.mkdtemp())
        subtitle_path = await subtitle_service._download_subtitle(
            subtitle,
            metadata,
            subtitle_service._get_logger(request.rating_key[:8])
        )

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
    """
    Approve và execute translation.

    User đã review cost estimate và approve translation.
    """
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    if not settings.translation_enabled:
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
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    # Remove from pending queue
    subtitle_service.remove_pending_translation(request.rating_key)

    return {
        "status": "rejected",
        "message": "Translation request rejected",
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
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    stats = subtitle_service.get_translation_stats()

    return stats
