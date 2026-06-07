"""T3/T4 — confirmation gate in the execution path.

A high-impact request must park the Job in ``waiting_for_approval`` and write a
``confirmation_requested`` event WITHOUT running the executor (no business write).
Approval resumes execution to a real ``completed``; rejection is terminal with no write.
"""

from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.execution_service import ExecutionService, ExecutionStepResult
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.models import ApprovalState, RequestContext
from operator_core.core.backbone.repositories import (
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryRunRepository,
)
from operator_core.core.backbone.run_service import RunService
from operator_core.core.backbone.statuses import JobStatus


class SpyExecutor:
    """Stands in for the lane work; records whether the protected write ran."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request_context, job, run) -> ExecutionStepResult:
        self.calls += 1
        return ExecutionStepResult(
            output_snapshot={"did_write": True, "lane_name": "content_ops"},
            result_summary="executed",
            job_status=JobStatus.COMPLETED,
        )


def _build(executor):
    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    svc = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        executor=executor,
    )
    return svc, job_repo, run_repo, event_repo


def _ctx(command_name: str) -> RequestContext:
    return RequestContext(
        request_id="req_1",
        project_key="everydayengel",
        source_type="telegram",
        source_user_id="u1",
        source_chat_id="c1",
        source_message_id="m1",
        command_name=command_name,
        command_body="",
        request_text=command_name,
    )


def _events(event_repo, job_id):
    return [e.event_type for e in event_repo.list_for_entity("everydayengel", "job", job_id)]


def test_high_impact_request_is_gated_without_executing() -> None:
    spy = SpyExecutor()
    svc, job_repo, run_repo, event_repo = _build(spy)

    result = svc.execute_request(_ctx("vollauto"), job_type="operator_request", title="vollauto")

    assert spy.calls == 0, "no business write before approval"
    assert result.job_status == JobStatus.WAITING_FOR_APPROVAL
    assert result.approval_state == ApprovalState.PENDING
    job = job_repo.get(result.job_id)
    assert job.status == JobStatus.WAITING_FOR_APPROVAL
    assert "confirmation_requested" in _events(event_repo, result.job_id)


def test_non_high_impact_request_executes_normally() -> None:
    spy = SpyExecutor()
    svc, job_repo, run_repo, event_repo = _build(spy)

    result = svc.execute_request(_ctx("idea"), job_type="operator_request", title="idea")

    assert spy.calls == 1
    assert result.job_status == JobStatus.COMPLETED
    assert result.approval_state == ApprovalState.NOT_REQUIRED


def test_approval_resumes_execution_to_completed() -> None:
    spy = SpyExecutor()
    svc, job_repo, run_repo, event_repo = _build(spy)
    gated = svc.execute_request(_ctx("vollauto"), job_type="operator_request", title="vollauto")

    resumed = svc.resume_confirmed_job(gated.job_id)

    assert spy.calls == 1, "the protected write runs exactly once, after approval"
    assert resumed.job_status == JobStatus.COMPLETED
    job = job_repo.get(gated.job_id)
    assert job.approval_state == ApprovalState.APPROVED
    assert "confirmation_resolved" in _events(event_repo, gated.job_id)
    assert resumed.run_id, "a new run was created for the resumed execution"


def test_rejection_is_terminal_without_executing() -> None:
    spy = SpyExecutor()
    svc, job_repo, run_repo, event_repo = _build(spy)
    gated = svc.execute_request(_ctx("vollauto"), job_type="operator_request", title="vollauto")

    rejected = svc.reject_job(gated.job_id)

    assert spy.calls == 0, "no business write on rejection"
    assert rejected.job_status == JobStatus.REJECTED
    job = job_repo.get(gated.job_id)
    assert job.approval_state == ApprovalState.REJECTED
    assert "confirmation_resolved" in _events(event_repo, gated.job_id)
