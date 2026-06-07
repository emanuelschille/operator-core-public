from __future__ import annotations

from dataclasses import dataclass, field

from .models import JsonDict


@dataclass(frozen=True)
class ReviewCriterion:
    criterion_key: str
    criterion_label: str
    score: int
    notes: str | None = None

    def to_snapshot(self) -> JsonDict:
        return {
            "criterion_key": self.criterion_key,
            "criterion_label": self.criterion_label,
            "score": self.score,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ReviewEntry:
    entry_id: str
    review_session_id: str
    reviewer_label: str
    criteria: tuple[ReviewCriterion, ...]
    notes: str | None = None

    def to_snapshot(self) -> JsonDict:
        return {
            "entry_id": self.entry_id,
            "review_session_id": self.review_session_id,
            "reviewer_label": self.reviewer_label,
            "criteria": [c.to_snapshot() for c in self.criteria],
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ReviewSession:
    review_session_id: str
    benchmark_run_id: str
    evaluation_case_id: str
    reviewer_id: str
    entries: tuple[ReviewEntry, ...]
    winner_reviewer_label: str | None
    created_at: str

    def to_snapshot(self) -> JsonDict:
        return {
            "review_session_id": self.review_session_id,
            "benchmark_run_id": self.benchmark_run_id,
            "evaluation_case_id": self.evaluation_case_id,
            "reviewer_id": self.reviewer_id,
            "entries": [e.to_snapshot() for e in self.entries],
            "winner_reviewer_label": self.winner_reviewer_label,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ReviewPackageCandidate:
    reviewer_label: str
    source_flow: str
    target_platform: str
    content_items: tuple[str, ...]
    output_payload: JsonDict = field(default_factory=dict)

    def to_snapshot(self) -> JsonDict:
        return {
            "reviewer_label": self.reviewer_label,
            "source_flow": self.source_flow,
            "target_platform": self.target_platform,
            "content_items": list(self.content_items),
            "output_payload": dict(self.output_payload),
        }


@dataclass(frozen=True)
class ReviewPackage:
    package_id: str
    benchmark_run_id: str
    evaluation_case_id: str
    created_at: str
    candidates: tuple[ReviewPackageCandidate, ...]
    rubric_labels: JsonDict
    import_template: JsonDict
    scoring_instructions: tuple[str, ...] = ()

    def to_snapshot(self) -> JsonDict:
        return {
            "package_id": self.package_id,
            "benchmark_run_id": self.benchmark_run_id,
            "evaluation_case_id": self.evaluation_case_id,
            "created_at": self.created_at,
            "candidates": [candidate.to_snapshot() for candidate in self.candidates],
            "rubric_labels": dict(self.rubric_labels),
            "import_template": dict(self.import_template),
            "scoring_instructions": list(self.scoring_instructions),
        }
