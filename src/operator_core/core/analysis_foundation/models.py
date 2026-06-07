from __future__ import annotations

from dataclasses import dataclass, field


JsonDict = dict[str, object]


@dataclass(frozen=True)
class ModelExecutionMeta:
    provider_name: str
    model_name: str
    task_role: str
    status: str = "prepared"
    notes: tuple[str, ...] = ()

    def to_snapshot(self) -> JsonDict:
        return {
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "task_role": self.task_role,
            "status": self.status,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class AnalysisSnapshot:
    snapshot_id: str
    project_key: str
    scope: str
    created_at: str
    title: str
    summary: str
    platform_key: str = ""
    analytics_summary_lines: tuple[str, ...] = ()
    rule_summary_lines: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    posting_context: JsonDict = field(default_factory=dict)
    airtable_record_id: str | None = None

    def to_snapshot(self) -> JsonDict:
        return {
            "snapshot_id": self.snapshot_id,
            "project_key": self.project_key,
            "scope": self.scope,
            "created_at": self.created_at,
            "title": self.title,
            "summary": self.summary,
            "platform_key": self.platform_key,
            "analytics_summary_lines": list(self.analytics_summary_lines),
            "rule_summary_lines": list(self.rule_summary_lines),
            "source_refs": list(self.source_refs),
            "posting_context": dict(self.posting_context),
            "airtable_record_id": self.airtable_record_id,
        }


@dataclass(frozen=True)
class WriterBrief:
    brief_id: str
    project_key: str
    created_at: str
    objective: str
    audience: str
    constraints: tuple[str, ...]
    source_snapshot_ids: tuple[str, ...]
    provider_name: str
    model_name: str
    task_role: str
    execution_meta: ModelExecutionMeta

    def to_snapshot(self) -> JsonDict:
        return {
            "brief_id": self.brief_id,
            "project_key": self.project_key,
            "created_at": self.created_at,
            "objective": self.objective,
            "audience": self.audience,
            "constraints": list(self.constraints),
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "task_role": self.task_role,
            "execution_meta": self.execution_meta.to_snapshot(),
        }


@dataclass(frozen=True)
class EvidencePack:
    evidence_pack_id: str
    project_key: str
    created_at: str
    summary: str
    snapshot_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    evidence_lines: tuple[str, ...]
    airtable_record_id: str | None = None

    def to_snapshot(self) -> JsonDict:
        return {
            "evidence_pack_id": self.evidence_pack_id,
            "project_key": self.project_key,
            "created_at": self.created_at,
            "summary": self.summary,
            "snapshot_ids": list(self.snapshot_ids),
            "source_refs": list(self.source_refs),
            "evidence_lines": list(self.evidence_lines),
            "airtable_record_id": self.airtable_record_id,
        }


@dataclass(frozen=True)
class AnalysisFoundationResult:
    lane_name: str
    project_key: str
    action_type: str
    title: str
    summary: str
    analysis_snapshots: tuple[AnalysisSnapshot, ...]
    writer_brief: WriterBrief
    evidence_pack: EvidencePack
    execution_meta: ModelExecutionMeta
    weekly_analysis: WeeklyAnalysisArtifact | None = None

    def to_snapshot(self) -> JsonDict:
        return {
            "lane_name": self.lane_name,
            "project_key": self.project_key,
            "action_type": self.action_type,
            "title": self.title,
            "summary": self.summary,
            "analysis_snapshots": [snapshot.to_snapshot() for snapshot in self.analysis_snapshots],
            "writer_brief": self.writer_brief.to_snapshot(),
            "evidence_pack": self.evidence_pack.to_snapshot(),
            "execution_meta": self.execution_meta.to_snapshot(),
            "weekly_analysis": self.weekly_analysis.to_snapshot() if self.weekly_analysis else None,
        }


@dataclass(frozen=True)
class WeeklyAnalysisArtifact:
    analysis_id: str
    project_key: str
    analysis_window_start: str
    analysis_window_end: str
    generated_at: str
    
    # Nuanced patterns
    key_winners: tuple[str, ...]
    weak_patterns: tuple[str, ...]
    
    # Recommendations
    recommended_content_directions: tuple[str, ...]
    recommended_hook_directions: tuple[str, ...]
    recommended_cta_directions: tuple[str, ...]
    recommended_platform_notes: dict[str, str]
    
    # Evidence & Trust
    confidence_score: float
    evidence_summary: str
    source_refs: tuple[str, ...]
    
    # Metadata
    execution_meta: ModelExecutionMeta
    job_id: str | None = None
    run_id: str | None = None
    airtable_record_id: str | None = None

    def to_snapshot(self) -> JsonDict:
        return {
            "analysis_id": self.analysis_id,
            "project_key": self.project_key,
            "analysis_window_start": self.analysis_window_start,
            "analysis_window_end": self.analysis_window_end,
            "generated_at": self.generated_at,
            "key_winners": list(self.key_winners),
            "weak_patterns": list(self.weak_patterns),
            "recommended_content_directions": list(self.recommended_content_directions),
            "recommended_hook_directions": list(self.recommended_hook_directions),
            "recommended_cta_directions": list(self.recommended_cta_directions),
            "recommended_platform_notes": dict(self.recommended_platform_notes),
            "confidence_score": self.confidence_score,
            "evidence_summary": self.evidence_summary,
            "source_refs": list(self.source_refs),
            "execution_meta": self.execution_meta.to_snapshot(),
            "job_id": self.job_id,
            "run_id": self.run_id,
            "airtable_record_id": self.airtable_record_id,
        }


@dataclass(frozen=True)
class WeeklyAnalysisStatus:
    project_key: str
    last_run_at: str
    last_success_at: str | None
    last_status: str  # success, failed, skipped_fresh
    latest_analysis_id: str | None
    actual_model_used: str | None
    fallback_used: bool
    artifact_age_days: int | None
    error_summary: str | None = None
    airtable_record_id: str | None = None

    def to_snapshot(self) -> JsonDict:
        return {
            "project_key": self.project_key,
            "last_run_at": self.last_run_at,
            "last_success_at": self.last_success_at,
            "last_status": self.last_status,
            "latest_analysis_id": self.latest_analysis_id,
            "actual_model_used": self.actual_model_used,
            "fallback_used": self.fallback_used,
            "artifact_age_days": self.artifact_age_days,
            "error_summary": self.error_summary,
            "airtable_record_id": self.airtable_record_id,
        }
