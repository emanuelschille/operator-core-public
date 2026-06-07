from __future__ import annotations

from enum import Enum


class InvalidStatusTransition(ValueError):
    """Raised when a status transition is not allowed."""


class JobStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {
        JobStatus.IN_PROGRESS,
        JobStatus.WAITING_FOR_APPROVAL,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.IN_PROGRESS: {
        JobStatus.WAITING_FOR_INPUT,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.WAITING_FOR_INPUT: {
        JobStatus.IN_PROGRESS,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.WAITING_FOR_APPROVAL: {
        JobStatus.IN_PROGRESS,
        JobStatus.REJECTED,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
    JobStatus.REJECTED: set(),
}

_ALLOWED_RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def ensure_job_transition(current: JobStatus, new: JobStatus) -> None:
    if current == new:
        return
    if new not in _ALLOWED_JOB_TRANSITIONS[current]:
        raise InvalidStatusTransition(f"invalid job transition: {current.value} -> {new.value}")


def ensure_run_transition(current: RunStatus, new: RunStatus) -> None:
    if current == new:
        return
    if new not in _ALLOWED_RUN_TRANSITIONS[current]:
        raise InvalidStatusTransition(f"invalid run transition: {current.value} -> {new.value}")
