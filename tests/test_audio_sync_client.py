"""AI audio alignment regressions without a Plex or AI connection."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.clients.audio_sync_client import AudioSyncClient
from app.clients.sync_client import SyncClientError, parse_srt_entries, write_srt_file
from app.services.subtitle_service import SubtitleService


def entries():
    return [{"index": i + 1, "start_ms": 10000 + i * 6000,
             "end_ms": 12000 + i * 6000, "text": f"Dialogue {i}"} for i in range(4)]


def matches():
    return {"matches": [{"id": 0, "transcript": "Dialogue 0", "confidence": 0.99}]}


def window_results():
    candidates = list(enumerate(entries()))
    return [(start, start + 5000, candidates,
             {"matches": [{"id": cue, "transcript": f"Dialogue {cue}", "confidence": 0.99}]})
            for start, cue in [(10000, 0), (20000, 1), (25000, 2)]]


def test_ai_alignment_shifts_all_cues(tmp_path):
    source = entries()
    out = tmp_path / "out.srt"
    result = AudioSyncClient(SimpleNamespace())._apply_alignment(window_results(), source, out, 90000)
    assert result["avg_offset_ms"] == 3500
    assert [e["start_ms"] for e in parse_srt_entries(out)] == [e["start_ms"] + 3500 for e in source]
    assert [e["text"] for e in parse_srt_entries(out)] == [e["text"] for e in source]


@pytest.mark.parametrize("change", ["few", "disagree", "duplicate", "confidence", "unknown", "empty_transcript", "ambiguous"])
def test_ai_alignment_rejects_unreliable_matches(tmp_path, change):
    source = entries()
    data = window_results()
    if change == "few":
        data = data[:2]
    elif change == "disagree":
        start, end, candidates, result = data[0]
        data[0] = (start + 20000, end + 20000, candidates, result)
    elif change == "duplicate":
        data[0][3]["matches"].append(data[0][3]["matches"][0])
    elif change == "confidence":
        data[0][3]["matches"][0]["confidence"] = 0.5
    elif change == "unknown":
        data[0][3]["matches"][0]["id"] = 99
    elif change == "empty_transcript":
        data[0][3]["matches"][0]["transcript"] = ""
    else:
        data = [(e["start_ms"], e["start_ms"] + 5000, list(enumerate(source)),
                 {"matches": [{"id": i, "transcript": "speech", "confidence": 0.99}]})
                for i, e in enumerate(source[:3])]
    output = tmp_path / "out.srt"
    with pytest.raises(SyncClientError):
        AudioSyncClient(SimpleNamespace())._apply_alignment(data, source, output, 90000)
    assert not output.exists()


@pytest.mark.asyncio
async def test_audio_request_uses_configured_chat_model(monkeypatch):
    payloads = []
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            payloads.append((url, kwargs))
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(matches())}}]},
                                  request=httpx.Request("POST", url))
    monkeypatch.setattr("app.clients.audio_sync_client.httpx.AsyncClient", Client)
    client = AudioSyncClient(SimpleNamespace(openai_api_key="secret", openai_base_url="https://example.test/v1/", openai_model="audio-model"))
    assert await client._request_alignment("base64audio", list(enumerate(entries())), 90) == matches()
    url, kwargs = payloads[0]
    assert url == "https://example.test/v1/chat/completions"
    assert kwargs["json"]["model"] == "audio-model"
    assert kwargs["json"]["messages"][0]["content"][1]["input_audio"] == {"data": "base64audio", "format": "wav"}


def test_plex_audio_selects_selected_track_and_rejects_multipart():
    config = SimpleNamespace(plex_url="http://plex:32400", plex_token="secret")
    client = AudioSyncClient(config)
    part = SimpleNamespace(key="/library/parts/1/file.mkv", streams=[SimpleNamespace(streamType=2, selected=False), SimpleNamespace(streamType=2, selected=True)])
    video = SimpleNamespace(media=[SimpleNamespace(parts=[part])])
    assert client._audio_source(video) == ("http://plex:32400/library/parts/1/file.mkv", 1)
    video.media[0].parts.append(part)
    with pytest.raises(SyncClientError):
        client._audio_source(video)


@pytest.mark.asyncio
async def test_failed_automatic_audio_sync_never_uploads_or_deletes(tmp_path):
    service = SubtitleService.__new__(SubtitleService)
    service.temp_dir = tmp_path
    service.runtime_config = SimpleNamespace(default_language="vi", openai_model="audio-model")
    service.audio_sync_client = SimpleNamespace(sync_subtitles=AsyncMock(side_effect=SyncClientError("Unreliable audio")))
    service.plex_client = SimpleNamespace(upload_subtitle=Mock(), remove_external_subtitles=Mock())
    service.add_sync_history_entry = Mock()
    metadata = SimpleNamespace(rating_key="123")
    assert await service._try_sync_timing(SimpleNamespace(), metadata, Path("target.srt"), Mock()) is None
    service.plex_client.upload_subtitle.assert_not_called()
    service.plex_client.remove_external_subtitles.assert_not_called()


@pytest.mark.asyncio
async def test_successful_sync_upload_does_not_delete_existing(tmp_path):
    service = SubtitleService.__new__(SubtitleService)
    service.runtime_config = SimpleNamespace(default_language="vi")
    service.plex_client = SimpleNamespace(upload_subtitle=Mock(return_value=True), remove_external_subtitles=Mock())
    service._invalidate_media_cache = Mock()
    await service._upload_synced_subtitle(SimpleNamespace(), tmp_path / "synced.srt", Mock())
    service.plex_client.upload_subtitle.assert_called_once()
    service.plex_client.remove_external_subtitles.assert_not_called()
