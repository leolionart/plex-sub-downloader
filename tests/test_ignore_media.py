"""Bỏ qua tìm sub chủ động + bỏ qua phim có audio cùng ngôn ngữ đích."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.clients.plex_client import PlexClient
from app.main import app
from app.models.runtime_config import RuntimeConfig
from app.models.settings import SubtitleSettings
from app.services.subtitle_service import SubtitleService


def _service(**settings) -> SubtitleService:
    svc = SubtitleService.__new__(SubtitleService)
    svc.config = SimpleNamespace(subtitle_settings=SubtitleSettings(**settings))
    svc.runtime_config = SimpleNamespace(default_language="vi", subtitle_settings=svc.config.subtitle_settings)
    svc.plex_client = MagicMock()
    svc.plex_client.has_audio_language.return_value = False
    return svc


def _episode(rk="3001", season="2001", show="1001"):
    return SimpleNamespace(ratingKey=rk, parentRatingKey=season, grandparentRatingKey=show)


async def _check(svc, video, event="library.new"):
    details = {"has_subtitle": False, "subtitle_count": 0, "subtitle_info": []}
    return await svc._should_download_subtitle(video, MagicMock(), details, MagicMock(), event=event)


# ── Danh sách bỏ qua ─────────────────────────────────────────

def test_ignore_show_applies_to_every_episode() -> None:
    svc = _service()
    svc.set_media_ignored([{"rating_key": "1001", "title": "Phim Việt", "type": "show", "section_key": "2"}], True)
    assert svc.is_media_ignored(video=_episode()) is True
    assert svc.is_media_ignored("1001") is True
    assert svc.is_media_ignored(video=_episode(show="9999")) is False

    svc.set_media_ignored([{"rating_key": "1001"}], False)
    assert svc.is_media_ignored(video=_episode()) is False


def test_ignore_metadata_is_sanitized() -> None:
    svc = _service()
    current = svc.set_media_ignored([{"rating_key": " 42 ", "title": "x" * 1000, "year": "1999", "section_key": 1}], True)
    assert set(current) == {"42"}
    assert len(current["42"]["title"]) == 300
    assert current["42"]["year"] is None  # chỉ nhận int
    assert current["42"]["section_key"] == "1"


@pytest.mark.asyncio
async def test_webhook_skips_ignored_media() -> None:
    svc = _service(ignored_media={"1001": {"title": "Show", "section_key": "2"}})
    ok, reason = await _check(svc, _episode())
    assert ok is False
    assert "ignore" in reason


# ── Phim có audio cùng ngôn ngữ đích ─────────────────────────

@pytest.mark.asyncio
async def test_webhook_skips_native_language_audio() -> None:
    svc = _service(skip_native_language=True)
    svc.plex_client.has_audio_language.return_value = True
    ok, reason = await _check(svc, _episode())
    assert ok is False
    assert "target language" in reason
    svc.plex_client.has_audio_language.assert_called_with(_episode(), "vi")


@pytest.mark.asyncio
async def test_native_language_check_can_be_disabled() -> None:
    svc = _service(skip_native_language=False)
    svc.plex_client.has_audio_language.return_value = True
    ok, _ = await _check(svc, _episode())
    assert ok is True


def _stream(stream_type, code):
    return SimpleNamespace(streamType=stream_type, languageTag=None, languageCode=code, language=None)


def test_has_audio_language_checks_audio_tracks_only() -> None:
    client = PlexClient(RuntimeConfig(), mock_mode=True)
    part = SimpleNamespace(streams=[_stream(2, "eng"), _stream(3, "vie")])
    video = SimpleNamespace(media=[SimpleNamespace(parts=[part])])
    assert client.has_audio_language(video, "vi") is False  # chỉ sub tiếng Việt, audio tiếng Anh

    part.streams.append(_stream(2, "vie"))  # thêm audio lồng tiếng Việt
    assert client.has_audio_language(video, "vi") is True
    assert client.has_audio_language(SimpleNamespace(media=None), "vi") is False


def _fake_section(search_impl):
    section = MagicMock()
    section.title = "Movies"
    section.type = "movie"
    section.totalSize = 10
    section.search.side_effect = search_impl
    return section


def test_section_items_exclude_native_language_server_side() -> None:
    client = PlexClient(RuntimeConfig(), mock_mode=True)
    client._mock_mode = False
    calls = []

    def search(**kwargs):
        calls.append(kwargs.get("filters"))
        filters = kwargs.get("filters") or {}
        if filters == {"audioLanguage": "vi"}:
            return [SimpleNamespace(ratingKey=7), SimpleNamespace(ratingKey=8)]
        if filters.get("subtitleLanguage") == "vi":
            return [SimpleNamespace(ratingKey=8)]
        if filters.get("subtitleLanguage") == "en":
            return []
        return []

    section = _fake_section(search)
    client._server = MagicMock()
    client._server.library.sectionByID.return_value = section

    data = client.get_section_items("1", subtitle_filter="missing_vi", use_cache=False, exclude_audio_language="vi")
    page_filters = next(f for f in calls if f and "subtitleLanguage!" in f)
    assert page_filters["audioLanguage!"] == "vi"
    # 10 tổng - 1 có Vietsub - 1 phim tiếng Việt chưa có sub (key 7; key 8 đã tính ở has_vi)
    assert data["stats"]["missing_vi"] == 8
    assert data["stats"]["native_language"] == 1


def test_section_items_fall_back_when_audio_filter_unsupported() -> None:
    client = PlexClient(RuntimeConfig(), mock_mode=True)
    client._mock_mode = False

    def search(**kwargs):
        filters = kwargs.get("filters") or {}
        if "audioLanguage!" in filters:
            raise ValueError("Unknown filter field audioLanguage")
        return []

    section = _fake_section(search)
    client._server = MagicMock()
    client._server.library.sectionByID.return_value = section
    data = client.get_section_items("1", subtitle_filter="missing_vi", use_cache=False, exclude_audio_language="vi")
    assert data["items"] == []
    assert "native_language" in data["stats"]


# ── Danh sách Thư viện ───────────────────────────────────────

def test_library_hides_ignored_from_missing_filter_and_lists_them() -> None:
    svc = _service(ignored_media={"2": {"title": "Bố Già", "type": "movie", "year": 2021, "section_key": "1"}})
    svc.plex_client.get_section_items.return_value = {
        "section_key": "1", "total_size": 2,
        "stats": {"total": 10, "has_vi": 3, "missing_vi": 7, "can_translate": 1},
        "items": [
            {"rating_key": "1", "title": "A", "has_vi": False, "can_translate": True},
            {"rating_key": "2", "title": "Bố Già", "has_vi": False, "can_translate": True},
        ],
    }
    data = svc.get_library_items("1", subtitle_filter="missing_vi")
    assert [i["rating_key"] for i in data["items"]] == ["1"]
    assert data["stats"]["missing_vi"] == 6
    assert data["stats"]["ignored"] == 1
    assert svc.plex_client.get_section_items.call_args.kwargs["exclude_audio_language"] == "vi"

    ignored = svc.get_library_items("1", subtitle_filter="ignored")
    assert [(i["rating_key"], i["title"], i["ignored"]) for i in ignored["items"]] == [("2", "Bố Già", True)]


# ── API ──────────────────────────────────────────────────────

@pytest.fixture
def api_service(monkeypatch):
    svc = MagicMock()
    svc.is_translation_skipped.return_value = False
    svc.set_media_ignored.return_value = {"5": {}}
    monkeypatch.setattr("app.main.subtitle_service", svc)
    monkeypatch.setattr("app.main.config_store", MagicMock())
    monkeypatch.setattr("app.main.runtime_config", RuntimeConfig())
    return svc


@pytest.mark.asyncio
async def test_ignore_api_roundtrip(api_service) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/library/ignored", json={"section_key": "1", "items": [{"rating_key": "5", "title": "Phim", "type": "movie", "year": 1990}]})
        assert resp.status_code == 200
        items = api_service.set_media_ignored.call_args.args[0]
        assert items[0]["rating_key"] == "5" and items[0]["section_key"] == "1"
        assert api_service.set_media_ignored.call_args.args[1] is True

        resp = await client.delete("/api/library/ignored/5")
        assert resp.status_code == 200
        assert api_service.set_media_ignored.call_args.args == ([{"rating_key": "5"}], False)


@pytest.mark.asyncio
async def test_batch_actions_reject_when_everything_ignored(api_service) -> None:
    api_service.is_media_ignored.return_value = True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/api/library/batch/upload-target", "/api/library/batch/translate"):
            resp = await client.post(path, json={"rating_keys": ["5"]})
            assert resp.status_code == 400


@pytest.mark.asyncio
async def test_batch_translate_creates_task(api_service) -> None:
    """Regression: endpoint dùng task_id chưa được tạo (NameError)."""
    api_service.is_media_ignored.return_value = False
    api_service.plex_client.get_item.return_value = SimpleNamespace(type="movie", ratingKey="5")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/library/batch/translate", json={"rating_keys": ["5"]})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_saving_settings_keeps_fields_not_in_form(monkeypatch) -> None:
    """Regression: lưu form Cài đặt từng reset ignored_media, translation_skip... về mặc định."""
    rc = RuntimeConfig(default_language="vi")
    current = SubtitleSettings(
        ignored_media={"9": {"title": "X"}},
        translation_skip_rating_keys=["3"],
        translation_context_enabled=True,
    )
    svc = MagicMock()
    svc.get_config.return_value = SimpleNamespace(subtitle_settings=current)
    monkeypatch.setattr("app.main.subtitle_service", svc)
    monkeypatch.setattr("app.main.config_store", MagicMock())
    monkeypatch.setattr("app.main.runtime_config", rc)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/settings", json={"default_language": "vi", "skip_native_language": False, "auto_download_on_play": False})
    assert resp.status_code == 200
    saved: SubtitleSettings = svc.update_settings.call_args.args[0]
    assert saved.ignored_media == {"9": {"title": "X"}}
    assert saved.translation_skip_rating_keys == ["3"]
    assert saved.translation_context_enabled is True
    assert saved.skip_native_language is False
    assert saved.auto_download_on_play is False
