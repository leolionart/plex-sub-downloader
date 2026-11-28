"""Conservative, audio-grounded constant-offset subtitle synchronization."""

import asyncio
import base64
import json
import math
import re
import shutil
import tempfile
import io
import wave
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.clients.sync_client import SyncClientError, parse_srt_entries, write_srt_file
from app.models.runtime_config import RuntimeConfig


class AudioSyncClient:
    """Align subtitles from the selected Plex audio track, or fail closed."""

    MAX_OFFSET_MS = 180000
    MAX_DURATION_SECONDS = 4 * 3600
    SAMPLE_SECONDS = 150
    WINDOW_SECONDS = 5
    MAX_CONCURRENT_REQUESTS = 4
    MAX_SAMPLE_CUES = 200

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def availability(self, video: Any) -> dict[str, Any]:
        try:
            self._audio_source(video)
            if not self.config.openai_api_key or not self.config.openai_base_url or not self.config.openai_model:
                raise SyncClientError("AI configuration is required for audio sync")
            if not shutil.which("ffmpeg"):
                raise SyncClientError("Audio sync requires ffmpeg")
            duration = getattr(video, "duration", None)
            if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 30000 <= duration <= self.MAX_DURATION_SECONDS * 1000:
                raise SyncClientError("Audio sync requires a known duration between 30 seconds and four hours")
            return {"available": True, "source": "plex_audio", "detail": "Plex audio with AI (requires an audio-capable chat model)"}
        except SyncClientError as exc:
            return {"available": False, "source": "plex_audio", "detail": str(exc)}

    def _audio_source(self, video: Any) -> tuple[str, int]:
        media = list(getattr(video, "media", []) or [])
        if not media:
            raise SyncClientError("No accessible Plex media for audio sync")
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
        duration = video.duration
        entries = self._read_entries(target_path, duration)
        sample_seconds = min(self.SAMPLE_SECONDS, duration / 1000)
        # Opening credits and captions often contain no dialogue; sample around
        # a later cue while retaining a margin for timing offsets.
        anchor = entries[min(10, len(entries) - 1)]["start_ms"]
        sample_start_ms = max(0, min(anchor - self.MAX_OFFSET_MS, duration - sample_seconds * 1000))
        candidates = [(i, entry) for i, entry in enumerate(entries)
                      if sample_start_ms - self.MAX_OFFSET_MS <= entry["start_ms"]
                      <= sample_start_ms + sample_seconds * 1000 + self.MAX_OFFSET_MS]
        if not 3 <= len(candidates) <= self.MAX_SAMPLE_CUES:
            raise SyncClientError("Insufficient or excessive subtitle cues in the audio sample; subtitles unchanged")
        url, track = self._audio_source(video)
        token = self.config.plex_token
        if "\r" in token or "\n" in token:
            raise SyncClientError("Invalid Plex token")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix="audio-sync-", dir=output_path.parent) as tmp:
                audio_path = Path(tmp) / "sample.wav"
                process = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-rw_timeout", "30000000", "-headers", f"X-Plex-Token: {token}\r\n",
                    "-ss", str(sample_start_ms / 1000), "-i", url, "-map", f"0:a:{track}", "-vn", "-sn", "-dn",
                    "-t", str(sample_seconds), "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(audio_path),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=120)
                    except asyncio.TimeoutError as exc:
                        raise SyncClientError("Plex audio extraction timed out; subtitles unchanged") from exc
                finally:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        await process.wait()
                if process.returncode:
                    raise SyncClientError("Cannot extract Plex audio; subtitles unchanged")
                with wave.open(str(audio_path), "rb") as wav:
                    if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000
                            or abs(wav.getnframes() / 16000 - sample_seconds) > 2):
                        raise SyncClientError("Plex audio sample is incomplete or invalid; subtitles unchanged")
                    windows = []
                    frames_per_window = self.WINDOW_SECONDS * 16000
                    for start_frame in range(0, wav.getnframes(), frames_per_window):
                        frames = wav.readframes(frames_per_window)
                        if len(frames) < 16000 * 2:
                            break
                        start_ms = sample_start_ms + start_frame / 16
                        end_ms = start_ms + len(frames) / 32
                        nearby = [(i, entry) for i, entry in candidates
                                  if entry["end_ms"] >= start_ms - self.MAX_OFFSET_MS
                                  and entry["start_ms"] <= end_ms + self.MAX_OFFSET_MS]
                        if not nearby:
                            continue
                        buffer = io.BytesIO()
                        with wave.open(buffer, "wb") as segment:
                            segment.setnchannels(1)
                            segment.setsampwidth(2)
                            segment.setframerate(16000)
                            segment.writeframes(frames)
                        windows.append((start_ms, end_ms, nearby, base64.b64encode(buffer.getvalue()).decode("ascii")))
                semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)
                async def analyze(window: tuple) -> tuple:
                    start_ms, end_ms, nearby, audio = window
                    async with semaphore:
                        result = await self._request_alignment(audio, nearby, (end_ms - start_ms) / 1000)
                    return start_ms, end_ms, nearby, result
                tasks = [asyncio.create_task(analyze(window)) for window in windows]
                try:
                    results = await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                return self._apply_alignment(results, entries, output_path, duration)
        except (OSError, ValueError, wave.Error) as exc:
            raise SyncClientError("Audio synchronization failed; subtitles unchanged") from exc
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

    @staticmethod
    def _read_entries(target_path: Path, duration_ms: float) -> list[dict[str, Any]]:
        entries = parse_srt_entries(target_path)
        blocks = [block for block in re.split(r"\n\s*\n", target_path.read_text(encoding="utf-8-sig").strip()) if block.strip()]
        if len(entries) != len(blocks) or len(entries) < 3:
            raise SyncClientError("Malformed or sparse subtitles; subtitles unchanged")
        previous = -1
        for entry in entries:
            start, end = entry["start_ms"], entry["end_ms"]
            if start < 0 or start < previous or end <= start or end > duration_ms + AudioSyncClient.MAX_OFFSET_MS:
                raise SyncClientError("Invalid subtitle timing; subtitles unchanged")
            previous = start
        return entries

    async def _request_alignment(self, audio: str, candidates: list[tuple[int, dict[str, Any]]], sample_seconds: float) -> Any:
        cues = [{"id": i, "text": e["text"][:500]} for i, e in candidates]
        prompt = (
            "Listen to the attached short audio clip and match clearly spoken dialogue to the provided "
            "subtitle cues, translating if needed. Return only exact, unambiguous matches; a cue may "
            "be partially audible. Do not infer matches from context or order. For each match return "
            "its id, a brief verbatim audio transcript, and confidence from 0 to 1. Do not report times. "
            'Return JSON only: {"matches":[{"id":0,"transcript":"...","confidence":0.0}]}. '
            "Return an empty array if no dialogue clearly matches. Clip duration in seconds: " + str(sample_seconds)
            + ". Subtitle cues: " + json.dumps(cues, ensure_ascii=False)
        )
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self.config.openai_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.openai_api_key}"},
                    json={"model": self.config.openai_model, "temperature": 0,
                          "messages": [{"role": "user", "content": [
                              {"type": "text", "text": prompt},
                              {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}},
                          ]}]},
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                if not isinstance(content, str) or len(content) > 50000:
                    raise ValueError("Invalid response")
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
                return json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise SyncClientError("AI audio analysis failed or is unsupported by the configured endpoint/model; subtitles unchanged") from exc

    def _apply_alignment(self, results: list[tuple], entries: list[dict[str, Any]],
                         output_path: Path, duration_ms: float) -> dict[str, Any]:
        # Each match gives an interval, not a model-invented timestamp: the cue
        # and clip must overlap after the shift. Intersect independent matches.
        lower, upper = -self.MAX_OFFSET_MS, self.MAX_OFFSET_MS
        matched_cues: set[int] = set()
        matched_windows: set[int] = set()
        for window_index, (start_ms, end_ms, candidates, result) in enumerate(results):
            if not isinstance(result, dict) or not isinstance(result.get("matches"), list) or len(result["matches"]) > self.MAX_SAMPLE_CUES:
                raise SyncClientError("Invalid AI audio alignment; subtitles unchanged")
            by_id = dict(candidates)
            seen: set[int] = set()
            for match in result["matches"]:
                if not isinstance(match, dict):
                    raise SyncClientError("Invalid AI audio alignment; subtitles unchanged")
                cue_id, confidence, transcript = match.get("id"), match.get("confidence"), match.get("transcript")
                if (type(cue_id) is not int or cue_id not in by_id or cue_id in seen
                        or type(confidence) not in (int, float) or not math.isfinite(confidence)
                        or not 0.9 <= confidence <= 1 or not isinstance(transcript, str) or not transcript.strip()):
                    raise SyncClientError("Invalid or uncertain AI audio match; subtitles unchanged")
                seen.add(cue_id)
                matched_cues.add(cue_id)
                matched_windows.add(window_index)
                lower = max(lower, start_ms - by_id[cue_id]["end_ms"])
                upper = min(upper, end_ms - by_id[cue_id]["start_ms"])
        if len(matched_cues) < 3 or len(matched_windows) < 3:
            raise SyncClientError("Too few independent AI audio matches; subtitles unchanged")
        if lower > upper or upper - lower > 3000 or lower <= -self.MAX_OFFSET_MS or upper >= self.MAX_OFFSET_MS:
            raise SyncClientError("AI audio matches disagree or are ambiguous; subtitles unchanged")
        offset_ms = round((lower + upper) / 2)
        shifted = []
        for entry in entries:
            updated = dict(entry)
            updated["start_ms"] += offset_ms
            updated["end_ms"] += offset_ms
            if updated["start_ms"] < 0 or updated["end_ms"] > duration_ms + 1000:
                raise SyncClientError("Audio offset would move cues outside the video; subtitles unchanged")
            shifted.append(updated)
        write_srt_file(shifted, output_path)
        return {"method": "plex_audio", "anchors_found": len(matched_cues), "avg_offset_ms": offset_ms,
                "total_entries": len(entries), "synced_entries": len(entries),
                "description": "AI dialogue matches in separate audio windows constrain a constant offset"}
