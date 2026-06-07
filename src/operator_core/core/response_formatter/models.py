from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdditionalFormattedResponse:
    text: str
    reply_to_message_id: int | None = None
    reply_markup: dict | None = None


@dataclass(frozen=True)
class FormattedResponse:
    decision: str
    text: str
    chat_id: int | None
    reply_to_message_id: int | None
    parse_mode: str | None = None
    disable_web_page_preview: bool = True
    reply_markup: dict | None = None
    callback_query_id: str | None = None
    callback_answer_text: str | None = None
    send_response: bool = True
    edit_message_id: int | None = None
    edit_message_text: str | None = None
    edit_reply_markup: dict | None = None
    additional_responses: tuple[AdditionalFormattedResponse, ...] = ()
