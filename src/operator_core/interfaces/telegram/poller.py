from __future__ import annotations

import logging
from threading import Event
from typing import TYPE_CHECKING, Any

from operator_core.interfaces.telegram.entry_flow import (
    build_telegram_entry_handoff,
    normalize_telegram_update,
)
from operator_core.integrations.telegram_service import TelegramServiceError

if TYPE_CHECKING:
    from operator_core.bootstrap import BootstrapContext
    from operator_core.core.request_flow.service import RequestFlowService
    from operator_core.core.response_formatter.service import ResponseFormatterService
    from operator_core.integrations.telegram_service import TelegramService

_PLATFORM_CALLBACK_LABELS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram_reel": "Instagram",
    "facebook_reel": "Facebook",
    "youtube_short": "YouTube",
}

_IDEA_FALLBACK_RECOVERY_LABELS: dict[str, str] = {
    "idea_fresh": "Frischer",
    "idea_angle": "Neuer Winkel",
}


def is_update_allowed(
    *,
    user_id: int | None,
    chat_id: int | None,
    allowed_user_ids: tuple[str, ...],
    allowed_chat_ids: tuple[str, ...],
) -> bool:
    user_id_str = str(user_id) if user_id is not None else None
    chat_id_str = str(chat_id) if chat_id is not None else None

    if allowed_user_ids and user_id_str in allowed_user_ids:
        return True
    if allowed_chat_ids and chat_id_str in allowed_chat_ids:
        return True
    return False


class TelegramPoller:
    def __init__(
        self,
        *,
        bootstrap_context: "BootstrapContext",
        telegram_service: "TelegramService",
        request_flow_service: "RequestFlowService",
        formatter_service: "ResponseFormatterService",
        logger: logging.Logger | None = None,
    ) -> None:
        self._bootstrap_context = bootstrap_context
        self._telegram_service = telegram_service
        self._request_flow_service = request_flow_service
        self._formatter_service = formatter_service
        self._logger = logger or logging.getLogger("operator_core.interfaces.telegram.poller")

    @staticmethod
    def _extract_daily_plan_record_id(reply_markup: dict[str, Any] | None) -> str:
        if not isinstance(reply_markup, dict):
            return ""
        inline_keyboard = reply_markup.get("inline_keyboard")
        if not isinstance(inline_keyboard, list):
            return ""
        for row in inline_keyboard:
            if not isinstance(row, list):
                continue
            for button in row:
                if not isinstance(button, dict):
                    continue
                callback_data = str(button.get("callback_data") or "").strip()
                parts = callback_data.split(":")
                if len(parts) >= 3 and parts[0] == "plan_demo" and parts[2].strip():
                    return parts[2].strip()
        return ""

    def _register_daily_plan_message(
        self,
        *,
        chat_id: int | None,
        message_id: int | None,
        reply_markup: dict[str, Any] | None,
    ) -> None:
        if chat_id is None or message_id is None:
            return
        record_id = self._extract_daily_plan_record_id(reply_markup)
        if not record_id:
            return
        register = getattr(self._request_flow_service, "register_daily_plan_message", None)
        if callable(register):
            register(chat_id=chat_id, message_id=message_id, record_id=record_id)

    def run_until_stopped(self, stop_event: Event) -> None:
        self._logger.info(
            "telegram poller starting | project=%s",
            self._bootstrap_context.settings.app.active_project,
        )
        offset: int | None = None

        while not stop_event.is_set():
            try:
                updates = self._telegram_service.get_updates(offset=offset, timeout=30)
            except TelegramServiceError as exc:
                self._logger.error("telegram poller get_updates failed | error=%s", exc)
                stop_event.wait(timeout=5)
                continue
            except Exception as exc:
                self._logger.error("telegram poller unexpected error | error=%s", exc)
                stop_event.wait(timeout=5)
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                self._handle_update(update)

        self._logger.info(
            "telegram poller stopped | project=%s",
            self._bootstrap_context.settings.app.active_project,
        )

    def _handle_update(self, update: dict[str, Any]) -> None:
        update_id = update.get("update_id", "?")
        try:
            entry_request = normalize_telegram_update(update)
        except (ValueError, KeyError) as exc:
            self._logger.warning("telegram poller malformed update | update_id=%s error=%s", update_id, exc)
            return

        settings = self._bootstrap_context.settings.telegram
        if not is_update_allowed(
            user_id=entry_request.user_id,
            chat_id=entry_request.chat_id,
            allowed_user_ids=settings.allowed_user_ids,
            allowed_chat_ids=settings.allowed_chat_ids,
        ):
            self._logger.warning(
                "telegram poller blocked update | update_id=%s user_id=%s chat_id=%s",
                update_id,
                entry_request.user_id,
                entry_request.chat_id,
            )
            return

        self._logger.info(
            "telegram poller accepted update | update_id=%s user_id=%s chat_id=%s text_len=%s",
            update_id,
            entry_request.user_id,
            entry_request.chat_id,
            len(entry_request.normalized_text),
        )

        try:
            handoff = build_telegram_entry_handoff(update, self._bootstrap_context)
            (
                platform_callback_answered,
                platform_edit_message_id,
                platform_edit_text,
            ) = self._confirm_platform_continue_callback(
                entry_request=entry_request,
                update_id=update_id,
            )
            (
                recovery_callback_answered,
                recovery_edit_message_id,
                recovery_edit_text,
            ) = self._confirm_idea_fallback_recovery_callback(
                entry_request=entry_request,
                update_id=update_id,
            )
            result = self._request_flow_service.handle_telegram_entry_handoff(handoff)
        except Exception as exc:
            self._logger.error(
                "telegram poller dispatch failed | update_id=%s error=%s", update_id, exc
            )
            return

        self._send_reply(
            result,
            update_id=update_id,
            callback_answer_already_sent=platform_callback_answered or recovery_callback_answered,
            edit_message_id_already_sent=platform_edit_message_id
            if platform_edit_message_id is not None
            else recovery_edit_message_id,
            edit_message_text_already_sent=platform_edit_text
            if platform_edit_text is not None
            else recovery_edit_text,
        )

    def _confirm_platform_continue_callback(
        self,
        *,
        entry_request: Any,
        update_id: Any,
    ) -> tuple[bool, int | None, str | None]:
        callback_data = str(getattr(entry_request, "callback_data", "") or "")
        if not callback_data.startswith("platform_mode:continue:"):
            return False, None, None

        platform = callback_data.split(":", 2)[2].strip()
        label = _PLATFORM_CALLBACK_LABELS.get(platform, platform or "Plattform")
        callback_query_id = getattr(entry_request, "callback_query_id", None)
        callback_answered = False
        if isinstance(callback_query_id, str) and callback_query_id:
            try:
                self._telegram_service.answer_callback_query(
                    callback_query_id=callback_query_id,
                    text=label,
                )
                callback_answered = True
            except Exception as exc:
                self._logger.warning(
                    "telegram poller immediate platform callback answer failed | update_id=%s error=%s",
                    update_id,
                    exc,
                )

        chat_id = getattr(entry_request, "chat_id", None)
        message_id = getattr(entry_request, "message_id", None)
        edit_text = f"✅ Plattform gewählt: {label}"
        edit_message_id: int | None = None
        if chat_id is not None and isinstance(message_id, int):
            try:
                self._telegram_service.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=edit_text,
                    parse_mode=None,
                    disable_web_page_preview=True,
                    reply_markup=None,
                )
                edit_message_id = message_id
            except Exception as exc:
                self._logger.warning(
                    "telegram poller immediate platform edit failed | update_id=%s error=%s",
                    update_id,
                    exc,
                )
        return callback_answered, edit_message_id, edit_text if edit_message_id is not None else None

    def _confirm_idea_fallback_recovery_callback(
        self,
        *,
        entry_request: Any,
        update_id: Any,
    ) -> tuple[bool, int | None, str | None]:
        callback_data = str(getattr(entry_request, "callback_data", "") or "")
        parts = callback_data.split(":", 2)
        if len(parts) != 3 or parts[0] != "content_ops":
            return False, None, None

        label = _IDEA_FALLBACK_RECOVERY_LABELS.get(parts[1])
        if label is None:
            return False, None, None

        callback_query_id = getattr(entry_request, "callback_query_id", None)
        callback_answered = False
        if isinstance(callback_query_id, str) and callback_query_id:
            try:
                self._telegram_service.answer_callback_query(
                    callback_query_id=callback_query_id,
                    text=label,
                )
                callback_answered = True
            except Exception as exc:
                self._logger.warning(
                    "telegram poller immediate idea fallback callback answer failed | update_id=%s error=%s",
                    update_id,
                    exc,
                )

        chat_id = getattr(entry_request, "chat_id", None)
        message_id = getattr(entry_request, "message_id", None)
        edit_text = f"↻ {label} wird erstellt."
        edit_message_id: int | None = None
        if chat_id is not None and isinstance(message_id, int):
            try:
                self._telegram_service.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=edit_text,
                    parse_mode=None,
                    disable_web_page_preview=True,
                    reply_markup=None,
                )
                edit_message_id = message_id
            except Exception as exc:
                self._logger.warning(
                    "telegram poller immediate idea fallback edit failed | update_id=%s error=%s",
                    update_id,
                    exc,
                )
        return callback_answered, edit_message_id, edit_text if edit_message_id is not None else None

    def _send_reply(
        self,
        result: Any,
        *,
        update_id: Any,
        callback_answer_already_sent: bool = False,
        edit_message_id_already_sent: int | None = None,
        edit_message_text_already_sent: str | None = None,
    ) -> None:
        try:
            formatted = self._formatter_service.format_request_flow_result(result)
        except Exception as exc:
            self._logger.error(
                "telegram poller format failed | update_id=%s error=%s", update_id, exc
            )
            return

        try:
            callback_query_id = getattr(formatted, "callback_query_id", None)
            callback_answer_text = getattr(formatted, "callback_answer_text", None)
            if (
                isinstance(callback_query_id, str)
                and callback_query_id
                and not callback_answer_already_sent
            ):
                try:
                    self._telegram_service.answer_callback_query(
                        callback_query_id=callback_query_id,
                        text=callback_answer_text if isinstance(callback_answer_text, str) else None,
                    )
                except Exception as exc:
                    self._logger.warning(
                        "telegram poller callback answer failed; continuing response delivery | update_id=%s error=%s",
                        update_id,
                        exc,
                    )

            edit_message_text = getattr(formatted, "edit_message_text", None)
            edit_message_id = getattr(formatted, "edit_message_id", None)
            edit_reply_markup = getattr(formatted, "edit_reply_markup", None)
            if (
                formatted.chat_id is not None
                and isinstance(edit_message_id, int)
                and isinstance(edit_message_text, str)
                and edit_message_text
                and (
                    edit_message_id != edit_message_id_already_sent
                    or edit_message_text != edit_message_text_already_sent
                )
            ):
                if not isinstance(edit_reply_markup, dict):
                    edit_reply_markup = None
                self._telegram_service.edit_message_text(
                    chat_id=formatted.chat_id,
                    message_id=edit_message_id,
                    text=edit_message_text,
                    parse_mode=formatted.parse_mode,
                    disable_web_page_preview=formatted.disable_web_page_preview,
                    reply_markup=edit_reply_markup,
                )
                self._register_daily_plan_message(
                    chat_id=formatted.chat_id,
                    message_id=edit_message_id,
                    reply_markup=edit_reply_markup,
                )

            send_response = getattr(formatted, "send_response", True)
            if send_response is False:
                self._logger.debug("telegram poller response suppressed | update_id=%s", update_id)
                return

            if formatted.chat_id is None or not formatted.text:
                self._logger.debug("telegram poller no reply message to send | update_id=%s", update_id)
                return

            reply_markup = getattr(formatted, "reply_markup", None)
            if not isinstance(reply_markup, dict):
                reply_markup = None

            sent_payload = self._telegram_service.send_message(
                chat_id=formatted.chat_id,
                text=formatted.text,
                reply_to_message_id=formatted.reply_to_message_id,
                parse_mode=formatted.parse_mode,
                disable_web_page_preview=formatted.disable_web_page_preview,
                reply_markup=reply_markup,
            )
            sent_result = sent_payload.get("result") if isinstance(sent_payload, dict) else None
            sent_message_id = sent_result.get("message_id") if isinstance(sent_result, dict) else None
            if isinstance(sent_message_id, int):
                self._register_daily_plan_message(
                    chat_id=formatted.chat_id,
                    message_id=sent_message_id,
                    reply_markup=reply_markup,
                )
            self._logger.info(
                "telegram poller reply sent | update_id=%s chat_id=%s decision=%s",
                update_id,
                formatted.chat_id,
                formatted.decision,
            )

            additional_responses = getattr(formatted, "additional_responses", ())
            for response in additional_responses:
                response_text = getattr(response, "text", "")
                if not response_text:
                    continue
                response_reply_markup = getattr(response, "reply_markup", None)
                if not isinstance(response_reply_markup, dict):
                    response_reply_markup = None
                extra_payload = self._telegram_service.send_message(
                    chat_id=formatted.chat_id,
                    text=response_text,
                    reply_to_message_id=getattr(response, "reply_to_message_id", None),
                    parse_mode=formatted.parse_mode,
                    disable_web_page_preview=formatted.disable_web_page_preview,
                    reply_markup=response_reply_markup,
                )
                extra_result = extra_payload.get("result") if isinstance(extra_payload, dict) else None
                extra_message_id = extra_result.get("message_id") if isinstance(extra_result, dict) else None
                if isinstance(extra_message_id, int):
                    self._register_daily_plan_message(
                        chat_id=formatted.chat_id,
                        message_id=extra_message_id,
                        reply_markup=response_reply_markup,
                    )
        except TelegramServiceError as exc:
            self._logger.error(
                "telegram poller send_message failed | update_id=%s error=%s", update_id, exc
            )
