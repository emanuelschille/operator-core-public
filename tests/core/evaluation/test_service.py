from operator_core.core.evaluation.service import EvaluationService


def test_evaluation_service_creates_case_from_grounded_output() -> None:
    service = EvaluationService()

    case = service.create_case(
        source_flow="vollauto",
        source_action_type="vollauto",
        target_platform="tiktok",
        selected_snapshot_ids=("as_platform", "as_cross"),
        writer_brief_id="wb_1",
        evidence_pack_id="ep_1",
        job_id="job_1",
        run_id="run_1",
        input_context={"command_body": "tiktok morgenroutine", "summary": "input"},
        generated_output={"action_type": "vollauto", "items": ["Title: Ruhiger Morgen"]},
        model_provider={"provider_name": "openai", "model_name": "gpt-5.4", "task_role": "writer"},
        created_at="2026-04-13T10:00:00+00:00",
    )

    assert case.evaluation_case_id.startswith("ec_")
    assert case.writer_brief_id == "wb_1"
    assert case.evidence_pack_id == "ep_1"
    assert case.selected_snapshot_ids == ("as_platform", "as_cross")
    assert case.generated_output["action_type"] == "vollauto"


def test_evaluation_service_builds_blind_review_export_without_provider_branding() -> None:
    service = EvaluationService()
    case = service.create_case(
        source_flow="caption",
        source_action_type="caption",
        target_platform="youtube_short",
        selected_snapshot_ids=("as_platform", "as_cross"),
        writer_brief_id="wb_2",
        evidence_pack_id="ep_2",
        job_id="job_2",
        run_id="run_2",
        input_context={"command_body": "youtube_short morgenroutine"},
        generated_output={"action_type": "caption", "items": ["Caption: Neue Caption"]},
        model_provider={"provider_name": "openai", "model_name": "gpt-5.4-mini", "task_role": "writer"},
        created_at="2026-04-13T10:00:00+00:00",
    )

    export = service.build_blind_review_export(
        evaluation_cases=(case,),
        created_at="2026-04-13T10:05:00+00:00",
    )

    reviewer_payload = export.reviewer_payload()
    internal_payload = export.internal_payload()

    assert reviewer_payload["entries"][0]["source_flow"] == "caption"
    assert reviewer_payload["entries"][0]["target_platform"] == "youtube_short"
    assert "provider_name" not in reviewer_payload["entries"][0]
    assert "model_name" not in reviewer_payload["entries"][0]
    assert internal_payload["linkage"][0]["evaluation_case_id"] == case.evaluation_case_id
    assert internal_payload["linkage"][0]["provider_name"] == "openai"
    assert internal_payload["linkage"][0]["model_name"] == "gpt-5.4-mini"
    assert internal_payload["linkage"][0]["writer_brief_id"] == "wb_2"
    assert internal_payload["linkage"][0]["evidence_pack_id"] == "ep_2"


def test_evaluation_service_creates_benchmark_run_from_case() -> None:
    service = EvaluationService()
    case = service.create_case(
        source_flow="hook",
        source_action_type="hook",
        target_platform="tiktok",
        selected_snapshot_ids=("as_platform", "as_cross"),
        writer_brief_id="wb_3",
        evidence_pack_id="ep_3",
        job_id="job_3",
        run_id="run_3",
        input_context={"command_body": "morgenroutine"},
        generated_output={"action_type": "hook", "items": ["Hook: Was waere wenn dein Morgen leichter wird?"]},
        model_provider={"provider_name": "openai", "model_name": "gpt-5.4", "task_role": "writer"},
        created_at="2026-04-13T10:00:00+00:00",
    )

    benchmark_run = service.create_benchmark_run_from_case(
        evaluation_case=case,
        benchmark_label="writer_groundwork",
        created_at="2026-04-13T10:15:00+00:00",
        candidate_payloads=(
            {
                "generated_output": {"action_type": "hook", "items": ["Hook: Dein Morgen darf leichter anfangen."]},
                "model_provider": {
                    "provider_name": "openai",
                    "model_name": "gpt-5.4-mini",
                    "task_role": "writer",
                },
                "job_id": "job_4",
                "run_id": "run_4",
                "selected_snapshot_ids": ("as_platform", "as_cross"),
                "writer_brief_id": "wb_3",
                "evidence_pack_id": "ep_4",
            },
        ),
    )

    assert benchmark_run.benchmark_run_id.startswith("br_")
    assert benchmark_run.evaluation_case_id == case.evaluation_case_id
    assert benchmark_run.benchmark_label == "writer_groundwork"
    assert len(benchmark_run.candidates) == 2
    assert benchmark_run.candidates[0].reviewer_label == "Candidate A"
    assert benchmark_run.candidates[1].reviewer_label == "Candidate B"
    assert benchmark_run.candidates[1].evaluation_case_id == case.evaluation_case_id
    assert benchmark_run.candidates[1].model_provider["model_name"] == "gpt-5.4-mini"


def test_evaluation_service_builds_benchmark_blind_review_export() -> None:
    service = EvaluationService()
    case = service.create_case(
        source_flow="caption",
        source_action_type="caption",
        target_platform="instagram",
        selected_snapshot_ids=("as_platform", "as_cross"),
        writer_brief_id="wb_5",
        evidence_pack_id="ep_5",
        job_id="job_5",
        run_id="run_5",
        input_context={"command_body": "abendroutine"},
        generated_output={"action_type": "caption", "items": ["Caption: Eine ruhigere Abendroutine beginnt klein."]},
        model_provider={"provider_name": "openai", "model_name": "gpt-5.4", "task_role": "writer"},
        created_at="2026-04-13T10:00:00+00:00",
    )
    benchmark_run = service.create_benchmark_run_from_case(
        evaluation_case=case,
        benchmark_label="blind_review_seed",
        created_at="2026-04-13T10:15:00+00:00",
        candidate_payloads=(
            {
                "reviewer_label": "Candidate B",
                "generated_output": {"action_type": "caption", "items": ["Caption: Kleine Rituale machen den Abend ruhiger."]},
                "model_provider": {
                    "provider_name": "openai",
                    "model_name": "gpt-5.4-mini",
                    "task_role": "writer",
                },
                "job_id": "job_6",
                "run_id": "run_6",
                "selected_snapshot_ids": ("as_platform", "as_cross"),
                "writer_brief_id": "wb_5",
                "evidence_pack_id": "ep_6",
            },
        ),
    )

    export = service.build_benchmark_blind_review_export(
        benchmark_run=benchmark_run,
        created_at="2026-04-13T10:20:00+00:00",
    )

    reviewer_payload = export.reviewer_payload()
    internal_payload = export.internal_payload()

    assert reviewer_payload["benchmark_run_id"] == benchmark_run.benchmark_run_id
    assert reviewer_payload["evaluation_case_id"] == case.evaluation_case_id
    assert reviewer_payload["entries"][0]["reviewer_label"] == "Candidate A"
    assert reviewer_payload["entries"][1]["reviewer_label"] == "Candidate B"
    assert "provider_name" not in reviewer_payload["entries"][0]
    assert "model_name" not in reviewer_payload["entries"][1]
    assert internal_payload["linkage"][0]["candidate_id"] == benchmark_run.candidates[0].candidate_id
    assert internal_payload["linkage"][1]["benchmark_run_id"] == benchmark_run.benchmark_run_id
    assert internal_payload["linkage"][1]["selected_snapshot_ids"] == ["as_platform", "as_cross"]
    assert internal_payload["linkage"][1]["model_name"] == "gpt-5.4-mini"
