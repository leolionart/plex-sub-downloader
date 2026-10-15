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
        # A Plex rating key, external ID, title, or a concrete video filename
        # is enough to perform a provider search.  Filename-only searches are
        # particularly useful for manual recovery when Plex metadata points to
        # the wrong release/episode (SubDL supports this directly).
        if not (
            self.rating_key
            or self.title
            or self.imdb_id
            or self.tmdb_id
            or self.video_filename
        ):
            raise ValueError(
                "rating_key, title, imdb_id, tmdb_id, or video_filename is required"
            )
        return self


class ManualSubtitleSearchRequest(SubtitleSearchRequest):
    """Manual search payload; cache is opt-in to avoid stale auto matches."""

    use_cache: bool = False


class SubtitleDownloadRequest(BaseModel):
    """Download one subtitle candidate as a file."""

    rating_key: str
    language: str | None = None
    subtitle_id: str | None = None
    use_cache: bool = True
    search_override: SubtitleSearchRequest | None = None


class SubtitleUploadRequest(BaseModel):
    """Find/download and upload one target subtitle to Plex."""

    rating_key: str
    subtitle_id: str | None = None
    use_cache: bool = True
    search_override: SubtitleSearchRequest | None = None


class SubtitleTranslateRequest(BaseModel):
    """Translate subtitles for media item."""

    rating_key: str
    from_lang: str = "en"
    use_cache: bool = True
    source_subtitle_id: str | None = None
    source_search_override: SubtitleSearchRequest | None = None


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


@router.post("/manual-search")
async def manual_search_subtitles(request: ManualSubtitleSearchRequest) -> dict[str, Any]:
    """Fresh, non-mutating subtitle search intended for manual recovery.

    This endpoint mirrors ``/search`` but defaults to bypassing the cache so a
    user can retry after an automatic match was wrong. Callers may still set
    ``use_cache=true`` explicitly when desired.
    """
    return await search_subtitles(request)


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
        search_override=(
            _search_params_from_override(request.search_override, request.language)
            if request.search_override
            else None
        ),
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
        use_cache=request.use_cache,
        search_override=(
            _search_params_from_override(request.search_override)
            if request.search_override
            else None
        ),
    )
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/translate")
async def translate_subtitle(request: SubtitleTranslateRequest) -> dict[str, Any]:
    """Execute subtitle translation for media item."""
    service = get_subtitle_service()
    if not service.runtime_config.ai_available:
        raise HTTPException(status_code=400, detail="OpenAI API key required for translation")

    result = await service.execute_translate_for_media(
        rating_key=request.rating_key,
        from_lang=request.from_lang,
        use_cache=request.use_cache,
        source_subtitle_id=request.source_subtitle_id,
        source_search_override=(
            _search_params_from_override(request.source_search_override, request.from_lang)
            if request.source_search_override
            else None
        ),
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Translation failed"))
    return result


def _search_params_from_override(
    override: SubtitleSearchRequest,
    language: str | None = None,
) -> SubtitleSearchParams:
    """Convert public override payload to provider search params."""
    return SubtitleSearchParams(
        language=override.language or language or "vi",
        title=override.title,
        year=override.year,
        imdb_id=override.imdb_id,
        tmdb_id=override.tmdb_id,
        season=override.season,
        episode=override.episode,
        video_filename=override.video_filename,
    )
