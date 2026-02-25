"""
Sync timing API routes.
Đồng bộ timing Vietsub dựa trên Engsub chuẩn kèm phim.
Hỗ trợ tìm Vietsub từ Subsource khi chưa có trên Plex.
"""

import asyncio
import re
from urllib.parse import urlparse, parse_qs

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


class SyncRequest(BaseModel):
    """Request để execute sync timing."""
    rating_key: str
    subtitle_id: str | None = None
    source_lang: str = "en"


class TranslateRequest(BaseModel):
    """Request để translate subtitle sang Vietnamese."""
    rating_key: str
    from_lang: str = "en"


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

    if not service.runtime_config.ai_available:
        raise HTTPException(status_code=400, detail="OpenAI API key required for sync timing")

    result = await service.execute_sync_for_media(
        rating_key=request.rating_key,
        subtitle_id=request.subtitle_id,
        source_lang=request.source_lang,
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
        from_lang=request.from_lang,
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
        "ai_available": service.runtime_config.ai_available,
        "model": service.runtime_config.openai_model,
    }


@router.get("/history")
async def get_sync_history(limit: int = 50):
    """Get sync timing history."""
    service = get_subtitle_service()
    return {"items": service.get_sync_history(limit=limit)}


def _parse_watch_plex_url(url: str) -> dict | None:
    """
    Parse watch.plex.tv URL to extract content type and Plex GUID.

    URL patterns:
    - /movie/{slug}
    - /show/{slug}
    - /show/{slug}/season/{n}
    - /show/{slug}/season/{n}/episode/{n}
    May have locale prefix: /vi/movie/..., /en-GB/show/...

    Returns dict with keys: content_type, plex_guid, slug
    Or None if URL can't be parsed.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    path = parsed.path.strip("/")
    parts = path.split("/")

    # Skip locale prefix (e.g. 'vi', 'en-GB', 'cs')
    if parts and len(parts[0]) <= 5 and parts[0] not in ("movie", "show"):
        parts = parts[1:]

    plex_guid = params.get("utm_content", [None])[0]

    if not parts:
        return None

    if parts[0] == "movie":
        return {
            "content_type": "movie",
            "plex_guid": plex_guid,
            "slug": parts[1] if len(parts) > 1 else None,
        }
    elif parts[0] == "show":
        if len(parts) >= 6 and parts[2] == "season" and parts[4] == "episode":
            return {
                "content_type": "episode",
                "plex_guid": plex_guid,
                "slug": parts[1],
                "season": int(parts[3]),
                "episode": int(parts[5]),
            }
        elif len(parts) >= 4 and parts[2] == "season":
            return {
                "content_type": "season",
                "plex_guid": plex_guid,
                "slug": parts[1],
                "season": int(parts[3]),
            }
        else:
            return {
                "content_type": "show",
                "plex_guid": plex_guid,
                "slug": parts[1] if len(parts) > 1 else None,
            }

    return None


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
    logger.info(f"Resolving input: {raw[:100]}")

    # Case 1: Plain numeric rating key
    if raw.isdigit():
        return {"rating_key": raw}

    # Case 2: URL containing metadata ID (app.plex.tv desktop links)
    match = re.search(r"metadata(?:%2F|/)(\d+)", raw)
    if match:
        logger.info(f"Extracted rating key from metadata URL: {match.group(1)}")
        return {"rating_key": match.group(1)}

    # Case 3: Plex share/web link — follow redirects
    if "plex.tv" in raw:
        import httpx
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                response = await client.get(raw)
                final_url = str(response.url)
                logger.info(f"Resolved URL: {raw[:60]} → {final_url[:120]}")

                # 3a: Check if redirected URL contains metadata ID
                match = re.search(r"metadata(?:%2F|/)(\d+)", final_url)
                if match:
                    logger.info(f"Extracted rating key from redirect: {match.group(1)}")
                    return {"rating_key": match.group(1)}

                # 3b: Handle watch.plex.tv URLs (share links)
                if "watch.plex.tv" in final_url:
                    parsed = _parse_watch_plex_url(final_url)
                    logger.info(f"Parsed watch.plex.tv URL: {parsed}")

                    if not parsed:
                        raise HTTPException(
                            status_code=400,
                            detail="Không thể phân tích link Plex. Vui lòng dùng link trực tiếp từ Plex app.",
                        )

                    # Show/season links can't be synced directly
                    if parsed["content_type"] in ("show", "season"):
                        type_vi = "show" if parsed["content_type"] == "show" else "season"
                        raise HTTPException(
                            status_code=400,
                            detail=f"Link này trỏ tới {type_vi}, không phải episode cụ thể. "
                                   f"Vui lòng share link của một episode hoặc movie cụ thể.",
                        )

                    # Movie or episode — search user's Plex library by GUID
                    if parsed.get("plex_guid"):
                        service = get_subtitle_service()
                        rating_key = await asyncio.to_thread(
                            service.plex_client.find_by_plex_guid,
                            parsed["content_type"],
                            parsed["plex_guid"],
                        )
                        if rating_key:
                            logger.info(f"Found rating key via GUID: {rating_key}")
                            return {"rating_key": str(rating_key)}

                        slug = parsed.get("slug", "")
                        title_hint = slug.replace("-", " ") if slug else ""
                        raise HTTPException(
                            status_code=404,
                            detail=f"Không tìm thấy \"{title_hint}\" trong thư viện Plex của bạn. "
                                   f"Hãy chắc chắn phim/episode này có trong library.",
                        )

                    raise HTTPException(
                        status_code=400,
                        detail="Link Plex không chứa thông tin GUID. Thử dùng Rating Key trực tiếp.",
                    )

                # 3c: Fallback — check response body for metadata reference
                body = response.text
                match = re.search(r"metadata(?:%2F|/)(\d+)", body)
                if match:
                    logger.info(f"Extracted rating key from body: {match.group(1)}")
                    return {"rating_key": match.group(1)}

                logger.warning(f"Could not extract rating key from URL: {final_url[:120]}")
                raise HTTPException(
                    status_code=400,
                    detail="Không thể trích xuất rating key từ link này.",
                )
        except HTTPException:
            raise
        except httpx.HTTPError as e:
            logger.error(f"HTTP error resolving URL: {e}")
            raise HTTPException(status_code=400, detail=f"Không thể truy cập link: {e}")
        except Exception as e:
            logger.error(f"Unexpected error resolving URL: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý link: {e}")

    raise HTTPException(status_code=400, detail="Định dạng input không được hỗ trợ. Dùng Rating Key hoặc link Plex.")


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
