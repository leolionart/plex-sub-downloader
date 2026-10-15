"""
Plex client wrapper sử dụng python-plexapi.
Handles tất cả interactions với Plex Media Server.
"""

import copy
import concurrent.futures
import logging
from pathlib import Path
import time
from typing import cast, Any

from plexapi.server import PlexServer
from plexapi.video import Movie, Episode, Show, Season, Video
from plexapi.exceptions import NotFound, Unauthorized, BadRequest
from requests.exceptions import Timeout as RequestsTimeout, ConnectionError as RequestsConnectionError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

# Note: tenacity decorators are static; we wrap the underlying connect method
# inside a retry-enabled wrapper that reads runtime config values at call time.

from app.models.runtime_config import RuntimeConfig
from app.models.webhook import MediaMetadata
from app.utils.logger import get_logger
from app.utils.ttl_cache import TTLCache

logger = get_logger(__name__)


class PlexClientError(Exception):
    """Base exception for Plex client errors."""
    pass


class PlexClient:
    """
    Client để tương tác với Plex Media Server.

    Features:
    - Fetch video metadata từ ratingKey
    - Check existing subtitles
    - Upload subtitle files
    - Refresh metadata sau khi upload
    """

    def __init__(self, config: RuntimeConfig, mock_mode: bool = False) -> None:
        """Initialize Plex server connection (deferred if credentials missing)."""
        self._server: PlexServer | None = None
        self._config = config
        self._mock_mode = mock_mode
        self._sections_cache = TTLCache(default_ttl=1800.0, maxsize=20, copy_on_read=True)
        self._section_subtitles_cache = TTLCache(default_ttl=1800.0, maxsize=50, copy_on_read=True)
        self._section_items_cache = TTLCache(default_ttl=600.0, maxsize=200, copy_on_read=True)
        self._show_tree_cache = TTLCache(default_ttl=1200.0, maxsize=200, copy_on_read=True)
        self._movie_details_cache = TTLCache(default_ttl=1200.0, maxsize=500, copy_on_read=True)
        if not self._mock_mode:
            if self._config.plex_url and self._config.plex_token:
                self._connect()
            else:
                logger.warning("Plex credentials not configured — will connect after setup")

    def clear_subtitle_cache(self) -> None:
        """Clear cached section subtitle keys and section item caches."""
        self._section_subtitles_cache.clear()
        self._section_items_cache.clear()

    def clear_all_caches(self) -> None:
        """Clear all Plex in-memory caches."""
        self._sections_cache.clear()
        self._section_subtitles_cache.clear()
        self._section_items_cache.clear()
        self._show_tree_cache.clear()
        self._movie_details_cache.clear()

    def get_cache_stats(self) -> dict[str, Any]:
        """Return statistics across all Plex in-memory caches."""
        return {
            "sections": self._sections_cache.stats() if hasattr(self._sections_cache, "stats") else {},
            "section_items": self._section_items_cache.stats() if hasattr(self._section_items_cache, "stats") else {},
            "section_subtitles": self._section_subtitles_cache.stats() if hasattr(self._section_subtitles_cache, "stats") else {},
            "show_tree": self._show_tree_cache.stats() if hasattr(self._show_tree_cache, "stats") else {},
            "movie_details": self._movie_details_cache.stats() if hasattr(self._movie_details_cache, "stats") else {},
        }

    def invalidate_item_cache(self, rating_key: str | int | Any) -> None:
        """Invalidate cached data for a specific media item (show tree, movie details, section lists)."""
        rk = str(rating_key)
        logger.debug(f"Invalidating item cache for rating_key={rk}")

        # Invalidate movie details cache
        if hasattr(self._movie_details_cache, "invalidate_predicate"):
            self._movie_details_cache.invalidate_predicate(lambda k, v: str(k[0]) == rk)
        else:
            self._movie_details_cache = {
                k: v for k, v in self._movie_details_cache.items()
                if str(k[0]) != rk
            }

        # Invalidate show tree cache (match show key or any episode/season rating_key inside the show tree)
        def _match_show_tree(k: Any, entry: Any) -> bool:
            if str(k[0]) == rk:
                return True
            tree_data = entry.get("data", entry) if isinstance(entry, dict) else entry
            if not isinstance(tree_data, dict):
                return False
            for season in tree_data.get("seasons", []):
                if str(season.get("rating_key")) == rk:
                    return True
                for ep in season.get("episodes", []):
                    if str(ep.get("rating_key")) == rk:
                        return True
            return False

        if hasattr(self._show_tree_cache, "invalidate_predicate"):
            self._show_tree_cache.invalidate_predicate(_match_show_tree)
        else:
            keys_to_remove = [k for k, v in self._show_tree_cache.items() if _match_show_tree(k, v)]
            for k in keys_to_remove:
                self._show_tree_cache.pop(k, None)

        # Invalidate section items cache if this item is part of any cached page
        def _match_section_item(k: Any, entry: Any) -> bool:
            page_data = entry.get("data", entry) if isinstance(entry, dict) else entry
            if not isinstance(page_data, dict):
                return False
            items = page_data.get("items", [])
            return any(str(item.get("rating_key")) == rk for item in items)

        if hasattr(self._section_items_cache, "invalidate_predicate"):
            self._section_items_cache.invalidate_predicate(_match_section_item)
        else:
            sec_keys_to_remove = [k for k, v in self._section_items_cache.items() if _match_section_item(k, v)]
            for k in sec_keys_to_remove:
                self._section_items_cache.pop(k, None)

    def invalidate_section_cache(self, section_key: str | int | Any | None = None) -> None:
        """Invalidate cached section items and section subtitle keys."""
        if section_key is None:
            logger.debug("Invalidating all section caches")
            self._sections_cache.clear()
            self._section_items_cache.clear()
            self._section_subtitles_cache.clear()
        else:
            sec = str(section_key)
            logger.debug(f"Invalidating section cache for section_key={sec}")
            if hasattr(self._section_items_cache, "invalidate_predicate"):
                self._section_items_cache.invalidate_predicate(lambda k, v: str(k[0]) == sec)
            else:
                self._section_items_cache = {
                    k: v for k, v in self._section_items_cache.items()
                    if str(k[0]) != sec
                }
            if hasattr(self._section_subtitles_cache, "delete"):
                self._section_subtitles_cache.delete(sec)
            else:
                self._section_subtitles_cache.pop(sec, None)
    def _connect(self) -> None:
        """Establish connection to Plex server với retry logic."""
        max_attempts = getattr(self._config, "max_retries", 3)
        retry_delay = getattr(self._config, "retry_delay", 2)

        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=retry_delay, max=10),
            retry=retry_if_exception_type((ConnectionError, TimeoutError, RequestsConnectionError, RequestsTimeout)),
        )
        def _do_connect() -> None:
            try:
                logger.info(f"Connecting to Plex server at {self._config.plex_url}")
                if not self._config.plex_url or not self._config.plex_token:
                    raise PlexClientError("Plex URL/token missing in runtime config")
                self._server = PlexServer(self._config.plex_url, self._config.plex_token, timeout=15)
                # Log server identity
                try:
                    machine_id = getattr(self._server, "machineIdentifier", None)
                    logger.info(
                        "Connected to Plex server",
                        extra={
                            "friendlyName": self._server.friendlyName,
                            "machineIdentifier": machine_id,
                        },
                    )
                except Exception:
                    logger.info(f"Connected to Plex server: {self._server.friendlyName}")
            except Unauthorized:
                raise PlexClientError("Invalid Plex token - check Plex credentials in setup")
            except Exception as e:
                logger.error(f"Failed to connect to Plex: {e}")
                raise

        _do_connect()

    @property
    def server(self) -> PlexServer:
        """Get PlexServer instance."""
        if not self._server:
            self._connect()
        assert self._server is not None
        return self._server

    def find_by_plex_guid(self, content_type: str, plex_guid_hex: str) -> int | None:
        """
        Find library item by Plex metadata GUID (from watch.plex.tv share links).

        Args:
            content_type: 'movie' or 'episode'
            plex_guid_hex: hex ID from Plex metadata (e.g. '693716a94ae2129de4af23b4')

        Returns:
            ratingKey as int, or None if not found
        """
        guid = f"plex://{content_type}/{plex_guid_hex}"
        logger.debug(f"Searching library for GUID: {guid}")

        try:
            items = self.server.library.search(libtype=content_type, guid=guid)
            if items:
                item = items[0]
                logger.info(f"Found item by GUID: {item.title} (ratingKey={item.ratingKey})")
                return item.ratingKey
        except Exception as e:
            logger.warning(f"GUID search failed: {e}")

        return None

    def get_video(self, rating_key: str) -> Video:
        """
        Fetch video object từ ratingKey.

        Args:
            rating_key: Plex ratingKey

        Returns:
            Video object (Movie hoặc Episode)

        Raises:
            PlexClientError: If video not found hoặc không phải movie/episode
        """
        try:
            logger.debug(f"Fetching video with ratingKey: {rating_key}")
            item = self.server.fetchItem(int(rating_key))

            # Verify it's a video type we support
            if isinstance(item, (Show, Season)):
                raise PlexClientError(
                    f"Item {rating_key} is not a movie or episode (type: {type(item).__name__})"
                )

            if not isinstance(item, (Movie, Episode)):
                raise PlexClientError(
                    f"Item {rating_key} is not a supported video type (type: {type(item).__name__})"
                )

            logger.info(f"Found video: {item.title} ({item.type})")
            return cast(Video, item)

        except NotFound:
            raise PlexClientError(f"Video with ratingKey {rating_key} not found")
        except ValueError as e:
            raise PlexClientError(f"Invalid ratingKey format: {rating_key}") from e

    def get_item(self, rating_key: str) -> Any:
        """
        Fetch any media item (Movie, Show, Season, Episode) from ratingKey.

        Args:
            rating_key: Plex ratingKey

        Returns:
            Plex item object

        Raises:
            PlexClientError: If item not found or invalid ratingKey
        """
        if self._mock_mode:
            from unittest.mock import MagicMock
            mock_item = MagicMock()
            mock_item.rating_key = str(rating_key)
            mock_item.thumb = f"/mock/thumb/{rating_key}.jpg"
            mock_item.title = "Mock Media"
            return mock_item

        try:
            logger.debug(f"Fetching media item with ratingKey: {rating_key}")
            return self.server.fetchItem(int(rating_key))
        except NotFound:
            raise PlexClientError(f"Item with ratingKey {rating_key} not found")
        except ValueError as e:
            raise PlexClientError(f"Invalid ratingKey format: {rating_key}") from e
        except Exception as e:
            raise PlexClientError(f"Failed to fetch item {rating_key}: {e}") from e

    def get_media_characters_context(self, rating_key: str) -> dict:
        """
        Fetch rich character and plot context for a media item.
        Inherits show/series roles for episodes if episode roles are empty.
        """
        if self._mock_mode:
            return {
                "rating_key": str(rating_key),
                "title": "Mock Media",
                "show_title": None,
                "year": 2024,
                "summary": "Mock summary plot",
                "genres": ["Action", "Sci-Fi"],
                "roles": [
                    {"actor": "Actor One", "character": "Character One"},
                    {"actor": "Actor Two", "character": "Character Two"},
                ],
                "parent_rating_key": str(rating_key),
            }

        try:
            item = self.get_item(rating_key)
            item_type = getattr(item, "type", None)

            show_title = None
            parent_key = str(rating_key)
            roles = []

            if item_type == "episode":
                show_title = getattr(item, "grandparentTitle", None)
                grandparent_key = getattr(item, "grandparentRatingKey", None)
                if grandparent_key:
                    parent_key = str(grandparent_key)

                ep_roles = getattr(item, "roles", []) or []
                for r in ep_roles:
                    char_name = getattr(r, "role", None) or getattr(r, "tag", None)
                    if char_name:
                        roles.append({"actor": getattr(r, "tag", ""), "character": getattr(r, "role", char_name)})

                if not roles and grandparent_key:
                    try:
                        show_item = self.get_item(str(grandparent_key))
                        for r in (getattr(show_item, "roles", []) or [])[:15]:
                            char_name = getattr(r, "role", None)
                            if char_name:
                                roles.append({"actor": getattr(r, "tag", ""), "character": char_name})
                    except Exception as e:
                        logger.debug(f"Could not load grandparent roles for episode {rating_key}: {e}")
            else:
                for r in (getattr(item, "roles", []) or [])[:15]:
                    char_name = getattr(r, "role", None)
                    if char_name:
                        roles.append({"actor": getattr(r, "tag", ""), "character": char_name})

            return {
                "rating_key": str(rating_key),
                "title": getattr(item, "title", "Unknown"),
                "show_title": show_title,
                "year": getattr(item, "year", None),
                "summary": getattr(item, "summary", "") or "",
                "genres": [g.tag for g in getattr(item, "genres", [])] if hasattr(item, "genres") and item.genres else [],
                "roles": roles,
                "parent_rating_key": parent_key,
            }
        except Exception as e:
            logger.warning(f"Error fetching character context for {rating_key}: {e}")
            return {
                "rating_key": str(rating_key),
                "title": "Unknown",
                "show_title": None,
                "year": None,
                "summary": "",
                "genres": [],
                "roles": [],
                "parent_rating_key": str(rating_key),
            }

    def extract_metadata(self, video: Video) -> MediaMetadata:
        """
        Extract metadata từ Plex Video object.

        Args:
            video: Plex Video object (Movie hoặc Episode)

        Returns:
            MediaMetadata object với normalized data
        """
        logger.debug(f"Extracting metadata from {video.title}")

        # Get existing subtitle languages
        existing_langs = self._get_existing_subtitle_languages(video)

        # Common metadata
        base_data = {
            "rating_key": str(video.ratingKey),
            "title": video.title,
            "year": getattr(video, "year", None),
            "existing_subtitle_languages": existing_langs,
        }

        if isinstance(video, Movie):
            # Movie metadata
            metadata = MediaMetadata(
                media_type="movie",
                imdb_id=self._extract_guid(video, "imdb"),
                tmdb_id=self._extract_guid(video, "tmdb"),
                **base_data,
            )
        elif isinstance(video, Episode):
            # Episode metadata
            episode = cast(Episode, video)
            show = episode.show()

            metadata = MediaMetadata(
                media_type="episode",
                show_title=show.title,
                season_number=episode.seasonNumber,
                episode_number=episode.episodeNumber,
                imdb_id=self._extract_guid(show, "imdb"),  # Show's IMDb ID
                tmdb_id=self._extract_guid(show, "tmdb"),  # Show's TMDb ID
                **base_data,
            )
        elif isinstance(video, Show):
            metadata = MediaMetadata(
                media_type="show",
                imdb_id=self._extract_guid(video, "imdb"),
                tmdb_id=self._extract_guid(video, "tmdb"),
                **base_data,
            )
        elif isinstance(video, Season):
            show = video.show()
            metadata = MediaMetadata(
                media_type="season",
                show_title=show.title,
                season_number=video.seasonNumber,
                imdb_id=self._extract_guid(show, "imdb"),
                tmdb_id=self._extract_guid(show, "tmdb"),
                **base_data,
            )
        else:
            # Fallback for unknown video types
            metadata = MediaMetadata(
                media_type="movie",  # Default to movie if unknown
                **base_data,
            )

        logger.info(f"Extracted metadata: {metadata}")
        return metadata

    @staticmethod
    def normalize_language(lang: str | None) -> str | None:
        """
        Normalize language code (e.g. 'vie' -> 'vi', 'eng' -> 'en').
        Supports ISO 639-1 and ISO 639-2.
        """
        if not lang:
            return None
        lang = lang.lower().strip()
        iso_map = {
            "vie": "vi", "vietnamese": "vi", "eng": "en", "english": "en",
            "fra": "fr", "fre": "fr",
            "deu": "de", "ger": "de", "zho": "zh", "chi": "zh",
            "spa": "es", "rus": "ru", "jpn": "ja", "kor": "ko",
            "ara": "ar", "bul": "bg", "hrv": "hr", "ces": "cs",
            "cze": "cs", "dan": "da", "nld": "nl", "dut": "nl",
            "fin": "fi", "ell": "el", "gre": "el", "heb": "he",
            "hun": "hu", "ind": "id", "ita": "it", "msa": "ms",
            "may": "ms", "nor": "no", "fas": "fa", "per": "fa",
            "pol": "pl", "por": "pt", "ron": "ro", "rum": "ro",
            "swe": "sv", "tha": "th", "tur": "tr", "ukr": "uk"
        }
        return iso_map.get(lang, lang)

    @staticmethod
    def _stream_matches_language(stream, language: str) -> bool:
        """
        Check if a subtitle stream matches the given language code.

        Plex exposes multiple language fields:
        - languageTag: ISO 639-1 (2-letter, e.g. "vi", "en")
        - languageCode: ISO 639-2 (3-letter, e.g. "vie", "eng")
        - language: Language name or code (e.g. "vi", "vie", "Vietnamese")
        """
        tag = getattr(stream, "languageTag", None)
        code = getattr(stream, "languageCode", None)
        lang = getattr(stream, "language", None)
        target_norm = PlexClient.normalize_language(language)

        if target_norm:
            if (
                PlexClient.normalize_language(tag) == target_norm or
                PlexClient.normalize_language(code) == target_norm or
                PlexClient.normalize_language(lang) == target_norm
            ):
                return True

        if target_norm == "vi" or (language and language.lower().strip() in ("vi", "vie")):
            vi_codes = {"vi", "vie", "vietnamese"}
            if (
                (tag and str(tag).lower().strip() in vi_codes) or
                (code and str(code).lower().strip() in vi_codes) or
                (lang and str(lang).lower().strip() in vi_codes)
            ):
                return True

        if target_norm == "en" or (language and language.lower().strip() in ("en", "eng")):
            en_codes = {"en", "eng", "english"}
            if (
                (tag and str(tag).lower().strip() in en_codes) or
                (code and str(code).lower().strip() in en_codes) or
                (lang and str(lang).lower().strip() in en_codes)
            ):
                return True

        return False

    def _get_existing_subtitle_languages(self, video: Video) -> list[str]:
        """
        Extract danh sách subtitle languages đã có sẵn.

        Plex structure: Video -> Media -> Part -> Stream
        Subtitle streams có streamType=3

        Returns ISO 639-1 (languageTag) codes when available.
        """
        languages: set[str] = set()

        try:
            for media in video.media:
                for part in media.parts:
                    for stream in part.streams:
                        if stream.streamType == 3:
                            # Prefer languageTag (ISO 639-1: "vi") over languageCode (ISO 639-2: "vie")
                            tag = getattr(stream, "languageTag", None)
                            code = getattr(stream, "languageCode", None)
                            lang = tag or code
                            if lang:
                                norm_lang = PlexClient.normalize_language(lang)
                                if norm_lang:
                                    languages.add(norm_lang)

            logger.debug(f"Found existing subtitles: {languages}")
            return list(languages)

        except Exception as e:
            logger.warning(f"Error checking existing subtitles: {e}")
            return []

    def get_subtitle_details(self, video: Video, language: str = "vi") -> dict:
        """
        Lấy thông tin chi tiết về subtitles đã có.

        Args:
            language: ISO 639-1 code (e.g. "vi", "en")

        Returns:
            Dict với info về subtitles:
            - has_subtitle: bool
            - subtitle_count: int
            - subtitle_info: list of {codec, forced, title, format}
        """
        subtitle_info = []

        try:
            streams_to_check = []
            for media in getattr(video, "media", []):
                for part in getattr(media, "parts", []):
                    for stream in getattr(part, "streams", []):
                        if getattr(stream, "streamType", None) == 3:
                            streams_to_check.append(stream)

            if not streams_to_check and hasattr(video, "subtitleStreams") and callable(video.subtitleStreams):
                try:
                    streams_to_check = list(video.subtitleStreams())
                except Exception:
                    pass

            for stream in streams_to_check:
                if self._stream_matches_language(stream, language):
                    codec = getattr(stream, "codec", "unknown") or "unknown"
                    key = getattr(stream, "key", None)
                    is_image_based = str(codec).lower() in ("pgs", "vobsub", "dvdsub", "dvd_subtitle")
                    subtitle_info.append({
                        "codec": codec,
                        "forced": getattr(stream, "forced", False),
                        "title": getattr(stream, "title", ""),
                        "format": getattr(stream, "format", ""),
                        "has_key": key is not None,
                        "is_embedded": key is None,
                        "is_image_based": is_image_based,
                    })

            logger.debug(f"Subtitle details for '{language}': {len(subtitle_info)} found")
            return {
                "has_subtitle": len(subtitle_info) > 0,
                "subtitle_count": len(subtitle_info),
                "subtitle_info": subtitle_info,
            }

        except Exception as e:
            logger.warning(f"Error getting subtitle details: {e}")
            return {"has_subtitle": False, "subtitle_count": 0, "subtitle_info": []}

    def _extract_guid(self, item: Video | Episode, provider: str) -> str | None:
        """
        Extract external ID (IMDb/TMDb) từ Plex GUID.

        Plex GUIDs format:
        - IMDb: imdb://tt1234567
        - TMDb: tmdb://12345
        - TVDb: tvdb://12345

        Args:
            item: Plex item (Movie, Episode, hoặc Show)
            provider: 'imdb' hoặc 'tmdb'

        Returns:
            ID string hoặc None
        """
        try:
            for guid in item.guids:
                if guid.id.startswith(f"{provider}://"):
                    external_id = guid.id.split("://")[1]
                    logger.debug(f"Found {provider} ID: {external_id}")
                    return external_id
        except Exception as e:
            logger.warning(f"Error extracting {provider} GUID: {e}")

        return None

    def has_subtitle(self, video: Video, language: str = "vi") -> bool:
        """
        Check xem video đã có subtitle cho language này chưa.

        Args:
            video: Plex Video object
            language: ISO 639-1 language code (e.g., 'vi')

        Returns:
            True nếu đã có subtitle
        """
        existing_langs = self._get_existing_subtitle_languages(video)
        has_sub = PlexClient.normalize_language(language) in existing_langs

        logger.info(
            f"Subtitle check for '{video.title}' (lang={language}): "
            f"{'FOUND' if has_sub else 'NOT FOUND'}"
        )
        return has_sub

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def upload_subtitle(
        self,
        video: Video,
        subtitle_path: Path,
        language: str = "vi",
    ) -> bool:
        """
        Upload subtitle file lên Plex.

        Args:
            video: Plex Video object
            subtitle_path: Đường dẫn tới file .srt
            language: ISO 639-1 language code

        Returns:
            True nếu upload thành công

        Raises:
            PlexClientError: Nếu upload fail
        """
        if not subtitle_path.exists():
            raise PlexClientError(f"Subtitle file not found: {subtitle_path}")

        if subtitle_path.suffix.lower() != ".srt":
            raise PlexClientError(f"Only .srt files supported, got: {subtitle_path.suffix}")

        try:
            logger.info(f"Uploading subtitle for '{video.title}' (lang={language})")
            logger.debug(f"Subtitle path: {subtitle_path}")

            # Rename file để include language code (Plex convention) nếu chưa có
            if not subtitle_path.name.lower().endswith(f".{language.lower()}.srt"):
                temp_path = subtitle_path.parent / f"{subtitle_path.stem}.{language}.srt"
                subtitle_path.rename(temp_path)
            else:
                temp_path = subtitle_path

            video.uploadSubtitles(str(temp_path))

            logger.info(f"✓ Successfully uploaded subtitle for '{video.title}'")

            # Trigger refresh để Plex scan subtitle mới
            self._refresh_metadata(video)
            self.clear_subtitle_cache()

            rk = getattr(video, "ratingKey", None)
            if rk is not None:
                self.invalidate_item_cache(rk)
            gp_rk = getattr(video, "grandparentRatingKey", None)
            if gp_rk is not None:
                self.invalidate_item_cache(gp_rk)

            sec_id = getattr(video, "librarySectionID", None) or getattr(video, "sectionKey", None)
            if sec_id is not None and isinstance(sec_id, (str, int)):
                self.invalidate_section_cache(sec_id)
            else:
                self.invalidate_section_cache(None)

            return True

        except BadRequest as e:
            raise PlexClientError(f"Plex rejected subtitle upload: {e}") from e
        except Exception as e:
            logger.error(f"Failed to upload subtitle: {e}")
            raise PlexClientError(f"Upload failed: {e}") from e

    def remove_external_subtitles(
        self,
        video: Video,
        language: str,
    ) -> int:
        """
        Remove tất cả external subtitles (uploaded/sidecar) cho language chỉ định.
        Embedded subtitles (PGS, VobSub) không bị ảnh hưởng.

        Args:
            video: Plex Video object
            language: ISO 639-1 language code (e.g., 'vi')

        Returns:
            Số subtitle đã xóa
        """
        removed = 0
        try:
            target_norm = PlexClient.normalize_language(language)
            for stream in video.subtitleStreams():
                tag = getattr(stream, "languageTag", None)
                code = getattr(stream, "languageCode", None)
                stream_lang = tag or code

                # Only remove external (uploaded/sidecar), skip embedded
                stream_format = getattr(stream, "format", "") or ""
                is_embedded = stream_format.lower() in ("pgs", "vobsub", "dvdsub")

                if (
                    PlexClient.normalize_language(stream_lang) == target_norm
                    and not is_embedded
                    and hasattr(stream, "key")
                    and stream.key
                ):
                    try:
                        video.removeSubtitles(subtitleStream=stream)
                        removed += 1
                        logger.info(f"Removed external subtitle: {getattr(stream, 'title', stream.id)} (lang={language})")
                    except Exception as e:
                        logger.warning(f"Failed to remove subtitle stream {stream.id}: {e}")

            if removed:
                self._refresh_metadata(video)
                self.clear_subtitle_cache()

                rk = getattr(video, "ratingKey", None)
                if rk is not None:
                    self.invalidate_item_cache(rk)
                gp_rk = getattr(video, "grandparentRatingKey", None)
                if gp_rk is not None:
                    self.invalidate_item_cache(gp_rk)

                sec_id = getattr(video, "librarySectionID", None) or getattr(video, "sectionKey", None)
                if sec_id is not None and isinstance(sec_id, (str, int)):
                    self.invalidate_section_cache(sec_id)
                else:
                    self.invalidate_section_cache(None)
        except Exception as e:
            logger.warning(f"Error removing subtitles: {e}")

        return removed

    def download_existing_subtitle(
        self,
        video: Video,
        language: str,
        dest_dir: Path,
    ) -> Path | None:
        """
        Download subtitle hiện có trên Plex cho language chỉ định.

        Chỉ hỗ trợ text-based subtitles (SRT, ASS, SSA).
        Không hỗ trợ image-based (PGS, VobSub).

        Args:
            video: Plex Video object
            language: Language code (e.g. 'en')
            dest_dir: Directory to save subtitle

        Returns:
            Path to downloaded file, or None if not found
        """
        TEXT_CODECS = {"srt", "ass", "ssa", "subrip", "text", "mov_text", "webvtt"}

        try:
            for media in video.media:
                for part in media.parts:
                    for stream in part.streams:
                        if stream.streamType != 3:
                            continue
                        if not self._stream_matches_language(stream, language):
                            continue

                        codec = getattr(stream, "codec", "") or ""
                        if codec.lower() not in TEXT_CODECS:
                            logger.debug(
                                f"Skipping non-text subtitle: codec={codec}"
                            )
                            continue

                        key = getattr(stream, "key", None)
                        if not key:
                            continue

                        # Download from Plex server
                        url = self.server.url(key)
                        response = self.server._session.get(
                            url,
                            headers={"X-Plex-Token": self._config.plex_token},
                        )

                        if not response.ok:
                            logger.warning(
                                f"Failed to download subtitle stream: {response.status_code}"
                            )
                            continue

                        dest_dir.mkdir(parents=True, exist_ok=True)
                        dest_path = dest_dir / f"plex_existing.{language}.srt"
                        dest_path.write_bytes(response.content)

                        logger.info(
                            f"Downloaded existing {language} subtitle from Plex: "
                            f"{len(response.content)} bytes"
                        )
                        return dest_path

            logger.debug(f"No downloadable {language} subtitle found on Plex")
            return None

        except Exception as e:
            logger.warning(f"Error downloading subtitle from Plex: {e}")
            return None

    def _refresh_metadata(self, video: Video) -> None:
        """
        Trigger Plex refresh metadata sau khi upload subtitle.
        Giúp subtitle hiển thị ngay lập tức trong UI.
        """
        try:
            logger.debug(f"Refreshing metadata for '{video.title}'")
            video.refresh()
        except Exception as e:
            logger.warning(f"Failed to refresh metadata (non-critical): {e}")

    def get_sessions(self) -> list[dict]:
        """Get currently active playback sessions from Plex."""
        try:
            sessions = self.server.sessions()
            results = []
            for session in sessions:
                if not isinstance(session, (Movie, Episode)):
                    continue
                metadata = self.extract_metadata(session)
                player = getattr(session, "player", None)
                thumb = getattr(session, "thumb", None)
                duration = getattr(session, "duration", 0) or 0
                view_offset = getattr(session, "viewOffset", 0) or 0
                results.append({
                    "rating_key": str(session.ratingKey),
                    "title": str(metadata),
                    "media_type": metadata.media_type,
                    "year": metadata.year,
                    "thumb": thumb,
                    "player": {
                        "title": getattr(player, "title", "Unknown") if player else "Unknown",
                        "state": getattr(player, "state", "unknown") if player else "unknown",
                    },
                    "progress": round((view_offset / duration) * 100) if duration > 0 else 0,
                })
            return results
        except Exception as e:
            logger.warning(f"Failed to get sessions: {e}")
            return []

    def get_on_deck(self, limit: int = 10) -> list[dict]:
        """Get on-deck items (started but not finished)."""
        try:
            items = self.server.library.onDeck()[:limit]
            results = []
            seen_keys: set[str] = set()
            for item in items:
                if not isinstance(item, (Movie, Episode)):
                    continue
                key = str(item.ratingKey)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                metadata = self.extract_metadata(item)
                results.append({
                    "rating_key": key,
                    "title": str(metadata),
                    "media_type": metadata.media_type,
                    "year": metadata.year,
                    "thumb": getattr(item, "thumb", None),
                })
            return results
        except Exception as e:
            logger.warning(f"Failed to get on-deck: {e}")
            return []

    def get_thumb_url(self, thumb_path: str | None) -> str | None:
        """Generate authenticated thumbnail URL."""
        if not thumb_path:
            return None
        try:
            return self.server.url(thumb_path, includeToken=True)
        except Exception:
            return None

    def get_sections(self, use_cache: bool = True) -> list[dict]:
        """Get all library sections (movies, shows, etc.)."""
        if use_cache:
            cached = self._sections_cache.get("all")
            if cached is not None:
                logger.debug("Cache hit for library sections")
                return copy.deepcopy(cached)

        if self._mock_mode:
            result = [
                {"key": "1", "title": "Movies", "type": "movie"},
                {"key": "2", "title": "TV Shows", "type": "show"},
            ]
            if use_cache:
                self._sections_cache.set("all", result)
            return result
        try:
            sections = self.server.library.sections()
            result = [
                {
                    "key": str(s.key),
                    "title": s.title,
                    "type": s.type,
                }
                for s in sections
            ]
            if use_cache:
                self._sections_cache.set("all", result)
            return result
        except Exception as e:
            logger.error(f"Failed to get library sections: {e}")
            raise PlexClientError(f"Failed to fetch library sections: {e}") from e

    def _get_section_subtitle_keys(self, section: Any, section_key: str, use_cache: bool = True) -> tuple[set[str], set[str]]:
        """
        Get ratingKeys of items that have Vietnamese and English subtitles for a library section.
        Caches results in memory to avoid repeated queries during pagination.
        """
        sk = str(section_key)
        if use_cache:
            cached = self._section_subtitles_cache.get(sk)
            if cached is not None:
                if isinstance(cached, dict) and "vi_keys" in cached and "en_keys" in cached:
                    return cached["vi_keys"], cached["en_keys"]

        def _fetch_vi():
            try:
                return {str(getattr(i, "ratingKey", i)) for i in section.search(filters={"subtitleLanguage": "vi"})}
            except Exception as e:
                logger.warning(f"Failed to fetch vi subtitle keys for section {section_key}: {e}")
                return set()

        def _fetch_en():
            try:
                return {str(getattr(i, "ratingKey", i)) for i in section.search(filters={"subtitleLanguage": "en"})}
            except Exception as e:
                logger.warning(f"Failed to fetch en subtitle keys for section {section_key}: {e}")
                return set()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_vi = executor.submit(_fetch_vi)
            fut_en = executor.submit(_fetch_en)
            vi_keys = fut_vi.result()
            en_keys = fut_en.result()

        res = {
            "vi_keys": vi_keys,
            "en_keys": en_keys,
        }
        if use_cache:
            self._section_subtitles_cache.set(sk, res)
        return vi_keys, en_keys


    def get_section_items(
        self,
        section_key: str,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
        subtitle_filter: str = "missing_vi",
        sort: str | None = None,
        use_cache: bool = True,
    ) -> dict:
        """Fetch items in a library section with optional search, subtitle filter, sort, and pagination."""
        cache_key = (
            str(section_key),
            query.strip() if query and query.strip() else None,
            subtitle_filter,
            sort,
            offset,
            limit,
        )
        if use_cache:
            cached = self._section_items_cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for section items: {cache_key}")
                data = cached.get("data") if isinstance(cached, dict) and "data" in cached else cached
                return copy.deepcopy(data)
        else:
            if hasattr(self._section_items_cache, "delete"):
                self._section_items_cache.delete(cache_key)
            else:
                self._section_items_cache.pop(cache_key, None)

        if self._mock_mode:
            result = {
                "section_key": str(section_key),
                "section_title": "Mock Section",
                "section_type": "movie",
                "total_size": 1,
                "offset": offset,
                "limit": limit,
                "stats": {
                    "total": 1,
                    "has_vi": 1 if subtitle_filter == "has_vi" else 0,
                    "missing_vi": 0 if subtitle_filter == "has_vi" else 1,
                    "can_translate": 1,
                },
                "items": [
                    {
                        "rating_key": "1001",
                        "title": "Mock Movie",
                        "year": 2024,
                        "type": "movie",
                        "thumb": None,
                        "summary": "Mock summary",
                        "genres": ["Action", "Sci-Fi"],
                        "has_vi": False,
                        "has_en": True,
                        "can_translate": True,
                    }
                ],
            }
            if use_cache:
                self._section_items_cache.set(cache_key, result)
            return result

        try:
            section = self.server.library.sectionByID(int(section_key))
            filters = {}
            if subtitle_filter == "missing_vi":
                filters["subtitleLanguage!"] = "vi"
            elif subtitle_filter == "has_vi":
                filters["subtitleLanguage"] = "vi"
            elif subtitle_filter == "can_translate":
                filters["subtitleLanguage!"] = "vi"
                filters["subtitleLanguage"] = "en"

            title_query = query.strip() if query and query.strip() else None

            paged = section.search(
                title=title_query,
                filters=filters or None,
                sort=sort or None,
                container_start=offset,
                maxresults=limit,
            )
            total = getattr(paged, "totalSize", len(paged))
            vi_keys, en_keys = self._get_section_subtitle_keys(section, str(section_key))

            items = []
            for item in paged:
                genres = [g.tag for g in getattr(item, "genres", [])] if hasattr(item, "genres") and item.genres else []
                rk = str(item.ratingKey)
                has_vi = rk in vi_keys
                can_translate = (rk not in vi_keys and rk in en_keys)
                items.append({
                    "rating_key": rk,
                    "title": item.title,
                    "year": getattr(item, "year", None),
                    "type": item.type,
                    "thumb": getattr(item, "thumb", None),
                    "summary": getattr(item, "summary", "") or "",
                    "genres": genres,
                    "has_vi": has_vi,
                    "has_en": rk in en_keys,
                    "can_translate": can_translate,
                })

            total_size_attr = getattr(section, "totalSize", None)
            total_in_section = total_size_attr if isinstance(total_size_attr, int) else total
            result = {
                "section_key": str(section_key),
                "section_title": section.title,
                "section_type": section.type,
                "total_size": total,
                "offset": offset,
                "limit": limit,
                "stats": {
                    "total": total_in_section,
                    "has_vi": len(vi_keys),
                    "missing_vi": max(0, total_in_section - len(vi_keys)),
                    "can_translate": len(en_keys - vi_keys),
                },
                "items": items,
            }
            if use_cache:
                self._section_items_cache.set(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"Failed to get items for section {section_key}: {e}")
            raise PlexClientError(f"Failed to get section items: {e}") from e

    def get_show_tree(
        self,
        rating_key: str,
        target_lang: str = "vi",
        source_lang: str = "en",
        use_cache: bool = True,
    ) -> dict:
        """Fetch show hierarchy with season/episode subtitle audit status."""
        cache_key = (str(rating_key), str(target_lang), str(source_lang))
        if use_cache:
            cached = self._show_tree_cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for show tree: {cache_key}")
                data = cached.get("data") if isinstance(cached, dict) and "data" in cached else cached
                return copy.deepcopy(data)
        else:
            if hasattr(self._show_tree_cache, "delete"):
                self._show_tree_cache.delete(cache_key)
            else:
                self._show_tree_cache.pop(cache_key, None)

        if self._mock_mode:
            result = {
                "rating_key": str(rating_key),
                "title": "Mock Show",
                "year": 2024,
                "thumb": None,
                "summary": "Mock summary",
                "total_episodes": 1,
                "vi_count": 0,
                "can_translate_count": 1,
                "missing_vi_count": 1,
                "seasons": [
                    {
                        "rating_key": "2001",
                        "season_number": 1,
                        "title": "Season 1",
                        "episodes": [
                            {
                                "rating_key": "3001",
                                "season_number": 1,
                                "episode_number": 1,
                                "title": "Episode 1",
                                "has_vi": False,
                                "has_en": True,
                                "can_translate": True,
                                "vi_details": {"has_subtitle": False, "subtitle_count": 0, "subtitle_info": []},
                                "en_details": {"has_subtitle": True, "subtitle_count": 1, "subtitle_info": []},
                            }
                        ],
                    }
                ],
            }
            if use_cache:
                self._show_tree_cache.set(cache_key, result)
            return result


        try:
            item = self.server.fetchItem(int(rating_key))
            if item.type != "show":
                raise PlexClientError(f"Item {rating_key} is of type {item.type}, expected 'show'")

            seasons_data = []
            total_episodes = 0
            vi_count = 0
            can_translate_count = 0

            for season in item.seasons():
                s_num = getattr(season, "index", 0) or 0
                episodes_data = []
                for ep in season.episodes():
                    total_episodes += 1
                    ep_key = str(ep.ratingKey)
                    ep_num = getattr(ep, "index", 0) or 0

                    vi_det = self.get_subtitle_details(ep, target_lang)
                    if not vi_det.get("has_subtitle") and target_lang in ("vi", "vie"):
                        alt_target = "vie" if target_lang == "vi" else "vi"
                        alt_vi_det = self.get_subtitle_details(ep, alt_target)
                        if alt_vi_det.get("has_subtitle"):
                            vi_det = alt_vi_det

                    en_det = self.get_subtitle_details(ep, source_lang)
                    if not en_det.get("has_subtitle") and source_lang in ("en", "eng"):
                        alt_source = "eng" if source_lang == "en" else "en"
                        alt_en_det = self.get_subtitle_details(ep, alt_source)
                        if alt_en_det.get("has_subtitle"):
                            en_det = alt_en_det
                    has_vi = vi_det.get("has_subtitle", False)
                    has_en = en_det.get("has_subtitle", False)
                    can_translate = False
                    if not has_vi and has_en:
                        infos = en_det.get("subtitle_info", [])
                        can_translate = any(not info.get("is_image_based", False) for info in infos)

                    if has_vi:
                        vi_count += 1
                    elif can_translate:
                        can_translate_count += 1

                    episodes_data.append({
                        "rating_key": ep_key,
                        "season_number": s_num,
                        "episode_number": ep_num,
                        "title": ep.title,
                        "thumb": getattr(ep, "thumb", None),
                        "has_vi": has_vi,
                        "has_en": has_en,
                        "can_translate": can_translate,
                        "vi_details": vi_det,
                        "en_details": en_det,
                    })

                seasons_data.append({
                    "rating_key": str(season.ratingKey),
                    "season_number": s_num,
                    "title": season.title,
                    "episodes": episodes_data,
                })

            result = {
                "rating_key": str(item.ratingKey),
                "title": item.title,
                "year": getattr(item, "year", None),
                "thumb": getattr(item, "thumb", None),
                "summary": getattr(item, "summary", "") or "",
                "total_episodes": total_episodes,
                "vi_count": vi_count,
                "can_translate_count": can_translate_count,
                "missing_vi_count": total_episodes - vi_count,
                "seasons": seasons_data,
            }
            if use_cache:
                self._show_tree_cache.set(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"Failed to get show tree for {rating_key}: {e}")
            raise PlexClientError(f"Failed to get show tree: {e}") from e

    def get_movie_details(
        self,
        rating_key: str,
        target_lang: str = "vi",
        source_lang: str = "en",
        use_cache: bool = True,
    ) -> dict:
        """Fetch movie details and subtitle status."""
        cache_key = (str(rating_key), str(target_lang), str(source_lang))
        if use_cache:
            cached = self._movie_details_cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for movie details: {cache_key}")
                data = cached.get("data") if isinstance(cached, dict) and "data" in cached else cached
                return copy.deepcopy(data)
        else:
            if hasattr(self._movie_details_cache, "delete"):
                self._movie_details_cache.delete(cache_key)
            else:
                self._movie_details_cache.pop(cache_key, None)

        if self._mock_mode:
            result = {
                "rating_key": str(rating_key),
                "title": "Mock Movie",
                "year": 2024,
                "type": "movie",
                "thumb": None,
                "summary": "Mock summary",
                "has_vi": False,
                "has_en": True,
                "can_translate": True,
                "vi_details": {"has_subtitle": False, "subtitle_count": 0, "subtitle_info": []},
                "en_details": {"has_subtitle": True, "subtitle_count": 1, "subtitle_info": []},
            }
            if use_cache:
                self._movie_details_cache.set(cache_key, result)
            return result


        video = self.get_video(rating_key)
        vi_det = self.get_subtitle_details(video, target_lang)
        en_det = self.get_subtitle_details(video, source_lang)
        has_vi = vi_det.get("has_subtitle", False)
        has_en = en_det.get("has_subtitle", False)
        can_translate = False
        if not has_vi and has_en:
            infos = en_det.get("subtitle_info", [])
            can_translate = any(not info.get("is_image_based", False) for info in infos)

        result = {
            "rating_key": str(video.ratingKey),
            "title": video.title,
            "year": getattr(video, "year", None),
            "type": getattr(video, "type", "movie"),

            "thumb": getattr(video, "thumb", None),
            "summary": getattr(video, "summary", "") or "",
            "has_vi": has_vi,
            "has_en": has_en,
            "can_translate": can_translate,
            "vi_details": vi_det,
            "en_details": en_det,
        }
        if use_cache:
            self._movie_details_cache.set(cache_key, result)
        return result

    get_media_details = get_movie_details
