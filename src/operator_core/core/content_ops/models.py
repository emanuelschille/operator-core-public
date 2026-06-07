from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_core.core.analysis_foundation.models import AnalysisSnapshot, ModelExecutionMeta, WriterBrief


SUPPORTED_CONTENT_ACTIONS = (
    "idea",
    "serie",
    "title",
    "hook",
    "cta",
    "caption",
    "vollauto",
    "followup",
    "draft",
    "variant",
    "mark_stale",
)


@dataclass(frozen=True)
class ContentOpResult:
    lane_name: str
    project_key: str
    action_type: str
    command_body: str
    title: str
    summary: str
    items: tuple[str, ...]
    openai_used: bool = False
    model_name: str | None = None
    airtable_record_id: str | None = None
    platform: str = ""
    foundation_snapshot_ids: tuple[str, ...] = ()
    writer_brief_id: str | None = None
    evidence_pack_id: str | None = None
    evidence_pack_record_id: str | None = None
    commercial_class: str | None = None

    def to_snapshot(self) -> dict[str, object]:
        return {
            "lane_name": self.lane_name,
            "project_key": self.project_key,
            "action_type": self.action_type,
            "command_body": self.command_body,
            "title": self.title,
            "summary": self.summary,
            "items": list(self.items),
            "openai_used": self.openai_used,
            "model_name": self.model_name,
            "airtable_record_id": self.airtable_record_id,
            "platform": self.platform,
            "foundation_snapshot_ids": list(self.foundation_snapshot_ids),
            "writer_brief_id": self.writer_brief_id,
            "evidence_pack_id": self.evidence_pack_id,
            "evidence_pack_record_id": self.evidence_pack_record_id,
            "commercial_class": self.commercial_class,
        }


@dataclass(frozen=True)
class FoundationIdeaResult:
    content_result: ContentOpResult
    selected_snapshots: tuple["AnalysisSnapshot", ...]
    writer_brief: "WriterBrief"
    execution_meta: "ModelExecutionMeta"


@dataclass(frozen=True)
class FoundationDraftResult:
    content_result: ContentOpResult
    selected_snapshots: tuple["AnalysisSnapshot", ...]
    writer_brief: "WriterBrief"
    execution_meta: "ModelExecutionMeta"


@dataclass(frozen=True)
class FoundationCaptionResult:
    content_result: ContentOpResult
    selected_snapshots: tuple["AnalysisSnapshot", ...]
    writer_brief: "WriterBrief"
    execution_meta: "ModelExecutionMeta"


@dataclass(frozen=True)
class FoundationHookResult:
    content_result: ContentOpResult
    selected_snapshots: tuple["AnalysisSnapshot", ...]
    writer_brief: "WriterBrief"
    execution_meta: "ModelExecutionMeta"


@dataclass(frozen=True)
class FoundationSerieResult:
    content_result: ContentOpResult
    selected_snapshots: tuple["AnalysisSnapshot", ...]
    writer_brief: "WriterBrief"
    execution_meta: "ModelExecutionMeta"


@dataclass(frozen=True)
class FoundationTitleResult:
    content_result: ContentOpResult
    selected_snapshots: tuple["AnalysisSnapshot", ...]
    writer_brief: "WriterBrief"
    execution_meta: "ModelExecutionMeta"


@dataclass(frozen=True)
class FoundationCtaResult:
    content_result: ContentOpResult
    selected_snapshots: tuple["AnalysisSnapshot", ...]
    writer_brief: "WriterBrief"
    execution_meta: "ModelExecutionMeta"


@dataclass(frozen=True)
class FoundationFollowupResult:
    content_result: ContentOpResult
    selected_snapshots: tuple["AnalysisSnapshot", ...]
    writer_brief: "WriterBrief"
    execution_meta: "ModelExecutionMeta"
    instruction: str
    mutation_mode: str
    source_action_type: str
