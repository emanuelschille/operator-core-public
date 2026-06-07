from pathlib import Path
from tempfile import TemporaryDirectory

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.execution_service import ExecutionService
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.repositories import (
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryRunRepository,
)
from operator_core.core.backbone.run_service import RunService
from operator_core.core.content_ops.correction_capture import CommercialClass, CommercialClassLog, CommercialLogEntry
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.interfaces.telegram.entry_flow import build_telegram_entry_handoff


def build_bootstrap_context() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="dev",
            log_level="INFO",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(
            enabled=False,
            bot_token="",
            allowed_user_ids=(),
            allowed_chat_ids=(),
        ),
        airtable=AirtableSettings(
            enabled=False,
            api_key="",
            project_base_ids={"everydayengel": ""},
        ),
        openai=OpenAISettings(
            enabled=False,
            api_key="",
            model="gpt-5",
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )
    return BootstrapContext(
        settings=settings,
        runtime_path=Path("projects/everydayengel/runtime.yaml"),
        project_runtime={
            "project_key": "everydayengel",
            "display_name": "Everyday Engel",
            "status": "active",
            "primary_interface": "telegram",
            "human_in_the_loop": "true",
        },
    )


def build_service() -> tuple[
    RequestFlowService,
    InMemoryJobRepository,
    InMemoryRunRepository,
    InMemoryEventRepository,
]:
    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    execution_service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
    )
    return RequestFlowService(execution_service), job_repo, run_repo, event_repo


def build_service_with_commercial_log(commercial_class_log: CommercialClassLog) -> tuple[
    RequestFlowService,
    InMemoryJobRepository,
    InMemoryRunRepository,
    InMemoryEventRepository,
]:
    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    execution_service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
        commercial_class_log=commercial_class_log,
    )
    return RequestFlowService(execution_service), job_repo, run_repo, event_repo


def test_known_command_flows_into_execution_service() -> None:
    request_flow_service, job_repo, run_repo, event_repo = build_service()
    update_payload = {
        "update_id": 1001,
        "message": {
            "message_id": 2002,
            "text": "neu erster test",
            "chat": {"id": 3003, "type": "private"},
            "from": {"id": 4004, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.decision == "executed"
    assert result.request_context.project_key == "everydayengel"
    assert result.request_context.command_name == "neu"
    assert result.execution_result is not None
    assert result.execution_result.job_status.value == "completed"
    assert result.formatter_payload.execution_summary["job_status"] == "completed"

    job = job_repo.get(result.execution_result.job_id)
    run = run_repo.get(result.execution_result.run_id)
    assert job is not None
    assert run is not None
    assert len(event_repo.list_for_entity("everydayengel", "job", job.job_id)) == 3
    assert len(event_repo.list_for_entity("everydayengel", "run", run.run_id)) == 3


def test_unknown_command_is_handled_without_execution() -> None:
    request_flow_service, job_repo, run_repo, event_repo = build_service()
    update_payload = {
        "update_id": 1002,
        "message": {
            "message_id": 2003,
            "text": "/nonsense please ignore",
            "chat": {"id": 3004, "type": "private"},
            "from": {"id": 4005, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is False
    assert result.decision == "unknown_command"
    assert result.execution_result is None
    assert result.formatter_payload.execution_summary == {}
    assert job_repo.list_by_project("everydayengel") == []
    assert run_repo.list_by_job("missing-job") == []
    assert event_repo.list_for_entity("everydayengel", "job", "missing-job") == []


def test_plain_message_returns_action_selection_buttons() -> None:
    request_flow_service, job_repo, _, _ = build_service()
    update_payload = {
        "update_id": 1003,
        "message": {
            "message_id": 2004,
            "text": "hallo zusammen",
            "chat": {"id": 3005, "type": "private"},
            "from": {"id": 4006, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is False
    assert result.decision == "free_text_selection"
    assert result.request_context.command_name == "message"
    assert result.formatter_payload.decision == "free_text_selection"
    assert "Wofür möchtest du das verwenden?" in result.formatter_payload.message_text
    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    assert markup["inline_keyboard"][0][0]["callback_data"] == "text_action:idea"
    assert markup["inline_keyboard"][0][1]["callback_data"] == "text_action:draft"
    assert markup["inline_keyboard"][1][0]["callback_data"] == "text_action:hook"
    assert markup["inline_keyboard"][1][1]["callback_data"] == "text_action:caption"
    assert markup["inline_keyboard"][2][0]["callback_data"] == "text_action:cancel"
    assert job_repo.list_by_project("everydayengel") == []


def test_text_action_callback_executes_existing_flow_with_pending_text() -> None:
    request_flow_service, job_repo, run_repo, event_repo = build_service()
    message_update = {
        "update_id": 10030,
        "message": {
            "message_id": 2104,
            "text": "Kekse zur Weihnachtszeit",
            "chat": {"id": 3105, "type": "private"},
            "from": {"id": 4106, "username": "julia"},
        },
    }
    callback_update = {
        "update_id": 10031,
        "callback_query": {
            "id": "cbq-text-1",
            "from": {"id": 4106, "username": "julia"},
            "data": "text_action:draft",
            "message": {
                "message_id": 2105,
                "text": "📝 Eingabe erkannt",
                "chat": {"id": 3105, "type": "private"},
            },
        },
    }

    request_flow_service.handle_telegram_entry_handoff(
        build_telegram_entry_handoff(message_update, build_bootstrap_context())
    )
    result = request_flow_service.handle_telegram_entry_handoff(
        build_telegram_entry_handoff(callback_update, build_bootstrap_context())
    )

    assert result.was_executed is True
    assert result.decision == "executed"
    assert result.request_context.command_name == "draft"
    assert result.request_context.command_body == "Kekse zur Weihnachtszeit"
    assert result.formatter_payload.callback_query_id == "cbq-text-1"
    assert result.formatter_payload.callback_answer_text == "Entwurf schreiben"

    assert result.formatter_payload.edit_message_id == 2105
    assert "Kekse zur Weihnachtszeit" in (result.formatter_payload.edit_message_text or "")
    assert len(job_repo.list_by_project("everydayengel")) == 1
    assert result.execution_result is not None
    assert len(run_repo.list_by_job(result.execution_result.job_id)) == 1
    assert event_repo.list_for_entity("everydayengel", "job", result.execution_result.job_id)


def test_text_action_cancel_clears_pending_text() -> None:
    request_flow_service, job_repo, _, _ = build_service()
    message_update = {
        "update_id": 10032,
        "message": {
            "message_id": 2106,
            "text": "Frühlingsfrühstück",
            "chat": {"id": 3106, "type": "private"},
            "from": {"id": 4107, "username": "julia"},
        },
    }
    callback_update = {
        "update_id": 10033,
        "callback_query": {
            "id": "cbq-text-2",
            "from": {"id": 4107, "username": "julia"},
            "data": "text_action:cancel",
            "message": {
                "message_id": 2107,
                "text": "📝 Eingabe erkannt",
                "chat": {"id": 3106, "type": "private"},
            },
        },
    }

    request_flow_service.handle_telegram_entry_handoff(
        build_telegram_entry_handoff(message_update, build_bootstrap_context())
    )
    result = request_flow_service.handle_telegram_entry_handoff(
        build_telegram_entry_handoff(callback_update, build_bootstrap_context())
    )

    assert result.was_executed is False
    assert result.decision == "text_action_callback"
    assert result.formatter_payload.send_response is False
    assert result.formatter_payload.callback_answer_text == "Abbrechen"
    assert result.formatter_payload.edit_message_text == "✖️ Eingabe verworfen."
    assert job_repo.list_by_project("everydayengel") == []


def test_status_command_executes_with_commercial_mix_snapshot() -> None:
    with TemporaryDirectory() as tmpdir:
        log = CommercialClassLog(file_path=Path(tmpdir) / "commercial_class_log.json")
        log.append(
            CommercialLogEntry(
                record_id="c1",
                project_key="everydayengel",
                action_type="idea",
                platform="instagram_reel",
                commercial_class=CommercialClass.trust_building,
                prompt_excerpt="eins",
            )
        )
        log.append(
            CommercialLogEntry(
                record_id="c2",
                project_key="everydayengel",
                action_type="draft",
                platform="tiktok",
                commercial_class=CommercialClass.product_near,
                prompt_excerpt="zwei",
            )
        )
        request_flow_service, _, _, _ = build_service_with_commercial_log(log)
        update_payload = {
            "update_id": 1033,
            "message": {
                "message_id": 2406,
                "text": "/status",
                "chat": {"id": 3106, "type": "private"},
                "from": {"id": 4106, "username": "julia"},
            },
        }

        handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
        result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.request_context.command_name == "status"
    assert result.execution_result is not None
    snapshot = result.execution_result.output_snapshot or {}
    assert snapshot["status_type"] == "commercial_mix"
    assert snapshot["window_days"] == 7
    assert snapshot["total"] == 2
    assert snapshot["commercial_mix"]["trust_building"] == 1
    assert snapshot["commercial_mix"]["product_near"] == 1


def test_plan_demo_returns_message_with_inline_buttons() -> None:
    request_flow_service, job_repo, run_repo, event_repo = build_service()
    update_payload = {
        "update_id": 1004,
        "message": {
            "message_id": 2005,
            "text": "/plan_demo",
            "chat": {"id": 3006, "type": "private"},
            "from": {"id": 4007, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is False
    assert result.decision == "plan_demo"
    assert result.formatter_payload.message_text.startswith("📋 Tagesplan")
    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    assert markup["inline_keyboard"][0][0]["text"] == "✅ Heute posten"
    assert markup["inline_keyboard"][0][0]["callback_data"] == "plan_demo:execute_today"
    assert job_repo.list_by_project("everydayengel") == []
    assert run_repo.list_by_job("missing-job") == []
    assert event_repo.list_for_entity("everydayengel", "job", "missing-job") == []


def test_menu_button_returns_menu_with_inline_buttons() -> None:
    request_flow_service, job_repo, run_repo, event_repo = build_service()
    update_payload = {
        "update_id": 1007,
        "message": {
            "message_id": 2008,
            "text": "☰ Menü",
            "chat": {"id": 3008, "type": "private"},
            "from": {"id": 4009, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is False
    assert result.decision == "menu"
    assert result.request_context.command_name == "menu"
    assert result.formatter_payload.message_text == "☰ Menü\n\nWähle eine Aktion."
    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    assert markup["inline_keyboard"][0][0]["text"] == "📋 Tagesplan"
    assert markup["inline_keyboard"][0][0]["callback_data"] == "menu:plan"
    assert markup["inline_keyboard"][0][1]["text"] == "📊 Status"

    assert markup["inline_keyboard"][1][0]["text"] == "💡 Idee erfassen"
    assert markup["inline_keyboard"][1][1]["text"] == "📝 Entwurf erstellen"
    assert markup["inline_keyboard"][2][0]["text"] == "🎣 Hook erstellen"
    assert markup["inline_keyboard"][2][1]["text"] == "💬 Caption erstellen"
    assert job_repo.list_by_project("everydayengel") == []
    assert run_repo.list_by_job("missing-job") == []
    assert event_repo.list_for_entity("everydayengel", "job", "missing-job") == []


def test_menu_callback_executes_existing_command_flow() -> None:
    request_flow_service, job_repo, run_repo, event_repo = build_service()
    update_payload = {
        "update_id": 1008,
        "callback_query": {
            "id": "cbq-44",
            "from": {"id": 4010, "username": "julia"},
            "data": "menu:draft",
            "message": {
                "message_id": 2009,
                "text": "☰ Menü",
                "chat": {"id": 3009, "type": "private"},
            },
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.decision == "executed"
    assert result.request_context.command_name == "draft"
    assert result.formatter_payload.command_name == "draft"
    assert result.formatter_payload.command_body == ""
    assert result.formatter_payload.callback_query_id == "cbq-44"
    assert result.formatter_payload.callback_answer_text == "Entwurf erstellen"
    assert result.formatter_payload.send_response is True
    assert result.execution_result is not None
    assert len(job_repo.list_by_project("everydayengel")) == 1
    assert len(run_repo.list_by_job(result.execution_result.job_id)) == 1
    assert event_repo.list_for_entity("everydayengel", "job", result.execution_result.job_id)


def test_plan_demo_callback_returns_confirmation_text() -> None:
    request_flow_service, _, _, _ = build_service()
    update_payload = {
        "update_id": 1005,
        "callback_query": {
            "id": "cbq-42",
            "from": {"id": 4008, "username": "julia"},
            "data": "plan_demo:execute_today",
            "message": {
                "message_id": 2006,
                "text": "📋 Tagesplan",
                "chat": {"id": 3007, "type": "private"},
            },
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is False
    assert result.decision == "plan_demo_callback"
    assert result.formatter_payload.command_body == "plan_demo:execute_today"
    assert result.formatter_payload.message_text == ""
    assert result.formatter_payload.callback_query_id == "cbq-42"
    assert result.formatter_payload.callback_answer_text == "Heute posten"
    assert result.formatter_payload.send_response is False
    assert result.formatter_payload.edit_message_id == 2006
    assert result.formatter_payload.edit_message_text is not None
    assert "✅ Auswahl gespeichert: Heute posten" in result.formatter_payload.edit_message_text
    assert result.formatter_payload.edit_reply_markup == {
        "inline_keyboard": [[{"text": "🔁 Auswahl ändern", "callback_data": "plan_demo:change_selection"}]]
    }


def test_plan_demo_change_selection_restores_original_buttons() -> None:
    request_flow_service, _, _, _ = build_service()
    update_payload = {
        "update_id": 1006,
        "callback_query": {
            "id": "cbq-43",
            "from": {"id": 4008, "username": "julia"},
            "data": "plan_demo:change_selection",
            "message": {
                "message_id": 2007,
                "text": "📋 Tagesplan\n✅ Auswahl gespeichert: Heute so ausführen",
                "chat": {"id": 3007, "type": "private"},
            },
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is False
    assert result.decision == "plan_demo_callback"
    assert result.formatter_payload.callback_answer_text == "Auswahl zurückgesetzt"
    assert result.formatter_payload.send_response is False
    assert result.formatter_payload.edit_message_id == 2007
    assert result.formatter_payload.edit_message_text.startswith("📋 Tagesplan\n\n")
    assert "✅ Auswahl gespeichert:" not in result.formatter_payload.edit_message_text
    markup = result.formatter_payload.edit_reply_markup
    assert markup is not None
    assert markup["inline_keyboard"][0][0]["callback_data"] == "plan_demo:execute_today"


# ---------------------------------------------------------------------------
# Real daily plan decision tests
# ---------------------------------------------------------------------------

from operator_core.proactive.posting_recommender import (
    PostingCandidate,
    PostingRecommendation,
    PostingRecommender,
)


class _MockRecommenderWithCandidate:
    """Stub that always returns a fixed PostingRecommendation."""

    def __init__(self) -> None:
        self._candidate = PostingCandidate(
            record_id="recABC",
            platform="tiktok",
            hook_preview="Warum du jeden Tag…",
            content_stage="drafted",
            content_format="reel",
            has_body=False,
            days_ready=7,
            days_since_last_post=-1,
            posting_time="20:00",
        )
        self._recommendation = PostingRecommendation(
            candidate=self._candidate,
            telegram_message="(proactive message)",
            candidate_count=2,
        )

    def recommend(self, *, project_key: str) -> PostingRecommendation:
        return self._recommendation

    def eligible_draft_count(self, *, project_key: str) -> int:
        return 2


class _MockRecommenderNoCandidate:
    """Stub with no recommendation but existing drafts."""

    def __init__(self, draft_count: int = 3) -> None:
        self._draft_count = draft_count

    def recommend(self, *, project_key: str) -> None:
        return None

    def eligible_draft_count(self, *, project_key: str) -> int:
        return self._draft_count


def build_service_with_recommender(recommender: object) -> RequestFlowService:
    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    execution_service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
    )
    return RequestFlowService(execution_service, posting_recommender=recommender)  # type: ignore[arg-type]


def _plan_demo_handoff(bootstrap_ctx: BootstrapContext) -> object:
    update_payload = {
        "update_id": 2000,
        "message": {
            "message_id": 3000,
            "text": "/plan_demo",
            "chat": {"id": 4000, "type": "private"},
            "from": {"id": 5000, "username": "julia"},
        },
    }
    return build_telegram_entry_handoff(update_payload, bootstrap_ctx)


def test_plan_demo_with_recommendation_shows_post_plan() -> None:
    svc = build_service_with_recommender(_MockRecommenderWithCandidate())
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo"
    text = result.formatter_payload.message_text
    assert "Heute posten: ja" in text
    assert "Anzahl: 1" in text
    assert "Plattform: TikTok" in text
    assert "Uhrzeit: 20:00" in text
    assert "Entwurf seit 7 Tagen bereit" in text
    assert "TikTok noch nie gepostet" in text
    assert "Ausgewählt aus 2 passenden Entwürfen (ältester zuerst)" in text


def test_plan_demo_without_recommendation_with_backlog_shows_draft_plan() -> None:
    svc = build_service_with_recommender(_MockRecommenderNoCandidate(draft_count=3))
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo"
    text = result.formatter_payload.message_text
    assert "Heute posten: nein" in text
    assert "Stattdessen:" in text
    assert "3 Entwürfe im Backlog" in text


def test_plan_demo_without_candidate_or_backlog_shows_skip_plan() -> None:
    svc = build_service_with_recommender(_MockRecommenderNoCandidate(draft_count=0))
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo"
    text = result.formatter_payload.message_text
    assert "Heute posten: nein" in text
    assert "Kein Inhalt bereit" in text


# ---------------------------------------------------------------------------
# Daily plan persistence tests
# ---------------------------------------------------------------------------


class _MockDailyPlanService:
    """Minimal stub for DailyPlanService."""

    def __init__(
        self,
        record_id: str = "recTestPlan001",
        today_snapshot: object = None,
    ) -> None:
        self._record_id = record_id
        self._today_snapshot = today_snapshot
        self.upsert_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def get_today_plan(self, *, project_key: str, date: str) -> object:
        self.get_calls.append({"project_key": project_key, "date": date})
        return self._today_snapshot

    def upsert_plan(self, **kwargs: object) -> str:
        self.upsert_calls.append(dict(kwargs))
        return self._record_id

    def update_decision(self, **kwargs: object) -> None:
        self.update_calls.append(dict(kwargs))


def _build_service_with_daily_plan(
    daily_plan_service: object,
    recommender: object = None,
) -> RequestFlowService:
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    return RequestFlowService(
        execution_service,
        posting_recommender=recommender,  # type: ignore[arg-type]
        daily_plan_service=daily_plan_service,  # type: ignore[arg-type]
    )


def _callback_handoff(callback_data: str, bootstrap_ctx: BootstrapContext) -> object:
    update_payload = {
        "update_id": 3000,
        "callback_query": {
            "id": "cbq-persist-01",
            "from": {"id": 5000, "username": "julia"},
            "data": callback_data,
            "message": {
                "message_id": 4000,
                "text": "📋 Tagesplan",
                "chat": {"id": 6000, "type": "private"},
            },
        },
    }
    return build_telegram_entry_handoff(update_payload, bootstrap_ctx)


def test_plan_demo_embeds_record_id_in_buttons() -> None:
    daily_plan_svc = _MockDailyPlanService(record_id="recTestPlan001")
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo"
    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    buttons = [btn for row in markup["inline_keyboard"] for btn in row]
    for btn in buttons:
        assert "recTestPlan001" in btn["callback_data"], (
            f"Expected record_id in callback_data, got: {btn['callback_data']}"
        )
    # Upsert was called once
    assert len(daily_plan_svc.upsert_calls) == 1
    assert daily_plan_svc.upsert_calls[0]["project_key"] == "everydayengel"


def test_plan_demo_no_service_falls_back_to_callbacks_without_record_id() -> None:
    svc = build_service_with_recommender(None)  # no daily_plan_service
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    # All buttons should use the plain format (no record_id segment)
    assert markup["inline_keyboard"][0][0]["callback_data"] == "plan_demo:execute_today"
    assert markup["inline_keyboard"][0][1]["callback_data"] == "plan_demo:skip_today"


def test_callback_execute_today_calls_update_decision_post() -> None:
    daily_plan_svc = _MockDailyPlanService()
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _callback_handoff("plan_demo:execute_today:recTestPlan001", build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo_callback"
    assert len(daily_plan_svc.update_calls) == 1
    call = daily_plan_svc.update_calls[0]
    assert call["record_id"] == "recTestPlan001"
    assert call["decision"] == "post"
    assert call["project_key"] == "everydayengel"


def test_callback_skip_today_calls_update_decision_skip() -> None:
    daily_plan_svc = _MockDailyPlanService()
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _callback_handoff("plan_demo:skip_today:recTestPlan001", build_bootstrap_context())
    svc.handle_telegram_entry_handoff(handoff)

    assert daily_plan_svc.update_calls[0]["decision"] == "skip"


def test_callback_draft_instead_calls_update_decision_draft() -> None:
    daily_plan_svc = _MockDailyPlanService()
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _callback_handoff("plan_demo:draft_instead:recTestPlan001", build_bootstrap_context())
    svc.handle_telegram_entry_handoff(handoff)

    assert daily_plan_svc.update_calls[0]["decision"] == "draft"


def test_callback_remind_later_no_update_decision() -> None:
    daily_plan_svc = _MockDailyPlanService()
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _callback_handoff("plan_demo:remind_later:recTestPlan001", build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo_callback"
    assert daily_plan_svc.update_calls == [], "remind_later must not trigger update_decision"


def test_callback_change_selection_resets_to_pending() -> None:
    daily_plan_svc = _MockDailyPlanService()
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _callback_handoff("plan_demo:change_selection:recTestPlan001", build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo_callback"
    assert result.formatter_payload.callback_answer_text == "Auswahl zurückgesetzt"
    assert len(daily_plan_svc.update_calls) == 1
    call = daily_plan_svc.update_calls[0]
    assert call["record_id"] == "recTestPlan001"
    assert call["decision"] == "pending"


def test_callback_change_selection_embeds_record_id_in_reset_buttons() -> None:
    daily_plan_svc = _MockDailyPlanService()
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _callback_handoff("plan_demo:change_selection:recTestPlan001", build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    markup = result.formatter_payload.edit_reply_markup
    assert markup is not None
    assert "recTestPlan001" in markup["inline_keyboard"][0][0]["callback_data"]


def test_callback_empty_record_id_does_not_call_update_decision() -> None:
    daily_plan_svc = _MockDailyPlanService()
    svc = _build_service_with_daily_plan(daily_plan_svc)
    # Old-format callback without record_id segment
    handoff = _callback_handoff("plan_demo:execute_today", build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo_callback"
    assert daily_plan_svc.update_calls == [], "Empty record_id must not trigger update_decision"


def test_selected_markup_embeds_record_id_in_change_selection_button() -> None:
    daily_plan_svc = _MockDailyPlanService()
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _callback_handoff("plan_demo:execute_today:recTestPlan001", build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    markup = result.formatter_payload.edit_reply_markup
    assert markup is not None
    change_btn = markup["inline_keyboard"][0][0]
    assert change_btn["text"] == "🔁 Auswahl ändern"
    assert "recTestPlan001" in change_btn["callback_data"]


# ---------------------------------------------------------------------------
# Daily plan readback tests
# ---------------------------------------------------------------------------

from operator_core.integrations.daily_plan_service import TodayPlanSnapshot


def _make_snapshot(
    decision: str = "post",
    plan_type: str | None = "post",
    platform: str | None = "tiktok",
    candidate_count: int | None = 2,
    record_id: str = "recStoredPlan001",
) -> TodayPlanSnapshot:
    return TodayPlanSnapshot(
        record_id=record_id,
        decision=decision,
        plan_type=plan_type,
        platform=platform,
        candidate_count=candidate_count,
    )


def test_decided_plan_shows_readback_not_main_buttons() -> None:
    """A decided plan should surface the change button, not the four main buttons."""
    snapshot = _make_snapshot(decision="post")
    daily_plan_svc = _MockDailyPlanService(today_snapshot=snapshot)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo"
    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    # Only one button: "🔁 Auswahl ändern"
    buttons = [btn for row in markup["inline_keyboard"] for btn in row]
    assert len(buttons) == 1
    assert buttons[0]["text"] == "🔁 Auswahl ändern"
    assert "recStoredPlan001" in buttons[0]["callback_data"]


def test_decided_plan_does_not_call_upsert() -> None:
    """A decided plan readback must not trigger upsert_plan."""
    snapshot = _make_snapshot(decision="skip")
    daily_plan_svc = _MockDailyPlanService(today_snapshot=snapshot)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    svc.handle_telegram_entry_handoff(handoff)

    assert daily_plan_svc.upsert_calls == [], "upsert_plan must not be called for a decided plan"


def test_pending_plan_shows_main_buttons_and_calls_upsert() -> None:
    """A pending existing plan should fall through to upsert + main buttons."""
    snapshot = TodayPlanSnapshot(
        record_id="recPending001",
        decision="pending",
        plan_type="post",
        platform="tiktok",
    )
    daily_plan_svc = _MockDailyPlanService(record_id="recPending001", today_snapshot=snapshot)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo"
    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    buttons = [btn for row in markup["inline_keyboard"] for btn in row]
    assert any("execute_today" in btn["callback_data"] for btn in buttons)
    assert len(daily_plan_svc.upsert_calls) == 1


def test_no_existing_plan_computes_fresh_plan_and_calls_upsert() -> None:
    """No existing plan: compute fresh, upsert, show main buttons."""
    daily_plan_svc = _MockDailyPlanService(today_snapshot=None)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo"
    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    assert len(daily_plan_svc.upsert_calls) == 1
    assert daily_plan_svc.upsert_calls[0]["project_key"] == "everydayengel"


def test_decided_post_plan_shows_status_label() -> None:
    snapshot = _make_snapshot(decision="post", plan_type="post", platform="tiktok")
    daily_plan_svc = _MockDailyPlanService(today_snapshot=snapshot)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    text = result.formatter_payload.message_text
    assert "Status: Heute posten gewählt" in text


def test_decided_skip_plan_shows_status_label() -> None:
    snapshot = _make_snapshot(decision="skip", plan_type="skip", platform=None)
    daily_plan_svc = _MockDailyPlanService(today_snapshot=snapshot)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    text = result.formatter_payload.message_text
    assert "Status: Heute auslassen gewählt" in text


def test_decided_draft_plan_shows_status_label() -> None:
    snapshot = _make_snapshot(decision="draft", plan_type="draft", platform=None)
    daily_plan_svc = _MockDailyPlanService(today_snapshot=snapshot)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    text = result.formatter_payload.message_text
    assert "Status: Entwurf erstellen gewählt" in text


def test_decided_post_plan_shows_platform_when_stored() -> None:
    snapshot = _make_snapshot(decision="post", plan_type="post", platform="tiktok")
    daily_plan_svc = _MockDailyPlanService(today_snapshot=snapshot)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    text = result.formatter_payload.message_text
    assert "Plattform: TikTok" in text
    assert "Heute posten: ja" in text


def test_decided_plan_text_starts_with_tagesplan_header() -> None:
    """Readback message must still look like a real Tagesplan."""
    snapshot = _make_snapshot(decision="post", plan_type="post", platform="instagram_reel")
    daily_plan_svc = _MockDailyPlanService(today_snapshot=snapshot)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    result = svc.handle_telegram_entry_handoff(handoff)

    text = result.formatter_payload.message_text
    assert text.startswith("📋 Tagesplan")
    assert "Plattform: Instagram" in text


def test_get_today_plan_is_called_with_correct_args() -> None:
    """get_today_plan must be called with today's date and correct project_key.

    When snapshot is None (no row) and upsert_plan returns a record_id, the
    authority re-check triggers a second get_today_plan call.  Both calls must
    use the same date and project_key.
    """
    import datetime as _dt

    daily_plan_svc = _MockDailyPlanService(today_snapshot=None)
    svc = _build_service_with_daily_plan(daily_plan_svc)
    handoff = _plan_demo_handoff(build_bootstrap_context())
    svc.handle_telegram_entry_handoff(handoff)

    # Initial check + authority re-check (snapshot was None, upsert returned record_id).
    assert len(daily_plan_svc.get_calls) == 2
    for call in daily_plan_svc.get_calls:
        assert call["project_key"] == "everydayengel"
        assert call["date"] == _dt.date.today().isoformat()


def test_change_selection_updates_same_record_used_by_readback() -> None:
    snapshot = _make_snapshot(decision="skip", plan_type="skip", record_id="recDecided001")
    daily_plan_svc = _MockDailyPlanService(today_snapshot=snapshot)
    svc = _build_service_with_daily_plan(daily_plan_svc)

    initial = svc.handle_telegram_entry_handoff(_plan_demo_handoff(build_bootstrap_context()))
    markup = initial.formatter_payload.response_reply_markup
    assert markup is not None
    callback_data = markup["inline_keyboard"][0][0]["callback_data"]
    assert callback_data == "plan_demo:change_selection:recDecided001"

    result = svc.handle_telegram_entry_handoff(
        _callback_handoff(callback_data, build_bootstrap_context())
    )

    assert result.decision == "plan_demo_callback"
    assert daily_plan_svc.update_calls[0]["record_id"] == "recDecided001"
    assert daily_plan_svc.update_calls[0]["decision"] == "pending"


# ---------------------------------------------------------------------------
# Authority re-check tests: decided plan surfaced even when first get fails
# ---------------------------------------------------------------------------


class _MockDailyPlanServiceWithSequence:
    """DailyPlanService stub where get_today_plan returns a sequence of results.

    Each entry in get_sequence is either a TodayPlanSnapshot (returned) or an
    Exception subclass instance (raised).  upsert_plan always returns record_id.
    """

    def __init__(
        self,
        get_sequence: list,
        record_id: str = "recTestPlan001",
    ) -> None:
        self._get_sequence = list(get_sequence)
        self._record_id = record_id
        self.get_calls: list[dict] = []
        self.upsert_calls: list[dict] = []
        self.update_calls: list[dict] = []

    def get_today_plan(self, *, project_key: str, date: str) -> object:
        self.get_calls.append({"project_key": project_key, "date": date})
        if not self._get_sequence:
            return None
        result = self._get_sequence.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def upsert_plan(self, **kwargs: object) -> str:
        self.upsert_calls.append(dict(kwargs))
        return self._record_id

    def update_decision(self, **kwargs: object) -> None:
        self.update_calls.append(dict(kwargs))


def test_decided_plan_shown_when_first_get_raises_but_second_succeeds() -> None:
    """If get_today_plan raises on the first call (transient failure) but the
    authority re-check succeeds and returns a decided snapshot, the decided branch
    must be rendered — not the open 4-button plan."""
    decided_snapshot = _make_snapshot(
        decision="post", plan_type="post", platform="tiktok", record_id="recDecided001"
    )
    svc_stub = _MockDailyPlanServiceWithSequence(
        get_sequence=[RuntimeError("transient"), decided_snapshot],
        record_id="recUpsertDummy",
    )
    svc = _build_service_with_daily_plan(svc_stub)
    result = svc.handle_telegram_entry_handoff(_plan_demo_handoff(build_bootstrap_context()))

    assert result.decision == "plan_demo"
    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    buttons = [btn for row in markup["inline_keyboard"] for btn in row]
    # Must show the single change-selection button, not the four open-plan buttons.
    assert len(buttons) == 1
    assert buttons[0]["text"] == "🔁 Auswahl ändern"
    assert "recDecided001" in buttons[0]["callback_data"]
    # Status line must be present.
    assert "Status:" in result.formatter_payload.message_text
    # upsert was called once (branch 2 executed before re-check)
    assert len(svc_stub.upsert_calls) == 1
    # get_today_plan was called twice: initial + re-check
    assert len(svc_stub.get_calls) == 2


def test_decided_plan_shown_when_first_get_returns_none_and_second_returns_decided() -> None:
    """If get_today_plan returns None on the first call (no row found at that instant)
    but the authority re-check returns a decided snapshot, the decided branch is rendered."""
    decided_snapshot = _make_snapshot(
        decision="skip", plan_type="skip", platform=None, record_id="recDecided002"
    )
    svc_stub = _MockDailyPlanServiceWithSequence(
        get_sequence=[None, decided_snapshot],
        record_id="recDecided002",
    )
    svc = _build_service_with_daily_plan(svc_stub)
    result = svc.handle_telegram_entry_handoff(_plan_demo_handoff(build_bootstrap_context()))

    assert result.decision == "plan_demo"
    buttons = [btn for row in result.formatter_payload.response_reply_markup["inline_keyboard"] for btn in row]
    assert len(buttons) == 1
    assert buttons[0]["text"] == "🔁 Auswahl ändern"
    assert "Status: Heute auslassen gewählt" in result.formatter_payload.message_text


def test_open_plan_shown_when_first_get_raises_and_second_also_raises() -> None:
    """If both get_today_plan calls fail (degraded mode), fall back to open plan."""
    svc_stub = _MockDailyPlanServiceWithSequence(
        get_sequence=[RuntimeError("fail1"), RuntimeError("fail2")],
        record_id="recPending001",
    )
    svc = _build_service_with_daily_plan(svc_stub)
    result = svc.handle_telegram_entry_handoff(_plan_demo_handoff(build_bootstrap_context()))

    assert result.decision == "plan_demo"
    markup = result.formatter_payload.response_reply_markup
    assert markup is not None
    buttons = [btn for row in markup["inline_keyboard"] for btn in row]
    # Must show the four open-plan buttons (not the change-selection button).
    assert any("execute_today" in btn["callback_data"] for btn in buttons)
    assert not any(btn["text"] == "🔁 Auswahl ändern" for btn in buttons)


def test_open_plan_shown_when_first_get_returns_none_and_second_returns_pending() -> None:
    """If both get_today_plan calls return a pending row, open plan must be shown."""
    pending_snapshot = TodayPlanSnapshot(
        record_id="recPending003",
        decision="pending",
        plan_type="post",
        platform="tiktok",
    )
    svc_stub = _MockDailyPlanServiceWithSequence(
        get_sequence=[None, pending_snapshot],
        record_id="recPending003",
    )
    svc = _build_service_with_daily_plan(svc_stub)
    result = svc.handle_telegram_entry_handoff(_plan_demo_handoff(build_bootstrap_context()))

    assert result.decision == "plan_demo"
    buttons = [btn for row in result.formatter_payload.response_reply_markup["inline_keyboard"] for btn in row]
    assert any("execute_today" in btn["callback_data"] for btn in buttons)


def test_no_authority_recheck_when_first_get_returns_pending() -> None:
    """When get_today_plan returns a pending snapshot on the first call, the
    authority re-check must NOT run (we trust the pending state, avoid extra call)."""
    pending_snapshot = TodayPlanSnapshot(
        record_id="recPending004",
        decision="pending",
        plan_type="post",
        platform="instagram_reel",
    )
    # Only one result: if a second call were made it would raise.
    svc_stub = _MockDailyPlanServiceWithSequence(
        get_sequence=[pending_snapshot, RuntimeError("should not be called")],
        record_id="recPending004",
    )
    svc = _build_service_with_daily_plan(svc_stub)
    result = svc.handle_telegram_entry_handoff(_plan_demo_handoff(build_bootstrap_context()))

    assert result.decision == "plan_demo"
    # Only one get_today_plan call (no re-check).
    assert len(svc_stub.get_calls) == 1
    # Open plan buttons shown.
    buttons = [btn for row in result.formatter_payload.response_reply_markup["inline_keyboard"] for btn in row]
    assert any("execute_today" in btn["callback_data"] for btn in buttons)
