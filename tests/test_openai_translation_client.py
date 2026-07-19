from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.clients.openai_translation_client import OpenAITranslationClient
from app.models.runtime_config import RuntimeConfig
from app.models.settings import SubtitleSettings


def write_srt(path: Path, count: int) -> None:
    blocks = []
    for idx in range(1, count + 1):
        blocks.append(f"{idx}\n00:00:{idx:02d},000 --> 00:00:{idx + 1:02d},000\nLine {idx}")
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_translate_srt_file_uses_episode_context_and_batch_windows(tmp_path: Path) -> None:
    settings = SubtitleSettings(
        translation_context_enabled=True,
        translation_context_window=2,
        translation_consistency_review_enabled=True,
    )
    client = OpenAITranslationClient(
        RuntimeConfig(openai_api_key="test-key", subtitle_settings=settings)
    )
    source = tmp_path / "source.srt"
    output = tmp_path / "output.srt"
    write_srt(source, 6)

    client.extract_episode_context = AsyncMock(
        return_value={
            "summary": "Test episode",
            "characters": [{"name": "Alice", "addressing": "xưng chị"}],
            "glossary": [{"source": "Sheriff", "target": "cảnh sát trưởng"}],
        }
    )
    seen_contexts = []

    async def fake_translate_text_batch(texts, from_lang="en", to_lang="vi", context=None):
        seen_contexts.append(context)
        return [f"VI {text}" for text in texts]

    async def fake_review_translation_consistency(
        source_entries,
        translated_entries,
        from_lang,
        to_lang,
        profile,
        media_metadata=None,
    ):
        return [{**entry, "text": f"REVIEW {entry['text']}"} for entry in translated_entries]

    client.translate_text_batch = fake_translate_text_batch
    client.review_translation_consistency = fake_review_translation_consistency

    stats = await client.translate_srt_file(
        source,
        output,
        batch_size=3,
        max_concurrent=1,
        media_metadata={"title": "FROM", "season_number": 4, "episode_number": 1},
    )

    assert stats["context_enabled"] is True
    assert stats["context_profile_extracted"] is True
    assert stats["review_enabled"] is True
    assert len(seen_contexts) == 2
    assert [entry["index"] for entry in seen_contexts[0]["next_context"]] == [4, 5]
    assert [entry["index"] for entry in seen_contexts[1]["previous_context"]] == [2, 3]
    assert "REVIEW VI Line 1" in output.read_text(encoding="utf-8")


def test_build_translation_user_prompt_includes_context() -> None:
    client = OpenAITranslationClient(RuntimeConfig(openai_api_key="test-key"))

    prompt = client._build_translation_user_prompt(
        ["Hello"],
        {
            "metadata": {"title": "FROM"},
            "profile": {"summary": "A tense scene"},
            "previous_context": [{"index": 1, "text": "Who are you?"}],
            "next_context": [{"index": 3, "text": "Run!"}],
        },
    )

    assert "EPISODE / MOVIE CONTEXT" in prompt
    assert "PREVIOUS CONTEXT" in prompt
    assert "NEXT CONTEXT" in prompt
    assert "LINES TO TRANSLATE" in prompt
    assert "[1] Hello" in prompt
