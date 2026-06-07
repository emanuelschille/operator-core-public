from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from operator_core.bootstrap import BootstrapContext
from operator_core.config import OpenAISettings


OpenAITransport = Callable[
    [str, str, dict[str, str], dict[str, Any] | None, int],
    tuple[int, dict[str, Any]],
]


class OpenAIServiceError(RuntimeError):
    pass


class OpenAIConfigError(OpenAIServiceError):
    pass


class OpenAIUsageError(OpenAIServiceError):
    pass


class OpenAITransportError(OpenAIServiceError):
    pass


class OpenAIAPIError(OpenAIServiceError):
    def __init__(
        self,
        status_code: int,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload or {}
        super().__init__(f"OpenAI API error ({status_code}): {message}")


class OpenAIInvalidResponseError(OpenAIServiceError):
    pass


@dataclass(frozen=True)
class OpenAIMessage:
    role: str
    content: str


@dataclass(frozen=True)
class OpenAIResponse:
    model: str
    output_text: str
    finish_reason: str | None
    raw_payload: dict[str, Any]


def _coerce_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _extract_error_message(payload: dict[str, Any]) -> str:
    error_value = payload.get("error")

    if isinstance(error_value, Mapping):
        return str(
            error_value.get("message")
            or error_value.get("type")
            or "OpenAI API error"
        )

    if error_value:
        return str(error_value)

    return "OpenAI API error"


_MODEL_ACCESS_STATUS_CODES: frozenset[int] = frozenset({400, 403, 404})
_MODEL_ACCESS_PHRASES: tuple[str, ...] = (
    "requested model was not found",
    "does not exist or you do not have access",
    "you don't have access to this model",
    "access to this model",
    "does not exist",
)


def _is_model_access_error(exc: OpenAIAPIError) -> bool:
    """Return True if the error indicates the requested model is unavailable on this key."""
    if exc.status_code in _MODEL_ACCESS_STATUS_CODES:
        return True
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _MODEL_ACCESS_PHRASES)


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
        raise OpenAITransportError(f"OpenAI connection error: {exc.reason}") from exc


class OpenAIService:
    def __init__(
        self,
        bootstrap_context: BootstrapContext,
        *,
        transport: OpenAITransport | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.bootstrap_context = bootstrap_context
        self.transport = transport or _urllib_transport
        self.logger = logger or logging.getLogger("operator_core.integrations.openai")

    def complete_messages(
        self,
        *,
        system_prompt: str = "",
        user_prompt: str = "",
        messages: Sequence[OpenAIMessage] = (),
        model: str | None = None,
        temperature: float = 0.2,
        fallback_to_default: bool = False,
    ) -> OpenAIResponse:
        settings = self.bootstrap_context.settings.openai

        if not settings.enabled:
            raise OpenAIConfigError("OpenAI integration is disabled")

        if not settings.api_key:
            raise OpenAIConfigError("OpenAI API key is missing")

        effective_model = (model or settings.model).strip()
        if not effective_model:
            raise OpenAIUsageError("model must not be empty")

        request_messages = self._build_messages(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            messages=messages,
        )
        if not request_messages:
            raise OpenAIUsageError("at least one message or prompt is required")

        try:
            return self._call_api(
                model=effective_model,
                request_messages=request_messages,
                temperature=temperature,
                settings=settings,
            )
        except OpenAIAPIError as exc:
            # Only retry if: fallback requested, an explicit model was given,
            # the error is model-access related, and the fallback differs from the preferred.
            default_model = settings.model.strip()
            if (
                fallback_to_default
                and model  # an explicit model was requested (not the default path)
                and _is_model_access_error(exc)
                and default_model
                and default_model != effective_model
            ):
                self.logger.warning(
                    "openai complete_messages | preferred model '%s' unavailable (%s), "
                    "retrying with default '%s'",
                    effective_model,
                    exc.status_code,
                    default_model,
                )
                return self._call_api(
                    model=default_model,
                    request_messages=request_messages,
                    temperature=temperature,
                    settings=settings,
                )
            raise

    def _call_api(
        self,
        *,
        model: str,
        request_messages: list[dict[str, Any]],
        temperature: float,
        settings: OpenAISettings,
    ) -> OpenAIResponse:
        payload = {
            "model": model,
            "input": request_messages,
            "temperature": temperature,
        }

        self.logger.debug(
            "openai complete_messages | model=%s message_count=%s temperature=%s",
            model,
            len(request_messages),
            temperature,
        )

        status_code, response_payload = self.transport(
            "POST",
            f"{settings.base_url.rstrip('/')}/responses",
            self._build_headers(),
            payload,
            settings.timeout_seconds,
        )

        if status_code >= 400:
            raise OpenAIAPIError(
                status_code=status_code,
                message=_extract_error_message(response_payload),
                payload=response_payload,
            )

        return self._parse_response(response_payload, model)

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bootstrap_context.settings.openai.api_key}",
            "Content-Type": "application/json",
        }

    def _build_messages(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        messages: Sequence[OpenAIMessage],
    ) -> list[dict[str, Any]]:
        built_messages: list[dict[str, Any]] = []

        if system_prompt.strip():
            built_messages.append(
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": system_prompt.strip(),
                        }
                    ],
                }
            )

        if user_prompt.strip():
            built_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt.strip(),
                        }
                    ],
                }
            )

        for message in messages:
            if not message.content.strip():
                continue

            built_messages.append(
                {
                    "role": message.role.strip(),
                    "content": [
                        {
                            "type": "input_text",
                            "text": message.content.strip(),
                        }
                    ],
                }
            )

        return built_messages

    def _parse_response(
        self,
        payload: Mapping[str, Any],
        requested_model: str,
    ) -> OpenAIResponse:
        model = str(payload.get("model") or requested_model).strip() or requested_model

        output_text = str(payload.get("output_text") or "").strip()
        finish_reason = None

        if not output_text:
            output = payload.get("output")
            if isinstance(output, list):
                output_text = self._extract_output_text(output)
                finish_reason = self._extract_finish_reason(output)

        if not output_text:
            raise OpenAIInvalidResponseError(
                "OpenAI response did not include output_text or readable output content"
            )

        return OpenAIResponse(
            model=model,
            output_text=output_text,
            finish_reason=finish_reason,
            raw_payload=dict(payload),
        )

    def _extract_output_text(self, output: list[Any]) -> str:
        text_parts: list[str] = []

        for item in output:
            if not isinstance(item, Mapping):
                continue

            content = item.get("content")
            if not isinstance(content, list):
                continue

            for content_item in content:
                if not isinstance(content_item, Mapping):
                    continue

                if content_item.get("type") in {"output_text", "text"}:
                    text_value = str(content_item.get("text") or "").strip()
                    if text_value:
                        text_parts.append(text_value)

        return "\n".join(text_parts).strip()

    def _extract_finish_reason(self, output: list[Any]) -> str | None:
        for item in output:
            if not isinstance(item, Mapping):
                continue

            finish_reason = str(item.get("finish_reason") or "").strip()
            if finish_reason:
                return finish_reason

        return None
