"""
OpenSubtitles.com REST API client.
Docs: https://opensubtitles.stoplight.io/docs/opensubtitles-api
"""

from pathlib import Path
from typing import Any

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


class OpenSubtitlesClientError(SubtitleProviderError):
    """OpenSubtitles provider error."""
    pass


class OpenSubtitlesClient:
    """Client for OpenSubtitles.com REST API search and download."""

    name = "opensubtitles"

    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config
        self.base_url = config.opensubtitles_base_url.rstrip("/")
        self.api_key = config.opensubtitles_api_key
        self.username = config.opensubtitles_username
        self.password = config.opensubtitles_password
        self._token: str | None = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={
                "Api-Key": self.api_key or "",
                "User-Agent": "PlexSubtitleService/0.4.1",
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _ensure_token(self) -> str | None:
        if self._token or not (self.username and self.password):
            return self._token
        response = await self._client.post(
            f"{self.base_url}/login",
            json={"username": self.username, "password": self.password},
        )
        response.raise_for_status()
        token = response.json().get("token")
        self._token = str(token) if token else None
        return self._token

    @staticmethod
    def _imdb_number(imdb_id: str | None) -> str | None:
        if not imdb_id:
            return None
        return imdb_id[2:] if imdb_id.startswith("tt") else imdb_id

    async def search_subtitles(self, params: SubtitleSearchParams) -> list[SubtitleResult]:
        if not self.enabled:
            return []

        query: dict[str, Any] = {
            "languages": params.language,
            "order_by": "download_count",
            "order_direction": "desc",
        }
        if imdb_number := self._imdb_number(params.imdb_id):
            query["imdb_id"] = imdb_number
        elif params.title:
            query["query"] = params.title
        if params.year:
            query["year"] = params.year
        if params.season:
            query["season_number"] = params.season
        if params.episode:
            query["episode_number"] = params.episode

        try:
            response = await self._client.get(f"{self.base_url}/subtitles", params=query)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OpenSubtitlesClientError(f"OpenSubtitles search failed: {e}") from e

        results = self._parse_results(response.json(), params.language)
        return self._rank_and_filter(results, params)

    def _parse_results(self, data: dict[str, Any], language: str) -> list[SubtitleResult]:
        results: list[SubtitleResult] = []
        for item in data.get("data", []):
            attrs = item.get("attributes") or {}
            files = attrs.get("files") or []
            if not files:
                continue
            file_id = files[0].get("file_id")
            if file_id is None:
                continue

            release = attrs.get("release") or attrs.get("feature_details", {}).get("title")
            name = release or files[0].get("file_name") or f"opensubtitles-{file_id}"
            season, episode = SubsourceClient._extract_season_episode(name)
            downloads = attrs.get("download_count") or attrs.get("new_download_count") or 0
            ratings = attrs.get("ratings")
            quality = "ai" if attrs.get("machine_translated") else "translated"

            results.append(
                SubtitleResult(
                    id=str(file_id),
                    provider=self.name,
                    name=name,
                    language=attrs.get("language") or language,
                    download_url=f"opensubtitles://{file_id}",
                    release_info=release,
                    uploader=(attrs.get("uploader") or {}).get("name"),
                    rating=float(ratings) if isinstance(ratings, (int, float)) else None,
                    downloads=int(downloads) if isinstance(downloads, int) else 0,
                    quality_type=quality,
                    season=attrs.get("season_number") or season,
                    episode=attrs.get("episode_number") or episode,
                )
            )
        return results

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
        if not self.enabled:
            raise OpenSubtitlesClientError("OpenSubtitles API key is not configured")

        headers: dict[str, str] = {}
        if token := await self._ensure_token():
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = await self._client.post(
                f"{self.base_url}/download",
                json={"file_id": int(subtitle.id)},
                headers=headers,
            )
            response.raise_for_status()
            link = response.json().get("link")
            if not link:
                raise OpenSubtitlesClientError("OpenSubtitles download response has no link")
            file_response = await self._client.get(str(link), headers={})
            file_response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OpenSubtitlesClientError(f"OpenSubtitles download failed: {e}") from e

        return save_subtitle_response(file_response, dest_dir, f"{self.name}-{subtitle.id}")
