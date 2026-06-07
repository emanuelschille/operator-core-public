"""T4 — /confirm and /reject routing for the execution-level confirmation gate.

These exercise the full Telegram handoff: a high-impact command parks for
confirmation, then /confirm resumes to a real execution and /reject terminates
without a business write. The existing proactive confirm/reject path must keep
working (no pending Job -> falls through unchanged).
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
from operator_core.core.backbone.statuses import JobStatus
from operator_core.core.content_ops.models import ContentOpResult
from operator_core.core.content_ops.platform_mode_store import PlatformModeStore
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.core.response_formatter.service import ResponseFormatterService
from operator_core.interfaces.telegram.entry_flow import build_telegram_entry_handoff


class _ContentStub:
    """Minimal content_ops lane; records whether the protected write ran."""

    def __init__(self) -> None:
        self.handled = 0

    def handle(self, *, project_key, action_type, command_body):
        self.handled += 1
        return ContentOpResult(
            lane_name="content_ops",
            project_key=project_key,
            action_type=action_type,
            command_body=command_body,
            title="Voll Auto",
            summary="generiert.",
            items=("Serie/Thema: Test",),
            platform="tiktok",
            openai_used=False,
        )

    def supports(self, action_type):
        return action_type == "vollauto"

    def can_use_foundation_backed_vollauto(self):
        return False

    def resolve_platform_hint(self, command_body):
        return "", command_body


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
        },
    )


def _build():
    job_repo = InMemoryJobRepository()
    exec_svc = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    stub = _ContentStub()
    exec_svc.content_ops_service = stub  # type: ignore[assignment]
    # Platform mode set so /vollauto resolves a platform and reaches the gate.
    platform_store = PlatformModeStore()
    platform_store.set_mode(chat_id="22", user_id="33", platform="tiktok")
    return RequestFlowService(exec_svc, platform_mode_store=platform_store), job_repo, stub


def _msg(text: str):
    return build_telegram_entry_handoff(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": text,
                "chat": {"id": 22, "type": "private"},
                "from": {"id": 33, "username": "julia"},
            },
        },
        _ctx(),
    )


def test_high_impact_command_requests_confirmation() -> None:
    svc, job_repo, stub = _build()

    result = svc.handle_telegram_entry_handoff(_msg("/vollauto morgenroutine"))

    assert result.decision == "confirmation_required"
    assert result.was_executed is False
    assert stub.handled == 0
    waiting = [j for j in job_repo.list_by_project("everydayengel") if j.status == JobStatus.WAITING_FOR_APPROVAL]
    assert len(waiting) == 1


def test_confirm_resumes_and_executes() -> None:
    svc, job_repo, stub = _build()
    svc.handle_telegram_entry_handoff(_msg("/vollauto morgenroutine"))

    result = svc.handle_telegram_entry_handoff(_msg("/confirm"))

    assert result.decision == "confirmed"
    assert stub.handled == 1
    completed = [j for j in job_repo.list_by_project("everydayengel") if j.status == JobStatus.COMPLETED]
    assert len(completed) == 1


def test_reject_terminates_without_executing() -> None:
    svc, job_repo, stub = _build()
    svc.handle_telegram_entry_handoff(_msg("/vollauto morgenroutine"))

    result = svc.handle_telegram_entry_handoff(_msg("/reject"))

    assert result.decision == "rejected"
    assert stub.handled == 0
    rejected = [j for j in job_repo.list_by_project("everydayengel") if j.status == JobStatus.REJECTED]
    assert len(rejected) == 1


def test_confirm_without_pending_confirmation_falls_through() -> None:
    svc, job_repo, stub = _build()

    # no prior high-impact request -> nothing to confirm -> existing fall-through
    result = svc.handle_telegram_entry_handoff(_msg("/confirm"))

    assert result.was_executed is False
    assert result.decision == "unknown_command"


def test_formatter_renders_confirmation_states() -> None:
    fmt = ResponseFormatterService()
    svc, job_repo, stub = _build()

    req = svc.handle_telegram_entry_handoff(_msg("/vollauto morgenroutine"))
    req_text = fmt.format_request_flow_result(req).text
    assert req_text.startswith("⏳ Bestätigung erforderlich")
    assert "/confirm" in req_text and "/reject" in req_text

    confirmed = svc.handle_telegram_entry_handoff(_msg("/confirm"))
    confirmed_text = fmt.format_request_flow_result(confirmed).text
    assert "Voll Auto" in confirmed_text  # the executed content is shown

    svc2, _, _ = _build()
    svc2.handle_telegram_entry_handoff(_msg("/vollauto morgenroutine"))
    rejected = svc2.handle_telegram_entry_handoff(_msg("/reject"))
    rejected_text = fmt.format_request_flow_result(rejected).text
    assert rejected_text.startswith("❌ Abgelehnt")
