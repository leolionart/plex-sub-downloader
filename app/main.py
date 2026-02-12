"""
FastAPI application - main entry point.
Webhook server để nhận events từ Plex/Tautulli.
"""

import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Header, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models.webhook import PlexWebhookPayload, TautulliWebhookPayload
from app.models.settings import SubtitleSettings
from app.services.subtitle_service import SubtitleService, SubtitleServiceError
from app.utils.logger import setup_logging, get_logger
from app.routes import translation

# Setup logging
setup_logging()
logger = get_logger(__name__)

# Global service instance
subtitle_service: SubtitleService | None = None

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup/shutdown."""
    global subtitle_service

    # Startup
    logger.info("🚀 Starting Plex Subtitle Service")
    logger.info(f"Plex URL: {settings.plex_url}")
    logger.info(f"Default language: {settings.default_language}")
    logger.info(f"Subsource API: {settings.subsource_base_url}")

    subtitle_service = SubtitleService()
    logger.info("✓ Service initialized")

    yield

    # Shutdown
    logger.info("Shutting down service...")
    if subtitle_service:
        await subtitle_service.close()
    logger.info("✓ Service stopped")


app = FastAPI(
    title="Plex Subtitle Service",
    description="Automated Vietnamese subtitle downloader and uploader for Plex",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware (allow webhooks from Plex/Tautulli)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(translation.router)


@app.middleware("http")
async def add_request_id(request: Request, call_next: Any) -> Any:
    """Middleware để thêm request ID vào mọi request."""
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id

    logger.info(
        f"→ {request.method} {request.url.path}",
        extra={"request_id": request_id},
    )

    response = await call_next(request)

    logger.info(
        f"← {request.method} {request.url.path} - {response.status_code}",
        extra={"request_id": request_id},
    )

    return response


def verify_webhook_secret(x_webhook_secret: str | None = Header(None)) -> None:
    """
    Verify webhook secret nếu được cấu hình.

    Plex/Tautulli có thể gửi custom header để authenticate.
    """
    if settings.webhook_secret:
        if not x_webhook_secret or x_webhook_secret != settings.webhook_secret:
            logger.warning("Webhook authentication failed - invalid secret")
            raise HTTPException(status_code=403, detail="Invalid webhook secret")


@app.get("/", response_class=HTMLResponse)
async def web_ui(request: Request) -> HTMLResponse:
    """Web UI - Settings page."""
    config = subtitle_service.get_config() if subtitle_service else None

    stats = {
        "total_downloads": config.total_downloads if config else 0,
        "total_skipped": config.total_skipped if config else 0,
        "success_rate": (
            round(config.total_downloads / (config.total_downloads + config.total_skipped) * 100)
            if config and (config.total_downloads + config.total_skipped) > 0
            else 0
        ),
    }

    settings_data = config.subtitle_settings if config else SubtitleSettings()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "stats": stats,
            "languages": settings_data.languages,
        },
    )


@app.get("/translation", response_class=HTMLResponse)
async def translation_ui(request: Request) -> HTMLResponse:
    """Translation approval UI."""
    return templates.TemplateResponse("translation.html", {"request": request})


@app.get("/api/info")
async def api_info() -> dict[str, Any]:
    """API info endpoint."""
    return {
        "service": "Plex Subtitle Service",
        "version": "0.2.0",
        "status": "running",
        "endpoints": {
            "web_ui": "/",
            "webhook": "/webhook",
            "health": "/health",
            "settings_api": "/api/settings",
            "docs": "/docs",
        },
    }


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    """Get current settings API."""
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    config = subtitle_service.get_config()
    return config.model_dump()


@app.post("/api/settings")
async def update_settings(settings_update: SubtitleSettings) -> dict[str, str]:
    """Update settings API."""
    if not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    subtitle_service.update_settings(settings_update)

    return {
        "status": "success",
        "message": "Settings updated successfully",
    }


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint cho Docker."""
    return {"status": "healthy"}


@app.post("/webhook")
async def handle_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(None),
) -> JSONResponse:
    """
    Main webhook endpoint.

    Accepts webhooks từ:
    - Plex Media Server (multipart/form-data với JSON payload)
    - Tautulli (application/json)

    Events được xử lý:
    - library.new - Media mới thêm vào library
    - media.play - User bắt đầu xem (optional)
    - media.scrobble - User xem xong (optional)
    """
    # Verify webhook secret nếu configured
    verify_webhook_secret(x_webhook_secret)

    request_id = request.state.request_id
    logger.info(f"[{request_id}] Received webhook")

    try:
        # Parse payload based on content type
        content_type = request.headers.get("content-type", "")

        if "multipart/form-data" in content_type:
            # Plex webhook format
            payload = await _parse_plex_webhook(request)
        else:
            # Tautulli webhook format (JSON)
            payload = await _parse_tautulli_webhook(request)

        logger.info(
            f"[{request_id}] Webhook event: {payload.get('event')}",
            extra={"rating_key": payload.get("rating_key")},
        )

        # Extract rating_key
        rating_key = payload.get("rating_key")
        if not rating_key:
            logger.warning(f"[{request_id}] No ratingKey in webhook payload")
            return JSONResponse(
                status_code=400,
                content={"error": "Missing ratingKey in payload"},
            )

        # Filter events we care about
        event = payload.get("event", "")
        if not _should_process_event(event):
            logger.info(f"[{request_id}] Ignoring event: {event}")
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "message": f"Event not processed: {event}"},
            )

        # Process trong background để webhook return nhanh
        background_tasks.add_task(
            _process_subtitle_task,
            rating_key,
            event,
            request_id,
        )

        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "message": "Subtitle task queued",
                "request_id": request_id,
            },
        )

    except Exception as e:
        logger.error(f"[{request_id}] Webhook error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


async def _parse_plex_webhook(request: Request) -> dict[str, Any]:
    """
    Parse Plex webhook payload.

    Plex gửi multipart/form-data với field 'payload' chứa JSON.
    """
    form = await request.form()
    payload_str = form.get("payload")

    if not payload_str:
        raise ValueError("No 'payload' field in form data")

    import json
    payload_dict = json.loads(payload_str)

    # Validate với Pydantic model
    plex_payload = PlexWebhookPayload(**payload_dict)

    return {
        "event": plex_payload.event,
        "rating_key": plex_payload.rating_key,
        "media_type": plex_payload.media_type,
    }


async def _parse_tautulli_webhook(request: Request) -> dict[str, Any]:
    """Parse Tautulli webhook payload (JSON)."""
    payload_dict = await request.json()

    # Validate với Pydantic model
    tautulli_payload = TautulliWebhookPayload(**payload_dict)

    return {
        "event": tautulli_payload.event,
        "rating_key": tautulli_payload.rating_key,
        "media_type": tautulli_payload.media_type,
    }


def _should_process_event(event: str) -> bool:
    """
    Check xem event có nên được xử lý không.

    Process:
    - library.new - Media mới
    - library.on.deck - (optional)

    Ignore:
    - media.play, media.pause, media.stop - playback events
    - admin.* - admin events
    """
    process_events = [
        "library.new",
        "library.on.deck",
    ]

    return event in process_events


async def _process_subtitle_task(rating_key: str, event: str, request_id: str) -> None:
    """
    Background task để process subtitle.

    Args:
        rating_key: Plex ratingKey
        event: Webhook event type
        request_id: Request ID cho logging
    """
    assert subtitle_service is not None

    try:
        logger.info(f"[{request_id}] Starting subtitle task for ratingKey: {rating_key}")

        result = await subtitle_service.process_webhook(rating_key, event, request_id)

        logger.info(
            f"[{request_id}] Task completed: {result['status']}",
            extra={"message": result["message"]},
        )

    except SubtitleServiceError as e:
        logger.error(f"[{request_id}] Task failed: {e}")
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected error in task: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,  # Development only
        log_level=settings.log_level.lower(),
    )
