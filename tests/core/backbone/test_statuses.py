import pytest

from operator_core.core.backbone.statuses import (
    InvalidStatusTransition,
    JobStatus,
    RunStatus,
    ensure_job_transition,
    ensure_run_transition,
)


def test_job_allowed_transitions() -> None:
    ensure_job_transition(JobStatus.PENDING, JobStatus.IN_PROGRESS)
    ensure_job_transition(JobStatus.IN_PROGRESS, JobStatus.WAITING_FOR_INPUT)
    ensure_job_transition(JobStatus.WAITING_FOR_INPUT, JobStatus.IN_PROGRESS)
    ensure_job_transition(JobStatus.IN_PROGRESS, JobStatus.COMPLETED)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (JobStatus.PENDING, JobStatus.COMPLETED),
        (JobStatus.WAITING_FOR_INPUT, JobStatus.COMPLETED),
        (JobStatus.COMPLETED, JobStatus.IN_PROGRESS),
    ],
)
def test_job_invalid_transitions_raise(current: JobStatus, new: JobStatus) -> None:
    with pytest.raises(InvalidStatusTransition):
        ensure_job_transition(current, new)


def test_run_allowed_transitions() -> None:
    ensure_run_transition(RunStatus.PENDING, RunStatus.RUNNING)
    ensure_run_transition(RunStatus.RUNNING, RunStatus.SUCCEEDED)
    ensure_run_transition(RunStatus.RUNNING, RunStatus.FAILED)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (RunStatus.PENDING, RunStatus.SUCCEEDED),
        (RunStatus.SUCCEEDED, RunStatus.RUNNING),
        (RunStatus.FAILED, RunStatus.SUCCEEDED),
    ],
)
def test_run_invalid_transitions_raise(current: RunStatus, new: RunStatus) -> None:
    with pytest.raises(InvalidStatusTransition):
        ensure_run_transition(current, new)
