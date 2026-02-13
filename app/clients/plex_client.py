"""
Plex client wrapper sử dụng python-plexapi.
Handles tất cả interactions với Plex Media Server.
"""

import logging
from pathlib import Path
from typing import cast

from plexapi.server import PlexServer
from plexapi.video import Movie, Episode, Video
from plexapi.exceptions import NotFound, Unauthorized, BadRequest
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
        if not self._mock_mode:
            if self._config.plex_url and self._config.plex_token:
                self._connect()
            else:
                logger.warning("Plex credentials not configured — will connect after setup")

    def _connect(self) -> None:
        """Establish connection to Plex server với retry logic."""
        max_attempts = getattr(self._config, "max_retries", 3)
        retry_delay = getattr(self._config, "retry_delay", 2)

        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=retry_delay, max=30),
            retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        )
        def _do_connect() -> None:
            try:
                logger.info(f"Connecting to Plex server at {self._config.plex_url}")
                if not self._config.plex_url or not self._config.plex_token:
                    raise PlexClientError("Plex URL/token missing in runtime config")
                self._server = PlexServer(self._config.plex_url, self._config.plex_token)
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
            if not isinstance(item, (Movie, Episode)):
                raise PlexClientError(
                    f"Item {rating_key} is not a movie or episode (type: {type(item).__name__})"
                )

            logger.info(f"Found video: {item.title} ({item.type})")
            return cast(Video, item)

        except NotFound:
            raise PlexClientError(f"Video with ratingKey {rating_key} not found")
        except ValueError as e:
            raise PlexClientError(f"Invalid ratingKey format: {rating_key}") from e

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
        else:
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

        logger.info(f"Extracted metadata: {metadata}")
        return metadata

    @staticmethod
    def _stream_matches_language(stream, language: str) -> bool:
        """
        Check if a subtitle stream matches the given language code.

        Plex exposes multiple language fields:
        - languageTag: ISO 639-1 (2-letter, e.g. "vi", "en")
        - languageCode: ISO 639-2 (3-letter, e.g. "vie", "eng")

        The service uses ISO 639-1 codes, so prefer languageTag for matching.
        """
        tag = getattr(stream, "languageTag", None)
        code = getattr(stream, "languageCode", None)
        return tag == language or code == language

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
                                languages.add(lang)

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
            for media in video.media:
                for part in media.parts:
                    for stream in part.streams:
                        if stream.streamType == 3 and self._stream_matches_language(stream, language):
                            subtitle_info.append({
                                "codec": getattr(stream, "codec", "unknown"),
                                "forced": getattr(stream, "forced", False),
                                "title": getattr(stream, "title", ""),
                                "format": getattr(stream, "format", ""),
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
        has_sub = language in existing_langs

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

            # Rename file để include language code (Plex convention)
            temp_path = subtitle_path.parent / f"{subtitle_path.stem}.{language}.srt"
            subtitle_path.rename(temp_path)

            video.uploadSubtitles(str(temp_path))

            logger.info(f"✓ Successfully uploaded subtitle for '{video.title}'")

            # Trigger refresh để Plex scan subtitle mới
            self._refresh_metadata(video)

            return True

        except BadRequest as e:
            raise PlexClientError(f"Plex rejected subtitle upload: {e}") from e
        except Exception as e:
            logger.error(f"Failed to upload subtitle: {e}")
            raise PlexClientError(f"Upload failed: {e}") from e

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
