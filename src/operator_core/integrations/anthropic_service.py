from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request


AnthropicTransport = Callable[
    [str, str, dict[str, str], dict[str, Any] | None, int],
    tuple[int, dict[str, Any]],
]


class AnthropicServiceError(RuntimeError):
    pass


class AnthropicConfigError(AnthropicServiceError):
    pass


class AnthropicUsageError(AnthropicServiceError):
    pass


class AnthropicTransportError(AnthropicServiceError):
    pass


class AnthropicAPIError(AnthropicServiceError):
    def __init__(
        self,
        status_code: int,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload or {}
        super().__init__(f"Anthropic API error ({status_code}): {message}")


class AnthropicInvalidResponseError(AnthropicServiceError):
    pass


@dataclass(frozen=True)
class AnthropicMessage:
    role: str
    content: str


@dataclass(frozen=True)
class AnthropicResponse:
    model: str
    output_text: str
    stop_reason: str | None
    raw_payload: dict[str, Any]


def _coerce_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _extract_error_message(payload: dict[str, Any]) -> str:
    error_value = payload.get("error")
    if isinstance(error_value, Mapping):
        return str(error_value.get("message") or error_value.get("type") or "Anthropic API error")
    if error_value:
        return str(error_value)
    return "Anthropic API error"


def _urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout_seconds: int,
) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = request.Request(url=url, data=data, headers=headers, method=method)

    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return response.getcode(), _coerce_payload(payload)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, _coerce_payload(payload)
    except error.URLError as exc:
        raise AnthropicTransportError(f"Anthropic connection error: {exc.reason}") from exc


class AnthropicService:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com/v1",
        timeout_seconds: int = 30,
        transport: AnthropicTransport | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.base_url = base_url.strip() or "https://api.anthropic.com/v1"
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _urllib_transport
        self.logger = logger or logging.getLogger("operator_core.integrations.anthropic")

    def complete_messages(
        self,
        *,
        system_prompt: str = "",
        user_prompt: str = "",
        messages: Sequence[AnthropicMessage] = (),
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> AnthropicResponse:
        if not self.api_key:
            raise AnthropicConfigError("Anthropic API key is missing")

        effective_model = (model or self.model).strip()
        if not effective_model:
            raise AnthropicUsageError("model must not be empty")

        request_messages = self._build_messages(user_prompt=user_prompt, messages=messages)
        if not request_messages:
            raise AnthropicUsageError("at least one user message or prompt is required")

        payload = {
            "model": effective_model,
            "messages": request_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_prompt.strip():
            payload["system"] = system_prompt.strip()

        self.logger.debug(
            "anthropic complete_messages | model=%s message_count=%s temperature=%s",
            effective_model,
            len(request_messages),
            temperature,
        )

        status_code, response_payload = self.transport(
            "POST",
            f"{self.base_url.rstrip('/')}/messages",
            self._build_headers(),
            payload,
            self.timeout_seconds,
        )

        if status_code >= 400:
            raise AnthropicAPIError(
                status_code=status_code,
                message=_extract_error_message(response_payload),
                payload=response_payload,
            )

        return self._parse_response(response_payload, effective_model)

    def _build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _build_messages(
        self,
        *,
        user_prompt: str,
        messages: Sequence[AnthropicMessage],
    ) -> list[dict[str, Any]]:
        built_messages: list[dict[str, Any]] = []

        if user_prompt.strip():
            built_messages.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": user_prompt.strip()}],
                }
            )

        for message in messages:
            if not message.content.strip():
                continue
            built_messages.append(
                {
                    "role": message.role.strip() or "user",
                    "content": [{"type": "text", "text": message.content.strip()}],
                }
            )

        return built_messages

    def _parse_response(
        self,
        payload: Mapping[str, Any],
        requested_model: str,
    ) -> AnthropicResponse:
        model = str(payload.get("model") or requested_model).strip() or requested_model
        content = payload.get("content")
        output_text = ""
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, Mapping) and str(item.get("type") or "") == "text":
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
            output_text = "\n".join(parts).strip()

        if not output_text:
            raise AnthropicInvalidResponseError("Anthropic response did not contain output text")

        stop_reason = str(payload.get("stop_reason") or "").strip() or None
        return AnthropicResponse(
            model=model,
            output_text=output_text,
            stop_reason=stop_reason,
            raw_payload=dict(payload),
        )
