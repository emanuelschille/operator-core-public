from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import BenchmarkExecutionResult, BenchmarkWriterProfile, EvaluationCase
from .service import EvaluationService

if TYPE_CHECKING:
    from operator_core.integrations.anthropic_service import AnthropicService
    from operator_core.integrations.openai_service import OpenAIService


@dataclass(frozen=True)
class BenchmarkExecutionRequest:
    evaluation_case: EvaluationCase
    benchmark_label: str
    created_at: str
    writer_profiles: tuple[BenchmarkWriterProfile, ...]
    include_source_case: bool = True


class BenchmarkExecutionService:
    def __init__(
        self,
        *,
        evaluation_service: EvaluationService,
        openai_service: "OpenAIService | None" = None,
        anthropic_service: "AnthropicService | None" = None,
    ) -> None:
        self.evaluation_service = evaluation_service
        self.openai_service = openai_service
        self.anthropic_service = anthropic_service

    def build_openai_writer_profiles(
        self,
        *,
        base_model: str,
    ) -> tuple[BenchmarkWriterProfile, ...]:
        normalized_base = (base_model or "gpt-5.4").strip() or "gpt-5.4"
        profiles = [
            BenchmarkWriterProfile(
                profile_id="openai_primary",
                label=f"OpenAI {normalized_base}",
                provider_name="openai",
                model_name=normalized_base,
                task_role="benchmark_writer",
                prompt_shaping={"variant": "control"},
            )
        ]
        if normalized_base != "gpt-5.4-mini":
            profiles.append(
                BenchmarkWriterProfile(
                    profile_id="openai_mini",
                    label="OpenAI gpt-5.4-mini",
                    provider_name="openai",
                    model_name="gpt-5.4-mini",
                    task_role="benchmark_writer",
                    prompt_shaping={"variant": "compressed"},
                )
            )
        return tuple(profiles)

    def build_writer_profiles(
        self,
        *,
        openai_base_model: str,
        anthropic_model: str | None = None,
    ) -> tuple[BenchmarkWriterProfile, ...]:
        profiles = list(
            self.build_openai_writer_profiles(base_model=openai_base_model)
        )
        if anthropic_model and anthropic_model.strip():
            profiles.extend(
                self.build_anthropic_writer_profiles(model=anthropic_model)
            )
        return tuple(profiles)

    def build_anthropic_writer_profiles(
        self,
        *,
        model: str,
    ) -> tuple[BenchmarkWriterProfile, ...]:
        normalized_model = model.strip()
        if not normalized_model:
            raise RuntimeError("Anthropic benchmark model must not be empty")
        return (
            BenchmarkWriterProfile(
                profile_id="anthropic_primary",
                label=f"Anthropic {normalized_model}",
                provider_name="anthropic",
                model_name=normalized_model,
                task_role="benchmark_writer",
                prompt_shaping={"variant": "cross_writer_control"},
            ),
        )

    def execute(
        self,
        request: BenchmarkExecutionRequest,
    ) -> BenchmarkExecutionResult:
        candidate_payloads: list[dict[str, object]] = []
        executed_profiles: list[BenchmarkWriterProfile] = []
        label_index_start = 2 if request.include_source_case else 1

        for index, profile in enumerate(request.writer_profiles, start=label_index_start):
            candidate_payloads.append(
                self._generate_candidate_payload(
                    evaluation_case=request.evaluation_case,
                    writer_profile=profile,
                    reviewer_label=f"Candidate {chr(64 + index)}",
                    created_at=request.created_at,
                )
            )
            executed_profiles.append(profile)

        benchmark_run = self.evaluation_service.create_benchmark_run_from_case(
            evaluation_case=request.evaluation_case,
            benchmark_label=request.benchmark_label,
            created_at=request.created_at,
            candidate_payloads=tuple(candidate_payloads),
            status="executed" if executed_profiles else "draft",
            comparison_meta={
                "candidate_count": len(candidate_payloads) + (1 if request.include_source_case else 0),
                "source_case_included": request.include_source_case,
                "executed_profile_ids": [profile.profile_id for profile in executed_profiles],
                "executed_writers": [
                    {
                        "profile_id": profile.profile_id,
                        "provider_name": profile.provider_name,
                        "model_name": profile.model_name,
                        "task_role": profile.task_role,
                        "prompt_shaping": dict(profile.prompt_shaping),
                    }
                    for profile in executed_profiles
                ],
            },
        )

        if not request.include_source_case and benchmark_run.candidates:
            benchmark_run = type(benchmark_run)(
                benchmark_run_id=benchmark_run.benchmark_run_id,
                evaluation_case_id=benchmark_run.evaluation_case_id,
                benchmark_label=benchmark_run.benchmark_label,
                created_at=benchmark_run.created_at,
                status=benchmark_run.status,
                candidates=benchmark_run.candidates[1:],
                comparison_meta={
                    **dict(benchmark_run.comparison_meta),
                    "candidate_count": len(benchmark_run.candidates[1:]),
                },
            )

        return BenchmarkExecutionResult(
            benchmark_run=benchmark_run,
            executed_profiles=tuple(executed_profiles),
        )

    def _generate_candidate_payload(
        self,
        *,
        evaluation_case: EvaluationCase,
        writer_profile: BenchmarkWriterProfile,
        reviewer_label: str,
        created_at: str,
    ) -> dict[str, object]:
        output_text, response_model = self._complete_with_writer_profile(
            evaluation_case=evaluation_case,
            writer_profile=writer_profile,
        )
        items = [line.strip() for line in output_text.splitlines() if line.strip()]
        if not items:
            items = [output_text.strip()] if output_text.strip() else []

        return {
            "reviewer_label": reviewer_label,
            "source_flow": evaluation_case.source_flow,
            "target_platform": evaluation_case.target_platform,
            "generated_output": {
                "action_type": evaluation_case.source_action_type,
                "title": str(evaluation_case.generated_output.get("title") or ""),
                "summary": "Benchmark candidate generated.",
                "items": items,
                "created_at": created_at,
            },
            "model_provider": {
                "provider_name": writer_profile.provider_name,
                "model_name": response_model or writer_profile.model_name,
                "task_role": writer_profile.task_role,
                "profile_id": writer_profile.profile_id,
                "profile_label": writer_profile.label,
                "prompt_shaping": dict(writer_profile.prompt_shaping),
            },
            "job_id": evaluation_case.job_id,
            "run_id": evaluation_case.run_id,
            "selected_snapshot_ids": evaluation_case.selected_snapshot_ids,
            "writer_brief_id": evaluation_case.writer_brief_id,
            "evidence_pack_id": evaluation_case.evidence_pack_id,
            "status": "generated",
            "notes": f"Generated from evaluation case {evaluation_case.evaluation_case_id}.",
        }

    def _complete_with_writer_profile(
        self,
        *,
        evaluation_case: EvaluationCase,
        writer_profile: BenchmarkWriterProfile,
    ) -> tuple[str, str]:
        if writer_profile.provider_name == "openai":
            if self.openai_service is None:
                raise RuntimeError("Benchmark execution requires openai_service")
            response = self.openai_service.complete_messages(
                system_prompt=self._build_system_prompt(writer_profile),
                user_prompt=self._build_user_prompt(evaluation_case, writer_profile),
                model=writer_profile.model_name,
                temperature=0.4,
            )
            return response.output_text, response.model

        if writer_profile.provider_name == "anthropic":
            if self.anthropic_service is None:
                raise RuntimeError("Benchmark execution requires anthropic_service")
            response = self.anthropic_service.complete_messages(
                system_prompt=self._build_system_prompt(writer_profile),
                user_prompt=self._build_user_prompt(evaluation_case, writer_profile),
                model=writer_profile.model_name,
                temperature=0.4,
            )
            return response.output_text, response.model

        raise RuntimeError(f"Unsupported benchmark provider: {writer_profile.provider_name}")

    def _build_system_prompt(self, writer_profile: BenchmarkWriterProfile) -> str:
        prompt_shaping = ", ".join(
            f"{key}={value}"
            for key, value in sorted(writer_profile.prompt_shaping.items())
        )
        return (
            "Du erzeugst interne Benchmark-Kandidaten fuer grounded Content-Ausgaben.\n"
            "Ausgabe: nur rohe Inhaltszeilen, kein Dict, kein JSON, keine Feldnamen.\n"
            "Nenne niemals Provider oder Modell im Output.\n"
            f"Writer-Profil: {writer_profile.label} ({prompt_shaping})."
        )

    def _build_user_prompt(
        self,
        evaluation_case: EvaluationCase,
        writer_profile: BenchmarkWriterProfile,
    ) -> str:
        ref_items: list[str] = [
            str(item) for item in (evaluation_case.generated_output.get("items") or [])
            if str(item).strip()
        ]
        if ref_items:
            items_block = "\n".join(f"- {item}" for item in ref_items)
            referenz_section = f"Referenz-Items ({len(ref_items)} Stueck):\n{items_block}"
        else:
            referenz_section = (
                f"Referenz-Flow: {evaluation_case.source_flow} / {evaluation_case.target_platform}"
            )

        return (
            f"Evaluation-Case-ID: {evaluation_case.evaluation_case_id}\n"
            f"Quelle: {evaluation_case.source_flow} / {evaluation_case.source_action_type}\n"
            f"Plattform: {evaluation_case.target_platform}\n"
            f"Snapshot-IDs: {', '.join(evaluation_case.selected_snapshot_ids)}\n"
            f"Writer-Brief-ID: {evaluation_case.writer_brief_id}\n"
            f"Evidence-Pack-ID: {evaluation_case.evidence_pack_id or '-'}\n"
            f"Eingangskontext: {evaluation_case.input_context}\n"
            f"{referenz_section}\n"
            f"Writer-Profil-ID: {writer_profile.profile_id}\n"
            "Erzeuge genau eine alternative Ausgabe fuer denselben Fall.\n"
            "Gib nur die alternativen Inhalts-Items aus, einen pro Zeile."
            " Kein Dict, kein JSON, keine Feldnamen, keine Erklaerungen."
        )
