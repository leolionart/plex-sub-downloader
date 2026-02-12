"""
Telegram notification client.
Gửi alerts về subtitle downloads, errors, stats.
"""

import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
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

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None) -> None:
        """Initialize Telegram client."""
        self.bot_token = bot_token or getattr(settings, "telegram_bot_token", None)
        self.chat_id = chat_id or getattr(settings, "telegram_chat_id", None)
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
    ) -> bool:
        """
        Gửi text message qua Telegram.

        Args:
            message: Message content (supports Markdown)
            parse_mode: "Markdown" or "HTML"
            disable_notification: Silent notification

        Returns:
            True nếu gửi thành công
        """
        if not self.enabled:
            logger.debug("Telegram disabled, skipping message")
            return False

        try:
            response = await self._client.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                    "disable_notification": disable_notification,
                },
            )
            response.raise_for_status()

            logger.debug(f"Telegram message sent: {message[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def notify_subtitle_downloaded(
        self,
        title: str,
        subtitle_name: str,
        language: str,
        quality: str,
    ) -> None:
        """Notify về subtitle download thành công."""
        message = f"""
✅ *Subtitle Downloaded*

📺 *Title:* {title}
🌍 *Language:* {language}
⭐ *Quality:* {quality}
📄 *File:* `{subtitle_name}`
"""
        await self.send_message(message, disable_notification=True)

    async def notify_subtitle_not_found(
        self,
        title: str,
        language: str,
    ) -> None:
        """Notify khi không tìm thấy subtitle."""
        message = f"""
⚠️ *Subtitle Not Found*

📺 *Title:* {title}
🌍 *Language:* {language}
💡 *Suggestion:* Check Subsource API or try manual search
"""
        await self.send_message(message, disable_notification=True)

    async def notify_error(
        self,
        title: str,
        error_message: str,
    ) -> None:
        """Notify về errors."""
        message = f"""
❌ *Error Processing Subtitle*

📺 *Title:* {title}
🐛 *Error:* `{error_message}`
"""
        await self.send_message(message)

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
    ) -> None:
        """Notify khi translate xong."""
        message = f"""
✅ *Translation Completed*

📺 *Title:* {title}
🌍 *Language:* {to_lang}
📝 *Lines:* {lines_translated}
"""
        await self.send_message(message, disable_notification=True)
