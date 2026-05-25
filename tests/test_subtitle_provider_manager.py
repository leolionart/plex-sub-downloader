from pathlib import Path

import pytest

from app.clients.subtitle_provider_manager import SubtitleProviderManager
from app.models.subtitle import SubtitleResult, SubtitleSearchParams


class FakeProvider:
    def __init__(self, name: str, results: list[SubtitleResult] | Exception) -> None:
        self.name = name
        self.results = results
        self.downloaded: list[str] = []

    async def search_subtitles(self, params: SubtitleSearchParams) -> list[SubtitleResult]:
        if isinstance(self.results, Exception):
            raise self.results
        return self.results

    async def download_subtitle(
        self,
        subtitle: SubtitleResult,
        dest_dir: Path,
        expected_season: int | None = None,
        expected_episode: int | None = None,
        video_filename: str | None = None,
    ) -> Path:
        self.downloaded.append(subtitle.id)
        path = dest_dir / f"{subtitle.provider}-{subtitle.id}.srt"
        path.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
        return path

    async def close(self) -> None:
        pass


def make_result(
    provider: str,
    subtitle_id: str,
    name: str,
    downloads: int,
) -> SubtitleResult:
    return SubtitleResult(
        id=subtitle_id,
        provider=provider,
        name=name,
        language="vi",
        download_url=f"https://example.test/{provider}/{subtitle_id}",
        quality_type="translated",
        downloads=downloads,
    )


@pytest.mark.asyncio
async def test_search_merges_enabled_providers_and_sorts() -> None:
    subsource = FakeProvider("subsource", [make_result("subsource", "1", "A", 10)])
    subdl = FakeProvider("subdl", [make_result("subdl", "2", "B", 1000)])
    manager = SubtitleProviderManager.__new__(SubtitleProviderManager)
    manager.providers = [subsource, subdl]
    manager._by_name = {p.name: p for p in manager.providers}

    results = await manager.search_subtitles(SubtitleSearchParams(language="vi", title="Movie"))

    assert [r.provider for r in results] == ["subdl", "subsource"]
    assert [r.id for r in results] == ["2", "1"]


@pytest.mark.asyncio
async def test_search_ignores_one_provider_failure() -> None:
    broken = FakeProvider("opensubtitles", RuntimeError("boom"))
    subdl = FakeProvider("subdl", [make_result("subdl", "2", "B", 10)])
    manager = SubtitleProviderManager.__new__(SubtitleProviderManager)
    manager.providers = [broken, subdl]
    manager._by_name = {p.name: p for p in manager.providers}

    results = await manager.search_subtitles(SubtitleSearchParams(language="vi", title="Movie"))

    assert len(results) == 1
    assert results[0].provider == "subdl"


@pytest.mark.asyncio
async def test_download_routes_to_result_provider(tmp_path: Path) -> None:
    subsource = FakeProvider("subsource", [])
    subdl = FakeProvider("subdl", [])
    manager = SubtitleProviderManager.__new__(SubtitleProviderManager)
    manager.providers = [subsource, subdl]
    manager._by_name = {p.name: p for p in manager.providers}
    result = make_result("subdl", "2", "B", 10)

    path = await manager.download_subtitle(result, tmp_path)

    assert path.name == "subdl-2.srt"
    assert subsource.downloaded == []
    assert subdl.downloaded == ["2"]
