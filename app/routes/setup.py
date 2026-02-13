"""
Setup routes for runtime configuration and Plex PIN login flow.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any
import xml.etree.ElementTree as ET

import socket

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.clients.plex_client import PlexClientError
from app.config import settings

from app.models.runtime_config import RuntimeConfig

router = APIRouter(prefix="/api/setup", tags=["setup"])

# In-memory PIN cache (best-effort; acceptable for setup flow)
_pin_cache: dict[str, dict[str, Any]] = {}


class RuntimeConfigPayload(RuntimeConfig):
    class Config:
        extra = "ignore"
        populate_by_name = True


def _get_services(require_subtitle_service: bool = False):
    """Lazy import to avoid circular imports."""
    from app.main import config_store, subtitle_service, runtime_config
    if not config_store or not runtime_config:
        raise HTTPException(status_code=503, detail="Service not initialized")
    if require_subtitle_service and not subtitle_service:
        raise HTTPException(status_code=503, detail="Service not fully configured — complete setup first")
    return config_store, subtitle_service, runtime_config


def _detect_lan_ip() -> str:
    """Detect the LAN IP address of this machine."""
    try:
        # Connect to an external address to determine which interface is used
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


@router.get("/config")
async def get_runtime_config() -> dict[str, Any]:
    config_store, subtitle_service, runtime_config = _get_services()
    data = runtime_config.sanitized().model_dump()

    # Add computed webhook_url
    if settings.external_url:
        base = settings.external_url.rstrip("/")
        data["webhook_url"] = f"{base}/webhook"
    else:
        lan_ip = _detect_lan_ip()
        port = settings.app_port
        data["webhook_url"] = f"http://{lan_ip}:{port}/webhook"

    return data


@router.post("/config")
async def update_runtime_config(payload: RuntimeConfigPayload) -> dict[str, str]:
    import app.main as main_module

    config_store, subtitle_service, runtime_config = _get_services()

    partial = payload.model_dump(exclude_unset=True, exclude_none=True)
    updated = runtime_config.model_copy(update=partial)

    config_store.save(updated)
    main_module.runtime_config = updated

    if subtitle_service:
        subtitle_service.update_runtime_config(updated)
    else:
        # First-time setup — try to initialize service now that config is saved
        main_module.reinit_service()

    return {"status": "ok"}


@router.get("/status")
async def setup_status() -> dict[str, Any]:
    _, _, runtime_config = _get_services()
    configured = bool(runtime_config.plex_url and runtime_config.plex_token and runtime_config.subsource_api_key)
    return {"configured": configured}


@router.post("/plex/pin")
async def plex_pin_request() -> dict[str, Any]:
    """Request a real Plex PIN (no mock), return code + verification URL."""
    _get_services()  # ensure config_store + runtime_config exist

    headers = {
        "X-Plex-Product": "Plex Subtitle Service",
        "X-Plex-Version": "0.2.0",
        "X-Plex-Client-Identifier": "plex-subtitle-service",
        "X-Plex-Platform": "web",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.post("https://plex.tv/api/v2/pins", params={"strong": "true"})
            resp.raise_for_status()
            data = resp.json()
            pin_id = str(data.get("id"))
            code = data.get("code")
            verification_url = f"https://app.plex.tv/auth#?clientID=plex-subtitle-service&code={code}" if code else (
                data.get("authUrl")
                or data.get("auth_url")
                or data.get("verifier")
                or "https://app.plex.tv/auth"
            )
            expires_at = data.get("expiresAt")
            _pin_cache[pin_id] = {"code": code, "expires_at": expires_at}
            return {"pin_id": pin_id, "code": code, "verification_url": verification_url, "expires_at": expires_at}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plex PIN request failed: {e}")


@router.get("/plex/poll")
async def plex_pin_poll(pin_id: str = Query(...)) -> dict[str, Any]:
    config_store, subtitle_service, runtime_config = _get_services()

    cached = _pin_cache.get(pin_id)
    if not cached:
        raise HTTPException(status_code=404, detail="PIN not found")

    headers = {
        "X-Plex-Product": "Plex Subtitle Service",
        "X-Plex-Version": "0.2.0",
        "X-Plex-Client-Identifier": "plex-subtitle-service",
        "X-Plex-Platform": "web",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get(f"https://plex.tv/api/v2/pins/{pin_id}")
            resp.raise_for_status()
            # Some responses may be XML; parse fallback
            if resp.headers.get("content-type", "").startswith("application/xml"):
                root = ET.fromstring(resp.text)
                token = root.findtext("authToken")
            else:
                data = resp.json()
                token = data.get("authToken") or data.get("auth_token")
            response = {"status": "ok", "token": token, "code": cached.get("code")}
            if token:
                import app.main as main_module
                updated = runtime_config.model_copy(update={"plex_token": token})
                config_store.save(updated)
                main_module.runtime_config = updated
                if subtitle_service:
                    subtitle_service.update_runtime_config(updated)
            return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plex PIN poll failed: {e}")


@router.get("/plex/resources")
async def plex_resources(token: str | None = None) -> dict[str, Any]:
    """Fetch Plex resources to list available servers for selection."""
    _, subtitle_service, runtime_config = _get_services()
    plex_token = token or runtime_config.plex_token
    if not plex_token:
        raise HTTPException(status_code=400, detail="Missing plex_token")

    headers = {
        "X-Plex-Product": "Plex Subtitle Service",
        "X-Plex-Version": "0.2.0",
        "X-Plex-Client-Identifier": "plex-subtitle-service",
        "X-Plex-Token": plex_token,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get("https://plex.tv/api/resources?includeHttps=1")
            resp.raise_for_status()
            # Plex resources is XML
            root = ET.fromstring(resp.text)
            servers: list[dict[str, Any]] = []
            for device in root.findall("Device"):
                if device.get("provides") != "server":
                    continue
                server = {
                    "name": device.get("name"),
                    "clientIdentifier": device.get("clientIdentifier"),
                    "machineIdentifier": device.get("machineIdentifier"),
                    "owned": device.get("owned"),
                    "connections": [],
                }
                for conn in device.findall("Connection"):
                    server["connections"].append(
                        {
                            "uri": conn.get("uri"),
                            "protocol": conn.get("protocol"),
                            "local": conn.get("local"),
                            "relay": conn.get("relay"),
                        }
                    )
                servers.append(server)
            return {"servers": servers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plex resources fetch failed: {e}")
