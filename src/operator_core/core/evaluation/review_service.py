from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .models import BlindReviewExport, JsonDict
from .review_models import (
    ReviewCriterion,
    ReviewEntry,
    ReviewPackage,
    ReviewPackageCandidate,
    ReviewSession,
)


# Standard rubric: criterion_key → human-readable label
RUBRIC: dict[str, str] = {
    "naturalness": "Natürlichkeit",
    "fit": "Passung / Relevanz",
    "less_ai_like": "Weniger KI-Wirkung",
    "usability": "Verwendbarkeit / Post-Reife",
    "overall": "Gesamtpräferenz",
}

# Canonical key order for stable iteration
_RUBRIC_KEYS: tuple[str, ...] = (
    "naturalness",
    "fit",
    "less_ai_like",
    "usability",
    "overall",
)


def _build_entry_id(benchmark_run_id: str, reviewer_label: str) -> str:
    stable_key = f"{benchmark_run_id}|{reviewer_label}"
    return f"re_{hashlib.sha1(stable_key.encode('utf-8')).hexdigest()[:12]}"


def _build_package_id(
    benchmark_run_id: str,
    evaluation_case_id: str,
    export_ids: tuple[str, ...],
) -> str:
    stable_key = "|".join((benchmark_run_id, evaluation_case_id, *export_ids))
    return f"rp_{hashlib.sha1(stable_key.encode('utf-8')).hexdigest()[:12]}"


class ReviewService:
    def build_review_package(
        self,
        blind_review_exports: BlindReviewExport | Iterable[BlindReviewExport],
    ) -> ReviewPackage:
        exports = self._normalize_exports(blind_review_exports)
        first_export = exports[0]
        benchmark_run_id = str(first_export.benchmark_run_id or "")
        evaluation_case_id = str(first_export.evaluation_case_id or "")

        candidates: list[ReviewPackageCandidate] = []
        for export in exports:
            for reviewer_entry in export.reviewer_entries:
                generated_output = dict(reviewer_entry.generated_output)
                items = tuple(
                    str(item)
                    for item in (generated_output.get("items") or [])
                    if str(item).strip()
                )
                candidates.append(
                    ReviewPackageCandidate(
                        reviewer_label=str(reviewer_entry.reviewer_label or ""),
                        source_flow=reviewer_entry.source_flow,
                        target_platform=reviewer_entry.target_platform,
                        content_items=items,
                        output_payload=generated_output,
                    )
                )

        return ReviewPackage(
            package_id=_build_package_id(
                benchmark_run_id,
                evaluation_case_id,
                tuple(export.export_id for export in exports),
            ),
            benchmark_run_id=benchmark_run_id,
            evaluation_case_id=evaluation_case_id,
            created_at=first_export.created_at,
            candidates=tuple(candidates),
            rubric_labels=dict(RUBRIC),
            import_template=self._build_import_template(
                benchmark_run_id=benchmark_run_id,
                evaluation_case_id=evaluation_case_id,
                reviewer_labels=tuple(candidate.reviewer_label for candidate in candidates),
            ),
            scoring_instructions=(
                "Bewerte jeden Kandidaten auf einer Skala von 1 bis 5 pro Kriterium.",
                "1 = schwach oder unpassend, 5 = klar bevorzugt.",
                "Trage optional kurze Notizen pro Kandidat ein und setze den Gewinner als Reviewer-Label.",
            ),
        )

    def import_results(
        self,
        payload: dict[str, Any],
        *,
        created_at: str | None = None,
    ) -> ReviewSession:
        """
        Import review outcomes from a dict payload.

        Expected shape::

            {
                "benchmark_run_id": "br_...",
                "evaluation_case_id": "ec_...",
                "reviewer_id": "human_01",
                "entries": {
                    "Candidate A": {
                        "naturalness": 4,
                        "fit": 5,
                        "less_ai_like": 3,
                        "usability": 4,
                        "overall": 4,
                        "notes": "..."     # optional
                    },
                    ...
                },
                "winner": "Candidate A",   # optional
                "created_at": "...",       # optional, overridden by kwarg
            }
        """
        review_session_id = f"rs_{uuid4().hex[:16]}"

        effective_created_at = (
            created_at
            or str(payload.get("created_at") or "").strip()
            or datetime.now(tz=timezone.utc).isoformat()
        )

        benchmark_run_id = str(payload.get("benchmark_run_id") or "")
        entries_data: dict[str, Any] = dict(payload.get("entries") or {})
        entries: list[ReviewEntry] = []

        for reviewer_label, entry_payload in entries_data.items():
            if not isinstance(entry_payload, Mapping):
                continue

            criteria = tuple(
                ReviewCriterion(
                    criterion_key=key,
                    criterion_label=RUBRIC.get(key, key),
                    score=int(entry_payload.get(key) or 0),
                    notes=None,
                )
                for key in _RUBRIC_KEYS
                if key in entry_payload
            )

            entry_notes = str(entry_payload.get("notes") or "").strip() or None

            entries.append(
                ReviewEntry(
                    entry_id=_build_entry_id(benchmark_run_id, reviewer_label),
                    review_session_id=review_session_id,
                    reviewer_label=reviewer_label,
                    criteria=criteria,
                    notes=entry_notes,
                )
            )

        winner_raw = str(payload.get("winner") or "").strip()
        winner_reviewer_label = winner_raw or None

        return ReviewSession(
            review_session_id=review_session_id,
            benchmark_run_id=benchmark_run_id,
            evaluation_case_id=str(payload.get("evaluation_case_id") or ""),
            reviewer_id=str(payload.get("reviewer_id") or ""),
            entries=tuple(entries),
            winner_reviewer_label=winner_reviewer_label,
            created_at=effective_created_at,
        )

    def import_results_from_json(
        self,
        json_str: str,
        *,
        created_at: str | None = None,
    ) -> ReviewSession:
        """Parse a JSON string and delegate to import_results."""
        payload: dict[str, Any] = json.loads(json_str)
        return self.import_results(payload, created_at=created_at)

    def resolve_internal_linkage(
        self,
        review_session: ReviewSession,
        blind_review_export: BlindReviewExport,
    ) -> dict[str, JsonDict]:
        """
        Map reviewer_label → full internal linkage metadata.

        Returns a dict keyed by reviewer_label (e.g. "Candidate A").
        Each value contains provider_name, model_name, candidate_id,
        evaluation_case_id, writer_brief_id, evidence_pack_id, etc.

        Provider/model info is NOT stored on ReviewSession — it is only
        accessible through this resolution step using the BlindReviewExport.
        Labels absent from the export get an empty dict.
        """
        # Build label → blind_entry_id from the reviewer-facing entries
        label_to_blind_entry: dict[str, str] = {}
        for entry in blind_review_export.reviewer_entries:
            if entry.reviewer_label:
                label_to_blind_entry[entry.reviewer_label] = entry.blind_entry_id

        # Build blind_entry_id → linkage snapshot
        entry_to_linkage: dict[str, JsonDict] = {
            link.blind_entry_id: link.to_snapshot()
            for link in blind_review_export.internal_linkage
        }

        result: dict[str, JsonDict] = {}
        for review_entry in review_session.entries:
            label = review_entry.reviewer_label
            blind_entry_id = label_to_blind_entry.get(label)
            if blind_entry_id:
                result[label] = entry_to_linkage.get(blind_entry_id, {})
            else:
                result[label] = {}

        return result

    def _normalize_exports(
        self,
        blind_review_exports: BlindReviewExport | Iterable[BlindReviewExport],
    ) -> tuple[BlindReviewExport, ...]:
        if isinstance(blind_review_exports, BlindReviewExport):
            exports = (blind_review_exports,)
        else:
            exports = tuple(blind_review_exports)

        if not exports:
            raise ValueError("build_review_package requires at least one BlindReviewExport")

        first_export = exports[0]
        benchmark_run_id = str(first_export.benchmark_run_id or "")
        evaluation_case_id = str(first_export.evaluation_case_id or "")

        for export in exports[1:]:
            if str(export.benchmark_run_id or "") != benchmark_run_id:
                raise ValueError("All BlindReviewExport objects must share the same benchmark_run_id")
            if str(export.evaluation_case_id or "") != evaluation_case_id:
                raise ValueError("All BlindReviewExport objects must share the same evaluation_case_id")

        return exports

    def _build_import_template(
        self,
        *,
        benchmark_run_id: str,
        evaluation_case_id: str,
        reviewer_labels: tuple[str, ...],
    ) -> JsonDict:
        entries: JsonDict = {}
        for reviewer_label in reviewer_labels:
            entry_template = {key: 0 for key in _RUBRIC_KEYS}
            entry_template["notes"] = ""
            entries[reviewer_label] = entry_template

        return {
            "benchmark_run_id": benchmark_run_id,
            "evaluation_case_id": evaluation_case_id,
            "reviewer_id": "",
            "entries": entries,
            "winner": "",
        }
