from __future__ import annotations

from typing import Any
from uuid import uuid4

from .models import Run, utc_now
from .repositories import RunRepository
from .statuses import RunStatus, ensure_run_transition


class RunNotFoundError(LookupError):
    """Raised when a run could not be found."""


class RunService:
    def __init__(self, repository: RunRepository) -> None:
        self.repository = repository

    def create_run(
        self,
        *,
        job_id: str,
        project_key: str,
        module_name: str,
        request_id: str | None,
        input_snapshot: dict[str, Any] | None = None,
        retry_count: int | None = None,
    ) -> Run:
        derived_retry = retry_count if retry_count is not None else self.get_latest_retry_count(job_id) + 1
        run = Run(
            run_id=f"run_{uuid4().hex}",
            job_id=job_id,
            project_key=project_key,
            module_name=module_name,
            status=RunStatus.PENDING,
            retry_count=derived_retry,
            input_snapshot=input_snapshot or {},
            request_id=request_id,
        )
        return self.repository.create(run)

    def mark_running(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        ensure_run_transition(run.status, RunStatus.RUNNING)
        run.status = RunStatus.RUNNING
        run.started_at = utc_now()
        return self.repository.update(run)

    def mark_succeeded(self, run_id: str, output_snapshot: dict[str, Any] | None = None) -> Run:
        run = self.get_run(run_id)
        ensure_run_transition(run.status, RunStatus.SUCCEEDED)
        run.status = RunStatus.SUCCEEDED
        run.output_snapshot = output_snapshot or {}
        self._finish(run)
        return self.repository.update(run)

    def mark_failed(self, run_id: str, error_detail: str) -> Run:
        run = self.get_run(run_id)
        ensure_run_transition(run.status, RunStatus.FAILED)
        run.status = RunStatus.FAILED
        run.error_detail = error_detail
        self._finish(run)
        return self.repository.update(run)

    def list_runs_for_job(self, job_id: str) -> list[Run]:
        return self.repository.list_by_job(job_id)

    def get_latest_retry_count(self, job_id: str) -> int:
        runs = self.repository.list_by_job(job_id)
        if not runs:
            return -1
        return max(run.retry_count for run in runs)

    def get_run(self, run_id: str) -> Run:
        run = self.repository.get(run_id)
        if run is None:
            raise RunNotFoundError(f"run not found: {run_id}")
        return run

    @staticmethod
    def _finish(run: Run) -> None:
        finished_at = utc_now()
        run.finished_at = finished_at
        if run.started_at is None:
            run.started_at = finished_at
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
