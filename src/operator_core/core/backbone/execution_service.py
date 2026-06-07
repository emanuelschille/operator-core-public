from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol

from operator_core.core.analysis_foundation.service import AnalysisFoundationService
from operator_core.core.affiliate_ops.service import AffiliateOpsService
from operator_core.core.content_ops.service import ContentOpsService
from operator_core.core.evaluation.service import EvaluationService
from operator_core.core.funnel_ops.service import FunnelOpsService
from operator_core.core.knowledge_ops.service import KnowledgeOpsService
from operator_core.core.review_ops.service import ReviewOpsService
from operator_core.integrations.analysis_foundation_persistence import AnalysisFoundationPersistenceService

from operator_core.core.rules_engine import requires_confirmation

from .event_log_service import EventLogService
from .job_service import JobService
from .models import ApprovalState, Job, RequestContext, Run
from .run_service import RunService
from .statuses import JobStatus

if TYPE_CHECKING:
    from operator_core.core.content_ops.correction_capture import CommercialClassLog, CorrectionFileRepository
    from operator_core.core.content_ops.proposal_store import ContentProposal
    from operator_core.integrations.airtable_service import AirtableService
    from operator_core.integrations.openai_service import OpenAIService
    from operator_core.integrations.operational_knowledge_service import OperationalKnowledgeLoader
    from operator_core.integrations.analytics_service import AnalyticsLoader
    from operator_core.integrations.platform_signal_service import PlatformSignalLoader
    from operator_core.integrations.weekly_analysis_persistence import WeeklyAnalysisPersistenceService
    from operator_core.projects.docs import ProjectDocsLoader


class ExecutorFn(Protocol):
    def __call__(self, request_context: RequestContext, job: Job, run: Run) -> "ExecutionStepResult": ...


@dataclass(slots=True)
class ExecutionStepResult:
    output_snapshot: dict[str, Any]
    result_summary: str | None = None
    job_status: JobStatus = JobStatus.COMPLETED
    trace_events: tuple["ExecutionTraceEvent", ...] = ()


@dataclass(slots=True, frozen=True)
class ExecutionTraceEvent:
    entity_type: str
    entity_id: str
    event_type: str
    message: str
    payload_json: dict[str, Any]


@dataclass(slots=True)
class ExecutionResult:
    job_id: str
    run_id: str
    job_status: JobStatus
    run_status: str
    event_count: int
    approval_state: ApprovalState = ApprovalState.NOT_REQUIRED
    result_summary: str | None = None
    error_summary: str | None = None
    output_snapshot: dict[str, Any] | None = None


class ExecutionService:
    def __init__(
        self,
        *,
        job_service: JobService,
        run_service: RunService,
        event_log_service: EventLogService,
        executor: ExecutorFn | None = None,
        module_name: str = "backbone.execution",
        docs_loader: "ProjectDocsLoader | None" = None,
        openai_service: "OpenAIService | None" = None,
        airtable_service: "AirtableService | None" = None,
        operational_knowledge_loader: "OperationalKnowledgeLoader | None" = None,
        analytics_loader: "AnalyticsLoader | None" = None,
        platform_signal_loader: "PlatformSignalLoader | None" = None,
        weekly_analysis_loader: "WeeklyAnalysisPersistenceService | None" = None,
        correction_repository: "CorrectionFileRepository | None" = None,
        commercial_class_log: "CommercialClassLog | None" = None,
        content_ops_live_actions: frozenset[str] | None = None,
        content_ops_service: ContentOpsService | None = None,
        knowledge_ops_service: KnowledgeOpsService | None = None,
        affiliate_ops_service: AffiliateOpsService | None = None,
        review_ops_service: ReviewOpsService | None = None,
        funnel_ops_service: FunnelOpsService | None = None,
        analysis_foundation_service: AnalysisFoundationService | None = None,
        analysis_foundation_persistence_service: AnalysisFoundationPersistenceService | None = None,
        evaluation_service: EvaluationService | None = None,
    ) -> None:
        self.job_service = job_service
        self.run_service = run_service
        self.event_log_service = event_log_service
        self.airtable_service = airtable_service
        self.content_ops_service = content_ops_service or ContentOpsService(
            docs_loader=docs_loader,
            openai_service=openai_service,
            airtable_service=airtable_service,
            operational_knowledge_loader=operational_knowledge_loader,
            analytics_loader=analytics_loader,
            platform_signal_loader=platform_signal_loader,
            weekly_analysis_loader=weekly_analysis_loader,
            correction_repository=correction_repository,
            commercial_class_log=commercial_class_log,
            live_actions=content_ops_live_actions,
        )
        self.commercial_class_log = commercial_class_log
        self.knowledge_ops_service = knowledge_ops_service or KnowledgeOpsService(docs_loader=docs_loader)
        self.affiliate_ops_service = affiliate_ops_service or AffiliateOpsService(docs_loader=docs_loader)
        self.review_ops_service = review_ops_service or ReviewOpsService(docs_loader=docs_loader)
        self.funnel_ops_service = funnel_ops_service or FunnelOpsService(docs_loader=docs_loader)
        self.analysis_foundation_service = analysis_foundation_service or AnalysisFoundationService(
            docs_loader=docs_loader,
            analytics_loader=analytics_loader,
            operational_knowledge_loader=operational_knowledge_loader,
            platform_signal_loader=platform_signal_loader,
            weekly_analysis_loader=weekly_analysis_loader,
            openai_service=openai_service,
        )
        self.analysis_foundation_persistence_service = (
            analysis_foundation_persistence_service
            or (
                AnalysisFoundationPersistenceService(airtable_service=airtable_service)
                if airtable_service is not None
                else None
            )
        )
        self.evaluation_service = evaluation_service or EvaluationService()
        self.executor = executor or self._default_executor
        self.module_name = module_name

    def _attach_evaluation_case(
        self,
        *,
        output_snapshot: dict[str, Any],
        source_flow: str,
        source_action_type: str,
        command_body: str,
        job: Job,
        run: Run,
        execution_meta: dict[str, Any],
    ) -> tuple[dict[str, Any], ExecutionTraceEvent | None]:
        selected_snapshot_ids = tuple(str(item) for item in (output_snapshot.get("foundation_snapshot_ids") or []) if str(item))
        writer_brief_id = str(output_snapshot.get("writer_brief_id") or "").strip()
        if not selected_snapshot_ids or not writer_brief_id:
            return output_snapshot, None

        created_at = run.started_at.isoformat() if run.started_at is not None else ""
        evaluation_case = self.evaluation_service.create_case(
            source_flow=source_flow,
            source_action_type=source_action_type,
            target_platform=str(output_snapshot.get("platform") or ""),
            selected_snapshot_ids=selected_snapshot_ids,
            writer_brief_id=writer_brief_id,
            evidence_pack_id=str(output_snapshot.get("evidence_pack_id") or "") or None,
            job_id=job.job_id,
            run_id=run.run_id,
            input_context={
                "command_body": command_body,
                "title": str(output_snapshot.get("title") or ""),
                "summary": str(output_snapshot.get("summary") or ""),
            },
            generated_output={
                "action_type": str(output_snapshot.get("action_type") or ""),
                "title": str(output_snapshot.get("title") or ""),
                "summary": str(output_snapshot.get("summary") or ""),
                "items": list(output_snapshot.get("items") or []),
            },
            model_provider=execution_meta,
            created_at=created_at,
        )
        enriched_output = dict(output_snapshot)
        enriched_output["evaluation_case_id"] = evaluation_case.evaluation_case_id
        enriched_output["evaluation_case"] = evaluation_case.to_snapshot()
        return enriched_output, ExecutionTraceEvent(
            entity_type="run",
            entity_id=run.run_id,
            event_type="evaluation.case_created",
            message="Evaluation case created from grounded output",
            payload_json={
                "evaluation_case_id": evaluation_case.evaluation_case_id,
                "writer_brief_id": writer_brief_id,
                "snapshot_ids": list(selected_snapshot_ids),
                "job_id": job.job_id,
                "run_id": run.run_id,
            },
        )

    def execute_request(
        self,
        request_context: RequestContext,
        *,
        job_type: str,
        title: str,
        priority: int = 0,
    ) -> ExecutionResult:
        job = self.job_service.create_job_from_request(
            request_context=request_context,
            job_type=job_type,
            title=title,
            priority=priority,
        )
        self.event_log_service.log_job_created(job)

        if requires_confirmation(request_context.command_name):
            return self._gate_for_confirmation(request_context, job)

        return self._execute_run_for_job(request_context, job)

    def _gate_for_confirmation(
        self, request_context: RequestContext, job: Job
    ) -> ExecutionResult:
        """Park a high-impact Job pending human confirmation; run nothing yet."""
        previous_job_status = job.status.value
        job = self.job_service.mark_waiting_for_approval(job.job_id)
        self.event_log_service.log_job_status_changed(job, previous_job_status)
        self.event_log_service.log_event(
            project_key=job.project_key,
            entity_type="job",
            entity_id=job.job_id,
            event_type="confirmation_requested",
            message=f"Job {job.job_id} is awaiting confirmation",
            payload_json={"command_name": request_context.command_name or ""},
        )
        event_count = len(self.event_log_service.list_for_entity(job.project_key, "job", job.job_id))
        return ExecutionResult(
            job_id=job.job_id,
            run_id="",
            job_status=job.status,
            run_status="",
            event_count=event_count,
            approval_state=job.approval_state,
            result_summary=job.result_summary,
            error_summary=job.error_summary,
            output_snapshot=None,
        )

    def resume_confirmed_job(self, job_id: str) -> ExecutionResult:
        """Approve a pending Job and resume it through the normal execution path."""
        job = self.job_service.get_job(job_id)
        request_context = self._request_context_from_job(job)
        self.job_service.mark_approved(job_id)
        result = self._execute_run_for_job(request_context, self.job_service.get_job(job_id))
        self.event_log_service.log_event(
            project_key=job.project_key,
            entity_type="job",
            entity_id=job_id,
            event_type="confirmation_resolved",
            message=f"Job {job_id} confirmation approved",
            payload_json={"approval_state": ApprovalState.APPROVED.value},
        )
        result.approval_state = ApprovalState.APPROVED
        result.event_count = len(self.event_log_service.list_for_entity(job.project_key, "job", job_id))
        return result

    def reject_job(self, job_id: str) -> ExecutionResult:
        """Reject a pending Job: terminal, with no business write."""
        job = self.job_service.get_job(job_id)
        previous_job_status = job.status.value
        job = self.job_service.mark_rejected(job_id)
        self.event_log_service.log_job_status_changed(job, previous_job_status)
        self.event_log_service.log_event(
            project_key=job.project_key,
            entity_type="job",
            entity_id=job_id,
            event_type="confirmation_resolved",
            message=f"Job {job_id} confirmation rejected",
            payload_json={"approval_state": ApprovalState.REJECTED.value},
        )
        event_count = len(self.event_log_service.list_for_entity(job.project_key, "job", job_id))
        return ExecutionResult(
            job_id=job.job_id,
            run_id="",
            job_status=job.status,
            run_status="",
            event_count=event_count,
            approval_state=job.approval_state,
            result_summary=job.result_summary,
            error_summary=job.error_summary,
            output_snapshot=None,
        )

    def _request_context_from_job(self, job: Job) -> RequestContext:
        ctx = dict(job.context_json or {})
        return RequestContext(
            request_id=str(ctx.get("request_id") or job.request_id or job.job_id),
            project_key=job.project_key,
            source_type=str(ctx.get("source_type") or "telegram"),
            source_user_id=ctx.get("source_user_id"),
            source_chat_id=ctx.get("source_chat_id"),
            source_message_id=ctx.get("source_message_id"),
            command_name=ctx.get("command_name"),
            command_body=ctx.get("command_body"),
            request_text=ctx.get("request_text") or job.input_text,
            reply_to_message_id=ctx.get("reply_to_message_id"),
        )

    def _execute_run_for_job(
        self, request_context: RequestContext, job: Job
    ) -> ExecutionResult:
        run = self.run_service.create_run(
            job_id=job.job_id,
            project_key=job.project_key,
            module_name=self.module_name,
            request_id=request_context.request_id,
            input_snapshot={"request_context": self._serialize_request_context(request_context)},
        )
        self.event_log_service.log_run_created(run)

        run = self.run_service.mark_running(run.run_id)
        self.event_log_service.log_run_started(run)

        previous_job_status = job.status.value
        job = self.job_service.mark_in_progress(job.job_id)
        job = self.job_service.set_latest_run_id(job.job_id, run.run_id)
        self.event_log_service.log_job_status_changed(job, previous_job_status)

        try:
            step_result = self.executor(request_context, job, run)
            run = self.run_service.mark_succeeded(run.run_id, output_snapshot=step_result.output_snapshot)
            self.event_log_service.log_run_succeeded(run)

            previous_job_status = job.status.value
            if step_result.job_status == JobStatus.WAITING_FOR_INPUT:
                job = self.job_service.mark_waiting_for_input(job.job_id, summary=step_result.result_summary)
            else:
                job = self.job_service.mark_completed(
                    job.job_id,
                    result_summary=step_result.result_summary,
                    latest_run_id=run.run_id,
                )
            self.event_log_service.log_job_status_changed(job, previous_job_status)
            for trace_event in step_result.trace_events:
                self.event_log_service.log_event(
                    project_key=job.project_key,
                    entity_type=trace_event.entity_type,
                    entity_id=trace_event.entity_id,
                    event_type=trace_event.event_type,
                    message=trace_event.message,
                    payload_json=trace_event.payload_json,
                )

            event_count = len(self.event_log_service.list_for_entity(job.project_key, "job", job.job_id))
            event_count += len(self.event_log_service.list_for_entity(run.project_key, "run", run.run_id))
            return ExecutionResult(
                job_id=job.job_id,
                run_id=run.run_id,
                job_status=job.status,
                run_status=run.status.value,
                event_count=event_count,
                result_summary=job.result_summary,
                error_summary=job.error_summary,
                output_snapshot=run.output_snapshot,
            )
        except Exception as exc:
            run = self.run_service.mark_failed(run.run_id, error_detail=str(exc))
            self.event_log_service.log_run_failed(run)

            previous_job_status = job.status.value
            job = self.job_service.mark_failed(job.job_id, error_summary=str(exc), latest_run_id=run.run_id)
            self.event_log_service.log_job_status_changed(job, previous_job_status)
            self.event_log_service.log_error(
                project_key=job.project_key,
                entity_type="job",
                entity_id=job.job_id,
                message="Execution failed",
                payload_json={"run_id": run.run_id, "error": str(exc)},
            )
            event_count = len(self.event_log_service.list_for_entity(job.project_key, "job", job.job_id))
            event_count += len(self.event_log_service.list_for_entity(run.project_key, "run", run.run_id))
            return ExecutionResult(
                job_id=job.job_id,
                run_id=run.run_id,
                job_status=job.status,
                run_status=run.status.value,
                event_count=event_count,
                result_summary=job.result_summary,
                error_summary=job.error_summary,
                output_snapshot=run.output_snapshot,
            )

    def execute_content_mutation(
        self,
        request_context: RequestContext,
        *,
        proposal: "ContentProposal",
        instruction: str,
        mutation_mode: str,
        title: str,
        priority: int = 0,
    ) -> ExecutionResult:
        job = self.job_service.create_job_from_request(
            request_context=request_context,
            job_type=f"content_{mutation_mode}",
            title=title,
            priority=priority,
        )
        self.event_log_service.log_job_created(job)

        run = self.run_service.create_run(
            job_id=job.job_id,
            project_key=job.project_key,
            module_name=self.module_name,
            request_id=request_context.request_id,
            input_snapshot={
                "request_context": self._serialize_request_context(request_context),
                "proposal_id": proposal.proposal_id,
                "proposal_action_type": proposal.action_type,
                "instruction": instruction,
                "mutation_mode": mutation_mode,
            },
        )
        self.event_log_service.log_run_created(run)

        run = self.run_service.mark_running(run.run_id)
        self.event_log_service.log_run_started(run)

        previous_job_status = job.status.value
        job = self.job_service.mark_in_progress(job.job_id)
        job = self.job_service.set_latest_run_id(job.job_id, run.run_id)
        self.event_log_service.log_job_status_changed(job, previous_job_status)

        try:
            step_result = self._execute_content_mutation_step(
                request_context=request_context,
                proposal=proposal,
                instruction=instruction,
                mutation_mode=mutation_mode,
                job=job,
                run=run,
            )
            run = self.run_service.mark_succeeded(run.run_id, output_snapshot=step_result.output_snapshot)
            self.event_log_service.log_run_succeeded(run)

            previous_job_status = job.status.value
            if step_result.job_status == JobStatus.WAITING_FOR_INPUT:
                job = self.job_service.mark_waiting_for_input(job.job_id, summary=step_result.result_summary)
            else:
                job = self.job_service.mark_completed(
                    job.job_id,
                    result_summary=step_result.result_summary,
                    latest_run_id=run.run_id,
                )
            self.event_log_service.log_job_status_changed(job, previous_job_status)
            for trace_event in step_result.trace_events:
                self.event_log_service.log_event(
                    project_key=job.project_key,
                    entity_type=trace_event.entity_type,
                    entity_id=trace_event.entity_id,
                    event_type=trace_event.event_type,
                    message=trace_event.message,
                    payload_json=trace_event.payload_json,
                )

            event_count = len(self.event_log_service.list_for_entity(job.project_key, "job", job.job_id))
            event_count += len(self.event_log_service.list_for_entity(run.project_key, "run", run.run_id))
            return ExecutionResult(
                job_id=job.job_id,
                run_id=run.run_id,
                job_status=job.status,
                run_status=run.status.value,
                event_count=event_count,
                result_summary=job.result_summary,
                error_summary=job.error_summary,
                output_snapshot=run.output_snapshot,
            )
        except Exception as exc:
            run = self.run_service.mark_failed(run.run_id, error_detail=str(exc))
            self.event_log_service.log_run_failed(run)

            previous_job_status = job.status.value
            job = self.job_service.mark_failed(job.job_id, error_summary=str(exc), latest_run_id=run.run_id)
            self.event_log_service.log_job_status_changed(job, previous_job_status)
            self.event_log_service.log_error(
                project_key=job.project_key,
                entity_type="job",
                entity_id=job.job_id,
                message="Execution failed",
                payload_json={"run_id": run.run_id, "error": str(exc)},
            )
            event_count = len(self.event_log_service.list_for_entity(job.project_key, "job", job.job_id))
            event_count += len(self.event_log_service.list_for_entity(run.project_key, "run", run.run_id))
            return ExecutionResult(
                job_id=job.job_id,
                run_id=run.run_id,
                job_status=job.status,
                run_status=run.status.value,
                event_count=event_count,
                result_summary=job.result_summary,
                error_summary=job.error_summary,
                output_snapshot=run.output_snapshot,
            )

    def _execute_content_mutation_step(
        self,
        *,
        request_context: RequestContext,
        proposal: "ContentProposal",
        instruction: str,
        mutation_mode: str,
        job: Job,
        run: Run,
    ) -> ExecutionStepResult:
        if (
            hasattr(self.content_ops_service, "can_use_foundation_backed_followup")
            and hasattr(self.content_ops_service, "generate_followup_from_foundation")
            and hasattr(self.content_ops_service, "build_followup_evidence_pack")
            and self.analysis_foundation_service is not None
            and self.analysis_foundation_service.supports("analysis_snapshot")
        ):
            platform_override, _ = self.content_ops_service.resolve_platform_hint(instruction)
            if platform_override:
                _, proposal_body = self.content_ops_service.resolve_platform_hint(proposal.source_command_body)
                foundation_command_body = f"{platform_override} {proposal_body}".strip()
            else:
                foundation_command_body = proposal.source_command_body

            if self.content_ops_service.can_use_foundation_backed_followup(proposal_action_type=proposal.action_type):
                foundation_result = self.analysis_foundation_service.handle(
                    project_key=request_context.project_key,
                    action_type="analysis_snapshot",
                    command_body=foundation_command_body,
                )
                followup_result = self.content_ops_service.generate_followup_from_foundation(
                    project_key=request_context.project_key,
                    proposal=proposal,
                    instruction=instruction,
                    foundation_result=foundation_result,
                    mutation_mode=mutation_mode,
                )
                evidence_pack = self.content_ops_service.build_followup_evidence_pack(
                    project_key=request_context.project_key,
                    followup_result=followup_result,
                )
                selected_snapshots = followup_result.selected_snapshots
                selected_snapshot_record_ids: list[str] = []
                evidence_pack_record_id: str | None = None
                if self.analysis_foundation_persistence_service is not None:
                    persisted = self.analysis_foundation_persistence_service.persist(
                        project_key=request_context.project_key,
                        job_id=job.job_id,
                        run_id=run.run_id,
                        analysis_snapshots=selected_snapshots,
                        evidence_pack=evidence_pack,
                        execution_meta=followup_result.execution_meta,
                    )
                    selected_snapshots = persisted.analysis_snapshots
                    evidence_pack = persisted.evidence_pack
                    selected_snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                    evidence_pack_record_id = persisted.evidence_pack_record.record_id

                lane_result = replace(
                    followup_result.content_result,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    evidence_pack_id=evidence_pack.evidence_pack_id,
                    evidence_pack_record_id=evidence_pack.airtable_record_id or evidence_pack_record_id,
                )
                trace_events = [
                    ExecutionTraceEvent(
                        entity_type="job",
                        entity_id=job.job_id,
                        event_type=f"content.{mutation_mode}_generated",
                        message=f"{mutation_mode.title()} generated from analysis snapshots and writer brief",
                        payload_json={
                            "writer_brief_id": followup_result.writer_brief.brief_id,
                            "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                            "proposal_action_type": proposal.action_type,
                            "proposal_id": proposal.proposal_id,
                        },
                    ),
                ]
                if self.analysis_foundation_persistence_service is not None:
                    trace_events.extend(
                        (
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.snapshot_persisted",
                                message=f"{mutation_mode.title()} grounding snapshots persisted",
                                payload_json={
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "snapshot_record_ids": selected_snapshot_record_ids,
                                    "writer_brief_id": followup_result.writer_brief.brief_id,
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.evidence_pack_persisted",
                                message=f"{mutation_mode.title()} evidence pack persisted",
                                payload_json={
                                    "evidence_pack_id": evidence_pack.evidence_pack_id,
                                    "airtable_record_id": evidence_pack.airtable_record_id or evidence_pack_record_id,
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "execution_meta": followup_result.execution_meta.to_snapshot(),
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                        )
                    )
                output_snapshot, evaluation_trace_event = self._attach_evaluation_case(
                    output_snapshot=lane_result.to_snapshot(),
                    source_flow=mutation_mode,
                    source_action_type=proposal.action_type,
                    command_body=instruction,
                    job=job,
                    run=run,
                    execution_meta=followup_result.execution_meta.to_snapshot(),
                )
                if evaluation_trace_event is not None:
                    trace_events.append(evaluation_trace_event)
                return ExecutionStepResult(
                    output_snapshot=output_snapshot,
                    result_summary=lane_result.summary,
                    job_status=JobStatus.COMPLETED,
                    trace_events=tuple(trace_events),
                )

        if mutation_mode == "rewrite" and hasattr(self.content_ops_service, "rewrite_proposal"):
            lane_result = self.content_ops_service.rewrite_proposal(
                project_key=request_context.project_key,
                proposal=proposal,
            )
        else:
            lane_result = self.content_ops_service.follow_up(
                project_key=request_context.project_key,
                proposal=proposal,
                instruction=instruction,
            )
        output_snapshot, _evaluation_trace_event = self._attach_evaluation_case(
            output_snapshot=lane_result.to_snapshot(),
            source_flow=mutation_mode,
            source_action_type=proposal.action_type,
            command_body=instruction,
            job=job,
            run=run,
            execution_meta={},
        )
        return ExecutionStepResult(
            output_snapshot=output_snapshot,
            result_summary=lane_result.summary,
            job_status=JobStatus.COMPLETED,
        )

    def _default_executor(
        self,
        request_context: RequestContext,
        job: Job,
        run: Run,
    ) -> ExecutionStepResult:
        command_name = (request_context.command_name or "").strip().lower()
        command_body = request_context.command_body or ""

        if command_name == "status":
            return self._build_status_step_result(project_key=request_context.project_key)

        if self.analysis_foundation_service.supports(command_name):
            lane_result = self.analysis_foundation_service.handle(
                project_key=request_context.project_key,
                action_type=command_name,
                command_body=command_body,
            )
            snapshot_record_ids: list[str] = []
            evidence_pack_record_id: str | None = None
            if self.analysis_foundation_persistence_service is not None:
                persisted = self.analysis_foundation_persistence_service.persist(
                    project_key=request_context.project_key,
                    job_id=job.job_id,
                    run_id=run.run_id,
                    analysis_snapshots=lane_result.analysis_snapshots,
                    evidence_pack=lane_result.evidence_pack,
                    execution_meta=lane_result.execution_meta,
                )
                lane_result = replace(
                    lane_result,
                    analysis_snapshots=persisted.analysis_snapshots,
                    evidence_pack=persisted.evidence_pack,
                )
                snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                evidence_pack_record_id = persisted.evidence_pack_record.record_id
            snapshot_ids = [snapshot.snapshot_id for snapshot in lane_result.analysis_snapshots]
            trace_events = [
                ExecutionTraceEvent(
                    entity_type="job",
                    entity_id=job.job_id,
                    event_type="analysis.snapshot_built",
                    message="Analysis snapshots prepared",
                    payload_json={
                        "snapshot_ids": snapshot_ids,
                        "writer_brief_id": lane_result.writer_brief.brief_id,
                        "snapshot_record_ids": snapshot_record_ids,
                    },
                ),
                ExecutionTraceEvent(
                    entity_type="run",
                    entity_id=run.run_id,
                    event_type="analysis.evidence_pack_built",
                    message="Evidence pack prepared",
                    payload_json={
                        "evidence_pack_id": lane_result.evidence_pack.evidence_pack_id,
                        "snapshot_ids": snapshot_ids,
                        "airtable_record_id": evidence_pack_record_id,
                    },
                ),
            ]
            if self.analysis_foundation_persistence_service is not None:
                trace_events.extend(
                    (
                        ExecutionTraceEvent(
                            entity_type="run",
                            entity_id=run.run_id,
                            event_type="analysis.snapshot_persisted",
                            message="Analysis snapshots persisted",
                            payload_json={
                                "snapshot_ids": snapshot_ids,
                                "snapshot_record_ids": snapshot_record_ids,
                                "job_id": job.job_id,
                                "run_id": run.run_id,
                            },
                        ),
                        ExecutionTraceEvent(
                            entity_type="run",
                            entity_id=run.run_id,
                            event_type="analysis.evidence_pack_persisted",
                            message="Evidence pack persisted",
                            payload_json={
                                "evidence_pack_id": lane_result.evidence_pack.evidence_pack_id,
                                "airtable_record_id": evidence_pack_record_id,
                                "snapshot_ids": snapshot_ids,
                                "execution_meta": lane_result.execution_meta.to_snapshot(),
                                "job_id": job.job_id,
                                "run_id": run.run_id,
                            },
                        ),
                    )
                )
            return ExecutionStepResult(
                output_snapshot=lane_result.to_snapshot(),
                result_summary=lane_result.summary,
                job_status=JobStatus.COMPLETED,
                trace_events=tuple(trace_events),
            )

        if command_name == "idea" and self.content_ops_service.supports(command_name):
            if (
                hasattr(self.content_ops_service, "can_use_foundation_backed_idea")
                and self.content_ops_service.can_use_foundation_backed_idea()
            ):
                foundation_result = self.analysis_foundation_service.handle(
                    project_key=request_context.project_key,
                    action_type="analysis_snapshot",
                    command_body=command_body,
                )
                idea_result = self.content_ops_service.generate_idea_from_foundation(
                    project_key=request_context.project_key,
                    command_body=command_body,
                    foundation_result=foundation_result,
                )
                evidence_pack = self.content_ops_service.build_idea_evidence_pack(
                    project_key=request_context.project_key,
                    idea_result=idea_result,
                )
                selected_snapshots = idea_result.selected_snapshots
                selected_snapshot_record_ids: list[str] = []
                evidence_pack_record_id: str | None = None
                if self.analysis_foundation_persistence_service is not None:
                    persisted = self.analysis_foundation_persistence_service.persist(
                        project_key=request_context.project_key,
                        job_id=job.job_id,
                        run_id=run.run_id,
                        analysis_snapshots=selected_snapshots,
                        evidence_pack=evidence_pack,
                        execution_meta=idea_result.execution_meta,
                    )
                    selected_snapshots = persisted.analysis_snapshots
                    evidence_pack = persisted.evidence_pack
                    selected_snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                    evidence_pack_record_id = persisted.evidence_pack_record.record_id

                lane_result = replace(
                    idea_result.content_result,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    evidence_pack_id=evidence_pack.evidence_pack_id,
                    evidence_pack_record_id=evidence_pack.airtable_record_id or evidence_pack_record_id,
                )
                trace_events = [
                    ExecutionTraceEvent(
                        entity_type="job",
                        entity_id=job.job_id,
                        event_type="content.idea_generated",
                        message="Idea generated from analysis snapshots and writer brief",
                        payload_json={
                            "writer_brief_id": idea_result.writer_brief.brief_id,
                            "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                            "idea_record_id": lane_result.airtable_record_id,
                        },
                    ),
                ]
                if self.analysis_foundation_persistence_service is not None:
                    trace_events.extend(
                        (
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.snapshot_persisted",
                                message="Idea grounding snapshots persisted",
                                payload_json={
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "snapshot_record_ids": selected_snapshot_record_ids,
                                    "writer_brief_id": idea_result.writer_brief.brief_id,
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.evidence_pack_persisted",
                                message="Idea evidence pack persisted",
                                payload_json={
                                    "evidence_pack_id": evidence_pack.evidence_pack_id,
                                    "airtable_record_id": evidence_pack.airtable_record_id or evidence_pack_record_id,
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "execution_meta": idea_result.execution_meta.to_snapshot(),
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                        )
                    )

                output_snapshot, evaluation_trace_event = self._attach_evaluation_case(
                    output_snapshot=lane_result.to_snapshot(),
                    source_flow="idea",
                    source_action_type="idea",
                    command_body=command_body,
                    job=job,
                    run=run,
                    execution_meta=idea_result.execution_meta.to_snapshot(),
                )
                if evaluation_trace_event is not None:
                    trace_events.append(evaluation_trace_event)
                return ExecutionStepResult(
                    output_snapshot=output_snapshot,
                    result_summary=lane_result.summary,
                    job_status=JobStatus.COMPLETED,
                    trace_events=tuple(trace_events),
                )

        if command_name == "draft" and self.content_ops_service.supports(command_name):
            if self.content_ops_service.can_use_foundation_backed_draft():
                foundation_result = self.analysis_foundation_service.handle(
                    project_key=request_context.project_key,
                    action_type="analysis_snapshot",
                    command_body=command_body,
                )
                draft_result = self.content_ops_service.generate_draft_from_foundation(
                    project_key=request_context.project_key,
                    command_body=command_body,
                    foundation_result=foundation_result,
                )
                evidence_pack = self.content_ops_service.build_draft_evidence_pack(
                    project_key=request_context.project_key,
                    draft_result=draft_result,
                )
                selected_snapshots = draft_result.selected_snapshots
                selected_snapshot_record_ids: list[str] = []
                evidence_pack_record_id: str | None = None
                if self.analysis_foundation_persistence_service is not None:
                    persisted = self.analysis_foundation_persistence_service.persist(
                        project_key=request_context.project_key,
                        job_id=job.job_id,
                        run_id=run.run_id,
                        analysis_snapshots=selected_snapshots,
                        evidence_pack=evidence_pack,
                        execution_meta=draft_result.execution_meta,
                    )
                    selected_snapshots = persisted.analysis_snapshots
                    evidence_pack = persisted.evidence_pack
                    selected_snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                    evidence_pack_record_id = persisted.evidence_pack_record.record_id

                lane_result = replace(
                    draft_result.content_result,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    evidence_pack_id=evidence_pack.evidence_pack_id,
                    evidence_pack_record_id=evidence_pack.airtable_record_id or evidence_pack_record_id,
                )
                trace_events = [
                    ExecutionTraceEvent(
                        entity_type="job",
                        entity_id=job.job_id,
                        event_type="content.draft_generated",
                        message="Draft generated from analysis snapshots and writer brief",
                        payload_json={
                            "writer_brief_id": draft_result.writer_brief.brief_id,
                            "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                            "draft_record_id": lane_result.airtable_record_id,
                        },
                    ),
                ]
                if self.analysis_foundation_persistence_service is not None:
                    trace_events.extend(
                        (
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.snapshot_persisted",
                                message="Draft grounding snapshots persisted",
                                payload_json={
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "snapshot_record_ids": selected_snapshot_record_ids,
                                    "writer_brief_id": draft_result.writer_brief.brief_id,
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.evidence_pack_persisted",
                                message="Draft evidence pack persisted",
                                payload_json={
                                    "evidence_pack_id": evidence_pack.evidence_pack_id,
                                    "airtable_record_id": evidence_pack.airtable_record_id or evidence_pack_record_id,
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "execution_meta": draft_result.execution_meta.to_snapshot(),
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                        )
                    )

                output_snapshot, evaluation_trace_event = self._attach_evaluation_case(
                    output_snapshot=lane_result.to_snapshot(),
                    source_flow="draft",
                    source_action_type="draft",
                    command_body=command_body,
                    job=job,
                    run=run,
                    execution_meta=draft_result.execution_meta.to_snapshot(),
                )
                if evaluation_trace_event is not None:
                    trace_events.append(evaluation_trace_event)
                return ExecutionStepResult(
                    output_snapshot=output_snapshot,
                    result_summary=lane_result.summary,
                    job_status=JobStatus.COMPLETED,
                    trace_events=tuple(trace_events),
                )

        if command_name == "vollauto" and self.content_ops_service.supports(command_name):
            if self.content_ops_service.can_use_foundation_backed_vollauto():
                foundation_result = self.analysis_foundation_service.handle(
                    project_key=request_context.project_key,
                    action_type="analysis_snapshot",
                    command_body=command_body,
                )
                draft_result = self.content_ops_service.generate_vollauto_from_foundation(
                    project_key=request_context.project_key,
                    command_body=command_body,
                    foundation_result=foundation_result,
                )
                evidence_pack = self.content_ops_service.build_vollauto_evidence_pack(
                    project_key=request_context.project_key,
                    draft_result=draft_result,
                )
                selected_snapshots = draft_result.selected_snapshots
                selected_snapshot_record_ids: list[str] = []
                evidence_pack_record_id: str | None = None
                if self.analysis_foundation_persistence_service is not None:
                    persisted = self.analysis_foundation_persistence_service.persist(
                        project_key=request_context.project_key,
                        job_id=job.job_id,
                        run_id=run.run_id,
                        analysis_snapshots=selected_snapshots,
                        evidence_pack=evidence_pack,
                        execution_meta=draft_result.execution_meta,
                    )
                    selected_snapshots = persisted.analysis_snapshots
                    evidence_pack = persisted.evidence_pack
                    selected_snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                    evidence_pack_record_id = persisted.evidence_pack_record.record_id

                lane_result = replace(
                    draft_result.content_result,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    evidence_pack_id=evidence_pack.evidence_pack_id,
                    evidence_pack_record_id=evidence_pack.airtable_record_id or evidence_pack_record_id,
                )
                trace_events = [
                    ExecutionTraceEvent(
                        entity_type="job",
                        entity_id=job.job_id,
                        event_type="content.vollauto_generated",
                        message="Vollauto generated from analysis snapshots and writer brief",
                        payload_json={
                            "writer_brief_id": draft_result.writer_brief.brief_id,
                            "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                            "draft_record_id": lane_result.airtable_record_id,
                        },
                    ),
                ]
                if self.analysis_foundation_persistence_service is not None:
                    trace_events.extend(
                        (
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.snapshot_persisted",
                                message="Vollauto grounding snapshots persisted",
                                payload_json={
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "snapshot_record_ids": selected_snapshot_record_ids,
                                    "writer_brief_id": draft_result.writer_brief.brief_id,
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.evidence_pack_persisted",
                                message="Vollauto evidence pack persisted",
                                payload_json={
                                    "evidence_pack_id": evidence_pack.evidence_pack_id,
                                    "airtable_record_id": evidence_pack.airtable_record_id or evidence_pack_record_id,
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "execution_meta": draft_result.execution_meta.to_snapshot(),
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                        )
                    )

                output_snapshot, evaluation_trace_event = self._attach_evaluation_case(
                    output_snapshot=lane_result.to_snapshot(),
                    source_flow="vollauto",
                    source_action_type="vollauto",
                    command_body=command_body,
                    job=job,
                    run=run,
                    execution_meta=draft_result.execution_meta.to_snapshot(),
                )
                if evaluation_trace_event is not None:
                    trace_events.append(evaluation_trace_event)
                return ExecutionStepResult(
                    output_snapshot=output_snapshot,
                    result_summary=lane_result.summary,
                    job_status=JobStatus.COMPLETED,
                    trace_events=tuple(trace_events),
                )

        if command_name == "caption" and self.content_ops_service.supports(command_name):
            if self.content_ops_service.can_use_foundation_backed_caption():
                foundation_result = self.analysis_foundation_service.handle(
                    project_key=request_context.project_key,
                    action_type="analysis_snapshot",
                    command_body=command_body,
                )
                caption_result = self.content_ops_service.generate_caption_from_foundation(
                    project_key=request_context.project_key,
                    command_body=command_body,
                    foundation_result=foundation_result,
                )
                evidence_pack = self.content_ops_service.build_caption_evidence_pack(
                    project_key=request_context.project_key,
                    caption_result=caption_result,
                )
                selected_snapshots = caption_result.selected_snapshots
                selected_snapshot_record_ids: list[str] = []
                evidence_pack_record_id: str | None = None
                if self.analysis_foundation_persistence_service is not None:
                    persisted = self.analysis_foundation_persistence_service.persist(
                        project_key=request_context.project_key,
                        job_id=job.job_id,
                        run_id=run.run_id,
                        analysis_snapshots=selected_snapshots,
                        evidence_pack=evidence_pack,
                        execution_meta=caption_result.execution_meta,
                    )
                    selected_snapshots = persisted.analysis_snapshots
                    evidence_pack = persisted.evidence_pack
                    selected_snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                    evidence_pack_record_id = persisted.evidence_pack_record.record_id

                lane_result = replace(
                    caption_result.content_result,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    evidence_pack_id=evidence_pack.evidence_pack_id,
                    evidence_pack_record_id=evidence_pack.airtable_record_id or evidence_pack_record_id,
                )
                trace_events = [
                    ExecutionTraceEvent(
                        entity_type="job",
                        entity_id=job.job_id,
                        event_type="content.caption_generated",
                        message="Caption generated from analysis snapshots and writer brief",
                        payload_json={
                            "writer_brief_id": caption_result.writer_brief.brief_id,
                            "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                            "caption_record_id": lane_result.airtable_record_id,
                        },
                    ),
                ]
                if self.analysis_foundation_persistence_service is not None:
                    trace_events.extend(
                        (
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.snapshot_persisted",
                                message="Caption grounding snapshots persisted",
                                payload_json={
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "snapshot_record_ids": selected_snapshot_record_ids,
                                    "writer_brief_id": caption_result.writer_brief.brief_id,
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.evidence_pack_persisted",
                                message="Caption evidence pack persisted",
                                payload_json={
                                    "evidence_pack_id": evidence_pack.evidence_pack_id,
                                    "airtable_record_id": evidence_pack.airtable_record_id or evidence_pack_record_id,
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "execution_meta": caption_result.execution_meta.to_snapshot(),
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                        )
                    )

                output_snapshot, evaluation_trace_event = self._attach_evaluation_case(
                    output_snapshot=lane_result.to_snapshot(),
                    source_flow="caption",
                    source_action_type="caption",
                    command_body=command_body,
                    job=job,
                    run=run,
                    execution_meta=caption_result.execution_meta.to_snapshot(),
                )
                if evaluation_trace_event is not None:
                    trace_events.append(evaluation_trace_event)
                return ExecutionStepResult(
                    output_snapshot=output_snapshot,
                    result_summary=lane_result.summary,
                    job_status=JobStatus.COMPLETED,
                    trace_events=tuple(trace_events),
                )

        if command_name == "hook" and self.content_ops_service.supports(command_name):
            if self.content_ops_service.can_use_foundation_backed_hook():
                foundation_result = self.analysis_foundation_service.handle(
                    project_key=request_context.project_key,
                    action_type="analysis_snapshot",
                    command_body=command_body,
                )
                hook_result = self.content_ops_service.generate_hook_from_foundation(
                    project_key=request_context.project_key,
                    command_body=command_body,
                    foundation_result=foundation_result,
                )
                evidence_pack = self.content_ops_service.build_hook_evidence_pack(
                    project_key=request_context.project_key,
                    hook_result=hook_result,
                )
                selected_snapshots = hook_result.selected_snapshots
                selected_snapshot_record_ids: list[str] = []
                evidence_pack_record_id: str | None = None
                if self.analysis_foundation_persistence_service is not None:
                    persisted = self.analysis_foundation_persistence_service.persist(
                        project_key=request_context.project_key,
                        job_id=job.job_id,
                        run_id=run.run_id,
                        analysis_snapshots=selected_snapshots,
                        evidence_pack=evidence_pack,
                        execution_meta=hook_result.execution_meta,
                    )
                    selected_snapshots = persisted.analysis_snapshots
                    evidence_pack = persisted.evidence_pack
                    selected_snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                    evidence_pack_record_id = persisted.evidence_pack_record.record_id

                lane_result = replace(
                    hook_result.content_result,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    evidence_pack_id=evidence_pack.evidence_pack_id,
                    evidence_pack_record_id=evidence_pack.airtable_record_id or evidence_pack_record_id,
                )
                trace_events = [
                    ExecutionTraceEvent(
                        entity_type="job",
                        entity_id=job.job_id,
                        event_type="content.hook_generated",
                        message="Hook generated from analysis snapshots and writer brief",
                        payload_json={
                            "writer_brief_id": hook_result.writer_brief.brief_id,
                            "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                            "hook_record_id": lane_result.airtable_record_id,
                        },
                    ),
                ]
                if self.analysis_foundation_persistence_service is not None:
                    trace_events.extend(
                        (
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.snapshot_persisted",
                                message="Hook grounding snapshots persisted",
                                payload_json={
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "snapshot_record_ids": selected_snapshot_record_ids,
                                    "writer_brief_id": hook_result.writer_brief.brief_id,
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.evidence_pack_persisted",
                                message="Hook evidence pack persisted",
                                payload_json={
                                    "evidence_pack_id": evidence_pack.evidence_pack_id,
                                    "airtable_record_id": evidence_pack.airtable_record_id or evidence_pack_record_id,
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "execution_meta": hook_result.execution_meta.to_snapshot(),
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                        )
                    )

                output_snapshot, evaluation_trace_event = self._attach_evaluation_case(
                    output_snapshot=lane_result.to_snapshot(),
                    source_flow="hook",
                    source_action_type="hook",
                    command_body=command_body,
                    job=job,
                    run=run,
                    execution_meta=hook_result.execution_meta.to_snapshot(),
                )
                if evaluation_trace_event is not None:
                    trace_events.append(evaluation_trace_event)
                return ExecutionStepResult(
                    output_snapshot=output_snapshot,
                    result_summary=lane_result.summary,
                    job_status=JobStatus.COMPLETED,
                    trace_events=tuple(trace_events),
                )

        if command_name == "serie" and self.content_ops_service.supports(command_name):
            if self.content_ops_service.can_use_foundation_backed_serie():
                foundation_result = self.analysis_foundation_service.handle(
                    project_key=request_context.project_key,
                    action_type="analysis_snapshot",
                    command_body=command_body,
                )
                serie_result = self.content_ops_service.generate_serie_from_foundation(
                    project_key=request_context.project_key,
                    command_body=command_body,
                    foundation_result=foundation_result,
                )
                evidence_pack = self.content_ops_service.build_serie_evidence_pack(
                    project_key=request_context.project_key,
                    serie_result=serie_result,
                )
                selected_snapshots = serie_result.selected_snapshots
                selected_snapshot_record_ids: list[str] = []
                evidence_pack_record_id: str | None = None
                if self.analysis_foundation_persistence_service is not None:
                    persisted = self.analysis_foundation_persistence_service.persist(
                        project_key=request_context.project_key,
                        job_id=job.job_id,
                        run_id=run.run_id,
                        analysis_snapshots=selected_snapshots,
                        evidence_pack=evidence_pack,
                        execution_meta=serie_result.execution_meta,
                    )
                    selected_snapshots = persisted.analysis_snapshots
                    evidence_pack = persisted.evidence_pack
                    selected_snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                    evidence_pack_record_id = persisted.evidence_pack_record.record_id

                lane_result = replace(
                    serie_result.content_result,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    evidence_pack_id=evidence_pack.evidence_pack_id,
                    evidence_pack_record_id=evidence_pack.airtable_record_id or evidence_pack_record_id,
                )
                trace_events = [
                    ExecutionTraceEvent(
                        entity_type="job",
                        entity_id=job.job_id,
                        event_type="content.serie_generated",
                        message="Serie generated from analysis snapshots and writer brief",
                        payload_json={
                            "writer_brief_id": serie_result.writer_brief.brief_id,
                            "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                        },
                    ),
                ]
                if self.analysis_foundation_persistence_service is not None:
                    trace_events.extend(
                        (
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.snapshot_persisted",
                                message="Serie grounding snapshots persisted",
                                payload_json={
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "snapshot_record_ids": selected_snapshot_record_ids,
                                    "writer_brief_id": serie_result.writer_brief.brief_id,
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.evidence_pack_persisted",
                                message="Serie evidence pack persisted",
                                payload_json={
                                    "evidence_pack_id": evidence_pack.evidence_pack_id,
                                    "airtable_record_id": evidence_pack.airtable_record_id or evidence_pack_record_id,
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "execution_meta": serie_result.execution_meta.to_snapshot(),
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                        )
                    )

                output_snapshot, evaluation_trace_event = self._attach_evaluation_case(
                    output_snapshot=lane_result.to_snapshot(),
                    source_flow="serie",
                    source_action_type="serie",
                    command_body=command_body,
                    job=job,
                    run=run,
                    execution_meta=serie_result.execution_meta.to_snapshot(),
                )
                if evaluation_trace_event is not None:
                    trace_events.append(evaluation_trace_event)
                return ExecutionStepResult(
                    output_snapshot=output_snapshot,
                    result_summary=lane_result.summary,
                    job_status=JobStatus.COMPLETED,
                    trace_events=tuple(trace_events),
                )

        if command_name == "title" and self.content_ops_service.supports(command_name):
            if self.content_ops_service.can_use_foundation_backed_title():
                foundation_result = self.analysis_foundation_service.handle(
                    project_key=request_context.project_key,
                    action_type="analysis_snapshot",
                    command_body=command_body,
                )
                title_result = self.content_ops_service.generate_title_from_foundation(
                    project_key=request_context.project_key,
                    command_body=command_body,
                    foundation_result=foundation_result,
                )
                evidence_pack = self.content_ops_service.build_title_evidence_pack(
                    project_key=request_context.project_key,
                    title_result=title_result,
                )
                selected_snapshots = title_result.selected_snapshots
                selected_snapshot_record_ids: list[str] = []
                evidence_pack_record_id: str | None = None
                if self.analysis_foundation_persistence_service is not None:
                    persisted = self.analysis_foundation_persistence_service.persist(
                        project_key=request_context.project_key,
                        job_id=job.job_id,
                        run_id=run.run_id,
                        analysis_snapshots=selected_snapshots,
                        evidence_pack=evidence_pack,
                        execution_meta=title_result.execution_meta,
                    )
                    selected_snapshots = persisted.analysis_snapshots
                    evidence_pack = persisted.evidence_pack
                    selected_snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                    evidence_pack_record_id = persisted.evidence_pack_record.record_id

                lane_result = replace(
                    title_result.content_result,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    evidence_pack_id=evidence_pack.evidence_pack_id,
                    evidence_pack_record_id=evidence_pack.airtable_record_id or evidence_pack_record_id,
                )
                trace_events = [
                    ExecutionTraceEvent(
                        entity_type="job",
                        entity_id=job.job_id,
                        event_type="content.title_generated",
                        message="Title generated from analysis snapshots and writer brief",
                        payload_json={
                            "writer_brief_id": title_result.writer_brief.brief_id,
                            "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                        },
                    ),
                ]
                if self.analysis_foundation_persistence_service is not None:
                    trace_events.extend(
                        (
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.snapshot_persisted",
                                message="Title grounding snapshots persisted",
                                payload_json={
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "snapshot_record_ids": selected_snapshot_record_ids,
                                    "writer_brief_id": title_result.writer_brief.brief_id,
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.evidence_pack_persisted",
                                message="Title evidence pack persisted",
                                payload_json={
                                    "evidence_pack_id": evidence_pack.evidence_pack_id,
                                    "airtable_record_id": evidence_pack.airtable_record_id or evidence_pack_record_id,
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "execution_meta": title_result.execution_meta.to_snapshot(),
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                        )
                    )

                output_snapshot, evaluation_trace_event = self._attach_evaluation_case(
                    output_snapshot=lane_result.to_snapshot(),
                    source_flow="title",
                    source_action_type="title",
                    command_body=command_body,
                    job=job,
                    run=run,
                    execution_meta=title_result.execution_meta.to_snapshot(),
                )
                if evaluation_trace_event is not None:
                    trace_events.append(evaluation_trace_event)
                return ExecutionStepResult(
                    output_snapshot=output_snapshot,
                    result_summary=lane_result.summary,
                    job_status=JobStatus.COMPLETED,
                    trace_events=tuple(trace_events),
                )

        if command_name == "cta" and self.content_ops_service.supports(command_name):
            if self.content_ops_service.can_use_foundation_backed_cta():
                foundation_result = self.analysis_foundation_service.handle(
                    project_key=request_context.project_key,
                    action_type="analysis_snapshot",
                    command_body=command_body,
                )
                cta_result = self.content_ops_service.generate_cta_from_foundation(
                    project_key=request_context.project_key,
                    command_body=command_body,
                    foundation_result=foundation_result,
                )
                evidence_pack = self.content_ops_service.build_cta_evidence_pack(
                    project_key=request_context.project_key,
                    cta_result=cta_result,
                )
                selected_snapshots = cta_result.selected_snapshots
                selected_snapshot_record_ids: list[str] = []
                evidence_pack_record_id: str | None = None
                if self.analysis_foundation_persistence_service is not None:
                    persisted = self.analysis_foundation_persistence_service.persist(
                        project_key=request_context.project_key,
                        job_id=job.job_id,
                        run_id=run.run_id,
                        analysis_snapshots=selected_snapshots,
                        evidence_pack=evidence_pack,
                        execution_meta=cta_result.execution_meta,
                    )
                    selected_snapshots = persisted.analysis_snapshots
                    evidence_pack = persisted.evidence_pack
                    selected_snapshot_record_ids = [record.record_id for record in persisted.snapshot_records]
                    evidence_pack_record_id = persisted.evidence_pack_record.record_id

                lane_result = replace(
                    cta_result.content_result,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    evidence_pack_id=evidence_pack.evidence_pack_id,
                    evidence_pack_record_id=evidence_pack.airtable_record_id or evidence_pack_record_id,
                )
                trace_events = [
                    ExecutionTraceEvent(
                        entity_type="job",
                        entity_id=job.job_id,
                        event_type="content.cta_generated",
                        message="CTA generated from analysis snapshots and writer brief",
                        payload_json={
                            "writer_brief_id": cta_result.writer_brief.brief_id,
                            "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                        },
                    ),
                ]
                if self.analysis_foundation_persistence_service is not None:
                    trace_events.extend(
                        (
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.snapshot_persisted",
                                message="CTA grounding snapshots persisted",
                                payload_json={
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "snapshot_record_ids": selected_snapshot_record_ids,
                                    "writer_brief_id": cta_result.writer_brief.brief_id,
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                            ExecutionTraceEvent(
                                entity_type="run",
                                entity_id=run.run_id,
                                event_type="analysis.evidence_pack_persisted",
                                message="CTA evidence pack persisted",
                                payload_json={
                                    "evidence_pack_id": evidence_pack.evidence_pack_id,
                                    "airtable_record_id": evidence_pack.airtable_record_id or evidence_pack_record_id,
                                    "snapshot_ids": [snapshot.snapshot_id for snapshot in selected_snapshots],
                                    "execution_meta": cta_result.execution_meta.to_snapshot(),
                                    "job_id": job.job_id,
                                    "run_id": run.run_id,
                                },
                            ),
                        )
                    )

                output_snapshot, evaluation_trace_event = self._attach_evaluation_case(
                    output_snapshot=lane_result.to_snapshot(),
                    source_flow="cta",
                    source_action_type="cta",
                    command_body=command_body,
                    job=job,
                    run=run,
                    execution_meta=cta_result.execution_meta.to_snapshot(),
                )
                if evaluation_trace_event is not None:
                    trace_events.append(evaluation_trace_event)
                return ExecutionStepResult(
                    output_snapshot=output_snapshot,
                    result_summary=lane_result.summary,
                    job_status=JobStatus.COMPLETED,
                    trace_events=tuple(trace_events),
                )

        if self.content_ops_service.supports(command_name):
            lane_result = self.content_ops_service.handle(
                project_key=request_context.project_key,
                action_type=command_name,
                command_body=command_body,
            )
            return ExecutionStepResult(
                output_snapshot=lane_result.to_snapshot(),
                result_summary=lane_result.summary,
                job_status=JobStatus.COMPLETED,
            )

        if self.knowledge_ops_service.supports(command_name):
            lane_result = self.knowledge_ops_service.handle(
                project_key=request_context.project_key,
                action_type=command_name,
                command_body=command_body,
            )
            return ExecutionStepResult(
                output_snapshot=lane_result.to_snapshot(),
                result_summary=lane_result.summary,
                job_status=JobStatus.COMPLETED,
            )

        if self.affiliate_ops_service.supports(command_name):
            lane_result = self.affiliate_ops_service.handle(
                project_key=request_context.project_key,
                action_type=command_name,
                command_body=command_body,
            )
            return ExecutionStepResult(
                output_snapshot=lane_result.to_snapshot(),
                result_summary=lane_result.summary,
                job_status=JobStatus.COMPLETED,
            )

        if self.review_ops_service.supports(command_name):
            lane_result = self.review_ops_service.handle(
                project_key=request_context.project_key,
                action_type=command_name,
                command_body=command_body,
            )
            return ExecutionStepResult(
                output_snapshot=lane_result.to_snapshot(),
                result_summary=lane_result.summary,
                job_status=JobStatus.COMPLETED,
            )

        if self.funnel_ops_service.supports(command_name):
            lane_result = self.funnel_ops_service.handle(
                project_key=request_context.project_key,
                action_type=command_name,
                command_body=command_body,
            )
            return ExecutionStepResult(
                output_snapshot=lane_result.to_snapshot(),
                result_summary=lane_result.summary,
                job_status=JobStatus.COMPLETED,
            )

        summary = f"Processed request {request_context.request_id} for job {job.job_id}"
        return ExecutionStepResult(
            output_snapshot={"job_id": job.job_id, "run_id": run.run_id, "request_id": request_context.request_id},
            result_summary=summary,
            job_status=JobStatus.COMPLETED,
        )

    def _build_status_step_result(self, *, project_key: str) -> ExecutionStepResult:
        from operator_core.core.content_ops.correction_capture import summarize_commercial_mix

        if self.commercial_class_log is not None:
            mix = summarize_commercial_mix(self.commercial_class_log, project_key=project_key, window_days=7)
        else:
            mix = summarize_commercial_mix(
                CommercialClassLog(file_path=None),
                project_key=project_key,
                window_days=7,
            )

        output_snapshot = {
            "lane_name": "operator_status",
            "status_type": "commercial_mix",
            "title": "Commercial Mix",
            "window_days": mix.window_days,
            "total": mix.total,
            "commercial_mix": {
                "trust_building": mix.trust_building,
                "product_near": mix.product_near,
                "recommendation_ready": mix.recommendation_ready,
                "direct_offer": mix.direct_offer,
                "off_thesis_or_monetization_waste": mix.off_thesis_or_monetization_waste,
            },
            "drift_hint": mix.drift_hint,
        }
        return ExecutionStepResult(
            output_snapshot=output_snapshot,
            result_summary="7-Tage Commercial Mix geladen.",
            job_status=JobStatus.COMPLETED,
        )

    @staticmethod
    def _serialize_request_context(request_context: RequestContext) -> dict[str, Any]:
        data = asdict(request_context)
        data["created_at"] = request_context.created_at.isoformat()
        return data
