from __future__ import annotations

from uuid import uuid4

from .models import Event, Job, Run
from .repositories import EventRepository


class EventLogService:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def log_job_created(self, job: Job) -> Event:
        return self._append(
            project_key=job.project_key,
            entity_type="job",
            entity_id=job.job_id,
            event_type="job.created",
            message=f"Job {job.job_id} created",
            payload_json={"status": job.status.value, "job_type": job.job_type},
        )

    def log_job_status_changed(self, job: Job, previous_status: str) -> Event:
        return self._append(
            project_key=job.project_key,
            entity_type="job",
            entity_id=job.job_id,
            event_type="job.status_changed",
            message=f"Job {job.job_id} status changed to {job.status.value}",
            payload_json={"previous_status": previous_status, "new_status": job.status.value},
        )

    def log_run_created(self, run: Run) -> Event:
        return self._append(
            project_key=run.project_key,
            entity_type="run",
            entity_id=run.run_id,
            event_type="run.created",
            message=f"Run {run.run_id} created",
            payload_json={"status": run.status.value, "job_id": run.job_id},
        )

    def log_run_started(self, run: Run) -> Event:
        return self._append(
            project_key=run.project_key,
            entity_type="run",
            entity_id=run.run_id,
            event_type="run.started",
            message=f"Run {run.run_id} started",
            payload_json={"status": run.status.value, "job_id": run.job_id},
        )

    def log_run_succeeded(self, run: Run) -> Event:
        return self._append(
            project_key=run.project_key,
            entity_type="run",
            entity_id=run.run_id,
            event_type="run.succeeded",
            message=f"Run {run.run_id} succeeded",
            payload_json={"status": run.status.value, "job_id": run.job_id},
        )

    def log_run_failed(self, run: Run) -> Event:
        return self._append(
            project_key=run.project_key,
            entity_type="run",
            entity_id=run.run_id,
            event_type="run.failed",
            message=f"Run {run.run_id} failed",
            payload_json={"status": run.status.value, "job_id": run.job_id, "error_detail": run.error_detail},
        )

    def log_error(
        self,
        *,
        project_key: str,
        entity_type: str,
        entity_id: str,
        message: str,
        payload_json: dict[str, object] | None = None,
    ) -> Event:
        return self._append(
            project_key=project_key,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type="system.error",
            message=message,
            payload_json=payload_json or {},
        )

    def log_event(
        self,
        *,
        project_key: str,
        entity_type: str,
        entity_id: str,
        event_type: str,
        message: str,
        payload_json: dict[str, object] | None = None,
    ) -> Event:
        return self._append(
            project_key=project_key,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            message=message,
            payload_json=payload_json or {},
        )

    def list_for_entity(self, project_key: str, entity_type: str, entity_id: str) -> list[Event]:
        return self.repository.list_for_entity(project_key, entity_type, entity_id)

    def _append(
        self,
        *,
        project_key: str,
        entity_type: str,
        entity_id: str,
        event_type: str,
        message: str,
        payload_json: dict[str, object],
    ) -> Event:
        event = Event(
            event_id=f"evt_{uuid4().hex}",
            project_key=project_key,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            message=message,
            payload_json=payload_json,
        )
        return self.repository.append(event)
