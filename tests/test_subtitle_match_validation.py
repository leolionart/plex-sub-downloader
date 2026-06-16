from typing import Any

import pytest

from app.clients.subtitle_match_validator_client import SubtitleMatchDecision
from app.models.settings import ServiceConfig, SubtitleSettings
from app.models.subtitle import SubtitleResult, SubtitleSearchParams
from app.services.subtitle_service import SubtitleService


class FakeLog:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, message: str, **kwargs: Any) -> None:
        self.entries.append(("info", message, kwargs))

    def warning(self, message: str, **kwargs: Any) -> None:
        self.entries.append(("warning", message, kwargs))


class FakeValidator:
    def __init__(self, decisions: list[SubtitleMatchDecision], enabled: bool = True) -> None:
        self.enabled = enabled
        self.decisions = decisions
        self.seen_candidates: list[SubtitleResult] = []

    async def validate_candidates(
        self,
        params: SubtitleSearchParams,
        candidates: list[SubtitleResult],
    ) -> list[SubtitleMatchDecision]:
        self.seen_candidates = candidates
        return self.decisions


def make_service(
    validator: FakeValidator,
    settings: SubtitleSettings | None = None,
) -> SubtitleService:
    service = SubtitleService.__new__(SubtitleService)
    service.config = ServiceConfig(subtitle_settings=settings or SubtitleSettings())
    service.match_validator_client = validator
    return service


def make_result(
    subtitle_id: str,
    name: str,
    season: int | None,
    episode: int | None,
) -> SubtitleResult:
    return SubtitleResult(
        id=subtitle_id,
        provider="subdl",
        name=name,
        language="vi",
        download_url=f"https://example.com/{subtitle_id}.srt",
        season=season,
        episode=episode,
    )


def make_params() -> SubtitleSearchParams:
    return SubtitleSearchParams(
        language="vi",
        title="FROM",
        year=2026,
        imdb_id="tt9813792",
        season=4,
        episode=1,
        video_filename="FROM - S04E01 - The Arrival WEBDL-1080p.mkv",
    )


@pytest.mark.asyncio
async def test_validate_subtitle_matches_keeps_exact_and_rejects_wrong_episode() -> None:
    service = make_service(FakeValidator([]))
    results = [
        make_result("exact", "FROM.S04E01.WEB-DL", 4, 1),
        make_result("wrong", "FROM.S04E02.WEB-DL", 4, 2),
    ]

    validated = await service._validate_subtitle_matches(make_params(), results, FakeLog())

    assert [r.id for r in validated] == ["exact"]
    assert validated[0].match_validation == "trusted"
    assert validated[0].match_confidence == 1.0


@pytest.mark.asyncio
async def test_validate_subtitle_matches_ai_accepts_ambiguous_candidate() -> None:
    validator = FakeValidator(
        [
            SubtitleMatchDecision(
                subtitle_id="ambiguous",
                is_match=True,
                confidence=0.92,
                reason="Filename contains FROM S04E01",
            )
        ]
    )
    service = make_service(validator)
    candidate = make_result("ambiguous", "FROM.S04E01.WEB-DL", None, None)

    validated = await service._validate_subtitle_matches(make_params(), [candidate], FakeLog())

    assert [r.id for r in validated] == ["ambiguous"]
    assert validated[0].match_validation == "ai_verified"
    assert validated[0].match_confidence == 0.92
    assert validator.seen_candidates == [candidate]


@pytest.mark.asyncio
async def test_validate_subtitle_matches_drops_ambiguous_when_ai_unavailable() -> None:
    service = make_service(FakeValidator([], enabled=False))
    candidate = make_result("ambiguous", "Unknown.Release", None, None)

    validated = await service._validate_subtitle_matches(make_params(), [candidate], FakeLog())

    assert validated == []
