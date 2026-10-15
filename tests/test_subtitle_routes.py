"""Focused tests for the public manual subtitle-search request contract."""

import pytest
from pydantic import ValidationError

from app.models.subtitle import SubtitleSearchParams
from app.routes.subtitles import SubtitleSearchRequest, _search_params_from_override


def test_search_request_requires_media_reference() -> None:
    """A manual search must identify media by key or searchable metadata."""
    with pytest.raises(ValidationError):
        SubtitleSearchRequest(language="vi")


def test_search_request_accepts_filename_only_with_title() -> None:
    request = SubtitleSearchRequest(
        title="From",
        season=3,
        episode=1,
        video_filename="From.S03E01.1080p.WEB-DL.mkv",
        language="vi",
    )

    assert request.title == "From"
    assert request.video_filename.endswith(".mkv")


def test_search_override_preserves_explicit_language_and_matching_fields() -> None:
    request = SubtitleSearchRequest(
        title="From",
        year=2024,
        season=3,
        episode=1,
        video_filename="From.S03E01.mkv",
        language="en",
    )

    params = _search_params_from_override(request, language="vi")

    assert params == SubtitleSearchParams(
        language="en",
        title="From",
        year=2024,
        season=3,
        episode=1,
        video_filename="From.S03E01.mkv",
    )


def test_subtitle_translate_request() -> None:
    from app.routes.subtitles import SubtitleTranslateRequest

    req = SubtitleTranslateRequest(rating_key="12345")
    assert req.rating_key == "12345"
    assert req.from_lang == "en"
    assert req.use_cache is True


@pytest.mark.asyncio
async def test_translate_endpoint(monkeypatch) -> None:
    from unittest.mock import AsyncMock, MagicMock
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    mock_service = MagicMock()
    mock_service.runtime_config.ai_available = True
    mock_service.execute_translate_for_media = AsyncMock(
        return_value={"status": "success", "message": "Translated"}
    )
    monkeypatch.setattr("app.main.subtitle_service", mock_service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/subtitles/translate", json={"rating_key": "12345"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "success", "message": "Translated"}
