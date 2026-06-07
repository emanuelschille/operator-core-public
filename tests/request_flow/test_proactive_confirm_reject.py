from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from operator_core.proactive.pending_store import PendingProposal, ProactivePendingStore
from operator_core.proactive.checker import SuppressionStore
from operator_core.core.request_flow.service import RequestFlowService


def _make_proposal(sent_message_id: int = 500) -> PendingProposal:
    return PendingProposal(
        action_type="mark_stale",
        record_id="recDRAFT1",
        display_text="Alter Entwurf",
        proposed_stage="stale",
        days_stale=20,
        sent_message_id=sent_message_id,
        created_at=datetime.now(tz=timezone.utc),
    )


def _make_entry_handoff(command_name: str, reply_to_message_id: int | None = None) -> MagicMock:
    handoff = MagicMock()
    handoff.routed_command.is_command = True
    handoff.routed_command.is_known_command = False
    handoff.routed_command.command_name = command_name
    handoff.routed_command.command_body = ""
    handoff.project_context.project_key = "everydayengel"
    handoff.project_context.display_name = "EverydayEngel"
    handoff.response_shell.chat_id = 12345
    handoff.response_shell.reply_to_message_id = None
    handoff.request.user_id = 8168658274
    handoff.request.chat_id = 12345
    handoff.request.message_id = 999
    handoff.request.update_id = 1
    handoff.request.normalized_text = f"/{command_name}"
    handoff.request.raw_text = f"/{command_name}"
    if reply_to_message_id is not None:
        handoff.request.reply_context = MagicMock()
        handoff.request.reply_context.message_id = reply_to_message_id
    else:
        handoff.request.reply_context = None
    return handoff


class TestProactiveConfirm:
    def test_confirm_with_matching_reply_executes(self):
        pending_store = ProactivePendingStore()
        pending_store.put(_make_proposal(sent_message_id=500))

        execution_svc = MagicMock()
        exec_result = MagicMock()
        exec_result.job_id = "job-1"
        exec_result.run_id = "run-1"
        exec_result.job_status.value = "completed"
        exec_result.run_status = "success"
        exec_result.event_count = 1
        exec_result.result_summary = "ok"
        exec_result.error_summary = None
        exec_result.output_snapshot = {}
        execution_svc.execute_request.return_value = exec_result

        svc = RequestFlowService(
            execution_svc,
            pending_store=pending_store,
        )

        handoff = _make_entry_handoff("confirm", reply_to_message_id=500)
        result = svc.handle_telegram_entry_handoff(handoff)

        assert result.was_executed is True
        assert result.decision == "executed"
        call_ctx = execution_svc.execute_request.call_args[0][0]
        assert call_ctx.command_name == "mark_stale"
        assert call_ctx.command_body == "recDRAFT1"

    def test_confirm_without_reply_falls_through_to_unknown_command(self):
        pending_store = ProactivePendingStore()
        pending_store.put(_make_proposal(sent_message_id=500))

        svc = RequestFlowService(MagicMock(), pending_store=pending_store)
        handoff = _make_entry_handoff("confirm", reply_to_message_id=None)
        result = svc.handle_telegram_entry_handoff(handoff)

        # no reply_to_message_id → falls through to unknown_command
        assert result.was_executed is False
        assert result.decision == "unknown_command"

    def test_confirm_with_wrong_reply_id_falls_through(self):
        pending_store = ProactivePendingStore()
        pending_store.put(_make_proposal(sent_message_id=500))

        svc = RequestFlowService(MagicMock(), pending_store=pending_store)
        handoff = _make_entry_handoff("confirm", reply_to_message_id=999)
        result = svc.handle_telegram_entry_handoff(handoff)

        assert result.was_executed is False
        assert result.decision == "unknown_command"
        # proposal still in store (not consumed)
        assert pending_store.consume(500) is not None

    def test_reject_consumes_proposal_and_records_suppression(self):
        pending_store = ProactivePendingStore()
        pending_store.put(_make_proposal(sent_message_id=500))
        suppression = SuppressionStore(cooldown_hours=168)

        svc = RequestFlowService(
            MagicMock(),
            pending_store=pending_store,
            stale_rejection_suppression=suppression,
        )
        handoff = _make_entry_handoff("reject", reply_to_message_id=500)
        result = svc.handle_telegram_entry_handoff(handoff)

        assert result.was_executed is False
        assert result.decision == "proactive_rejected"
        assert suppression.is_suppressed("stale_rejected_recDRAFT1")
        # proposal consumed
        assert pending_store.consume(500) is None

    def test_confirm_proposal_consumed_after_use(self):
        pending_store = ProactivePendingStore()
        pending_store.put(_make_proposal(sent_message_id=500))

        execution_svc = MagicMock()
        exec_result = MagicMock()
        exec_result.job_id = "job-1"
        exec_result.run_id = "run-1"
        exec_result.job_status.value = "completed"
        exec_result.run_status = "success"
        exec_result.event_count = 1
        exec_result.result_summary = "ok"
        exec_result.error_summary = None
        exec_result.output_snapshot = {}
        execution_svc.execute_request.return_value = exec_result

        svc = RequestFlowService(execution_svc, pending_store=pending_store)
        handoff = _make_entry_handoff("confirm", reply_to_message_id=500)
        svc.handle_telegram_entry_handoff(handoff)

        # second /confirm with same reply_id → no proposal, falls through
        handoff2 = _make_entry_handoff("confirm", reply_to_message_id=500)
        result2 = svc.handle_telegram_entry_handoff(handoff2)
        assert result2.was_executed is False
