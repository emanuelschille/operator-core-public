from __future__ import annotations

import hashlib
from uuid import uuid4

from .models import (
    BenchmarkCandidate,
    BenchmarkRun,
    BlindReviewEntry,
    BlindReviewExport,
    BlindReviewLinkage,
    EvaluationCase,
)


class EvaluationService:
    def _build_review_case_id(self, evaluation_case_id: str) -> str:
        return f"review_{hashlib.sha1(evaluation_case_id.encode('utf-8')).hexdigest()[:10]}"

    def _build_candidate_id(self, benchmark_run_id: str, reviewer_label: str) -> str:
        stable_key = f"{benchmark_run_id}|{reviewer_label}"
        return f"cand_{hashlib.sha1(stable_key.encode('utf-8')).hexdigest()[:12]}"

    def create_case(
        self,
        *,
        source_flow: str,
        source_action_type: str,
        target_platform: str,
        selected_snapshot_ids: tuple[str, ...],
        writer_brief_id: str,
        evidence_pack_id: str | None,
        job_id: str,
        run_id: str,
        input_context: dict[str, object],
        generated_output: dict[str, object],
        model_provider: dict[str, object],
        created_at: str,
    ) -> EvaluationCase:
        stable_key = "|".join(
            (
                source_flow,
                source_action_type,
                target_platform,
                job_id,
                run_id,
                writer_brief_id,
                evidence_pack_id or "",
                ",".join(selected_snapshot_ids),
            )
        )
        evaluation_case_id = f"ec_{hashlib.sha1(stable_key.encode('utf-8')).hexdigest()[:16]}"
        return EvaluationCase(
            evaluation_case_id=evaluation_case_id,
            source_flow=source_flow,
            source_action_type=source_action_type,
            target_platform=target_platform,
            selected_snapshot_ids=selected_snapshot_ids,
            writer_brief_id=writer_brief_id,
            evidence_pack_id=evidence_pack_id,
            job_id=job_id,
            run_id=run_id,
            input_context=input_context,
            generated_output=generated_output,
            model_provider=model_provider,
            created_at=created_at,
        )

    def build_blind_review_export(
        self,
        *,
        evaluation_cases: tuple[EvaluationCase, ...],
        created_at: str,
    ) -> BlindReviewExport:
        export_id = f"bre_{uuid4().hex[:16]}"
        reviewer_entries: list[BlindReviewEntry] = []
        internal_linkage: list[BlindReviewLinkage] = []

        for index, case in enumerate(evaluation_cases, start=1):
            review_case_id = self._build_review_case_id(case.evaluation_case_id)
            blind_entry_id = f"entry_{index:02d}_{review_case_id[-6:]}"
            reviewer_entries.append(
                BlindReviewEntry(
                    blind_entry_id=blind_entry_id,
                    review_case_id=review_case_id,
                    source_flow=case.source_flow,
                    target_platform=case.target_platform,
                    generated_output=case.generated_output,
                )
            )
            internal_linkage.append(
                BlindReviewLinkage(
                    blind_entry_id=blind_entry_id,
                    review_case_id=review_case_id,
                    evaluation_case_id=case.evaluation_case_id,
                    benchmark_run_id=None,
                    candidate_id=None,
                    provider_name=str(case.model_provider.get("provider_name") or ""),
                    model_name=str(case.model_provider.get("model_name") or ""),
                    task_role=str(case.model_provider.get("task_role") or ""),
                    job_id=case.job_id,
                    run_id=case.run_id,
                    selected_snapshot_ids=case.selected_snapshot_ids,
                    writer_brief_id=case.writer_brief_id,
                    evidence_pack_id=case.evidence_pack_id,
                )
            )

        return BlindReviewExport(
            export_id=export_id,
            created_at=created_at,
            reviewer_entries=tuple(reviewer_entries),
            internal_linkage=tuple(internal_linkage),
            evaluation_case_id=evaluation_cases[0].evaluation_case_id if len(evaluation_cases) == 1 else None,
        )

    def create_benchmark_candidate(
        self,
        *,
        evaluation_case_id: str,
        benchmark_run_id: str,
        reviewer_label: str,
        source_flow: str,
        target_platform: str,
        generated_output: dict[str, object],
        model_provider: dict[str, object],
        job_id: str,
        run_id: str,
        selected_snapshot_ids: tuple[str, ...],
        writer_brief_id: str | None,
        evidence_pack_id: str | None,
        status: str = "ready",
        notes: str | None = None,
    ) -> BenchmarkCandidate:
        return BenchmarkCandidate(
            candidate_id=self._build_candidate_id(benchmark_run_id, reviewer_label),
            evaluation_case_id=evaluation_case_id,
            benchmark_run_id=benchmark_run_id,
            reviewer_label=reviewer_label,
            source_flow=source_flow,
            target_platform=target_platform,
            generated_output=generated_output,
            model_provider=model_provider,
            job_id=job_id,
            run_id=run_id,
            selected_snapshot_ids=selected_snapshot_ids,
            writer_brief_id=writer_brief_id,
            evidence_pack_id=evidence_pack_id,
            status=status,
            notes=notes,
        )

    def create_benchmark_run_from_case(
        self,
        *,
        evaluation_case: EvaluationCase,
        benchmark_label: str,
        created_at: str,
        candidate_payloads: tuple[dict[str, object], ...] = (),
        status: str = "draft",
        comparison_meta: dict[str, object] | None = None,
    ) -> BenchmarkRun:
        benchmark_run_id = f"br_{uuid4().hex[:16]}"
        candidates: list[BenchmarkCandidate] = [
            self.create_benchmark_candidate(
                evaluation_case_id=evaluation_case.evaluation_case_id,
                benchmark_run_id=benchmark_run_id,
                reviewer_label="Candidate A",
                source_flow=evaluation_case.source_flow,
                target_platform=evaluation_case.target_platform,
                generated_output=evaluation_case.generated_output,
                model_provider=evaluation_case.model_provider,
                job_id=evaluation_case.job_id,
                run_id=evaluation_case.run_id,
                selected_snapshot_ids=evaluation_case.selected_snapshot_ids,
                writer_brief_id=evaluation_case.writer_brief_id,
                evidence_pack_id=evaluation_case.evidence_pack_id,
                status="ready",
            )
        ]

        for index, payload in enumerate(candidate_payloads, start=2):
            candidates.append(
                self.create_benchmark_candidate(
                    evaluation_case_id=evaluation_case.evaluation_case_id,
                    benchmark_run_id=benchmark_run_id,
                    reviewer_label=str(payload.get("reviewer_label") or f"Candidate {chr(64 + index)}"),
                    source_flow=str(payload.get("source_flow") or evaluation_case.source_flow),
                    target_platform=str(payload.get("target_platform") or evaluation_case.target_platform),
                    generated_output=dict(payload.get("generated_output") or {}),
                    model_provider=dict(payload.get("model_provider") or {}),
                    job_id=str(payload.get("job_id") or ""),
                    run_id=str(payload.get("run_id") or ""),
                    selected_snapshot_ids=tuple(
                        str(item) for item in (payload.get("selected_snapshot_ids") or evaluation_case.selected_snapshot_ids)
                    ),
                    writer_brief_id=str(payload.get("writer_brief_id") or "") or None,
                    evidence_pack_id=str(payload.get("evidence_pack_id") or "") or None,
                    status=str(payload.get("status") or "candidate_only"),
                    notes=str(payload.get("notes") or "") or None,
                )
            )

        return BenchmarkRun(
            benchmark_run_id=benchmark_run_id,
            evaluation_case_id=evaluation_case.evaluation_case_id,
            benchmark_label=benchmark_label,
            created_at=created_at,
            status=status,
            candidates=tuple(candidates),
            comparison_meta=comparison_meta or {"candidate_count": len(candidates)},
        )

    def build_benchmark_blind_review_export(
        self,
        *,
        benchmark_run: BenchmarkRun,
        created_at: str,
    ) -> BlindReviewExport:
        export_id = f"bre_{uuid4().hex[:16]}"
        reviewer_entries: list[BlindReviewEntry] = []
        internal_linkage: list[BlindReviewLinkage] = []
        review_case_id = self._build_review_case_id(benchmark_run.evaluation_case_id)

        for candidate in benchmark_run.candidates:
            blind_entry_id = f"entry_{candidate.reviewer_label.replace(' ', '_').lower()}_{candidate.candidate_id[-4:]}"
            reviewer_entries.append(
                BlindReviewEntry(
                    blind_entry_id=blind_entry_id,
                    review_case_id=review_case_id,
                    source_flow=candidate.source_flow,
                    target_platform=candidate.target_platform,
                    generated_output=candidate.generated_output,
                    benchmark_run_id=benchmark_run.benchmark_run_id,
                    reviewer_label=candidate.reviewer_label,
                )
            )
            internal_linkage.append(
                BlindReviewLinkage(
                    blind_entry_id=blind_entry_id,
                    review_case_id=review_case_id,
                    evaluation_case_id=candidate.evaluation_case_id,
                    benchmark_run_id=benchmark_run.benchmark_run_id,
                    candidate_id=candidate.candidate_id,
                    provider_name=str(candidate.model_provider.get("provider_name") or ""),
                    model_name=str(candidate.model_provider.get("model_name") or ""),
                    task_role=str(candidate.model_provider.get("task_role") or ""),
                    job_id=candidate.job_id,
                    run_id=candidate.run_id,
                    selected_snapshot_ids=candidate.selected_snapshot_ids,
                    writer_brief_id=candidate.writer_brief_id,
                    evidence_pack_id=candidate.evidence_pack_id,
                )
            )

        return BlindReviewExport(
            export_id=export_id,
            created_at=created_at,
            reviewer_entries=tuple(reviewer_entries),
            internal_linkage=tuple(internal_linkage),
            evaluation_case_id=benchmark_run.evaluation_case_id,
            benchmark_run_id=benchmark_run.benchmark_run_id,
        )
