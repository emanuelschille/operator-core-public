from operator_core.core.analysis_foundation.models import (
    AnalysisFoundationResult,
    AnalysisSnapshot,
    EvidencePack,
    ModelExecutionMeta,
    WriterBrief,
)


def test_analysis_foundation_models_serialize_cleanly() -> None:
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
        notes=("prepared",),
    )
    snapshot = AnalysisSnapshot(
        snapshot_id="as_1",
        project_key="everydayengel",
        scope="platform",
        created_at="2026-04-13T10:00:00+00:00",
        title="TikTok analysis snapshot",
        summary="TikTok snapshot for monday: 20:06",
        platform_key="tiktok",
        analytics_summary_lines=("Post count: 4",),
        rule_summary_lines=("Audience: Frauen 23-38",),
        source_refs=("ok:posting_schedule_tiktok_monday",),
        posting_context={"enabled": True, "time_local": "20:06"},
    )
    writer_brief = WriterBrief(
        brief_id="wb_1",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        objective="Turn analysis into a writer-ready brief.",
        audience="Frauen 23-38",
        constraints=("Use explicit evidence.",),
        source_snapshot_ids=("as_1",),
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="writer",
        execution_meta=execution_meta,
    )
    evidence_pack = EvidencePack(
        evidence_pack_id="ep_1",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Evidence pack",
        snapshot_ids=("as_1",),
        source_refs=("analytics:global_recent",),
        evidence_lines=("TikTok: 20:06",),
    )
    result = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared.",
        analysis_snapshots=(snapshot,),
        writer_brief=writer_brief,
        evidence_pack=evidence_pack,
        execution_meta=execution_meta,
    )

    serialized = result.to_snapshot()

    assert serialized["lane_name"] == "analysis_foundation"
    assert serialized["analysis_snapshots"][0]["platform_key"] == "tiktok"
    assert serialized["writer_brief"]["execution_meta"]["provider_name"] == "openai"
    assert serialized["evidence_pack"]["evidence_lines"] == ["TikTok: 20:06"]
