from __future__ import annotations

from dataclasses import dataclass

from operator_core.core.command_router import RoutedCommand
from operator_core.core.project_resolver import ResolvedProjectContext


@dataclass(frozen=True)
class TelegramReplyContext:
    message_id: int | None
    user_id: int | None
    username: str
    text: str
    reply_markup: dict | None = None


@dataclass(frozen=True)
class TelegramEntryRequest:
    update_id: int | None
    callback_query_id: str | None
    chat_id: int | None
    chat_type: str
    message_id: int | None
    user_id: int | None
    username: str
    raw_text: str
    normalized_text: str
    callback_data: str
    has_reply_context: bool
    reply_context: TelegramReplyContext | None


@dataclass(frozen=True)
class TelegramResponseShell:
    chat_id: int | None
    text: str
    reply_to_message_id: int | None
    parse_mode: str | None = None
    disable_web_page_preview: bool = True
    reply_markup: dict | None = None
    callback_query_id: str | None = None
    callback_answer_text: str | None = None


@dataclass(frozen=True)
class TelegramEntryHandoff:
    request: TelegramEntryRequest
    project_context: ResolvedProjectContext
    routed_command: RoutedCommand
    response_shell: TelegramResponseShell
