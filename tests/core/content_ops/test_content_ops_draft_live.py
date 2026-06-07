"""
Tests for the content_ops draft live path:
  - with fake OpenAI → structured draft generated from project docs
  - with fake Airtable → record creation triggered in Content Drafts table
  - with both → combined success path
  - OpenAI error → graceful fallback to docs-only result
  - no integrations → docs-only path (unchanged from previous activation)
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
from operator_core.integrations.openai_service import (
    OpenAIService,
    OpenAITransportError,
)
from operator_core.projects.docs import ProjectDocsLoader

# ---------------------------------------------------------------------------
# Fake response fixture
# ---------------------------------------------------------------------------

_DRAFT_RESPONSE = (
    "Hauptpunkt: Julia zeigt ihre 10-Minuten-Morgenroutine ohne Stress\n"
    "Hook: Was wäre wenn dein Morgen entspannter wäre ohne früher aufzustehen?\n"
    "Body: 1. Kein Handy in den ersten 5 Minuten. 2. Ein Glas Wasser zuerst. 3. Eine Sache erledigen.\n"
    "CTA-Richtung: Soft – Kommentar zu eigener Morgenroutine einladen\n"
    "Format: Direct-to-camera\n"
    "Bereit-Check: Hauptpunkt klar, Hook stark, Format passend für Reels"
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_bootstrap(
    *,
    openai_enabled: bool = True,
    openai_api_key: str = "sk-test",
    airtable_enabled: bool = True,
    airtable_api_key: str = "pat-test",
) -> BootstrapContext:
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
            enabled=airtable_enabled,
            api_key=airtable_api_key,
            project_base_ids={"everydayengel": "appTestBase123"},
        ),
        openai=OpenAISettings(
            enabled=openai_enabled,
            api_key=openai_api_key,
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


def _openai_transport_ok(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    return 200, {
        "model": "gpt-test",
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": _DRAFT_RESPONSE}
                ]
            }
        ],
    }


def _openai_transport_error(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    raise OpenAITransportError("connection refused (test)")


def _airtable_transport_ok(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    return 200, {"id": "recDraft001", "fields": body.get("fields", {}) if body else {}}


def _airtable_transport_error(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    return 422, {"error": {"message": "table not found"}}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def docs_loader() -> ProjectDocsLoader:
    return ProjectDocsLoader()


# ---------------------------------------------------------------------------
# Tests: docs-only path (no OpenAI) — existing behaviour must remain intact
# ---------------------------------------------------------------------------

def test_draft_without_openai_returns_docs_fallback(docs_loader: ProjectDocsLoader) -> None:
    service = ContentOpsService(docs_loader=docs_loader)

    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="morgenroutine entwurf",
    )

    assert result.lane_name == "content_ops"
    assert result.action_type == "draft"
    assert result.summary == "Draft-Kontext geladen."
    assert result.openai_used is False
    assert result.airtable_record_id is None
    assert any("Produktionsreife" in item or "Richtung" in item for item in result.items)


# ---------------------------------------------------------------------------
# Tests: draft with OpenAI only (no Airtable)
# ---------------------------------------------------------------------------

def test_draft_with_openai_returns_generated_result(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="morgenroutine ohne stress",
    )

    assert result.lane_name == "content_ops"
    assert result.action_type == "draft"
    assert result.summary == "Entwurf generiert."
    assert result.openai_used is True
    assert result.airtable_record_id is None
    assert len(result.items) >= 1
    assert any("Hauptpunkt" in item for item in result.items)
    assert any("Hook" in item for item in result.items)


def test_draft_with_openai_snapshot_includes_openai_used(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="",
    )

    snapshot = result.to_snapshot()
    assert snapshot["openai_used"] is True
    assert snapshot["airtable_record_id"] is None
    assert snapshot["action_type"] == "draft"
    assert snapshot["lane_name"] == "content_ops"


def test_draft_with_openai_parses_all_keys(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="test",
    )

    item_keys = {item.split(":")[0] for item in result.items}
    assert "Hauptpunkt" in item_keys
    assert "Hook" in item_keys
    assert "Format" in item_keys


# ---------------------------------------------------------------------------
# Tests: draft with both OpenAI and Airtable
# ---------------------------------------------------------------------------

def test_draft_with_openai_and_airtable_creates_record(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    airtable_svc = AirtableService(ctx, transport=_airtable_transport_ok)
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        airtable_service=airtable_svc,
    )

    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="morgenroutine",
    )

    assert result.summary == "Entwurf generiert."
    assert result.openai_used is True
    assert result.airtable_record_id == "recDraft001"

    snapshot = result.to_snapshot()
    assert snapshot["airtable_record_id"] == "recDraft001"


def test_draft_airtable_record_has_drafted_stage(docs_loader: ProjectDocsLoader) -> None:
    """Verify the stage field written to Airtable is 'drafted', not 'raw_idea'."""
    captured: dict[str, Any] = {}

    def transport_capture(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and body:
            captured.update(body)
        return 200, {"id": "recDraft002", "fields": {}}

    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    airtable_svc = AirtableService(ctx, transport=transport_capture)
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        airtable_service=airtable_svc,
    )

    service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="test",
    )

    assert captured.get("fields", {}).get("stage") == "drafted"
    assert captured.get("fields", {}).get("project_key") == "everydayengel"
    assert captured.get("fields", {}).get("created_by") == "operator_core"


# ---------------------------------------------------------------------------
# Tests: OpenAI error → graceful fallback to docs-only
# ---------------------------------------------------------------------------

def test_draft_openai_error_falls_back_to_docs(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_error)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="any kontext",
    )

    assert result.summary == "Draft-Kontext geladen."
    assert result.openai_used is False
    assert result.airtable_record_id is None
    assert len(result.items) == 3


def test_draft_openai_error_snapshot_is_clean(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_error)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="",
    )

    snapshot = result.to_snapshot()
    assert snapshot["openai_used"] is False
    assert snapshot["airtable_record_id"] is None
    assert snapshot["action_type"] == "draft"


# ---------------------------------------------------------------------------
# Tests: Airtable error → idea result returned without record_id
# ---------------------------------------------------------------------------

def test_draft_airtable_error_returns_result_without_record_id(
    docs_loader: ProjectDocsLoader,
) -> None:
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    airtable_svc = AirtableService(ctx, transport=_airtable_transport_error)
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        airtable_service=airtable_svc,
    )

    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="test",
    )

    assert result.summary == "Entwurf generiert."
    assert result.openai_used is True
    assert result.airtable_record_id is None


# ---------------------------------------------------------------------------
# Tests: draft does NOT interfere with idea action
# ---------------------------------------------------------------------------

def test_idea_action_unaffected_by_draft_changes(docs_loader: ProjectDocsLoader) -> None:
    """Regression: draft activation must not change idea path behaviour."""
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    # idea path uses its own OpenAI response — here the transport returns draft text,
    # but the idea parser will still attempt to extract its own keys (Titel/Pillar/etc.)
    result = service.handle(
        project_key="everydayengel",
        action_type="idea",
        command_body="test",
    )

    assert result.action_type == "idea"
    assert result.openai_used is True
