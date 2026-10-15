"""
Library API routes for proactive Plex subtitle management.
Enables browsing library sections, inspecting show/movie subtitle health,
and triggering batch download / translation tasks with real-time progress.
"""

import asyncio
import uuid
from typing import Any
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel

from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/library", tags=["library"])


class BatchActionRequest(BaseModel):
    rating_keys: list[str]
    use_cache: bool = True
    pronoun_override: str | None = None

class TaskTracker:
    """In-memory task tracker for batch operations."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}

    def create_task(self, task_type: str, total: int) -> str:
        task_id = str(uuid.uuid4())[:8]
        self._tasks[task_id] = {
            "id": task_id,
            "type": task_type,
            "status": "running",
            "total": total,
            "current": 0,
            "current_item": "",
            "logs": [],
            "results": [],
        }
        return task_id

    def add_log(self, task_id: str, message: str) -> None:
        if task_id in self._tasks:
            self._tasks[task_id]["logs"].append(message)

    def update_progress(
        self,
        task_id: str,
        current: int,
        item_title: str,
        log_message: str | None = None,
        result_item: dict[str, Any] | None = None,
    ) -> None:
        if task_id in self._tasks:
            task = self._tasks[task_id]
            task["current"] = current
            task["current_item"] = item_title
            if log_message:
                task["logs"].append(log_message)
            if result_item:
                task["results"].append(result_item)

    def finish_task(self, task_id: str, status: str = "completed") -> None:
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = status

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)


task_tracker = TaskTracker()


def get_subtitle_service():
    """Get active subtitle service instance."""
    from app.main import subtitle_service
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return subtitle_service


@router.get("/sections")
async def get_sections(
    use_cache: bool = Query(True),
    refresh: bool = Query(False),
):
    """List all Plex library sections (movies, tv shows)."""
    service = get_subtitle_service()
    effective_use_cache = use_cache and not refresh
    try:
        sections = await asyncio.to_thread(service.get_library_sections, use_cache=effective_use_cache)
        return {"sections": sections}
    except Exception as e:
        logger.error(f"Error fetching sections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache/stats")
async def get_cache_stats():
    """Get in-memory TTL cache statistics for Plex queries."""
    service = get_subtitle_service()
    try:
        return service.get_plex_cache_stats()
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/clear")
async def clear_cache(
    rating_key: str | None = None,
    section_key: str | None = None,
):
    """Clear or invalidate Plex in-memory TTL caches."""
    service = get_subtitle_service()
    try:
        res = service.clear_plex_cache(rating_key=rating_key, section_key=section_key)
        return {"ok": True, **res}
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sections/{section_key}/items")
async def get_section_items(
    section_key: str,
    query: str | None = None,
    filter: str = Query("missing_vi"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    use_cache: bool = Query(True),
    refresh: bool = Query(False),
):
    """List media items in a section with search, subtitle filter, and pagination."""
    service = get_subtitle_service()
    effective_use_cache = use_cache and not refresh
    try:
        data = await asyncio.to_thread(
            service.get_library_items,
            section_key=section_key,
            query=query,
            offset=offset,
            limit=limit,
            subtitle_filter=filter,
            use_cache=effective_use_cache,
        )
        return data
    except Exception as e:
        logger.error(f"Error fetching section items: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shows/{rating_key}/tree")
async def get_show_tree(
    rating_key: str,
    use_cache: bool = Query(True),
    refresh: bool = Query(False),
):
    """Get hierarchical show structure with subtitle status for each episode."""
    service = get_subtitle_service()
    effective_use_cache = use_cache and not refresh
    try:
        tree = await asyncio.to_thread(
            service.get_show_tree,
            rating_key,
            use_cache=effective_use_cache,
        )
        return tree
    except Exception as e:
        logger.error(f"Error fetching show tree for {rating_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/movies/{rating_key}")
async def get_movie_details(
    rating_key: str,
    use_cache: bool = Query(True),
    refresh: bool = Query(False),
):
    """Get movie details and subtitle status."""
    service = get_subtitle_service()
    effective_use_cache = use_cache and not refresh
    try:
        details = await asyncio.to_thread(
            service.get_movie_details,
            rating_key,
            use_cache=effective_use_cache,
        )
        return details
    except Exception as e:
        logger.error(f"Error fetching movie details for {rating_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def _run_batch_upload_task(
    task_id: str,
    rating_keys: list[str],
    use_cache: bool,
) -> None:
    """Execute background batch subtitle upload with progress updates."""
    service = get_subtitle_service()
    total = len(rating_keys)
    task_tracker.add_log(task_id, f"Bắt đầu tìm và tải Vietsub cho {total} tập/phim...")

    for idx, rk in enumerate(rating_keys, start=1):
        item_label = f"RatingKey {rk}"
        try:
            # First preview to get item title
            preview = await service.preview_sync_for_media(rk)
            item_label = preview.get("title", item_label)

            if preview.get("has_vi_subtitle"):
                task_tracker.update_progress(
                    task_id=task_id,
                    current=idx,
                    item_title=item_label,
                    log_message=f"[{idx}/{total}] {item_label}: Đã có Vietsub trên Plex. Bỏ qua.",
                    result_item={"rating_key": rk, "title": item_label, "status": "skipped_exists"},
                )
                await asyncio.sleep(0.5)
                continue

            task_tracker.update_progress(
                task_id=task_id,
                current=idx,
                item_title=item_label,
                log_message=f"[{idx}/{total}] {item_label}: Đang tìm kiếm Vietsub từ các provider...",
            )

            res = await service.execute_manual_target_upload_for_media(
                rating_key=rk,
                use_cache=use_cache,
            )

            status = res.get("status")
            if status == "success":
                task_tracker.update_progress(
                    task_id=task_id,
                    current=idx,
                    item_title=item_label,
                    log_message=f"[{idx}/{total}] {item_label}: ✓ Đã tải và upload Vietsub thành công!",
                    result_item={"rating_key": rk, "title": item_label, "status": "success"},
                )
            else:
                task_tracker.update_progress(
                    task_id=task_id,
                    current=idx,
                    item_title=item_label,
                    log_message=f"[{idx}/{total}] {item_label}: ⚠ Không tìm thấy Vietsub phù hợp.",
                    result_item={"rating_key": rk, "title": item_label, "status": "not_found"},
                )
        except Exception as exc:
            logger.error(f"Batch upload error for {rk}: {exc}")
            task_tracker.update_progress(
                task_id=task_id,
                current=idx,
                item_title=item_label,
                log_message=f"[{idx}/{total}] {item_label}: ✕ Lỗi: {exc}",
                result_item={"rating_key": rk, "title": item_label, "status": "error", "error": str(exc)},
            )
        await asyncio.sleep(1)

    task_tracker.add_log(task_id, "Hoàn tất toàn bộ tác vụ tìm sub!")
    task_tracker.finish_task(task_id, "completed")


async def _run_batch_translate_task(
    task_id: str,
    rating_keys: list[str],
    pronoun_override: str | None = None,
) -> None:
    """Execute background batch subtitle translation with progress updates."""
    service = get_subtitle_service()
    total = len(rating_keys)
    task_tracker.add_log(task_id, f"Bắt đầu dịch AI từ Engsub cho {total} tập/phim...")

    for idx, rk in enumerate(rating_keys, start=1):
        item_label = f"RatingKey {rk}"
        try:
            preview = await service.preview_sync_for_media(rk)
            item_label = preview.get("title", item_label)

            if preview.get("has_vi_subtitle"):
                task_tracker.update_progress(
                    task_id=task_id,
                    current=idx,
                    item_title=item_label,
                    log_message=f"[{idx}/{total}] {item_label}: Đã có Vietsub trên Plex. Bỏ qua.",
                    result_item={"rating_key": rk, "title": item_label, "status": "skipped_exists"},
                )
                await asyncio.sleep(0.5)
                continue

            if not preview.get("can_translate"):
                task_tracker.update_progress(
                    task_id=task_id,
                    current=idx,
                    item_title=item_label,
                    log_message=f"[{idx}/{total}] {item_label}: ⚠ Không có Engsub văn bản để dịch.",
                    result_item={"rating_key": rk, "title": item_label, "status": "cannot_translate"},
                )
                await asyncio.sleep(0.5)
                continue

            task_tracker.update_progress(
                task_id=task_id,
                current=idx,
                item_title=item_label,
                log_message=f"[{idx}/{total}] {item_label}: Đang gửi AI dịch thuật...",
            )

            res = await service.execute_translate_for_media(
                rating_key=rk,
                pronoun_override=pronoun_override,
            )
            status = res.get("status") if isinstance(res, dict) else "success"

            task_tracker.update_progress(
                task_id=task_id,
                current=idx,
                item_title=item_label,
                log_message=f"[{idx}/{total}] {item_label}: ✓ Đã dịch và upload Vietsub thành công!",
                result_item={"rating_key": rk, "title": item_label, "status": "success"},
            )
        except Exception as exc:
            logger.error(f"Batch translate error for {rk}: {exc}")
            task_tracker.update_progress(
                task_id=task_id,
                current=idx,
                item_title=item_label,
                log_message=f"[{idx}/{total}] {item_label}: ✕ Lỗi khi dịch: {exc}",
                result_item={"rating_key": rk, "title": item_label, "status": "error", "error": str(exc)},
            )
        await asyncio.sleep(2)

    task_tracker.add_log(task_id, "Hoàn tất toàn bộ tác vụ dịch thuật!")
    task_tracker.finish_task(task_id, "completed")

def _expand_rating_keys_to_videos(service: Any, rating_keys: list[str]) -> list[str]:
    """Expand any show/season rating keys into individual episode rating keys."""
    expanded: list[str] = []
    seen: set[str] = set()

    for rk in rating_keys:
        try:
            item = service.plex_client.get_item(rk)
            item_type = getattr(item, "type", None)
            if item_type in ("show", "season") and hasattr(item, "episodes"):
                for ep in item.episodes():
                    ep_rk = str(getattr(ep, "ratingKey", ep))
                    if ep_rk not in seen:
                        seen.add(ep_rk)
                        expanded.append(ep_rk)
            else:
                str_rk = str(rk)
                if str_rk not in seen:
                    seen.add(str_rk)
                    expanded.append(str_rk)
        except Exception as e:
            logger.warning(f"Could not inspect item {rk} for expansion, keeping original: {e}")
            str_rk = str(rk)
            if str_rk not in seen:
                seen.add(str_rk)
                expanded.append(str_rk)

    return expanded


@router.post("/batch/upload-target")
async def batch_upload_target(request: BatchActionRequest, background_tasks: BackgroundTasks):
    """Start background batch subtitle search and upload."""
    if not request.rating_keys:
        raise HTTPException(status_code=400, detail="rating_keys list cannot be empty")

    service = get_subtitle_service()
    expanded_keys = await asyncio.to_thread(_expand_rating_keys_to_videos, service, request.rating_keys)
    if not expanded_keys:
        expanded_keys = request.rating_keys

    task_id = task_tracker.create_task("upload_target", len(expanded_keys))
    background_tasks.add_task(
        _run_batch_upload_task,
        task_id=task_id,
        rating_keys=expanded_keys,
        use_cache=request.use_cache,
    )
    return {"task_id": task_id, "total": len(expanded_keys), "status": "started"}


@router.post("/batch/translate")
async def batch_translate(request: BatchActionRequest, background_tasks: BackgroundTasks):
    """Start background batch subtitle translation."""
    if not request.rating_keys:
        raise HTTPException(status_code=400, detail="rating_keys list cannot be empty")

    service = get_subtitle_service()
    expanded_keys = await asyncio.to_thread(_expand_rating_keys_to_videos, service, request.rating_keys)
    if not expanded_keys:
        expanded_keys = request.rating_keys

    task_id = task_tracker.create_task("translate", len(expanded_keys))
    background_tasks.add_task(
        _run_batch_translate_task,
        task_id=task_id,
        rating_keys=expanded_keys,
        pronoun_override=request.pronoun_override,
    )
    return {"task_id": task_id, "total": len(expanded_keys), "status": "started"}


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Query progress and logs of an active or finished batch task."""
    task = task_tracker.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
