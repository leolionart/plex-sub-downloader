"""
Telegram notification client.
Gửi alerts về subtitle downloads, errors, stats.
"""

from typing import Any, Mapping, Sequence

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.models.runtime_config import RuntimeConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TelegramClientError(Exception):
    """Base exception for Telegram client errors."""
    pass


class TelegramClient:
    """
    Client để gửi notifications qua Telegram Bot API.

    Setup:
    1. Create bot: @BotFather on Telegram
    2. Get bot token
    3. Get chat ID: Send message to bot, then visit:
       https://api.telegram.org/bot<TOKEN>/getUpdates
    """

    def __init__(self, config: RuntimeConfig, bot_token: str | None = None, chat_id: str | None = None) -> None:
        """Initialize Telegram client."""
        self._config = config
        self.bot_token = bot_token or config.telegram_bot_token
        self.chat_id = chat_id or config.telegram_chat_id
        self.enabled = bool(self.bot_token and self.chat_id)

        if not self.enabled:
            logger.info("Telegram notifications disabled - no bot_token or chat_id")
        else:
            logger.info("Telegram notifications enabled")

        self.base_url = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else ""
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def send_message(
        self,
        message: str,
        parse_mode: str = "Markdown",
        disable_notification: bool = False,
        reply_markup: Mapping[str, Any] | Sequence[Sequence[Mapping[str, Any]]] | None = None,
    ) -> bool:
        """
        Gửi text message qua Telegram.

        Args:
            message: Message content (supports Markdown)
            parse_mode: "Markdown" or "HTML"
            disable_notification: Silent notification
            reply_markup: Telegram inline keyboard markup. Either the raw
                ``{"inline_keyboard": ...}`` object or a 2-D sequence of
                button dictionaries (``{"text": ..., "callback_data": ...}``).

        Returns:
            True nếu gửi thành công
        """
        if not self.enabled:
            logger.debug("Telegram disabled, skipping message")
            return False

        try:
            payload: dict[str, Any] = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            }
            if reply_markup:
                payload["reply_markup"] = self._normalize_reply_markup(reply_markup)

            response = await self._client.post(
                f"{self.base_url}/sendMessage",
                json=payload,
            )
            response.raise_for_status()

            logger.debug(f"Telegram message sent: {message[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    @staticmethod
    def _normalize_reply_markup(
        reply_markup: Mapping[str, Any] | Sequence[Sequence[Mapping[str, Any]]],
    ) -> dict[str, Any]:
        """Normalize shorthand button rows to Telegram's markup object."""
        if isinstance(reply_markup, Mapping):
            return dict(reply_markup)
        return {"inline_keyboard": [list(row) for row in reply_markup]}

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        """Acknowledge a Telegram callback query to stop the client spinner."""
        if not self.enabled:
            return False
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text
        try:
            response = await self._client.post(f"{self.base_url}/answerCallbackQuery", json=payload)
            response.raise_for_status()
            return bool(response.json().get("ok", True))
        except Exception as e:
            logger.error(f"Failed to answer Telegram callback query: {e}")
            return False

    async def edit_message_reply_markup(
        self,
        message_id: int,
        reply_markup: Mapping[str, Any] | Sequence[Sequence[Mapping[str, Any]]] | None = None,
        chat_id: str | int | None = None,
    ) -> bool:
        """Replace/remove inline buttons on an existing message."""
        if not self.enabled:
            return False
        payload: dict[str, Any] = {
            "chat_id": chat_id or self.chat_id,
            "message_id": message_id,
            "reply_markup": self._normalize_reply_markup(reply_markup or {"inline_keyboard": []}),
        }
        try:
            response = await self._client.post(
                f"{self.base_url}/editMessageReplyMarkup", json=payload
            )
            response.raise_for_status()
            return bool(response.json().get("ok", True))
        except Exception as e:
            logger.error(f"Failed to edit Telegram message markup: {e}")
            return False

    async def notify_processing_started(
        self,
        title: str,
        language: str,
    ) -> None:
        """Notify khi bắt đầu xử lý subtitle cho media mới."""
        message = (
            f"🎬 *New Media Detected*\n\n"
            f"📺 *Title:* {title}\n"
            f"🌍 *Language:* {language}\n"
            f"🔍 *Status:* Searching subtitle..."
        )
        await self.send_message(message, disable_notification=True)

    async def notify_subtitle_found(
        self,
        title: str,
        subtitle_name: str,
        language: str,
        quality: str,
        total_results: int,
        rating_key: str | None = None,
        subtitle_id: str | None = None,
    ) -> None:
        """Notify khi tìm thấy subtitle."""
        message = (
            f"🔎 *Subtitle Found*\n\n"
            f"📺 *Title:* {title}\n"
            f"🌍 *Language:* {language}\n"
            f"📄 *Best match:* `{subtitle_name}`\n"
            f"⭐ *Quality:* {quality}\n"
            f"📊 *Results:* {total_results} subtitle(s)"
        )
        buttons = None
        if rating_key:
            target = subtitle_id or "best"
            buttons = [[
                {"text": "⬆️ Upload bản này", "callback_data": f"subtitle:upload:{rating_key}:{target}"},
                {"text": "🔎 Tìm lại", "callback_data": f"subtitle:manual_search:{rating_key}"},
            ], [{"text": "🌐 Dịch sang Vietsub", "callback_data": f"subtitle:translate:{rating_key}"}]]
        if buttons:
            await self.send_message(message, disable_notification=True, reply_markup=buttons)
        else:
            await self.send_message(message, disable_notification=True)

    async def notify_subtitle_downloaded(
        self,
        title: str,
        subtitle_name: str,
        language: str,
        quality: str,
        rating_key: str | None = None,
    ) -> None:
        """Notify về subtitle download và upload thành công."""
        message = (
            f"✅ *Subtitle Uploaded to Plex*\n\n"
            f"📺 *Title:* {title}\n"
            f"🌍 *Language:* {language}\n"
            f"⭐ *Quality:* {quality}\n"
            f"📄 *File:* `{subtitle_name}`"
        )
        buttons = None
        if rating_key:
            buttons = [[
                {"text": "🔄 Sync timing", "callback_data": f"subtitle:sync:{rating_key}"},
                {"text": "✨ Cải thiện", "callback_data": f"subtitle:improve:{rating_key}"},
            ], [{"text": "🌐 Dịch lại", "callback_data": f"subtitle:translate:{rating_key}"}]]
        if buttons:
            await self.send_message(message, disable_notification=True, reply_markup=buttons)
        else:
            await self.send_message(message, disable_notification=True)

    async def notify_subtitle_not_found(
        self,
        title: str,
        language: str,
        rating_key: str | None = None,
    ) -> None:
        """Notify khi không tìm thấy subtitle."""
        message = f"""
⚠️ *Subtitle Not Found*

📺 *Title:* {title}
🌍 *Language:* {language}
💡 *Suggestion:* Check Subsource API or try manual search
"""
        buttons = None
        if rating_key:
            buttons = [[
                {"text": "🔎 Tìm sub thủ công", "callback_data": f"subtitle:manual_search:{rating_key}"},
                {"text": "⬆️ Upload bản tốt nhất", "callback_data": f"subtitle:upload_best:{rating_key}"},
            ]]
        if buttons:
            await self.send_message(message, disable_notification=True, reply_markup=buttons)
        else:
            await self.send_message(message, disable_notification=True)

    async def notify_error(
        self,
        title: str,
        error_message: str,
        rating_key: str | None = None,
    ) -> None:
        """Notify về errors."""
        message = f"""
❌ *Error Processing Subtitle*

📺 *Title:* {title}
🐛 *Error:* `{error_message}`
"""
        buttons = None
        if rating_key:
            buttons = [[
                {"text": "🔁 Retry workflow", "callback_data": f"subtitle:retry:{rating_key}"},
                {"text": "🔎 Tìm sub", "callback_data": f"subtitle:manual_search:{rating_key}"},
                {"text": "🔄 Sync timing", "callback_data": f"subtitle:sync:{rating_key}"},
            ]]
        await self.send_message(message, reply_markup=buttons)

    async def notify_daily_stats(
        self,
        downloads: int,
        skipped: int,
        errors: int,
        success_rate: float,
    ) -> None:
        """Gửi daily stats summary."""
        message = f"""
📊 *Daily Subtitle Stats*

✅ Downloads: {downloads}
⏭️ Skipped: {skipped}
❌ Errors: {errors}
📈 Success Rate: {success_rate:.1f}%
"""
        await self.send_message(message)

    async def notify_translation_started(
        self,
        title: str,
        from_lang: str,
        to_lang: str,
    ) -> None:
        """Notify khi bắt đầu translate subtitle."""
        message = f"""
🔄 *Translating Subtitle*

📺 *Title:* {title}
🌐 *Translation:* {from_lang} → {to_lang}
⏳ *Status:* Processing with OpenAI...
"""
        await self.send_message(message, disable_notification=True)

    async def notify_translation_completed(
        self,
        title: str,
        to_lang: str,
        lines_translated: int,
        rating_key: str | None = None,
    ) -> None:
        """Notify khi translate xong."""
        message = f"""
✅ *Translation Completed*

📺 *Title:* {title}
🌍 *Language:* {to_lang}
📝 *Lines:* {lines_translated}
"""
        buttons = None
        if rating_key:
            buttons = [[
                {"text": "✨ Cải thiện Vietsub", "callback_data": f"subtitle:improve:{rating_key}"},
                {"text": "🔄 Sync timing", "callback_data": f"subtitle:sync:{rating_key}"},
            ]]
        await self.send_message(message, disable_notification=True, reply_markup=buttons)

    async def notify_sync_started(
        self,
        title: str,
    ) -> None:
        """Notify khi bắt đầu sync timing."""
        message = (
            f"🔄 *Syncing Subtitle Timing*\n\n"
            f"📺 *Title:* {title}\n"
            f"⏳ *Status:* Analyzing timing with AI..."
        )
        await self.send_message(message, disable_notification=True)

    async def notify_sync_completed(
        self,
        title: str,
        anchors: int,
        avg_offset_ms: int,
        rating_key: str | None = None,
    ) -> None:
        """Notify khi sync timing xong."""
        offset_s = abs(avg_offset_ms) / 1000
        direction = "trễ" if avg_offset_ms > 0 else "sớm"
        message = (
            f"✅ *Subtitle Timing Synced*\n\n"
            f"📺 *Title:* {title}\n"
            f"🎯 *Anchors:* {anchors} điểm neo\n"
            f"⏱ *Avg offset:* {offset_s:.1f}s ({direction})"
        )
        buttons = None
        if rating_key:
            buttons = [[
                {"text": "🔄 Sync lại", "callback_data": f"subtitle:sync:{rating_key}"},
                {"text": "✨ Cải thiện Vietsub", "callback_data": f"subtitle:improve:{rating_key}"},
            ]]
        await self.send_message(message, disable_notification=True, reply_markup=buttons)
