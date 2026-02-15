"""
AI-powered subtitle timing synchronization.
Đồng bộ timing của Vietsub dựa trên mốc thời gian Engsub chuẩn kèm phim.

Approach: Anchor-Point Based Sync
1. Sample N anchor groups từ target subtitle (Vietnamese)
2. Dùng AI match mỗi nhóm với reference subtitle (English)
3. Tính piecewise-linear time mapping từ anchor points
4. Áp dụng mapping cho toàn bộ target subtitle
"""

import re
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.models.runtime_config import RuntimeConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SyncClientError(Exception):
    """Base exception for sync client errors."""
    pass


def parse_srt_time(time_str: str) -> int:
    """Parse SRT timestamp thành milliseconds.

    Format: HH:MM:SS,mmm
    """
    match = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", time_str.strip())
    if not match:
        raise ValueError(f"Invalid SRT time format: {time_str}")
    h, m, s, ms = match.groups()
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def format_srt_time(ms: int) -> str:
    """Format milliseconds thành SRT timestamp.

    Returns: HH:MM:SS,mmm
    """
    if ms < 0:
        ms = 0
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt_entries(srt_path: Path) -> list[dict[str, Any]]:
    """Parse .srt file thành list of entries.

    Returns:
        List of {index, start_ms, end_ms, timing, text}
    """
    content = srt_path.read_text(encoding="utf-8-sig")
    entries = []
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        try:
            index = int(lines[0].strip())
            timing = lines[1].strip()
            text = "\n".join(lines[2:])

            timing_match = re.match(
                r"(.+?)\s*-->\s*(.+)",
                timing,
            )
            if not timing_match:
                continue

            start_str, end_str = timing_match.groups()
            start_ms = parse_srt_time(start_str)
            end_ms = parse_srt_time(end_str)

            entries.append({
                "index": index,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "timing": timing,
                "text": text.strip(),
            })
        except (ValueError, IndexError) as e:
            logger.debug(f"Skipping malformed SRT block: {e}")
            continue

    return entries


def write_srt_file(entries: list[dict[str, Any]], output_path: Path) -> None:
    """Write entries thành .srt file."""
    lines = []
    for i, entry in enumerate(entries, 1):
        start = format_srt_time(entry["start_ms"])
        end = format_srt_time(entry["end_ms"])
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(entry["text"])
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


class SubtitleSyncClient:
    """
    AI-powered subtitle timing synchronization.

    Đồng bộ timing của Vietsub dựa trên mốc thời gian Engsub chuẩn.
    Sử dụng anchor-point approach để giảm thiểu API calls.
    """

    ANCHOR_GROUPS = 6       # Số nhóm anchor points
    ENTRIES_PER_GROUP = 4   # Entries mỗi nhóm
    SEARCH_WINDOW = 40      # Số entries English để search trong mỗi nhóm

    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config
        self.api_key = config.openai_api_key
        self.base_url = config.openai_base_url
        self.model = config.openai_model
        self.enabled = bool(self.api_key)

        self._client = httpx.AsyncClient(
            timeout=60.0,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
        )

        if self.enabled:
            logger.info(f"Subtitle sync enabled (model={self.model})")
        else:
            logger.info("Subtitle sync disabled")

    async def close(self) -> None:
        await self._client.aclose()

    async def sync_subtitles(
        self,
        reference_path: Path,
        target_path: Path,
        output_path: Path,
    ) -> dict[str, Any]:
        """
        Sync target subtitle timing dựa trên reference subtitle.

        Args:
            reference_path: English SRT (correct timing)
            target_path: Vietnamese SRT (timing cần fix)
            output_path: Output file path

        Returns:
            Dict với stats: {entries_synced, anchors_found, avg_offset_ms}
        """
        if not self.enabled:
            raise SyncClientError("Subtitle sync is disabled")

        logger.info(
            f"Syncing subtitle timing: "
            f"ref={reference_path.name} → target={target_path.name}"
        )

        ref_entries = parse_srt_entries(reference_path)
        target_entries = parse_srt_entries(target_path)

        if not ref_entries:
            raise SyncClientError("Reference subtitle is empty")
        if not target_entries:
            raise SyncClientError("Target subtitle is empty")

        logger.info(
            f"Parsed: {len(ref_entries)} reference entries, "
            f"{len(target_entries)} target entries"
        )

        # Step 1: Find anchor points using AI
        anchors = await self._find_anchor_points(ref_entries, target_entries)

        if len(anchors) < 2:
            raise SyncClientError(
                f"Not enough anchor points found ({len(anchors)}). "
                "Cannot build reliable time mapping."
            )

        logger.info(f"Found {len(anchors)} anchor points for time mapping")

        # Step 2: Build time mapping function
        time_mapping = self._build_time_mapping(anchors)

        # Step 3: Apply time correction to all target entries
        synced_entries = self._apply_time_correction(target_entries, time_mapping)

        # Step 4: Write output
        write_srt_file(synced_entries, output_path)
        logger.info(f"Synced subtitle saved to: {output_path}")

        # Calculate stats
        offsets = [a["offset_ms"] for a in anchors]
        avg_offset = sum(offsets) / len(offsets) if offsets else 0

        return {
            "entries_synced": len(synced_entries),
            "total_ref_entries": len(ref_entries),
            "total_target_entries": len(target_entries),
            "anchors_found": len(anchors),
            "avg_offset_ms": round(avg_offset),
            "min_offset_ms": min(offsets) if offsets else 0,
            "max_offset_ms": max(offsets) if offsets else 0,
            "output_file": str(output_path),
        }

    async def _find_anchor_points(
        self,
        ref_entries: list[dict],
        target_entries: list[dict],
    ) -> list[dict[str, Any]]:
        """
        Dùng AI tìm anchor points - các cặp (target_entry, ref_entry) match nhau.

        Approach:
        - Chia target entries thành N nhóm đều nhau
        - Mỗi nhóm lấy vài entries gửi cho AI
        - AI match từng target entry với reference entry dựa trên nội dung
        - Thu thập tất cả matched pairs làm anchor points
        """
        anchors = []
        target_len = len(target_entries)
        ref_len = len(ref_entries)

        # Xác định vị trí sample points
        num_groups = min(self.ANCHOR_GROUPS, max(2, target_len // 10))
        group_size = self.ENTRIES_PER_GROUP

        for group_idx in range(num_groups):
            # Vị trí sample trong target file
            center_pos = int((group_idx + 0.5) / num_groups * target_len)
            start_pos = max(0, center_pos - group_size // 2)
            end_pos = min(target_len, start_pos + group_size)

            target_sample = target_entries[start_pos:end_pos]
            if not target_sample:
                continue

            # Ước tính vị trí tương ứng trong reference file
            ratio = center_pos / target_len
            ref_center = int(ratio * ref_len)
            ref_start = max(0, ref_center - self.SEARCH_WINDOW // 2)
            ref_end = min(ref_len, ref_start + self.SEARCH_WINDOW)

            ref_window = ref_entries[ref_start:ref_end]
            if not ref_window:
                continue

            logger.info(
                f"Anchor group {group_idx + 1}/{num_groups}: "
                f"target[{start_pos}:{end_pos}] vs ref[{ref_start}:{ref_end}]"
            )

            # AI matching
            try:
                matches = await self._ai_match_entries(
                    ref_window, target_sample, ref_start, start_pos,
                )

                for match in matches:
                    target_idx = match["target_idx"]
                    ref_idx = match["ref_idx"]

                    if 0 <= target_idx < target_len and 0 <= ref_idx < ref_len:
                        t_entry = target_entries[target_idx]
                        r_entry = ref_entries[ref_idx]

                        anchors.append({
                            "target_idx": target_idx,
                            "ref_idx": ref_idx,
                            "target_start_ms": t_entry["start_ms"],
                            "target_end_ms": t_entry["end_ms"],
                            "ref_start_ms": r_entry["start_ms"],
                            "ref_end_ms": r_entry["end_ms"],
                            "offset_ms": r_entry["start_ms"] - t_entry["start_ms"],
                        })

            except Exception as e:
                logger.warning(f"Anchor group {group_idx + 1} failed: {e}")
                continue

        # Sort anchors by target position
        anchors.sort(key=lambda a: a["target_start_ms"])

        # Remove outliers (anchors with offset way off from neighbors)
        anchors = self._remove_outlier_anchors(anchors)

        return anchors

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def _ai_match_entries(
        self,
        ref_window: list[dict],
        target_sample: list[dict],
        ref_offset: int,
        target_offset: int,
    ) -> list[dict[str, int]]:
        """
        Gửi một nhóm entries cho AI để match.

        Returns:
            List of {target_idx, ref_idx} (absolute indices)
        """
        # Build compact representation
        ref_text = "\n".join(
            f"[EN-{ref_offset + i}] ({format_srt_time(e['start_ms'])}) {e['text'][:80]}"
            for i, e in enumerate(ref_window)
        )

        target_text = "\n".join(
            f"[VI-{target_offset + i}] {e['text'][:80]}"
            for i, e in enumerate(target_sample)
        )

        system_prompt = (
            "You are a subtitle alignment tool. Match Vietnamese subtitle entries "
            "to their corresponding English subtitle entries based on meaning/content.\n\n"
            "Rules:\n"
            "- Each Vietnamese entry should match exactly one English entry\n"
            "- Match by semantic meaning, not by position\n"
            "- If no good match exists, skip that entry\n"
            "- Return ONLY a JSON array of matches\n"
            "- Format: [{\"vi\": <VI-index>, \"en\": <EN-index>}, ...]\n"
            "- Use the exact index numbers shown in brackets"
        )

        user_prompt = (
            f"English subtitles (with timing):\n{ref_text}\n\n"
            f"Vietnamese subtitles (timing may be wrong):\n{target_text}\n\n"
            f"Match each Vietnamese entry to its English equivalent. "
            f"Return JSON array only."
        )

        response = await self._client.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # Parse AI response
        import json
        try:
            parsed = json.loads(content)
            # Handle both {"matches": [...]} and direct [...]
            if isinstance(parsed, dict):
                matches_raw = parsed.get("matches", parsed.get("results", []))
                if not matches_raw:
                    # Try first list value in dict
                    for v in parsed.values():
                        if isinstance(v, list):
                            matches_raw = v
                            break
            elif isinstance(parsed, list):
                matches_raw = parsed
            else:
                matches_raw = []

            results = []
            for m in matches_raw:
                vi_idx = m.get("vi")
                en_idx = m.get("en")
                if vi_idx is not None and en_idx is not None:
                    results.append({
                        "target_idx": int(vi_idx),
                        "ref_idx": int(en_idx),
                    })

            logger.debug(f"AI matched {len(results)} entries")
            return results

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse AI response: {e}")
            logger.debug(f"AI response content: {content[:500]}")
            return []

    def _remove_outlier_anchors(
        self,
        anchors: list[dict],
        max_deviation_factor: float = 3.0,
    ) -> list[dict]:
        """Loại bỏ anchor points có offset bất thường so với neighbors."""
        if len(anchors) < 3:
            return anchors

        offsets = [a["offset_ms"] for a in anchors]
        median_offset = sorted(offsets)[len(offsets) // 2]

        # Calculate MAD (Median Absolute Deviation)
        deviations = [abs(o - median_offset) for o in offsets]
        mad = sorted(deviations)[len(deviations) // 2] or 1000  # min 1s

        filtered = []
        for anchor in anchors:
            deviation = abs(anchor["offset_ms"] - median_offset)
            if deviation <= mad * max_deviation_factor:
                filtered.append(anchor)
            else:
                logger.debug(
                    f"Removing outlier anchor: offset={anchor['offset_ms']}ms "
                    f"(median={median_offset}ms, deviation={deviation}ms)"
                )

        return filtered

    def _build_time_mapping(
        self,
        anchors: list[dict],
    ) -> "TimeMapping":
        """
        Build piecewise-linear time mapping từ anchor points.

        Mỗi cặp anchor liền kề tạo thành một đoạn linear mapping.
        Ngoài phạm vi anchors: extrapolate từ đoạn gần nhất.
        """
        # Sort by target time
        sorted_anchors = sorted(anchors, key=lambda a: a["target_start_ms"])
        return TimeMapping(sorted_anchors)

    def _apply_time_correction(
        self,
        target_entries: list[dict],
        time_mapping: "TimeMapping",
    ) -> list[dict]:
        """Áp dụng time mapping cho toàn bộ target entries."""
        synced = []
        for entry in target_entries:
            new_start = time_mapping.map_time(entry["start_ms"])
            new_end = time_mapping.map_time(entry["end_ms"])

            # Ensure minimum duration (100ms)
            if new_end <= new_start:
                duration = entry["end_ms"] - entry["start_ms"]
                new_end = new_start + max(duration, 100)

            synced.append({
                "index": entry["index"],
                "start_ms": new_start,
                "end_ms": new_end,
                "text": entry["text"],
            })

        return synced

    async def estimate_sync(
        self,
        reference_path: Path,
        target_path: Path,
    ) -> dict[str, Any]:
        """Estimate sync operation (entries count, estimated API calls)."""
        ref_entries = parse_srt_entries(reference_path)
        target_entries = parse_srt_entries(target_path)

        num_groups = min(
            self.ANCHOR_GROUPS,
            max(2, len(target_entries) // 10),
        )

        return {
            "ref_entries": len(ref_entries),
            "target_entries": len(target_entries),
            "estimated_api_calls": num_groups,
            "model": self.model,
        }


class TimeMapping:
    """
    Piecewise-linear time mapping dựa trên anchor points.

    Mỗi đoạn giữa 2 anchors có hệ số scale riêng.
    Ngoài phạm vi: extrapolate từ đoạn gần nhất.
    """

    def __init__(self, anchors: list[dict]) -> None:
        """
        Args:
            anchors: Sorted list of anchor dicts with target_start_ms và ref_start_ms
        """
        self.anchors = anchors

        # Pre-compute segments
        self.segments: list[dict] = []
        for i in range(len(anchors) - 1):
            a1 = anchors[i]
            a2 = anchors[i + 1]

            target_span = a2["target_start_ms"] - a1["target_start_ms"]
            ref_span = a2["ref_start_ms"] - a1["ref_start_ms"]

            scale = ref_span / target_span if target_span > 0 else 1.0
            offset = a1["ref_start_ms"] - scale * a1["target_start_ms"]

            self.segments.append({
                "target_start": a1["target_start_ms"],
                "target_end": a2["target_start_ms"],
                "scale": scale,
                "offset": offset,
            })

    def map_time(self, target_ms: int) -> int:
        """Map target timestamp thành reference timestamp."""
        if not self.segments:
            # Fallback: simple offset from single anchor
            if self.anchors:
                offset = self.anchors[0]["ref_start_ms"] - self.anchors[0]["target_start_ms"]
                return target_ms + offset
            return target_ms

        # Before first segment: extrapolate
        if target_ms <= self.segments[0]["target_start"]:
            seg = self.segments[0]
            return int(seg["scale"] * target_ms + seg["offset"])

        # After last segment: extrapolate
        if target_ms >= self.segments[-1]["target_end"]:
            seg = self.segments[-1]
            return int(seg["scale"] * target_ms + seg["offset"])

        # Find matching segment
        for seg in self.segments:
            if seg["target_start"] <= target_ms <= seg["target_end"]:
                return int(seg["scale"] * target_ms + seg["offset"])

        # Fallback (shouldn't reach here)
        seg = self.segments[-1]
        return int(seg["scale"] * target_ms + seg["offset"])
