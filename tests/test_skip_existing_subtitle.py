"""Logic bỏ qua khi media đã có subtitle target language."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.settings import SubtitleSettings
from app.services.subtitle_service import SubtitleService


def _service(**settings) -> SubtitleService:
    svc = SubtitleService.__new__(SubtitleService)
    svc.config = SimpleNamespace(subtitle_settings=SubtitleSettings(**settings))
    svc.runtime_config = SimpleNamespace(default_language="vi")
    return svc


def _details(*subs: dict) -> dict:
    return {"has_subtitle": bool(subs), "subtitle_count": len(subs), "subtitle_info": list(subs)}


EXTERNAL_SRT = {"codec": "srt", "title": "", "is_embedded": False, "forced": False}
RETAIL_SRT = {"codec": "srt", "title": "Retail WEB-DL", "is_embedded": False, "forced": False}


async def _check(svc, details, event):
    return await svc._should_download_subtitle(MagicMock(), MagicMock(), details, MagicMock(), event=event)


@pytest.mark.asyncio
async def test_skip_when_has_sub_and_no_replace() -> None:
    ok, _ = await _check(_service(skip_if_has_subtitle=True, replace_existing=False), _details(EXTERNAL_SRT), "library.new")
    assert ok is False


@pytest.mark.asyncio
async def test_replace_mode_skips_on_play() -> None:
    ok, reason = await _check(_service(skip_if_has_subtitle=True, replace_existing=True), _details(EXTERNAL_SRT), "media.play")
    assert ok is False
    assert "library.new" in reason


@pytest.mark.asyncio
async def test_replace_mode_skips_when_existing_is_retail() -> None:
    ok, _ = await _check(_service(skip_if_has_subtitle=True, replace_existing=True), _details(RETAIL_SRT), "library.new")
    assert ok is False


@pytest.mark.asyncio
async def test_replace_mode_allows_upgrade_check_on_new() -> None:
    ok, _ = await _check(_service(skip_if_has_subtitle=True, replace_existing=True), _details(EXTERNAL_SRT), "library.new")
    assert ok is True


@pytest.mark.asyncio
async def test_no_sub_downloads_on_play() -> None:
    ok, _ = await _check(_service(skip_if_has_subtitle=True, replace_existing=True), _details(), "media.play")
    assert ok is True


@pytest.mark.asyncio
async def test_upgrade_check_is_silent_when_nothing_found(monkeypatch) -> None:
    svc = _service(skip_if_has_subtitle=True, replace_existing=True, skip_if_embedded=True, skip_forced_subtitles=True)
    svc.plex_client = MagicMock()
    svc.plex_client.get_subtitle_details.return_value = _details(EXTERNAL_SRT)
    svc.plex_client.extract_metadata.return_value = SimpleNamespace(title="Movie", __str__=lambda self: "Movie")
    svc.telegram_client = MagicMock(
        notify_processing_started=AsyncMock(),
        notify_subtitle_not_found=AsyncMock(),
    )
    svc.stats = MagicMock()
    svc._find_subtitles = AsyncMock(return_value=[])
    svc._get_video_filename = lambda video: None
    svc._cleanup_temp_files = lambda key: None

    result = await svc.process_webhook("1", "library.new")

    assert result["status"] == "skipped"
    svc.telegram_client.notify_processing_started.assert_not_awaited()
    svc.telegram_client.notify_subtitle_not_found.assert_not_awaited()
