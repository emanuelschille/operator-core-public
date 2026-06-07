from unittest.mock import MagicMock

from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.execution_service import ExecutionService, ExecutionStepResult
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.models import RequestContext
from operator_core.core.backbone.repositories import (
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryRunRepository,
)
from operator_core.core.backbone.run_service import RunService
from operator_core.core.backbone.statuses import JobStatus


def build_execution_service(executor=None):
    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    return (
        ExecutionService(
            job_service=JobService(job_repo),
            run_service=RunService(run_repo),
            event_log_service=EventLogService(event_repo),
            executor=executor,
        ),
        job_repo,
        run_repo,
        event_repo,
    )


def request_context() -> RequestContext:
    return RequestContext(
        request_id="req_1",
        project_key="everydayengel",
        source_type="telegram",
        source_user_id="u1",
        source_chat_id="c1",
        source_message_id="m1",
        command_name="start",
        request_text="hello",
    )


def test_execution_service_success_flow() -> None:
    service, job_repo, run_repo, event_repo = build_execution_service()

    result = service.execute_request(request_context(), job_type="inbound_request", title="Inbound request")

    job = job_repo.get(result.job_id)
    run = run_repo.get(result.run_id)

    assert result.job_status == JobStatus.COMPLETED
    assert result.run_status == "succeeded"
    assert job is not None and job.latest_run_id == run.run_id
    assert run is not None and run.status.value == "succeeded"
    assert len(event_repo.list_for_entity("everydayengel", "job", result.job_id)) == 3
    assert len(event_repo.list_for_entity("everydayengel", "run", result.run_id)) == 3


def test_execution_service_failure_flow() -> None:
    def failing_executor(request_context, job, run):
        raise RuntimeError("simulated failure")

    service, job_repo, run_repo, event_repo = build_execution_service(executor=failing_executor)

    result = service.execute_request(request_context(), job_type="inbound_request", title="Inbound request")

    job = job_repo.get(result.job_id)
    run = run_repo.get(result.run_id)

    assert result.job_status == JobStatus.FAILED
    assert result.run_status == "failed"
    assert result.error_summary == "simulated failure"
    assert job is not None and job.error_summary == "simulated failure"
    assert run is not None and run.error_detail == "simulated failure"
    assert len(event_repo.list_for_entity("everydayengel", "job", result.job_id)) == 4
    assert len(event_repo.list_for_entity("everydayengel", "run", result.run_id)) == 3


def test_execution_service_waiting_for_input_flow() -> None:
    def waiting_executor(request_context, job, run):
        return ExecutionStepResult(
            output_snapshot={"state": "awaiting_user"},
            result_summary="Need human approval",
            job_status=JobStatus.WAITING_FOR_INPUT,
        )

    service, job_repo, run_repo, event_repo = build_execution_service(executor=waiting_executor)

    result = service.execute_request(request_context(), job_type="inbound_request", title="Inbound request")

    job = job_repo.get(result.job_id)
    run = run_repo.get(result.run_id)

    assert result.job_status == JobStatus.WAITING_FOR_INPUT
    assert result.run_status == "succeeded"
    assert job is not None and job.result_summary == "Need human approval"
    assert run is not None and run.status.value == "succeeded"
    assert len(event_repo.list_for_entity("everydayengel", "job", result.job_id)) == 3
    assert len(event_repo.list_for_entity("everydayengel", "run", result.run_id)) == 3


def test_execution_service_writes_trace_events_from_step_result() -> None:
    from operator_core.core.backbone.execution_service import ExecutionTraceEvent

    def tracing_executor(request_context, job, run):
        return ExecutionStepResult(
            output_snapshot={"state": "done"},
            result_summary="Prepared analysis foundation",
            trace_events=(
                ExecutionTraceEvent(
                    entity_type="job",
                    entity_id=job.job_id,
                    event_type="analysis.snapshot_built",
                    message="Analysis snapshots prepared",
                    payload_json={"snapshot_ids": ["as_1"]},
                ),
                ExecutionTraceEvent(
                    entity_type="run",
                    entity_id=run.run_id,
                    event_type="analysis.evidence_pack_built",
                    message="Evidence pack prepared",
                    payload_json={"evidence_pack_id": "ep_1"},
                ),
            ),
        )

    service, _job_repo, _run_repo, event_repo = build_execution_service(executor=tracing_executor)

    result = service.execute_request(request_context(), job_type="analysis_snapshot", title="Analysis snapshot")

    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)

    assert [event.event_type for event in job_events][-1] == "analysis.snapshot_built"
    assert [event.event_type for event in run_events][-1] == "analysis.evidence_pack_built"


def test_execution_service_default_executor_handles_analysis_snapshot() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=(
            AnalysisSnapshot(
                snapshot_id="as_1",
                project_key="everydayengel",
                scope="platform",
                created_at="2026-04-13T10:00:00+00:00",
                title="TikTok",
                summary="TikTok snapshot",
                platform_key="tiktok",
            ),
        ),
        writer_brief=WriterBrief(
            brief_id="wb_1",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_1",),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_1",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Evidence",
            snapshot_ids=("as_1",),
            source_refs=("docs:project-state",),
            evidence_lines=("TikTok: 20:06",),
        ),
        execution_meta=execution_meta,
    )

    airtable_service = MagicMock()
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshot1", fields={"snapshot_id": "as_1"}),
        AirtableRecord(record_id="recEvidence1", fields={"evidence_pack_id": "ep_1"}),
    )

    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "analysis_snapshot"

    result = service.execute_request(req, job_type="analysis_snapshot", title="Analysis snapshot")

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["lane_name"] == "analysis_foundation"
    assert result.output_snapshot["analysis_snapshots"][0]["airtable_record_id"] == "recSnapshot1"
    assert result.output_snapshot["evidence_pack"]["airtable_record_id"] == "recEvidence1"
    assert [event.event_type for event in event_repo.list_for_entity("everydayengel", "job", result.job_id)][-1] == "analysis.snapshot_built"
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    assert [event.event_type for event in run_events][-2:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
    ]
    assert run_events[-1].payload_json["job_id"] == result.job_id
    assert run_events[-1].payload_json["run_id"] == result.run_id


def test_execution_service_foundation_backed_idea_persists_evidence_and_trace() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.core.content_ops.models import ContentOpResult, FoundationIdeaResult
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    selected_snapshots = (
        AnalysisSnapshot(
            snapshot_id="as_platform",
            project_key="everydayengel",
            scope="platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="TikTok",
            summary="TikTok snapshot",
            platform_key="tiktok",
        ),
        AnalysisSnapshot(
            snapshot_id="as_cross",
            project_key="everydayengel",
            scope="cross_platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="Cross-platform",
            summary="Cross snapshot",
        ),
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=selected_snapshots,
        writer_brief=WriterBrief(
            brief_id="wb_idea",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Base evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("docs:project-state",),
            evidence_lines=("base",),
        ),
        execution_meta=execution_meta,
    )

    content_ops_service = MagicMock()
    content_ops_service.supports.side_effect = lambda action: action == "idea"
    content_ops_service.can_use_foundation_backed_idea.return_value = True
    content_ops_service.generate_idea_from_foundation.return_value = FoundationIdeaResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="idea",
            command_body="morgenroutine",
            title="Content idea",
            summary="Idee generiert.",
            items=("Idee: Ehrliche Morgenroutine.",),
            openai_used=True,
            airtable_record_id="recIdea001",
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_idea",
        ),
        selected_snapshots=selected_snapshots,
        writer_brief=analysis_service.handle.return_value.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            status="completed",
        ),
    )
    content_ops_service.build_idea_evidence_pack.return_value = EvidencePack(
        evidence_pack_id="ep_idea",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Idea evidence",
        snapshot_ids=("as_platform", "as_cross"),
        source_refs=("docs:project-state",),
        evidence_lines=("Idea output: Idee: Ehrliche Morgenroutine.",),
    )

    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshotPlatform", fields={"snapshot_id": "as_platform"}),
        AirtableRecord(record_id="recSnapshotCross", fields={"snapshot_id": "as_cross"}),
        AirtableRecord(record_id="recEvidenceIdea", fields={"evidence_pack_id": "ep_idea"}),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        content_ops_service=content_ops_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "idea"
    req.command_body = "morgenroutine"

    result = service.execute_request(req, job_type="content_idea", title="Idea")

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["action_type"] == "idea"
    assert result.output_snapshot["writer_brief_id"] == "wb_idea"
    assert result.output_snapshot["evidence_pack_id"] == "ep_idea"
    assert result.output_snapshot["evidence_pack_record_id"] == "recEvidenceIdea"
    assert result.output_snapshot["foundation_snapshot_ids"] == ["as_platform", "as_cross"]
    assert result.output_snapshot["evaluation_case"]["writer_brief_id"] == "wb_idea"
    assert result.output_snapshot["evaluation_case"]["evidence_pack_id"] == "ep_idea"
    assert result.output_snapshot["evaluation_case"]["job_id"] == result.job_id
    assert result.output_snapshot["evaluation_case"]["run_id"] == result.run_id
    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    assert [event.event_type for event in job_events][-1] == "content.idea_generated"
    assert [event.event_type for event in run_events][-3:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
        "evaluation.case_created",
    ]
    assert run_events[-2].payload_json["execution_meta"]["task_role"] == "writer"
    assert run_events[-2].payload_json["job_id"] == result.job_id
    assert run_events[-2].payload_json["run_id"] == result.run_id


def test_execution_service_foundation_backed_vollauto_persists_evidence_and_trace() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.core.content_ops.models import ContentOpResult, FoundationDraftResult
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    selected_snapshots = (
        AnalysisSnapshot(
            snapshot_id="as_platform",
            project_key="everydayengel",
            scope="platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="TikTok",
            summary="TikTok snapshot",
            platform_key="tiktok",
        ),
        AnalysisSnapshot(
            snapshot_id="as_cross",
            project_key="everydayengel",
            scope="cross_platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="Cross-platform",
            summary="Cross snapshot",
        ),
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=selected_snapshots,
        writer_brief=WriterBrief(
            brief_id="wb_vollauto",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Base evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("docs:project-state",),
            evidence_lines=("base",),
        ),
        execution_meta=execution_meta,
    )

    content_ops_service = MagicMock()
    content_ops_service.supports.side_effect = lambda action: action == "vollauto"
    content_ops_service.can_use_foundation_backed_vollauto.return_value = True
    content_ops_service.generate_vollauto_from_foundation.return_value = FoundationDraftResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="vollauto",
            command_body="morgenroutine",
            title="Content draft",
            summary="Voll Auto generiert.",
            items=(
                "Serie/Thema: Morgenroutine",
                "Title: Kleine Schritte entlasten den Start in den Tag.",
            ),
            openai_used=True,
            airtable_record_id="recDraft001",
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_vollauto",
        ),
        selected_snapshots=selected_snapshots,
        writer_brief=analysis_service.handle.return_value.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            status="completed",
        ),
    )
    content_ops_service.build_vollauto_evidence_pack.return_value = EvidencePack(
        evidence_pack_id="ep_vollauto",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Vollauto evidence",
        snapshot_ids=("as_platform", "as_cross"),
        source_refs=("docs:project-state",),
        evidence_lines=("Draft output: Serie/Thema: Morgenroutine",),
    )

    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshotPlatform", fields={"snapshot_id": "as_platform"}),
        AirtableRecord(record_id="recSnapshotCross", fields={"snapshot_id": "as_cross"}),
        AirtableRecord(record_id="recEvidenceVollauto", fields={"evidence_pack_id": "ep_vollauto"}),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        content_ops_service=content_ops_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "vollauto"
    req.command_body = "morgenroutine"

    # `vollauto` is confirmation-gated: the request parks for approval, then resumes.
    gated = service.execute_request(req, job_type="content_draft", title="Voll Auto")
    assert gated.job_status == JobStatus.WAITING_FOR_APPROVAL
    result = service.resume_confirmed_job(gated.job_id)

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["action_type"] == "vollauto"
    assert result.output_snapshot["writer_brief_id"] == "wb_vollauto"
    assert result.output_snapshot["evidence_pack_id"] == "ep_vollauto"
    assert result.output_snapshot["evidence_pack_record_id"] == "recEvidenceVollauto"
    assert result.output_snapshot["foundation_snapshot_ids"] == ["as_platform", "as_cross"]
    assert result.output_snapshot["evaluation_case"]["writer_brief_id"] == "wb_vollauto"
    assert result.output_snapshot["evaluation_case"]["evidence_pack_id"] == "ep_vollauto"
    assert result.output_snapshot["evaluation_case"]["job_id"] == result.job_id
    assert result.output_snapshot["evaluation_case"]["run_id"] == result.run_id
    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    job_event_types = [event.event_type for event in job_events]
    # the lane's domain event is recorded; confirmation resolution closes the job trail
    assert "content.vollauto_generated" in job_event_types
    assert job_event_types[-1] == "confirmation_resolved"
    assert [event.event_type for event in run_events][-3:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
        "evaluation.case_created",
    ]
    assert run_events[-2].payload_json["execution_meta"]["task_role"] == "writer"
    assert run_events[-1].payload_json["job_id"] == result.job_id
    assert run_events[-1].payload_json["run_id"] == result.run_id


def test_execution_service_foundation_backed_draft_persists_evidence_and_trace() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.core.content_ops.models import ContentOpResult, FoundationDraftResult
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    selected_snapshots = (
        AnalysisSnapshot(
            snapshot_id="as_platform",
            project_key="everydayengel",
            scope="platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="TikTok",
            summary="TikTok snapshot",
            platform_key="tiktok",
        ),
        AnalysisSnapshot(
            snapshot_id="as_cross",
            project_key="everydayengel",
            scope="cross_platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="Cross-platform",
            summary="Cross snapshot",
        ),
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=selected_snapshots,
        writer_brief=WriterBrief(
            brief_id="wb_draft",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Base evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("docs:project-state",),
            evidence_lines=("base",),
        ),
        execution_meta=execution_meta,
    )

    content_ops_service = MagicMock()
    content_ops_service.supports.side_effect = lambda action: action == "draft"
    content_ops_service.can_use_foundation_backed_draft.return_value = True
    content_ops_service.generate_draft_from_foundation.return_value = FoundationDraftResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="draft",
            command_body="morgenroutine",
            title="Content draft",
            summary="Entwurf generiert.",
            items=(
                "Serie/Thema: Morgenroutine",
                "Title: Kleine Schritte entlasten den Start in den Tag.",
            ),
            openai_used=True,
            airtable_record_id="recDraft002",
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_draft",
        ),
        selected_snapshots=selected_snapshots,
        writer_brief=analysis_service.handle.return_value.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            status="completed",
        ),
    )
    content_ops_service.build_draft_evidence_pack.return_value = EvidencePack(
        evidence_pack_id="ep_draft",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Draft evidence",
        snapshot_ids=("as_platform", "as_cross"),
        source_refs=("docs:project-state",),
        evidence_lines=("Draft output: Serie/Thema: Morgenroutine",),
    )

    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshotPlatform", fields={"snapshot_id": "as_platform"}),
        AirtableRecord(record_id="recSnapshotCross", fields={"snapshot_id": "as_cross"}),
        AirtableRecord(record_id="recEvidenceDraft", fields={"evidence_pack_id": "ep_draft"}),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        content_ops_service=content_ops_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "draft"
    req.command_body = "morgenroutine"

    result = service.execute_request(req, job_type="content_draft", title="Entwurf")

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["action_type"] == "draft"
    assert result.output_snapshot["writer_brief_id"] == "wb_draft"
    assert result.output_snapshot["evidence_pack_id"] == "ep_draft"
    assert result.output_snapshot["evidence_pack_record_id"] == "recEvidenceDraft"
    assert result.output_snapshot["foundation_snapshot_ids"] == ["as_platform", "as_cross"]
    assert result.output_snapshot["evaluation_case"]["writer_brief_id"] == "wb_draft"
    assert result.output_snapshot["evaluation_case"]["evidence_pack_id"] == "ep_draft"
    assert result.output_snapshot["evaluation_case"]["job_id"] == result.job_id
    assert result.output_snapshot["evaluation_case"]["run_id"] == result.run_id
    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    assert [event.event_type for event in job_events][-1] == "content.draft_generated"
    assert [event.event_type for event in run_events][-3:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
        "evaluation.case_created",
    ]
    assert run_events[-2].payload_json["execution_meta"]["task_role"] == "writer"
    assert run_events[-1].payload_json["job_id"] == result.job_id
    assert run_events[-1].payload_json["run_id"] == result.run_id


def test_execute_content_mutation_foundation_backed_followup_persists_evidence_and_trace() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.core.content_ops.models import ContentOpResult, FoundationFollowupResult
    from operator_core.core.content_ops.proposal_store import ContentProposal
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    selected_snapshots = (
        AnalysisSnapshot(
            snapshot_id="as_platform",
            project_key="everydayengel",
            scope="platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="TikTok",
            summary="TikTok snapshot",
            platform_key="tiktok",
        ),
        AnalysisSnapshot(
            snapshot_id="as_cross",
            project_key="everydayengel",
            scope="cross_platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="Cross-platform",
            summary="Cross snapshot",
        ),
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=selected_snapshots,
        writer_brief=WriterBrief(
            brief_id="wb_followup",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Base evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("docs:project-state",),
            evidence_lines=("base",),
        ),
        execution_meta=execution_meta,
    )

    content_ops_service = MagicMock()
    content_ops_service.resolve_platform_hint.return_value = ("", "mach CTA direkter")
    content_ops_service.can_use_foundation_backed_followup.return_value = True
    content_ops_service.generate_followup_from_foundation.return_value = FoundationFollowupResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="followup",
            command_body="tiktok morgenroutine",
            title="Follow-up",
            summary="Vorschlag aktualisiert.",
            items=("CTA: Neue CTA",),
            openai_used=True,
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_followup",
        ),
        selected_snapshots=selected_snapshots,
        writer_brief=analysis_service.handle.return_value.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            status="completed",
        ),
        instruction="mach CTA direkter",
        mutation_mode="followup",
        source_action_type="vollauto",
    )
    content_ops_service.build_followup_evidence_pack.return_value = EvidencePack(
        evidence_pack_id="ep_followup",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Follow-up evidence",
        snapshot_ids=("as_platform", "as_cross"),
        source_refs=("docs:project-state",),
        evidence_lines=("Follow-up output: CTA: Neue CTA",),
    )

    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshotPlatform", fields={"snapshot_id": "as_platform"}),
        AirtableRecord(record_id="recSnapshotCross", fields={"snapshot_id": "as_cross"}),
        AirtableRecord(record_id="recEvidenceFollowup", fields={"evidence_pack_id": "ep_followup"}),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        content_ops_service=content_ops_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "followup"
    req.command_body = "mach CTA direkter"
    proposal = ContentProposal(
        proposal_id="job-proposal-1",
        project_key="everydayengel",
        action_type="vollauto",
        platform="tiktok",
        fields={"cta": "Alte CTA"},
        source_command_body="tiktok morgenroutine",
    )

    result = service.execute_content_mutation(
        req,
        proposal=proposal,
        instruction="mach CTA direkter",
        mutation_mode="followup",
        title="Follow-up request",
    )

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["action_type"] == "followup"
    assert result.output_snapshot["writer_brief_id"] == "wb_followup"
    assert result.output_snapshot["evidence_pack_id"] == "ep_followup"
    assert result.output_snapshot["evidence_pack_record_id"] == "recEvidenceFollowup"
    assert result.output_snapshot["foundation_snapshot_ids"] == ["as_platform", "as_cross"]
    assert result.output_snapshot["evaluation_case"]["writer_brief_id"] == "wb_followup"
    assert result.output_snapshot["evaluation_case"]["evidence_pack_id"] == "ep_followup"
    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    assert [event.event_type for event in job_events][-1] == "content.followup_generated"
    assert [event.event_type for event in run_events][-3:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
        "evaluation.case_created",
    ]
    assert run_events[-2].payload_json["execution_meta"]["task_role"] == "writer"


def test_execution_service_foundation_backed_caption_persists_evidence_and_trace() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.core.content_ops.models import ContentOpResult, FoundationCaptionResult
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    selected_snapshots = (
        AnalysisSnapshot(
            snapshot_id="as_platform",
            project_key="everydayengel",
            scope="platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="TikTok",
            summary="TikTok snapshot",
            platform_key="tiktok",
        ),
        AnalysisSnapshot(
            snapshot_id="as_cross",
            project_key="everydayengel",
            scope="cross_platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="Cross-platform",
            summary="Cross snapshot",
        ),
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=selected_snapshots,
        writer_brief=WriterBrief(
            brief_id="wb_caption",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Base evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("docs:project-state",),
            evidence_lines=("base",),
        ),
        execution_meta=execution_meta,
    )

    content_ops_service = MagicMock()
    content_ops_service.supports.side_effect = lambda action: action == "caption"
    content_ops_service.can_use_foundation_backed_caption.return_value = True
    content_ops_service.generate_caption_from_foundation.return_value = FoundationCaptionResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="caption",
            command_body="morgenroutine",
            title="Content caption",
            summary="Caption generiert.",
            items=(
                "Caption: Manchmal braucht der Morgen nur 10 Minuten.",
                "CTA-Richtung: Meinung",
            ),
            openai_used=True,
            airtable_record_id="recCaption001",
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_caption",
        ),
        selected_snapshots=selected_snapshots,
        writer_brief=analysis_service.handle.return_value.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            status="completed",
        ),
    )
    content_ops_service.build_caption_evidence_pack.return_value = EvidencePack(
        evidence_pack_id="ep_caption",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Caption evidence",
        snapshot_ids=("as_platform", "as_cross"),
        source_refs=("docs:project-state",),
        evidence_lines=("Caption output: Caption: Manchmal braucht der Morgen nur 10 Minuten.",),
    )

    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshotPlatform", fields={"snapshot_id": "as_platform"}),
        AirtableRecord(record_id="recSnapshotCross", fields={"snapshot_id": "as_cross"}),
        AirtableRecord(record_id="recEvidenceCaption", fields={"evidence_pack_id": "ep_caption"}),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        content_ops_service=content_ops_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "caption"
    req.command_body = "morgenroutine"

    result = service.execute_request(req, job_type="content_caption_generate", title="Caption")

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["action_type"] == "caption"
    assert result.output_snapshot["writer_brief_id"] == "wb_caption"
    assert result.output_snapshot["evidence_pack_id"] == "ep_caption"
    assert result.output_snapshot["evidence_pack_record_id"] == "recEvidenceCaption"
    assert result.output_snapshot["foundation_snapshot_ids"] == ["as_platform", "as_cross"]
    assert result.output_snapshot["evaluation_case"]["writer_brief_id"] == "wb_caption"
    assert result.output_snapshot["evaluation_case"]["evidence_pack_id"] == "ep_caption"
    assert result.output_snapshot["evaluation_case"]["job_id"] == result.job_id
    assert result.output_snapshot["evaluation_case"]["run_id"] == result.run_id
    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    assert [event.event_type for event in job_events][-1] == "content.caption_generated"
    assert [event.event_type for event in run_events][-3:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
        "evaluation.case_created",
    ]
    assert run_events[-2].payload_json["execution_meta"]["task_role"] == "writer"
    assert run_events[-1].payload_json["job_id"] == result.job_id
    assert run_events[-1].payload_json["run_id"] == result.run_id


def test_execution_service_foundation_backed_hook_persists_evidence_and_trace() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.core.content_ops.models import ContentOpResult, FoundationHookResult
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    selected_snapshots = (
        AnalysisSnapshot(
            snapshot_id="as_platform",
            project_key="everydayengel",
            scope="platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="TikTok",
            summary="TikTok snapshot",
            platform_key="tiktok",
        ),
        AnalysisSnapshot(
            snapshot_id="as_cross",
            project_key="everydayengel",
            scope="cross_platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="Cross-platform",
            summary="Cross snapshot",
        ),
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=selected_snapshots,
        writer_brief=WriterBrief(
            brief_id="wb_hook",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Base evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("docs:project-state",),
            evidence_lines=("base",),
        ),
        execution_meta=execution_meta,
    )

    content_ops_service = MagicMock()
    content_ops_service.supports.side_effect = lambda action: action == "hook"
    content_ops_service.can_use_foundation_backed_hook.return_value = True
    content_ops_service.generate_hook_from_foundation.return_value = FoundationHookResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="hook",
            command_body="morgenroutine",
            title="Content hook",
            summary="Hook generiert.",
            items=(
                "Hook-Typ: Neugier",
                "Eröffnung: Was wäre wenn dein Morgen entspannter wäre?",
            ),
            openai_used=True,
            airtable_record_id="recHook001",
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_hook",
        ),
        selected_snapshots=selected_snapshots,
        writer_brief=analysis_service.handle.return_value.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            status="completed",
        ),
    )
    content_ops_service.build_hook_evidence_pack.return_value = EvidencePack(
        evidence_pack_id="ep_hook",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Hook evidence",
        snapshot_ids=("as_platform", "as_cross"),
        source_refs=("docs:project-state",),
        evidence_lines=("Hook output: Hook-Typ: Neugier",),
    )

    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshotPlatform", fields={"snapshot_id": "as_platform"}),
        AirtableRecord(record_id="recSnapshotCross", fields={"snapshot_id": "as_cross"}),
        AirtableRecord(record_id="recEvidenceHook", fields={"evidence_pack_id": "ep_hook"}),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        content_ops_service=content_ops_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "hook"
    req.command_body = "morgenroutine"

    result = service.execute_request(req, job_type="content_hook_generate", title="Hook")

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["action_type"] == "hook"
    assert result.output_snapshot["writer_brief_id"] == "wb_hook"
    assert result.output_snapshot["evidence_pack_id"] == "ep_hook"
    assert result.output_snapshot["evidence_pack_record_id"] == "recEvidenceHook"
    assert result.output_snapshot["foundation_snapshot_ids"] == ["as_platform", "as_cross"]
    assert result.output_snapshot["evaluation_case"]["writer_brief_id"] == "wb_hook"
    assert result.output_snapshot["evaluation_case"]["evidence_pack_id"] == "ep_hook"
    assert result.output_snapshot["evaluation_case"]["job_id"] == result.job_id
    assert result.output_snapshot["evaluation_case"]["run_id"] == result.run_id
    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    assert [event.event_type for event in job_events][-1] == "content.hook_generated"
    assert [event.event_type for event in run_events][-3:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
        "evaluation.case_created",
    ]
    assert run_events[-2].payload_json["execution_meta"]["task_role"] == "writer"
    assert run_events[-1].payload_json["job_id"] == result.job_id
    assert run_events[-1].payload_json["run_id"] == result.run_id


def test_execution_service_foundation_backed_serie_persists_evidence_and_trace() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.core.content_ops.models import ContentOpResult, FoundationSerieResult
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    selected_snapshots = (
        AnalysisSnapshot(
            snapshot_id="as_platform",
            project_key="everydayengel",
            scope="platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="TikTok",
            summary="TikTok snapshot",
            platform_key="tiktok",
        ),
        AnalysisSnapshot(
            snapshot_id="as_cross",
            project_key="everydayengel",
            scope="cross_platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="Cross-platform",
            summary="Cross snapshot",
        ),
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=selected_snapshots,
        writer_brief=WriterBrief(
            brief_id="wb_serie",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Base evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("docs:project-state",),
            evidence_lines=("base",),
        ),
        execution_meta=execution_meta,
    )

    content_ops_service = MagicMock()
    content_ops_service.supports.side_effect = lambda action: action == "serie"
    content_ops_service.can_use_foundation_backed_serie.return_value = True
    content_ops_service.generate_serie_from_foundation.return_value = FoundationSerieResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="serie",
            command_body="morgenroutine",
            title="Serie/Thema",
            summary="Serie/Thema generiert.",
            items=("Serie/Thema: Ruhiger Morgen",),
            openai_used=True,
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_serie",
        ),
        selected_snapshots=selected_snapshots,
        writer_brief=analysis_service.handle.return_value.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            status="completed",
        ),
    )
    content_ops_service.build_serie_evidence_pack.return_value = EvidencePack(
        evidence_pack_id="ep_serie",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Serie evidence",
        snapshot_ids=("as_platform", "as_cross"),
        source_refs=("docs:project-state",),
        evidence_lines=("Serie output: Serie/Thema: Ruhiger Morgen",),
    )

    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshotPlatform", fields={"snapshot_id": "as_platform"}),
        AirtableRecord(record_id="recSnapshotCross", fields={"snapshot_id": "as_cross"}),
        AirtableRecord(record_id="recEvidenceSerie", fields={"evidence_pack_id": "ep_serie"}),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        content_ops_service=content_ops_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "serie"
    req.command_body = "morgenroutine"

    result = service.execute_request(req, job_type="content_serie_generate", title="Serie")

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["action_type"] == "serie"
    assert result.output_snapshot["writer_brief_id"] == "wb_serie"
    assert result.output_snapshot["evidence_pack_id"] == "ep_serie"
    assert result.output_snapshot["evidence_pack_record_id"] == "recEvidenceSerie"
    assert result.output_snapshot["foundation_snapshot_ids"] == ["as_platform", "as_cross"]
    assert result.output_snapshot["evaluation_case"]["writer_brief_id"] == "wb_serie"
    assert result.output_snapshot["evaluation_case"]["evidence_pack_id"] == "ep_serie"
    assert result.output_snapshot["evaluation_case"]["job_id"] == result.job_id
    assert result.output_snapshot["evaluation_case"]["run_id"] == result.run_id
    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    assert [event.event_type for event in job_events][-1] == "content.serie_generated"
    assert [event.event_type for event in run_events][-3:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
        "evaluation.case_created",
    ]
    assert run_events[-2].payload_json["execution_meta"]["task_role"] == "writer"
    assert run_events[-1].payload_json["job_id"] == result.job_id
    assert run_events[-1].payload_json["run_id"] == result.run_id


def test_execution_service_foundation_backed_title_persists_evidence_and_trace() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.core.content_ops.models import ContentOpResult, FoundationTitleResult
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    selected_snapshots = (
        AnalysisSnapshot(
            snapshot_id="as_platform",
            project_key="everydayengel",
            scope="platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="TikTok",
            summary="TikTok snapshot",
            platform_key="tiktok",
        ),
        AnalysisSnapshot(
            snapshot_id="as_cross",
            project_key="everydayengel",
            scope="cross_platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="Cross-platform",
            summary="Cross snapshot",
        ),
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=selected_snapshots,
        writer_brief=WriterBrief(
            brief_id="wb_title",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Base evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("docs:project-state",),
            evidence_lines=("base",),
        ),
        execution_meta=execution_meta,
    )

    content_ops_service = MagicMock()
    content_ops_service.supports.side_effect = lambda action: action == "title"
    content_ops_service.can_use_foundation_backed_title.return_value = True
    content_ops_service.generate_title_from_foundation.return_value = FoundationTitleResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="title",
            command_body="morgenroutine",
            title="Title",
            summary="Title generiert.",
            items=("Title: Ruhiger Morgen ohne Hektik",),
            openai_used=True,
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_title",
        ),
        selected_snapshots=selected_snapshots,
        writer_brief=analysis_service.handle.return_value.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            status="completed",
        ),
    )
    content_ops_service.build_title_evidence_pack.return_value = EvidencePack(
        evidence_pack_id="ep_title",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Title evidence",
        snapshot_ids=("as_platform", "as_cross"),
        source_refs=("docs:project-state",),
        evidence_lines=("Title output: Title: Ruhiger Morgen ohne Hektik",),
    )

    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshotPlatform", fields={"snapshot_id": "as_platform"}),
        AirtableRecord(record_id="recSnapshotCross", fields={"snapshot_id": "as_cross"}),
        AirtableRecord(record_id="recEvidenceTitle", fields={"evidence_pack_id": "ep_title"}),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        content_ops_service=content_ops_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "title"
    req.command_body = "morgenroutine"

    result = service.execute_request(req, job_type="content_title_generate", title="Title")

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["action_type"] == "title"
    assert result.output_snapshot["writer_brief_id"] == "wb_title"
    assert result.output_snapshot["evidence_pack_id"] == "ep_title"
    assert result.output_snapshot["evidence_pack_record_id"] == "recEvidenceTitle"
    assert result.output_snapshot["foundation_snapshot_ids"] == ["as_platform", "as_cross"]
    assert result.output_snapshot["evaluation_case"]["writer_brief_id"] == "wb_title"
    assert result.output_snapshot["evaluation_case"]["evidence_pack_id"] == "ep_title"
    assert result.output_snapshot["evaluation_case"]["job_id"] == result.job_id
    assert result.output_snapshot["evaluation_case"]["run_id"] == result.run_id
    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    assert [event.event_type for event in job_events][-1] == "content.title_generated"
    assert [event.event_type for event in run_events][-3:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
        "evaluation.case_created",
    ]
    assert run_events[-2].payload_json["execution_meta"]["task_role"] == "writer"
    assert run_events[-1].payload_json["job_id"] == result.job_id
    assert run_events[-1].payload_json["run_id"] == result.run_id


def test_execution_service_foundation_backed_cta_persists_evidence_and_trace() -> None:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult,
        AnalysisSnapshot,
        EvidencePack,
        ModelExecutionMeta,
        WriterBrief,
    )
    from operator_core.core.content_ops.models import ContentOpResult, FoundationCtaResult
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList

    analysis_service = MagicMock()
    analysis_service.supports.side_effect = lambda action: action == "analysis_snapshot"
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
    )
    selected_snapshots = (
        AnalysisSnapshot(
            snapshot_id="as_platform",
            project_key="everydayengel",
            scope="platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="TikTok",
            summary="TikTok snapshot",
            platform_key="tiktok",
        ),
        AnalysisSnapshot(
            snapshot_id="as_cross",
            project_key="everydayengel",
            scope="cross_platform",
            created_at="2026-04-13T10:00:00+00:00",
            title="Cross-platform",
            summary="Cross snapshot",
        ),
    )
    analysis_service.handle.return_value = AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=selected_snapshots,
        writer_brief=WriterBrief(
            brief_id="wb_cta",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Objective",
            audience="Audience",
            constraints=("Constraint",),
            source_snapshot_ids=("as_platform", "as_cross"),
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_base",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Base evidence",
            snapshot_ids=("as_platform", "as_cross"),
            source_refs=("docs:project-state",),
            evidence_lines=("base",),
        ),
        execution_meta=execution_meta,
    )

    content_ops_service = MagicMock()
    content_ops_service.supports.side_effect = lambda action: action == "cta"
    content_ops_service.can_use_foundation_backed_cta.return_value = True
    content_ops_service.generate_cta_from_foundation.return_value = FoundationCtaResult(
        content_result=ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="cta",
            command_body="morgenroutine",
            title="CTA",
            summary="CTA generiert.",
            items=("CTA: Speichere dir die Routine fuer morgen frueh.",),
            openai_used=True,
            platform="tiktok",
            foundation_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_cta",
        ),
        selected_snapshots=selected_snapshots,
        writer_brief=analysis_service.handle.return_value.writer_brief,
        execution_meta=ModelExecutionMeta(
            provider_name="openai",
            model_name="gpt-5.4",
            task_role="writer",
            status="completed",
        ),
    )
    content_ops_service.build_cta_evidence_pack.return_value = EvidencePack(
        evidence_pack_id="ep_cta",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="CTA evidence",
        snapshot_ids=("as_platform", "as_cross"),
        source_refs=("docs:project-state",),
        evidence_lines=("CTA output: CTA: Speichere dir die Routine fuer morgen frueh.",),
    )

    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshotPlatform", fields={"snapshot_id": "as_platform"}),
        AirtableRecord(record_id="recSnapshotCross", fields={"snapshot_id": "as_cross"}),
        AirtableRecord(record_id="recEvidenceCta", fields={"evidence_pack_id": "ep_cta"}),
    )

    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        analysis_foundation_service=analysis_service,
        content_ops_service=content_ops_service,
        airtable_service=airtable_service,
    )
    req = request_context()
    req.command_name = "cta"
    req.command_body = "morgenroutine"

    result = service.execute_request(req, job_type="content_cta_generate", title="CTA")

    assert result.job_status == JobStatus.COMPLETED
    assert result.output_snapshot["action_type"] == "cta"
    assert result.output_snapshot["writer_brief_id"] == "wb_cta"
    assert result.output_snapshot["evidence_pack_id"] == "ep_cta"
    assert result.output_snapshot["evidence_pack_record_id"] == "recEvidenceCta"
    assert result.output_snapshot["foundation_snapshot_ids"] == ["as_platform", "as_cross"]
    assert result.output_snapshot["evaluation_case"]["writer_brief_id"] == "wb_cta"
    assert result.output_snapshot["evaluation_case"]["evidence_pack_id"] == "ep_cta"
    assert result.output_snapshot["evaluation_case"]["job_id"] == result.job_id
    assert result.output_snapshot["evaluation_case"]["run_id"] == result.run_id
    job_events = event_repo.list_for_entity("everydayengel", "job", result.job_id)
    run_events = event_repo.list_for_entity("everydayengel", "run", result.run_id)
    assert [event.event_type for event in job_events][-1] == "content.cta_generated"
    assert [event.event_type for event in run_events][-3:] == [
        "analysis.snapshot_persisted",
        "analysis.evidence_pack_persisted",
        "evaluation.case_created",
    ]
    assert run_events[-2].payload_json["execution_meta"]["task_role"] == "writer"
    assert run_events[-1].payload_json["job_id"] == result.job_id
    assert run_events[-1].payload_json["run_id"] == result.run_id
