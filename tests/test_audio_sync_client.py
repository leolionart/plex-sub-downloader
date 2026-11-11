"""Signal-level regressions; no Plex connection or real media is required."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from app.clients.audio_sync_client import AudioSyncClient
from app.clients.sync_client import SyncClientError, parse_srt_entries, write_srt_file
from app.services.subtitle_service import SubtitleService


def signal_fixture():
    rng = np.random.default_rng(47)
    frames = 45000  # fifteen minutes at 20ms resolution
    activity = np.zeros(frames)
    entries = []
    start = 500
    while start < frames - 800:
        length = int(rng.integers(35, 160))
        entries.append({"start_ms": start * 20, "end_ms": (start + length) * 20,
                        "text": f"Dialogue {len(entries)}", "index": len(entries) + 1})
        activity[start:start + length] = 1
        start += length + int(rng.integers(30, 230))
    return activity, entries


@pytest.mark.parametrize("lag", [-175, 0, 225])
def test_audio_signal_alignment_preserves_text(tmp_path, lag):

    subtitles, entries = signal_fixture()
    audio = np.roll(subtitles, lag)
    source, output = tmp_path / "source.srt", tmp_path / "synced.srt"
    write_srt_file(entries, source)
    client = AudioSyncClient(SimpleNamespace())
    result = client._align_activity(audio, entries, source, output, len(audio) * 20)
    assert result["avg_offset_ms"] == lag * 20
    updated = parse_srt_entries(output)
    assert [e["text"] for e in updated] == [e["text"] for e in entries]
    assert [e["start_ms"] for e in updated] == [e["start_ms"] + lag * 20 for e in entries]
    assert source.read_text() != ""
def test_wav_pipeline_aligns_synthetic_voice_activity(tmp_path, monkeypatch):
    import sys
    import wave

    signal, entries = signal_fixture()
    lag = 125
    audio = np.roll(signal, lag)
    source, output, wav_path = tmp_path / "source.srt", tmp_path / "out.srt", tmp_path / "audio.wav"
    write_srt_file(entries, source)
    pcm = np.repeat(audio.astype("<i2"), 320)
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm.tobytes())
    # Synthetic VAD isolates WAV decoding and timing from classifier accuracy.
    monkeypatch.setitem(sys.modules, "webrtcvad", SimpleNamespace(
        Vad=lambda mode: SimpleNamespace(is_speech=lambda frame, rate: frame[:2] != b"\0\0")
    ))
    stats = AudioSyncClient(SimpleNamespace())._align(wav_path, source, output, len(signal) * 20)
    assert stats["avg_offset_ms"] == 2500
    assert len(parse_srt_entries(output)) == len(entries)


def test_audio_signal_rejects_silence(tmp_path):
    signal, entries = signal_fixture()
    with pytest.raises(SyncClientError, match="contrast"):
        AudioSyncClient(SimpleNamespace())._align_activity(
            np.zeros(len(signal)), entries, tmp_path / "source.srt", tmp_path / "out.srt", len(signal) * 20)
    assert not (tmp_path / "out.srt").exists()


def test_audio_signal_rejects_periodic_ambiguity(tmp_path):
    signal = np.tile(np.concatenate((np.ones(100), np.zeros(100))), 225)
    entries = [{"start_ms": i * 20, "end_ms": (i + 100) * 20, "text": "Repeated"}
               for i in range(0, len(signal), 200)]
    with pytest.raises(SyncClientError, match="ambiguous"):
        AudioSyncClient(SimpleNamespace())._align_activity(
            signal, entries, tmp_path / "source.srt", tmp_path / "out.srt", len(signal) * 20)


def test_audio_signal_rejects_inconsistent_sections(tmp_path):
    signal, entries = signal_fixture()
    audio = np.concatenate([np.roll(section, lag) for section, lag in
                            zip(np.array_split(signal, 3), [50, 200, 400])])
    with pytest.raises(SyncClientError):
        AudioSyncClient(SimpleNamespace())._align_activity(
            audio, entries, tmp_path / "source.srt", tmp_path / "out.srt", len(signal) * 20)
    assert not (tmp_path / "out.srt").exists()


def test_plex_audio_selects_selected_track_and_rejects_multipart():
    config = SimpleNamespace(plex_url="http://plex:32400", plex_token="secret")
    client = AudioSyncClient(config)
    tracks = [SimpleNamespace(streamType=2, selected=False), SimpleNamespace(streamType=2, selected=True)]
    part = SimpleNamespace(key="/library/parts/1/file.mkv", streams=tracks)
    video = SimpleNamespace(media=[SimpleNamespace(parts=[part])])
    assert client._audio_source(video) == ("http://plex:32400/library/parts/1/file.mkv", 1)
    video.media[0].parts.append(part)
    with pytest.raises(SyncClientError, match="single-part"):
        client._audio_source(video)


@pytest.mark.asyncio
async def test_failed_automatic_audio_sync_never_uploads_or_deletes(tmp_path):
    service = SubtitleService.__new__(SubtitleService)
    service.temp_dir = tmp_path
    service.runtime_config = SimpleNamespace(default_language="vi")
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
