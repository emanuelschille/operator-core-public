from __future__ import annotations

from typing import Any

import pytest

from operator_core.integrations.anthropic_service import (
    AnthropicAPIError,
    AnthropicService,
)


def _transport_ok(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    assert method == "POST"
    assert url.endswith("/messages")
    assert body is not None
    return 200, {
        "model": body["model"],
        "content": [{"type": "text", "text": "Alternative caption from Anthropic."}],
        "stop_reason": "end_turn",
    }


def _transport_error(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    return 401, {"error": {"message": "invalid x-api-key"}}


def test_anthropic_service_complete_messages_returns_text() -> None:
    service = AnthropicService(
        api_key="test-key",
        model="claude-3-5-sonnet-20241022",
        transport=_transport_ok,
    )

    response = service.complete_messages(
        system_prompt="You are a benchmark writer.",
        user_prompt="Write one caption.",
    )

    assert response.model == "claude-3-5-sonnet-20241022"
    assert response.output_text == "Alternative caption from Anthropic."
    assert response.stop_reason == "end_turn"


def test_anthropic_service_complete_messages_raises_api_error() -> None:
    service = AnthropicService(
        api_key="bad-key",
        model="claude-3-5-sonnet-20241022",
        transport=_transport_error,
    )

    with pytest.raises(AnthropicAPIError):
        service.complete_messages(user_prompt="Write one caption.")
