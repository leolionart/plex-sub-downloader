"""
Sync timing API routes.
Đồng bộ timing Vietsub dựa trên Engsub chuẩn kèm phim.
Hỗ trợ tìm Vietsub từ Subsource khi chưa có trên Plex.
"""

import asyncio
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/sync", tags=["sync"])


class SyncRequest(BaseModel):
    """Request để execute sync timing."""
    rating_key: str
    subtitle_id: str | None = None


class TranslateRequest(BaseModel):
    """Request để translate English sub."""
    rating_key: str


class ResolveUrlRequest(BaseModel):
    """Request để resolve Plex URL/share link thành rating key."""
    input: str


def get_subtitle_service():
    """Get subtitle service instance từ main app."""
    from app.main import subtitle_service
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return subtitle_service


@router.post("/preview")
async def preview_sync(request: SyncRequest):
    """
    Preview sync: kiểm tra subtitle có sẵn trên Plex + Subsource.

    Trả về metadata, trạng thái English sub, danh sách Vietnamese sub candidates.
    """
    service = get_subtitle_service()

    try:
        result = await service.preview_sync_for_media(
            rating_key=request.rating_key,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execute")
async def execute_sync(request: SyncRequest):
    """
    Execute sync timing cho một media item.

    Tìm Engsub trên Plex + Vietsub trên Plex/Subsource, sync timing, upload.
    """
    service = get_subtitle_service()

    if not service.config.subtitle_settings.auto_sync_timing:
        raise HTTPException(status_code=400, detail="Sync timing is disabled. Enable it in Settings.")

    if not service.runtime_config.ai_available:
        raise HTTPException(status_code=400, detail="OpenAI API key required for sync timing")

    result = await service.execute_sync_for_media(
        rating_key=request.rating_key,
        subtitle_id=request.subtitle_id,
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return result


@router.post("/translate")
async def translate_for_media(request: TranslateRequest):
    """
    Chủ động dịch English subtitle sang Vietnamese.

    Dùng khi không tìm được Vietnamese subtitle phù hợp.
    """
    service = get_subtitle_service()

    if not service.runtime_config.ai_available:
        raise HTTPException(status_code=400, detail="OpenAI API key required for translation")

    result = await service.execute_translate_for_media(
        rating_key=request.rating_key,
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return result


@router.get("/status")
async def get_sync_status():
    """Get sync feature status."""
    service = get_subtitle_service()

    ss = service.config.subtitle_settings
    return {
        "sync_enabled": ss.auto_sync_timing,
        "auto_sync_after_download": ss.auto_sync_after_download,
        "ai_available": service.runtime_config.ai_available,
        "model": service.runtime_config.openai_model,
    }


@router.post("/resolve-url")
async def resolve_plex_url(request: ResolveUrlRequest):
    """
    Resolve Plex URL / share link / rating key thành rating key.

    Accepts:
    - Plain rating key: "12345"
    - Share link: "https://l.plex.tv/XXXXX"
    - Full URL: "https://app.plex.tv/.../metadata/12345"
    """
    raw = request.input.strip()

    # Case 1: Plain numeric rating key
    if raw.isdigit():
        return {"rating_key": raw}

    # Case 2: URL containing metadata ID
    match = re.search(r"metadata(?:%2F|/)(\d+)", raw)
    if match:
        return {"rating_key": match.group(1)}

    # Case 3: Plex share/web link — follow redirects
    if "plex.tv" in raw:
        import httpx
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                response = await client.get(raw)
                final_url = str(response.url)

                match = re.search(r"metadata(?:%2F|/)(\d+)", final_url)
                if match:
                    return {"rating_key": match.group(1)}

                # Fallback: check response body for metadata reference
                body = response.text
                match = re.search(r"metadata(?:%2F|/)(\d+)", body)
                if match:
                    return {"rating_key": match.group(1)}

                raise HTTPException(
                    status_code=400,
                    detail=f"Could not extract rating key from resolved URL",
                )
        except httpx.HTTPError as e:
            raise HTTPException(status_code=400, detail=f"Failed to resolve URL: {e}")

    raise HTTPException(status_code=400, detail="Unrecognized input format")


@router.get("/now-playing")
async def get_now_playing():
    """Get currently playing sessions."""
    service = get_subtitle_service()

    sessions = await asyncio.to_thread(service.plex_client.get_sessions)

    return {
        "sessions": sessions,
    }


@router.get("/thumb/{rating_key}")
async def proxy_thumb(rating_key: str):
    """Proxy thumbnail from Plex server (avoids CORS/network issues)."""
    service = get_subtitle_service()

    try:
        video = await asyncio.to_thread(service.plex_client.get_video, rating_key)
    except Exception:
        raise HTTPException(status_code=404, detail="Video not found")

    thumb = getattr(video, "thumb", None)
    if not thumb:
        raise HTTPException(status_code=404, detail="No thumbnail")

    thumb_url = service.plex_client.get_thumb_url(thumb)
    if not thumb_url:
        raise HTTPException(status_code=404, detail="Could not generate thumb URL")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.get(thumb_url)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to fetch thumbnail")
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=3600"},
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Failed to fetch thumbnail")
