from __future__ import annotations

from pathlib import Path

import pytest

from operator_core.bootstrap import BootstrapContext
from operator_core.config import AirtableSettings, AppSettings, OpenAISettings, Settings, TelegramSettings
from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.execution_service import ExecutionService
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.models import RequestContext
from operator_core.core.backbone.repositories import InMemoryEventRepository, InMemoryJobRepository, InMemoryRunRepository
from operator_core.core.backbone.run_service import RunService
from operator_core.core.backbone.statuses import JobStatus
from operator_core.core.content_ops.service import ContentOpsService
from operator_core.core.menu_layouts import MENU_OVERLAY_REPLY_MARKUP, PERSISTENT_MENU_REPLY_MARKUP
from operator_core.core.content_ops.proposal_store import ContentProposal, ContentProposalStore
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.core.response_formatter.service import ResponseFormatterService
from operator_core.integrations.daily_plan_service import TodayPlanSnapshot
from operator_core.interfaces.telegram.entry_flow import build_telegram_entry_handoff


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


class _DailyPlanStub:
    def __init__(self) -> None:
        self.upsert_calls: list[dict[str, object]] = []
        self.patch_calls: list[dict[str, object]] = []

    def upsert_plan(self, **kwargs):
        self.upsert_calls.append(kwargs)
        return "recPlan001"

    def get_plan_record(self, *, project_key: str, record_id: str) -> TodayPlanSnapshot:
        return TodayPlanSnapshot(record_id=record_id, decision="pending", platform="youtube_short")

    def patch_fields(self, *, project_key: str, record_id: str, fields: dict[str, object], current: TodayPlanSnapshot):
        self.patch_calls.append(fields)
        return TodayPlanSnapshot(
            record_id=record_id,
            decision="pending",
            platform="youtube_short",
            serie_thema=str(fields.get("serie_thema") or ""),
            title_raw=str(fields.get("title_raw") or ""),
            cta=str(fields.get("cta") or ""),
        )


def test_response_formatter_adds_buttons_for_content_ops_proposals() -> None:
    from operator_core.core.content_ops.models import ContentOpResult

    class _IdeaStub:
        def handle(self, *, project_key, action_type, command_body):
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type="idea",
                command_body=command_body,
                title="Idee",
                summary="Idee generiert.",
                items=("Idee: Morgenroutine kurz gezeigt",),
                platform="youtube_short",
                openai_used=True,
            )

        def supports(self, action_type):
            return action_type == "idea"

        def resolve_platform_hint(self, command_body):
            return "youtube_short", command_body

    formatter = ResponseFormatterService()
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _IdeaStub()  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service)
    handoff = build_telegram_entry_handoff(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "text": "/idea youtube morgenroutine",
                "chat": {"id": 22, "type": "private"},
                "from": {"id": 33, "username": "julia"},
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    formatted = formatter.format_request_flow_result(result)

    assert formatted.reply_markup is not None
    first_row = formatted.reply_markup["inline_keyboard"][0]
    assert first_row[0]["callback_data"].startswith("content_ops:idea_to_draft:")
    assert first_row[1]["callback_data"].startswith("content_ops:dismiss:")


def test_no_item_idea_fallback_shows_summary_with_recovery_buttons() -> None:
    from operator_core.core.content_ops.models import ContentOpResult

    summary = (
        "Diese Idee wurde gerade in fast diesem Kern verworfen. "
        "Gib mir bitte einen neuen Winkel oder nutze /idea Neue Idee."
    )

    class _NoItemIdeaStub:
        def handle(self, *, project_key, action_type, command_body):
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type="idea",
                command_body=command_body,
                title="Idee",
                summary=summary,
                items=(),
                platform="youtube_short",
                openai_used=True,
            )

        def supports(self, action_type):
            return action_type == "idea"

        def resolve_platform_hint(self, command_body):
            return "youtube_short", command_body

    formatter = ResponseFormatterService()
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _NoItemIdeaStub()  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service)
    handoff = build_telegram_entry_handoff(
        {
            "update_id": 1001,
            "message": {
                "message_id": 1011,
                "text": "/idea youtube beim Kochen plötzlich sitzen wegen Schwindel",
                "chat": {"id": 1022, "type": "private"},
                "from": {"id": 1033, "username": "julia"},
            },
        },
        _ctx(),
    )

    result = request_flow.handle_telegram_entry_handoff(handoff)
    formatted = formatter.format_request_flow_result(result)

    assert summary in formatted.text
    assert "Kein Vorschlag verfügbar" not in formatted.text
    assert formatted.reply_markup is not None
    keyboard = formatted.reply_markup["inline_keyboard"]
    assert keyboard[0][0]["callback_data"].startswith("content_ops:idea_fresh:")
    assert keyboard[0][1]["callback_data"].startswith("content_ops:idea_angle:")
    assert keyboard[1][0]["callback_data"].startswith("content_ops:dismiss:")
    assert "content_ops:idea_to_draft:" not in str(formatted.reply_markup)
    assert "content_ops:accept:" not in str(formatted.reply_markup)
    assert "content_ops:reject:" not in str(formatted.reply_markup)


def test_idea_fresh_fallback_button_reruns_idea_with_fresh_steering() -> None:
    from operator_core.core.content_ops.models import ContentOpResult

    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job-fallback-fresh",
            project_key="everydayengel",
            action_type="idea_fallback",
            platform="youtube_short",
            fields={},
            source_command_body="/idea beim Kochen plötzlich sitzen wegen Schwindel",
            explanation="Diese Idee wurde gerade in fast diesem Kern verworfen.",
        )
    )

    captured: dict[str, str] = {}

    class _FreshIdeaStub:
        def handle(self, *, project_key, action_type, command_body):
            captured["action_type"] = action_type
            captured["command_body"] = command_body
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type="idea",
                command_body=command_body,
                title="Idee",
                summary="Idee generiert.",
                items=("Idee: Beim Kochen werden mir Gerüche plötzlich zu viel.",),
                platform="youtube_short",
                openai_used=True,
            )

        def supports(self, action_type):
            return action_type == "idea"

        def resolve_platform_hint(self, command_body):
            return "youtube_short", command_body

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _FreshIdeaStub()  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)
    formatter = ResponseFormatterService()

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 1002,
            "callback_query": {
                "id": "cbq-fresh",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:idea_fresh:job-fallback-fresh",
                "message": {
                    "message_id": 1012,
                    "text": "💡 Idee",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )

    result = request_flow.handle_telegram_entry_handoff(handoff)
    formatted = formatter.format_request_flow_result(result)

    assert result.was_executed is True
    assert captured["action_type"] == "idea"
    assert "/idea /idea" not in result.request_context.request_text
    assert result.request_context.request_text.startswith("/idea youtube_short Neue Idee")
    # idea_fresh must NOT echo the blocked source — it forces IDEATION mode with a clean prompt
    assert "beim Kochen" not in captured["command_body"]
    assert "sitzen" not in captured["command_body"].lower()
    assert "frischer Alltagspunkt" in captured["command_body"]
    assert "verworfenen Kern" in captured["command_body"]
    assert "youtube_short" in captured["command_body"]
    assert formatted.callback_answer_text == "Frischer"
    assert formatted.edit_message_text == "↻ Frischer wird erstellt."
    assert "Gerüche" in formatted.text
    assert formatted.edit_message_id == 1012
    assert formatted.edit_reply_markup is None


def test_idea_angle_fallback_button_reruns_idea_with_same_family_new_angle_steering() -> None:
    from operator_core.core.content_ops.models import ContentOpResult

    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job-fallback-angle",
            project_key="everydayengel",
            action_type="idea_fallback",
            platform="youtube_short",
            fields={},
            source_command_body="/idea beim Kochen plötzlich sitzen wegen Schwindel",
            explanation="Diese Idee wurde gerade in fast diesem Kern verworfen.",
        )
    )

    captured: dict[str, str] = {}

    class _AngleIdeaStub:
        def handle(self, *, project_key, action_type, command_body):
            captured["action_type"] = action_type
            captured["command_body"] = command_body
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type="idea",
                command_body=command_body,
                title="Idee",
                summary="Idee generiert.",
                items=("Idee: Beim Kochen merke ich, dass starke Gerüche mich sofort stoppen.",),
                platform="youtube_short",
                openai_used=True,
            )

        def supports(self, action_type):
            return action_type == "idea"

        def resolve_platform_hint(self, command_body):
            return "youtube_short", command_body

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _AngleIdeaStub()  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 1003,
            "callback_query": {
                "id": "cbq-angle",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:idea_angle:job-fallback-angle",
                "message": {
                    "message_id": 1013,
                    "text": "💡 Idee",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )

    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert captured["action_type"] == "idea"
    assert "/idea /idea" not in result.request_context.request_text
    assert not captured["command_body"].startswith("/idea")
    # idea_angle strips scene-connectors and concrete verbs so MIRROR mode is not re-triggered,
    # but keeps thematic domain words (e.g. "Kochen", "Schwindel") in the prompt.
    assert "beim" not in captured["command_body"].lower().split()
    assert "sitzen" not in captured["command_body"].lower().split()
    assert "wegen" not in captured["command_body"].lower().split()
    # topical domain word preserved
    assert "Kochen" in captured["command_body"] or "kochen" in captured["command_body"].lower()
    assert "gleiche" in captured["command_body"].lower() or "andere" in captured["command_body"].lower()
    assert result.formatter_payload.callback_answer_text == "Neuer Winkel"
    assert result.formatter_payload.edit_message_text == "↻ Neuer Winkel wird erstellt."


# ---------------------------------------------------------------------------
# Recovery path distinctness and exhausted-fallback messages
# ---------------------------------------------------------------------------

def _make_no_item_fallback_stub():
    """Stub that always returns a no-item rejected-same-core fallback."""
    from operator_core.core.content_ops.models import ContentOpResult
    from operator_core.core.content_ops.service import _REJECTED_SAME_CORE_FALLBACK_SUMMARY

    class _AlwaysFallbackStub:
        def handle(self, *, project_key, action_type, command_body):
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type="idea",
                command_body=command_body,
                title="Content idea",
                summary=_REJECTED_SAME_CORE_FALLBACK_SUMMARY,
                items=(),
                openai_used=True,
            )

        def supports(self, action_type):
            return action_type == "idea"

        def resolve_platform_hint(self, command_body):
            return "tiktok", command_body

    return _AlwaysFallbackStub()


def test_idea_fresh_exhausted_gives_path_specific_message() -> None:
    """When idea_fresh still hits no-item fallback, shows fresh-specific message (not generic)."""
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job-exhaust-fresh",
            project_key="everydayengel",
            action_type="idea_fallback",
            platform="tiktok",
            fields={},
            source_command_body="beim Kochen plötzlich sitzen wegen Schwindel",
            explanation="Diese Idee wurde gerade in fast diesem Kern verworfen.",
        )
    )
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _make_no_item_fallback_stub()  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 2001,
            "callback_query": {
                "id": "cbq-exhaust-fresh",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:idea_fresh:job-exhaust-fresh",
                "message": {
                    "message_id": 2011,
                    "text": "💡 Idee",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )

    result = request_flow.handle_telegram_entry_handoff(handoff)
    assert result.formatter_payload.edit_message_text is not None
    assert "frischen Vorschlag" in result.formatter_payload.edit_message_text
    assert "anderen Alltagspunkt" in result.formatter_payload.edit_message_text
    # must NOT be the generic no-item fallback (no Frischer/Neuer-Winkel buttons)
    assert "Neuer Winkel" not in (result.formatter_payload.edit_message_text or "")
    assert result.formatter_payload.callback_answer_text == "Kein Vorschlag"


def test_idea_angle_exhausted_gives_path_specific_message() -> None:
    """When idea_angle still hits no-item fallback, shows angle-specific message (not generic)."""
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job-exhaust-angle",
            project_key="everydayengel",
            action_type="idea_fallback",
            platform="tiktok",
            fields={},
            source_command_body="beim Kochen plötzlich sitzen wegen Schwindel",
            explanation="Diese Idee wurde gerade in fast diesem Kern verworfen.",
        )
    )
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _make_no_item_fallback_stub()  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 2002,
            "callback_query": {
                "id": "cbq-exhaust-angle",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:idea_angle:job-exhaust-angle",
                "message": {
                    "message_id": 2012,
                    "text": "💡 Idee",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )

    result = request_flow.handle_telegram_entry_handoff(handoff)
    assert result.formatter_payload.edit_message_text is not None
    assert "neuem Winkel" in result.formatter_payload.edit_message_text
    assert "anderen Alltagspunkt" in result.formatter_payload.edit_message_text
    # fresh path message must NOT appear
    assert "frischen Vorschlag" not in result.formatter_payload.edit_message_text
    assert result.formatter_payload.callback_answer_text == "Kein Vorschlag"


def test_idea_fresh_and_angle_exhausted_messages_are_distinct() -> None:
    """idea_fresh exhausted text differs from idea_angle exhausted text."""
    from operator_core.core.request_flow.service import RequestFlowService as RFS
    fresh_text = RFS._idea_fallback_rerun_exhausted_text("idea_fresh")
    angle_text = RFS._idea_fallback_rerun_exhausted_text("idea_angle")
    assert fresh_text != angle_text
    assert "frischen Vorschlag" in fresh_text
    assert "neuem Winkel" in angle_text


def test_idea_fresh_command_body_forces_ideation_mode() -> None:
    """idea_fresh command body must not contain MIRROR-triggering tokens from source."""
    from operator_core.core.content_ops.duplicate_guard import (
        _IDEA_CONCRETE_VERBS,
        _IDEA_SCENE_CONNECTORS,
        IdeaQualityGate,
    )

    proposal = ContentProposal(
        proposal_id="mode-check-fresh",
        project_key="everydayengel",
        action_type="idea_fallback",
        platform="tiktok",
        fields={},
        source_command_body="/idea beim Kochen plötzlich sitzen wegen Schwindel",
        explanation="",
    )
    from operator_core.core.request_flow.service import RequestFlowService as RFS
    body = RFS._idea_fallback_rerun_command_body(proposal=proposal, action="idea_fresh")
    assert IdeaQualityGate.classify_idea_mode(body) == "ideation"
    assert not body.startswith("/idea")
    # source tokens stripped
    assert "beim" not in body.lower().split()
    assert "sitzen" not in body.lower().split()


def test_idea_angle_command_body_forces_ideation_mode() -> None:
    """idea_angle command body strips scene connectors + verbs → IDEATION mode, keeps topic."""
    from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate

    proposal = ContentProposal(
        proposal_id="mode-check-angle",
        project_key="everydayengel",
        action_type="idea_fallback",
        platform="tiktok",
        fields={},
        source_command_body="/idea beim Kochen plötzlich sitzen wegen Schwindel",
        explanation="",
    )
    from operator_core.core.request_flow.service import RequestFlowService as RFS
    body = RFS._idea_fallback_rerun_command_body(proposal=proposal, action="idea_angle")
    assert IdeaQualityGate.classify_idea_mode(body) == "ideation"
    assert not body.startswith("/idea")
    # scene-connector and concrete-verb tokens removed
    assert "beim" not in body.lower().split()
    assert "sitzen" not in body.lower().split()
    assert "wegen" not in body.lower().split()
    # topical domain words preserved
    assert "kochen" in body.lower()


def test_idea_fresh_and_angle_command_bodies_are_distinct() -> None:
    """The two recovery paths must produce different command bodies."""
    proposal = ContentProposal(
        proposal_id="dist-check",
        project_key="everydayengel",
        action_type="idea_fallback",
        platform="tiktok",
        fields={},
        source_command_body="/idea beim Kochen plötzlich sitzen wegen Schwindel",
        explanation="",
    )
    from operator_core.core.request_flow.service import RequestFlowService as RFS
    fresh_body = RFS._idea_fallback_rerun_command_body(proposal=proposal, action="idea_fresh")
    angle_body = RFS._idea_fallback_rerun_command_body(proposal=proposal, action="idea_angle")
    assert fresh_body != angle_body
    # fresh: does NOT contain topical domain word from source
    # angle: DOES contain it
    assert "kochen" not in fresh_body.lower()
    assert "kochen" in angle_body.lower()


def test_idea_fallback_dismiss_closes_state_safely() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job-fallback-dismiss",
            project_key="everydayengel",
            action_type="idea_fallback",
            platform="youtube_short",
            fields={},
            source_command_body="beim Kochen plötzlich sitzen wegen Schwindel",
            explanation="Diese Idee wurde gerade in fast diesem Kern verworfen.",
        )
    )
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 1004,
            "callback_query": {
                "id": "cbq-dismiss",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:dismiss:job-fallback-dismiss",
                "message": {
                    "message_id": 1014,
                    "text": "💡 Idee",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )

    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert result.formatter_payload.edit_message_text == "✖️ Vorschlag verworfen."
    assert result.formatter_payload.edit_reply_markup is None
    assert proposal_store.get("job-fallback-dismiss") is None


def test_idea_to_draft_callback_replaces_idea_with_structured_draft() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job-idea-1",
            project_key="everydayengel",
            action_type="idea",
            platform="youtube_short",
            fields={"title_raw": "Ruhiger Morgen als Mini-Idee"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )

    class _ContentOpsStub:
        def handle(self, *, project_key, action_type, command_body):
            from operator_core.core.content_ops.models import ContentOpResult

            assert action_type == "vollauto"
            assert command_body == "youtube_short Ruhiger Morgen als Mini-Idee"
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type="vollauto",
                command_body=command_body,
                title="Voll Auto",
                summary="Entwurf erstellt.",
                items=(
                    "Serie/Thema: Alltag",
                    "Title: Ruhiger Morgen ohne Hektik",
                    "Hook: So wird der Start ruhiger",
                    "CTA: Was hilft dir morgens?",
                    "Caption: Ein ruhiger Morgen beginnt oft mit einer Kleinigkeit.",
                    "Format: YouTube Short",
                    "Bereit: bereit",
                ),
                platform="youtube_short",
                openai_used=True,
            )

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _ContentOpsStub()  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)
    formatter = ResponseFormatterService()

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 1_1,
            "callback_query": {
                "id": "cbq-idea-to-draft",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:idea_to_draft:job-idea-1",
                "message": {
                    "message_id": 12,
                    "text": "💡 Idee",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)
    formatted = formatter.format_request_flow_result(result)

    assert result.was_executed is True
    assert "Serie/Thema: Alltag" in formatted.text
    assert "Title: Ruhiger Morgen ohne Hektik" in formatted.text
    assert formatted.reply_markup is not None
    first_row = formatted.reply_markup["inline_keyboard"][0]
    second_row = formatted.reply_markup["inline_keyboard"][1]
    assert first_row[0]["callback_data"] == "content_ops:apply:job-idea-1"
    assert first_row[1]["callback_data"] == "content_ops:dismiss:job-idea-1"
    assert second_row[0]["callback_data"] == "content_ops:rewrite:job-idea-1"
    assert second_row[1]["callback_data"] == "content_ops:regenerate:job-idea-1"
    active = proposal_store.active_for(chat_id="22", user_id="33")
    assert active is not None
    assert active.action_type == "vollauto"
    assert active.fields["title_raw"] == "Ruhiger Morgen ohne Hektik"


def test_idea_apply_callback_is_rejected_until_draft_exists() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job-idea-2",
            project_key="everydayengel",
            action_type="idea",
            platform="youtube_short",
            fields={"title_raw": "Nur eine Idee"},
        )
    )
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    daily_plan = _DailyPlanStub()
    request_flow = RequestFlowService(
        execution_service,
        daily_plan_service=daily_plan,
        content_proposal_store=proposal_store,
    )

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 1_2,
            "callback_query": {
                "id": "cbq-idea-apply",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:apply:job-idea-2",
                "message": {
                    "message_id": 13,
                    "text": "💡 Idee",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert "zuerst in einen Entwurf" in (result.formatter_payload.edit_message_text or "")
    assert daily_plan.upsert_calls == []


def test_content_ops_apply_callback_sets_fields_into_daily_plan() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job123",
            project_key="everydayengel",
            action_type="vollauto",
            platform="youtube_short",
            fields={
                "serie_thema": "Alltag",
                "title_raw": "Kleine Routinen entlasten den Morgen spürbar.",
                "cta": "Welche Mini-Routine hilft dir am meisten?",
            },
        )
    )
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    daily_plan = _DailyPlanStub()
    request_flow = RequestFlowService(
        execution_service,
        daily_plan_service=daily_plan,
        content_proposal_store=proposal_store,
    )

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 2,
            "callback_query": {
                "id": "cbq-1",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:apply:job123",
                "message": {
                    "message_id": 12,
                    "text": "📝 Voll Auto",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert daily_plan.upsert_calls[0]["platform"] == "youtube_short"
    assert daily_plan.patch_calls[0]["serie_thema"] == "Alltag"
    assert "In Tagesplan gesetzt" in (result.formatter_payload.edit_message_text or "")
    assert result.formatter_payload.edit_reply_markup is not None
    buttons = [button["text"] for row in result.formatter_payload.edit_reply_markup["inline_keyboard"] for button in row]
    assert "⏭ Heute auslassen" in buttons
    assert "🪄 Automatisch ergänzen" in buttons
    assert "🔄 Ersetzen" in buttons
    assert "⬆️ Upload in Airtable" in buttons


def test_content_ops_apply_overwrites_existing_field_for_field_command() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job124",
            project_key="everydayengel",
            action_type="cta",
            platform="youtube_short",
            fields={"cta": "Neue CTA"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    daily_plan = _DailyPlanStub()
    request_flow = RequestFlowService(
        execution_service,
        daily_plan_service=daily_plan,
        content_proposal_store=proposal_store,
    )

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 3,
            "callback_query": {
                "id": "cbq-2",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:apply:job124",
                "message": {
                    "message_id": 13,
                    "text": "CTA",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )
    request_flow.handle_telegram_entry_handoff(handoff)

    assert daily_plan.patch_calls[0] == {"cta": "Neue CTA"}


def test_followup_uses_last_active_proposal_context() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job125",
            project_key="everydayengel",
            action_type="vollauto",
            platform="youtube_short",
            fields={"cta": "Alte CTA", "title_raw": "Alter Title"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )

    class _ContentOpsStub:
        def follow_up(self, *, project_key: str, proposal: ContentProposal, instruction: str):
            from operator_core.core.content_ops.models import ContentOpResult

            assert proposal.platform == "youtube_short"
            assert instruction == "gib mir eine andere CTA"
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type="followup",
                command_body=instruction,
                title="Follow-up",
                summary="Vorschlag aktualisiert.",
                items=("Title: Alter Title", "CTA: Neue CTA"),
                platform="youtube_short",
                openai_used=True,
            )

        def resolve_platform_hint(self, command_body: str):
            return "", command_body

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _ContentOpsStub()  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)
    formatter = ResponseFormatterService()

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 4,
            "message": {
                "message_id": 14,
                "text": "gib mir eine andere CTA",
                "chat": {"id": 22, "type": "private"},
                "from": {"id": 33, "username": "julia"},
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)
    formatted = formatter.format_request_flow_result(result)

    assert result.was_executed is True
    assert "Neue CTA" in formatted.text
    active = proposal_store.active_for(chat_id="22", user_id="33")
    assert active is not None
    assert active.fields["cta"] == "Neue CTA"


def test_followup_routes_through_execution_service_mutation_runner() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job125exec",
            project_key="everydayengel",
            action_type="vollauto",
            platform="youtube_short",
            fields={"cta": "Alte CTA", "title_raw": "Alter Title"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )

    captured: dict[str, object] = {}

    def _execute_content_mutation(request_context: RequestContext, *, proposal: ContentProposal, instruction: str, mutation_mode: str, title: str, priority: int = 0):
        captured["proposal_id"] = proposal.proposal_id
        captured["instruction"] = instruction
        captured["mutation_mode"] = mutation_mode
        from operator_core.core.backbone.execution_service import ExecutionResult
        return ExecutionResult(
            job_id="job-followup-real",
            run_id="run-followup-real",
            job_status=JobStatus.COMPLETED,
            run_status="succeeded",
            event_count=5,
            result_summary="Vorschlag aktualisiert.",
            error_summary=None,
            output_snapshot={
                "lane_name": "content_ops",
                "action_type": "followup",
                "command_body": "youtube_short ruhiger morgen",
                "summary": "Vorschlag aktualisiert.",
                "items": ["Title: Alter Title", "CTA: Neue CTA"],
                "platform": "youtube_short",
                "writer_brief_id": "wb_followup",
                "foundation_snapshot_ids": ["as_platform", "as_cross"],
                "evidence_pack_id": "ep_followup",
            },
        )

    execution_service.execute_content_mutation = _execute_content_mutation  # type: ignore[method-assign]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 4_1,
            "message": {
                "message_id": 14,
                "text": "gib mir eine andere CTA",
                "chat": {"id": 22, "type": "private"},
                "from": {"id": 33, "username": "julia"},
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert captured == {
        "proposal_id": "job125exec",
        "instruction": "gib mir eine andere CTA",
        "mutation_mode": "followup",
    }
    assert result.formatter_payload.execution_summary["job_id"] == "job-followup-real"
    assert result.formatter_payload.execution_summary["run_id"] == "run-followup-real"
    assert result.formatter_payload.execution_summary["output_snapshot"]["evidence_pack_id"] == "ep_followup"


def test_followup_preserves_field_scope_for_active_caption_proposal() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job125b",
            project_key="everydayengel",
            action_type="caption",
            platform="youtube_short",
            fields={"caption": "Alte Caption"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )

    class _ContentOpsStub:
        def follow_up(self, *, project_key: str, proposal: ContentProposal, instruction: str):
            from operator_core.core.content_ops.models import ContentOpResult

            assert proposal.action_type == "caption"
            assert instruction == "mach es direkter"
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type="followup",
                command_body="youtube_short ruhiger morgen",
                title="Follow-up",
                summary="Vorschlag aktualisiert.",
                items=(
                    "Serie/Thema: Sollte nicht erscheinen",
                    "Title: Auch nicht",
                    "Caption: Neue direkte Caption",
                ),
                platform="youtube_short",
                openai_used=True,
            )

        def resolve_platform_hint(self, command_body: str):
            return "", command_body

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _ContentOpsStub()  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)
    formatter = ResponseFormatterService()

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 4_1,
            "message": {
                "message_id": 14,
                "text": "mach es direkter",
                "chat": {"id": 22, "type": "private"},
                "from": {"id": 33, "username": "julia"},
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)
    formatted = formatter.format_request_flow_result(result)

    assert "Caption: Neue direkte Caption" in formatted.text
    assert "Serie/Thema:" not in formatted.text
    assert "Title:" not in formatted.text
    active = proposal_store.active_for(chat_id="22", user_id="33")
    assert active is not None
    assert active.action_type == "caption"
    assert active.fields["caption"] == "Neue direkte Caption"


def test_followup_retries_when_change_request_returns_same_fields_first() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job126",
            project_key="everydayengel",
            action_type="vollauto",
            platform="youtube_short",
            fields={"cta": "Alte CTA", "title_raw": "Alter Title"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )

    class _OpenAIStub:
        def __init__(self) -> None:
            self.calls = 0

        def complete_messages(self, *, system_prompt: str, user_prompt: str, temperature: float):
            from types import SimpleNamespace

            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(output_text="Title: Alter Title\nCTA: Alte CTA")
            return SimpleNamespace(output_text="Title: Alter Title\nCTA: Neue CTA")

    openai_stub = _OpenAIStub()

    class _DocsStub:
        def load(self, project_key: str, doc_name: str):
            from types import SimpleNamespace
            return SimpleNamespace(content="")

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = ContentOpsService(
        docs_loader=_DocsStub(),
        openai_service=openai_stub,
    )
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 9,
            "message": {
                "message_id": 19,
                "text": "gib mir eine andere CTA",
                "chat": {"id": 22, "type": "private"},
                "from": {"id": 33, "username": "julia"},
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    active = proposal_store.active_for(chat_id="22", user_id="33")
    assert active is not None
    assert active.fields["cta"] == "Neue CTA"
    assert openai_stub.calls == 2


def test_rewrite_button_uses_followup_and_updates_active_proposal() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job127",
            project_key="everydayengel",
            action_type="cta",
            platform="youtube_short",
            fields={"cta": "Alte CTA"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )

    class _ContentOpsStub:
        def __init__(self) -> None:
            self.instructions: list[str] = []

        def rewrite_proposal(self, *, project_key: str, proposal: ContentProposal):
            from operator_core.core.content_ops.models import ContentOpResult

            self.instructions.append("rewrite")
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type="followup",
                command_body="rewrite",
                title="Follow-up",
                summary="Vorschlag aktualisiert.",
                items=("CTA: Neu formulierte CTA",),
                platform=proposal.platform,
                openai_used=True,
            )

    stub = _ContentOpsStub()
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = stub  # type: ignore[assignment]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 10,
            "callback_query": {
                "id": "cbq-rewrite",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:rewrite:job127",
                "message": {
                    "message_id": 20,
                    "text": "CTA",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.formatter_payload.callback_answer_text == "Umformuliert"
    assert stub.instructions == ["rewrite"]
    active = proposal_store.active_for(chat_id="22", user_id="33")
    assert active is not None
    assert active.action_type == "cta"
    assert active.fields["cta"] == "Neu formulierte CTA"
    items = result.formatter_payload.execution_summary["output_snapshot"]["items"]
    assert items == ["CTA: Neu formulierte CTA"]


def test_idea_to_draft_routes_through_execution_service_request_path_when_supported() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job-idea-exec",
            project_key="everydayengel",
            action_type="idea",
            platform="youtube_short",
            fields={"title_raw": "Ruhiger Morgen als Mini-Idee"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )

    class _ContentOpsStub:
        def supports(self, action_type):
            return action_type == "vollauto"

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _ContentOpsStub()  # type: ignore[assignment]

    captured: dict[str, str] = {}

    def _execute_request(request_context: RequestContext, *, job_type: str, title: str, priority: int = 0):
        captured["command_name"] = str(request_context.command_name)
        captured["command_body"] = str(request_context.command_body)
        from operator_core.core.backbone.execution_service import ExecutionResult
        return ExecutionResult(
            job_id="job-vollauto-real",
            run_id="run-vollauto-real",
            job_status=JobStatus.COMPLETED,
            run_status="succeeded",
            event_count=5,
            result_summary="Voll Auto generiert.",
            error_summary=None,
            output_snapshot={
                "lane_name": "content_ops",
                "action_type": "vollauto",
                "command_body": "youtube_short Ruhiger Morgen als Mini-Idee",
                "summary": "Voll Auto generiert.",
                "items": [
                    "Serie/Thema: Alltag",
                    "Title: Ruhiger Morgen ohne Hektik",
                    "Hook: So wird der Start ruhiger",
                    "CTA: Was hilft dir morgens?",
                    "Caption: Ein ruhiger Morgen beginnt oft mit einer Kleinigkeit.",
                    "Format: YouTube Short",
                    "Bereit: bereit",
                ],
                "platform": "youtube_short",
                "writer_brief_id": "wb_vollauto",
                "foundation_snapshot_ids": ["as_platform", "as_cross"],
                "evidence_pack_id": "ep_vollauto",
            },
        )

    execution_service.execute_request = _execute_request  # type: ignore[method-assign]
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 1_2,
            "callback_query": {
                "id": "cbq-idea-to-draft-exec",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:idea_to_draft:job-idea-exec",
                "message": {
                    "message_id": 12,
                    "text": "💡 Idee",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert captured == {
        "command_name": "vollauto",
        "command_body": "youtube_short Ruhiger Morgen als Mini-Idee",
    }
    assert result.formatter_payload.execution_summary["job_id"] == "job-vollauto-real"
    assert result.formatter_payload.execution_summary["run_id"] == "run-vollauto-real"


def test_regenerate_button_shows_replace_prompt_for_vollauto() -> None:
    """content_ops:regenerate now opens the literal-replace dialog (no AI)."""
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job128",
            project_key="everydayengel",
            action_type="vollauto",
            platform="youtube_short",
            fields={"title_raw": "Alter Title", "cta": "Alte CTA", "caption": "Alte Caption"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 11,
            "callback_query": {
                "id": "cbq-regenerate",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:regenerate:job128",
                "message": {
                    "message_id": 21,
                    "text": "Voll Auto",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    # Shows a dialog prompt, not an AI result
    assert result.decision == "content_ops_callback"
    assert result.was_executed is False
    assert "Womit willst du" in result.formatter_payload.message_text
    assert result.formatter_payload.send_response is True
    # Proposal stays untouched
    assert proposal_store.get("job128") is not None
    assert proposal_store.get("job128").fields["caption"] == "Alte Caption"  # type: ignore[union-attr]


def test_regenerate_button_shows_replace_prompt_for_caption() -> None:
    """content_ops:regenerate on caption proposal shows 'Caption ersetzen' dialog."""
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="job129",
            project_key="everydayengel",
            action_type="caption",
            platform="youtube_short",
            fields={"caption": "Alte Caption"},
            source_command_body="youtube_short ruhiger morgen",
            chat_id="22",
            user_id="33",
        )
    )

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 12,
            "callback_query": {
                "id": "cbq-caption-regenerate",
                "from": {"id": 33, "username": "julia"},
                "data": "content_ops:regenerate:job129",
                "message": {
                    "message_id": 22,
                    "text": "Caption",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert result.was_executed is False
    assert "Caption" in result.formatter_payload.message_text
    assert "ersetzen" in result.formatter_payload.message_text.lower()


def test_command_without_platform_and_without_active_proposal_requests_platform() -> None:
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    request_flow = RequestFlowService(execution_service)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 5,
            "message": {
                "message_id": 15,
                "text": "/vollauto morgenroutine",
                "chat": {"id": 23, "type": "private"},
                "from": {"id": 34, "username": "julia"},
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is False
    assert result.decision == "not_a_command"
    assert result.formatter_payload.response_reply_markup is not None


def test_menu_result_includes_keyboard_refresh_message() -> None:
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    request_flow = RequestFlowService(execution_service)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 6,
            "message": {
                "message_id": 16,
                "text": "/menu",
                "chat": {"id": 23, "type": "private"},
                "from": {"id": 34, "username": "julia"},
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "menu"
    assert result.formatter_payload.message_text == "⌨️ Menü wird aktualisiert."
    assert result.formatter_payload.response_reply_markup == {"remove_keyboard": True}
    assert result.formatter_payload.additional_messages
    assert result.formatter_payload.additional_messages[0].text == "⌨️ Menü aktualisiert."
    assert result.formatter_payload.additional_messages[0].reply_markup == PERSISTENT_MENU_REPLY_MARKUP
    assert result.formatter_payload.additional_messages[1].text == "☰ Menü\n\nWähle eine Aktion."
    assert result.formatter_payload.additional_messages[1].reply_markup == MENU_OVERLAY_REPLY_MARKUP


def test_menu_and_persistent_keyboard_both_show_modus() -> None:
    persistent_buttons = [button["text"] for row in PERSISTENT_MENU_REPLY_MARKUP["keyboard"] for button in row]
    overlay_buttons = [button["text"] for row in MENU_OVERLAY_REPLY_MARKUP["inline_keyboard"] for button in row]

    assert "🎯 Modus" in persistent_buttons
    assert "🎯 Modus" in overlay_buttons


def test_menu_modus_callback_uses_existing_modus_flow() -> None:
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    request_flow = RequestFlowService(execution_service)

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 8,
            "callback_query": {
                "id": "cbq-modus",
                "from": {"id": 34, "username": "julia"},
                "data": "menu:modus",
                "message": {
                    "message_id": 18,
                    "text": "☰ Menü",
                    "chat": {"id": 23, "type": "private"},
                },
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "modus"
    assert result.formatter_payload.command_name == "modus"
    assert "Plattform wählen" in (result.formatter_payload.message_text or "")


def test_custom_platform_prompt_is_shown_by_formatter() -> None:
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    request_flow = RequestFlowService(execution_service)
    formatter = ResponseFormatterService()

    handoff = build_telegram_entry_handoff(
        {
            "update_id": 7,
            "message": {
                "message_id": 17,
                "text": "/vollauto morgenroutine",
                "chat": {"id": 23, "type": "private"},
                "from": {"id": 34, "username": "julia"},
            },
        },
        _ctx(),
    )
    result = request_flow.handle_telegram_entry_handoff(handoff)
    formatted = formatter.format_request_flow_result(result)

    assert "vollauto" in formatted.text.lower() or "plattform" in formatted.text.lower()
    assert formatted.reply_markup is not None
