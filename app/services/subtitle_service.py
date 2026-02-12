"""
Subtitle service - orchestrates subtitle search và upload workflow.
"""

import asyncio
import logging
from pathlib import Path
from typing import cast
from datetime import datetime

from plexapi.video import Video

from app.config import settings
from app.clients.plex_client import PlexClient, PlexClientError
from app.clients.subsource_client import SubsourceClient, SubsourceClientError
from app.models.webhook import MediaMetadata
from app.models.subtitle import SubtitleSearchParams, SubtitleResult
from app.models.settings import ServiceConfig, SubtitleSettings
from app.utils.logger import get_logger, RequestContextLogger

logger = get_logger(__name__)


class SubtitleServiceError(Exception):
    """Base exception for subtitle service errors."""
    pass


class SubtitleService:
    """
    Core service để xử lý subtitle workflow.

    Workflow:
    1. Receive webhook → extract ratingKey
    2. Fetch video metadata từ Plex
    3. Check nếu đã có subtitle → skip (based on settings)
    4. Search subtitle trên Subsource
    5. Download subtitle
    6. Upload subtitle lên Plex
    """

    def __init__(self, service_config: ServiceConfig | None = None) -> None:
        """Initialize service with clients."""
        self.plex_client = PlexClient()
        self.subsource_client = SubsourceClient()
        self.temp_dir = Path(settings.temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Runtime configuration
        self.config = service_config or ServiceConfig()

    async def close(self) -> None:
        """Cleanup resources."""
        await self.subsource_client.close()

    def update_settings(self, new_settings: SubtitleSettings) -> None:
        """Update subtitle settings từ Web UI."""
        self.config.subtitle_settings = new_settings
        logger.info("Subtitle settings updated", extra={"settings": new_settings.model_dump()})

    def get_config(self) -> ServiceConfig:
        """Get current configuration."""
        return self.config

    async def process_webhook(
        self,
        rating_key: str,
        event: str = "library.new",
        request_id: str | None = None,
    ) -> dict[str, str]:
        """
        Process webhook event và handle subtitle workflow.

        Args:
            rating_key: Plex ratingKey
            event: Webhook event type
            request_id: Request ID cho logging

        Returns:
            Dict với status và message

        Raises:
            SubtitleServiceError: Nếu workflow fail
        """
        log = RequestContextLogger(logger, request_id)
        log.info("Processing webhook", rating_key=rating_key, event=event)

        # Check settings xem có nên process event này không
        if not self.config.subtitle_settings.should_download_on_event(event):
            log.info(f"Event {event} disabled in settings - skipping")
            return {
                "status": "skipped",
                "message": f"Auto-download disabled for event: {event}",
            }

        try:
            # Step 1: Fetch video từ Plex
            video = await asyncio.to_thread(
                self.plex_client.get_video,
                rating_key,
            )
            log.info(f"Fetched video: {video.title}", type=video.type)

            # Step 2: Extract metadata
            metadata = await asyncio.to_thread(
                self.plex_client.extract_metadata,
                video,
            )
            log.info(f"Extracted metadata: {metadata}")

            # Step 3: Check existing subtitles với improved logic
            should_download, reason = await self._should_download_subtitle(video, metadata, log)
            if not should_download:
                log.info(f"Skipping download: {reason}", title=metadata.title)
                self.config.increment_skipped()
                return {
                    "status": "skipped",
                    "message": reason,
                }

            # Step 4: Search subtitle
            subtitle = await self._find_best_subtitle(metadata, log)
            if not subtitle:
                log.warning("No suitable subtitle found", title=metadata.title)
                return {
                    "status": "not_found",
                    "message": "No subtitle found",
                }

            # Step 5: Quality threshold check
            if not self._meets_quality_threshold(subtitle):
                log.info(
                    f"Subtitle quality below threshold",
                    quality=subtitle.quality_type,
                    threshold=self.config.subtitle_settings.min_quality_threshold,
                )
                return {
                    "status": "quality_too_low",
                    "message": f"Subtitle quality ({subtitle.quality_type}) below threshold",
                }

            log.info(f"Selected subtitle: {subtitle.name}", score=subtitle.priority_score)

            # Step 6: Download subtitle
            subtitle_path = await self._download_subtitle(subtitle, metadata, log)

            # Step 7: Upload to Plex
            await self._upload_to_plex(video, subtitle_path, log)

            # Update stats
            self.config.increment_downloads()
            self.config.last_download = datetime.now().isoformat()

            log.info("✓ Subtitle workflow completed successfully")
            return {
                "status": "success",
                "message": f"Uploaded subtitle: {subtitle.name}",
            }

        except PlexClientError as e:
            log.error(f"Plex error: {e}")
            raise SubtitleServiceError(f"Plex error: {e}") from e
        except SubsourceClientError as e:
            log.error(f"Subsource error: {e}")
            raise SubtitleServiceError(f"Subsource error: {e}") from e
        except Exception as e:
            log.error(f"Unexpected error: {e}")
            raise SubtitleServiceError(f"Workflow failed: {e}") from e
        finally:
            # Cleanup temp files
            self._cleanup_temp_files(rating_key)

    async def _should_download_subtitle(
        self,
        video: Video,
        metadata: MediaMetadata,
        log: RequestContextLogger,
    ) -> tuple[bool, str]:
        """
        Quyết định có nên download subtitle không dựa trên settings và existing subs.

        Returns:
            (should_download: bool, reason: str)
        """
        settings = self.config.subtitle_settings

        # Get subtitle details
        sub_details = await asyncio.to_thread(
            self.plex_client.get_subtitle_details,
            video,
            settings.default_language,
        )

        # Check 1: Đã có subtitle và setting là skip
        if sub_details["has_subtitle"] and settings.skip_if_has_subtitle:
            if not settings.replace_existing:
                return False, f"Already has {sub_details['subtitle_count']} subtitle(s) and skip_if_has_subtitle=True"

        # Check 2: Có forced subtitle và setting là skip forced
        if settings.skip_forced_subtitles:
            for sub_info in sub_details["subtitle_info"]:
                if sub_info.get("forced"):
                    return False, "Has forced subtitle and skip_forced_subtitles=True"

        # Check 3: Có embedded subtitle
        if settings.skip_if_embedded:
            for sub_info in sub_details["subtitle_info"]:
                if sub_info.get("format") in ["pgs", "vobsub", "dvdsub"]:
                    return False, "Has embedded subtitle and skip_if_embedded=True"

        # Check 4: Replace mode - chỉ download nếu có subtitle mới tốt hơn
        if sub_details["has_subtitle"] and settings.replace_existing:
            # TODO: Implement quality comparison với existing subtitle
            # For now, cho phép replace
            log.info("Replace mode enabled - will replace existing subtitle if better quality found")

        return True, "All checks passed"

    def _meets_quality_threshold(self, subtitle: SubtitleResult) -> bool:
        """
        Check xem subtitle có đáp ứng quality threshold không.

        Args:
            subtitle: SubtitleResult to check

        Returns:
            True nếu đạt threshold
        """
        threshold = self.config.subtitle_settings.min_quality_threshold

        if threshold == "any":
            return True

        quality_ranking = {
            "retail": 3,
            "translated": 2,
            "ai": 1,
            "unknown": 0,
        }

        threshold_ranking = {
            "retail": 3,
            "translated": 2,
        }

        subtitle_rank = quality_ranking.get(subtitle.quality_type, 0)
        threshold_rank = threshold_ranking.get(threshold, 0)

        return subtitle_rank >= threshold_rank

    async def _find_best_subtitle(
        self,
        metadata: MediaMetadata,
        log: RequestContextLogger,
    ) -> SubtitleResult | None:
        """
        Search và chọn subtitle tốt nhất.

        Args:
            metadata: MediaMetadata
            log: Logger instance

        Returns:
            Best SubtitleResult hoặc None
        """
        search_params = SubtitleSearchParams(
            language=settings.default_language,
            title=metadata.search_title,
            year=metadata.year,
            imdb_id=metadata.imdb_id,
            tmdb_id=metadata.tmdb_id,
            season=metadata.season_number,
            episode=metadata.episode_number,
        )

        log.info("Searching subtitles", params=str(search_params))

        results = await self.subsource_client.search_subtitles(search_params)

        if not results:
            return None

        # Return highest priority subtitle (already sorted)
        best = results[0]
        log.info(
            f"Found {len(results)} subtitles, selected best",
            name=best.name,
            quality=best.quality_type,
        )

        return best

    async def _download_subtitle(
        self,
        subtitle: SubtitleResult,
        metadata: MediaMetadata,
        log: RequestContextLogger,
    ) -> Path:
        """
        Download subtitle vào temp directory.

        Args:
            subtitle: SubtitleResult
            metadata: MediaMetadata (for naming)
            log: Logger instance

        Returns:
            Path to downloaded .srt file
        """
        # Create subdirectory cho rating_key
        dest_dir = self.temp_dir / metadata.rating_key
        dest_dir.mkdir(parents=True, exist_ok=True)

        log.info("Downloading subtitle", url=str(subtitle.download_url))

        subtitle_path = await self.subsource_client.download_subtitle(
            subtitle,
            dest_dir,
        )

        log.info(f"✓ Downloaded to: {subtitle_path}")
        return subtitle_path

    async def _upload_to_plex(
        self,
        video: Video,
        subtitle_path: Path,
        log: RequestContextLogger,
    ) -> None:
        """
        Upload subtitle file lên Plex.

        Args:
            video: Plex Video object
            subtitle_path: Path to .srt file
            log: Logger instance
        """
        log.info("Uploading subtitle to Plex", path=str(subtitle_path))

        success = await asyncio.to_thread(
            self.plex_client.upload_subtitle,
            video,
            subtitle_path,
            settings.default_language,
        )

        if not success:
            raise SubtitleServiceError("Upload to Plex failed")

        log.info("✓ Uploaded subtitle to Plex")

    def _cleanup_temp_files(self, rating_key: str) -> None:
        """
        Clean up temporary subtitle files.

        Args:
            rating_key: Rating key (used as subdirectory name)
        """
        try:
            temp_subdir = self.temp_dir / rating_key
            if temp_subdir.exists():
                import shutil
                shutil.rmtree(temp_subdir)
                logger.debug(f"Cleaned up temp directory: {temp_subdir}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp files: {e}")
