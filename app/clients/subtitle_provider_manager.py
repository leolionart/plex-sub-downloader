"""
Aggregate subtitle providers and route downloads back to their source provider.
"""

import asyncio
from pathlib import Path

from app.clients.opensubtitles_client import OpenSubtitlesClient
from app.clients.subdl_client import SubDLClient
from app.clients.subsource_client import SubsourceClient
from app.clients.subtitle_provider import (
    SubtitleProvider,
    search_subtitles_multi_lang as provider_search_multi_lang,
)
from app.models.runtime_config import RuntimeConfig
from app.models.subtitle import SubtitleResult, SubtitleSearchParams
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SubtitleProviderManager:
    """Search multiple subtitle providers concurrently."""

    def __init__(self, config: RuntimeConfig) -> None:
        providers: list[SubtitleProvider] = [SubsourceClient(config)]
        if config.opensubtitles_api_key:
            providers.append(OpenSubtitlesClient(config))
        if config.subdl_api_key:
            providers.append(SubDLClient(config))

        self.providers = providers
        self._by_name = {provider.name: provider for provider in providers}

    async def close(self) -> None:
        await asyncio.gather(
            *(provider.close() for provider in self.providers),
            return_exceptions=True,
        )

    async def search_subtitles(self, params: SubtitleSearchParams) -> list[SubtitleResult]:
        tasks = [provider.search_subtitles(params) for provider in self.providers]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        merged: list[SubtitleResult] = []
        for provider, response in zip(self.providers, responses):
            if isinstance(response, Exception):
                logger.warning(f"{provider.name} search failed: {response}")
                continue
            logger.info(f"{provider.name} returned {len(response)} subtitle(s)")
            merged.extend(response)

        return self._sort_results(merged, params)

    async def search_subtitles_multi_lang(
        self,
        base_params: SubtitleSearchParams,
        languages: list[str],
    ) -> dict[str, list[SubtitleResult]]:
        tasks = [
            provider_search_multi_lang(provider, base_params, languages)
            for provider in self.providers
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        merged = {lang: [] for lang in languages}
        for provider, response in zip(self.providers, responses):
            if isinstance(response, Exception):
                logger.warning(f"{provider.name} multi-language search failed: {response}")
                continue
            for lang, results in response.items():
                merged.setdefault(lang, []).extend(results)

        return {
            lang: self._sort_results(results, base_params.model_copy(update={"language": lang}))
            for lang, results in merged.items()
        }

    async def download_subtitle(
        self,
        subtitle: SubtitleResult,
        dest_dir: Path,
        expected_season: int | None = None,
        expected_episode: int | None = None,
        video_filename: str | None = None,
    ) -> Path:
        provider = self._by_name.get(subtitle.provider)
        if not provider:
            raise ValueError(f"Unknown subtitle provider: {subtitle.provider}")
        return await provider.download_subtitle(
            subtitle,
            dest_dir,
            expected_season=expected_season,
            expected_episode=expected_episode,
            video_filename=video_filename,
        )

    @staticmethod
    def _sort_results(
        results: list[SubtitleResult],
        params: SubtitleSearchParams,
    ) -> list[SubtitleResult]:
        from app.clients.subtitle_provider import rank_and_filter_subtitles

        return rank_and_filter_subtitles(results, params)
