from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operator_core.bootstrap import BootstrapContext
from operator_core.config import AirtableSettings, AppSettings, OpenAISettings, Settings, TelegramSettings
from operator_core.core.analysis_foundation.models import (
    AnalysisFoundationResult,
    AnalysisSnapshot,
    EvidencePack,
    ModelExecutionMeta,
    WriterBrief,
)
from operator_core.core.content_ops.models import ContentOpResult, FoundationCtaResult, FoundationFollowupResult, FoundationSerieResult, FoundationTitleResult
from operator_core.core.content_ops.service import ContentOpsService
from operator_core.core.content_ops.proposal_store import ContentProposal
from operator_core.integrations.operational_knowledge_service import (
    OperationalKnowledgeContext,
    OperationalKnowledgeRow,
)
from operator_core.integrations.platform_signal_service import PlatformContext
from operator_core.projects.docs import ProjectDocsLoader


def _ctx() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(env="test", log_level="INFO", runtime_mode="service", active_project="everydayengel"),
        telegram=TelegramSettings(enabled=False, bot_token="", allowed_user_ids=(), allowed_chat_ids=()),
        airtable=AirtableSettings(enabled=False, api_key="", project_base_ids={"everydayengel": ""}),
        openai=OpenAISettings(enabled=False, api_key="", model="gpt-test", base_url="https://api.openai.com/v1", timeout_seconds=30),
    )
    return BootstrapContext(
        settings=settings,
        runtime_path=Path("projects/everydayengel/runtime.yaml"),
        project_runtime={
            "project_key": "everydayengel",
            "display_name": "Everyday Engel",
            "status": "active",
            "primary_interface": "telegram",
            "human_in_the_loop": "true",
        },
    )


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
                analytics_summary_lines=("Dominant CTA: save/share",),
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
            brief_id="wb_serie",
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
            evidence_pack_id="ep_serie_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("analytics:global_recent",),
            evidence_lines=("TikTok: 20:06",),
        ),
        execution_meta=execution_meta,
    )


def test_supports_new_unified_content_commands() -> None:
    service = ContentOpsService()

    for action in ("serie", "title", "cta", "vollauto"):
        assert service.supports(action) is True


def test_vollauto_uses_platform_signal_summary_in_prompt() -> None:
    captured: dict[str, str] = {}

    class _OpenAIStub:
        def complete_messages(self, *, system_prompt: str, user_prompt: str, temperature: float, model: str | None = None, **kwargs: Any):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return SimpleNamespace(
                output_text=(
                    "Serie/Thema: Alltag\n"
                    "Title: Kleine Routinen entlasten den Morgen spürbar.\n"
                    "Hook: Kennst du diesen kleinen Trick für ruhigere Morgen?\n"
                    "CTA: Welche Mini-Routine hilft dir am meisten?\n"
                    "Caption: Kleine Schritte machen den Morgen oft leichter.\n"
                    "Format: YouTube Short\n"
                    "Bereit: Ja, einsatzfähig"
                )
            )

    ok_loader = MagicMock()
    ok_loader.load_active.return_value = OperationalKnowledgeContext(
        rows=(
            OperationalKnowledgeRow(
                key="posting_time_youtube",
                label="Posting-Zeit YouTube",
                value="20:30",
                category="posting",
                status="active",
            ),
        )
    )
    platform_loader = MagicMock()
    platform_loader.load_all.return_value = {
        "youtube_short": PlatformContext(
            platform_key="youtube_short",
            table_id="tblYT",
            post_count=8,
            dominant_cta="Community-Frage",
            gap="mehr praktische Routinen testen",
            hook_examples=("Hook A", "Hook B"),
            dominant_format="YouTube Short",
            format_examples=("YouTube Short",),
            numeric_summary_lines=("Views: Ø 900 | best 1200 | Felder: views_72h",),
            numeric_fields_used=("views_72h",),
        )
    }

    service = ContentOpsService(
        docs_loader=ProjectDocsLoader(),
        openai_service=_OpenAIStub(),
        operational_knowledge_loader=ok_loader,
        platform_signal_loader=platform_loader,
    )

    result = service.handle(
        project_key="everydayengel",
        action_type="vollauto",
        command_body="youtube morgenroutine für entspanntere starts",
    )

    assert result.platform == "youtube_short"
    assert result.action_type == "vollauto"
    assert any(item.startswith("Serie/Thema:") for item in result.items)
    assert "Plattform: YouTube Shorts" in captured["system_prompt"]
    assert "Views: Ø 900 | best 1200 | Felder: views_72h" in captured["system_prompt"]


def test_idea_returns_single_natural_idea_item() -> None:
    class _OpenAIStub:
        def complete_messages(self, *, system_prompt: str, user_prompt: str, temperature: float, model: str | None = None, **kwargs: Any):
            return SimpleNamespace(
                output_text="Idee: Zeig in einem ruhigen YouTube Short, wie eine kleine Morgenroutine den Tag entschärft."
            )

    service = ContentOpsService(
        docs_loader=ProjectDocsLoader(),
        openai_service=_OpenAIStub(),
    )

    result = service.handle(
        project_key="everydayengel",
        action_type="idea",
        command_body="youtube entspannter morgen",
    )

    assert result.items == (
        "Idee: Zeig in einem ruhigen YouTube Short, wie eine kleine Morgenroutine den Tag entschärft.",
    )


def test_resolve_platform_accepts_canonical_platform_key() -> None:
    service = ContentOpsService()

    platform, body = service.resolve_platform_hint("youtube_short morgenroutine")

    assert platform == "youtube_short"
    assert body == "morgenroutine"


def test_followup_platform_override_uses_new_platform_context() -> None:
    captured: dict[str, str] = {}

    class _OpenAIStub:
        def complete_messages(self, *, system_prompt: str, user_prompt: str, temperature: float, model: str | None = None, **kwargs: Any):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return SimpleNamespace(
                output_text="Caption: Neue YouTube-Caption",
                model="gpt-test",
            )

    platform_loader = MagicMock()
    platform_loader.load_all.return_value = {
        "youtube_short": PlatformContext(
            platform_key="youtube_short",
            table_id="tblYT",
            post_count=8,
            dominant_cta="Community-Frage",
            gap="mehr praktische Routinen testen",
            hook_examples=("Hook A",),
            dominant_format="YouTube Short",
            format_examples=("YouTube Short",),
            numeric_summary_lines=("Views: Ø 900 | best 1200 | Felder: views_72h",),
            numeric_fields_used=("views_72h",),
        )
    }

    service = ContentOpsService(
        docs_loader=ProjectDocsLoader(),
        openai_service=_OpenAIStub(),
        platform_signal_loader=platform_loader,
    )
    proposal = ContentProposal(
        proposal_id="job-followup-1",
        project_key="everydayengel",
        action_type="caption",
        platform="instagram_reel",
        fields={"caption": "Alte Caption"},
        source_command_body="instagram_reel ruhiger morgen",
    )

    result = service.follow_up(
        project_key="everydayengel",
        proposal=proposal,
        instruction="schreib es um für YouTube",
    )

    assert result.platform == "youtube_short"
    assert result.command_body == "youtube_short ruhiger morgen"
    assert "YouTube" in captured["system_prompt"]
    assert "Views: Ø 900 | best 1200 | Felder: views_72h" in captured["system_prompt"]


def test_regeneration_command_body_blocks_previous_framing_kernels_for_caption() -> None:
    service = ContentOpsService()
    proposal = ContentProposal(
        proposal_id="job-regen-1",
        project_key="everydayengel",
        action_type="caption",
        platform="youtube_short",
        fields={"caption": "Ein ruhiger Morgen kann Wunder wirken. Welche Morgenroutine hilft dir am meisten?"},
        source_command_body="youtube_short ruhiger morgen",
    )

    command_body = service._build_regeneration_command_body(  # type: ignore[attr-defined]
        proposal=proposal,
        direction="Nutze eine praktischere Caption mit Mini-Erleichterung oder Chaos-reduzierendem Fokus.",
    )

    assert "Vermeide nach Möglichkeit diese bisherigen Framing-Kerne" in command_body
    assert "wunder" in command_body.lower()
    assert "morgenroutine" in command_body.lower()


def test_foundation_backed_serie_uses_writer_brief_and_selected_snapshots() -> None:
    captured: dict[str, str] = {}

    class _OpenAIStub:
        def complete_messages(self, *, system_prompt: str, user_prompt: str, temperature: float, model: str | None = None, **kwargs: Any):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return SimpleNamespace(output_text="Serie/Thema: Ruhiger Morgen", model="gpt-test")

    service = ContentOpsService(
        docs_loader=ProjectDocsLoader(),
        openai_service=_OpenAIStub(),
    )

    result = service.generate_serie_from_foundation(
        project_key="everydayengel",
        command_body="tiktok morgenroutine",
        foundation_result=_make_foundation_result(),
    )

    assert isinstance(result, FoundationSerieResult)
    assert result.content_result.action_type == "serie"
    assert result.content_result.writer_brief_id == "wb_serie"
    assert result.content_result.foundation_snapshot_ids == ("as_platform", "as_cross")
    assert "Writer-Brief (bindend)" in captured["system_prompt"]
    assert "TikTok analysis snapshot" in captured["system_prompt"]
    assert "Cross-platform analysis snapshot" in captured["system_prompt"]
    assert captured["user_prompt"] == "morgenroutine"


def test_build_serie_evidence_pack_links_snapshots_and_output() -> None:
    service = ContentOpsService()
    foundation = _make_foundation_result()
    serie_result = FoundationSerieResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="serie",
            command_body="morgenroutine",
            title="Serie/Thema",
            summary="Serie/Thema generiert.",
            items=("Serie/Thema: Ruhiger Morgen",),
            platform="tiktok",
            openai_used=True,
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_serie",
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

    evidence_pack = service.build_serie_evidence_pack(
        project_key="everydayengel",
        serie_result=serie_result,
    )

    assert evidence_pack.snapshot_ids == ("as_platform", "as_cross")
    assert "docs:project-state" in evidence_pack.source_refs
    assert any(line.startswith("Serie output: Serie/Thema: Ruhiger Morgen") for line in evidence_pack.evidence_lines)


def test_foundation_backed_title_uses_writer_brief_and_selected_snapshots() -> None:
    captured: dict[str, str] = {}

    class _OpenAIStub:
        def complete_messages(self, *, system_prompt: str, user_prompt: str, temperature: float, model: str | None = None, **kwargs: Any):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return SimpleNamespace(output_text="Title: Ruhiger Morgen ohne Hektik", model="gpt-test")

    service = ContentOpsService(
        docs_loader=ProjectDocsLoader(),
        openai_service=_OpenAIStub(),
    )

    result = service.generate_title_from_foundation(
        project_key="everydayengel",
        command_body="tiktok morgenroutine",
        foundation_result=_make_foundation_result(),
    )

    assert isinstance(result, FoundationTitleResult)
    assert result.content_result.action_type == "title"
    assert result.content_result.writer_brief_id == "wb_serie"
    assert result.content_result.foundation_snapshot_ids == ("as_platform", "as_cross")
    assert "Writer-Brief (bindend)" in captured["system_prompt"]
    assert "TikTok analysis snapshot" in captured["system_prompt"]
    assert "Cross-platform analysis snapshot" in captured["system_prompt"]
    assert captured["user_prompt"] == "morgenroutine"


def test_build_title_evidence_pack_links_snapshots_and_output() -> None:
    service = ContentOpsService()
    foundation = _make_foundation_result()
    title_result = FoundationTitleResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="title",
            command_body="morgenroutine",
            title="Title",
            summary="Title generiert.",
            items=("Title: Ruhiger Morgen ohne Hektik",),
            platform="tiktok",
            openai_used=True,
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_serie",
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

    evidence_pack = service.build_title_evidence_pack(
        project_key="everydayengel",
        title_result=title_result,
    )

    assert evidence_pack.snapshot_ids == ("as_platform", "as_cross")
    assert "docs:project-state" in evidence_pack.source_refs
    assert any(line.startswith("Title output: Title: Ruhiger Morgen ohne Hektik") for line in evidence_pack.evidence_lines)


def test_foundation_backed_cta_uses_writer_brief_and_selected_snapshots() -> None:
    captured: dict[str, str] = {}

    class _OpenAIStub:
        def complete_messages(self, *, system_prompt: str, user_prompt: str, temperature: float, model: str | None = None, **kwargs: Any):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return SimpleNamespace(output_text="CTA: Speichere dir die Routine fuer morgen frueh.", model="gpt-test")

    service = ContentOpsService(
        docs_loader=ProjectDocsLoader(),
        openai_service=_OpenAIStub(),
    )

    result = service.generate_cta_from_foundation(
        project_key="everydayengel",
        command_body="tiktok morgenroutine",
        foundation_result=_make_foundation_result(),
    )

    assert isinstance(result, FoundationCtaResult)
    assert result.content_result.action_type == "cta"
    assert result.content_result.writer_brief_id == "wb_serie"
    assert result.content_result.foundation_snapshot_ids == ("as_platform", "as_cross")
    assert "Writer-Brief (bindend)" in captured["system_prompt"]
    assert "TikTok analysis snapshot" in captured["system_prompt"]
    assert "Cross-platform analysis snapshot" in captured["system_prompt"]
    assert captured["user_prompt"] == "morgenroutine"


def test_build_cta_evidence_pack_links_snapshots_and_output() -> None:
    service = ContentOpsService()
    foundation = _make_foundation_result()
    cta_result = FoundationCtaResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="cta",
            command_body="morgenroutine",
            title="CTA",
            summary="CTA generiert.",
            items=("CTA: Speichere dir die Routine fuer morgen frueh.",),
            platform="tiktok",
            openai_used=True,
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_serie",
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

    evidence_pack = service.build_cta_evidence_pack(
        project_key="everydayengel",
        cta_result=cta_result,
    )

    assert evidence_pack.snapshot_ids == ("as_platform", "as_cross")
    assert "docs:project-state" in evidence_pack.source_refs
    assert any(line.startswith("CTA output: CTA: Speichere dir die Routine fuer morgen frueh.") for line in evidence_pack.evidence_lines)


def test_foundation_backed_followup_uses_writer_brief_and_selected_snapshots() -> None:
    captured: dict[str, str] = {}

    class _OpenAIStub:
        def complete_messages(self, *, system_prompt: str, user_prompt: str, temperature: float, model: str | None = None, **kwargs: Any):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return SimpleNamespace(output_text="Title: Neuer ruhiger Morgen\nCTA: Neue CTA", model="gpt-test")

    service = ContentOpsService(
        docs_loader=ProjectDocsLoader(),
        openai_service=_OpenAIStub(),
    )
    proposal = ContentProposal(
        proposal_id="job-followup-foundation",
        project_key="everydayengel",
        action_type="vollauto",
        platform="tiktok",
        fields={"title_raw": "Alter Titel", "cta": "Alte CTA"},
        source_command_body="tiktok morgenroutine",
    )

    result = service.generate_followup_from_foundation(
        project_key="everydayengel",
        proposal=proposal,
        instruction="mach die CTA direkter",
        foundation_result=_make_foundation_result(),
        mutation_mode="followup",
    )

    assert isinstance(result, FoundationFollowupResult)
    assert result.content_result.action_type == "followup"
    assert result.content_result.writer_brief_id == "wb_serie"
    assert result.content_result.foundation_snapshot_ids == ("as_platform", "as_cross")
    assert "Writer-Brief (bindend)" in captured["system_prompt"]
    assert "TikTok analysis snapshot" in captured["system_prompt"]
    assert "Cross-platform analysis snapshot" in captured["system_prompt"]
    assert "Aktueller Vorschlag:" in captured["system_prompt"]
    assert "Anweisung:" in captured["user_prompt"]


def test_build_followup_evidence_pack_links_snapshots_and_output() -> None:
    service = ContentOpsService()
    foundation = _make_foundation_result()
    followup_result = FoundationFollowupResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="followup",
            command_body="tiktok morgenroutine",
            title="Follow-up",
            summary="Vorschlag aktualisiert.",
            items=("Title: Neuer ruhiger Morgen", "CTA: Neue CTA"),
            platform="tiktok",
            openai_used=True,
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_serie",
        ),
        selected_snapshots=foundation.analysis_snapshots[:2],
        writer_brief=foundation.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-test",
            task_role="writer",
            status="completed",
        ),
        instruction="mach die CTA direkter",
        mutation_mode="rewrite",
        source_action_type="vollauto",
    )

    evidence_pack = service.build_followup_evidence_pack(
        project_key="everydayengel",
        followup_result=followup_result,
    )

    assert evidence_pack.snapshot_ids == ("as_platform", "as_cross")
    assert "docs:project-state" in evidence_pack.source_refs
    assert any(line.startswith("Instruction: mach die CTA direkter") for line in evidence_pack.evidence_lines)
    assert any(line.startswith("Follow-up output: Title: Neuer ruhiger Morgen") for line in evidence_pack.evidence_lines)


def test_regeneration_score_penalizes_repeated_anchor_families() -> None:
    service = ContentOpsService()
    proposal = ContentProposal(
        proposal_id="job-regen-2",
        project_key="everydayengel",
        action_type="caption",
        platform="youtube_short",
        fields={"caption": "Ein ruhiger Morgen kann Wunder wirken. Welche Morgenroutine hilft dir am meisten?"},
        source_command_body="youtube_short ruhiger morgen",
    )

    near_score = service._regeneration_score(  # type: ignore[attr-defined]
        proposal=proposal,
        candidate_fields={"caption": "Wie sieht deine Morgenroutine aus? Ein ruhiger Start kann so viel verändern."},
    )
    far_score = service._regeneration_score(  # type: ignore[attr-defined]
        proposal=proposal,
        candidate_fields={"caption": "Welche kleine Gewohnheit nimmt dir morgens das erste Chaos und macht den Tagesbeginn leichter?"},
    )

    assert far_score > near_score


def test_followup_creative_instruction_still_uses_model_path() -> None:
    class _OpenAIStub:
        def __init__(self) -> None:
            self.calls = 0

        def complete_messages(self, *, system_prompt: str, user_prompt: str, temperature: float, model: str | None = None, **kwargs: Any):
            self.calls += 1
            return SimpleNamespace(
                output_text="Caption: Kreativ ausgearbeitet",
                model="gpt-test",
            )
    openai_stub = _OpenAIStub()
    service = ContentOpsService(
        docs_loader=ProjectDocsLoader(),
        openai_service=openai_stub,
    )
    proposal = ContentProposal(
        proposal_id="job-followup-override-4",
        project_key="everydayengel",
        action_type="caption",
        platform="youtube_short",
        fields={"caption": "kekse zur weihnachtszeit"},
        source_command_body="youtube_short ruhiger morgen",
    )

    result = service.follow_up(
        project_key="everydayengel",
        proposal=proposal,
        instruction="Schreib daraus eine sinnvolle caption",
    )

    assert result.items[0] == "Caption: Kreativ ausgearbeitet"
    assert openai_stub.calls == 1
