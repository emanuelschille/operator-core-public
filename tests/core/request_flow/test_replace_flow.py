"""Tests for the explicit Ersetzen (literal field replace) UI flows.

A. content_ops:regenerate callback → dialog → literal field replace on proposal
B. plan_demo:replace_field_select → field selection → plan_demo:replace_field → dialog → literal plan field patch
"""
from __future__ import annotations

from pathlib import Path

from operator_core.bootstrap import BootstrapContext
from operator_core.config import AirtableSettings, AppSettings, OpenAISettings, Settings, TelegramSettings
from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.execution_service import ExecutionService
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.repositories import (
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryRunRepository,
)
from operator_core.core.backbone.run_service import RunService
from operator_core.core.content_ops.proposal_store import ContentProposal, ContentProposalStore
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.core.response_formatter.service import ResponseFormatterService
from operator_core.integrations.daily_plan_service import TodayPlanSnapshot
from operator_core.interfaces.telegram.entry_flow import build_telegram_entry_handoff


def _bootstrap() -> BootstrapContext:
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


def _callback_handoff(callback_data: str, user_id: int = 33, chat_id: int = 22, message_id: int = 100) -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": 5000,
            "callback_query": {
                "id": "cbq-replace",
                "from": {"id": user_id, "username": "julia"},
                "data": callback_data,
                "message": {
                    "message_id": message_id,
                    "text": "📋 Tagesplan",
                    "chat": {"id": chat_id, "type": "private"},
                },
            },
        },
        _bootstrap(),
    )


def _message_handoff(text: str, user_id: int = 33, chat_id: int = 22) -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": 5001,
            "message": {
                "message_id": 101,
                "text": text,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": user_id, "username": "julia"},
            },
        },
        _bootstrap(),
    )


# ── A. Proposal replace ───────────────────────────────────────────────────────

def test_regenerate_callback_shows_replace_prompt_for_caption_proposal() -> None:
    store = ContentProposalStore()
    store.save(ContentProposal(
        proposal_id="job-cap-1",
        project_key="everydayengel",
        action_type="caption",
        platform="youtube_short",
        fields={"caption": "Alte Caption"},
        source_command_body="youtube_short morgen",
        chat_id="22",
        user_id="33",
    ))
    svc = RequestFlowService(
        ExecutionService(
            job_service=JobService(InMemoryJobRepository()),
            run_service=RunService(InMemoryRunRepository()),
            event_log_service=EventLogService(InMemoryEventRepository()),
        ),
        content_proposal_store=store,
    )

    result = svc.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:regenerate:job-cap-1")
    )

    assert result.decision == "content_ops_callback"
    assert result.was_executed is False
    assert "Caption" in result.formatter_payload.message_text
    assert "Womit willst du" in result.formatter_payload.message_text
    # Proposal still in store
    assert store.get("job-cap-1") is not None


def test_regenerate_callback_shows_field_specific_prompt_for_hook() -> None:
    store = ContentProposalStore()
    store.save(ContentProposal(
        proposal_id="job-hook-1",
        project_key="everydayengel",
        action_type="hook",
        platform="tiktok",
        fields={"hook": "Alter Hook"},
        source_command_body="tiktok morgen",
        chat_id="22",
        user_id="33",
    ))
    svc = RequestFlowService(
        ExecutionService(
            job_service=JobService(InMemoryJobRepository()),
            run_service=RunService(InMemoryRunRepository()),
            event_log_service=EventLogService(InMemoryEventRepository()),
        ),
        content_proposal_store=store,
    )

    result = svc.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:regenerate:job-hook-1")
    )

    assert "Hook" in result.formatter_payload.message_text
    assert "Womit willst du" in result.formatter_payload.message_text


def test_proposal_replace_reply_literally_replaces_field_and_keeps_buttons() -> None:
    store = ContentProposalStore()
    store.save(ContentProposal(
        proposal_id="job-cap-2",
        project_key="everydayengel",
        action_type="caption",
        platform="youtube_short",
        fields={"caption": "Alte Caption"},
        source_command_body="youtube_short morgen",
        chat_id="22",
        user_id="33",
    ))
    svc = RequestFlowService(
        ExecutionService(
            job_service=JobService(InMemoryJobRepository()),
            run_service=RunService(InMemoryRunRepository()),
            event_log_service=EventLogService(InMemoryEventRepository()),
        ),
        content_proposal_store=store,
    )
    formatter = ResponseFormatterService()

    # Step 1: click Ersetzen
    svc.handle_telegram_entry_handoff(_callback_handoff("content_ops:regenerate:job-cap-2"))

    # Step 2: send new value
    result = svc.handle_telegram_entry_handoff(_message_handoff("Neue Caption"))
    formatted = formatter.format_request_flow_result(result)

    assert result.decision == "executed"
    assert result.was_executed is True
    assert "Caption: Neue Caption" in formatted.text
    assert formatted.reply_markup is not None  # buttons stay
    # Proposal updated in store
    updated = store.get("job-cap-2")
    assert updated is not None
    assert updated.fields["caption"] == "Neue Caption"


def test_proposal_replace_does_not_call_openai() -> None:
    """Literal replacement must bypass AI completely."""
    store = ContentProposalStore()
    store.save(ContentProposal(
        proposal_id="job-cta-1",
        project_key="everydayengel",
        action_type="cta",
        platform="tiktok",
        fields={"cta": "Alter CTA"},
        source_command_body="tiktok test",
        chat_id="22",
        user_id="33",
    ))
    from operator_core.core.content_ops.service import ContentOpsService

    class _NoAIStub(ContentOpsService):
        def follow_up(self, **kwargs):  # type: ignore[override]
            raise AssertionError("follow_up must not be called for literal replace")

        def regenerate_proposal(self, **kwargs):  # type: ignore[override]
            raise AssertionError("regenerate_proposal must not be called for literal replace")

    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    execution_service.content_ops_service = _NoAIStub()  # type: ignore[attr-defined]
    svc = RequestFlowService(execution_service, content_proposal_store=store)

    svc.handle_telegram_entry_handoff(_callback_handoff("content_ops:regenerate:job-cta-1"))
    result = svc.handle_telegram_entry_handoff(_message_handoff("Neuer CTA"))

    assert result.was_executed is True
    assert store.get("job-cta-1").fields["cta"] == "Neuer CTA"  # type: ignore[union-attr]


# ── B. Plan field replace ─────────────────────────────────────────────────────

def test_plan_replace_button_exists_in_plan_markup() -> None:
    from operator_core.core.request_flow.service import _build_plan_demo_reply_markup
    markup = _build_plan_demo_reply_markup("rec-abc")
    buttons_flat = [btn for row in markup["inline_keyboard"] for btn in row]
    replace_btn = next((b for b in buttons_flat if "Ersetzen" in b["text"]), None)
    assert replace_btn is not None
    assert "plan_demo:replace_field_select:rec-abc" == replace_btn["callback_data"]


class _DailyPlanStub:
    def __init__(self) -> None:
        self.rows: dict[str, TodayPlanSnapshot] = {
            "rec-yt": TodayPlanSnapshot(
                record_id="rec-yt",
                decision="pending",
                platform="youtube_short",
                hook="Alter Hook",
                cta="Alter CTA",
                caption="Alte Caption",
            ),
        }
        self.patch_calls: list[dict] = []

    def get_plan_record(self, *, project_key: str, record_id: str) -> TodayPlanSnapshot:
        return self.rows[record_id]

    def list_today_plans(self, *, project_key: str, date: str) -> tuple[TodayPlanSnapshot, ...]:
        return tuple(self.rows.values())

    def patch_fields(self, *, project_key: str, record_id: str, fields: dict, current: TodayPlanSnapshot) -> TodayPlanSnapshot:
        self.patch_calls.append({"record_id": record_id, "fields": fields})
        from dataclasses import replace as dc_replace
        updated = dc_replace(current, **fields)
        self.rows[record_id] = updated
        return updated

    def update_decision(self, **kwargs: object) -> None:
        pass

    def autofill_selection(self, **kwargs: object) -> TodayPlanSnapshot:
        return self.rows["rec-yt"]

    def clear_selection(self, **kwargs: object) -> TodayPlanSnapshot:
        return self.rows["rec-yt"]


def _plan_service() -> tuple[RequestFlowService, _DailyPlanStub]:
    stub = _DailyPlanStub()
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    svc = RequestFlowService(
        execution_service,
        daily_plan_service=stub,  # type: ignore[arg-type]
    )
    return svc, stub


def test_replace_field_select_shows_field_selection_keyboard() -> None:
    svc, _ = _plan_service()

    result = svc.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:replace_field_select:rec-yt")
    )

    assert result.decision == "plan_demo_callback"
    assert result.formatter_payload.send_response is True
    buttons_flat = [
        btn
        for row in result.formatter_payload.response_reply_markup["inline_keyboard"]
        for btn in row
    ]
    labels = [b["text"] for b in buttons_flat]
    assert "Hook" in labels
    assert "CTA" in labels
    assert "Caption" in labels
    # Each button callback includes the record_id and field name
    hook_btn = next(b for b in buttons_flat if b["text"] == "Hook")
    assert hook_btn["callback_data"] == "plan_demo:replace_field:rec-yt:hook"


def test_replace_field_callback_saves_pending_and_asks_for_new_value() -> None:
    svc, _ = _plan_service()

    result = svc.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:replace_field:rec-yt:cta")
    )

    assert result.decision == "plan_demo_callback"
    assert result.formatter_payload.send_response is True
    assert "CTA" in result.formatter_payload.message_text
    assert "ersetzen" in result.formatter_payload.message_text.lower()


def test_plan_field_replace_patches_correct_field_and_returns_plan() -> None:
    svc, stub = _plan_service()
    formatter = ResponseFormatterService()

    # Step 1: select field
    svc.handle_telegram_entry_handoff(_callback_handoff("plan_demo:replace_field:rec-yt:cta"))

    # Step 2: send new value
    result = svc.handle_telegram_entry_handoff(_message_handoff("Neuer CTA"))
    formatted = formatter.format_request_flow_result(result)

    assert result.decision == "plan_demo_callback"
    assert "CTA ersetzt" in formatted.text or "Neuer CTA" in formatted.text
    assert len(stub.patch_calls) == 1
    assert stub.patch_calls[0]["fields"] == {"cta": "Neuer CTA"}
    assert stub.patch_calls[0]["record_id"] == "rec-yt"
    # Plan buttons are present in the reply
    assert formatted.reply_markup is not None
    buttons_flat = [btn for row in formatted.reply_markup["inline_keyboard"] for btn in row]
    assert any("Ersetzen" in b["text"] for b in buttons_flat)


def test_plan_field_replace_only_replaces_requested_field() -> None:
    """Hook and Caption must be unchanged when only CTA is replaced."""
    svc, stub = _plan_service()

    svc.handle_telegram_entry_handoff(_callback_handoff("plan_demo:replace_field:rec-yt:cta"))
    svc.handle_telegram_entry_handoff(_message_handoff("Neuer CTA"))

    updated = stub.rows["rec-yt"]
    assert updated.cta == "Neuer CTA"
    assert updated.hook == "Alter Hook"
    assert updated.caption == "Alte Caption"
