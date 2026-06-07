"""
Tests for the content_ops hook live path:
  - without OpenAI/Airtable → docs-only fallback (unchanged)
  - with fake OpenAI → generated hook path
  - with fake Airtable → record creation in Content Hooks table
  - with both → combined success path
  - OpenAI error → graceful fallback to docs-only
  - Airtable error → result returned without record_id
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
from operator_core.core.analysis_foundation.models import (
    AnalysisFoundationResult,
    AnalysisSnapshot,
    EvidencePack,
    ModelExecutionMeta,
    WriterBrief,
)
from operator_core.core.content_ops.models import ContentOpResult, FoundationHookResult
from operator_core.core.content_ops.service import ContentOpsService
from operator_core.integrations.airtable_service import AirtableService
from operator_core.integrations.openai_service import (
    OpenAIService,
    OpenAITransportError,
)
from operator_core.projects.docs import ProjectDocsLoader

# ---------------------------------------------------------------------------
# Fake response
# ---------------------------------------------------------------------------

_HOOK_RESPONSE = (
    "Hook-Typ: Neugier\n"
    "Eröffnung: Was wäre wenn dein Morgen entspannter wäre ohne früher aufzustehen?\n"
    "Versprechen: Julia zeigt eine 10-Minuten-Routine die wirklich funktioniert\n"
    "Format: Direct-to-camera\n"
    "Stärke-Check: Hook öffnet mit Neugier, Versprechen klar und konkret"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bootstrap(
    *,
    openai_enabled: bool = True,
    airtable_enabled: bool = True,
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
            api_key="pat-test" if airtable_enabled else "",
            project_base_ids={"everydayengel": "appTestBase123"},
        ),
        openai=OpenAISettings(
            enabled=openai_enabled,
            api_key="sk-test" if openai_enabled else "",
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
        "output": [{"content": [{"type": "output_text", "text": _HOOK_RESPONSE}]}],
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
    return 200, {"id": "recHook001", "fields": body.get("fields", {}) if body else {}}


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


def _make_foundation_result() -> AnalysisFoundationResult:
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-test",
        task_role="analysis_control",
        status="prepared",
    )
    return AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=(
            AnalysisSnapshot(
                snapshot_id="as_platform",
                project_key="everydayengel",
                scope="platform",
                created_at="2026-04-13T10:00:00+00:00",
                title="TikTok analysis snapshot",
                summary="TikTok snapshot for Monday: 20:06",
                platform_key="tiktok",
                analytics_summary_lines=("Dominant CTA: save/share", "Gap signal: more routine honesty"),
                rule_summary_lines=("Audience: Frauen 23-38",),
                source_refs=("docs:project-state", "analytics:platform_signal:tiktok"),
            ),
            AnalysisSnapshot(
                snapshot_id="as_cross",
                project_key="everydayengel",
                scope="cross_platform",
                created_at="2026-04-13T10:00:00+00:00",
                title="Cross-platform analysis snapshot",
                summary="Cross-platform snapshot",
                analytics_summary_lines=("Cross-platform gap: practical routine content",),
                rule_summary_lines=("Avoid: generic platitudes",),
                source_refs=("analytics:global_recent",),
            ),
        ),
        writer_brief=WriterBrief(
            brief_id="wb_hook",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Turn explicit analysis into a writer-ready brief for platform-specific short-form output.",
            audience="Frauen 23-38",
            constraints=("Use analysis snapshots as the primary grounding layer.", "Keep output traceable to explicit evidence."),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-test",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_hook_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("analytics:global_recent",),
            evidence_lines=("TikTok: 20:06",),
        ),
        execution_meta=execution_meta,
    )


# ---------------------------------------------------------------------------
# Tests: docs-only (no OpenAI) — existing behaviour preserved
# ---------------------------------------------------------------------------

def test_hook_without_openai_returns_docs_fallback(docs_loader: ProjectDocsLoader) -> None:
    service = ContentOpsService(docs_loader=docs_loader)

    result = service.handle(
        project_key="everydayengel",
        action_type="hook",
        command_body="morgenroutine hook",
    )

    assert result.lane_name == "content_ops"
    assert result.action_type == "hook"
    assert result.summary == "Hook-Regeln geladen."
    assert result.openai_used is False
    assert result.airtable_record_id is None
    assert any("Hook" in item or "Ton" in item for item in result.items)


# ---------------------------------------------------------------------------
# Tests: hook with OpenAI only (no Airtable)
# ---------------------------------------------------------------------------

def test_hook_with_openai_returns_generated_result(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="hook",
        command_body="morgenroutine ohne stress",
    )

    assert result.lane_name == "content_ops"
    assert result.action_type == "hook"
    assert result.summary == "Hook generiert."
    assert result.openai_used is True
    assert result.airtable_record_id is None
    assert len(result.items) >= 1
    assert any("Hook-Typ" in item for item in result.items)
    assert any("Eröffnung" in item for item in result.items)


def test_hook_with_openai_snapshot_is_correct(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="hook",
        command_body="",
    )

    snapshot = result.to_snapshot()
    assert snapshot["openai_used"] is True
    assert snapshot["airtable_record_id"] is None
    assert snapshot["action_type"] == "hook"
    assert snapshot["lane_name"] == "content_ops"


def test_hook_with_openai_parses_all_keys(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="hook",
        command_body="test",
    )

    item_keys = {item.split(":")[0] for item in result.items}
    assert "Hook-Typ" in item_keys
    assert "Eröffnung" in item_keys
    assert "Versprechen" in item_keys
    assert "Format" in item_keys


# ---------------------------------------------------------------------------
# Tests: hook with both OpenAI and Airtable
# ---------------------------------------------------------------------------

def test_hook_with_openai_and_airtable_creates_record(docs_loader: ProjectDocsLoader) -> None:
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
        action_type="hook",
        command_body="morgenroutine",
    )

    assert result.summary == "Hook generiert."
    assert result.openai_used is True
    assert result.airtable_record_id == "recHook001"

    snapshot = result.to_snapshot()
    assert snapshot["airtable_record_id"] == "recHook001"


def test_foundation_backed_hook_uses_writer_brief_and_selected_snapshots(
    docs_loader: ProjectDocsLoader,
) -> None:
    captured_system_prompt: dict[str, str] = {}

    def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout: int,
    ) -> tuple[int, dict[str, Any]]:
        assert body is not None
        captured_system_prompt["text"] = body["input"][0]["content"][0]["text"]
        return _openai_transport_ok(method, url, headers, body, timeout)

    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=transport)
    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
    )

    result = service.generate_hook_from_foundation(
        project_key="everydayengel",
        command_body="tiktok morgenroutine hook",
        foundation_result=_make_foundation_result(),
    )

    assert isinstance(result, FoundationHookResult)
    assert result.content_result.action_type == "hook"
    assert result.content_result.writer_brief_id == "wb_hook"
    assert result.content_result.foundation_snapshot_ids == ("as_platform", "as_cross")
    prompt = captured_system_prompt["text"]
    assert "Writer-Brief (bindend)" in prompt
    assert "Hook-Kontext (bindend)" in prompt
    assert "TikTok analysis snapshot" in prompt
    assert "Cross-platform analysis snapshot" in prompt
    assert "Use analysis snapshots as the primary grounding layer." in prompt


def test_build_hook_evidence_pack_links_snapshots_and_output(
    docs_loader: ProjectDocsLoader,
) -> None:
    service = ContentOpsService(docs_loader=docs_loader)
    foundation = _make_foundation_result()
    hook_result = FoundationHookResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="hook",
            command_body="morgenroutine",
            title="Content hook",
            summary="Hook generiert.",
            items=(
                "Hook-Typ: Neugier",
                "Eröffnung: Was wäre wenn dein Morgen entspannter wäre?",
                "Versprechen: Julia zeigt eine 10-Minuten-Routine.",
                "Format: Direct-to-camera",
            ),
            openai_used=True,
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_hook",
        ),
        selected_snapshots=foundation.analysis_snapshots[:2],
        writer_brief=foundation.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-test",
            task_role="writer",
            status="completed",
        ),
    )

    evidence_pack = service.build_hook_evidence_pack(
        project_key="everydayengel",
        hook_result=hook_result,
    )

    assert evidence_pack.snapshot_ids == ("as_platform", "as_cross")
    assert "docs:project-state" in evidence_pack.source_refs
    assert any(
        line.startswith("Hook output: Hook-Typ: Neugier")
        for line in evidence_pack.evidence_lines
    )


def test_hook_airtable_record_has_correct_fields(docs_loader: ProjectDocsLoader) -> None:
    """Verify stage and created_by are written correctly to the Airtable record."""
    captured: dict[str, Any] = {}

    def transport_capture(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and body:
            captured.update(body)
        return 200, {"id": "recHook002", "fields": {}}

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
        action_type="hook",
        command_body="test",
    )

    fields = captured.get("fields", {})
    assert fields.get("stage") == "raw_idea"
    assert fields.get("project_key") == "everydayengel"
    assert fields.get("created_by") == "operator_core"
    assert "hook_type" in fields


# ---------------------------------------------------------------------------
# Tests: OpenAI error → fallback to docs-only
# ---------------------------------------------------------------------------

def test_hook_openai_error_falls_back_to_docs(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_error)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="hook",
        command_body="any kontext",
    )

    assert result.summary == "Hook-Regeln geladen."
    assert result.openai_used is False
    assert result.airtable_record_id is None
    assert len(result.items) == 3


def test_hook_openai_error_snapshot_is_clean(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_error)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="hook",
        command_body="",
    )

    snapshot = result.to_snapshot()
    assert snapshot["openai_used"] is False
    assert snapshot["airtable_record_id"] is None
    assert snapshot["action_type"] == "hook"


# ---------------------------------------------------------------------------
# Tests: Airtable error → result without record_id
# ---------------------------------------------------------------------------

def test_hook_airtable_error_returns_result_without_record_id(
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
        action_type="hook",
        command_body="test",
    )

    assert result.summary == "Hook generiert."
    assert result.openai_used is True
    assert result.airtable_record_id is None


# ---------------------------------------------------------------------------
# Regression: hook activation must not change idea or draft behaviour
# ---------------------------------------------------------------------------

def test_idea_action_unaffected_by_hook_activation(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="idea",
        command_body="test",
    )

    assert result.action_type == "idea"
    assert result.openai_used is True


def test_draft_action_unaffected_by_hook_activation(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="test",
    )

    assert result.action_type == "draft"
    assert result.openai_used is True
