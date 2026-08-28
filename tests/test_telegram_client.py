from unittest.mock import AsyncMock, Mock

import pytest

from app.clients.telegram_client import TelegramClient
from app.models.runtime_config import RuntimeConfig


@pytest.fixture
def telegram_client() -> TelegramClient:
    client = TelegramClient(
        RuntimeConfig(telegram_bot_token="token", telegram_chat_id="123")
    )
    client._client = AsyncMock()
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True}
    client._client.post.return_value = response
    return client


@pytest.mark.asyncio
async def test_send_message_includes_inline_keyboard(telegram_client: TelegramClient) -> None:
    result = await telegram_client.send_message(
        "Pick an action",
        reply_markup=[[{"text": "Search again", "callback_data": "manual_search:42"}]],
    )

    assert result is True
    payload = telegram_client._client.post.call_args.kwargs["json"]
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "Search again", "callback_data": "manual_search:42"}]]
    }


@pytest.mark.asyncio
async def test_answer_callback_query_posts_ack(telegram_client: TelegramClient) -> None:
    result = await telegram_client.answer_callback_query("callback-1", text="Done")

    assert result is True
    call = telegram_client._client.post.call_args
    assert call.args[0].endswith("/answerCallbackQuery")
    assert call.kwargs["json"] == {
        "callback_query_id": "callback-1",
        "show_alert": False,
        "text": "Done",
    }


@pytest.mark.asyncio
async def test_subtitle_not_found_includes_recovery_actions(telegram_client: TelegramClient) -> None:
    """A not-found alert carries callbacks for the manual recovery flow."""
    await telegram_client.notify_subtitle_not_found(
        title="From S03E01", language="vi", rating_key="12688"
    )

    payload = telegram_client._client.post.call_args.kwargs["json"]
    keyboard = payload["reply_markup"]["inline_keyboard"]
    assert keyboard == [[
        {"text": "🔎 Tìm sub thủ công", "callback_data": "subtitle:manual_search:12688"},
        {"text": "⬆️ Upload bản tốt nhất", "callback_data": "subtitle:upload_best:12688"},
    ]]


@pytest.mark.asyncio
async def test_edit_message_reply_markup_removes_buttons(telegram_client: TelegramClient) -> None:
    result = await telegram_client.edit_message_reply_markup(42)

    assert result is True
    call = telegram_client._client.post.call_args
    assert call.args[0].endswith("/editMessageReplyMarkup")
    assert call.kwargs["json"] == {
        "chat_id": "123",
        "message_id": 42,
        "reply_markup": {"inline_keyboard": []},
    }
