"""
Tests for startup activation-readiness logging in app.py.

Only covers _log_activation_readiness() — the logging helper wired into main().
Does not start the runtime or make any live calls.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from operator_core.app import _log_activation_readiness
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(
    *,
    telegram_enabled: bool = False,
    telegram_token: str = "",
    telegram_user_ids: tuple[str, ...] = (),
    airtable_enabled: bool = False,
    airtable_key: str = "",
    airtable_base_ids: dict[str, str] | None = None,
    openai_enabled: bool = False,
    openai_key: str = "",
    openai_model: str = "",
) -> Settings:
    return Settings(
        app=AppSettings(
            env="test",
            log_level="DEBUG",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(
            enabled=telegram_enabled,
            bot_token=telegram_token,
            allowed_user_ids=telegram_user_ids,
            allowed_chat_ids=(),
        ),
        airtable=AirtableSettings(
            enabled=airtable_enabled,
            api_key=airtable_key,
            project_base_ids=airtable_base_ids or {},
        ),
        openai=OpenAISettings(
            enabled=openai_enabled,
            api_key=openai_key,
            model=openai_model,
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )


class _CapturingLogger:
    """Minimal logger stand-in that captures calls by level."""

    def __init__(self) -> None:
        self.debug_calls: list[str] = []
        self.info_calls: list[str] = []
        self.warning_calls: list[str] = []

    def _fmt(self, msg: str, *args: Any) -> str:
        try:
            return msg % args
        except (TypeError, ValueError):
            return msg

    def debug(self, msg: str, *args: Any, **_kw: Any) -> None:
        self.debug_calls.append(self._fmt(msg, *args))

    def info(self, msg: str, *args: Any, **_kw: Any) -> None:
        self.info_calls.append(self._fmt(msg, *args))

    def warning(self, msg: str, *args: Any, **_kw: Any) -> None:
        self.warning_calls.append(self._fmt(msg, *args))

    @property
    def all_calls(self) -> list[str]:
        return self.debug_calls + self.info_calls + self.warning_calls


# ---------------------------------------------------------------------------
# Tests: all integrations disabled
# ---------------------------------------------------------------------------

def test_all_disabled_logs_debug_for_each_integration() -> None:
    log = _CapturingLogger()
    _log_activation_readiness(log, _settings())  # type: ignore[arg-type]

    assert len(log.debug_calls) == 3
    names = {line.split("integration=")[1].split(" ")[0] for line in log.debug_calls}
    assert names == {"telegram", "airtable", "openai"}


def test_all_disabled_logs_overall_ready_at_info() -> None:
    log = _CapturingLogger()
    _log_activation_readiness(log, _settings())  # type: ignore[arg-type]

    assert any("overall=ready" in line for line in log.info_calls)
    assert not log.warning_calls


# ---------------------------------------------------------------------------
# Tests: fully configured integrations → INFO only
# ---------------------------------------------------------------------------

def test_fully_configured_logs_info_for_ready_integrations() -> None:
    log = _CapturingLogger()
    settings = _settings(
        openai_enabled=True,
        openai_key="sk-test",
        openai_model="gpt-4o",
        airtable_enabled=True,
        airtable_key="pat-test",
        airtable_base_ids={"everydayengel": "appBase"},
    )
    _log_activation_readiness(log, settings)  # type: ignore[arg-type]

    assert any("openai" in line and "ready=true" in line for line in log.info_calls)
    assert any("airtable" in line and "ready=true" in line for line in log.info_calls)
    assert any("overall=ready" in line for line in log.info_calls)
    assert not log.warning_calls


# ---------------------------------------------------------------------------
# Tests: misconfigured enabled integrations → WARNING
# ---------------------------------------------------------------------------

def test_openai_missing_key_logs_warning() -> None:
    log = _CapturingLogger()
    settings = _settings(openai_enabled=True, openai_key="", openai_model="gpt-4o")
    _log_activation_readiness(log, settings)  # type: ignore[arg-type]

    assert any("openai" in line and "ready=false" in line for line in log.warning_calls)
    assert any("api_key" in line for line in log.warning_calls)


def test_airtable_missing_base_id_logs_warning() -> None:
    log = _CapturingLogger()
    settings = _settings(
        airtable_enabled=True,
        airtable_key="pat-test",
        airtable_base_ids={},  # no base ID for everydayengel
    )
    _log_activation_readiness(log, settings)  # type: ignore[arg-type]

    assert any("airtable" in line and "ready=false" in line for line in log.warning_calls)
    assert any("base_id" in line for line in log.warning_calls)


def test_telegram_no_allowed_ids_logs_warning() -> None:
    log = _CapturingLogger()
    settings = _settings(
        telegram_enabled=True,
        telegram_token="123:abc",
        telegram_user_ids=(),
    )
    _log_activation_readiness(log, settings)  # type: ignore[arg-type]

    assert any("telegram" in line and "ready=false" in line for line in log.warning_calls)


def test_blocking_overall_logs_warning_not_ready() -> None:
    log = _CapturingLogger()
    settings = _settings(openai_enabled=True, openai_key="", openai_model="gpt-4o")
    _log_activation_readiness(log, settings)  # type: ignore[arg-type]

    assert any("overall=not_ready" in line for line in log.warning_calls)
    assert not any("overall=ready" in line for line in log.info_calls)


# ---------------------------------------------------------------------------
# Tests: non-Settings argument is silently ignored
# ---------------------------------------------------------------------------

def test_non_settings_argument_does_not_raise() -> None:
    log = _CapturingLogger()
    _log_activation_readiness(log, object())  # type: ignore[arg-type]
    # Must produce no output and not raise
    assert log.all_calls == []


# ---------------------------------------------------------------------------
# Tests: no secrets logged
# ---------------------------------------------------------------------------

def test_api_key_values_not_logged() -> None:
    log = _CapturingLogger()
    settings = _settings(
        openai_enabled=True,
        openai_key="sk-super-secret-key",
        openai_model="gpt-4o",
    )
    _log_activation_readiness(log, settings)  # type: ignore[arg-type]

    for line in log.all_calls:
        assert "sk-super-secret-key" not in line


def test_bot_token_value_not_logged() -> None:
    log = _CapturingLogger()
    settings = _settings(
        telegram_enabled=True,
        telegram_token="123456:ABCdef-secret",
        telegram_user_ids=("789",),
    )
    _log_activation_readiness(log, settings)  # type: ignore[arg-type]

    for line in log.all_calls:
        assert "ABCdef-secret" not in line
