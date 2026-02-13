"""
Log viewer API routes with SSE streaming.
"""

import asyncio
import json

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.utils.logger import log_buffer

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def get_logs(
    limit: int = Query(default=200, ge=1, le=2000),
    level: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> dict:
    """Get buffered log entries."""
    entries = log_buffer.get_entries(limit=limit, level=level, search=search)
    return {
        "count": len(entries),
        "total_buffered": log_buffer.entry_count,
        "entries": entries,
    }


@router.get("/stream")
async def stream_logs() -> StreamingResponse:
    """SSE endpoint for real-time log streaming."""

    async def event_generator():
        queue = log_buffer.subscribe()
        try:
            # Send initial keepalive
            yield ": connected\n\n"
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=30.0)
                    data = json.dumps(entry.to_dict(), ensure_ascii=False)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment to prevent connection timeout
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            log_buffer.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/clear")
async def clear_logs() -> dict:
    """Clear the log buffer."""
    log_buffer.clear()
    return {"status": "ok", "message": "Log buffer cleared"}
