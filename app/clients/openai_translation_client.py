"""
OpenAI translation client cho subtitle translation.
Translate English subtitles → Vietnamese khi không tìm thấy subtitle tiếng Việt.
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.models.runtime_config import RuntimeConfig
from app.models.settings import (
    DEFAULT_TRANSLATION_CONTEXT_SYSTEM_PROMPT_TEMPLATE,
    DEFAULT_TRANSLATION_REVIEW_SYSTEM_PROMPT_TEMPLATE,
    DEFAULT_TRANSLATION_SYSTEM_PROMPT_TEMPLATE,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TranslationClientError(Exception):
    """Base exception for translation client errors."""

    pass


class OpenAITranslationClient:
    """
    Client để translate subtitles sử dụng OpenAI API (hoặc compatible endpoints).

    Features:
    - Translate .srt subtitle files
    - Batch translation (nhiều entries cùng lúc)
    - Preserve timing và formatting
    - Support custom OpenAI-compatible endpoints (e.g., OpenRouter, LM Studio)
    """

    def __init__(
        self,
        config: RuntimeConfig,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        """
        Initialize translation client.

        Args:
            config: RuntimeConfig
            api_key: OpenAI API key
            base_url: Custom base URL (e.g., https://api.openai.com/v1)
            model: Model name (e.g., gpt-4o-mini, gpt-3.5-turbo)
        """
        self._config = config
        self.api_key = api_key or config.openai_api_key
        self.base_url = base_url or config.openai_base_url
        self.model = model or config.openai_model

        self.enabled = bool(self.api_key)

        if not self.enabled:
            logger.info("OpenAI translation disabled - no API key")
        else:
            logger.info(
                f"OpenAI translation enabled (model={self.model}, base_url={self.base_url})"
            )

        self._client = httpx.AsyncClient(
            timeout=60.0,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
        )

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()

    def parse_srt_file(self, srt_path: Path) -> list[dict[str, Any]]:
        """
        Parse .srt file thành list of subtitle entries.

        Returns:
            List of {index, timing, text}
        """
        content = srt_path.read_text(encoding="utf-8")

        # SRT format:
        # 1
        # 00:00:01,000 --> 00:00:05,000
        # Subtitle text line 1
        # Subtitle text line 2
        #
        # 2
        # 00:00:06,000 --> 00:00:10,000
        # Next subtitle

        entries = []
        blocks = content.strip().split("\n\n")

        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 3:
                continue

            try:
                index = int(lines[0])
                timing = lines[1]
                text = "\n".join(lines[2:])

                entries.append(
                    {
                        "index": index,
                        "timing": timing,
                        "text": text,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to parse SRT block: {e}")
                continue

        logger.debug(f"Parsed {len(entries)} subtitle entries from {srt_path.name}")
        return entries

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def translate_text_batch(
        self,
        texts: list[str],
        from_lang: str = "en",
        to_lang: str = "vi",
        context: dict[str, Any] | None = None,
    ) -> list[str]:
        """
        Translate batch of texts sử dụng OpenAI API.

        Args:
            texts: List of text strings to translate
            from_lang: Source language
            to_lang: Target language

        Returns:
            List of translated strings
        """
        if not self.enabled:
            raise TranslationClientError("Translation disabled - no API key")

        # Create prompt — numbered format for reliable parsing
        prompt_template = self._config.subtitle_settings.translation_system_prompt_template
        if not prompt_template.strip():
            prompt_template = DEFAULT_TRANSLATION_SYSTEM_PROMPT_TEMPLATE

        try:
            system_prompt = prompt_template.format(
                from_lang=from_lang,
                to_lang=to_lang,
                count=len(texts),
            )
        except Exception as e:
            logger.warning(
                "Invalid translation_system_prompt_template format, using default: %s",
                e,
            )
            system_prompt = DEFAULT_TRANSLATION_SYSTEM_PROMPT_TEMPLATE.format(
                from_lang=from_lang,
                to_lang=to_lang,
                count=len(texts),
            )

        user_prompt = self._build_translation_user_prompt(texts, context)

        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,  # Lower = more consistent
                },
            )
            response.raise_for_status()

            data = response.json()
            translated_text = data["choices"][0]["message"]["content"]

            # Parse numbered response: [1] text, [2] text, ...
            translations = self._parse_numbered_response(translated_text, len(texts))

            # Verify count matches
            if len(translations) != len(texts):
                logger.warning(
                    f"Translation count mismatch: expected {len(texts)}, got {len(translations)}"
                )
                # Pad with original text for missing entries
                while len(translations) < len(texts):
                    translations.append(texts[len(translations)])
                translations = translations[: len(texts)]

            return translations

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI API error: {e.response.text}")
            raise TranslationClientError(f"API error: {e}") from e
        except Exception as e:
            logger.error(f"Translation error: {e}")
            raise TranslationClientError(f"Translation failed: {e}") from e

    def _build_translation_user_prompt(
        self,
        texts: list[str],
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build a numbered translation prompt with optional episode/batch context."""
        sections: list[str] = []
        if context:
            sections.append(
                "EPISODE / MOVIE CONTEXT:\n"
                + json.dumps(context.get("profile") or {}, ensure_ascii=False, indent=2)
            )
            metadata = context.get("metadata")
            if metadata:
                sections.append(
                    "MEDIA METADATA:\n" + json.dumps(metadata, ensure_ascii=False, indent=2)
                )
            previous_context = context.get("previous_context") or []
            if previous_context:
                sections.append(
                    "PREVIOUS CONTEXT (do not translate, use only for context):\n"
                    + self._format_context_entries(previous_context)
                )
            next_context = context.get("next_context") or []
            if next_context:
                sections.append(
                    "NEXT CONTEXT (do not translate, use only for context):\n"
                    + self._format_context_entries(next_context)
                )

        numbered_lines = [f"[{i}] {text}" for i, text in enumerate(texts, 1)]
        sections.append("LINES TO TRANSLATE:\n" + "\n".join(numbered_lines))
        return "\n\n".join(sections)

    @staticmethod
    def _format_context_entries(entries: list[dict[str, Any]]) -> str:
        return "\n".join(f"[{entry['index']}] {entry['text']}" for entry in entries)

    def _parse_numbered_response(self, response_text: str, expected_count: int) -> list[str]:
        """
        Parse numbered AI response: [1] text, [2] text, ...

        Robust hơn split("---") vì dùng regex pattern matching.
        Xử lý được multi-line entries giữa các [N] markers.
        """
        # Split by [N] pattern, capture both index and content between markers
        parts = re.split(r"\[(\d+)\]\s*", response_text.strip())
        # parts = ['preamble', '1', 'text1\n', '2', 'text2\n', ...]

        results: dict[int, str] = {}
        for i in range(1, len(parts) - 1, 2):
            try:
                idx = int(parts[i])
                content = parts[i + 1].strip()
                results[idx] = content
            except (ValueError, IndexError):
                continue

        # Build ordered list matching expected count
        translations = []
        for i in range(1, expected_count + 1):
            if i in results:
                translations.append(results[i])
            else:
                logger.warning(f"Missing translation for entry [{i}]")

        return translations

    async def translate_srt_file(
        self,
        srt_path: Path,
        output_path: Path,
        from_lang: str = "en",
        to_lang: str = "vi",
        batch_size: int = 25,
        max_concurrent: int = 5,
        media_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Translate entire .srt subtitle file with parallel batch processing.

        Args:
            srt_path: Input .srt file path
            output_path: Output .srt file path
            from_lang: Source language
            to_lang: Target language
            batch_size: Number of subtitle entries per API call
            max_concurrent: Max concurrent API calls (semaphore limit)

        Returns:
            Dict với stats: {lines_translated, batches, cost_estimate}
        """
        if not self.enabled:
            raise TranslationClientError("Translation disabled")

        logger.info(f"Translating subtitle: {srt_path.name} ({from_lang} → {to_lang})")

        # Parse SRT
        entries = self.parse_srt_file(srt_path)
        if not entries:
            raise TranslationClientError("No subtitle entries found")

        profile: dict[str, Any] = {}
        context_enabled = self._config.subtitle_settings.translation_context_enabled
        if context_enabled:
            try:
                profile = await self.extract_episode_context(
                    entries,
                    from_lang=from_lang,
                    to_lang=to_lang,
                    media_metadata=media_metadata,
                )
                logger.info("✓ Translation context profile extracted")
            except Exception as e:
                logger.warning("Translation context extraction failed, continuing: %s", e)
                profile = {}

        # Split entries into batches
        batches = [entries[i : i + batch_size] for i in range(0, len(entries), batch_size)]
        total_batches = len(batches)
        semaphore = asyncio.Semaphore(max_concurrent)
        context_window = self._config.subtitle_settings.translation_context_window

        logger.info(
            f"Translation plan: {len(entries)} entries → {total_batches} batches "
            f"(size={batch_size}, concurrent={max_concurrent})"
        )

        async def _translate_one_batch(
            batch_idx: int, batch: list[dict[str, Any]]
        ) -> tuple[int, list[dict[str, Any]]]:
            """Translate a single batch under semaphore control."""
            async with semaphore:
                batch_num = batch_idx + 1
                logger.info(f"Translating batch {batch_num}/{total_batches} ({len(batch)} entries)")
                texts = [entry["text"] for entry in batch]
                context = self._build_batch_context(
                    entries=entries,
                    batch_start=batch_idx * batch_size,
                    batch_size=len(batch),
                    context_window=context_window,
                    profile=profile,
                    media_metadata=media_metadata,
                )

                try:
                    translated_texts = await self.translate_text_batch(
                        texts,
                        from_lang,
                        to_lang,
                        context=context,
                    )
                    translated = [
                        {"index": entry["index"], "timing": entry["timing"], "text": t}
                        for entry, t in zip(batch, translated_texts)
                    ]
                    return (batch_idx, translated)

                except Exception as e:
                    logger.error(f"Batch {batch_num} translation failed: {e}")
                    # Fallback: keep original text
                    return (batch_idx, list(batch))

        # Launch all batches concurrently (semaphore limits parallelism)
        results = await asyncio.gather(
            *[_translate_one_batch(idx, batch) for idx, batch in enumerate(batches)]
        )

        # Sort by batch index and flatten — guarantees correct order
        results_sorted = sorted(results, key=lambda r: r[0])
        translated_entries = [
            entry for _, batch_entries in results_sorted for entry in batch_entries
        ]

        review_enabled = self._config.subtitle_settings.translation_consistency_review_enabled
        if review_enabled:
            try:
                translated_entries = await self.review_translation_consistency(
                    source_entries=entries,
                    translated_entries=translated_entries,
                    from_lang=from_lang,
                    to_lang=to_lang,
                    profile=profile,
                    media_metadata=media_metadata,
                )
                logger.info("✓ Translation consistency review completed")
            except Exception as e:
                logger.warning("Translation consistency review failed, keeping batch output: %s", e)

        # Write translated SRT
        srt_content = []
        for entry in translated_entries:
            srt_content.append(f"{entry['index']}")
            srt_content.append(entry["timing"])
            srt_content.append(entry["text"])
            srt_content.append("")  # Empty line

        output_path.write_text("\n".join(srt_content), encoding="utf-8")

        logger.info(f"✓ Translated subtitle saved to: {output_path}")

        # Stats
        stats = {
            "lines_translated": len(entries),
            "batches": total_batches,
            "input_file": str(srt_path),
            "output_file": str(output_path),
            "model": self.model,
            "context_enabled": context_enabled,
            "context_profile_extracted": bool(profile),
            "context_window": context_window,
            "review_enabled": review_enabled,
        }

        return stats

    async def extract_episode_context(
        self,
        entries: list[dict[str, Any]],
        from_lang: str,
        to_lang: str,
        media_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract whole-episode translation context before batching."""
        if not self.enabled:
            raise TranslationClientError("Translation disabled - no API key")

        prompt_template = self._config.subtitle_settings.translation_context_system_prompt_template
        if not prompt_template.strip():
            prompt_template = DEFAULT_TRANSLATION_CONTEXT_SYSTEM_PROMPT_TEMPLATE

        response = await self._client.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": prompt_template},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "from_lang": from_lang,
                                "to_lang": to_lang,
                                "metadata": media_metadata or {},
                                "transcript": [
                                    {
                                        "index": entry["index"],
                                        "timing": entry["timing"],
                                        "text": entry["text"],
                                    }
                                    for entry in entries
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return self._parse_json_object(str(content))

    async def review_translation_consistency(
        self,
        source_entries: list[dict[str, Any]],
        translated_entries: list[dict[str, Any]],
        from_lang: str,
        to_lang: str,
        profile: dict[str, Any],
        media_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a final full-episode consistency pass over translated subtitles."""
        if not self.enabled:
            raise TranslationClientError("Translation disabled - no API key")

        prompt_template = self._config.subtitle_settings.translation_review_system_prompt_template
        if not prompt_template.strip():
            prompt_template = DEFAULT_TRANSLATION_REVIEW_SYSTEM_PROMPT_TEMPLATE

        try:
            system_prompt = prompt_template.format(
                to_lang=to_lang,
                count=len(translated_entries),
            )
        except Exception as e:
            logger.warning(
                "Invalid translation_review_system_prompt_template, using default: %s", e
            )
            system_prompt = DEFAULT_TRANSLATION_REVIEW_SYSTEM_PROMPT_TEMPLATE.format(
                to_lang=to_lang,
                count=len(translated_entries),
            )

        response = await self._client.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "from_lang": from_lang,
                                "to_lang": to_lang,
                                "metadata": media_metadata or {},
                                "profile": profile,
                                "source": [
                                    {"index": entry["index"], "text": entry["text"]}
                                    for entry in source_entries
                                ],
                                "translation": [
                                    {"index": entry["index"], "text": entry["text"]}
                                    for entry in translated_entries
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        reviewed_texts = self._parse_numbered_response(str(content), len(translated_entries))
        if len(reviewed_texts) != len(translated_entries):
            raise TranslationClientError(
                f"Review count mismatch: expected {len(translated_entries)}, got {len(reviewed_texts)}"
            )
        return [
            {**entry, "text": reviewed_text}
            for entry, reviewed_text in zip(translated_entries, reviewed_texts)
        ]

    @staticmethod
    def _build_batch_context(
        entries: list[dict[str, Any]],
        batch_start: int,
        batch_size: int,
        context_window: int,
        profile: dict[str, Any],
        media_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        batch_end = batch_start + batch_size
        previous_start = max(0, batch_start - context_window)
        next_end = min(len(entries), batch_end + context_window)
        return {
            "metadata": media_metadata or {},
            "profile": profile,
            "previous_context": entries[previous_start:batch_start],
            "next_context": entries[batch_end:next_end],
        }

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if not match:
                raise
            payload = json.loads(match.group(0))
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object")
        return payload

    async def estimate_cost(
        self,
        srt_path: Path,
        batch_size: int = 10,
    ) -> dict[str, Any]:
        """
        Estimate translation cost (approximate).

        Returns:
            Dict với token count và estimated cost
        """
        entries = self.parse_srt_file(srt_path)
        total_chars = sum(len(entry["text"]) for entry in entries)

        # Rough estimate: 1 token ≈ 4 characters
        estimated_tokens = total_chars // 4

        # OpenAI pricing (approximate, update based on actual model)
        # gpt-4o-mini: $0.15 / 1M input tokens, $0.60 / 1M output tokens
        pricing = {
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
            "gpt-4": {"input": 30.00, "output": 60.00},
        }

        model_pricing = pricing.get(self.model, pricing["gpt-4o-mini"])

        input_cost = (estimated_tokens / 1_000_000) * model_pricing["input"]
        output_cost = (estimated_tokens / 1_000_000) * model_pricing["output"]
        total_cost = input_cost + output_cost

        return {
            "subtitle_entries": len(entries),
            "total_characters": total_chars,
            "estimated_tokens": estimated_tokens,
            "estimated_cost_usd": round(total_cost, 4),
            "model": self.model,
        }
