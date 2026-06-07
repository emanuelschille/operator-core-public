from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operator_core.core.backbone.execution_service import ExecutionResult
from operator_core.core.backbone.models import RequestContext
from operator_core.interfaces.telegram.models import TelegramEntryHandoff


@dataclass(frozen=True)
class AdditionalFormatterMessage:
    text: str
    reply_markup: dict[str, Any] | None = None
    reply_to_message_id: int | None = None


@dataclass(frozen=True)
class FormatterPayload:
    project_key: str
    project_display_name: str
    command_name: str
    command_body: str
    response_chat_id: int | None
    response_reply_to_message_id: int | None
    decision: str
    message_text: str
    execution_summary: dict[str, Any]
    response_reply_markup: dict[str, Any] | None = None
    callback_query_id: str | None = None
    callback_answer_text: str | None = None
    send_response: bool = True
    edit_message_id: int | None = None
    edit_message_text: str | None = None
    edit_reply_markup: dict[str, Any] | None = None
    additional_messages: tuple[AdditionalFormatterMessage, ...] = ()


@dataclass(frozen=True)
class RequestFlowResult:
    entry_handoff: TelegramEntryHandoff
    request_context: RequestContext
    decision: str
    was_executed: bool
    execution_result: ExecutionResult | None
    formatter_payload: FormatterPayload
