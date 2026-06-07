"""
Tests for the live /idea correction capture flow.

Covers:
- IdeaCorrectionService.record_correction() → store + event log
- content_ops:accept callback → accepted_as_is recorded, edit text confirmed
- content_ops:reject callback → reason tag keyboard returned
- content_ops:reject_tag callback → rejected + tag recorded, edit text confirmed
- build_content_ops_reply_markup for idea includes accept/reject row
- ContentProposal.commercial_class populated from output snapshot
- Invalid reject_tag falls back to CorrectionReasonTag.none
- Missing IdeaCorrectionService logs warning and does not crash
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operator_core.bootstrap import BootstrapContext
from operator_core.config import AirtableSettings, AppSettings, OpenAISettings, Settings, TelegramSettings
from operator_core.core.backbone.models import RequestContext
from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.execution_service import ExecutionService
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.repositories import (
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryRunRepository,
)
from operator_core.core.backbone.run_service import RunService
from operator_core.core.content_ops.correction_capture import (
    CommercialClass,
    CorrectionCaptureStore,
    CorrectionReasonTag,
    CorrectionStatus,
    IdeaCorrectionService,
    REASON_TAG_LABELS,
)
from operator_core.core.content_ops.proposal_store import ContentProposal, ContentProposalStore
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.core.response_formatter.service import ResponseFormatterService
from operator_core.interfaces.telegram.entry_flow import build_telegram_entry_handoff


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_execution_service() -> ExecutionService:
    return ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )


def _make_correction_service() -> tuple[IdeaCorrectionService, CorrectionCaptureStore, MagicMock]:
    store = CorrectionCaptureStore()
    event_log = MagicMock()
    svc = IdeaCorrectionService(correction_store=store, event_log_service=event_log)
    return svc, store, event_log


def _make_idea_proposal(
    proposal_id: str = "prop-idea-1",
    commercial_class: str | None = "trust_building",
    title_raw: str = "Beim Kochen merke ich, dass ich sitzen muss.",
) -> ContentProposal:
    return ContentProposal(
        proposal_id=proposal_id,
        project_key="everydayengel",
        action_type="idea",
        platform="tiktok",
        fields={"title_raw": title_raw},
        source_command_body="beim Kochen schwindelig",
        commercial_class=commercial_class,
        chat_id="22",
        user_id="33",
    )


def _callback_handoff(callback_data: str, proposal_id: str, update_id: int = 1) -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": update_id,
            "callback_query": {
                "id": f"cbq-{update_id}",
                "from": {"id": 33, "username": "julia"},
                "data": callback_data,
                "message": {
                    "message_id": 100 + update_id,
                    "text": "💡 Idee",
                    "chat": {"id": 22, "type": "private"},
                },
            },
        },
        _ctx(),
    )


def _flatten_markup_texts(markup: dict | None) -> list[str]:
    assert markup is not None
    return [btn["text"] for row in markup["inline_keyboard"] for btn in row]


def _flatten_markup_callbacks(markup: dict | None) -> list[str]:
    assert markup is not None
    return [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]


# ---------------------------------------------------------------------------
# IdeaCorrectionService unit tests
# ---------------------------------------------------------------------------

def test_idea_correction_service_writes_to_store() -> None:
    svc, store, _ = _make_correction_service()
    record = svc.record_correction(
        project_key="everydayengel",
        proposal_id="prop-001",
        prompt="beim Kochen",
        bot_output="Beim Kochen merke ich plötzlich, dass ich sitzen muss.",
        commercial_class="trust_building",
        status=CorrectionStatus.accepted_as_is,
    )
    assert store.get(record.record_id) is record
    assert record.status is CorrectionStatus.accepted_as_is
    assert record.commercial_class is CommercialClass.trust_building
    assert record.reason_tag is CorrectionReasonTag.none


def test_idea_correction_service_calls_event_log() -> None:
    svc, _, event_log = _make_correction_service()
    svc.record_correction(
        project_key="everydayengel",
        proposal_id="prop-002",
        prompt="Rücken schmerzt",
        bot_output="Rücken meldet sich nach zehn Minuten stehen.",
        commercial_class=None,
        status=CorrectionStatus.rejected,
        reason_tag=CorrectionReasonTag.tone_off,
    )
    event_log.log_event.assert_called_once()
    call_kwargs = event_log.log_event.call_args.kwargs
    assert call_kwargs["entity_type"] == "idea"
    assert call_kwargs["entity_id"] == "prop-002"
    assert call_kwargs["event_type"] == "idea.correction_recorded"
    assert call_kwargs["payload_json"]["status"] == "rejected"
    assert call_kwargs["payload_json"]["reason_tag"] == "tone_off"


def test_idea_correction_service_invalid_commercial_class_is_silently_dropped() -> None:
    svc, store, _ = _make_correction_service()
    record = svc.record_correction(
        project_key="everydayengel",
        proposal_id="prop-003",
        prompt="test",
        bot_output="output",
        commercial_class="invalid_class_that_does_not_exist",
        status=CorrectionStatus.accepted_as_is,
    )
    assert record.commercial_class is None
    assert store.get(record.record_id) is record


def test_idea_correction_service_rejected_with_reason_tag() -> None:
    svc, store, _ = _make_correction_service()
    record = svc.record_correction(
        project_key="everydayengel",
        proposal_id="prop-004",
        prompt="Schlafen mit Bauch",
        bot_output="Das Schlafen hat sich verändert.",
        commercial_class="product_near",
        status=CorrectionStatus.rejected,
        reason_tag=CorrectionReasonTag.not_julia,
    )
    assert record.status is CorrectionStatus.rejected
    assert record.reason_tag is CorrectionReasonTag.not_julia
    assert record.commercial_class is CommercialClass.product_near


# ---------------------------------------------------------------------------
# content_ops:accept callback → accepted_as_is
# ---------------------------------------------------------------------------

def test_accept_callback_records_accepted_and_edits_message() -> None:
    proposal_store = ContentProposalStore()
    proposal = _make_idea_proposal(proposal_id="prop-acc-1")
    proposal_store.save(proposal)

    correction_svc, corr_store, _ = _make_correction_service()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    )

    handoff = _callback_handoff("content_ops:accept:prop-acc-1", "prop-acc-1", update_id=20)
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert result.formatter_payload.callback_answer_text == "Gespeichert ✓"
    assert "Beim Kochen merke ich, dass ich sitzen muss." in (result.formatter_payload.edit_message_text or "")
    assert "✅" in (result.formatter_payload.edit_message_text or "")
    assert "gut bewertet" in (result.formatter_payload.edit_message_text or "")
    assert _flatten_markup_texts(result.formatter_payload.edit_reply_markup) == [
        "📝 Aus Idee Entwurf erstellen",
        "✖️ Verwerfen",
        "↩ Bewertung ändern",
    ]
    assert _flatten_markup_callbacks(result.formatter_payload.edit_reply_markup) == [
        "content_ops:idea_to_draft:prop-acc-1",
        "content_ops:dismiss:prop-acc-1",
        "content_ops:rerate:prop-acc-1",
    ]
    assert "✅ Gut" not in _flatten_markup_texts(result.formatter_payload.edit_reply_markup)
    assert "❌ Nicht gut" not in _flatten_markup_texts(result.formatter_payload.edit_reply_markup)

    records = corr_store.list_by_action("everydayengel", "idea")
    assert len(records) == 1
    assert records[0].status is CorrectionStatus.accepted_as_is
    assert records[0].reason_tag is CorrectionReasonTag.none
    assert records[0].proposal_id == "prop-acc-1"


def test_accept_callback_bot_output_from_title_raw() -> None:
    proposal_store = ContentProposalStore()
    proposal = _make_idea_proposal(proposal_id="prop-acc-2", title_raw="Beim Kochen — kurz hinsetzen.")
    proposal_store.save(proposal)

    correction_svc, corr_store, _ = _make_correction_service()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    )

    _callback_handoff("content_ops:accept:prop-acc-2", "prop-acc-2", update_id=21)
    request_flow.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:accept:prop-acc-2", "prop-acc-2", update_id=21)
    )

    records = corr_store.list_by_action("everydayengel", "idea")
    assert records[0].bot_output == "Beim Kochen — kurz hinsetzen."
    assert records[0].prompt == "beim Kochen schwindelig"


def test_accept_callback_persists_visible_idea_from_raw_item_not_internal_summary() -> None:
    visible_idea = "Beim Kochen merke ich plötzlich, dass ich mich hinsetzen muss."
    proposal_store = ContentProposalStore()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)
    proposal = request_flow._build_content_proposal(  # type: ignore[attr-defined]
        proposal_id="prop-visible-raw",
        project_key="everydayengel",
        snapshot={
            "action_type": "idea",
            "platform": "tiktok",
            "command_body": "beim Kochen plötzlich sitzen wegen Schwindel",
            "summary": "Duplikatsrisiko erkannt. Stärkster frischer Angle ausgewählt.",
            "items": (visible_idea,),
            "commercial_class": "trust_building",
        },
        request_context=RequestContext(
            request_id="req-visible-raw",
            project_key="everydayengel",
            source_type="telegram",
            source_user_id="33",
            source_chat_id="22",
            command_name="idea",
            command_body="beim Kochen plötzlich sitzen wegen Schwindel",
        ),
    )
    assert proposal is not None
    proposal_store.save(proposal)

    correction_svc, corr_store, _ = _make_correction_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    )

    result = request_flow.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:accept:prop-visible-raw", "prop-visible-raw", update_id=22)
    )

    records = corr_store.list_by_action("everydayengel", "idea")
    assert records[0].bot_output == visible_idea
    assert "Duplikatsrisiko" not in records[0].bot_output
    assert visible_idea in (result.formatter_payload.edit_message_text or "")


def test_accept_callback_persists_visible_idea_from_antwort_item() -> None:
    visible_idea = "Im Supermarkt merke ich plötzlich, dass ich eine Pause brauche."
    proposal_store = ContentProposalStore()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)
    proposal = request_flow._build_content_proposal(  # type: ignore[attr-defined]
        proposal_id="prop-visible-antwort",
        project_key="everydayengel",
        snapshot={
            "action_type": "idea",
            "platform": "tiktok",
            "command_body": "im Supermarkt plötzlich Pause brauchen",
            "summary": "Duplikatsrisiko erkannt. Stärkster frischer Angle ausgewählt.",
            "items": (f"Antwort: {visible_idea}",),
        },
        request_context=RequestContext(
            request_id="req-visible-antwort",
            project_key="everydayengel",
            source_type="telegram",
            source_user_id="33",
            source_chat_id="22",
            command_name="idea",
            command_body="im Supermarkt plötzlich Pause brauchen",
        ),
    )
    assert proposal is not None
    proposal_store.save(proposal)

    correction_svc, corr_store, _ = _make_correction_service()
    RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    ).handle_telegram_entry_handoff(
        _callback_handoff("content_ops:accept:prop-visible-antwort", "prop-visible-antwort", update_id=23)
    )

    records = corr_store.list_by_action("everydayengel", "idea")
    assert records[0].bot_output == visible_idea


# ---------------------------------------------------------------------------
# content_ops:reject callback → reason tag keyboard
# ---------------------------------------------------------------------------

def test_reject_callback_returns_reason_tag_keyboard() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-rej-1"))

    execution_service = _make_execution_service()
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    handoff = _callback_handoff("content_ops:reject:prop-rej-1", "prop-rej-1", update_id=30)
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert result.formatter_payload.callback_answer_text == "Grund auswählen"
    assert "Beim Kochen merke ich, dass ich sitzen muss." in (result.formatter_payload.edit_message_text or "")
    assert "❌" in (result.formatter_payload.edit_message_text or "")
    assert "Idee nicht gut" in (result.formatter_payload.edit_message_text or "")
    assert "Warum ist die Idee nicht gut?" in (result.formatter_payload.edit_message_text or "")
    assert "Bitte einen Grund auswählen:" in (result.formatter_payload.edit_message_text or "")
    assert result.formatter_payload.edit_message_text != result.formatter_payload.callback_answer_text

    markup = result.formatter_payload.edit_reply_markup
    assert markup is not None
    all_buttons = [btn for row in markup["inline_keyboard"] for btn in row]
    callback_values = [btn["callback_data"] for btn in all_buttons]
    button_texts = [btn["text"] for btn in all_buttons]

    # All 11 reason buttons must be present without oversized Telegram callback payloads.
    expected_tags = {t.value for t in CorrectionReasonTag if t != CorrectionReasonTag.none}
    reason_callbacks = [cd for cd in callback_values if cd.startswith("content_ops:rt:")]
    assert len(reason_callbacks) == len(expected_tags)
    assert all(len(cd.encode("utf-8")) <= 64 for cd in reason_callbacks)

    # German labels from REASON_TAG_LABELS
    assert "Zu wörtlich" in button_texts
    assert "Ton falsch" in button_texts
    assert "Nicht Julia" in button_texts
    assert "↩ Zurück" in button_texts
    assert f"content_ops:rerate:prop-rej-1" in callback_values


def test_reject_callback_buttons_reference_correct_proposal_id() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-rej-2"))

    execution_service = _make_execution_service()
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    result = request_flow.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:reject:prop-rej-2", "prop-rej-2", update_id=31)
    )

    markup = result.formatter_payload.edit_reply_markup
    assert markup is not None
    all_callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert all("prop-rej-2" in cd for cd in all_callbacks)
    assert all(
        cd.startswith("content_ops:rt:prop-rej-2:")
        or cd == "content_ops:rerate:prop-rej-2"
        for cd in all_callbacks
    )


def test_reject_callback_uses_telegram_safe_callbacks_with_realistic_proposal_id() -> None:
    proposal_id = "123e4567-e89b-12d3-a456-426614174000"
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id=proposal_id))

    execution_service = _make_execution_service()
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    result = request_flow.handle_telegram_entry_handoff(
        _callback_handoff(f"content_ops:reject:{proposal_id}", proposal_id, update_id=33)
    )

    callbacks = _flatten_markup_callbacks(result.formatter_payload.edit_reply_markup)
    assert callbacks
    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)
    assert f"content_ops:rt:{proposal_id}:gwp" in callbacks


def test_reject_back_button_restores_rating_state() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-rej-back"))

    execution_service = _make_execution_service()
    request_flow = RequestFlowService(execution_service, content_proposal_store=proposal_store)

    result = request_flow.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:rerate:prop-rej-back", "prop-rej-back", update_id=32)
    )

    assert result.decision == "content_ops_callback"
    assert "Beim Kochen merke ich, dass ich sitzen muss." in (result.formatter_payload.edit_message_text or "")
    texts = _flatten_markup_texts(result.formatter_payload.edit_reply_markup)
    callbacks = _flatten_markup_callbacks(result.formatter_payload.edit_reply_markup)
    assert "✅ Gut" in texts
    assert "❌ Nicht gut" in texts
    assert "↩ Zurück" not in texts
    assert "content_ops:accept:prop-rej-back" in callbacks
    assert "content_ops:reject:prop-rej-back" in callbacks


# ---------------------------------------------------------------------------
# content_ops:reject_tag callback → rejected + tag recorded
# ---------------------------------------------------------------------------

def test_reject_tag_callback_records_rejection_with_tag() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-rtag-1"))

    correction_svc, corr_store, _ = _make_correction_service()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    )

    handoff = _callback_handoff("content_ops:reject_tag:prop-rtag-1:tone_off", "prop-rtag-1", update_id=40)
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert result.formatter_payload.callback_answer_text == "Gespeichert ✓"
    assert "Beim Kochen merke ich, dass ich sitzen muss." in (result.formatter_payload.edit_message_text or "")
    assert "✖️" in (result.formatter_payload.edit_message_text or "")
    assert "Ton falsch" in (result.formatter_payload.edit_message_text or "")
    assert _flatten_markup_texts(result.formatter_payload.edit_reply_markup) == [
        "📝 Aus Idee Entwurf erstellen",
        "✖️ Verwerfen",
        "↩ Bewertung ändern",
    ]
    assert _flatten_markup_callbacks(result.formatter_payload.edit_reply_markup) == [
        "content_ops:idea_to_draft:prop-rtag-1",
        "content_ops:dismiss:prop-rtag-1",
        "content_ops:rerate:prop-rtag-1",
    ]
    assert "✅ Gut" not in _flatten_markup_texts(result.formatter_payload.edit_reply_markup)
    assert "❌ Nicht gut" not in _flatten_markup_texts(result.formatter_payload.edit_reply_markup)
    assert "Ton falsch" not in _flatten_markup_texts(result.formatter_payload.edit_reply_markup)

    records = corr_store.list_by_action("everydayengel", "idea")
    assert len(records) == 1
    assert records[0].status is CorrectionStatus.rejected
    assert records[0].reason_tag is CorrectionReasonTag.tone_off


def test_compact_reject_reason_callback_records_rejection_with_tag() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-rtag-compact"))

    correction_svc, corr_store, _ = _make_correction_service()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    )

    handoff = _callback_handoff("content_ops:rt:prop-rtag-compact:to", "prop-rtag-compact", update_id=41)
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert "Ton falsch" in (result.formatter_payload.edit_message_text or "")
    records = corr_store.list_by_action("everydayengel", "idea")
    assert len(records) == 1
    assert records[0].status is CorrectionStatus.rejected
    assert records[0].reason_tag is CorrectionReasonTag.tone_off


def test_rerate_callback_reopens_rating_options_for_same_idea() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-rerate-1"))

    correction_svc, _, _ = _make_correction_service()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    )

    result = request_flow.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:rerate:prop-rerate-1", "prop-rerate-1", update_id=42)
    )

    assert result.decision == "content_ops_callback"
    assert result.formatter_payload.callback_answer_text == "Bewertung ändern"
    assert "Beim Kochen merke ich, dass ich sitzen muss." in (result.formatter_payload.edit_message_text or "")
    texts = _flatten_markup_texts(result.formatter_payload.edit_reply_markup)
    callbacks = _flatten_markup_callbacks(result.formatter_payload.edit_reply_markup)
    assert "📝 Aus Idee Entwurf erstellen" in texts
    assert "✖️ Verwerfen" in texts
    assert "✅ Gut" in texts
    assert "❌ Nicht gut" in texts
    assert "↩ Bewertung ändern" not in texts
    assert "content_ops:accept:prop-rerate-1" in callbacks
    assert "content_ops:reject:prop-rerate-1" in callbacks


def test_changed_rating_supersedes_previous_effective_record() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-change-1"))

    correction_svc, corr_store, _ = _make_correction_service()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    )

    request_flow.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:accept:prop-change-1", "prop-change-1", update_id=43)
    )
    request_flow.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:reject_tag:prop-change-1:tone_off", "prop-change-1", update_id=44)
    )

    records = corr_store.list_by_action("everydayengel", "idea")
    assert len(records) == 2
    assert records[0].status is CorrectionStatus.accepted_as_is
    assert records[1].status is CorrectionStatus.rejected
    assert records[1].reason_tag is CorrectionReasonTag.tone_off
    assert records[1].supersedes_record_id == records[0].record_id
    latest = corr_store.latest_for_proposal("everydayengel", "prop-change-1")
    assert latest is records[1]


def test_reject_tag_callback_each_tag_produces_correct_label() -> None:
    for tag in CorrectionReasonTag:
        if tag == CorrectionReasonTag.none:
            continue
        proposal_store = ContentProposalStore()
        pid = f"prop-tag-{tag.value}"
        proposal_store.save(_make_idea_proposal(proposal_id=pid))

        correction_svc, _, _ = _make_correction_service()
        execution_service = _make_execution_service()
        request_flow = RequestFlowService(
            execution_service,
            content_proposal_store=proposal_store,
            idea_correction_service=correction_svc,
        )

        result = request_flow.handle_telegram_entry_handoff(
            _callback_handoff(f"content_ops:reject_tag:{pid}:{tag.value}", pid, update_id=hash(tag.value) % 1000 + 50)
        )
        expected_label = REASON_TAG_LABELS[tag.value]
        assert expected_label in (result.formatter_payload.edit_message_text or ""), (
            f"Label '{expected_label}' not in edit text for tag {tag.value}"
        )


def test_reject_tag_invalid_tag_falls_back_to_none() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-invalid-tag"))

    correction_svc, corr_store, _ = _make_correction_service()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    )

    handoff = _callback_handoff("content_ops:reject_tag:prop-invalid-tag:total_nonsense", "prop-invalid-tag", update_id=45)
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    records = corr_store.list_by_action("everydayengel", "idea")
    assert len(records) == 1
    assert records[0].status is CorrectionStatus.rejected
    assert records[0].reason_tag is CorrectionReasonTag.none


# ---------------------------------------------------------------------------
# Missing correction service → graceful degradation
# ---------------------------------------------------------------------------

def test_accept_without_correction_service_does_not_crash() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-no-svc-1"))

    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        # idea_correction_service not passed → defaults to None
    )

    handoff = _callback_handoff("content_ops:accept:prop-no-svc-1", "prop-no-svc-1", update_id=60)
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert "✅" in (result.formatter_payload.edit_message_text or "")


def test_reject_tag_without_correction_service_does_not_crash() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-no-svc-2"))

    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
    )

    handoff = _callback_handoff("content_ops:reject_tag:prop-no-svc-2:too_loud", "prop-no-svc-2", update_id=61)
    result = request_flow.handle_telegram_entry_handoff(handoff)

    assert result.decision == "content_ops_callback"
    assert "Zu laut" in (result.formatter_payload.edit_message_text or "")


# ---------------------------------------------------------------------------
# build_content_ops_reply_markup — idea row includes accept/reject
# ---------------------------------------------------------------------------

def test_idea_reply_markup_has_accept_reject_row() -> None:
    formatter = ResponseFormatterService()
    markup = formatter.build_content_ops_reply_markup({
        "action_type": "idea",
        "proposal_id": "prop-markup-1",
        "openai_used": True,
    })
    assert markup is not None
    rows = markup["inline_keyboard"]
    assert len(rows) == 2
    row1_data = [btn["callback_data"] for btn in rows[0]]
    row2_data = [btn["callback_data"] for btn in rows[1]]
    assert "content_ops:idea_to_draft:prop-markup-1" in row1_data
    assert "content_ops:dismiss:prop-markup-1" in row1_data
    assert "content_ops:accept:prop-markup-1" in row2_data
    assert "content_ops:reject:prop-markup-1" in row2_data


def test_idea_reply_markup_accept_reject_button_labels() -> None:
    formatter = ResponseFormatterService()
    markup = formatter.build_content_ops_reply_markup({
        "action_type": "idea",
        "proposal_id": "prop-markup-2",
        "openai_used": True,
    })
    assert markup is not None
    row2_texts = [btn["text"] for btn in markup["inline_keyboard"][1]]
    assert "✅ Gut" in row2_texts
    assert "❌ Nicht gut" in row2_texts


# ---------------------------------------------------------------------------
# ContentProposal.commercial_class populated from output snapshot
# ---------------------------------------------------------------------------

def test_content_proposal_commercial_class_field_is_present() -> None:
    proposal = ContentProposal(
        proposal_id="prop-cc-1",
        project_key="everydayengel",
        action_type="idea",
        platform="tiktok",
        fields={},
        commercial_class="trust_building",
    )
    assert proposal.commercial_class == "trust_building"


def test_content_proposal_commercial_class_defaults_none() -> None:
    proposal = ContentProposal(
        proposal_id="prop-cc-2",
        project_key="everydayengel",
        action_type="idea",
        platform="tiktok",
        fields={},
    )
    assert proposal.commercial_class is None


def test_accept_callback_carries_commercial_class_into_correction_record() -> None:
    proposal_store = ContentProposalStore()
    proposal_store.save(_make_idea_proposal(proposal_id="prop-cc-flow", commercial_class="product_near"))

    correction_svc, corr_store, _ = _make_correction_service()
    execution_service = _make_execution_service()
    request_flow = RequestFlowService(
        execution_service,
        content_proposal_store=proposal_store,
        idea_correction_service=correction_svc,
    )

    request_flow.handle_telegram_entry_handoff(
        _callback_handoff("content_ops:accept:prop-cc-flow", "prop-cc-flow", update_id=70)
    )

    records = corr_store.list_by_action("everydayengel", "idea")
    assert records[0].commercial_class is CommercialClass.product_near
