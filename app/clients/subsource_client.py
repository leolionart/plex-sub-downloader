"""
Subsource API v1 client for subtitle search and download.
API Docs: https://subsource.net/api-docs

Flow:
1. Search movie by IMDb/TMDb ID or title → get movieId
2. Search subtitles by movieId + language → get subtitleId
3. Download subtitle by subtitleId → ZIP file
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

# ISO 639-1 → Subsource full language name
LANGUAGE_MAP = {
    "vi": "vietnamese",
    "en": "english",
    "ar": "arabic",
    "bg": "bulgarian",
    "zh": "chinese",
    "hr": "croatian",
    "cs": "czech",
    "da": "danish",
    "nl": "dutch",
    "fi": "finnish",
    "fr": "french",
    "de": "german",
    "el": "greek",
    "he": "hebrew",
    "hu": "hungarian",
    "id": "indonesian",
    "it": "italian",
    "ja": "japanese",
    "ko": "korean",
    "ms": "malay",
    "no": "norwegian",
    "fa": "persian",
    "pl": "polish",
    "pt": "portuguese",
    "ro": "romanian",
    "ru": "russian",
    "sr": "serbian",
    "sk": "slovak",
    "sl": "slovenian",
    "es": "spanish",
    "sv": "swedish",
    "th": "thai",
    "tr": "turkish",
    "uk": "ukrainian",
}


class SubsourceClientError(Exception):
    """Base exception for Subsource client errors."""
    pass


class SubsourceClient:
    """
    Client for Subsource API v1.

    Two-step search flow:
    1. Search movie → get movieId
    2. Search subtitles for movieId + language
    """

    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config
        self.base_url = config.subsource_base_url.rstrip("/")
        self.api_key = config.subsource_api_key
        self.timeout = httpx.Timeout(30.0, connect=10.0)

        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "X-API-Key": self.api_key or "",
                "User-Agent": "PlexSubtitleService/0.2.0",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _to_subsource_lang(self, iso_code: str) -> str:
        """Convert ISO 639-1 code to Subsource language name."""
        return LANGUAGE_MAP.get(iso_code, iso_code)

    # ── Movie search ──────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def _search_movie(self, params: SubtitleSearchParams) -> int | None:
        """
        Search for a movie on Subsource, return movieId.

        Tries IMDb ID first, then title fallback.
        """
        # Strategy 1: Search by IMDb ID (most accurate)
        if params.imdb_id:
            movie_id = await self._search_movie_by_imdb(params.imdb_id)
            if movie_id:
                return movie_id

        # Strategy 2: Search by title
        if params.title:
            movie_id = await self._search_movie_by_title(
                params.title, params.year, params.season
            )
            if movie_id:
                return movie_id

        return None

    async def _search_movie_by_imdb(self, imdb_id: str) -> int | None:
        """Search movie by IMDb ID."""
        try:
            response = await self._client.get(
                f"{self.base_url}/v1/movies/search",
                params={"searchType": "imdb", "imdb": imdb_id},
            )
            response.raise_for_status()
            data = response.json()

            movies = data.get("data", [])
            if movies:
                movie_id = movies[0]["movieId"]
                logger.info(
                    f"Found movie via IMDb: {movies[0]['title']} "
                    f"(movieId={movie_id}, subs={movies[0].get('subtitleCount', 0)})"
                )
                return movie_id

        except httpx.HTTPStatusError as e:
            logger.warning(f"Movie search by IMDb failed: {e.response.status_code}")
        except Exception as e:
            logger.warning(f"Movie search by IMDb error: {e}")

        return None

    async def _search_movie_by_title(
        self, title: str, year: int | None = None, season: int | None = None
    ) -> int | None:
        """Search movie by title, optionally filter by year and season."""
        try:
            query_params: dict[str, Any] = {"searchType": "text", "q": title}
            if year:
                query_params["year"] = year
            if season:
                query_params["season"] = season

            response = await self._client.get(
                f"{self.base_url}/v1/movies/search",
                params=query_params,
            )
            response.raise_for_status()
            data = response.json()

            movies = data.get("data", [])
            if movies:
                # Pick best match (first result, optionally filter by year)
                for movie in movies:
                    if year and movie.get("releaseYear") == year:
                        logger.info(
                            f"Found movie via title+year: {movie['title']} "
                            f"(movieId={movie['movieId']})"
                        )
                        return movie["movieId"]

                # Fallback to first result
                movie_id = movies[0]["movieId"]
                logger.info(
                    f"Found movie via title: {movies[0]['title']} "
                    f"(movieId={movie_id})"
                )
                return movie_id

        except httpx.HTTPStatusError as e:
            logger.warning(f"Movie search by title failed: {e.response.status_code}")
        except Exception as e:
            logger.warning(f"Movie search by title error: {e}")

        return None

    # ── Subtitle search ───────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def search_subtitles(
        self,
        params: SubtitleSearchParams,
    ) -> list[SubtitleResult]:
        """
        Search subtitles: find movie first, then get subtitles.

        Args:
            params: SubtitleSearchParams with language, title, IDs, etc.

        Returns:
            List of SubtitleResult, sorted by priority
        """
        logger.info(f"Searching subtitles for: {params}")

        # Step 1: Find movie
        movie_id = await self._search_movie(params)
        if not movie_id:
            logger.warning(f"Movie not found on Subsource: {params.title}")
            return []

        # Step 2: Get subtitles for this movie
        language = self._to_subsource_lang(params.language)

        try:
            query_params: dict[str, Any] = {
                "movieId": movie_id,
                "language": language,
            }

            response = await self._client.get(
                f"{self.base_url}/v1/subtitles",
                params=query_params,
            )

            if response.status_code == 401:
                raise SubsourceClientError("Subsource API key invalid or missing")
            response.raise_for_status()

            data = response.json()
            results = self._parse_subtitle_results(data)

            logger.info(f"Found {len(results)} {language} subtitles for movieId={movie_id}")
            return self._rank_and_filter(results, params)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug("No subtitles found")
                return []
            raise SubsourceClientError(f"Subtitle search failed: {e}") from e

    def _parse_subtitle_results(self, data: dict[str, Any]) -> list[SubtitleResult]:
        """Parse Subsource API v1 subtitle response."""
        results: list[SubtitleResult] = []

        for item in data.get("data", []):
            try:
                subtitle_id = str(item["subtitleId"])
                release_info_list = item.get("releaseInfo", [])
                release_info = release_info_list[0] if release_info_list else ""

                # Build name from release info or fallback
                name = release_info or f"subtitle-{subtitle_id}"

                # Map productionType to quality_type
                production_type = (item.get("productionType") or "").lower()
                quality_map = {
                    "retail": "retail",
                    "translated": "translated",
                    "ai": "ai",
                    "machine": "ai",
                }
                quality_type = quality_map.get(production_type, "unknown")

                # Rating: use total or compute from good/bad
                rating_data = item.get("rating", {})
                good = rating_data.get("good", 0)
                total = rating_data.get("total", 0)
                rating = (good / total * 10) if total > 0 else None

                # Uploader
                contributors = item.get("contributors", [])
                uploader = contributors[0]["displayname"] if contributors else None

                # Download URL
                download_url = f"{self.base_url}/v1/subtitles/{subtitle_id}/download"

                result = SubtitleResult(
                    id=subtitle_id,
                    name=name,
                    language=item.get("language", ""),
                    download_url=download_url,
                    release_info=release_info,
                    uploader=uploader,
                    rating=rating,
                    downloads=item.get("downloads", 0),
                    quality_type=quality_type,
                )
                results.append(result)

            except Exception as e:
                logger.warning(f"Failed to parse subtitle result: {e}")
                continue

        return results

    def _rank_and_filter(
        self,
        results: list[SubtitleResult],
        params: SubtitleSearchParams,
    ) -> list[SubtitleResult]:
        """Sort subtitles by priority score."""
        if not results:
            return []

        sorted_results = sorted(results)

        logger.info(
            f"Ranked {len(sorted_results)} subtitles. "
            f"Top: {sorted_results[0].name} "
            f"(quality={sorted_results[0].quality_type}, score={sorted_results[0].priority_score})"
        )

        return sorted_results

    # ── Download ──────────────────────────────────────────────────

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
        Download subtitle file (usually a ZIP containing .srt).

        Args:
            subtitle: SubtitleResult with download_url
            dest_dir: Directory to save file

        Returns:
            Path to downloaded .srt file
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Downloading subtitle: {subtitle.name} (id={subtitle.id})")

        try:
            response = await self._client.get(str(subtitle.download_url))
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            is_zip = "zip" in content_type or "octet-stream" in content_type

            if is_zip:
                zip_path = dest_dir / f"{subtitle.id}.zip"
                zip_path.write_bytes(response.content)
                logger.debug(f"Extracting ZIP: {zip_path}")
                return self._extract_subtitle_from_zip(zip_path, dest_dir)
            else:
                srt_path = dest_dir / f"{subtitle.id}.srt"
                srt_path.write_bytes(response.content)
                logger.info(f"Downloaded subtitle to: {srt_path}")
                return srt_path

        except httpx.HTTPStatusError as e:
            raise SubsourceClientError(f"Download failed: {e}") from e
        except SubsourceClientError:
            raise
        except Exception as e:
            raise SubsourceClientError(f"Download error: {e}") from e

    def _extract_subtitle_from_zip(self, zip_path: Path, dest_dir: Path) -> Path:
        """Extract .srt file from ZIP archive."""
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                srt_files = [f for f in zip_ref.namelist() if f.lower().endswith(".srt")]

                if not srt_files:
                    raise SubsourceClientError("No .srt file found in ZIP archive")

                srt_filename = srt_files[0]
                zip_ref.extract(srt_filename, dest_dir)
                extracted_path = dest_dir / srt_filename

                logger.info(f"Extracted subtitle: {extracted_path}")
                return extracted_path

        except zipfile.BadZipFile as e:
            raise SubsourceClientError("Invalid ZIP file") from e
        finally:
            zip_path.unlink(missing_ok=True)
