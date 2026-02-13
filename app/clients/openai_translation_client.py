"""
OpenAI translation client cho subtitle translation.
Translate English subtitles → Vietnamese khi không tìm thấy subtitle tiếng Việt.
"""

import logging
import re
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.models.runtime_config import RuntimeConfig
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
            logger.info(f"OpenAI translation enabled (model={self.model})")

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

                entries.append({
                    "index": index,
                    "timing": timing,
                    "text": text,
                })
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

        # Create prompt
        system_prompt = f"""You are a professional subtitle translator.
Translate the following subtitle texts from {from_lang} to {to_lang}.
Keep the translation natural and preserve any special formatting.
Return ONLY the translations, one per line, in the same order."""

        user_prompt = "\n---\n".join(texts)

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

            # Parse response
            translations = [t.strip() for t in translated_text.split("---")]

            # Verify count matches
            if len(translations) != len(texts):
                logger.warning(
                    f"Translation count mismatch: expected {len(texts)}, got {len(translations)}"
                )
                # Pad or truncate
                while len(translations) < len(texts):
                    translations.append(texts[len(translations)])
                translations = translations[:len(texts)]

            return translations

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI API error: {e.response.text}")
            raise TranslationClientError(f"API error: {e}") from e
        except Exception as e:
            logger.error(f"Translation error: {e}")
            raise TranslationClientError(f"Translation failed: {e}") from e

    async def translate_srt_file(
        self,
        srt_path: Path,
        output_path: Path,
        from_lang: str = "en",
        to_lang: str = "vi",
        batch_size: int = 10,
    ) -> dict[str, Any]:
        """
        Translate entire .srt subtitle file.

        Args:
            srt_path: Input .srt file path
            output_path: Output .srt file path
            from_lang: Source language
            to_lang: Target language
            batch_size: Number of subtitle entries per API call

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

        # Translate in batches
        translated_entries = []
        total_batches = (len(entries) + batch_size - 1) // batch_size

        for i in range(0, len(entries), batch_size):
            batch = entries[i:i + batch_size]
            batch_num = i // batch_size + 1

            logger.info(f"Translating batch {batch_num}/{total_batches} ({len(batch)} entries)")

            # Extract texts
            texts = [entry["text"] for entry in batch]

            # Translate
            try:
                translated_texts = await self.translate_text_batch(texts, from_lang, to_lang)

                # Update entries
                for entry, translated_text in zip(batch, translated_texts):
                    translated_entries.append({
                        "index": entry["index"],
                        "timing": entry["timing"],
                        "text": translated_text,
                    })

            except Exception as e:
                logger.error(f"Batch {batch_num} translation failed: {e}")
                # Fallback: keep original text
                translated_entries.extend(batch)

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
        }

        return stats

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
