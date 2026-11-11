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


def test_chunked_correlation_matches_full_fft_and_bounds_transform_size(monkeypatch):
    rng = np.random.default_rng(73)
    audio = rng.integers(0, 2, 70000, dtype=np.uint8)
    subtitles = np.roll(audio, -175)
    max_lag = 300
    n = len(audio)
    size = 1 << (2 * n - 1).bit_length()
    correlation = np.fft.irfft(np.fft.rfft(audio, size) * np.fft.rfft(subtitles[::-1], size), size)
    lags = np.arange(-max_lag, max_lag + 1)
    lower, upper = np.maximum(0, lags), np.minimum(n, n + lags)
    count = upper - lower
    ap = np.concatenate(([0.0], np.cumsum(audio)))
    sp = np.concatenate(([0.0], np.cumsum(subtitles)))
    asum, ssum = ap[upper] - ap[lower], sp[upper - lags] - sp[lower - lags]
    scores = (correlation[n - 1 + lags] - asum * ssum / count) / np.sqrt(
        asum * (1 - asum / count) * ssum * (1 - ssum / count))
    best = int(np.argmax(scores))
    expected = (lags[best], scores[best], scores[best] - np.max(scores[np.abs(lags - lags[best]) > 50]))
    sizes = []
    original = np.fft.rfft

    def recording_rfft(values, n):
        sizes.append(n)
        return original(values, n)

    monkeypatch.setattr(np.fft, "rfft", recording_rfft)
    assert AudioSyncClient._best_offset(audio, subtitles, max_lag) == pytest.approx(expected)
    assert max(sizes) <= 65536
    assert len(sizes) > 2


@pytest.mark.parametrize("failure", [False, True])
def test_alignment_collects_memory_after_each_job(monkeypatch, tmp_path, failure):
    client = AudioSyncClient(SimpleNamespace())
    collect = Mock()
    monkeypatch.setattr("app.clients.audio_sync_client.gc.collect", collect)
    result = {"ok": True}
    align = Mock(side_effect=SyncClientError("failed") if failure else None, return_value=result)
    monkeypatch.setattr(client, "_align_audio", align)
    args = (tmp_path / "audio.wav", tmp_path / "source.srt", tmp_path / "out.srt", 30000)
    if failure:
        with pytest.raises(SyncClientError, match="failed"):
            client._align(*args)
    else:
        assert client._align(*args) is result
    collect.assert_called_once_with()

@pytest.mark.asyncio
async def test_cancelled_analysis_keeps_audio_until_worker_exits(tmp_path, monkeypatch):
    import asyncio
    import threading

    client = AudioSyncClient(SimpleNamespace(plex_token="token"))
    monkeypatch.setattr(client, "availability", lambda video: {"available": True})
    monkeypatch.setattr(client, "_audio_source", lambda video: ("http://plex/file", 0))
    process = SimpleNamespace(returncode=0, wait=AsyncMock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    started, release = threading.Event(), threading.Event()
    output = tmp_path / "out.srt"
    audio = output.with_suffix(".audio.wav")
    audio.write_bytes(b"audio")

    def align(audio_path, target_path, output_path, duration):
        started.set()
        release.wait(5)
        assert audio_path.exists()
        return {}

    monkeypatch.setattr(client, "_align", align)
    task = asyncio.create_task(client.sync_subtitles(SimpleNamespace(duration=30000), tmp_path / "in.srt", output))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert audio.exists()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not audio.exists()



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
