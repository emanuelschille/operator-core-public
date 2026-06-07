from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.models import Job, Run
from operator_core.core.backbone.repositories import InMemoryEventRepository
from operator_core.core.backbone.statuses import JobStatus, RunStatus


def test_event_log_service_writes_expected_events() -> None:
    service = EventLogService(InMemoryEventRepository())
    job = Job(
        job_id="job_1",
        project_key="everydayengel",
        job_type="inbound_request",
        status=JobStatus.PENDING,
        title="Inbound",
        input_text="hello",
        context_json={},
    )
    run = Run(
        run_id="run_1",
        job_id="job_1",
        project_key="everydayengel",
        module_name="backbone.execution",
        status=RunStatus.RUNNING,
    )

    service.log_job_created(job)
    job.status = JobStatus.IN_PROGRESS
    service.log_job_status_changed(job, previous_status="pending")
    service.log_run_created(run)
    service.log_run_started(run)
    service.log_run_succeeded(run)

    job_events = service.list_for_entity("everydayengel", "job", "job_1")
    run_events = service.list_for_entity("everydayengel", "run", "run_1")

    assert [event.event_type for event in job_events] == ["job.created", "job.status_changed"]
    assert [event.event_type for event in run_events] == ["run.created", "run.started", "run.succeeded"]


def test_event_log_service_can_write_custom_event() -> None:
    service = EventLogService(InMemoryEventRepository())

    service.log_event(
        project_key="everydayengel",
        entity_type="job",
        entity_id="job_1",
        event_type="analysis.snapshot_built",
        message="Analysis snapshots prepared",
        payload_json={"snapshot_ids": ["as_1"]},
    )

    job_events = service.list_for_entity("everydayengel", "job", "job_1")

    assert [event.event_type for event in job_events] == ["analysis.snapshot_built"]
    assert job_events[0].payload_json["snapshot_ids"] == ["as_1"]
