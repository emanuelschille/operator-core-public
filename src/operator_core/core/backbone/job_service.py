from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import uuid4

from .models import ApprovalState, Job, RequestContext, utc_now
from .repositories import JobRepository
from .statuses import JobStatus, ensure_job_transition


class JobNotFoundError(LookupError):
    """Raised when a job could not be found."""


class JobService:
    def __init__(self, repository: JobRepository) -> None:
        self.repository = repository

    def create_job_from_request(
        self,
        request_context: RequestContext,
        job_type: str,
        title: str,
        *,
        priority: int = 0,
        context_json: dict[str, Any] | None = None,
        related_entity_type: str | None = None,
        related_entity_id: str | None = None,
    ) -> Job:
        timestamp = utc_now()
        job = Job(
            job_id=f"job_{uuid4().hex}",
            project_key=request_context.project_key,
            job_type=job_type,
            status=JobStatus.PENDING,
            title=title,
            input_text=request_context.request_text,
            context_json=context_json or self._context_from_request(request_context),
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            priority=priority,
            created_at=timestamp,
            updated_at=timestamp,
            request_id=request_context.request_id,
            request_key=self.build_request_key(request_context),
        )
        return self.repository.create(job)

    def mark_in_progress(self, job_id: str) -> Job:
        return self._set_status(job_id, JobStatus.IN_PROGRESS)

    def mark_waiting_for_input(self, job_id: str, summary: str | None = None) -> Job:
        updates: dict[str, Any] = {}
        if summary is not None:
            updates["result_summary"] = summary
        return self._set_status(job_id, JobStatus.WAITING_FOR_INPUT, **updates)

    def mark_completed(
        self,
        job_id: str,
        result_summary: str | None = None,
        latest_run_id: str | None = None,
    ) -> Job:
        updates: dict[str, Any] = {}
        if result_summary is not None:
            updates["result_summary"] = result_summary
        if latest_run_id is not None:
            updates["latest_run_id"] = latest_run_id
        return self._set_status(job_id, JobStatus.COMPLETED, **updates)

    def mark_failed(
        self,
        job_id: str,
        error_summary: str,
        latest_run_id: str | None = None,
    ) -> Job:
        updates: dict[str, Any] = {"error_summary": error_summary}
        if latest_run_id is not None:
            updates["latest_run_id"] = latest_run_id
        return self._set_status(job_id, JobStatus.FAILED, **updates)

    def mark_waiting_for_approval(self, job_id: str) -> Job:
        """Park a Job pending human confirmation (sets status + approval_state)."""
        return self._set_status(
            job_id,
            JobStatus.WAITING_FOR_APPROVAL,
            approval_state=ApprovalState.PENDING,
        )

    def mark_approved(self, job_id: str) -> Job:
        """Record approval without changing lifecycle status; execution resumes separately."""
        job = self.get_job(job_id)
        job.approval_state = ApprovalState.APPROVED
        job.updated_at = utc_now()
        return self.repository.update(job)

    def mark_rejected(self, job_id: str) -> Job:
        """Resolve a pending Job as rejected (terminal, no execution)."""
        return self._set_status(
            job_id,
            JobStatus.REJECTED,
            approval_state=ApprovalState.REJECTED,
        )

    def attach_related_entity(self, job_id: str, entity_type: str, entity_id: str) -> Job:
        job = self.get_job(job_id)
        job.related_entity_type = entity_type
        job.related_entity_id = entity_id
        job.updated_at = utc_now()
        return self.repository.update(job)

    def set_latest_run_id(self, job_id: str, latest_run_id: str) -> Job:
        job = self.get_job(job_id)
        job.latest_run_id = latest_run_id
        job.updated_at = utc_now()
        return self.repository.update(job)

    def get_job(self, job_id: str) -> Job:
        job = self.repository.get(job_id)
        if job is None:
            raise JobNotFoundError(f"job not found: {job_id}")
        return job

    @staticmethod
    def build_request_key(request_context: RequestContext) -> str:
        parts = [
            request_context.project_key,
            request_context.source_type,
            request_context.source_chat_id or "",
            request_context.source_user_id or "",
            request_context.source_message_id or "",
            request_context.command_name or "",
        ]
        return ":".join(parts)

    @staticmethod
    def _context_from_request(request_context: RequestContext) -> dict[str, Any]:
        data = asdict(request_context)
        data["created_at"] = request_context.created_at.isoformat()
        return data

    def _set_status(self, job_id: str, new_status: JobStatus, **extra_updates: Any) -> Job:
        job = self.get_job(job_id)
        ensure_job_transition(job.status, new_status)
        job.status = new_status
        for key, value in extra_updates.items():
            setattr(job, key, value)
        job.updated_at = utc_now()
        return self.repository.update(job)
