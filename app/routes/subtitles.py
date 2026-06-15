"""
Subtitle provider API routes.

These endpoints expose provider status, direct subtitle search, download, and upload
without requiring the web UI.
"""

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, model_validator

from app.models.subtitle import SubtitleSearchParams
from app.utils.logger import RequestContextLogger, get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/subtitles", tags=["subtitles"])


class SubtitleSearchRequest(BaseModel):
    """Search subtitles either by Plex rating_key or explicit metadata."""

    rating_key: str | None = None
    language: str | None = None
    title: str | None = None
    year: int | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None
    season: int | None = None
    episode: int | None = None
    video_filename: str | None = None
    use_cache: bool = True

    @model_validator(mode="after")
    def require_media_reference(self) -> "SubtitleSearchRequest":
        if not (self.rating_key or self.title or self.imdb_id or self.tmdb_id):
            raise ValueError("rating_key, title, imdb_id, or tmdb_id is required")
        return self


class SubtitleDownloadRequest(BaseModel):
    """Download one subtitle candidate as a file."""

    rating_key: str
    language: str | None = None
    subtitle_id: str | None = None
    use_cache: bool = True


class SubtitleUploadRequest(BaseModel):
    """Find/download and upload one target subtitle to Plex."""

    rating_key: str
    subtitle_id: str | None = None


def get_subtitle_service():
    """Get subtitle service instance từ main app."""
    from app.main import subtitle_service

    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return subtitle_service


@router.get("/providers")
async def get_subtitle_providers() -> dict[str, Any]:
    """Return enabled/skipped subtitle providers and the active cache scope."""
    service = get_subtitle_service()
    return service.get_subtitle_provider_status()


@router.post("/search")
async def search_subtitles(request: SubtitleSearchRequest) -> dict[str, Any]:
    """
    Search subtitles across all configured providers.

    Use `rating_key` for Plex media search, or explicit metadata fields for a
    provider-only search.
    """
    service = get_subtitle_service()
    log = RequestContextLogger(logger, request.rating_key or "api-search")

    if request.rating_key:
        return await service.search_subtitles_for_media(
            rating_key=request.rating_key,
            log=log,
            language=request.language,
            use_cache=request.use_cache,
        )

    params = SubtitleSearchParams(
        language=request.language or service.runtime_config.default_language,
        title=request.title,
        year=request.year,
        imdb_id=request.imdb_id,
        tmdb_id=request.tmdb_id,
        season=request.season,
        episode=request.episode,
        video_filename=request.video_filename,
    )
    results = await service._search_subtitles_by_params(
        params,
        log,
        use_cache=request.use_cache,
    )
    return {
        "language": params.language,
        "query": params.model_dump(),
        "provider_status": service.get_subtitle_provider_status(),
        "provider_counts": service._provider_counts(results),
        "candidates": [service._subtitle_result_payload(result) for result in results],
    }


@router.post("/download")
async def download_subtitle(
    request: SubtitleDownloadRequest,
    background_tasks: BackgroundTasks,
) -> FileResponse:
    """
    Search and download a subtitle file without uploading it to Plex.

    `subtitle_id` accepts both legacy raw IDs and provider-qualified IDs like
    `opensubtitles:12345`.
    """
    service = get_subtitle_service()
    log = RequestContextLogger(logger, request.rating_key)
    result = await service.download_subtitle_for_media(
        rating_key=request.rating_key,
        log=log,
        language=request.language,
        subtitle_id=request.subtitle_id,
        use_cache=request.use_cache,
    )
    if not result:
        raise HTTPException(status_code=404, detail="No downloadable subtitle found")

    path = Path(result["path"])
    cleanup_dir = Path(result["cleanup_dir"])
    background_tasks.add_task(shutil.rmtree, cleanup_dir, ignore_errors=True)
    subtitle = result["subtitle"]
    filename = f"{subtitle['provider']}-{subtitle['id']}-{path.name}"
    return FileResponse(
        path,
        filename=filename,
        media_type="application/x-subrip",
    )


@router.post("/upload")
async def upload_subtitle(request: SubtitleUploadRequest) -> dict[str, Any]:
    """
    Find/download and upload a target subtitle to Plex.

    This is the API equivalent of manual target upload in the sync UI.
    """
    service = get_subtitle_service()
    result = await service.execute_manual_target_upload_for_media(
        rating_key=request.rating_key,
        subtitle_id=request.subtitle_id,
    )
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result
