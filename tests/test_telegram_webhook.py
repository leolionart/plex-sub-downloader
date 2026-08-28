from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routes.telegram import telegram_webhook


def _service() -> SimpleNamespace:
    client = SimpleNamespace(
        chat_id="123",
        answer_callback_query=AsyncMock(return_value=True),
        send_message=AsyncMock(return_value=True),
        edit_message_reply_markup=AsyncMock(return_value=True),
    )
    return SimpleNamespace(
        telegram_client=client,
        search_subtitles_for_media=AsyncMock(
            return_value={"candidates": [{"provider": "subdl", "id": "abc", "name": "From.vi.srt"}]}
        ),
        execute_manual_target_upload_for_media=AsyncMock(
            return_value={"message": "Đã upload"}
        ),
    )


@pytest.mark.asyncio
async def test_webhook_dispatches_manual_search_and_clears_buttons(monkeypatch) -> None:
    service = _service()
    monkeypatch.setattr("app.main.subtitle_service", service)
    update = {
        "callback_query": {
            "id": "cb-1",
            "data": "subtitle:manual_search:12688",
            "message": {"message_id": 77, "chat": {"id": 123}},
        }
    }

    result = await telegram_webhook(update)

    assert result == {"ok": True, "action": "manual_search", "rating_key": "12688"}
    service.telegram_client.answer_callback_query.assert_awaited_once_with("cb-1", "Đang xử lý…")
    service.search_subtitles_for_media.assert_awaited_once()
    assert service.search_subtitles_for_media.await_args.kwargs["use_cache"] is False
    service.telegram_client.send_message.assert_awaited_once()
    assert "subdl:abc" in service.telegram_client.send_message.await_args.args[0]
    service.telegram_client.edit_message_reply_markup.assert_awaited_once_with(77)


@pytest.mark.asyncio
async def test_webhook_dispatches_upload_best(monkeypatch) -> None:
    service = _service()
    monkeypatch.setattr("app.main.subtitle_service", service)
    update = {
        "callback_query": {
            "id": "cb-2",
            "data": "subtitle:upload_best:12688",
            "message": {"message_id": 78, "chat": {"id": "123"}},
        }
    }

    result = await telegram_webhook(update)

    assert result["action"] == "upload_best"
    service.execute_manual_target_upload_for_media.assert_awaited_once_with(rating_key="12688")
    service.telegram_client.send_message.assert_awaited_once_with("Đã upload")


@pytest.mark.asyncio
async def test_webhook_rejects_callback_from_other_chat(monkeypatch) -> None:
    service = _service()
    monkeypatch.setattr("app.main.subtitle_service", service)
    update = {
        "callback_query": {
            "id": "cb-3",
            "data": "subtitle:upload_best:12688",
            "message": {"chat": {"id": "999"}},
        }
    }

    with pytest.raises(HTTPException) as exc:
        await telegram_webhook(update)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_webhook_ignores_unknown_callbacks() -> None:
    assert await telegram_webhook({"callback_query": {"id": "x", "data": "other:action"}}) == {
        "ok": True,
        "ignored": True,
    }
