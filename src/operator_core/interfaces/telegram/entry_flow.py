from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from operator_core.bootstrap import BootstrapContext
from operator_core.core.command_router import route_operator_request
from operator_core.core.project_resolver import resolve_active_project_context
from operator_core.interfaces.telegram.models import (
    TelegramEntryHandoff,
    TelegramEntryRequest,
    TelegramReplyContext,
    TelegramResponseShell,
)


def _to_int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> tuple[str, str]:
    raw_text = "" if value is None else str(value)
    normalized_text = " ".join(raw_text.strip().split())
    return raw_text, normalized_text


def _get_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _extract_message(update_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    callback_query = _get_mapping(update_payload.get("callback_query"))
    if callback_query:
        callback_message = _get_mapping(callback_query.get("message"))
        if callback_message:
            return callback_message

    message = update_payload.get("message")
    if isinstance(message, Mapping):
        return message

    edited_message = update_payload.get("edited_message")
    if isinstance(edited_message, Mapping):
        return edited_message

    raise ValueError("Telegram update must contain a message or edited_message object")


def _extract_reply_context(message: Mapping[str, Any]) -> TelegramReplyContext | None:
    reply_message = _get_mapping(message.get("reply_to_message"))
    if not reply_message:
        return None

    reply_from = _get_mapping(reply_message.get("from"))
    raw_text, normalized_text = _normalize_text(
        reply_message.get("text") or reply_message.get("caption") or ""
    )

    return TelegramReplyContext(
        message_id=_to_int(reply_message.get("message_id")),
        user_id=_to_int(reply_from.get("id")),
        username=str(reply_from.get("username") or "").strip(),
        text=normalized_text or raw_text,
        reply_markup=reply_message.get("reply_markup") if isinstance(reply_message.get("reply_markup"), Mapping) else None,
    )


def normalize_telegram_update(update_payload: Mapping[str, Any]) -> TelegramEntryRequest:
    message = _extract_message(update_payload)
    chat = _get_mapping(message.get("chat"))
    callback_query = _get_mapping(update_payload.get("callback_query"))

    if callback_query:
        from_user = _get_mapping(callback_query.get("from"))
        callback_data = str(callback_query.get("data") or "").strip()
        raw_text = callback_data
        if callback_data.startswith("menu:"):
            normalized_text = f"/menu_callback {callback_data}"
        elif callback_data.startswith("text_action:"):
            normalized_text = f"/text_action_callback {callback_data}"
        elif callback_data.startswith("content_ops:"):
            normalized_text = f"/content_ops_callback {callback_data}"
        elif callback_data.startswith("platform_mode:"):
            normalized_text = f"/platform_mode_callback {callback_data}"
        else:
            normalized_text = f"/plan_demo_callback {callback_data}" if callback_data else ""
    else:
        from_user = _get_mapping(message.get("from"))
        callback_data = ""
        raw_text, normalized_text = _normalize_text(
            message.get("text") or message.get("caption") or ""
        )

    reply_context = _extract_reply_context(message)

    return TelegramEntryRequest(
        update_id=_to_int(update_payload.get("update_id")),
        callback_query_id=str(callback_query.get("id") or "").strip() or None,
        chat_id=_to_int(chat.get("id")),
        chat_type=str(chat.get("type") or "").strip(),
        message_id=_to_int(message.get("message_id")),
        user_id=_to_int(from_user.get("id")),
        username=str(from_user.get("username") or "").strip(),
        raw_text=raw_text,
        normalized_text=normalized_text,
        callback_data=callback_data,
        has_reply_context=reply_context is not None,
        reply_context=reply_context,
    )


def build_telegram_entry_handoff(
    update_payload: Mapping[str, Any],
    bootstrap_context: BootstrapContext,
) -> TelegramEntryHandoff:
    request = normalize_telegram_update(update_payload)
    project_context = resolve_active_project_context(bootstrap_context)
    routed_command = route_operator_request(request.normalized_text, project_context)

    response_shell = TelegramResponseShell(
        chat_id=request.chat_id,
        text="",
        reply_to_message_id=request.message_id,
        parse_mode=None,
        disable_web_page_preview=True,
        callback_query_id=request.callback_query_id,
    )

    return TelegramEntryHandoff(
        request=request,
        project_context=project_context,
        routed_command=routed_command,
        response_shell=response_shell,
    )
