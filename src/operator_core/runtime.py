from __future__ import annotations

import logging
import signal
from dataclasses import dataclass, field
from threading import Event, Thread
from types import FrameType

from operator_core.bootstrap import BootstrapContext
from operator_core.core.project_resolver import (
    ResolvedProjectContext,
    resolve_active_project_context,
)
from operator_core.integrations.telegram_service import TelegramServiceError
from operator_core.projects.registry import PROJECT_ROOT


_TELEGRAM_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": "menu", "description": "Menü öffnen"},
    {"command": "plan_demo", "description": "Tagesplan ansehen"},
    {"command": "idea", "description": "Eine Idee"},
    {"command": "vollauto", "description": "Voll Auto"},
    {"command": "serie", "description": "Serie/Thema"},
    {"command": "title", "description": "Title"},
    {"command": "hook", "description": "Hook erstellen"},
    {"command": "cta", "description": "CTA erstellen"},
    {"command": "caption", "description": "Caption erstellen"},
    {"command": "status", "description": "📊 Projekt-Stand"},
]


@dataclass
class OperatorRuntime:
    bootstrap_context: BootstrapContext
    project_context: ResolvedProjectContext = field(init=False)
    stop_event: Event = field(default_factory=Event, init=False, repr=False)
    signal_name: str | None = field(default=None, init=False)
    logger: logging.Logger = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.project_context = resolve_active_project_context(self.bootstrap_context)
        self.logger = logging.getLogger("operator_core.runtime")

    def run(self) -> None:
        self._install_signal_handlers()
        self._log_runtime_started()
        if self.bootstrap_context.settings.telegram.enabled:
            self._start_telegram_polling()
            self._start_proactive_checker()
        self.stop_event.wait()
        self._log_runtime_stopped()

    def _start_telegram_polling(self) -> None:
        from operator_core.core.backbone.event_log_service import EventLogService
        from operator_core.core.backbone.execution_service import ExecutionService
        from operator_core.core.backbone.job_service import JobService
        from operator_core.core.backbone.repositories import (
            InMemoryEventRepository,
            InMemoryJobRepository,
            InMemoryRunRepository,
        )
        from operator_core.core.backbone.run_service import RunService
        from operator_core.core.request_flow.service import RequestFlowService
        from operator_core.core.response_formatter.service import ResponseFormatterService
        from operator_core.core.request_flow.daily_plan_message_store import DailyPlanMessageStore
        from operator_core.integrations.airtable_service import AirtableService
        from operator_core.integrations.openai_service import OpenAIService
        from operator_core.integrations.operational_knowledge_service import OperationalKnowledgeLoader
        from operator_core.integrations.analytics_service import AnalyticsLoader
        from operator_core.integrations.platform_signal_service import PlatformSignalLoader
        from operator_core.integrations.weekly_analysis_persistence import WeeklyAnalysisPersistenceService
        from operator_core.integrations.telegram_service import TelegramService
        from operator_core.interfaces.telegram.poller import TelegramPoller
        from operator_core.projects.docs import ProjectDocsLoader
        from operator_core.core.content_ops.correction_capture import (
            CommercialClassLog,
            CorrectionCaptureStore,
            CorrectionFileRepository,
            IdeaCorrectionService,
        )

        ctx = self.bootstrap_context
        settings = ctx.settings
        telegram_service = TelegramService(ctx)

        self._register_telegram_commands(telegram_service)

        openai_svc = OpenAIService(ctx) if settings.openai.enabled else None
        airtable_svc = AirtableService(ctx) if settings.airtable.enabled else None
        ok_loader = OperationalKnowledgeLoader(airtable_svc) if airtable_svc is not None else None
        analytics_loader = (
            AnalyticsLoader(airtable_svc)
            if airtable_svc is not None and settings.airtable.get_base_id("analytics")
            else None
        )
        platform_signal_loader = (
            PlatformSignalLoader(airtable_svc, ok_loader)
            if airtable_svc is not None and ok_loader is not None and settings.airtable.get_base_id("analytics")
            else None
        )
        weekly_analysis_loader = (
            WeeklyAnalysisPersistenceService(airtable_service=airtable_svc)
            if airtable_svc is not None
            else None
        )
        live_actions: frozenset[str] | None = (
            frozenset(settings.app.content_ops_live_actions)
            if settings.app.content_ops_live_actions
            else None
        )

        docs_loader = ProjectDocsLoader()
        runtime_state_dir = PROJECT_ROOT / ".runtime"
        correction_file_repo = CorrectionFileRepository(
            file_path=runtime_state_dir / "idea_corrections.json"
        )
        commercial_class_log = CommercialClassLog(
            file_path=runtime_state_dir / "commercial_class_log.json"
        )

        execution_svc = ExecutionService(
            job_service=JobService(InMemoryJobRepository()),
            run_service=RunService(InMemoryRunRepository()),
            event_log_service=EventLogService(InMemoryEventRepository()),
            docs_loader=docs_loader,
            openai_service=openai_svc,
            airtable_service=airtable_svc,
            operational_knowledge_loader=ok_loader,
            analytics_loader=analytics_loader,
            platform_signal_loader=platform_signal_loader,
            weekly_analysis_loader=weekly_analysis_loader,
            correction_repository=correction_file_repo,
            commercial_class_log=commercial_class_log,
            content_ops_live_actions=live_actions,
        )

        from operator_core.core.content_ops.platform_mode_store import PlatformModeStore
        from operator_core.proactive.checker import SuppressionStore
        from operator_core.proactive.daily_plan_schedule_service import DailyPlanScheduleService
        from operator_core.proactive.daily_plan_schedule_store import DailyPlanScheduleStore
        from operator_core.proactive.pending_store import ProactivePendingStore
        from operator_core.proactive.plan_reminder_store import PlanReminderStore
        from operator_core.proactive.plan_reminder_service import PlanReminderService
        from operator_core.proactive.posting_recommender import PostingRecommender
        from operator_core.core.analysis_foundation.weekly_analysis_service import WeeklyAnalysisService
        from operator_core.core.analysis_foundation.weekly_analysis_schedule_service import WeeklyAnalysisScheduleService
        from operator_core.integrations.daily_plan_generation_service import DailyPlanGenerationService
        from operator_core.integrations.daily_plan_service import DailyPlanService
        from operator_core.integrations.daily_plan_upload_service import DailyPlanUploadService

        idea_correction_svc = IdeaCorrectionService(
            correction_store=CorrectionCaptureStore(),
            event_log_service=execution_svc.event_log_service,
            correction_repository=correction_file_repo,
        )

        pending_store = ProactivePendingStore()
        stale_rejection_suppression = SuppressionStore(cooldown_hours=168)
        plan_reminder_store = PlanReminderStore(file_path=runtime_state_dir / "plan_reminders.json")
        daily_plan_schedule_store = DailyPlanScheduleStore(
            file_path=runtime_state_dir / "daily_plan_schedule_sent.json"
        )
        platform_mode_store = PlatformModeStore()
        daily_plan_message_store = DailyPlanMessageStore()
        self._pending_store = pending_store

        plan_recommender = (
            PostingRecommender(
                airtable_svc=airtable_svc,
                ok_loader=ok_loader,
                platform_signal_loader=platform_signal_loader,
                weekly_analysis_loader=weekly_analysis_loader,
            )
            if airtable_svc is not None and ok_loader is not None
            else None
        )

        daily_plan_svc = (
            DailyPlanService(airtable_svc)
            if airtable_svc is not None
            and live_actions is not None
            and "save_daily_plan" in live_actions
            else None
        )
        daily_plan_upload_svc = (
            DailyPlanUploadService(
                airtable_service=airtable_svc,
                ok_loader=ok_loader,
                daily_plan_service=daily_plan_svc,
            )
            if airtable_svc is not None
            and ok_loader is not None
            and daily_plan_svc is not None
            and settings.airtable.get_base_id("analytics")
            else None
        )

        daily_plan_gen_svc = (
            DailyPlanGenerationService(
                daily_plan_service=daily_plan_svc,
                openai_service=openai_svc,
                ok_loader=ok_loader,
                platform_signal_loader=platform_signal_loader,
                weekly_analysis_loader=weekly_analysis_loader,
                docs_loader=docs_loader,
            )
            if daily_plan_svc is not None and openai_svc is not None
            else None
        )

        poller = TelegramPoller(
            bootstrap_context=ctx,
            telegram_service=telegram_service,
            request_flow_service=RequestFlowService(
                execution_svc,
                pending_store=pending_store,
                stale_rejection_suppression=stale_rejection_suppression,
                posting_recommender=plan_recommender,
                daily_plan_service=daily_plan_svc,
                daily_plan_upload_service=daily_plan_upload_svc,
                daily_plan_generation_service=daily_plan_gen_svc,
                plan_reminder_store=plan_reminder_store,
                platform_mode_store=platform_mode_store,
                daily_plan_message_store=daily_plan_message_store,
                idea_correction_service=idea_correction_svc,
            ),
            formatter_service=ResponseFormatterService(),
        )

        thread = Thread(
            target=poller.run_until_stopped,
            args=(self.stop_event,),
            daemon=True,
            name="telegram-poller",
        )
        thread.start()

        plan_reminder_svc = PlanReminderService(
            store=plan_reminder_store,
            telegram_svc=TelegramService(ctx),
            settings=settings,
            daily_plan_service=daily_plan_svc,
            project_key=settings.app.active_project,
        )
        reminder_thread = Thread(
            target=plan_reminder_svc.run_until_stopped,
            args=(self.stop_event,),
            daemon=True,
            name="plan-reminder",
        )
        reminder_thread.start()
        self.logger.info(
            "operator core plan reminder service started | project=%s",
            self.project_context.project_key,
        )
        if daily_plan_svc is not None:
            daily_plan_schedule_svc = DailyPlanScheduleService(
                daily_plan_service=daily_plan_svc,
                posting_recommender=plan_recommender,
                telegram_svc=TelegramService(ctx),
                settings=settings,
                schedule_store=daily_plan_schedule_store,
                daily_plan_message_store=daily_plan_message_store,
                project_key=settings.app.active_project,
            )
            daily_plan_thread = Thread(
                target=daily_plan_schedule_svc.run_until_stopped,
                args=(self.stop_event,),
                daemon=True,
                name="daily-plan-scheduler",
            )
            daily_plan_thread.start()
            self.logger.info(
                "operator core daily plan schedule service started | project=%s",
                self.project_context.project_key,
            )

        if weekly_analysis_loader is not None and openai_svc is not None:
            weekly_analysis_svc = WeeklyAnalysisService(
                foundation_service=execution_svc.analysis_foundation_service,
                persistence_service=weekly_analysis_loader,
                openai_service=openai_svc,
            )
            weekly_schedule_svc = WeeklyAnalysisScheduleService(
                weekly_analysis_service=weekly_analysis_svc,
                persistence_service=weekly_analysis_loader,
                project_key=settings.app.active_project,
            )
            weekly_thread = Thread(
                target=weekly_schedule_svc.run_until_stopped,
                args=(self.stop_event,),
                daemon=True,
                name="weekly-analysis-scheduler",
            )
            weekly_thread.start()
            self.logger.info(
                "operator core weekly analysis schedule service started | project=%s",
                self.project_context.project_key,
            )

        self.logger.info(
            "operator core telegram polling started | project=%s",
            self.project_context.project_key,
        )

    def _register_telegram_commands(self, telegram_service: object) -> None:
        try:
            getattr(telegram_service, "set_my_commands")(commands=_TELEGRAM_BOT_COMMANDS)
            self.logger.info(
                "telegram slash commands registered | project=%s command_count=%s",
                self.project_context.project_key,
                len(_TELEGRAM_BOT_COMMANDS),
            )
        except TelegramServiceError as exc:
            self.logger.error(
                "telegram slash command registration failed | project=%s error=%s",
                self.project_context.project_key,
                exc,
            )

    def _start_proactive_checker(self) -> None:
        try:
            from operator_core.integrations.airtable_service import AirtableService
            from operator_core.integrations.operational_knowledge_service import OperationalKnowledgeLoader
            from operator_core.integrations.platform_signal_service import PlatformSignalLoader
            from operator_core.integrations.telegram_service import TelegramService
            from operator_core.proactive.checker import (
                AnalyticsGapTrigger,
                ProactiveCheckerService,
                StaleDraftTrigger,
            )
            from operator_core.proactive.posting_recommender import PostingRecommender

            ctx = self.bootstrap_context
            settings = ctx.settings

            if settings.app.active_project != "everydayengel":
                return
            if not settings.airtable.enabled:
                return

            airtable_svc = AirtableService(ctx)
            telegram_svc = TelegramService(ctx)
            pending_store = getattr(self, "_pending_store", None)

            ok_loader = OperationalKnowledgeLoader(airtable_svc)
            platform_signal_loader = PlatformSignalLoader(
                airtable_svc=airtable_svc,
                ok_loader=ok_loader,
            )
            recommender = PostingRecommender(
                airtable_svc=airtable_svc,
                ok_loader=ok_loader,
                platform_signal_loader=platform_signal_loader,
            )

            checker = ProactiveCheckerService(
                airtable_svc=airtable_svc,
                telegram_svc=telegram_svc,
                settings=settings,
                analytics_gap_trigger=AnalyticsGapTrigger(
                    platform_signal_loader=platform_signal_loader,
                    ok_project_key=settings.app.active_project,
                ),
                stale_draft_trigger=StaleDraftTrigger(),
                pending_store=pending_store,
                posting_recommender=recommender,
            )

            thread = Thread(
                target=checker.run_until_stopped,
                args=(self.stop_event,),
                daemon=True,
                name="proactive-checker",
            )
            thread.start()
            self.logger.info(
                "operator core proactive checker started | project=%s",
                self.project_context.project_key,
            )
        except Exception as exc:
            self.logger.error(
                "operator core proactive checker failed to start | error=%s", exc
            )

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_stop_signal)
        signal.signal(signal.SIGINT, self._handle_stop_signal)

    def _handle_stop_signal(self, signum: int, frame: FrameType | None) -> None:
        del frame

        if self.signal_name is None:
            self.signal_name = signal.Signals(signum).name
            self.logger.info(
                "operator core runtime stopping | project=%s signal=%s",
                self.project_context.project_key,
                self.signal_name,
            )

        self.stop_event.set()

    def _log_runtime_started(self) -> None:
        self.logger.info(
            "operator core runtime started | project=%s status=%s interface=%s human_in_the_loop=%s",
            self.project_context.project_key,
            self.project_context.status,
            self.project_context.primary_interface,
            self.project_context.human_in_the_loop,
        )

    def _log_runtime_stopped(self) -> None:
        self.logger.info(
            "operator core runtime stopped | project=%s signal=%s",
            self.project_context.project_key,
            self.signal_name or "unknown",
        )
