"""
Tests for ContentOpsService live_actions gate.

Verifies that when live_actions is set to a specific allow-list, only those
action types use OpenAI/Airtable — all others fall back to docs-only,
even when openai_service and airtable_service are fully configured.

This is the mechanism used for the first controlled server activation:
  OPERATOR_CONTENT_OPS_LIVE_ACTIONS=idea
  → only idea goes live; draft/hook/caption remain docs-only.
"""
from __future__ import annotations

from typing import Any

import pytest

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.core.content_ops.service import ContentOpsService
from operator_core.integrations.airtable_service import AirtableService
from operator_core.integrations.openai_service import OpenAIService
from operator_core.projects.docs import ProjectDocsLoader


# ---------------------------------------------------------------------------
# Shared fake transports
# ---------------------------------------------------------------------------

_IDEA_TEXT = (
    "Titel: Morgenroutine ohne Stress\n"
    "Pillar: Small routines that improve daily life\n"
    "Angle: Julia zeigt ihre 10-Minuten-Morgenroutine\n"
    "Hook: Was wäre wenn dein Morgen mit nur 10 Minuten besser läuft?\n"
    "Format: Direct-to-camera"
)

_DRAFT_TEXT = (
    "Hauptpunkt: Julia zeigt ihre 10-Minuten-Morgenroutine ohne Stress\n"
    "Hook: Was wäre wenn dein Morgen entspannter wäre?\n"
    "Body: 1. Kein Handy. 2. Ein Glas Wasser. 3. Eine Sache erledigen.\n"
    "CTA-Richtung: Soft\n"
    "Format: Direct-to-camera\n"
    "Bereit-Check: Klar"
)

_HOOK_TEXT = (
    "Hook-Typ: Neugier\n"
    "Eröffnung: Was wäre wenn dein Morgen entspannter wäre?\n"
    "Versprechen: Julia zeigt eine 10-Minuten-Routine\n"
    "Format: Direct-to-camera\n"
    "Stärke-Check: Stark"
)

_CAPTION_TEXT = (
    "Caption: Manchmal braucht der Morgen nur 10 Minuten.\n"
    "CTA-Richtung: Meinung einladen\n"
    "Ton-Check: Natürlich\n"
    "Länge-Check: Kurz"
)


def _openai_transport_ok(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    # Returns idea text for all calls — good enough for gate testing
    return 200, {
        "model": "gpt-test",
        "output": [{"content": [{"type": "output_text", "text": _IDEA_TEXT}]}],
    }


def _airtable_transport_ok(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    return 200, {"id": "recGated001", "fields": {}}


def _make_ctx() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="WARNING",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(
            enabled=False, bot_token="", allowed_user_ids=(), allowed_chat_ids=()
        ),
        airtable=AirtableSettings(
            enabled=True,
            api_key="pat-test",
            project_base_ids={"everydayengel": "appTestBase"},
        ),
        openai=OpenAISettings(
            enabled=True,
            api_key="sk-test",
            model="gpt-test",
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )
    return BootstrapContext(
        settings=settings,
        runtime_path=__import__("pathlib").Path("projects/everydayengel/runtime.yaml"),
        project_runtime={
            "project_key": "everydayengel",
            "display_name": "everydayengel",
            "status": "active",
            "primary_interface": "telegram",
            "human_in_the_loop": "true",
        },
    )


@pytest.fixture()
def docs_loader() -> ProjectDocsLoader:
    return ProjectDocsLoader()


@pytest.fixture()
def openai_svc() -> OpenAIService:
    return OpenAIService(_make_ctx(), transport=_openai_transport_ok)


@pytest.fixture()
def airtable_svc() -> AirtableService:
    return AirtableService(_make_ctx(), transport=_airtable_transport_ok)


# ---------------------------------------------------------------------------
# gate=None (default) — all actions live-capable (existing behaviour)
# ---------------------------------------------------------------------------

def test_gate_none_idea_uses_openai(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        live_actions=None,  # all live
    )
    result = service.handle(
        project_key="everydayengel", action_type="idea", command_body="test"
    )
    assert result.openai_used is True


def test_gate_none_draft_uses_openai(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        live_actions=None,
    )
    result = service.handle(
        project_key="everydayengel", action_type="draft", command_body="test"
    )
    assert result.openai_used is True


# ---------------------------------------------------------------------------
# gate={idea} — only idea is live, others fall back to docs-only
# ---------------------------------------------------------------------------

def test_gate_idea_only_idea_uses_openai(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        live_actions=frozenset({"idea"}),
    )
    result = service.handle(
        project_key="everydayengel", action_type="idea", command_body="test"
    )
    assert result.openai_used is True
    assert result.summary == "Idee generiert."


def test_gate_idea_only_draft_falls_back_to_docs(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        live_actions=frozenset({"idea"}),
    )
    result = service.handle(
        project_key="everydayengel", action_type="draft", command_body="test"
    )
    assert result.openai_used is False
    assert result.summary == "Draft-Kontext geladen."


def test_gate_draft_alias_allows_vollauto_live(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        live_actions=frozenset({"draft"}),
    )
    result = service.handle(
        project_key="everydayengel", action_type="vollauto", command_body="test"
    )
    assert result.openai_used is True
    assert result.summary == "Voll Auto generiert."


def test_gate_idea_only_hook_falls_back_to_docs(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        live_actions=frozenset({"idea"}),
    )
    result = service.handle(
        project_key="everydayengel", action_type="hook", command_body="test"
    )
    assert result.openai_used is False
    assert result.summary == "Hook-Regeln geladen."


def test_gate_idea_only_caption_falls_back_to_docs(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        live_actions=frozenset({"idea"}),
    )
    result = service.handle(
        project_key="everydayengel", action_type="caption", command_body="test"
    )
    assert result.openai_used is False
    assert result.summary == "Caption-Regeln geladen."


# ---------------------------------------------------------------------------
# gate={idea} + Airtable — idea creates record, others do not
# ---------------------------------------------------------------------------

def test_gate_idea_only_idea_creates_airtable_record(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
    airtable_svc: AirtableService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        airtable_service=airtable_svc,
        live_actions=frozenset({"idea"}),
    )
    result = service.handle(
        project_key="everydayengel", action_type="idea", command_body="test"
    )
    assert result.openai_used is True
    assert result.airtable_record_id == "recGated001"


def test_gate_idea_only_draft_no_airtable_record(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
    airtable_svc: AirtableService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        airtable_service=airtable_svc,
        live_actions=frozenset({"idea"}),
    )
    result = service.handle(
        project_key="everydayengel", action_type="draft", command_body="test"
    )
    assert result.openai_used is False
    assert result.airtable_record_id is None


# ---------------------------------------------------------------------------
# gate=frozenset() — empty set blocks all live integrations
# ---------------------------------------------------------------------------

def test_gate_empty_blocks_all_live_integrations(
    docs_loader: ProjectDocsLoader,
    openai_svc: OpenAIService,
) -> None:
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        live_actions=frozenset(),
    )
    for action in ("idea", "draft", "hook", "caption"):
        result = service.handle(
            project_key="everydayengel", action_type=action, command_body="test"
        )
        assert result.openai_used is False, f"{action} should not use OpenAI when gate is empty"


# ---------------------------------------------------------------------------
# config.py: AppSettings.content_ops_live_actions field
# ---------------------------------------------------------------------------

def test_app_settings_content_ops_live_actions_default_is_empty() -> None:
    from operator_core.config import AppSettings

    s = AppSettings(
        env="test",
        log_level="WARNING",
        runtime_mode="service",
        active_project="everydayengel",
    )
    assert s.content_ops_live_actions == ()


def test_app_settings_content_ops_live_actions_set_explicitly() -> None:
    from operator_core.config import AppSettings

    s = AppSettings(
        env="test",
        log_level="WARNING",
        runtime_mode="service",
        active_project="everydayengel",
        content_ops_live_actions=("idea",),
    )
    assert "idea" in s.content_ops_live_actions
    assert "draft" not in s.content_ops_live_actions


# ---------------------------------------------------------------------------
# ExecutionService: live_actions is threaded through to ContentOpsService
# ---------------------------------------------------------------------------

def test_execution_service_passes_live_actions_to_content_ops() -> None:
    """Verify ContentOpsService built inside ExecutionService respects live_actions."""
    from operator_core.core.backbone.execution_service import ExecutionService
    from operator_core.core.backbone.job_service import JobService
    from operator_core.core.backbone.run_service import RunService
    from operator_core.core.backbone.event_log_service import EventLogService
    from operator_core.core.backbone.repositories import (
        InMemoryJobRepository,
        InMemoryRunRepository,
        InMemoryEventRepository,
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()

    ctx = _make_ctx()
    openai = OpenAIService(ctx, transport=_openai_transport_ok)

    exec_svc = ExecutionService(
        job_service=JobService(repository=job_repo),
        run_service=RunService(repository=run_repo),
        event_log_service=EventLogService(repository=event_repo),
        docs_loader=ProjectDocsLoader(),
        openai_service=openai,
        content_ops_live_actions=frozenset({"idea"}),
    )

    # Gate is respected: idea uses live integration, draft does not
    assert exec_svc.content_ops_service._integration_active_for("idea") is True
    assert exec_svc.content_ops_service._integration_active_for("draft") is False
    assert exec_svc.content_ops_service._integration_active_for("hook") is False
    assert exec_svc.content_ops_service._integration_active_for("caption") is False
