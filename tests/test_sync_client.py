import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.clients.sync_client import SubtitleSyncClient, SyncClientError, TimeMapping
from app.models.runtime_config import RuntimeConfig


def entry(index, start):
    return {"index": index, "start_ms": start, "end_ms": start + 500, "text": f"Line {index}"}


@pytest.mark.asyncio
async def test_ai_match_skips_invalid_indices_without_losing_valid_matches():
    client = SubtitleSyncClient(RuntimeConfig(openai_api_key="test"))
    content = json.dumps({"matches": [
        {"vi": "bad", "en": 10},
        {"vi": 2, "en": "10"},
        {"vi": None, "en": 10},
        {"vi": 2.5, "en": 10},
        {"vi": True, "en": 10},
        {"vi": 3, "en": 99},
        "invalid",
        {"vi": "3", "en": 11},
    ]})
    response = httpx.Response(200, json={"choices": [{"message": {"content": content}}]}, request=httpx.Request("POST", "https://example.com"))
    client._client.post = AsyncMock(return_value=response)
    try:
        matches = await client._ai_match_entries([entry(1, 0), entry(2, 1000)], [entry(1, 0), entry(2, 1000)], 10, 2)
        assert matches == [{"target_idx": 2, "ref_idx": 10}, {"target_idx": 3, "ref_idx": 11}]
        prompt = client._client.post.call_args.kwargs["json"]["messages"][1]["content"]
        assert '"matches"' in prompt
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_anchor_sampling_rejects_outside_matches_and_duplicate_times():
    client = SubtitleSyncClient(RuntimeConfig(openai_api_key="test"))
    entries = [entry(i, i * 1000) for i in range(4)]
    async def matches(ref, target, ref_offset, target_offset):
        return [
            {"target_idx": target_offset, "ref_idx": target_offset},
            {"target_idx": target_offset, "ref_idx": target_offset},
            {"target_idx": 3, "ref_idx": 3},
        ]
    client._ai_match_entries = matches
    try:
        anchors = await client._find_anchor_points(entries, entries)
        assert [a["target_idx"] for a in anchors] == [0, 1, 3]
    finally:
        await client.close()


def test_monotonic_chain_drops_crossed_and_duplicate_anchors():
    client = SubtitleSyncClient(RuntimeConfig(openai_api_key="test"))
    anchors = [
        {"target_start_ms": 0, "ref_start_ms": 0},
        {"target_start_ms": 1000, "ref_start_ms": 3000},
        {"target_start_ms": 1000, "ref_start_ms": 1000},
        {"target_start_ms": 2000, "ref_start_ms": 2000},
        {"target_start_ms": 3000, "ref_start_ms": 3000},
    ]
    anchors.sort(key=lambda a: (a["target_start_ms"], a["ref_start_ms"]))
    selected = client._monotonic_anchors(anchors)
    assert [(a["target_start_ms"], a["ref_start_ms"]) for a in selected] == [(0, 0), (1000, 1000), (2000, 2000), (3000, 3000)]


def test_mapping_interpolates_but_does_not_extrapolate_local_slope():
    mapping = TimeMapping([
        {"target_start_ms": 10000, "ref_start_ms": 11000},
        {"target_start_ms": 11000, "ref_start_ms": 13000},
    ])
    assert mapping.map_time(0) == 1000
    assert mapping.map_time(10500) == 12000
    assert mapping.map_time(20000) == 22000


def test_negative_mapped_times_keep_positive_duration():
    client = SubtitleSyncClient(RuntimeConfig(openai_api_key="test"))
    mapping = TimeMapping([{"target_start_ms": 1000, "ref_start_ms": 0}])
    corrected = client._apply_time_correction([entry(1, 0)], mapping)
    assert corrected[0]["start_ms"] == 0
    assert corrected[0]["end_ms"] == 500


@pytest.mark.asyncio
async def test_sync_rejects_insufficient_consistent_anchors(tmp_path):
    client = SubtitleSyncClient(RuntimeConfig(openai_api_key="test"))
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    client._ai_match_entries = AsyncMock(return_value=[{"target_idx": 0, "ref_idx": 0}])
    try:
        with pytest.raises(SyncClientError, match="Not enough anchor points"):
            await client.sync_subtitles(source, source, tmp_path / "out.srt")
        assert not (tmp_path / "out.srt").exists()
    finally:
        await client.close()
