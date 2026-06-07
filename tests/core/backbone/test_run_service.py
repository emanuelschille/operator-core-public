from operator_core.core.backbone.repositories import InMemoryRunRepository
from operator_core.core.backbone.run_service import RunService
from operator_core.core.backbone.statuses import RunStatus


def test_create_and_complete_run() -> None:
    service = RunService(InMemoryRunRepository())

    run = service.create_run(
        job_id="job_1",
        project_key="everydayengel",
        module_name="backbone.execution",
        request_id="req_1",
        input_snapshot={"text": "hello"},
    )
    running = service.mark_running(run.run_id)
    succeeded = service.mark_succeeded(run.run_id, output_snapshot={"ok": True})

    assert run.retry_count == 0
    assert running.status == RunStatus.RUNNING
    assert running.started_at is not None
    assert succeeded.status == RunStatus.SUCCEEDED
    assert succeeded.finished_at is not None
    assert succeeded.duration_ms is not None
    assert succeeded.output_snapshot == {"ok": True}


def test_retry_count_increments() -> None:
    service = RunService(InMemoryRunRepository())

    first = service.create_run(job_id="job_1", project_key="everydayengel", module_name="mod", request_id="req")
    second = service.create_run(job_id="job_1", project_key="everydayengel", module_name="mod", request_id="req")

    assert first.retry_count == 0
    assert second.retry_count == 1


def test_run_failure_records_error() -> None:
    service = RunService(InMemoryRunRepository())

    run = service.create_run(job_id="job_1", project_key="everydayengel", module_name="mod", request_id="req")
    service.mark_running(run.run_id)
    failed = service.mark_failed(run.run_id, error_detail="boom")

    assert failed.status == RunStatus.FAILED
    assert failed.error_detail == "boom"
    assert failed.finished_at is not None
