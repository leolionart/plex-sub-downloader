"""Inbound Telegram webhook for inline-keyboard callback actions."""

from typing import Any

from fastapi import APIRouter, HTTPException

from app.utils.logger import RequestContextLogger
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


def get_subtitle_service():
    import app.main as main_app
    if not main_app.subtitle_service:
        main_app.ensure_service_initialized()
    if not main_app.subtitle_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return main_app.subtitle_service


@router.post("/webhook")
async def telegram_webhook(update: dict[str, Any]) -> dict[str, Any]:
    """Handle inline button callbacks sent by Telegram Bot API."""
    callback = update.get("callback_query") or {}
    callback_id = callback.get("id")
    data = str(callback.get("data") or "")
    if not callback_id or not data.startswith("subtitle:"):
        return {"ok": True, "ignored": True}

    service = get_subtitle_service()
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if service.telegram_client.chat_id and chat_id != str(service.telegram_client.chat_id):
        raise HTTPException(status_code=403, detail="Telegram chat is not authorized")
    await service.telegram_client.answer_callback_query(callback_id, "Đang xử lý…")
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    rating_key = parts[2] if len(parts) > 2 else ""
    subtitle_id = ":".join(parts[3:]) if len(parts) > 3 else None
    if not rating_key:
        return {"ok": True, "ignored": True}

    log = RequestContextLogger(logger, rating_key)
    try:
        if action == "manual_search":
            result = await service.search_subtitles_for_media(rating_key, log, use_cache=False)
            candidates = result.get("candidates", [])[:8]
            text = "🔎 *Kết quả tìm sub thủ công*\n\n" + ("\n".join(
                f"• `{c.get('provider')}:{c.get('id')}` — {c.get('name', 'Subtitle')}"
                for c in candidates
            ) or "Không tìm thấy subtitle phù hợp")
            buttons = [[{
                "text": f"⬆️ {c.get('provider')}:{c.get('id')}",
                "callback_data": f"subtitle:upload:{rating_key}:{c.get('provider')}:{c.get('id')}",
            }] for c in candidates[:4]]
            await service.telegram_client.send_message(text, reply_markup=buttons or None)
        elif action in {"upload_best", "upload"}:
            selected_id = None if not subtitle_id or subtitle_id == "best" else subtitle_id
            result = await service.execute_manual_target_upload_for_media(
                rating_key=rating_key, subtitle_id=selected_id, use_cache=False
            )
            await service.telegram_client.send_message(result.get("message", "Đã xử lý"))
        elif action == "sync":
            result = await service.execute_sync_for_media(rating_key=rating_key, request_id=rating_key)
            await service.telegram_client.send_message(result.get("message", "Đã xử lý sync timing"))
        elif action == "translate":
            result = await service.execute_translate_for_media(rating_key=rating_key, request_id=rating_key)
            await service.telegram_client.send_message(result.get("message", "Đã xử lý dịch subtitle"))
        elif action == "improve":
            result = await service.execute_improve_for_media(rating_key=rating_key, request_id=rating_key)
            await service.telegram_client.send_message(result.get("message", "Đã xử lý cải thiện subtitle"))
        elif action == "retry":
            result = await service.process_webhook(rating_key=rating_key, event="library.new", request_id=rating_key)
            await service.telegram_client.send_message(result.get("message", "Đã retry workflow"))
        else:
            return {"ok": True, "ignored": True}
    except Exception as exc:
        logger.exception("Telegram callback action failed: %s", exc)
        await service.telegram_client.send_message(f"❌ Không thể thực hiện `{action}`: {exc}")
        return {"ok": False, "action": action, "rating_key": rating_key, "error": str(exc)}
    if message.get("message_id"):
        await service.telegram_client.edit_message_reply_markup(message["message_id"], chat_id=chat_id)
    return {"ok": True, "action": action, "rating_key": rating_key}
