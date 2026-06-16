"""
SubDL API client.
Docs: https://subdl.com/api-doc
"""

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.clients.subsource_client import SubsourceClient
from app.clients.subtitle_provider import (
    SubtitleProviderError,
    rank_and_filter_subtitles,
    save_subtitle_response,
)
from app.models.runtime_config import RuntimeConfig
from app.models.subtitle import SubtitleResult, SubtitleSearchParams
from app.utils.logger import get_logger

logger = get_logger(__name__)

SUBDL_USER_AGENT = "Plex Subtitle Service v0.4.1"
SUBDL_MAX_RATE_LIMIT_RETRIES = 1


class SubDLClientError(SubtitleProviderError):
    """SubDL provider error."""
    pass


class SubDLClient:
    """Client for SubDL search and download API."""

    name = "subdl"

    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config
        self.base_url = config.subdl_base_url.rstrip("/")
        self.api_key = config.subdl_api_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Accept": "application/json", "User-Agent": SUBDL_USER_AGENT},
        )

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search_subtitles(self, params: SubtitleSearchParams) -> list[SubtitleResult]:
        if not self.enabled:
            return []

        query: dict[str, Any] = {
            "api_key": self.api_key,
            "languages": params.language.upper(),
            "subs_per_page": 30,
            "releases": 1,
            "unpack": 1,
        }
        if params.imdb_id:
            query["imdb_id"] = params.imdb_id
        elif params.title:
            query["film_name"] = params.title
        if params.video_filename:
            query["file_name"] = params.video_filename
        if params.tmdb_id:
            query["tmdb_id"] = params.tmdb_id
        if params.year:
            query["year"] = params.year
        if params.season:
            query["season_number"] = params.season
            query["type"] = "tv"
        else:
            query["type"] = "movie"
        if params.episode:
            query["episode_number"] = params.episode

        try:
            response = await self._get_with_rate_limit_retry(f"{self.base_url}/subtitles", query)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise SubDLClientError(f"SubDL search failed: {self._format_status_error(e)}") from e

        data = response.json()
        if data.get("status") is False:
            raise SubDLClientError(f"SubDL search failed: {data.get('error', 'unknown error')}")
        results = self._parse_results(data, params.language)
        return self._rank_and_filter(results, params)

    def _parse_results(self, data: dict[str, Any], language: str) -> list[SubtitleResult]:
        results: list[SubtitleResult] = []
        for item in data.get("subtitles", []):
            rows = item.get("unpack_files") or [item]
            for row in rows:
                url = row.get("url")
                if not url:
                    continue
                absolute_url = self._absolute_download_url(str(url))
                name = row.get("release_name") or row.get("name") or f"subdl-{row.get('md5') or row.get('file_n_id') or len(results)}"
                season, episode = SubsourceClient._extract_season_episode(name)
                subtitle_id = str(row.get("file_n_id") or row.get("md5") or url).strip("/")
                results.append(
                    SubtitleResult(
                        id=subtitle_id,
                        provider=self.name,
                        name=name,
                        language=str(row.get("language") or language).lower(),
                        download_url=absolute_url,
                        release_info=row.get("release_name"),
                        downloads=None,
                        rating=None,
                        quality_type="translated",
                        season=row.get("season") or item.get("season") or season,
                        episode=row.get("episode") or item.get("episode") or episode,
                    )
                )
        return results

    @staticmethod
    def _absolute_download_url(url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return f"https://dl.subdl.com{url if url.startswith('/') else '/' + url}"

    @staticmethod
    def _rank_and_filter(
        results: list[SubtitleResult],
        params: SubtitleSearchParams,
    ) -> list[SubtitleResult]:
        return rank_and_filter_subtitles(results, params)

    async def download_subtitle(
        self,
        subtitle: SubtitleResult,
        dest_dir: Path,
        expected_season: int | None = None,
        expected_episode: int | None = None,
        video_filename: str | None = None,
    ) -> Path:
        try:
            response = await self._client.get(str(subtitle.download_url))
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise SubDLClientError(f"SubDL download failed: {self._format_status_error(e)}") from e
        return save_subtitle_response(response, dest_dir, f"{self.name}-{subtitle.id}")

    async def _get_with_rate_limit_retry(
        self,
        url: str,
        params: dict[str, Any],
    ) -> httpx.Response:
        response = await self._client.get(url, params=params)
        for _ in range(SUBDL_MAX_RATE_LIMIT_RETRIES):
            if response.status_code != 429:
                break
            await asyncio.sleep(self._retry_after_seconds(response))
            response = await self._client.get(url, params=params)
        return response

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 1.0), 10.0)
            except ValueError:
                pass
        try:
            body = response.json()
        except ValueError:
            body = {}
        retry_after_body = body.get("retryAfterSeconds") if isinstance(body, dict) else None
        if isinstance(retry_after_body, (int, float)):
            return min(max(float(retry_after_body), 1.0), 10.0)
        return 5.0

    @classmethod
    def _format_status_error(cls, error: httpx.HTTPStatusError) -> str:
        response = error.response
        message = (
            f"{response.status_code} {response.reason_phrase} for url "
            f"'{cls._redact_api_key(str(response.url))}'"
        )
        body = response.text.strip()
        if body:
            message = f"{message}; response_body={body[:500]}"
        return message

    @staticmethod
    def _redact_api_key(url: str) -> str:
        parts = urlsplit(url)
        query = urlencode(
            [
                (key, "***" if key.lower() == "api_key" else value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ],
            doseq=True,
            safe="*",
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
