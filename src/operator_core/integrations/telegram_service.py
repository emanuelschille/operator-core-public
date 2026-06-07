from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any
from urllib import error, parse, request

if TYPE_CHECKING:
    from operator_core.bootstrap import BootstrapContext


TelegramTransport = Callable[
    [str, str, dict[str, str], dict[str, Any] | None],
    tuple[int, dict[str, Any]],
]

_TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramServiceError(RuntimeError):
    """Base class for all Telegram service errors."""


class TelegramConfigError(TelegramServiceError):
    """Raised when Telegram is misconfigured."""


class TelegramAPIError(TelegramServiceError):
    """Raised when the Telegram Bot API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Telegram API error ({status_code}): {message}")
        self.status_code = status_code
        self.message = message


class TelegramTransportError(TelegramServiceError):
    """Raised on network-level failures."""


def _coerce_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def _extract_error_message(payload: dict[str, Any]) -> str:
    description = payload.get("description")
    if isinstance(description, str) and description:
        return description
    return "Telegram API error"


def _urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=35) as response:
            raw = json.loads(response.read().decode("utf-8"))
            return response.status, _coerce_payload(raw)
    except error.HTTPError as exc:
        try:
            raw = json.loads(exc.read().decode("utf-8"))
            payload = _coerce_payload(raw)
        except Exception:
            payload = {}
        return exc.code, payload
    except error.URLError as exc:
        raise TelegramTransportError(f"Network error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise TelegramTransportError(f"Invalid JSON response: {exc}") from exc


class TelegramService:
    def __init__(
        self,
        bootstrap_context: "BootstrapContext",
        *,
        transport: TelegramTransport | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.bootstrap_context = bootstrap_context
        self.transport = transport or _urllib_transport
        self.logger = logger or logging.getLogger("operator_core.integrations.telegram")

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        settings = self.bootstrap_context.settings.telegram
        if not settings.enabled:
            raise TelegramConfigError("Telegram integration is disabled")
        if not settings.bot_token:
            raise TelegramConfigError("Telegram bot token is missing")

        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset

        query = parse.urlencode(params)
        url = f"{self._api_url('getUpdates')}?{query}"

        self.logger.debug("telegram get_updates | offset=%s timeout=%s", offset, timeout)
        payload = self._request("GET", url, None)

        result = payload.get("result")
        if not isinstance(result, list):
            return []
        return [u for u in result if isinstance(u, Mapping)]

    def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = self.bootstrap_context.settings.telegram
        if not settings.enabled:
            raise TelegramConfigError("Telegram integration is disabled")
        if not settings.bot_token:
            raise TelegramConfigError("Telegram bot token is missing")

        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_to_message_id is not None:
            body["reply_to_message_id"] = reply_to_message_id
        if parse_mode is not None:
            body["parse_mode"] = parse_mode
        if reply_markup is not None:
            body["reply_markup"] = reply_markup

        self.logger.debug(
            "telegram send_message | chat_id=%s reply_to=%s text_len=%s",
            chat_id,
            reply_to_message_id,
            len(text),
        )
        return self._request("POST", self._api_url("sendMessage"), body)

    def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
    ) -> dict[str, Any]:
        settings = self.bootstrap_context.settings.telegram
        if not settings.enabled:
            raise TelegramConfigError("Telegram integration is disabled")
        if not settings.bot_token:
            raise TelegramConfigError("Telegram bot token is missing")

        body: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            body["text"] = text

        self.logger.debug(
            "telegram answer_callback_query | callback_query_id=%s text_len=%s",
            callback_query_id,
            len(text or ""),
        )
        return self._request("POST", self._api_url("answerCallbackQuery"), body)

    def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = self.bootstrap_context.settings.telegram
        if not settings.enabled:
            raise TelegramConfigError("Telegram integration is disabled")
        if not settings.bot_token:
            raise TelegramConfigError("Telegram bot token is missing")

        body: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode is not None:
            body["parse_mode"] = parse_mode
        if reply_markup is not None:
            body["reply_markup"] = reply_markup

        self.logger.debug(
            "telegram edit_message_text | chat_id=%s message_id=%s text_len=%s",
            chat_id,
            message_id,
            len(text),
        )
        return self._request("POST", self._api_url("editMessageText"), body)

    def set_my_commands(
        self,
        *,
        commands: list[dict[str, str]],
    ) -> dict[str, Any]:
        settings = self.bootstrap_context.settings.telegram
        if not settings.enabled:
            raise TelegramConfigError("Telegram integration is disabled")
        if not settings.bot_token:
            raise TelegramConfigError("Telegram bot token is missing")

        body: dict[str, Any] = {"commands": commands}
        self.logger.debug(
            "telegram set_my_commands | command_count=%s",
            len(commands),
        )
        return self._request("POST", self._api_url("setMyCommands"), body)

    def _api_url(self, method: str) -> str:
        token = self.bootstrap_context.settings.telegram.bot_token
        return f"{_TELEGRAM_API_BASE}/bot{token}/{method}"

    def _request(self, method: str, url: str, body: dict[str, Any] | None) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        status_code, payload = self.transport(method, url, headers, body)

        if 200 <= status_code < 300:
            return payload

        raise TelegramAPIError(
            status_code=status_code,
            message=_extract_error_message(payload),
        )
