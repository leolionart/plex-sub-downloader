import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.models.settings import ServiceConfig, SubtitleSettings
from app.models.runtime_config import RuntimeConfig


def test_should_download_on_event() -> None:
    settings = SubtitleSettings(auto_download_on_add=True, auto_download_on_play=False)
    assert settings.should_download_on_event("library.new") is True
    assert settings.should_download_on_event("library.on.deck") is True
    assert settings.should_download_on_event("media.play") is False

    settings.auto_download_on_play = True
    assert settings.should_download_on_event("media.play") is True


@pytest.mark.asyncio
async def test_get_settings_endpoint(monkeypatch) -> None:
    mock_service = MagicMock()
    mock_service.get_config.return_value = ServiceConfig(
        subtitle_settings=SubtitleSettings()
    )
    mock_service.config = mock_service.get_config()

    mock_rc = RuntimeConfig(default_language="vi")

    monkeypatch.setattr("app.main.subtitle_service", mock_service)
    monkeypatch.setattr("app.main.runtime_config", mock_rc)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "subtitle_settings" in data
        assert data["default_language"] == "vi"


@pytest.mark.asyncio
async def test_webhook_skips_disabled_playback_event(monkeypatch) -> None:
    mock_service = MagicMock()
    mock_rc = RuntimeConfig(
        default_language="vi",
        subtitle_settings=SubtitleSettings(auto_download_on_play=False),
    )

    monkeypatch.setattr("app.main.subtitle_service", mock_service)
    monkeypatch.setattr("app.main.runtime_config", mock_rc)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook",
            json={
                "event": "media.play",
                "rating_key": "12345",
                "media_type": "episode",
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
