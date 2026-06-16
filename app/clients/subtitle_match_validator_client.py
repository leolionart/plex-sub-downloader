"""
AI validator for subtitle search candidates.

This client is intentionally conservative. It only validates metadata-level
evidence and returns structured decisions; the service decides how to use them.
"""

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.models.runtime_config import RuntimeConfig
from app.models.settings import DEFAULT_SUBTITLE_MATCH_SYSTEM_PROMPT_TEMPLATE
from app.models.subtitle import SubtitleResult, SubtitleSearchParams
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SubtitleMatchValidatorError(Exception):
    """Raised when AI subtitle match validation fails."""

    pass


class SubtitleMatchDecision(BaseModel):
    """AI decision for a subtitle candidate."""

    subtitle_id: str
    is_match: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class SubtitleMatchValidatorClient:
    """OpenAI-compatible client for validating subtitle candidate matches."""

    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config
        self.api_key = config.openai_api_key
        self.base_url = config.openai_base_url
        self.model = config.openai_model
        self.enabled = bool(self.api_key)
        self._client = httpx.AsyncClient(
            timeout=45.0,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
        )

        if self.enabled:
            logger.info(f"Subtitle match validator enabled (model={self.model})")
        else:
            logger.info("Subtitle match validator disabled - no OpenAI API key")

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()

    async def validate_candidates(
        self,
        params: SubtitleSearchParams,
        candidates: list[SubtitleResult],
    ) -> list[SubtitleMatchDecision]:
        """Validate a batch of ambiguous subtitle candidates."""
        if not self.enabled:
            raise SubtitleMatchValidatorError("Subtitle match validation disabled - no API key")
        if not candidates:
            return []

        system_prompt = (
            self._config.subtitle_settings.subtitle_match_system_prompt_template.strip()
            or DEFAULT_SUBTITLE_MATCH_SYSTEM_PROMPT_TEMPLATE
        )

        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(
                                self._validation_payload(params, candidates),
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return self._parse_decisions(str(content))
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise SubtitleMatchValidatorError(f"AI validation API error: {e}; body={body}") from e
        except (KeyError, TypeError, ValueError, ValidationError) as e:
            raise SubtitleMatchValidatorError(f"Invalid AI validation response: {e}") from e

    @staticmethod
    def _validation_payload(
        params: SubtitleSearchParams,
        candidates: list[SubtitleResult],
    ) -> dict[str, Any]:
        return {
            "target": {
                "language": params.language,
                "title": params.title,
                "year": params.year,
                "imdb_id": params.imdb_id,
                "tmdb_id": params.tmdb_id,
                "season": params.season,
                "episode": params.episode,
                "video_filename": params.video_filename,
            },
            "candidates": [
                {
                    "subtitle_id": candidate.id,
                    "provider": candidate.provider,
                    "name": candidate.name,
                    "release_info": candidate.release_info,
                    "language": candidate.language,
                    "season": candidate.season,
                    "episode": candidate.episode,
                    "imdb_id": candidate.imdb_id,
                    "tmdb_id": candidate.tmdb_id,
                }
                for candidate in candidates
            ],
        }

    @classmethod
    def _parse_decisions(cls, content: str) -> list[SubtitleMatchDecision]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if not match:
                raise
            payload = json.loads(match.group(0))

        decisions = payload.get("decisions") if isinstance(payload, dict) else None
        if not isinstance(decisions, list):
            raise ValueError("AI validation response must contain a decisions array")
        return [SubtitleMatchDecision(**item) for item in decisions]
