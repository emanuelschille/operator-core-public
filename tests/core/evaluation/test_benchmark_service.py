from __future__ import annotations

from dataclasses import dataclass

from operator_core.core.evaluation.benchmark_service import (
    BenchmarkExecutionRequest,
    BenchmarkExecutionService,
    BenchmarkWriterProfile,
)
from operator_core.core.evaluation.service import EvaluationService


@dataclass
class _FakeOpenAIResponse:
    model: str
    output_text: str


class _FakeOpenAIService:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = iter(outputs)
        self.calls: list[dict[str, object]] = []

    def complete_messages(
        self,
        *,
        system_prompt: str = "",
        user_prompt: str = "",
        model: str | None = None,
        temperature: float = 0.2,
    ) -> _FakeOpenAIResponse:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model": model,
                "temperature": temperature,
            }
        )
        return _FakeOpenAIResponse(
            model=str(model or ""),
            output_text=next(self._outputs),
        )


@dataclass
class _FakeAnthropicResponse:
    model: str
    output_text: str


class _FakeAnthropicService:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = iter(outputs)
        self.calls: list[dict[str, object]] = []

    def complete_messages(
        self,
        *,
        system_prompt: str = "",
        user_prompt: str = "",
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> _FakeAnthropicResponse:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return _FakeAnthropicResponse(
            model=str(model or ""),
            output_text=next(self._outputs),
        )


def _make_case() -> object:
    return EvaluationService().create_case(
        source_flow="caption",
        source_action_type="caption",
        target_platform="instagram",
        selected_snapshot_ids=("as_platform", "as_cross"),
        writer_brief_id="wb_case",
        evidence_pack_id="ep_case",
        job_id="job_case",
        run_id="run_case",
        input_context={"command_body": "abendroutine", "summary": "same grounded input"},
        generated_output={"action_type": "caption", "items": ["Caption: Eine ruhige Abendroutine beginnt klein."]},
        model_provider={"provider_name": "openai", "model_name": "gpt-5.4", "task_role": "writer"},
        created_at="2026-04-13T10:00:00+00:00",
    )


def test_user_prompt_does_not_expose_dict_repr_and_instructs_plain_items() -> None:
    """
    Root-cause regression: _build_user_prompt must not pass generated_output as a
    raw Python dict repr, and must not instruct the model to 'preserve field/list
    structure' (which causes it to echo the dict back verbatim).
    """
    service = BenchmarkExecutionService(evaluation_service=EvaluationService())
    case = _make_case()
    profile = BenchmarkWriterProfile(
        profile_id="openai_primary",
        label="OpenAI gpt-4o",
        provider_name="openai",
        model_name="gpt-4o",
        task_role="benchmark_writer",
        prompt_shaping={"variant": "control"},
    )

    prompt = service._build_user_prompt(case, profile)
    sys_prompt = service._build_system_prompt(profile)

    # The dict key "action_type" must NOT appear in the reference section of the prompt
    # (it should only be readable item lines, not a Python dict repr).
    assert "'action_type'" not in prompt, (
        "_build_user_prompt must not include raw dict repr of generated_output"
    )
    # The known item text must appear as a plain line.
    assert "Eine ruhige Abendroutine beginnt klein." in prompt, (
        "Reference item text must appear in prompt as a plain line"
    )
    # The broken instruction must not be present.
    assert "Felder-/Listenstruktur" not in prompt, (
        "Prompt must not instruct model to preserve dict structure"
    )
    # System prompt must not tell model to match the 'Ausgabeformat' of a dict.
    assert "Ausgabeformat wie die Referenzausgabe" not in sys_prompt, (
        "System prompt must not reinforce dict-format matching"
    )


def test_benchmark_execution_generates_multiple_candidates_for_same_case() -> None:
    evaluation_service = EvaluationService()
    openai_service = _FakeOpenAIService(
        [
            "Caption: Kleine Routinen machen den Abend leichter.",
            "Caption: Weniger Druck, mehr Ruhe am Abend.",
        ]
    )
    service = BenchmarkExecutionService(
        evaluation_service=evaluation_service,
        openai_service=openai_service,
    )
    case = _make_case()
    profiles = service.build_openai_writer_profiles(base_model="gpt-5.4")

    result = service.execute(
        BenchmarkExecutionRequest(
            evaluation_case=case,
            benchmark_label="caption_multi_candidate",
            created_at="2026-04-13T10:30:00+00:00",
            writer_profiles=profiles,
        )
    )

    assert len(openai_service.calls) == 2
    assert len(result.executed_profiles) == 2
    assert len(result.benchmark_run.candidates) == 3
    assert result.benchmark_run.candidates[0].reviewer_label == "Candidate A"
    assert result.benchmark_run.candidates[1].reviewer_label == "Candidate B"
    assert result.benchmark_run.candidates[2].reviewer_label == "Candidate C"
    assert all(
        candidate.evaluation_case_id == case.evaluation_case_id
        for candidate in result.benchmark_run.candidates
    )
    assert all(
        candidate.selected_snapshot_ids == ("as_platform", "as_cross")
        for candidate in result.benchmark_run.candidates
    )
    assert result.benchmark_run.candidates[1].writer_brief_id == "wb_case"
    assert result.benchmark_run.candidates[1].evidence_pack_id == "ep_case"
    assert result.benchmark_run.comparison_meta["source_case_included"] is True


def test_benchmark_execution_candidates_export_cleanly_to_blind_review() -> None:
    evaluation_service = EvaluationService()
    openai_service = _FakeOpenAIService(
        [
            "Caption: Der Abend wird ruhiger, wenn du klein beginnst.",
        ]
    )
    benchmark_service = BenchmarkExecutionService(
        evaluation_service=evaluation_service,
        openai_service=openai_service,
    )
    case = _make_case()
    profile = benchmark_service.build_openai_writer_profiles(base_model="gpt-5.4-mini")[:1]

    result = benchmark_service.execute(
        BenchmarkExecutionRequest(
            evaluation_case=case,
            benchmark_label="caption_blind_export",
            created_at="2026-04-13T10:45:00+00:00",
            writer_profiles=profile,
            include_source_case=False,
        )
    )
    export = evaluation_service.build_benchmark_blind_review_export(
        benchmark_run=result.benchmark_run,
        created_at="2026-04-13T10:50:00+00:00",
    )

    reviewer_payload = export.reviewer_payload()
    internal_payload = export.internal_payload()

    assert len(result.benchmark_run.candidates) == 1
    assert result.benchmark_run.candidates[0].reviewer_label == "Candidate A"
    assert reviewer_payload["entries"][0]["reviewer_label"] == "Candidate A"
    assert reviewer_payload["benchmark_run_id"] == result.benchmark_run.benchmark_run_id
    assert "provider_name" not in reviewer_payload["entries"][0]
    assert internal_payload["linkage"][0]["evaluation_case_id"] == case.evaluation_case_id
    assert internal_payload["linkage"][0]["writer_brief_id"] == "wb_case"
    assert internal_payload["linkage"][0]["evidence_pack_id"] == "ep_case"
    assert internal_payload["linkage"][0]["selected_snapshot_ids"] == ["as_platform", "as_cross"]


def test_benchmark_execution_supports_openai_and_anthropic_candidates() -> None:
    evaluation_service = EvaluationService()
    openai_service = _FakeOpenAIService(
        [
            "Caption: Kleine Gewohnheiten koennen den Abend ruhiger machen.",
            "Caption: Weniger Reibung am Abend beginnt oft mit einem kleinen festen Schritt.",
        ]
    )
    anthropic_service = _FakeAnthropicService(
        [
            "Caption: Ein entspannter Abend beginnt oft mit einem einzigen klaren Ritual.",
        ]
    )
    benchmark_service = BenchmarkExecutionService(
        evaluation_service=evaluation_service,
        openai_service=openai_service,
        anthropic_service=anthropic_service,
    )
    case = _make_case()
    profiles = benchmark_service.build_writer_profiles(
        openai_base_model="gpt-5.4",
        anthropic_model="claude-3-5-sonnet-20241022",
    )

    result = benchmark_service.execute(
        BenchmarkExecutionRequest(
            evaluation_case=case,
            benchmark_label="caption_cross_writer",
            created_at="2026-04-13T11:00:00+00:00",
            writer_profiles=profiles,
        )
    )
    export = evaluation_service.build_benchmark_blind_review_export(
        benchmark_run=result.benchmark_run,
        created_at="2026-04-13T11:05:00+00:00",
    )

    assert len(openai_service.calls) == 2
    assert len(anthropic_service.calls) == 1
    assert len(result.executed_profiles) == 3
    assert len(result.benchmark_run.candidates) == 4
    assert result.benchmark_run.candidates[1].model_provider["provider_name"] == "openai"
    assert result.benchmark_run.candidates[2].model_provider["provider_name"] == "openai"
    assert result.benchmark_run.candidates[3].model_provider["provider_name"] == "anthropic"
    assert result.benchmark_run.candidates[3].model_provider["model_name"] == "claude-3-5-sonnet-20241022"
    assert export.reviewer_payload()["entries"][3]["reviewer_label"] == "Candidate D"
    assert "provider_name" not in export.reviewer_payload()["entries"][3]
    assert export.internal_payload()["linkage"][3]["provider_name"] == "anthropic"
    assert export.internal_payload()["linkage"][3]["writer_brief_id"] == "wb_case"
    assert result.benchmark_run.comparison_meta["executed_profile_ids"] == [
        "openai_primary",
        "openai_mini",
        "anthropic_primary",
    ]
    assert result.benchmark_run.comparison_meta["executed_writers"][2]["provider_name"] == "anthropic"
    assert result.benchmark_run.comparison_meta["executed_writers"][2]["model_name"] == "claude-3-5-sonnet-20241022"
