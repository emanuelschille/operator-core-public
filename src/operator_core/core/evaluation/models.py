from __future__ import annotations

from dataclasses import dataclass


JsonDict = dict[str, object]


@dataclass(frozen=True)
class EvaluationCase:
    evaluation_case_id: str
    source_flow: str
    source_action_type: str
    target_platform: str
    selected_snapshot_ids: tuple[str, ...]
    writer_brief_id: str
    evidence_pack_id: str | None
    job_id: str
    run_id: str
    input_context: JsonDict
    generated_output: JsonDict
    model_provider: JsonDict
    created_at: str

    def to_snapshot(self) -> JsonDict:
        return {
            "evaluation_case_id": self.evaluation_case_id,
            "source_flow": self.source_flow,
            "source_action_type": self.source_action_type,
            "target_platform": self.target_platform,
            "selected_snapshot_ids": list(self.selected_snapshot_ids),
            "writer_brief_id": self.writer_brief_id,
            "evidence_pack_id": self.evidence_pack_id,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "input_context": dict(self.input_context),
            "generated_output": dict(self.generated_output),
            "model_provider": dict(self.model_provider),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class BlindReviewEntry:
    blind_entry_id: str
    review_case_id: str
    source_flow: str
    target_platform: str
    generated_output: JsonDict
    benchmark_run_id: str | None = None
    reviewer_label: str | None = None

    def to_snapshot(self) -> JsonDict:
        return {
            "blind_entry_id": self.blind_entry_id,
            "review_case_id": self.review_case_id,
            "source_flow": self.source_flow,
            "target_platform": self.target_platform,
            "benchmark_run_id": self.benchmark_run_id,
            "reviewer_label": self.reviewer_label,
            "generated_output": dict(self.generated_output),
        }


@dataclass(frozen=True)
class BlindReviewLinkage:
    blind_entry_id: str
    review_case_id: str
    evaluation_case_id: str
    benchmark_run_id: str | None
    candidate_id: str | None
    provider_name: str
    model_name: str
    task_role: str
    job_id: str
    run_id: str
    selected_snapshot_ids: tuple[str, ...]
    writer_brief_id: str | None
    evidence_pack_id: str | None

    def to_snapshot(self) -> JsonDict:
        return {
            "blind_entry_id": self.blind_entry_id,
            "review_case_id": self.review_case_id,
            "evaluation_case_id": self.evaluation_case_id,
            "benchmark_run_id": self.benchmark_run_id,
            "candidate_id": self.candidate_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "task_role": self.task_role,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "selected_snapshot_ids": list(self.selected_snapshot_ids),
            "writer_brief_id": self.writer_brief_id,
            "evidence_pack_id": self.evidence_pack_id,
        }


@dataclass(frozen=True)
class BlindReviewExport:
    export_id: str
    created_at: str
    reviewer_entries: tuple[BlindReviewEntry, ...]
    internal_linkage: tuple[BlindReviewLinkage, ...]
    evaluation_case_id: str | None = None
    benchmark_run_id: str | None = None

    def reviewer_payload(self) -> JsonDict:
        return {
            "export_id": self.export_id,
            "created_at": self.created_at,
            "evaluation_case_id": self.evaluation_case_id,
            "benchmark_run_id": self.benchmark_run_id,
            "entries": [entry.to_snapshot() for entry in self.reviewer_entries],
        }

    def internal_payload(self) -> JsonDict:
        return {
            "export_id": self.export_id,
            "created_at": self.created_at,
            "evaluation_case_id": self.evaluation_case_id,
            "benchmark_run_id": self.benchmark_run_id,
            "linkage": [link.to_snapshot() for link in self.internal_linkage],
        }


@dataclass(frozen=True)
class BenchmarkCandidate:
    candidate_id: str
    evaluation_case_id: str
    benchmark_run_id: str
    reviewer_label: str
    source_flow: str
    target_platform: str
    generated_output: JsonDict
    model_provider: JsonDict
    job_id: str
    run_id: str
    selected_snapshot_ids: tuple[str, ...]
    writer_brief_id: str | None
    evidence_pack_id: str | None
    status: str
    notes: str | None = None

    def to_snapshot(self) -> JsonDict:
        return {
            "candidate_id": self.candidate_id,
            "evaluation_case_id": self.evaluation_case_id,
            "benchmark_run_id": self.benchmark_run_id,
            "reviewer_label": self.reviewer_label,
            "source_flow": self.source_flow,
            "target_platform": self.target_platform,
            "generated_output": dict(self.generated_output),
            "model_provider": dict(self.model_provider),
            "job_id": self.job_id,
            "run_id": self.run_id,
            "selected_snapshot_ids": list(self.selected_snapshot_ids),
            "writer_brief_id": self.writer_brief_id,
            "evidence_pack_id": self.evidence_pack_id,
            "status": self.status,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class BenchmarkRun:
    benchmark_run_id: str
    evaluation_case_id: str
    benchmark_label: str
    created_at: str
    status: str
    candidates: tuple[BenchmarkCandidate, ...]
    comparison_meta: JsonDict

    def to_snapshot(self) -> JsonDict:
        return {
            "benchmark_run_id": self.benchmark_run_id,
            "evaluation_case_id": self.evaluation_case_id,
            "benchmark_label": self.benchmark_label,
            "created_at": self.created_at,
            "status": self.status,
            "candidates": [candidate.to_snapshot() for candidate in self.candidates],
            "comparison_meta": dict(self.comparison_meta),
        }


@dataclass(frozen=True)
class BenchmarkWriterProfile:
    profile_id: str
    label: str
    provider_name: str
    model_name: str
    task_role: str
    prompt_shaping: JsonDict

    def to_snapshot(self) -> JsonDict:
        return {
            "profile_id": self.profile_id,
            "label": self.label,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "task_role": self.task_role,
            "prompt_shaping": dict(self.prompt_shaping),
        }


@dataclass(frozen=True)
class BenchmarkExecutionResult:
    benchmark_run: BenchmarkRun
    executed_profiles: tuple[BenchmarkWriterProfile, ...]

    def to_snapshot(self) -> JsonDict:
        return {
            "benchmark_run": self.benchmark_run.to_snapshot(),
            "executed_profiles": [profile.to_snapshot() for profile in self.executed_profiles],
        }
