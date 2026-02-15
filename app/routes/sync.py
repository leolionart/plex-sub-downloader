"""
Sync timing API routes.
Đồng bộ timing Vietsub dựa trên Engsub chuẩn kèm phim.
Hỗ trợ tìm Vietsub từ Subsource khi chưa có trên Plex.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/sync", tags=["sync"])


class SyncRequest(BaseModel):
    """Request để execute sync timing."""
    rating_key: str
    subtitle_id: str | None = None


class TranslateRequest(BaseModel):
    """Request để translate English sub."""
    rating_key: str


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

    if not service.runtime_config.sync_enabled:
        raise HTTPException(status_code=400, detail="Sync timing is disabled. Enable it in Setup.")

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

    return {
        "sync_enabled": service.runtime_config.sync_enabled,
        "auto_sync_after_download": service.runtime_config.auto_sync_after_download,
        "ai_available": service.runtime_config.ai_available,
        "model": service.runtime_config.openai_model,
    }
