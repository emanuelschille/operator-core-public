"""
Activation-readiness check for operator-core.

Returns a structured, config-driven readiness report without making any live
calls.  Use this to verify that all required config is in place before
enabling live polling or integrations.
"""
from __future__ import annotations

from dataclasses import dataclass

from operator_core.config import Settings


@dataclass(frozen=True)
class IntegrationReadiness:
    name: str
    enabled: bool
    ready: bool
    issues: tuple[str, ...]

    @property
    def blocking(self) -> bool:
        """True when the integration is enabled but not ready (misconfigured)."""
        return self.enabled and not self.ready


@dataclass(frozen=True)
class ActivationReadiness:
    project_key: str
    telegram: IntegrationReadiness
    airtable: IntegrationReadiness
    openai: IntegrationReadiness

    @property
    def fully_ready(self) -> bool:
        """True when no enabled integration has a blocking issue."""
        return not any(
            i.blocking for i in (self.telegram, self.airtable, self.openai)
        )

    @property
    def blocking_issues(self) -> tuple[str, ...]:
        """All blocking issues across integrations, prefixed with integration name."""
        issues: list[str] = []
        for integration in (self.telegram, self.airtable, self.openai):
            if integration.blocking:
                for issue in integration.issues:
                    issues.append(f"[{integration.name}] {issue}")
        return tuple(issues)

    def to_report(self) -> dict[str, object]:
        return {
            "project_key": self.project_key,
            "fully_ready": self.fully_ready,
            "blocking_issues": list(self.blocking_issues),
            "telegram": {
                "enabled": self.telegram.enabled,
                "ready": self.telegram.ready,
                "issues": list(self.telegram.issues),
            },
            "airtable": {
                "enabled": self.airtable.enabled,
                "ready": self.airtable.ready,
                "issues": list(self.airtable.issues),
            },
            "openai": {
                "enabled": self.openai.enabled,
                "ready": self.openai.ready,
                "issues": list(self.openai.issues),
            },
        }


def check_activation_readiness(settings: Settings) -> ActivationReadiness:
    """Return a structured readiness report based on current config.

    No live calls are made — purely config-driven.
    """
    return ActivationReadiness(
        project_key=settings.active_project,
        telegram=_check_telegram(settings),
        airtable=_check_airtable(settings),
        openai=_check_openai(settings),
    )


def _check_telegram(settings: Settings) -> IntegrationReadiness:
    if not settings.telegram.enabled:
        return IntegrationReadiness(
            name="telegram", enabled=False, ready=False, issues=()
        )

    issues: list[str] = []

    if not settings.telegram.bot_token:
        issues.append("bot_token is missing")

    if (
        not settings.telegram.allowed_user_ids
        and not settings.telegram.allowed_chat_ids
    ):
        issues.append(
            "allowed_user_ids and allowed_chat_ids are both empty "
            "— bot would accept messages from any sender"
        )

    return IntegrationReadiness(
        name="telegram",
        enabled=True,
        ready=len(issues) == 0,
        issues=tuple(issues),
    )


def _check_airtable(settings: Settings) -> IntegrationReadiness:
    if not settings.airtable.enabled:
        return IntegrationReadiness(
            name="airtable", enabled=False, ready=False, issues=()
        )

    issues: list[str] = []

    if not settings.airtable.api_key:
        issues.append("api_key is missing")

    if not settings.airtable.get_base_id(settings.active_project):
        issues.append(
            f"base_id for project '{settings.active_project}' is missing"
        )

    return IntegrationReadiness(
        name="airtable",
        enabled=True,
        ready=len(issues) == 0,
        issues=tuple(issues),
    )


def _check_openai(settings: Settings) -> IntegrationReadiness:
    if not settings.openai.enabled:
        return IntegrationReadiness(
            name="openai", enabled=False, ready=False, issues=()
        )

    issues: list[str] = []

    if not settings.openai.api_key:
        issues.append("api_key is missing")

    if not settings.openai.model:
        issues.append("model is missing")

    if not settings.openai.base_url:
        issues.append("base_url is missing")

    if settings.openai.timeout_seconds <= 0:
        issues.append("timeout_seconds must be > 0")

    return IntegrationReadiness(
        name="openai",
        enabled=True,
        ready=len(issues) == 0,
        issues=tuple(issues),
    )
