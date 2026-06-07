"""
Tests for activation-readiness checks and config validation guardrails.

Covers:
  - OpenAI enabled with incomplete config → not ready
  - Airtable enabled with incomplete config → not ready
  - Telegram enabled with incomplete config → not ready
  - Telegram enabled without allowed IDs → security guardrail fires
  - Mixed partial activation states handled safely
  - All disabled → fully_ready (no blocking)
  - All enabled and fully configured → fully_ready
  - Settings.validate() raises on misconfigured enabled integrations
  - to_report() shape is stable
"""
from __future__ import annotations

import pytest

from operator_core.activation import (
    ActivationReadiness,
    check_activation_readiness,
)
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

def _app() -> AppSettings:
    return AppSettings(
        env="test",
        log_level="WARNING",
        runtime_mode="service",
        active_project="everydayengel",
    )


def _telegram(
    *,
    enabled: bool = False,
    bot_token: str = "",
    allowed_user_ids: tuple[str, ...] = (),
    allowed_chat_ids: tuple[str, ...] = (),
) -> TelegramSettings:
    return TelegramSettings(
        enabled=enabled,
        bot_token=bot_token,
        allowed_user_ids=allowed_user_ids,
        allowed_chat_ids=allowed_chat_ids,
    )


def _airtable(
    *,
    enabled: bool = False,
    api_key: str = "",
    base_ids: dict[str, str] | None = None,
) -> AirtableSettings:
    return AirtableSettings(
        enabled=enabled,
        api_key=api_key,
        project_base_ids=base_ids or {},
    )


def _openai(
    *,
    enabled: bool = False,
    api_key: str = "",
    model: str = "",
    base_url: str = "https://api.openai.com/v1",
    timeout_seconds: int = 30,
) -> OpenAISettings:
    return OpenAISettings(
        enabled=enabled,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )


def _settings(
    *,
    telegram: TelegramSettings | None = None,
    airtable: AirtableSettings | None = None,
    openai: OpenAISettings | None = None,
) -> Settings:
    return Settings(
        app=_app(),
        telegram=telegram or _telegram(),
        airtable=airtable or _airtable(),
        openai=openai or _openai(),
    )


# ---------------------------------------------------------------------------
# All disabled → fully_ready (nothing is blocking)
# ---------------------------------------------------------------------------

def test_all_disabled_is_fully_ready() -> None:
    settings = _settings()
    result = check_activation_readiness(settings)

    assert result.fully_ready is True
    assert result.blocking_issues == ()


def test_all_disabled_integrations_are_not_enabled() -> None:
    settings = _settings()
    result = check_activation_readiness(settings)

    assert result.telegram.enabled is False
    assert result.airtable.enabled is False
    assert result.openai.enabled is False


# ---------------------------------------------------------------------------
# OpenAI: enabled but misconfigured → blocking
# ---------------------------------------------------------------------------

def test_openai_enabled_missing_api_key_is_blocking() -> None:
    settings = _settings(
        openai=_openai(enabled=True, api_key="", model="gpt-4o")
    )
    result = check_activation_readiness(settings)

    assert result.openai.enabled is True
    assert result.openai.ready is False
    assert result.openai.blocking is True
    assert any("api_key" in issue for issue in result.openai.issues)
    assert result.fully_ready is False


def test_openai_enabled_missing_model_is_blocking() -> None:
    settings = _settings(
        openai=_openai(enabled=True, api_key="sk-test", model="")
    )
    result = check_activation_readiness(settings)

    assert result.openai.blocking is True
    assert any("model" in issue for issue in result.openai.issues)


def test_openai_enabled_missing_base_url_is_blocking() -> None:
    settings = _settings(
        openai=_openai(enabled=True, api_key="sk-test", model="gpt-4o", base_url="")
    )
    result = check_activation_readiness(settings)

    assert result.openai.blocking is True
    assert any("base_url" in issue for issue in result.openai.issues)


def test_openai_enabled_bad_timeout_is_blocking() -> None:
    settings = _settings(
        openai=_openai(enabled=True, api_key="sk-test", model="gpt-4o", timeout_seconds=0)
    )
    result = check_activation_readiness(settings)

    assert result.openai.blocking is True
    assert any("timeout" in issue for issue in result.openai.issues)


def test_openai_fully_configured_is_ready() -> None:
    settings = _settings(
        openai=_openai(enabled=True, api_key="sk-test", model="gpt-4o")
    )
    result = check_activation_readiness(settings)

    assert result.openai.enabled is True
    assert result.openai.ready is True
    assert result.openai.blocking is False
    assert result.openai.issues == ()


# ---------------------------------------------------------------------------
# Airtable: enabled but misconfigured → blocking
# ---------------------------------------------------------------------------

def test_airtable_enabled_missing_api_key_is_blocking() -> None:
    settings = _settings(
        airtable=_airtable(
            enabled=True,
            api_key="",
            base_ids={"everydayengel": "appTestBase"},
        )
    )
    result = check_activation_readiness(settings)

    assert result.airtable.blocking is True
    assert any("api_key" in issue for issue in result.airtable.issues)
    assert result.fully_ready is False


def test_airtable_enabled_missing_base_id_is_blocking() -> None:
    settings = _settings(
        airtable=_airtable(
            enabled=True,
            api_key="pat-test",
            base_ids={},  # no base ID for everydayengel
        )
    )
    result = check_activation_readiness(settings)

    assert result.airtable.blocking is True
    assert any("base_id" in issue for issue in result.airtable.issues)


def test_airtable_enabled_wrong_project_base_id_is_blocking() -> None:
    """Base ID exists but for a different project key."""
    settings = _settings(
        airtable=_airtable(
            enabled=True,
            api_key="pat-test",
            base_ids={"otherproject": "appOther"},  # not everydayengel
        )
    )
    result = check_activation_readiness(settings)

    assert result.airtable.blocking is True


def test_airtable_fully_configured_is_ready() -> None:
    settings = _settings(
        airtable=_airtable(
            enabled=True,
            api_key="pat-test",
            base_ids={"everydayengel": "appTestBase"},
        )
    )
    result = check_activation_readiness(settings)

    assert result.airtable.enabled is True
    assert result.airtable.ready is True
    assert result.airtable.blocking is False


# ---------------------------------------------------------------------------
# Telegram: enabled but misconfigured → blocking
# ---------------------------------------------------------------------------

def test_telegram_enabled_missing_bot_token_is_blocking() -> None:
    settings = _settings(
        telegram=_telegram(
            enabled=True,
            bot_token="",
            allowed_user_ids=("123",),
        )
    )
    result = check_activation_readiness(settings)

    assert result.telegram.blocking is True
    assert any("bot_token" in issue for issue in result.telegram.issues)
    assert result.fully_ready is False


def test_telegram_enabled_no_allowed_ids_is_blocking() -> None:
    """Security guardrail: Telegram enabled with empty allowed ID lists must be blocking."""
    settings = _settings(
        telegram=_telegram(
            enabled=True,
            bot_token="123:abc",
            allowed_user_ids=(),
            allowed_chat_ids=(),
        )
    )
    result = check_activation_readiness(settings)

    assert result.telegram.blocking is True
    assert any("allowed" in issue.lower() for issue in result.telegram.issues)


def test_telegram_enabled_with_user_ids_only_is_ready() -> None:
    settings = _settings(
        telegram=_telegram(
            enabled=True,
            bot_token="123:abc",
            allowed_user_ids=("456",),
            allowed_chat_ids=(),
        )
    )
    result = check_activation_readiness(settings)

    assert result.telegram.ready is True
    assert result.telegram.blocking is False


def test_telegram_enabled_with_chat_ids_only_is_ready() -> None:
    settings = _settings(
        telegram=_telegram(
            enabled=True,
            bot_token="123:abc",
            allowed_user_ids=(),
            allowed_chat_ids=("-100123456789",),
        )
    )
    result = check_activation_readiness(settings)

    assert result.telegram.ready is True
    assert result.telegram.blocking is False


# ---------------------------------------------------------------------------
# Mixed partial activation states
# ---------------------------------------------------------------------------

def test_openai_ready_airtable_blocking_not_fully_ready() -> None:
    settings = _settings(
        openai=_openai(enabled=True, api_key="sk-test", model="gpt-4o"),
        airtable=_airtable(enabled=True, api_key="", base_ids={}),
    )
    result = check_activation_readiness(settings)

    assert result.openai.ready is True
    assert result.airtable.blocking is True
    assert result.fully_ready is False
    assert len(result.blocking_issues) >= 1
    assert all("[airtable]" in issue for issue in result.blocking_issues)


def test_all_enabled_and_fully_configured_is_fully_ready() -> None:
    settings = _settings(
        telegram=_telegram(
            enabled=True,
            bot_token="123:abc",
            allowed_user_ids=("456",),
        ),
        airtable=_airtable(
            enabled=True,
            api_key="pat-test",
            base_ids={"everydayengel": "appTestBase"},
        ),
        openai=_openai(enabled=True, api_key="sk-test", model="gpt-4o"),
    )
    result = check_activation_readiness(settings)

    assert result.fully_ready is True
    assert result.blocking_issues == ()
    assert result.telegram.ready is True
    assert result.airtable.ready is True
    assert result.openai.ready is True


def test_blocking_issues_prefixed_with_integration_name() -> None:
    settings = _settings(
        openai=_openai(enabled=True, api_key="", model=""),
        airtable=_airtable(enabled=True, api_key="", base_ids={}),
    )
    result = check_activation_readiness(settings)

    issue_prefixes = {issue.split("]")[0].lstrip("[") for issue in result.blocking_issues}
    assert "openai" in issue_prefixes
    assert "airtable" in issue_prefixes


# ---------------------------------------------------------------------------
# to_report() shape
# ---------------------------------------------------------------------------

def test_to_report_shape_is_stable() -> None:
    settings = _settings(
        openai=_openai(enabled=True, api_key="sk-test", model="gpt-4o"),
    )
    result = check_activation_readiness(settings)
    report = result.to_report()

    assert report["project_key"] == "everydayengel"
    assert isinstance(report["fully_ready"], bool)
    assert isinstance(report["blocking_issues"], list)
    assert "telegram" in report
    assert "airtable" in report
    assert "openai" in report

    for name in ("telegram", "airtable", "openai"):
        section = report[name]
        assert isinstance(section, dict)
        assert "enabled" in section
        assert "ready" in section
        assert "issues" in section


# ---------------------------------------------------------------------------
# Settings.validate() — security guardrail for Telegram allowed IDs
# ---------------------------------------------------------------------------

def test_settings_validate_raises_when_telegram_has_no_allowed_ids() -> None:
    settings = _settings(
        telegram=_telegram(
            enabled=True,
            bot_token="123:abc",
            allowed_user_ids=(),
            allowed_chat_ids=(),
        )
    )
    with pytest.raises(ValueError, match="ALLOWED_TELEGRAM"):
        settings.validate()


def test_settings_validate_passes_when_telegram_has_allowed_user_ids() -> None:
    settings = _settings(
        telegram=_telegram(
            enabled=True,
            bot_token="123:abc",
            allowed_user_ids=("789",),
        )
    )
    # Must not raise
    settings.validate()


def test_settings_validate_raises_when_openai_missing_api_key() -> None:
    settings = _settings(
        openai=_openai(enabled=True, api_key="", model="gpt-4o")
    )
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        settings.validate()


def test_settings_validate_raises_when_airtable_missing_base_id() -> None:
    settings = _settings(
        airtable=_airtable(
            enabled=True,
            api_key="pat-test",
            base_ids={},
        )
    )
    with pytest.raises(ValueError, match="base ID"):
        settings.validate()
