"""Conservative, audio-grounded constant-offset subtitle synchronization."""

import asyncio
import math
import shutil
import wave
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.clients.sync_client import SyncClientError, parse_srt_entries, write_srt_file
from app.models.runtime_config import RuntimeConfig


class AudioSyncClient:
    """Use Plex's selected audio track, never a subtitle release as timing truth.

    Only constant offsets are supported. Independent sections must agree; edits,
    drift, ambiguous speech patterns and sparse subtitles fail closed.
    """

    FRAME_MS = 20
    MAX_OFFSET_MS = 60000
    MAX_DURATION_SECONDS = 4 * 3600

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def availability(self, video: Any) -> dict[str, Any]:
        try:
            self._audio_source(video)
            if not shutil.which("ffmpeg"):
                raise SyncClientError("Audio sync requires ffmpeg")
            duration = getattr(video, "duration", None)
            if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 30000 <= duration <= self.MAX_DURATION_SECONDS * 1000:
                raise SyncClientError("Audio sync requires a known duration between 30 seconds and four hours")
            import numpy  # noqa: F401
            import webrtcvad  # noqa: F401
            return {"available": True, "source": "plex_audio", "detail": "Plex audio (constant-offset alignment)"}
        except (SyncClientError, ImportError) as exc:
            return {"available": False, "source": "plex_audio", "detail": str(exc)}

    def _audio_source(self, video: Any) -> tuple[str, int]:
        media = list(getattr(video, "media", []) or [])
        if not media:
            raise SyncClientError("No accessible Plex media for audio sync")
        # Plex's first media version is also used by subtitle download/upload.
        parts = list(getattr(media[0], "parts", []) or [])
        if len(parts) != 1:
            raise SyncClientError("Audio sync requires a single-part Plex media version")
        part = parts[0]
        key = getattr(part, "key", None)
        if not isinstance(key, str) or not key.startswith("/library/parts/"):
            raise SyncClientError("Plex media has no accessible audio part URL")
        parsed_key = urlsplit(key)
        if parsed_key.scheme or parsed_key.netloc or parsed_key.query or parsed_key.fragment:
            raise SyncClientError("Unsupported Plex audio part URL")
        tracks = [s for s in getattr(part, "streams", []) if getattr(s, "streamType", None) == 2]
        if not tracks:
            raise SyncClientError("No audio track found on Plex media")
        selected = next((i for i, track in enumerate(tracks) if getattr(track, "selected", False)), 0)
        base = urlsplit(str(self.config.plex_url).rstrip("/"))
        if base.scheme not in {"http", "https"} or not base.netloc or not self.config.plex_token:
            raise SyncClientError("Plex credentials are required for audio sync")
        return str(self.config.plex_url).rstrip("/") + key, selected

    async def sync_subtitles(self, video: Any, target_path: Path, output_path: Path) -> dict[str, Any]:
        status = self.availability(video)
        if not status["available"]:
            raise SyncClientError(status["detail"])
        duration = getattr(video, "duration", None)
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 30000 <= duration <= self.MAX_DURATION_SECONDS * 1000:
            raise SyncClientError("Audio sync requires a known duration between 30 seconds and four hours")
        url, track = self._audio_source(video)
        token = self.config.plex_token
        if "\r" in token or "\n" in token:
            raise SyncClientError("Invalid Plex token")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path = output_path.with_suffix(".audio.wav")
        # No shell, token-bearing URLs or stderr logging.
        # ffmpeg reads the Plex part directly; no local media mount is necessary.
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-rw_timeout", "30000000", "-headers", f"X-Plex-Token: {token}\r\n",
                "-i", url, "-map", f"0:a:{track}", "-vn", "-sn", "-dn",
                "-t", str(duration / 1000 + 1), "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(audio_path),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=600)
            except asyncio.TimeoutError as exc:
                raise SyncClientError("Plex audio extraction timed out; subtitles unchanged") from exc
            if process.returncode:
                raise SyncClientError("Cannot extract Plex audio; subtitles unchanged")
            return await asyncio.to_thread(self._align, audio_path, target_path, output_path, duration)
        except (OSError, ValueError, wave.Error) as exc:
            raise SyncClientError("Audio synchronization could not start; subtitles unchanged") from exc
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            audio_path.unlink(missing_ok=True)

    @staticmethod
    def _best_offset(audio: Any, subtitles: Any, max_lag: int) -> tuple[int, float, float]:
        import numpy as np

        n = len(audio)
        max_lag = min(max_lag, n // 4)
        size = 1 << (2 * n - 1).bit_length()
        correlation = np.fft.irfft(np.fft.rfft(audio, size) * np.fft.rfft(subtitles[::-1], size), size)
        lags = np.arange(-max_lag, max_lag + 1)
        lower = np.maximum(0, lags)
        upper = np.minimum(n, n + lags)
        count = upper - lower
        ap = np.concatenate(([0.0], np.cumsum(audio)))
        sp = np.concatenate(([0.0], np.cumsum(subtitles)))
        asum = ap[upper] - ap[lower]
        ssum = sp[upper - lags] - sp[lower - lags]
        variance = asum * (1 - asum / count) * ssum * (1 - ssum / count)
        scores = np.full(len(lags), -1.0)
        valid = variance > 0
        scores[valid] = (correlation[n - 1 + lags[valid]] - asum[valid] * ssum[valid] / count[valid]) / np.sqrt(variance[valid])
        best = int(np.argmax(scores))
        alternatives = scores[np.abs(lags - lags[best]) > 50]  # exclude the same one-second peak
        runner_up = float(np.max(alternatives)) if len(alternatives) else -1.0
        return int(lags[best]), float(scores[best]), float(scores[best]) - runner_up

    def _align(self, audio_path: Path, target_path: Path, output_path: Path, duration_ms: float) -> dict[str, Any]:
        import numpy as np
        import webrtcvad

        entries = parse_srt_entries(target_path)
        blocks = [block for block in re.split(r"\n\s*\n", target_path.read_text(encoding="utf-8-sig").strip()) if block.strip()]
        if len(entries) != len(blocks):
            raise SyncClientError("Malformed subtitle cues; subtitles unchanged")
        if len(entries) < 20:
            raise SyncClientError("Too few subtitle cues for reliable audio sync")
        with wave.open(str(audio_path), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                raise SyncClientError("Unsupported extracted audio format")
            frames = wav.getnframes() // 320
            if abs(frames * self.FRAME_MS - duration_ms) > max(2000, duration_ms * 0.01):
                raise SyncClientError("Plex audio is incomplete; subtitles unchanged")
            vad = webrtcvad.Vad(2)
            audio = np.fromiter((vad.is_speech(wav.readframes(320), 16000) for _ in range(frames)), dtype=np.float64, count=frames)
        return self._align_activity(audio, entries, target_path, output_path, duration_ms)

    def _align_activity(self, audio: Any, entries: list[dict[str, Any]], target_path: Path,
                        output_path: Path, duration_ms: float) -> dict[str, Any]:
        import numpy as np

        frames = len(audio)
        if len(entries) < 20 or frames < 1500:
            raise SyncClientError("Too few subtitle cues or audio frames for reliable audio sync")
        subtitles = np.zeros(frames, dtype=np.float64)
        previous = -1
        for entry in entries:
            start, end = entry["start_ms"], entry["end_ms"]
            if start < 0 or start < previous or end <= start or end > duration_ms + self.MAX_OFFSET_MS:
                raise SyncClientError("Invalid subtitle timing; subtitles unchanged")
            previous = start
            subtitles[max(0, start // self.FRAME_MS):min(frames, (end + self.FRAME_MS - 1) // self.FRAME_MS)] = 1
        if not 0.02 <= float(audio.mean()) <= 0.85 or not 0.02 <= float(subtitles.mean()) <= 0.85:
            raise SyncClientError("Insufficient speech/silence contrast for reliable audio sync")
        lag, score, margin = self._best_offset(audio, subtitles, self.MAX_OFFSET_MS // self.FRAME_MS)
        if score < 0.15 or margin < 0.015 or abs(lag * self.FRAME_MS) >= self.MAX_OFFSET_MS - 1000:
            raise SyncClientError("Audio alignment is weak or ambiguous; subtitles unchanged")
        # All three distributed sections must independently support the offset.
        # This rejects common wrong-cut, drift and sparse-dialogue false matches.
        for a, s in zip(np.array_split(audio, 3), np.array_split(subtitles, 3)):
            local_lag, local_score, local_margin = self._best_offset(a, s, self.MAX_OFFSET_MS // self.FRAME_MS)
            if local_score < 0.10 or local_margin < 0.005 or abs(local_lag - lag) * self.FRAME_MS > 600:
                raise SyncClientError("Audio alignment differs across the video; subtitles unchanged")
        offset_ms = lag * self.FRAME_MS
        shifted = []
        for entry in entries:
            updated = dict(entry)
            updated["start_ms"] += offset_ms
            updated["end_ms"] += offset_ms
            if updated["start_ms"] < 0 or updated["end_ms"] > duration_ms + 1000:
                raise SyncClientError("Audio offset would move cues outside the video; subtitles unchanged")
            shifted.append(updated)
        write_srt_file(shifted, output_path)
        return {"method": "plex_audio", "anchors_found": 3, "avg_offset_ms": offset_ms,
                "confidence": round(score, 4), "peak_margin": round(margin, 4),
                "total_entries": len(entries), "synced_entries": len(entries),
                "description": "WebRTC speech correlation; three independent sections agree"}
