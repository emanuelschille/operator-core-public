"""T1 — data model for the confirmation gate (approval_state + statuses)."""

import pytest

from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.models import ApprovalState, RequestContext
from operator_core.core.backbone.repositories import InMemoryJobRepository
from operator_core.core.backbone.statuses import (
    InvalidStatusTransition,
    JobStatus,
    ensure_job_transition,
)


@pytest.fixture()
def request_context() -> RequestContext:
    return RequestContext(
        request_id="req_1",
        project_key="everydayengel",
        source_type="telegram",
        source_user_id="u1",
        source_chat_id="c1",
        source_message_id="m1",
        command_name="vollauto",
        request_text="vollauto",
    )


def test_new_job_defaults_to_not_required_approval(request_context: RequestContext) -> None:
    service = JobService(InMemoryJobRepository())
    job = service.create_job_from_request(request_context, job_type="operator_request", title="x")
    assert job.approval_state == ApprovalState.NOT_REQUIRED


def test_status_enum_includes_waiting_for_approval_and_rejected() -> None:
    assert JobStatus.WAITING_FOR_APPROVAL.value == "waiting_for_approval"
    assert JobStatus.REJECTED.value == "rejected"


def test_approval_transitions_allowed() -> None:
    ensure_job_transition(JobStatus.PENDING, JobStatus.WAITING_FOR_APPROVAL)
    ensure_job_transition(JobStatus.WAITING_FOR_APPROVAL, JobStatus.IN_PROGRESS)
    ensure_job_transition(JobStatus.WAITING_FOR_APPROVAL, JobStatus.REJECTED)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (JobStatus.WAITING_FOR_APPROVAL, JobStatus.COMPLETED),
        (JobStatus.REJECTED, JobStatus.IN_PROGRESS),
        (JobStatus.COMPLETED, JobStatus.WAITING_FOR_APPROVAL),
    ],
)
def test_invalid_approval_transitions_raise(current: JobStatus, new: JobStatus) -> None:
    with pytest.raises(InvalidStatusTransition):
        ensure_job_transition(current, new)


def test_mark_waiting_for_approval_sets_state(request_context: RequestContext) -> None:
    service = JobService(InMemoryJobRepository())
    job = service.create_job_from_request(request_context, job_type="operator_request", title="x")

    waiting = service.mark_waiting_for_approval(job.job_id)

    assert waiting.status == JobStatus.WAITING_FOR_APPROVAL
    assert waiting.approval_state == ApprovalState.PENDING


def test_mark_approved_records_approval(request_context: RequestContext) -> None:
    service = JobService(InMemoryJobRepository())
    job = service.create_job_from_request(request_context, job_type="operator_request", title="x")
    service.mark_waiting_for_approval(job.job_id)

    approved = service.mark_approved(job.job_id)

    assert approved.approval_state == ApprovalState.APPROVED
    # status remains waiting_for_approval until execution resumes
    assert approved.status == JobStatus.WAITING_FOR_APPROVAL


def test_mark_rejected_is_terminal(request_context: RequestContext) -> None:
    service = JobService(InMemoryJobRepository())
    job = service.create_job_from_request(request_context, job_type="operator_request", title="x")
    service.mark_waiting_for_approval(job.job_id)

    rejected = service.mark_rejected(job.job_id)

    assert rejected.status == JobStatus.REJECTED
    assert rejected.approval_state == ApprovalState.REJECTED
