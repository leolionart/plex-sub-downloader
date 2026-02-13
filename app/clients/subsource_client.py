"""
Subsource API client for subtitle search and download.
API Docs: https://subsource.net/api-docs
"""

import logging
import zipfile
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.models.runtime_config import RuntimeConfig
from app.models.subtitle import SubtitleResult, SubtitleSearchParams
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SubsourceClientError(Exception):
    """Base exception for Subsource client errors."""
    pass


class SubsourceClient:
    """
    Client để tương tác với Subsource API.

    Features:
    - Search subtitles bằng IMDb/TMDb ID hoặc title
    - Filter theo language và quality
    - Download và extract subtitle files
    """

    def __init__(self, config: RuntimeConfig) -> None:
        """Initialize Subsource client."""
        self._config = config
        self.base_url = config.subsource_base_url
        self.api_key = config.subsource_api_key
        self.timeout = httpx.Timeout(30.0, connect=10.0)

        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
                "User-Agent": "PlexSubtitleService/0.1.0",
            },
        )

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def search_subtitles(
        self,
        params: SubtitleSearchParams,
    ) -> list[SubtitleResult]:
        """
        Search subtitles trên Subsource.

        Strategy:
        1. Nếu có IMDb/TMDb ID -> search by ID (accurate nhất)
        2. Fallback: search by title + year + season/episode

        Args:
            params: SubtitleSearchParams

        Returns:
            List of SubtitleResult, sorted by priority

        TODO: Update API endpoint và response parsing dựa trên actual Subsource API docs
        """
        logger.info(f"Searching subtitles for: {params}")

        # Try search by external IDs first (most accurate)
        if params.has_external_id:
            results = await self._search_by_id(params)
            if results:
                logger.info(f"Found {len(results)} subtitles via ID search")
                return self._rank_and_filter(results, params)

        # Fallback: search by title
        results = await self._search_by_title(params)
        logger.info(f"Found {len(results)} subtitles via title search")
        return self._rank_and_filter(results, params)

    async def _search_by_id(self, params: SubtitleSearchParams) -> list[SubtitleResult]:
        """
        Search by IMDb or TMDb ID.

        TODO: Implement actual Subsource API call
        Endpoint might be: GET /subtitles?imdb_id={id}&language={lang}
        """
        search_params: dict[str, Any] = {"language": params.language}

        if params.imdb_id:
            search_params["imdb_id"] = params.imdb_id
        elif params.tmdb_id:
            search_params["tmdb_id"] = params.tmdb_id

        # For TV episodes
        if params.season:
            search_params["season"] = params.season
        if params.episode:
            search_params["episode"] = params.episode

        try:
            logger.debug(f"ID search params: {search_params}")
            response = await self._client.get(
                f"{self.base_url}/subtitles/search",
                params=search_params,
            )
            if response.status_code == 401:
                raise SubsourceClientError("Subsource API key invalid or missing")
            response.raise_for_status()

            # TODO: Parse actual API response
            # This is placeholder - adjust based on real API structure
            data = response.json()
            return self._parse_search_results(data)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug("No subtitles found via ID search")
                return []
            raise SubsourceClientError(f"API error: {e}") from e
        except Exception as e:
            logger.error(f"ID search failed: {e}")
            return []

    async def _search_by_title(self, params: SubtitleSearchParams) -> list[SubtitleResult]:
        """
        Search by title + year + season/episode.

        TODO: Implement actual Subsource API call
        Endpoint might be: GET /subtitles?query={title}&year={year}&language={lang}
        """
        if not params.title:
            logger.warning("Cannot search by title - no title provided")
            return []

        search_params: dict[str, Any] = {
            "query": params.title,
            "language": params.language,
        }

        if params.year:
            search_params["year"] = params.year
        if params.season:
            search_params["season"] = params.season
        if params.episode:
            search_params["episode"] = params.episode

        try:
            logger.debug(f"Title search params: {search_params}")
            response = await self._client.get(
                f"{self.base_url}/subtitles/search",
                params=search_params,
            )
            if response.status_code == 401:
                raise SubsourceClientError("Subsource API key invalid or missing")
            response.raise_for_status()

            # TODO: Parse actual API response
            data = response.json()
            return self._parse_search_results(data)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug("No subtitles found via title search")
                return []
            raise SubsourceClientError(f"API error: {e}") from e

    def _parse_search_results(self, data: dict[str, Any]) -> list[SubtitleResult]:
        """
        Parse API response thành SubtitleResult objects.

        TODO: Implement actual parsing logic based on Subsource API response structure

        Expected structure (example):
        {
            "results": [
                {
                    "id": "12345",
                    "name": "Movie.Name.2024.WEB-DL.Vi.srt",
                    "language": "vi",
                    "download_url": "https://...",
                    "release_info": "WEB-DL",
                    "rating": 8.5,
                    "downloads": 1234,
                    ...
                }
            ]
        }
        """
        results: list[SubtitleResult] = []

        # TODO: Replace with actual parsing
        # This is placeholder code
        for item in data.get("results", []):
            try:
                # Detect quality type from release_info or filename
                quality_type = self._detect_quality_type(item)

                result = SubtitleResult(
                    id=item["id"],
                    name=item["name"],
                    language=item["language"],
                    download_url=item["download_url"],
                    release_info=item.get("release_info"),
                    uploader=item.get("uploader"),
                    rating=item.get("rating"),
                    downloads=item.get("downloads"),
                    quality_type=quality_type,
                    imdb_id=item.get("imdb_id"),
                    tmdb_id=item.get("tmdb_id"),
                    season=item.get("season"),
                    episode=item.get("episode"),
                )
                results.append(result)

            except Exception as e:
                logger.warning(f"Failed to parse subtitle result: {e}")
                continue

        return results

    def _detect_quality_type(self, item: dict[str, Any]) -> str:
        """
        Detect subtitle quality type từ metadata.

        Heuristics:
        - "retail" keywords: BluRay, Retail, Official
        - "ai" keywords: AI, Auto-generated, Machine
        - Default: "translated"
        """
        text = f"{item.get('name', '')} {item.get('release_info', '')}".lower()

        if any(kw in text for kw in ["bluray", "retail", "official", "web-dl"]):
            return "retail"
        elif any(kw in text for kw in ["ai", "auto", "machine"]):
            return "ai"
        else:
            return "translated"

    def _rank_and_filter(
        self,
        results: list[SubtitleResult],
        params: SubtitleSearchParams,
    ) -> list[SubtitleResult]:
        """
        Filter và sort subtitles theo priority.

        1. Filter: chỉ giữ language match
        2. Sort: theo priority_score (retail > translated > ai > unknown)
        """
        # Filter by language
        filtered = [r for r in results if r.language == params.language]

        if not filtered:
            logger.warning(f"No subtitles found for language: {params.language}")
            return []

        # Sort by priority (SubtitleResult.__lt__ handles this)
        sorted_results = sorted(filtered)

        logger.info(
            f"Ranked {len(sorted_results)} subtitles. "
            f"Top result: {sorted_results[0].name} (score={sorted_results[0].priority_score})"
        )

        return sorted_results

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def download_subtitle(
        self,
        subtitle: SubtitleResult,
        dest_dir: Path,
    ) -> Path:
        """
        Download subtitle file.

        Handles:
        - Direct .srt download
        - ZIP archive extraction

        Args:
            subtitle: SubtitleResult
            dest_dir: Directory để lưu file

        Returns:
            Path to downloaded .srt file

        Raises:
            SubsourceClientError: Nếu download fail
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Downloading subtitle: {subtitle.name}")
        logger.debug(f"Download URL: {subtitle.download_url}")

        try:
            response = await self._client.get(str(subtitle.download_url))
            response.raise_for_status()

            # Detect file type from content-type or filename
            content_type = response.headers.get("content-type", "")
            is_zip = "zip" in content_type or subtitle.name.endswith(".zip")

            if is_zip:
                # Handle ZIP archive
                zip_path = dest_dir / f"{subtitle.id}.zip"
                zip_path.write_bytes(response.content)

                logger.debug(f"Extracting ZIP: {zip_path}")
                return self._extract_subtitle_from_zip(zip_path, dest_dir)
            else:
                # Direct .srt file
                srt_path = dest_dir / f"{subtitle.id}.srt"
                srt_path.write_bytes(response.content)

                logger.info(f"✓ Downloaded subtitle to: {srt_path}")
                return srt_path

        except httpx.HTTPStatusError as e:
            raise SubsourceClientError(f"Download failed: {e}") from e
        except Exception as e:
            logger.error(f"Download error: {e}")
            raise SubsourceClientError(f"Download error: {e}") from e

    def _extract_subtitle_from_zip(self, zip_path: Path, dest_dir: Path) -> Path:
        """
        Extract .srt file từ ZIP archive.

        Args:
            zip_path: Path to ZIP file
            dest_dir: Destination directory

        Returns:
            Path to extracted .srt file

        Raises:
            SubsourceClientError: Nếu không tìm thấy .srt trong ZIP
        """
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                # Find .srt files in archive
                srt_files = [f for f in zip_ref.namelist() if f.endswith(".srt")]

                if not srt_files:
                    raise SubsourceClientError("No .srt file found in ZIP archive")

                # Extract first .srt file (usually there's only one)
                srt_filename = srt_files[0]
                logger.debug(f"Extracting: {srt_filename}")

                zip_ref.extract(srt_filename, dest_dir)
                extracted_path = dest_dir / srt_filename

                logger.info(f"✓ Extracted subtitle to: {extracted_path}")
                return extracted_path

        except zipfile.BadZipFile as e:
            raise SubsourceClientError("Invalid ZIP file") from e
        finally:
            # Cleanup ZIP file
            zip_path.unlink(missing_ok=True)
