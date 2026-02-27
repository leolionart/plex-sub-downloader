"""
Subtitle service - orchestrates subtitle search và upload workflow.
"""

import asyncio
import json
import logging
from pathlib import Path
from threading import RLock
from typing import Any, cast
from datetime import datetime

from plexapi.video import Video

from app.clients.plex_client import PlexClient, PlexClientError
from app.clients.subsource_client import SubsourceClient, SubsourceClientError, LANGUAGE_MAP

# Languages to try as source for AI translation when EN not available
# Ordered by prevalence on Subsource
_FALLBACK_SOURCE_LANGS = ["ko", "ja", "zh", "fr", "es", "de", "pt", "ru", "it", "ar"]
from app.clients.telegram_client import TelegramClient
from app.clients.cache_client import CacheClient
from app.clients.openai_translation_client import OpenAITranslationClient, TranslationClientError
from app.clients.sync_client import SubtitleSyncClient, SyncClientError
from app.models.runtime_config import RuntimeConfig
from app.services.stats_store import StatsStore
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
        self.sync_client = SubtitleSyncClient(runtime_config)

        self.temp_dir = Path(runtime_config.temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Runtime configuration/state
        self.config = service_config or ServiceConfig(subtitle_settings=runtime_config.subtitle_settings)

        # Persistent stats store (survives restarts)
        self.stats = StatsStore()

        # Pending translation queue (for approval mode)
        self._pending_translations: dict[str, dict] = {}

        # Translation history (persisted to JSON)
        self._history_path = Path("data") / "translation_history.json"
        self._history_lock = RLock()
        self._translation_history: list[dict] = self._load_history()

        # Sync history (persisted to JSON)
        self._sync_history_path = Path("data") / "sync_history.json"
        self._sync_history_lock = RLock()
        self._sync_history: list[dict] = self._load_sync_history()

    async def close(self) -> None:
        """Cleanup resources."""
        await self.subsource_client.close()
        await self.telegram_client.close()
        await self.cache_client.close()
        await self.translation_client.close()
        await self.sync_client.close()

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
        self.sync_client = SubtitleSyncClient(new_runtime)

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
                self.stats.increment("total_skipped")
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

                # Try proactive translation (if enabled) or translation fallback
                ss = self.config.subtitle_settings
                should_translate = (
                    ss.translation_enabled
                    or ss.auto_translate_if_no_vi
                )
                if should_translate:
                    mode = "proactive" if ss.auto_translate_if_no_vi else "fallback"
                    log.info(f"[Step 4/7] Attempting {mode} translation (en → vi)")
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

            # Step 7b: Sync timing (if enabled and English reference available)
            sync_result = None
            if self.config.subtitle_settings.auto_sync_timing:
                sync_result = await self._try_sync_timing(
                    video, metadata, subtitle_path, log,
                )

            # Update persistent stats
            self.stats.increment("total_downloads")

            # Send Telegram notification
            await self.telegram_client.notify_subtitle_downloaded(
                title=str(metadata),
                subtitle_name=subtitle.name,
                language=self.runtime_config.default_language,
                quality=subtitle.quality_type,
            )

            result_msg = f"Uploaded subtitle: {subtitle.name}"
            if sync_result:
                result_msg += f" (timing synced: {sync_result['anchors_found']} anchors)"

            log.info(f"▶ Workflow completed successfully for: {metadata.title}")
            return {
                "status": "success",
                "message": result_msg,
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

        # Check 3: Có embedded subtitle (image-based hoặc text-based trong container)
        # Embedded subs không thể replace bằng external .srt upload → bỏ qua.
        # Lưu ý: sub_details đã filter theo default_language ("vi"), nên chỉ skip
        # khi CHÍNH ngôn ngữ target có embedded stream — không bao giờ skip vì EN sub.
        if runtime_settings.skip_if_embedded:
            for sub_info in sub_details["subtitle_info"]:
                if sub_info.get("is_embedded"):
                    codec = sub_info.get("codec", "unknown")
                    log.info(f"[Step 3/7] Found embedded {self.runtime_config.default_language} sub (codec={codec}) — skipping")
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
        video_filename: str | None = None,
    ) -> list[SubtitleResult]:
        """
        Search subtitles với cache support, trả về danh sách đã sorted.

        Args:
            metadata: MediaMetadata
            log: Logger instance
            language: Override language (default: runtime default_language)
            video_filename: Video filename for similarity matching (optional)

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
            video_filename=video_filename,
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
        Nếu replace_existing=True, xóa subtitle cũ cùng language trước khi upload.

        Args:
            video: Plex Video object
            subtitle_path: Path to .srt file
            log: Logger instance
        """
        language = self.runtime_config.default_language

        # Remove existing external subtitles if replace mode is on
        if self.config.subtitle_settings.replace_existing:
            removed = await asyncio.to_thread(
                self.plex_client.remove_external_subtitles,
                video,
                language,
            )
            if removed:
                log.info(f"Removed {removed} existing {language} subtitle(s) before upload")

        log.info("Uploading subtitle to Plex", path=str(subtitle_path))

        success = await asyncio.to_thread(
            self.plex_client.upload_subtitle,
            video,
            subtitle_path,
            language,
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

    # ── Sync Timing Methods ──────────────────────────────────────────────

    async def _try_sync_timing(
        self,
        video: Video,
        metadata: MediaMetadata,
        subtitle_path: Path,
        log: RequestContextLogger,
    ) -> dict | None:
        """
        Thử sync timing Vietsub dựa trên Engsub reference.

        Chạy sau khi upload Vietsub lên Plex.
        Tìm Engsub trên Plex → sync timing → re-upload.

        Returns:
            Dict với sync stats hoặc None nếu không sync được
        """
        if not self.runtime_config.ai_available:
            return None

        log.info("[Sync] Checking for English reference subtitle on Plex...")

        # Download English subtitle from Plex
        dest_dir = self.temp_dir / f"{metadata.rating_key}_sync"
        dest_dir.mkdir(parents=True, exist_ok=True)

        en_path = await asyncio.to_thread(
            self.plex_client.download_existing_subtitle,
            video,
            "en",
            dest_dir,
        )

        if not en_path:
            log.info("[Sync] No English subtitle on Plex — skipping sync")
            return None

        log.info(f"[Sync] Found English reference: {en_path.name}")

        # Download the Vietnamese subtitle we just uploaded (from Plex)
        vi_path = await asyncio.to_thread(
            self.plex_client.download_existing_subtitle,
            video,
            self.runtime_config.default_language,
            dest_dir,
        )

        if not vi_path:
            # Use the subtitle file we already have
            vi_path = subtitle_path
            log.info(f"[Sync] Using local Vietsub file: {vi_path.name}")
        else:
            log.info(f"[Sync] Downloaded Vietsub from Plex: {vi_path.name}")

        # Perform sync
        try:
            output_path = dest_dir / f"synced.{self.runtime_config.default_language}.srt"

            await self.telegram_client.notify_sync_started(
                title=str(metadata),
            )

            sync_stats = await self.sync_client.sync_subtitles(
                reference_path=en_path,
                target_path=vi_path,
                output_path=output_path,
            )

            log.info(
                f"[Sync] ✓ Timing synced: {sync_stats['anchors_found']} anchors, "
                f"avg offset: {sync_stats['avg_offset_ms']}ms"
            )

            # Re-upload synced subtitle to Plex
            await self._upload_to_plex(video, output_path, log)
            log.info("[Sync] ✓ Synced subtitle re-uploaded to Plex")

            # Update persistent stats
            self.stats.increment("total_syncs")

            self.add_sync_history_entry(
                rating_key=metadata.rating_key,
                title=str(metadata),
                status="success",
                source="auto",
                anchors_found=sync_stats["anchors_found"],
                avg_offset_ms=sync_stats["avg_offset_ms"],
                ref_lang="en",
                ref_source="plex",
                model=self.runtime_config.openai_model,
            )

            await self.telegram_client.notify_sync_completed(
                title=str(metadata),
                anchors=sync_stats["anchors_found"],
                avg_offset_ms=sync_stats["avg_offset_ms"],
            )

            return sync_stats

        except SyncClientError as e:
            log.warning(f"[Sync] Sync failed: {e}")
            self.add_sync_history_entry(
                rating_key=metadata.rating_key,
                title=str(metadata),
                status="failed",
                source="auto",
                ref_lang="en",
                ref_source="plex",
                model=self.runtime_config.openai_model,
                error=str(e),
            )
            await self.telegram_client.notify_error(
                title=str(metadata),
                error_message=f"Sync timing failed: {e}",
            )
            return None
        except Exception as e:
            log.warning(f"[Sync] Unexpected sync error: {e}")
            self.add_sync_history_entry(
                rating_key=metadata.rating_key,
                title=str(metadata),
                status="failed",
                source="auto",
                ref_lang="en",
                ref_source="plex",
                model=self.runtime_config.openai_model,
                error=str(e),
            )
            await self.telegram_client.notify_error(
                title=str(metadata),
                error_message=f"Sync timing failed: {e}",
            )
            return None
        finally:
            # Cleanup sync temp files
            try:
                import shutil
                shutil.rmtree(dest_dir, ignore_errors=True)
            except Exception:
                pass

    async def preview_sync_for_media(
        self,
        rating_key: str,
    ) -> dict[str, Any]:
        """
        Preview sync: kiểm tra subtitle có sẵn trên Plex + Subsource.

        Returns:
            Dict với metadata, trạng thái chi tiết English/Vietnamese sub,
            danh sách candidates từ Subsource, và actions khả dụng.
        """
        log = RequestContextLogger(logger, rating_key[:8])

        video = await asyncio.to_thread(self.plex_client.get_video, rating_key)
        metadata = await asyncio.to_thread(self.plex_client.extract_metadata, video)
        lang = self.runtime_config.default_language

        dest_dir = self.temp_dir / f"{rating_key}_preview"
        dest_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Lấy video filename cho similarity matching (Subsource)
            video_filename = None
            try:
                if video.media and video.media[0].parts:
                    video_filename = Path(video.media[0].parts[0].file).name
            except Exception:
                pass

            # --- Target subtitle (ngôn ngữ user chọn trong settings) ---
            # Kiểm tra target lang trên Plex ngay đầu tiên (fast, không gọi Subsource).
            # Nếu đã có → không cần translate/search → bỏ qua toàn bộ AI search.
            target_details = await asyncio.to_thread(
                self.plex_client.get_subtitle_details, video, lang,
            )
            vi_path = await asyncio.to_thread(
                self.plex_client.download_existing_subtitle,
                video, lang, dest_dir,
            )
            has_vi_text = vi_path is not None

            # --- Source subtitle (bất kỳ lang nào ≠ target, dùng cho sync/translate) ---
            # Ưu tiên: Plex trước (tất cả langs có sẵn, trừ target) → Subsource.
            # Chỉ chạy khi target chưa có trên Plex (nếu có rồi, translate không cần).
            source_status: dict[str, Any] = {"available": False, "source": None}
            source_lang = "en"
            source_candidates: list[dict] = []

            # 1) Tìm source sub trên Plex: lấy tất cả langs ≠ target
            plex_langs = await asyncio.to_thread(
                self.plex_client._get_existing_subtitle_languages, video,
            )
            source_langs_on_plex = [l for l in plex_langs if l != lang]
            # Ưu tiên EN nếu có, còn lại sort theo thứ tự alphabet
            source_langs_on_plex.sort(key=lambda l: (l != "en", l))

            for plex_lang in source_langs_on_plex:
                path = await asyncio.to_thread(
                    self.plex_client.download_existing_subtitle,
                    video, plex_lang, dest_dir,
                )
                if path:
                    source_lang = plex_lang
                    lang_name = LANGUAGE_MAP.get(plex_lang, plex_lang).title()
                    source_status["available"] = True
                    source_status["source"] = "plex"
                    source_status["detail"] = f"Plex (text-based, {lang_name})"
                    log.info(f"[Preview] Source sub on Plex: {plex_lang}")
                    break
                else:
                    # Sub exists but not downloadable (image-based/embedded)
                    details = await asyncio.to_thread(
                        self.plex_client.get_subtitle_details, video, plex_lang,
                    )
                    if details["has_subtitle"] and not source_status.get("detail"):
                        subs = details["subtitle_info"]
                        codecs = [s["codec"] for s in subs]
                        image_based = [s for s in subs if s.get("is_image_based")]
                        embedded = [s for s in subs if s.get("is_embedded")]
                        lang_name = LANGUAGE_MAP.get(plex_lang, plex_lang).title()
                        if image_based:
                            source_status["detail"] = f"Plex có {lang_name} sub dạng image ({', '.join(codecs)}) — không dùng được"
                        elif embedded:
                            source_status["detail"] = f"Plex có {lang_name} sub dạng embedded — không extract được"

            has_source_available = source_status["available"]

            # --- Target subtitle (Subsource fallback, chỉ khi chưa có trên Plex) ---
            vi_details = target_details  # Already fetched above

            vi_status: dict[str, Any] = {
                "available": has_vi_text,
                "source": "plex" if has_vi_text else None,
            }
            if vi_details["has_subtitle"] and not has_vi_text:
                subs = vi_details["subtitle_info"]
                embedded = [s for s in subs if s.get("is_embedded")]
                image_based = [s for s in subs if s.get("is_image_based")]
                codecs = [s["codec"] for s in subs]

                if image_based:
                    reason = f"dạng image-based ({', '.join(codecs)})"
                elif embedded:
                    reason = f"dạng embedded ({', '.join(codecs)}) — không extract được"
                else:
                    reason = f"không download được ({', '.join(codecs)})"

                lang_name = LANGUAGE_MAP.get(lang, lang).title()
                vi_status["detail"] = (
                    f"Plex có {vi_details['subtitle_count']} {lang_name} sub nhưng {reason}"
                )

            # 2) Tìm trên Subsource — chỉ khi target chưa có trên Plex.
            # Dùng multi-lang search: movie lookup 1 lần, subtitle queries song song.
            vi_candidates: list[dict] = []
            if not has_vi_text:
                # Xây danh sách ngôn ngữ cần tìm
                source_search_order = ["en"] + [
                    l for l in _FALLBACK_SOURCE_LANGS if l != "en" and l != lang
                ]
                if has_source_available:
                    # Đã có source trên Plex → chỉ cần tìm target lang
                    langs_to_search = [lang]
                    source_search_order = []
                else:
                    # Cần tìm cả target lang + tất cả fallback source langs
                    langs_to_search = [lang] + source_search_order

                base_params = SubtitleSearchParams(
                    language=lang,
                    title=metadata.search_title,
                    year=metadata.year,
                    imdb_id=metadata.imdb_id,
                    tmdb_id=metadata.tmdb_id,
                    season=metadata.season_number,
                    episode=metadata.episode_number,
                    video_filename=video_filename,
                )

                try:
                    multi_results = await self.subsource_client.search_subtitles_multi_lang(
                        base_params, langs_to_search,
                    )
                except Exception as e:
                    log.warning(f"[Preview] Subsource multi-lang search failed: {e}")
                    multi_results = {}

                # Kết quả target lang (VI)
                target_results = multi_results.get(lang, [])
                vi_candidates = [
                    {
                        "id": r.id,
                        "name": r.name,
                        "quality": r.quality_type,
                        "downloads": r.downloads,
                        "rating": r.rating,
                        "score": r.priority_score,
                    }
                    for r in target_results
                ]

                # Kết quả source lang (nếu chưa tìm được trên Plex)
                if not has_source_available:
                    for fb_lang in source_search_order:
                        fb_results = multi_results.get(fb_lang, [])
                        if fb_results:
                            source_lang = fb_lang
                            lang_name = LANGUAGE_MAP.get(fb_lang, fb_lang).title()
                            source_candidates = [
                                {
                                    "id": r.id,
                                    "name": r.name,
                                    "quality": r.quality_type,
                                    "downloads": r.downloads,
                                    "rating": r.rating,
                                    "score": r.priority_score,
                                }
                                for r in fb_results
                            ]
                            source_status["available"] = True
                            has_source_available = True
                            source_status["source"] = "subsource"
                            source_status["detail"] = f"Tìm được {len(source_candidates)} {lang_name} sub trên Subsource"
                            log.info(f"[Preview] Source sub from Subsource: {fb_lang} ({len(source_candidates)})")
                            break

                    if not source_status["available"]:
                        source_status["detail"] = "Không tìm thấy sub nguồn trên Plex hoặc Subsource"

            has_target_available = has_vi_text or len(vi_candidates) > 0

            can_sync = has_source_available and has_target_available
            can_translate = has_source_available and self.translation_client.enabled

            return {
                "rating_key": rating_key,
                "title": str(metadata),
                "media_type": metadata.media_type,
                "source_status": source_status,
                "vi_status": vi_status,
                "has_source_on_plex": source_status["source"] == "plex",
                "has_vi_on_plex": has_vi_text,
                "source_candidates": source_candidates,
                "vi_candidates": vi_candidates,
                "can_sync": can_sync,
                "can_translate": can_translate,
                "source_lang": source_lang,
                "sync_enabled": self.runtime_config.ai_available,
            }

        finally:
            import shutil
            shutil.rmtree(dest_dir, ignore_errors=True)

    async def execute_sync_for_media(
        self,
        rating_key: str,
        subtitle_id: str | None = None,
        request_id: str | None = None,
        source_lang: str = "en",
    ) -> dict[str, Any]:
        """
        Execute sync timing cho một media item cụ thể (từ Web UI / API).

        Dùng bất kỳ ngôn ngữ nào làm timing reference (source_lang).
        Tìm reference sub theo thứ tự: Plex → Subsource (source_lang) → fallback langs.

        Args:
            rating_key: Plex ratingKey
            subtitle_id: Subsource subtitle ID cụ thể cho VI sub (tuỳ chọn)
            request_id: Request ID for logging
            source_lang: Ngôn ngữ dùng làm timing reference (default: "en")

        Returns:
            Dict với status và sync stats
        """
        log = RequestContextLogger(logger, request_id or rating_key[:8])

        if not self.runtime_config.ai_available:
            return {"status": "error", "message": "OpenAI API key required for sync timing"}

        log.info(f"[Sync] Manual sync requested for ratingKey: {rating_key} (source_lang={source_lang})")

        # Get video from Plex
        video = await asyncio.to_thread(self.plex_client.get_video, rating_key)
        metadata = await asyncio.to_thread(self.plex_client.extract_metadata, video)
        log.info(f"[Sync] Media: {metadata}")

        dest_dir = self.temp_dir / f"{rating_key}_sync"
        dest_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Download reference subtitle: try source_lang on Plex first, then Subsource
            # If source_lang fails, try all fallback languages
            ref_path: Path | None = None
            ref_source = "plex"
            ref_lang_used = source_lang

            # 1) Try source_lang on Plex
            ref_path = await asyncio.to_thread(
                self.plex_client.download_existing_subtitle,
                video, source_lang, dest_dir,
            )

            # 2) Try source_lang on Subsource
            if not ref_path:
                log.info(f"[Sync] No text-based {source_lang.upper()} sub on Plex — searching Subsource...")
                ref_results = await self._find_subtitles(metadata, log, language=source_lang)
                if ref_results:
                    downloaded = await self._download_first_available(ref_results, metadata, log)
                    if downloaded:
                        _, ref_path = downloaded
                        ref_source = "subsource"
                        log.info(f"[Sync] Downloaded {source_lang.upper()} sub from Subsource: {ref_path.name}")

            # 3) Fallback: try other languages (EN first if source_lang wasn't EN, then rest)
            if not ref_path:
                fallback_order = (["en"] if source_lang != "en" else []) + [
                    l for l in _FALLBACK_SOURCE_LANGS if l != source_lang and l != "en"
                ]
                for fb_lang in fallback_order:
                    fb_results = await self._find_subtitles(metadata, log, language=fb_lang)
                    if fb_results:
                        downloaded = await self._download_first_available(fb_results, metadata, log)
                        if downloaded:
                            _, ref_path = downloaded
                            ref_source = "subsource"
                            ref_lang_used = fb_lang
                            log.info(f"[Sync] Using fallback {fb_lang.upper()} sub from Subsource")
                            break

            if not ref_path:
                return {
                    "status": "error",
                    "message": "Không tìm được subtitle nào làm timing reference trên Plex hoặc Subsource",
                }

            en_path = ref_path  # Alias for legacy variable used below

            # Download Vietnamese subtitle: Plex first, Subsource fallback
            lang = self.runtime_config.default_language
            vi_path = await asyncio.to_thread(
                self.plex_client.download_existing_subtitle,
                video, lang, dest_dir,
            )
            vi_source = "plex"

            if not vi_path:
                log.info("[Sync] No Vietsub on Plex — searching Subsource...")
                vi_path = await self._get_vietsub_from_subsource(
                    metadata, log, dest_dir, subtitle_id,
                )
                vi_source = "subsource"

            if not vi_path:
                return {
                    "status": "error",
                    "message": f"No {lang} subtitle found on Plex or Subsource",
                }

            log.info(f"[Sync] Using Vietsub from {vi_source}: {vi_path.name}")

            # Perform sync
            output_path = dest_dir / f"synced.{lang}.srt"
            sync_stats = await self.sync_client.sync_subtitles(
                reference_path=en_path,
                target_path=vi_path,
                output_path=output_path,
            )

            # Upload synced subtitle
            await self._upload_to_plex(video, output_path, log)

            # Update persistent stats
            self.stats.increment("total_syncs")

            self.add_sync_history_entry(
                rating_key=rating_key,
                title=str(metadata),
                status="success",
                source="manual",
                anchors_found=sync_stats["anchors_found"],
                avg_offset_ms=sync_stats["avg_offset_ms"],
                ref_lang=ref_lang_used,
                ref_source=ref_source,
                model=self.runtime_config.openai_model,
            )

            await self.telegram_client.notify_sync_completed(
                title=str(metadata),
                anchors=sync_stats["anchors_found"],
                avg_offset_ms=sync_stats["avg_offset_ms"],
            )

            return {
                "status": "success",
                "message": f"Timing synced ({sync_stats['anchors_found']} anchors, ref: {ref_lang_used.upper()} from {ref_source}, vi: {vi_source})",
                "stats": sync_stats,
            }

        except SyncClientError as e:
            self.add_sync_history_entry(
                rating_key=rating_key,
                title=str(metadata),
                status="failed",
                source="manual",
                model=self.runtime_config.openai_model,
                error=str(e),
            )
            return {"status": "error", "message": str(e)}
        except PlexClientError as e:
            return {"status": "error", "message": f"Plex error: {e}"}
        finally:
            import shutil
            shutil.rmtree(dest_dir, ignore_errors=True)

    async def _get_vietsub_from_subsource(
        self,
        metadata: MediaMetadata,
        log: RequestContextLogger,
        dest_dir: Path,
        subtitle_id: str | None = None,
    ) -> Path | None:
        """
        Tìm và download Vietnamese subtitle từ Subsource.

        Args:
            metadata: MediaMetadata
            log: Logger
            dest_dir: Thư mục lưu tạm
            subtitle_id: Subsource subtitle ID cụ thể (nếu user chọn từ UI)

        Returns:
            Path đến file .srt hoặc None
        """
        lang = self.runtime_config.default_language
        results = await self._find_subtitles(metadata, log, language=lang)

        if not results:
            log.warning("[Sync] No Vietnamese subtitle found on Subsource")
            return None

        # Nếu user chỉ định subtitle_id, tìm subtitle đó
        if subtitle_id:
            target = next((r for r in results if r.id == subtitle_id), None)
            if target:
                results = [target]
            else:
                log.warning(f"[Sync] Subtitle ID {subtitle_id} not found, using best match")

        downloaded = await self._download_first_available(results, metadata, log)
        if not downloaded:
            log.warning("[Sync] All Subsource downloads failed")
            return None

        subtitle, path = downloaded
        log.info(f"[Sync] Downloaded Vietsub from Subsource: {subtitle.name}")
        return path

    async def execute_translate_for_media(
        self,
        rating_key: str,
        request_id: str | None = None,
        from_lang: str = "en",
    ) -> dict[str, Any]:
        """
        Chủ động dịch subtitle sang Vietnamese cho media item.

        Hỗ trợ bất kỳ ngôn ngữ nguồn nào (EN, KO, JA, ZH, v.v.) vì AI translate
        không bị giới hạn ngôn ngữ.

        Args:
            rating_key: Plex ratingKey
            request_id: Request ID for logging
            from_lang: Source language code (default: "en")

        Returns:
            Dict với status và translation result
        """
        log = RequestContextLogger(logger, request_id or rating_key[:8])

        if not self.translation_client.enabled:
            return {"status": "error", "message": "Translation disabled — no OpenAI API key"}

        log.info(f"[Translate] Manual translation requested for ratingKey: {rating_key} (from_lang={from_lang})")

        video = await asyncio.to_thread(self.plex_client.get_video, rating_key)
        metadata = await asyncio.to_thread(self.plex_client.extract_metadata, video)

        result = await self._execute_translation(
            metadata=metadata,
            video=video,
            from_lang=from_lang,
            to_lang=self.runtime_config.default_language,
            log=log,
        )

        if not result:
            return {"status": "error", "message": "Translation failed"}

        return result

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
        ss = self.config.subtitle_settings
        can_translate = ss.translation_enabled or ss.auto_translate_if_no_vi
        if not can_translate:
            return None

        if not self.translation_client.enabled:
            log.warning("Translation requested but no OpenAI API key configured")
            return None

        log.info("Translation fallback: Searching English subtitle")

        # Lấy video filename để dùng cho similarity matching
        video_filename = None
        try:
            if video.media and video.media[0].parts:
                video_filename = Path(video.media[0].parts[0].file).name
                log.debug(f"Video filename for similarity: {video_filename}")
        except Exception:
            pass

        # Search English subtitle on Subsource (with filename similarity fallback)
        en_search_params = SubtitleSearchParams(
            language="en",
            title=metadata.search_title,
            year=metadata.year,
            imdb_id=metadata.imdb_id,
            tmdb_id=metadata.tmdb_id,
            season=metadata.season_number,
            episode=metadata.episode_number,
            video_filename=video_filename,
        )

        en_results = await self._search_subtitles_by_params(en_search_params, log)

        if en_results:
            log.info(f"Found {len(en_results)} English subtitle(s) on Subsource")
        else:
            log.warning("No English subtitle found on Subsource")
            return None

        subtitle_source = en_results[0].name

        # Check if requires approval
        if self.config.subtitle_settings.translation_requires_approval:
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
            source_subtitle_path=None,
            approval_type="auto_approved",
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
        """Get translation statistics (from persistent store)."""
        all_stats = self.stats.get_all()
        return {
            "total_translations": all_stats["total_translations"],
            "total_lines": all_stats["total_translation_lines"],
            "pending_count": len(self._pending_translations),
        }

    # ── Translation History ─────────────────────────────────────────────

    def _load_history(self) -> list[dict]:
        """Load translation history từ JSON file."""
        try:
            if self._history_path.exists():
                data = json.loads(self._history_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load translation history: {e}")
        return []

    def _save_history(self) -> None:
        """Persist translation history to JSON file."""
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history_path.write_text(
                json.dumps(self._translation_history, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"Failed to save translation history: {e}")

    def add_history_entry(
        self,
        *,
        rating_key: str,
        title: str,
        from_lang: str,
        to_lang: str,
        status: str,
        lines_translated: int = 0,
        cost_usd: float = 0.0,
        model: str = "",
    ) -> None:
        """
        Ghi một entry vào translation history.

        Args:
            status: "approved" | "auto_approved" | "rejected"
        """
        entry = {
            "rating_key": rating_key,
            "title": title,
            "from_lang": from_lang,
            "to_lang": to_lang,
            "status": status,
            "lines_translated": lines_translated,
            "cost_usd": cost_usd,
            "model": model,
            "timestamp": datetime.now().isoformat(),
        }
        with self._history_lock:
            self._translation_history.insert(0, entry)
            # Giới hạn 200 entries để tránh file quá lớn
            self._translation_history = self._translation_history[:200]
            self._save_history()
        logger.info(f"Translation history: [{status}] {title} ({from_lang}→{to_lang})")

    def get_translation_history(self, limit: int = 50) -> list[dict]:
        """Get translation history, mới nhất trước."""
        with self._history_lock:
            return self._translation_history[:limit]

    # ── Sync History ─────────────────────────────────────────────────

    def _load_sync_history(self) -> list[dict]:
        """Load sync history từ JSON file."""
        try:
            if self._sync_history_path.exists():
                data = json.loads(self._sync_history_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load sync history: {e}")
        return []

    def _save_sync_history(self) -> None:
        """Persist sync history to JSON file."""
        try:
            self._sync_history_path.parent.mkdir(parents=True, exist_ok=True)
            self._sync_history_path.write_text(
                json.dumps(self._sync_history, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"Failed to save sync history: {e}")

    def add_sync_history_entry(
        self,
        *,
        rating_key: str,
        title: str,
        status: str,
        source: str,
        anchors_found: int = 0,
        avg_offset_ms: int = 0,
        ref_lang: str = "",
        ref_source: str = "",
        model: str = "",
        error: str = "",
    ) -> None:
        """Ghi một entry vào sync history."""
        entry = {
            "rating_key": rating_key,
            "title": title,
            "status": status,
            "source": source,
            "anchors_found": anchors_found,
            "avg_offset_ms": avg_offset_ms,
            "ref_lang": ref_lang,
            "ref_source": ref_source,
            "model": model,
            "error": error,
            "timestamp": datetime.now().isoformat(),
        }
        with self._sync_history_lock:
            self._sync_history.insert(0, entry)
            self._sync_history = self._sync_history[:200]
            self._save_sync_history()
        logger.info(f"Sync history: [{status}] {title} ({source})")

    def get_sync_history(self, limit: int = 50) -> list[dict]:
        """Get sync history, mới nhất trước."""
        with self._sync_history_lock:
            return self._sync_history[:limit]

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
        approval_type: str = "approved",
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
            approval_type: "approved" (manual) or "auto_approved" (auto mode)

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

            concurrency = self.config.subtitle_settings.translation_batch_concurrency
            stats = await self.translation_client.translate_srt_file(
                srt_path=source_subtitle_path,
                output_path=target_subtitle_path,
                from_lang=from_lang,
                to_lang=to_lang,
                max_concurrent=concurrency,
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

            # Update persistent stats
            self.stats.increment("total_downloads")
            self.stats.increment("total_translations")
            self.stats.increment("total_translation_lines", stats["lines_translated"])

            # Record translation history
            self.add_history_entry(
                rating_key=metadata.rating_key,
                title=str(metadata),
                from_lang=from_lang,
                to_lang=to_lang,
                status=approval_type,
                lines_translated=stats["lines_translated"],
                cost_usd=stats.get("cost_usd", 0.0),
                model=stats.get("model", ""),
            )

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
