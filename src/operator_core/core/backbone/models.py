from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .statuses import JobStatus, RunStatus


JsonDict = dict[str, Any]


class ApprovalState(str, Enum):
    """Confirmation/approval state of a Job (orthogonal to its lifecycle status)."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RequestContext:
    request_id: str
    project_key: str
    source_type: str
    source_user_id: str | None = None
    source_chat_id: str | None = None
    source_message_id: str | None = None
    command_name: str | None = None
    command_body: str | None = None
    request_text: str | None = None
    reply_to_message_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Job:
    job_id: str
    project_key: str
    job_type: str
    status: JobStatus
    title: str
    input_text: str | None
    context_json: JsonDict
    approval_state: ApprovalState = ApprovalState.NOT_REQUIRED
    related_entity_type: str | None = None
    related_entity_id: str | None = None
    priority: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    latest_run_id: str | None = None
    result_summary: str | None = None
    error_summary: str | None = None
    request_id: str | None = None
    request_key: str | None = None


@dataclass(slots=True)
class Run:
    run_id: str
    job_id: str
    project_key: str
    module_name: str
    status: RunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int = 0
    input_snapshot: JsonDict = field(default_factory=dict)
    output_snapshot: JsonDict = field(default_factory=dict)
    error_detail: str | None = None
    duration_ms: int | None = None
    request_id: str | None = None


@dataclass(slots=True)
class Event:
    event_id: str
    project_key: str
    entity_type: str
    entity_id: str
    event_type: str
    message: str
    payload_json: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
