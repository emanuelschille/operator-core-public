"""
Tests for the content_ops caption live path:
  - without OpenAI/Airtable → docs-only fallback (unchanged)
  - with fake OpenAI → generated caption path
  - with fake Airtable → record creation in Content Captions table
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
from operator_core.core.content_ops.models import ContentOpResult, FoundationCaptionResult
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

_CAPTION_RESPONSE = (
    "Caption: Manchmal braucht der Morgen nur 10 Minuten um sich besser anzufühlen.\n"
    "CTA-Richtung: Meinung – Kommentar zu eigener Morgenroutine einladen\n"
    "Ton-Check: Natürlich und direkt, passt zum Projekt\n"
    "Länge-Check: Kurz genug für TikTok und Reels"
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
        "output": [{"content": [{"type": "output_text", "text": _CAPTION_RESPONSE}]}],
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
    return 200, {"id": "recCaption001", "fields": body.get("fields", {}) if body else {}}


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
            brief_id="wb_caption",
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
            evidence_pack_id="ep_caption_base",
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

def test_caption_without_openai_returns_docs_fallback(docs_loader: ProjectDocsLoader) -> None:
    service = ContentOpsService(docs_loader=docs_loader)

    result = service.handle(
        project_key="everydayengel",
        action_type="caption",
        command_body="baby trage alltag",
    )

    assert result.lane_name == "content_ops"
    assert result.action_type == "caption"
    assert result.summary == "Caption-Regeln geladen."
    assert result.openai_used is False
    assert result.airtable_record_id is None
    assert any("Caption" in item or "CTA" in item for item in result.items)


# ---------------------------------------------------------------------------
# Tests: caption with OpenAI only (no Airtable)
# ---------------------------------------------------------------------------

def test_caption_with_openai_returns_generated_result(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="caption",
        command_body="morgenroutine caption",
    )

    assert result.lane_name == "content_ops"
    assert result.action_type == "caption"
    assert result.summary == "Caption generiert."
    assert result.openai_used is True
    assert result.airtable_record_id is None
    assert len(result.items) >= 1
    assert any("Caption" in item for item in result.items)
    assert any("CTA-Richtung" in item for item in result.items)


def test_caption_with_openai_snapshot_is_correct(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="caption",
        command_body="",
    )

    snapshot = result.to_snapshot()
    assert snapshot["openai_used"] is True
    assert snapshot["airtable_record_id"] is None
    assert snapshot["action_type"] == "caption"
    assert snapshot["lane_name"] == "content_ops"


def test_caption_with_openai_parses_all_keys(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="caption",
        command_body="test",
    )

    item_keys = {item.split(":")[0] for item in result.items}
    assert "Caption" in item_keys
    assert "CTA-Richtung" in item_keys
    assert "Ton-Check" in item_keys
    assert "Länge-Check" in item_keys


# ---------------------------------------------------------------------------
# Tests: caption with both OpenAI and Airtable
# ---------------------------------------------------------------------------

def test_caption_with_openai_and_airtable_creates_record(docs_loader: ProjectDocsLoader) -> None:
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
        action_type="caption",
        command_body="morgenroutine",
    )

    assert result.summary == "Caption generiert."
    assert result.openai_used is True
    assert result.airtable_record_id == "recCaption001"

    snapshot = result.to_snapshot()
    assert snapshot["airtable_record_id"] == "recCaption001"


def test_foundation_backed_caption_uses_writer_brief_and_selected_snapshots(
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

    result = service.generate_caption_from_foundation(
        project_key="everydayengel",
        command_body="tiktok morgenroutine caption",
        foundation_result=_make_foundation_result(),
    )

    assert isinstance(result, FoundationCaptionResult)
    assert result.content_result.action_type == "caption"
    assert result.content_result.writer_brief_id == "wb_caption"
    assert result.content_result.foundation_snapshot_ids == ("as_platform", "as_cross")
    prompt = captured_system_prompt["text"]
    assert "Writer-Brief (bindend)" in prompt
    assert "Caption-Kontext (bindend)" in prompt
    assert "TikTok analysis snapshot" in prompt
    assert "Cross-platform analysis snapshot" in prompt
    assert "Use analysis snapshots as the primary grounding layer." in prompt


def test_build_caption_evidence_pack_links_snapshots_and_output(
    docs_loader: ProjectDocsLoader,
) -> None:
    service = ContentOpsService(docs_loader=docs_loader)
    foundation = _make_foundation_result()
    caption_result = FoundationCaptionResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="caption",
            command_body="morgenroutine",
            title="Content caption",
            summary="Caption generiert.",
            items=(
                "Caption: Manchmal braucht der Morgen nur 10 Minuten.",
                "CTA-Richtung: Meinung",
                "Ton-Check: Natürlich und direkt.",
                "Länge-Check: Kurz genug für TikTok.",
            ),
            openai_used=True,
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_caption",
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

    evidence_pack = service.build_caption_evidence_pack(
        project_key="everydayengel",
        caption_result=caption_result,
    )

    assert evidence_pack.snapshot_ids == ("as_platform", "as_cross")
    assert "docs:project-state" in evidence_pack.source_refs
    assert any(
        line.startswith("Caption output: Caption: Manchmal braucht der Morgen nur 10 Minuten.")
        for line in evidence_pack.evidence_lines
    )


def test_caption_airtable_record_has_correct_fields(docs_loader: ProjectDocsLoader) -> None:
    """Verify stage=drafted and structural fields are written to Airtable."""
    captured: dict[str, Any] = {}

    def transport_capture(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and body:
            captured.update(body)
        return 200, {"id": "recCaption002", "fields": {}}

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
        action_type="caption",
        command_body="test input",
    )

    fields = captured.get("fields", {})
    assert fields.get("stage") == "drafted"
    assert fields.get("project_key") == "everydayengel"
    assert fields.get("created_by") == "operator_core"
    assert fields.get("source_input") == "test input"
    assert "caption_text" in fields
    assert "cta_direction" in fields


# ---------------------------------------------------------------------------
# Tests: OpenAI error → fallback to docs-only
# ---------------------------------------------------------------------------

def test_caption_openai_error_falls_back_to_docs(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_error)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(
        project_key="everydayengel",
        action_type="caption",
        command_body="any kontext",
    )

    assert result.summary == "Caption-Regeln geladen."
    assert result.openai_used is False
    assert result.airtable_record_id is None
    assert len(result.items) == 3


def test_caption_openai_error_snapshot_is_clean(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_error)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    snapshot = service.handle(
        project_key="everydayengel",
        action_type="caption",
        command_body="",
    ).to_snapshot()

    assert snapshot["openai_used"] is False
    assert snapshot["airtable_record_id"] is None
    assert snapshot["action_type"] == "caption"


# ---------------------------------------------------------------------------
# Tests: Airtable error → result without record_id
# ---------------------------------------------------------------------------

def test_caption_airtable_error_returns_result_without_record_id(
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
        action_type="caption",
        command_body="test",
    )

    assert result.summary == "Caption generiert."
    assert result.openai_used is True
    assert result.airtable_record_id is None


# ---------------------------------------------------------------------------
# Regression: caption activation must not affect idea, draft, or hook
# ---------------------------------------------------------------------------

def test_idea_unaffected_by_caption_activation(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(project_key="everydayengel", action_type="idea", command_body="test")
    assert result.action_type == "idea"
    assert result.openai_used is True


def test_draft_unaffected_by_caption_activation(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(project_key="everydayengel", action_type="draft", command_body="test")
    assert result.action_type == "draft"
    assert result.openai_used is True


def test_hook_unaffected_by_caption_activation(docs_loader: ProjectDocsLoader) -> None:
    ctx = _make_bootstrap(airtable_enabled=False)
    openai_svc = OpenAIService(ctx, transport=_openai_transport_ok)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)

    result = service.handle(project_key="everydayengel", action_type="hook", command_body="test")
    assert result.action_type == "hook"
    assert result.openai_used is True


# ---------------------------------------------------------------------------
# Tests: caption parse fallback – prompt leak guard
# ---------------------------------------------------------------------------

def test_caption_parse_fallback_when_model_returns_question() -> None:
    """Model returns a clarifying question — raw text must not reach user.
    A post-ready generic caption must be returned instead."""
    service = ContentOpsService()
    result = service._parse_caption_response(
        "Bitte beschreibe den Inhalt des Videos, damit ich eine passende Caption erstellen kann."
    )
    assert "Bitte beschreibe" not in " ".join(result)
    assert any(item.startswith("Caption:") for item in result)
    caption_item = next(item for item in result if item.startswith("Caption:"))
    assert len(caption_item) > len("Caption: ")  # must contain actual text


def test_caption_parse_fallback_when_output_is_empty() -> None:
    """Empty model output must return a post-ready generic caption, not a system message."""
    service = ContentOpsService()
    result = service._parse_caption_response("")
    assert any(item.startswith("Caption:") for item in result)
    caption_item = next(item for item in result if item.startswith("Caption:"))
    assert len(caption_item) > len("Caption: ")
    assert "Ergebnis" not in caption_item
    assert "ergänzen" not in caption_item


def test_caption_parse_valid_response_not_affected_by_guard() -> None:
    """A valid response with Caption: key must pass through normally."""
    service = ContentOpsService()
    result = service._parse_caption_response(
        "Caption: Mein Morgenritual in 3 Schritten.\nCTA-Richtung: Meinung einladen"
    )
    assert any(item.startswith("Caption:") for item in result)
    assert any(item.startswith("CTA-Richtung:") for item in result)
