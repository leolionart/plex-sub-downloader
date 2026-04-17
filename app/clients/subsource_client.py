"""
Subsource API v1 client for subtitle search and download.
API Docs: https://subsource.net/api-docs

Flow:
1. Search movie by IMDb/TMDb ID or title → get movieId
2. Search subtitles by movieId + language → get subtitleId
3. Download subtitle by subtitleId → ZIP file
"""

import asyncio
import logging
import math
import re
import zipfile
from difflib import SequenceMatcher
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

        # In-session movie lookup cache: prevents repeating the same API call
        # across multi-language searches. Key: imdb ID or "title:year".
        # Value: movieId (int) or None (not found on Subsource).
        self._movie_id_cache: dict[str, int | None] = {}

    async def close(self) -> None:
        await self._client.aclose()

    def _to_subsource_lang(self, iso_code: str) -> str:
        """Convert ISO 639-1 code to Subsource language name."""
        return LANGUAGE_MAP.get(iso_code, iso_code)

    def _movie_cache_key(self, params: SubtitleSearchParams) -> str:
        """Build cache key: prefer IMDb ID, fallback to title+year."""
        if params.imdb_id:
            return f"imdb:{params.imdb_id}"
        return f"title:{params.title}:{params.year}"

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        """Normalize titles/release names for similarity scoring."""
        value = Path(value).stem
        value = value.lower()
        value = re.sub(r"\b(web[ ._-]?dl)\b", "webdl", value)
        value = re.sub(r"\b(web[ ._-]?rip)\b", "webrip", value)
        value = re.sub(r"\bblu[ -]?ray\b", "bluray", value)
        value = re.sub(r"\bhd[ -]?tv\b", "hdtv", value)
        value = re.sub(r"\b(ddp)[ ._-]?([257]\.1)\b", r"\1\2", value)
        value = re.sub(r"\b(h[ ._-]?264|x[ ._-]?264|avc)\b", "h264", value)
        value = re.sub(r"\b(h[ ._-]?265|x[ ._-]?265|hevc)\b", "h265", value)
        value = re.sub(r"[\[\](){}]", " ", value)
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @classmethod
    def _tokenize_match_text(cls, value: str) -> list[str]:
        normalized = cls._normalize_match_text(value)
        return normalized.split() if normalized else []

    @staticmethod
    def _release_token_weight(token: str) -> float:
        """Higher weights for release-specific tokens than for title words."""
        high_signal = {
            "webdl", "webrip", "bluray", "hdtv", "remux", "dvdrip",
            "amzn", "nf", "dsnp", "hmax", "atvp", "pcok", "1080p",
            "2160p", "720p", "h264", "h265", "ddp51", "aac", "proper",
            "repack",
        }
        if token in high_signal:
            return 3.0
        if re.fullmatch(r"s\d{1,2}e\d{1,2}", token):
            return 2.5
        if re.fullmatch(r"\d{3,4}p", token):
            return 2.5
        if re.fullmatch(r"\d{4}", token):
            return 0.5
        if len(token) <= 2:
            return 0.2
        return 1.0

    @classmethod
    def _weighted_token_overlap(cls, left: list[str], right: list[str]) -> float:
        if not left or not right:
            return 0.0

        left_counts: dict[str, int] = {}
        right_counts: dict[str, int] = {}
        for token in left:
            left_counts[token] = left_counts.get(token, 0) + 1
        for token in right:
            right_counts[token] = right_counts.get(token, 0) + 1

        all_tokens = set(left_counts) | set(right_counts)
        if not all_tokens:
            return 0.0

        intersection = 0.0
        union = 0.0
        for token in all_tokens:
            weight = cls._release_token_weight(token)
            intersection += min(left_counts.get(token, 0), right_counts.get(token, 0)) * weight
            union += max(left_counts.get(token, 0), right_counts.get(token, 0)) * weight

        return intersection / union if union else 0.0

    @classmethod
    def _title_similarity(cls, left: str, right: str) -> float:
        left_norm = cls._normalize_match_text(left)
        right_norm = cls._normalize_match_text(right)
        if not left_norm or not right_norm:
            return 0.0

        sequence_score = SequenceMatcher(None, left_norm, right_norm).ratio()
        token_score = cls._weighted_token_overlap(
            cls._tokenize_match_text(left_norm),
            cls._tokenize_match_text(right_norm),
        )
        exact_bonus = 1.0 if left_norm == right_norm else 0.0
        return min(1.0, sequence_score * 0.55 + token_score * 0.35 + exact_bonus * 0.10)

    def _movie_match_score(
        self,
        movie: dict[str, Any],
        title: str,
        year: int | None = None,
        season: int | None = None,
    ) -> float:
        """Score a movie/show result when IMDb/TMDb IDs are unavailable."""
        movie_title = str(movie.get("title") or "")
        score = self._title_similarity(title, movie_title)

        if year:
            release_year = movie.get("releaseYear")
            if isinstance(release_year, int):
                if release_year == year:
                    score += 0.25
                else:
                    score -= min(abs(release_year - year) * 0.08, 0.40)

        if season:
            movie_season = movie.get("season")
            if isinstance(movie_season, int):
                if movie_season == season:
                    score += 0.20
                else:
                    score -= 0.20
            else:
                parsed_season, _ = self._extract_season_episode(movie_title)
                if parsed_season == season:
                    score += 0.15

        subtitle_count = movie.get("subtitleCount")
        if isinstance(subtitle_count, int) and subtitle_count > 0:
            score += min(math.log10(subtitle_count + 1) * 0.03, 0.10)

        return score

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
        Results (including "not found") are cached in-session to avoid
        repeating the same API call across multi-language searches.
        """
        cache_key = self._movie_cache_key(params)
        if cache_key in self._movie_id_cache:
            cached = self._movie_id_cache[cache_key]
            logger.debug(
                f"Movie cache hit: {cache_key} → "
                f"{'movieId=' + str(cached) if cached else 'not found'}"
            )
            return cached

        movie_id: int | None = None

        # Strategy 1: Search by IMDb ID (most accurate)
        if params.imdb_id:
            movie_id = await self._search_movie_by_imdb(params.imdb_id)

        # Strategy 2: Search by title
        if not movie_id and params.title:
            movie_id = await self._search_movie_by_title(
                params.title, params.year, params.season
            )

        # Cache result (including None for "not found on Subsource")
        self._movie_id_cache[cache_key] = movie_id
        return movie_id

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
                scored_movies = [
                    (
                        movie,
                        self._movie_match_score(movie, title, year=year, season=season),
                    )
                    for movie in movies
                    if movie.get("movieId") is not None
                ]
                if not scored_movies:
                    return None

                scored_movies.sort(key=lambda item: item[1], reverse=True)
                best_movie, best_score = scored_movies[0]
                movie_id = best_movie["movieId"]
                logger.info(
                    f"Found movie via title match: {best_movie['title']} "
                    f"(movieId={movie_id}, score={best_score:.2f})"
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

    async def _search_subtitles_for_movie(
        self,
        movie_id: int,
        language: str,
        params: SubtitleSearchParams,
    ) -> list[SubtitleResult]:
        """
        Fetch and rank subtitles for a known movieId + language.
        Used internally by search_subtitles_multi_lang for parallel queries.
        """
        subsource_lang = self._to_subsource_lang(language)
        try:
            response = await self._client.get(
                f"{self.base_url}/v1/subtitles",
                params={"movieId": movie_id, "language": subsource_lang},
            )
            if response.status_code == 401:
                raise SubsourceClientError("Subsource API key invalid or missing")
            if response.status_code == 404:
                return []
            response.raise_for_status()

            data = response.json()
            results = self._parse_subtitle_results(data)
            ranked = self._rank_and_filter(results, params)
            logger.info(f"Found {len(ranked)} {subsource_lang} subtitles for movieId={movie_id}")
            return ranked

        except SubsourceClientError:
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return []
            logger.debug(f"Subtitle search failed for {language}: {e.response.status_code}")
            return []
        except Exception as e:
            logger.debug(f"Subtitle search failed for {language}: {e}")
            return []

    async def search_subtitles_multi_lang(
        self,
        params: SubtitleSearchParams,
        languages: list[str],
    ) -> dict[str, list[SubtitleResult]]:
        """
        Search subtitles for multiple languages efficiently.

        - Finds the movie ONCE (result cached in-session)
        - Fetches subtitles for all languages in PARALLEL via asyncio.gather

        Args:
            params: Base search params (title, imdb_id, season, episode, etc.)
                    The `language` field is ignored — each lang searched separately.
            languages: List of ISO 639-1 codes to search (e.g. ["vi", "en", "ko"])

        Returns:
            Dict mapping language code → sorted SubtitleResult list ([] if not found)
        """
        if not languages:
            return {}

        logger.info(f"Multi-lang search ({', '.join(languages)}) for '{params.title}'")

        # Find movie ONCE — subsequent calls for same movie are instant (cached)
        movie_id = await self._search_movie(params)
        if not movie_id:
            logger.warning(f"Movie not found on Subsource: {params.title}")
            return {lang: [] for lang in languages}

        # Search all languages in parallel
        results = await asyncio.gather(*[
            self._search_subtitles_for_movie(movie_id, lang, params)
            for lang in languages
        ])
        return dict(zip(languages, results))

    @staticmethod
    def _extract_season_episode(name: str) -> tuple[int | None, int | None]:
        """
        Extract season/episode from release name.
        Handles: S01E03, s02e01, S1E5, Season.2.Episode.1, S01.COMPLETE,
        first season, 4th season, etc.
        """
        if not name:
            return None, None

        word_to_num = {
            "first": 1,
            "one": 1,
            "second": 2,
            "two": 2,
            "third": 3,
            "three": 3,
            "fourth": 4,
            "four": 4,
            "fifth": 5,
            "five": 5,
            "sixth": 6,
            "six": 6,
            "seventh": 7,
            "seven": 7,
            "eighth": 8,
            "eight": 8,
            "ninth": 9,
            "nine": 9,
            "tenth": 10,
            "ten": 10,
        }

        # Standard SxxEyy pattern (most common)
        match = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", name)
        if match:
            return int(match.group(1)), int(match.group(2))

        # XxY pattern: 4x05, 04x5
        match = re.search(r"\b(\d{1,2})[xX](\d{1,2})\b", name)
        if match:
            return int(match.group(1)), int(match.group(2))

        # Season/episode aliases: season-4-ep-5, episode.05, ep05
        match = re.search(
            r"[Ss]eason[\s._-]*(\d{1,2})[\s._-]*(?:[Ee][Pp]?(?:isode)?)?[\s._-]*(\d{1,2})",
            name,
            re.IGNORECASE,
        )
        if match:
            return int(match.group(1)), int(match.group(2))

        # Season.X.Episode.Y pattern
        match = re.search(r"[Ss]eason\s*\.?\s*(\d{1,2})\s*\.?\s*[Ee]pisode\s*\.?\s*(\d{1,2})", name, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))

        # Season pack patterns: ".S01.", " Season 2 ", "S01.COMPLETE", etc.
        match = re.search(r"(?:^|[.\s_\-\[])[Ss](\d{1,2})(?=$|[.\s_\-\]])", name)
        if match:
            return int(match.group(1)), None

        match = re.search(r"[Ss]eason\s*\.?\s*(\d{1,2})(?!\d)", name, re.IGNORECASE)
        if match:
            return int(match.group(1)), None

        # Textual season patterns: "first season", "season four", "4th season"
        match = re.search(
            r"\b(?:(\d{1,2})(?:st|nd|rd|th)?|(first|one|second|two|third|three|fourth|four|fifth|five|sixth|six|seventh|seven|eighth|eight|ninth|nine|tenth|ten))[\s._-]+season(?=$|[\s._-])",
            name,
            re.IGNORECASE,
        )
        if match:
            if match.group(1):
                return int(match.group(1)), None
            if match.group(2):
                return word_to_num[match.group(2).lower()], None

        match = re.search(
            r"\bseason[\s._-]+(?:(\d{1,2})(?:st|nd|rd|th)?|(first|one|second|two|third|three|fourth|four|fifth|five|sixth|six|seventh|seven|eighth|eight|ninth|nine|tenth|ten))(?=$|[\s._-])",
            name,
            re.IGNORECASE,
        )
        if match:
            if match.group(1):
                return int(match.group(1)), None
            if match.group(2):
                return word_to_num[match.group(2).lower()], None

        return None, None

    def _result_sort_key(
        self,
        result: SubtitleResult,
        params: SubtitleSearchParams,
    ) -> tuple[float, int]:
        """Sort results by release similarity first, then quality score."""
        similarity = 0.0
        if params.video_filename:
            similarity = self._filename_similarity(params.video_filename, result.name)

        return (similarity, result.priority_score)

    @staticmethod
    def _filename_similarity(video_filename: str, release_name: str) -> float:
        """
        Tính độ tương đồng giữa video filename và subtitle release name.
        Kết hợp weighted token overlap + sequence similarity cho release names.

        Returns:
            float 0.0 - 1.0 (1.0 = giống hoàn toàn)
        """
        norm_video = SubsourceClient._normalize_match_text(video_filename)
        norm_release = SubsourceClient._normalize_match_text(release_name)
        if not norm_video or not norm_release:
            return 0.0

        sequence_score = SequenceMatcher(None, norm_video, norm_release).ratio()
        token_score = SubsourceClient._weighted_token_overlap(
            SubsourceClient._tokenize_match_text(norm_video),
            SubsourceClient._tokenize_match_text(norm_release),
        )

        return sequence_score * 0.40 + token_score * 0.60

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

                # Extract season/episode from release name
                parsed_season, parsed_episode = self._extract_season_episode(name)

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
                    season=parsed_season,
                    episode=parsed_episode,
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
        """
        Filter by season/episode match, then sort by priority score.

        For TV episodes (season + episode set):
        1. Hard filter: only keep subtitles matching the exact SxxEyy
        2. If no exact match, try season-only match (subtitle packs)
        3. Subtitles without parseable season/episode are only used when
           filename similarity is strong enough
        """
        if not results:
            return []

        results = [
            r for r in results
            if not params.language or r.language.lower() == params.language.lower()
        ]
        if not results:
            return []

        is_episode_search = params.season is not None and params.episode is not None

        if is_episode_search:
            # Exact match: same season AND episode
            exact_matches = [
                r for r in results
                if r.season == params.season and r.episode == params.episode
            ]

            # Season match: same season, no episode info (could be season pack)
            season_matches = [
                r for r in results
                if r.season == params.season and r.episode is None
            ]

            # No season/episode info at all (unparseable names, subtitle packs)
            unknown_matches = [
                r for r in results
                if r.season is None and r.episode is None
            ]

            if exact_matches:
                filtered = exact_matches
                logger.info(
                    f"Episode filter: {len(exact_matches)} exact S{params.season:02d}E{params.episode:02d} matches "
                    f"(filtered out {len(results) - len(exact_matches)} non-matching)"
                )
            elif season_matches:
                filtered = season_matches
                logger.info(
                    f"Episode filter: no exact match, using {len(season_matches)} season-{params.season} packs "
                    f"(filtered out {len(results) - len(season_matches)} non-matching)"
                )
            elif unknown_matches and params.video_filename:
                # Safer fallback for untagged releases:
                # only accept candidates that look substantially like the exact file.
                similarity_threshold = 0.75
                scored = [
                    (r, self._filename_similarity(params.video_filename, r.name))
                    for r in unknown_matches
                ]
                scored.sort(key=lambda x: x[1], reverse=True)
                filtered = [
                    r for r, similarity in scored
                    if similarity >= similarity_threshold
                ]

                if filtered:
                    logger.info(
                        f"Episode filter: no exact match, keeping {len(filtered)} untagged subs "
                        f"with filename similarity >= {similarity_threshold:.2f} "
                        f"(best: {scored[0][1]:.2f} — {scored[0][0].name})"
                    )
                else:
                    filtered = []
                    logger.warning(
                        f"Episode filter: rejecting {len(unknown_matches)} untagged subtitles "
                        f"for S{params.season:02d}E{params.episode:02d}; "
                        f"best filename similarity only {scored[0][1]:.2f}"
                    )
            else:
                filtered = []
                logger.warning(
                    f"Episode filter: no safe match for S{params.season:02d}E{params.episode:02d}; "
                    f"returning empty instead of guessing"
                )
        else:
            filtered = results

        if params.video_filename:
            sorted_results = sorted(
                filtered,
                key=lambda r: self._result_sort_key(r, params),
                reverse=True,
            )
        else:
            sorted_results = sorted(filtered)

        if sorted_results:
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
        expected_season: int | None = None,
        expected_episode: int | None = None,
        video_filename: str | None = None,
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
                return self._extract_subtitle_from_zip(
                    zip_path,
                    dest_dir,
                    expected_season=expected_season,
                    expected_episode=expected_episode,
                    video_filename=video_filename,
                )
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

    def _zip_member_sort_key(
        self,
        member: str,
        expected_season: int | None = None,
        expected_episode: int | None = None,
        video_filename: str | None = None,
    ) -> tuple[int, float, int]:
        """Score ZIP members to prefer the correct episode/release."""
        member_name = Path(member).name
        parsed_season, parsed_episode = self._extract_season_episode(member_name)

        match_rank = 0
        if expected_season is not None and expected_episode is not None:
            if parsed_season == expected_season and parsed_episode == expected_episode:
                match_rank = 4
            elif parsed_season == expected_season and parsed_episode is None:
                match_rank = 2
            elif parsed_season is None and parsed_episode is None:
                match_rank = 1
        elif expected_season is not None:
            if parsed_season == expected_season:
                match_rank = 2
            elif parsed_season is None:
                match_rank = 1
        else:
            match_rank = 1

        similarity = 0.0
        if video_filename:
            similarity = self._filename_similarity(video_filename, member_name)

        # Prefer shallower paths when everything else ties.
        return (match_rank, similarity, -member.count("/"))

    def _extract_subtitle_from_zip(
        self,
        zip_path: Path,
        dest_dir: Path,
        expected_season: int | None = None,
        expected_episode: int | None = None,
        video_filename: str | None = None,
    ) -> Path:
        """Extract subtitle file from ZIP archive. Supports .srt, .vtt, .ass, .ssa, .sub."""
        SUBTITLE_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sub"}

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                # Find subtitle files, prefer .srt
                all_subs = [
                    f for f in zip_ref.namelist()
                    if Path(f).suffix.lower() in SUBTITLE_EXTS
                ]

                if not all_subs:
                    raise SubsourceClientError(
                        f"No subtitle file found in ZIP (files: {zip_ref.namelist()})"
                    )

                # Prefer .srt, then choose the member that best matches the target episode/file.
                srt_files = [f for f in all_subs if f.lower().endswith(".srt")]
                candidate_files = srt_files if srt_files else all_subs
                chosen = max(
                    candidate_files,
                    key=lambda member: self._zip_member_sort_key(
                        member,
                        expected_season=expected_season,
                        expected_episode=expected_episode,
                        video_filename=video_filename,
                    ),
                )
                zip_ref.extract(chosen, dest_dir)
                extracted_path = dest_dir / chosen

                logger.info(f"Extracted subtitle: {extracted_path}")

                # Convert non-SRT to SRT (Plex only accepts .srt)
                if extracted_path.suffix.lower() != ".srt":
                    extracted_path = self._convert_to_srt(extracted_path)

                return extracted_path

        except zipfile.BadZipFile as e:
            raise SubsourceClientError("Invalid ZIP file") from e
        finally:
            zip_path.unlink(missing_ok=True)

    @staticmethod
    def _convert_to_srt(source_path: Path) -> Path:
        """Convert VTT/ASS/SSA subtitle to SRT format."""
        import re

        ext = source_path.suffix.lower()
        srt_path = source_path.with_suffix(".srt")

        try:
            content = source_path.read_text(encoding="utf-8", errors="replace")

            if ext == ".vtt":
                srt_content = SubsourceClient._vtt_to_srt(content)
            else:
                # For .ass/.ssa/.sub — just wrap as-is with basic SRT structure
                # Plex can handle these natively, but we convert for safety
                logger.warning(f"No converter for {ext}, renaming to .srt")
                srt_path.write_text(content, encoding="utf-8")
                source_path.unlink(missing_ok=True)
                return srt_path

            srt_path.write_text(srt_content, encoding="utf-8")
            source_path.unlink(missing_ok=True)
            logger.info(f"Converted {ext} → .srt: {srt_path}")
            return srt_path

        except Exception as e:
            raise SubsourceClientError(f"Failed to convert {ext} to SRT: {e}") from e

    @staticmethod
    def _vtt_to_srt(vtt_content: str) -> str:
        """Convert WebVTT content to SRT format."""
        import re

        lines = vtt_content.strip().splitlines()

        # Skip VTT header (WEBVTT and any metadata before first blank line)
        start_idx = 0
        for i, line in enumerate(lines):
            if line.strip() == "" and i > 0:
                start_idx = i + 1
                break

        # Parse cues
        srt_blocks: list[str] = []
        counter = 0
        i = start_idx

        while i < len(lines):
            line = lines[i].strip()

            # Skip empty lines
            if not line:
                i += 1
                continue

            # Skip cue identifiers (lines before timestamp that aren't timestamps)
            if "-->" not in line:
                # Check if next line has timestamp
                if i + 1 < len(lines) and "-->" in lines[i + 1]:
                    i += 1
                    continue
                # Could be text continuation, skip
                i += 1
                continue

            # Timestamp line
            timestamp_line = line
            # Convert VTT timestamps: 00:01:23.456 → 00:01:23,456
            timestamp_line = re.sub(
                r"(\d{2}:\d{2}:\d{2})\.(\d{3})",
                r"\1,\2",
                timestamp_line,
            )
            # Handle short timestamps: 01:23.456 → 00:01:23,456
            timestamp_line = re.sub(
                r"(\d{2}:\d{2})\.(\d{3})",
                lambda m: f"00:{m.group(1)},{m.group(2)}",
                timestamp_line,
            )
            # Strip position/alignment metadata after timestamps
            timestamp_line = re.sub(
                r"([\d:,]+\s*-->\s*[\d:,]+)\s+.*",
                r"\1",
                timestamp_line,
            )

            i += 1
            # Collect text lines
            text_lines: list[str] = []
            while i < len(lines) and lines[i].strip():
                # Strip VTT tags like <c>, </c>, <b>, etc.
                cleaned = re.sub(r"<[^>]+>", "", lines[i])
                text_lines.append(cleaned)
                i += 1

            if text_lines:
                counter += 1
                block = f"{counter}\n{timestamp_line}\n" + "\n".join(text_lines)
                srt_blocks.append(block)

        return "\n\n".join(srt_blocks) + "\n"
