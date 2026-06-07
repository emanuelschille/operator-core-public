import pytest

from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.models import RequestContext
from operator_core.core.backbone.repositories import InMemoryJobRepository
from operator_core.core.backbone.statuses import InvalidStatusTransition, JobStatus


@pytest.fixture()
def request_context() -> RequestContext:
    return RequestContext(
        request_id="req_1",
        project_key="everydayengel",
        source_type="telegram",
        source_user_id="u1",
        source_chat_id="c1",
        source_message_id="m1",
        command_name="start",
        request_text="hello",
    )


def test_create_job_from_request(request_context: RequestContext) -> None:
    service = JobService(InMemoryJobRepository())

    job = service.create_job_from_request(request_context, job_type="inbound_request", title="Inbound request")

    assert job.project_key == "everydayengel"
    assert job.status == JobStatus.PENDING
    assert job.request_id == "req_1"
    assert job.request_key == "everydayengel:telegram:c1:u1:m1:start"


def test_job_status_flow_and_related_updates(request_context: RequestContext) -> None:
    service = JobService(InMemoryJobRepository())
    job = service.create_job_from_request(request_context, job_type="inbound_request", title="Inbound request")

    in_progress = service.mark_in_progress(job.job_id)
    waiting = service.mark_waiting_for_input(job.job_id, summary="Need approval")
    service.attach_related_entity(job.job_id, entity_type="message", entity_id="msg_123")
    failed = service.mark_failed(job.job_id, error_summary="timed out", latest_run_id="run_1")

    assert in_progress.status == JobStatus.IN_PROGRESS
    assert waiting.status == JobStatus.WAITING_FOR_INPUT
    assert waiting.result_summary == "Need approval"
    assert failed.status == JobStatus.FAILED
    assert failed.latest_run_id == "run_1"
    assert failed.error_summary == "timed out"


def test_invalid_job_transition_raises(request_context: RequestContext) -> None:
    service = JobService(InMemoryJobRepository())
    job = service.create_job_from_request(request_context, job_type="inbound_request", title="Inbound request")

    with pytest.raises(InvalidStatusTransition):
        service.mark_completed(job.job_id, result_summary="done")
