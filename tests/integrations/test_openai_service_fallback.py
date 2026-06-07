"""
Tests for OpenAIService model-access fallback behavior.

Covers:
  - preferred model succeeds → no fallback, one transport call
  - preferred model 403 + fallback_to_default=True → retries with settings.model, returns fallback response
  - preferred model 403 + fallback also fails → raises the fallback error
  - non-model-access error (e.g., 400) with fallback_to_default=True → NOT retried, raises immediately
  - fallback_to_default=False (default) → 403 raises immediately, no retry
  - fallback skipped when preferred model equals settings default (no-op retry)
  - _is_model_access_error correctly classifies 403 and known message patterns
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AppSettings,
    AirtableSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.integrations.openai_service import (
    OpenAIAPIError,
    OpenAIService,
    _is_model_access_error,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bootstrap(default_model: str = "gpt-4o") -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="WARNING",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(
            enabled=False,
            bot_token="",
            allowed_user_ids=(),
            allowed_chat_ids=(),
        ),
        airtable=AirtableSettings(
            enabled=False,
            api_key="",
            project_base_ids={},
        ),
        openai=OpenAISettings(
            enabled=True,
            api_key="test-key",
            model=default_model,
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )
    return BootstrapContext(
        settings=settings,
        runtime_path=Path("projects/everydayengel/runtime.yaml"),
        project_runtime={"project_key": "everydayengel"},
    )


def _success_payload(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "output": [
            {
                "content": [{"type": "output_text", "text": f"Response from {model}"}],
                "finish_reason": "stop",
            }
        ],
    }


def _model_access_error_payload() -> dict[str, Any]:
    return {"error": {"message": "requested model was not found"}}


def _bad_request_payload() -> dict[str, Any]:
    return {"error": {"message": "Invalid prompt parameter"}}


# ---------------------------------------------------------------------------
# _is_model_access_error unit tests
# ---------------------------------------------------------------------------

def test_is_model_access_error_on_403() -> None:
    exc = OpenAIAPIError(403, "requested model was not found")
    assert _is_model_access_error(exc) is True


def test_is_model_access_error_on_404_with_model_message() -> None:
    exc = OpenAIAPIError(404, "The model gpt-5.4 does not exist or you do not have access to it.")
    assert _is_model_access_error(exc) is True


def test_is_not_model_access_error_on_400() -> None:
    exc = OpenAIAPIError(400, "Invalid prompt parameter")
    assert _is_model_access_error(exc) is False


def test_is_not_model_access_error_on_429() -> None:
    exc = OpenAIAPIError(429, "Rate limit exceeded")
    assert _is_model_access_error(exc) is False


def test_is_not_model_access_error_on_500() -> None:
    exc = OpenAIAPIError(500, "Internal server error")
    assert _is_model_access_error(exc) is False


def test_is_not_model_access_error_on_401() -> None:
    exc = OpenAIAPIError(401, "invalid x-api-key")
    assert _is_model_access_error(exc) is False


# ---------------------------------------------------------------------------
# complete_messages fallback behavior
# ---------------------------------------------------------------------------

def test_preferred_model_succeeds_no_fallback_used() -> None:
    """When preferred model works, exactly one transport call is made."""
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(body["model"])
        return 200, _success_payload(body["model"])

    svc = OpenAIService(_make_bootstrap(default_model="gpt-4o"), transport=transport)
    response = svc.complete_messages(
        user_prompt="test",
        model="gpt-5.4",
        fallback_to_default=True,
    )

    assert calls == ["gpt-5.4"]
    assert "gpt-5.4" in response.model


def test_preferred_model_403_falls_back_to_settings_default() -> None:
    """When preferred model returns 403, retries with settings.model and succeeds."""
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(body["model"])
        if body["model"] == "gpt-5.4":
            return 403, _model_access_error_payload()
        return 200, _success_payload(body["model"])

    svc = OpenAIService(_make_bootstrap(default_model="gpt-4o"), transport=transport)
    response = svc.complete_messages(
        user_prompt="test",
        model="gpt-5.4",
        fallback_to_default=True,
    )

    assert calls == ["gpt-5.4", "gpt-4o"]
    assert "gpt-4o" in response.model
    assert "gpt-4o" in response.output_text


def test_preferred_model_403_fallback_also_fails_raises_error() -> None:
    """When preferred model 403s AND fallback also fails, raises the fallback error."""
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(body["model"])
        if body["model"] == "gpt-5.4":
            return 403, _model_access_error_payload()
        return 500, {"error": {"message": "Internal server error"}}

    svc = OpenAIService(_make_bootstrap(default_model="gpt-4o"), transport=transport)
    with pytest.raises(OpenAIAPIError) as exc_info:
        svc.complete_messages(
            user_prompt="test",
            model="gpt-5.4",
            fallback_to_default=True,
        )

    assert calls == ["gpt-5.4", "gpt-4o"]
    assert exc_info.value.status_code == 500


def test_non_model_access_error_is_not_retried() -> None:
    """A 400 bad-request error with fallback_to_default=True is NOT retried."""
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(body["model"])
        return 400, _bad_request_payload()

    svc = OpenAIService(_make_bootstrap(default_model="gpt-4o"), transport=transport)
    with pytest.raises(OpenAIAPIError) as exc_info:
        svc.complete_messages(
            user_prompt="test",
            model="gpt-5.4",
            fallback_to_default=True,
        )

    assert calls == ["gpt-5.4"]
    assert exc_info.value.status_code == 400


def test_fallback_not_triggered_when_flag_is_false() -> None:
    """Without fallback_to_default=True, a 403 raises immediately."""
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(body["model"])
        return 403, _model_access_error_payload()

    svc = OpenAIService(_make_bootstrap(default_model="gpt-4o"), transport=transport)
    with pytest.raises(OpenAIAPIError) as exc_info:
        svc.complete_messages(
            user_prompt="test",
            model="gpt-5.4",
        )

    assert calls == ["gpt-5.4"]
    assert exc_info.value.status_code == 403


def test_fallback_skipped_when_preferred_equals_default() -> None:
    """When model= matches settings.model, no retry (avoids pointless duplicate call)."""
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(body["model"])
        return 403, _model_access_error_payload()

    svc = OpenAIService(_make_bootstrap(default_model="gpt-4o"), transport=transport)
    with pytest.raises(OpenAIAPIError) as exc_info:
        svc.complete_messages(
            user_prompt="test",
            model="gpt-4o",
            fallback_to_default=True,
        )

    assert calls == ["gpt-4o"]
    assert exc_info.value.status_code == 403


def test_fallback_not_triggered_when_no_explicit_model() -> None:
    """When model=None (default path), 403 raises immediately regardless of flag."""
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(body["model"])
        return 403, _model_access_error_payload()

    svc = OpenAIService(_make_bootstrap(default_model="gpt-4o"), transport=transport)
    with pytest.raises(OpenAIAPIError) as exc_info:
        svc.complete_messages(
            user_prompt="test",
            model=None,
            fallback_to_default=True,
        )

    assert calls == ["gpt-4o"]
    assert exc_info.value.status_code == 403
