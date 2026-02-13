"""
Subtitle service - orchestrates subtitle search và upload workflow.
"""

import asyncio
import logging
from pathlib import Path
from typing import cast
from datetime import datetime

from plexapi.video import Video

from app.clients.plex_client import PlexClient, PlexClientError
from app.clients.subsource_client import SubsourceClient, SubsourceClientError
from app.clients.telegram_client import TelegramClient
from app.clients.cache_client import CacheClient
from app.clients.openai_translation_client import OpenAITranslationClient, TranslationClientError
from app.models.runtime_config import RuntimeConfig
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

    def __init__(self, runtime_config: RuntimeConfig, service_config: ServiceConfig | None = None) -> None:
        """Initialize service with clients and runtime config."""
        self.runtime_config = runtime_config

        from app.config import settings as infra_settings
        self.plex_client = PlexClient(runtime_config, mock_mode=infra_settings.mock_mode)
        self.subsource_client = SubsourceClient(runtime_config)
        self.telegram_client = TelegramClient(runtime_config)
        self.cache_client = CacheClient(runtime_config)
        self.translation_client = OpenAITranslationClient(runtime_config)

        self.temp_dir = Path(runtime_config.temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Runtime configuration/state
        self.config = service_config or ServiceConfig(subtitle_settings=runtime_config.subtitle_settings)

        # Pending translation queue (for approval mode)
        self._pending_translations: dict[str, dict] = {}
        self._translation_stats = {
            "total_translations": 0,
            "total_lines": 0,
            "total_cost": 0.0,
        }

    async def close(self) -> None:
        """Cleanup resources."""
        await self.subsource_client.close()
        await self.telegram_client.close()
        await self.cache_client.close()
        await self.translation_client.close()

    def update_settings(self, new_settings: SubtitleSettings) -> None:
        """Update subtitle settings từ Web UI."""
        self.config.subtitle_settings = new_settings
        self.runtime_config.subtitle_settings = new_settings
        logger.info("Subtitle settings updated", extra={"settings": new_settings.model_dump()})

    def get_config(self) -> ServiceConfig:
        """Get current configuration."""
        return self.config

    def update_runtime_config(self, new_runtime: RuntimeConfig) -> None:
        """Hot-reload runtime config and refresh clients."""
        self.runtime_config = new_runtime
        self.config.subtitle_settings = new_runtime.subtitle_settings

        # Re-init clients with new credentials
        from app.config import settings as infra_settings
        self.plex_client = PlexClient(new_runtime, mock_mode=infra_settings.mock_mode)
        self.subsource_client = SubsourceClient(new_runtime)
        self.telegram_client = TelegramClient(new_runtime)
        self.cache_client = CacheClient(new_runtime)
        self.translation_client = OpenAITranslationClient(new_runtime)

        # Ensure temp dir exists
        self.temp_dir = Path(new_runtime.temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Runtime config hot-reloaded")

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
        log.info("▶ Processing webhook", rating_key=rating_key, event=event)

        # Check settings xem có nên process event này không
        if not self.config.subtitle_settings.should_download_on_event(event):
            log.info(f"⏭ Event {event} disabled in settings - skipping")
            return {
                "status": "skipped",
                "message": f"Auto-download disabled for event: {event}",
            }

        title_label = "Unknown"

        try:
            # Step 1: Fetch video từ Plex
            log.info("[Step 1/7] Fetching video from Plex", rating_key=rating_key)
            video = await asyncio.to_thread(
                self.plex_client.get_video,
                rating_key,
            )
            title_label = video.title
            log.info(f"[Step 1/7] ✓ Fetched: {video.title}", type=video.type)

            # Step 2: Extract metadata
            log.info("[Step 2/7] Extracting metadata")
            metadata = await asyncio.to_thread(
                self.plex_client.extract_metadata,
                video,
            )
            title_label = str(metadata)
            log.info(f"[Step 2/7] ✓ Metadata: {metadata}")

            # Step 3: Check existing subtitles với improved logic
            log.info("[Step 3/7] Checking existing subtitles")
            should_download, reason = await self._should_download_subtitle(video, metadata, log)
            if not should_download:
                log.info(f"[Step 3/7] ⏭ Skipping: {reason}", title=metadata.title)
                self.config.increment_skipped()
                return {
                    "status": "skipped",
                    "message": reason,
                }
            log.info(f"[Step 3/7] ✓ Download needed: {reason}")

            # Notify: new media detected
            await self.telegram_client.notify_processing_started(
                title=str(metadata),
                language=self.runtime_config.default_language,
            )

            # Step 4: Search subtitle
            log.info(f"[Step 4/7] Searching {self.runtime_config.default_language} subtitle")
            subtitles = await self._find_subtitles(metadata, log)
            if not subtitles:
                log.warning(f"[Step 4/7] ✗ No {self.runtime_config.default_language} subtitle found for: {metadata.title}")

                # Try translation fallback if enabled
                if self.runtime_config.translation_enabled:
                    log.info("[Step 4/7] Attempting translation fallback (en → vi)")
                    translation_result = await self._try_translation_fallback(
                        metadata,
                        video,
                        log,
                    )
                    if translation_result:
                        return translation_result

                # Send Telegram notification
                await self.telegram_client.notify_subtitle_not_found(
                    title=str(metadata),
                    language=self.runtime_config.default_language,
                )

                log.warning(f"▶ Workflow finished: no subtitle found for {metadata.title}")
                return {
                    "status": "not_found",
                    "message": "No subtitle found",
                }

            # Step 5: Quality threshold check on best match
            subtitle = subtitles[0]
            log.info(f"[Step 4/7] ✓ Found {len(subtitles)} subtitle(s). Best: {subtitle.name}", score=subtitle.priority_score)

            # Notify: subtitle found
            await self.telegram_client.notify_subtitle_found(
                title=str(metadata),
                subtitle_name=subtitle.name,
                language=self.runtime_config.default_language,
                quality=subtitle.quality_type,
                total_results=len(subtitles),
            )

            log.info(f"[Step 5/7] Checking quality threshold")
            if not self._meets_quality_threshold(subtitle):
                log.info(
                    f"[Step 5/7] ✗ Quality below threshold",
                    quality=subtitle.quality_type,
                    threshold=self.config.subtitle_settings.min_quality_threshold,
                )
                return {
                    "status": "quality_too_low",
                    "message": f"Subtitle quality ({subtitle.quality_type}) below threshold",
                }
            log.info(f"[Step 5/7] ✓ Quality OK: {subtitle.quality_type}")

            # Step 6: Download subtitle (try each candidate on failure)
            subtitle_path = None
            for i, candidate in enumerate(subtitles):
                if not self._meets_quality_threshold(candidate):
                    continue
                try:
                    log.info(f"[Step 6/7] Downloading subtitle ({i+1}/{len(subtitles)}): {candidate.name}")
                    subtitle_path = await self._download_subtitle(candidate, metadata, log)
                    subtitle = candidate
                    log.info(f"[Step 6/7] ✓ Downloaded to: {subtitle_path}")
                    break
                except Exception as e:
                    log.warning(f"[Step 6/7] Download failed for '{candidate.name}': {e}")
                    if i < len(subtitles) - 1:
                        log.info(f"[Step 6/7] Trying next subtitle...")
                    continue

            if not subtitle_path:
                log.error("[Step 6/7] ✗ All subtitle downloads failed")
                return {
                    "status": "download_failed",
                    "message": "All subtitle download attempts failed",
                }

            # Step 7: Upload to Plex
            log.info(f"[Step 7/7] Uploading subtitle to Plex")
            await self._upload_to_plex(video, subtitle_path, log)
            log.info(f"[Step 7/7] ✓ Uploaded successfully")

            # Update stats
            self.config.increment_downloads()
            self.config.last_download = datetime.now().isoformat()

            # Send Telegram notification
            await self.telegram_client.notify_subtitle_downloaded(
                title=str(metadata),
                subtitle_name=subtitle.name,
                language=self.runtime_config.default_language,
                quality=subtitle.quality_type,
            )

            log.info(f"▶ Workflow completed successfully for: {metadata.title}")
            return {
                "status": "success",
                "message": f"Uploaded subtitle: {subtitle.name}",
            }

        except PlexClientError as e:
            log.error(f"✗ Plex error while processing '{title_label}': {e}")
            await self.telegram_client.notify_error(
                title=title_label,
                error_message=str(e),
            )
            raise SubtitleServiceError(f"Plex error: {e}") from e
        except SubsourceClientError as e:
            log.error(f"✗ Subsource error while processing '{title_label}': {e}")
            await self.telegram_client.notify_error(
                title=title_label,
                error_message=str(e),
            )
            raise SubtitleServiceError(f"Subsource error: {e}") from e
        except SubtitleServiceError:
            raise
        except Exception as e:
            log.error(f"✗ Unexpected error while processing '{title_label}': {e}")
            await self.telegram_client.notify_error(
                title=title_label,
                error_message=str(e),
            )
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
            self.runtime_config.default_language,
        )

        runtime_settings = self.config.subtitle_settings

        # Check 1: Đã có subtitle và setting là skip
        if sub_details["has_subtitle"] and runtime_settings.skip_if_has_subtitle:
            if not runtime_settings.replace_existing:
                return False, f"Already has {sub_details['subtitle_count']} subtitle(s) and skip_if_has_subtitle=True"

        # Check 2: Có forced subtitle và setting là skip forced
        if runtime_settings.skip_forced_subtitles:
            for sub_info in sub_details["subtitle_info"]:
                if sub_info.get("forced"):
                    return False, "Has forced subtitle and skip_forced_subtitles=True"

        # Check 3: Có embedded subtitle
        if runtime_settings.skip_if_embedded:
            for sub_info in sub_details["subtitle_info"]:
                if sub_info.get("format") in ["pgs", "vobsub", "dvdsub"]:
                    return False, "Has embedded subtitle and skip_if_embedded=True"

        # Check 4: Replace mode - chỉ download nếu có subtitle mới tốt hơn
        if sub_details["has_subtitle"] and runtime_settings.replace_existing:
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

    async def _find_subtitles(
        self,
        metadata: MediaMetadata,
        log: RequestContextLogger,
        language: str | None = None,
    ) -> list[SubtitleResult]:
        """
        Search subtitles với cache support, trả về danh sách đã sorted.

        Args:
            metadata: MediaMetadata
            log: Logger instance
            language: Override language (default: runtime default_language)

        Returns:
            List of SubtitleResult sorted by priority (best first)
        """
        lang = language or self.runtime_config.default_language

        search_params = SubtitleSearchParams(
            language=lang,
            title=metadata.search_title,
            year=metadata.year,
            imdb_id=metadata.imdb_id,
            tmdb_id=metadata.tmdb_id,
            season=metadata.season_number,
            episode=metadata.episode_number,
        )

        log.info(f"Searching subtitles: lang={search_params.language}, title={search_params.title}, imdb={search_params.imdb_id}")

        # Try cache first
        cached_results = await self.cache_client.get_search_results(search_params)
        if cached_results:
            log.info(f"Cache hit: {len(cached_results)} subtitle(s)")
            return cached_results

        log.info("Cache miss — querying Subsource API")
        # Search via API — errors treated as "not found" so fallback can kick in
        try:
            results = await self.subsource_client.search_subtitles(search_params)
            log.info(f"Subsource API returned {len(results)} result(s)")
        except SubsourceClientError as e:
            log.error(f"Subsource API error: {e}")
            results = []

        # Cache results
        if results:
            await self.cache_client.set_search_results(search_params, results)

        if not results:
            log.warning(f"No subtitle found for lang={lang}")

        return results

    async def _find_best_subtitle(
        self,
        metadata: MediaMetadata,
        log: RequestContextLogger,
    ) -> SubtitleResult | None:
        """Convenience wrapper: trả về best match hoặc None."""
        results = await self._find_subtitles(metadata, log)
        if not results:
            return None

        best = results[0]
        log.info(
            f"Best match: {best.name}",
            quality=best.quality_type,
            score=best.priority_score,
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
            self.runtime_config.default_language,
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

    async def _try_translation_fallback(
        self,
        metadata: MediaMetadata,
        video: Video,
        log: RequestContextLogger,
    ) -> dict[str, str] | None:
        """
        Fallback: Search English subtitle và translate sang Vietnamese.

        Args:
            metadata: MediaMetadata
            video: Plex Video object
            log: Logger instance

        Returns:
            Dict với status nếu thành công, None nếu fail
        """
        if not self.runtime_config.translation_enabled:
            return None

        log.info("Translation fallback: Searching English subtitle")

        # Search English subtitle
        en_search_params = SubtitleSearchParams(
            language="en",
            title=metadata.search_title,
            year=metadata.year,
            imdb_id=metadata.imdb_id,
            tmdb_id=metadata.tmdb_id,
            season=metadata.season_number,
            episode=metadata.episode_number,
        )

        # Strategy 1: Search EN subtitle on Subsource
        en_results = await self._search_subtitles_by_params(en_search_params, log)
        plex_subtitle_path: Path | None = None

        if en_results:
            log.info(f"Found {len(en_results)} English subtitle(s) on Subsource")
        else:
            # Strategy 2: Download existing EN subtitle from Plex
            log.info("No EN subtitle on Subsource — checking Plex for existing EN subtitle")
            dest_dir = self.temp_dir / metadata.rating_key
            plex_subtitle_path = await asyncio.to_thread(
                self.plex_client.download_existing_subtitle,
                video,
                "en",
                dest_dir,
            )
            if plex_subtitle_path:
                log.info(f"Found existing EN subtitle on Plex: {plex_subtitle_path}")
            else:
                log.warning("No English subtitle found (Subsource + Plex)")
                return None

        subtitle_source = en_results[0].name if en_results else "Plex existing subtitle"

        # Check if requires approval
        if self.runtime_config.translation_requires_approval:
            # Add to pending queue
            self.add_pending_translation(
                rating_key=metadata.rating_key,
                metadata=metadata,
                from_lang="en",
                to_lang=self.runtime_config.default_language,
            )

            await self.telegram_client.send_message(
                f"🔔 *Translation Approval Required*\n\n"
                f"📺 *Title:* {metadata}\n"
                f"🌐 *Translation:* en → vi\n"
                f"📄 *Source:* {subtitle_source}\n\n"
                f"Open Web UI to approve/reject.",
                parse_mode="Markdown",
            )

            log.warning("Translation requires approval — added to pending queue")

            return {
                "status": "pending_approval",
                "message": "Translation request added to queue. Check Web UI to approve.",
            }

        # Auto mode - execute immediately
        log.info("Auto-translation enabled, executing...")

        return await self._execute_translation(
            metadata=metadata,
            video=video,
            from_lang="en",
            to_lang="vi",
            log=log,
            source_subtitle_path=plex_subtitle_path,
        )

    async def _search_subtitles_by_params(
        self,
        params: SubtitleSearchParams,
        log: RequestContextLogger,
    ) -> list[SubtitleResult]:
        """
        Helper to search subtitles với custom params.

        Returns:
            List of SubtitleResult sorted by priority
        """
        # Try cache first
        cached_results = await self.cache_client.get_search_results(params)
        if cached_results:
            return cached_results

        try:
            results = await self.subsource_client.search_subtitles(params)
        except SubsourceClientError as e:
            log.warning(f"Subsource search failed: {e} — treating as no results")
            results = []

        if results:
            await self.cache_client.set_search_results(params, results)

        return results

    async def _download_first_available(
        self,
        subtitles: list[SubtitleResult],
        metadata: MediaMetadata,
        log: RequestContextLogger,
    ) -> tuple[SubtitleResult, Path] | None:
        """
        Thử download lần lượt từng subtitle cho đến khi thành công.

        Returns:
            Tuple (subtitle, path) hoặc None nếu tất cả fail
        """
        for i, candidate in enumerate(subtitles):
            try:
                log.info(f"Downloading subtitle ({i+1}/{len(subtitles)}): {candidate.name}")
                path = await self._download_subtitle(candidate, metadata, log)
                return candidate, path
            except Exception as e:
                log.warning(f"Download failed for '{candidate.name}': {e}")
                if i < len(subtitles) - 1:
                    log.info("Trying next subtitle...")
                continue
        return None

    def add_pending_translation(
        self,
        rating_key: str,
        metadata: MediaMetadata,
        from_lang: str = "en",
        to_lang: str = "vi",
    ) -> None:
        """
        Add translation request vào pending queue.

        User sẽ approve/reject qua Web UI.
        """
        self._pending_translations[rating_key] = {
            "rating_key": rating_key,
            "title": str(metadata),
            "from_lang": from_lang,
            "to_lang": to_lang,
            "added_at": datetime.now().isoformat(),
            "metadata": metadata.model_dump(),
        }

        logger.info(f"Added pending translation: {metadata} ({from_lang} → {to_lang})")

    def get_pending_translations(self) -> list[dict]:
        """Get list of pending translations."""
        return list(self._pending_translations.values())

    def remove_pending_translation(self, rating_key: str) -> None:
        """Remove translation từ pending queue."""
        if rating_key in self._pending_translations:
            del self._pending_translations[rating_key]
            logger.info(f"Removed pending translation: {rating_key}")

    def get_translation_stats(self) -> dict:
        """Get translation statistics."""
        return {
            **self._translation_stats,
            "pending_count": len(self._pending_translations),
            "average_cost": (
                self._translation_stats["total_cost"] / self._translation_stats["total_translations"]
                if self._translation_stats["total_translations"] > 0
                else 0
            ),
        }

    def _get_logger(self, request_id: str) -> RequestContextLogger:
        """Create logger với request ID."""
        return RequestContextLogger(logger, request_id)

    async def _execute_translation(
        self,
        metadata: MediaMetadata,
        video: Video,
        from_lang: str,
        to_lang: str,
        log: RequestContextLogger,
        source_subtitle_path: Path | None = None,
    ) -> dict[str, str] | None:
        """
        Execute translation (called after approval).

        Args:
            metadata: MediaMetadata
            video: Plex Video object
            from_lang: Source language
            to_lang: Target language
            log: Logger instance
            source_subtitle_path: Pre-downloaded subtitle path (e.g. from Plex).
                                  If None, will search and download from Subsource.

        Returns:
            Dict với status nếu thành công
        """
        if source_subtitle_path is None:
            # Search and download from Subsource
            search_params = SubtitleSearchParams(
                language=from_lang,
                title=metadata.search_title,
                year=metadata.year,
                imdb_id=metadata.imdb_id,
                tmdb_id=metadata.tmdb_id,
                season=metadata.season_number,
                episode=metadata.episode_number,
            )

            results = await self._search_subtitles_by_params(search_params, log)
            if not results:
                log.warning(f"No {from_lang} subtitle found for translation")
                return None

            downloaded = await self._download_first_available(results, metadata, log)
            if not downloaded:
                log.warning(f"All {from_lang} subtitle downloads failed")
                return None

            source_subtitle, source_subtitle_path = downloaded
            log.info(f"Using {from_lang} subtitle: {source_subtitle.name}")
        else:
            log.info(f"Using pre-downloaded subtitle: {source_subtitle_path}")

        # Notify translation started
        await self.telegram_client.notify_translation_started(
            title=str(metadata),
            from_lang=from_lang,
            to_lang=to_lang,
        )

        # Translate
        try:
            log.info(f"Translating {from_lang} subtitle to {to_lang}...")

            target_subtitle_path = source_subtitle_path.parent / f"{source_subtitle_path.stem}.{to_lang}.srt"

            stats = await self.translation_client.translate_srt_file(
                srt_path=source_subtitle_path,
                output_path=target_subtitle_path,
                from_lang=from_lang,
                to_lang=to_lang,
            )

            log.info(f"✓ Translation completed: {stats['lines_translated']} lines")

            # Upload translated subtitle
            await self._upload_to_plex(video, target_subtitle_path, log)

            # Notify success
            await self.telegram_client.notify_translation_completed(
                title=str(metadata),
                to_lang=to_lang,
                lines_translated=stats["lines_translated"],
            )

            # Update stats
            self.config.increment_downloads()
            self._translation_stats["total_translations"] += 1
            self._translation_stats["total_lines"] += stats["lines_translated"]
            # Note: Actual cost would need to be calculated from API response

            # Remove from pending queue if exists
            self.remove_pending_translation(metadata.rating_key)

            return {
                "status": "success",
                "message": f"Translated subtitle uploaded ({stats['lines_translated']} lines)",
                "stats": stats,
            }

        except TranslationClientError as e:
            log.error(f"Translation failed: {e}")
            await self.telegram_client.notify_error(
                title=str(metadata),
                error_message=f"Translation failed: {e}",
            )
            return None
