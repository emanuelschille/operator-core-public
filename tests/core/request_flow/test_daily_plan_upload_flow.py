from pathlib import Path

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
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.integrations.daily_plan_service import TodayPlanSnapshot
from operator_core.interfaces.telegram.entry_flow import build_telegram_entry_handoff
from operator_core.proactive.plan_reminder_store import PlanReminderStore


def _bootstrap() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="INFO",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(enabled=False, bot_token="", allowed_user_ids=(), allowed_chat_ids=()),
        airtable=AirtableSettings(
            enabled=False,
            api_key="",
            project_base_ids={"everydayengel": "", "analytics": ""},
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


class _DailyPlanStub:
    def __init__(self) -> None:
        self.rows = {
            "rec-tiktok": TodayPlanSnapshot(
                record_id="rec-tiktok",
                decision="pending",
                platform="tiktok",
                candidate_record_id="rec-draft-1",
                serie_thema="Wintergebäck",
                title_raw="Kekse zur Weihnachtszeit",
                hook="Hook",
                cta="CTA",
                caption="Caption",
                format_typ="Reel",
                bereit="bereit",
            ),
            "rec-youtube": TodayPlanSnapshot(
                record_id="rec-youtube",
                decision="pending",
                platform="youtube_short",
                candidate_record_id="rec-draft-youtube",
                serie_thema="Ruhiger Morgen",
                title_raw="Morgenruhe",
                hook="Hook",
                cta="CTA",
                caption="Caption",
                format_typ="YouTube Short",
                bereit="bereit",
            ),
        }
        self.get_calls: list[dict] = []
        self.set_posted_at_calls: list[dict] = []

    def get_plan_record(self, *, project_key: str, record_id: str) -> TodayPlanSnapshot:
        self.get_calls.append({"project_key": project_key, "record_id": record_id})
        return self.rows[record_id]

    def list_today_plans(self, *, project_key: str, date: str) -> tuple[TodayPlanSnapshot, ...]:
        return tuple(self.rows.values())

    def set_posted_at_local(self, *, project_key: str, record_id: str, posted_at_local: str) -> TodayPlanSnapshot:
        self.set_posted_at_calls.append(
            {"project_key": project_key, "record_id": record_id, "posted_at_local": posted_at_local}
        )
        current = self.rows[record_id]
        updated = TodayPlanSnapshot(**{**current.__dict__, "posted_at_local": posted_at_local})
        self.rows[record_id] = updated
        return updated

    def update_decision(self, **kwargs: object) -> None:
        return None

    def autofill_selection(self, **kwargs: object) -> TodayPlanSnapshot:
        return self.rows["rec-tiktok"]

    def clear_selection(self, **kwargs: object) -> TodayPlanSnapshot:
        return self.rows["rec-tiktok"]


class _UploadStub:
    def __init__(self, daily_plan_stub: _DailyPlanStub) -> None:
        self.daily_plan_stub = daily_plan_stub
        self.upload_calls: list[dict] = []
        self.posted_calls: list[dict] = []

    def build_default_posted_at_local(self, *, project_key: str, platform: str, date: str) -> str:
        return f"{date} {'20:30' if platform == 'youtube_short' else '20:00'}"

    def upload_plan_snapshot(self, *, project_key: str, snapshot: TodayPlanSnapshot, date: str):
        self.upload_calls.append({"project_key": project_key, "snapshot": snapshot, "date": date})
        updated = TodayPlanSnapshot(
            **{
                **snapshot.__dict__,
                "platform_record_id": "recAnalytics1",
                "platform_table_id": "tblTikTok",
            }
        )
        self.daily_plan_stub.rows[snapshot.record_id] = updated
        return type(
            "UploadResult",
            (),
            {
                "updated_snapshot": updated,
                "analytics_record_id": "recAnalytics1",
                "analytics_table_id": "tblYouTube" if snapshot.platform == "youtube_short" else "tblTikTok",
                "default_posted_at_local": self.build_default_posted_at_local(
                    project_key=project_key,
                    platform=snapshot.platform or "",
                    date=date,
                ),
            },
        )()

    def set_posted_at_local(self, *, project_key: str, snapshot: TodayPlanSnapshot, posted_at_local: str) -> TodayPlanSnapshot:
        self.posted_calls.append(
            {"project_key": project_key, "snapshot": snapshot, "posted_at_local": posted_at_local}
        )
        updated = TodayPlanSnapshot(**{**snapshot.__dict__, "posted_at_local": posted_at_local})
        self.daily_plan_stub.rows[snapshot.record_id] = updated
        return updated


def _service(
    *,
    plan_reminder_store: PlanReminderStore | None = None,
) -> tuple[RequestFlowService, _DailyPlanStub, _UploadStub]:
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    daily_plan = _DailyPlanStub()
    upload = _UploadStub(daily_plan)
    return (
        RequestFlowService(
            execution_service,
            daily_plan_service=daily_plan,  # type: ignore[arg-type]
            daily_plan_upload_service=upload,  # type: ignore[arg-type]
            plan_reminder_store=plan_reminder_store,
        ),
        daily_plan,
        upload,
    )


def _callback_handoff(callback_data: str) -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": 1001,
            "callback_query": {
                "id": "cbq-upload",
                "from": {"id": 4000, "username": "julia"},
                "data": callback_data,
                "message": {
                    "message_id": 2001,
                    "text": "📋 Tagesplan · TikTok",
                    "chat": {"id": 3000, "type": "private"},
                },
            },
        },
        _bootstrap(),
    )


def _message_handoff(text: str) -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": 1002,
            "message": {
                "message_id": 2002,
                "text": text,
                "chat": {"id": 3000, "type": "private"},
                "from": {"id": 4000, "username": "julia"},
            },
        },
        _bootstrap(),
    )


def test_upload_callback_triggers_upload_and_prompts_for_posted_time() -> None:
    service, _daily_plan, upload = _service()
    result = service.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:upload_airtable:rec-tiktok")
    )

    assert result.decision == "plan_demo_upload"
    assert len(upload.upload_calls) == 1
    assert result.formatter_payload.send_response is True
    assert "Wann wurde wirklich gepostet?" in result.formatter_payload.message_text
    assert result.formatter_payload.response_reply_markup["inline_keyboard"][0][0]["callback_data"] == (
        "plan_demo:posted_at_default:rec-tiktok"
    )
    assert "Airtable: hochgeladen" in (result.formatter_payload.edit_message_text or "")


def test_youtube_upload_callback_prompts_for_time_before_upload() -> None:
    service, _daily_plan, upload = _service()

    result = service.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:upload_airtable:rec-youtube")
    )

    assert result.decision == "plan_demo_upload"
    assert upload.upload_calls == []
    # YouTube shows a different prompt: upload happens only after time is confirmed
    assert "Upload wird nach Zeitbestätigung ausgeführt" in result.formatter_payload.message_text
    assert "Wann hast du gepostet?" in result.formatter_payload.message_text
    assert result.formatter_payload.response_reply_markup["inline_keyboard"][0][0]["callback_data"] == (
        "plan_demo:posted_at_default:rec-youtube"
    )
    assert result.formatter_payload.edit_message_id is None
    assert result.formatter_payload.edit_message_text is None


def test_posted_at_text_reply_uses_pending_capture_and_persists_time() -> None:
    service, _daily_plan, upload = _service()
    service.handle_telegram_entry_handoff(_callback_handoff("plan_demo:upload_airtable:rec-tiktok"))

    result = service.handle_telegram_entry_handoff(_message_handoff("19:45"))

    assert result.decision == "plan_demo_posted_at"
    assert len(upload.posted_calls) == 1
    assert upload.posted_calls[0]["posted_at_local"].endswith("19:45")
    assert "19:45" in result.formatter_payload.message_text


def test_posted_at_default_callback_uses_default_time() -> None:
    service, _daily_plan, upload = _service()
    service.handle_telegram_entry_handoff(_callback_handoff("plan_demo:upload_airtable:rec-tiktok"))

    result = service.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:posted_at_default:rec-tiktok")
    )

    assert result.decision == "plan_demo_posted_at"
    assert len(upload.posted_calls) == 1
    assert upload.posted_calls[0]["posted_at_local"].endswith("20:00")
    assert result.formatter_payload.edit_message_text is not None
    assert "Posted at local:" in result.formatter_payload.edit_message_text


def test_youtube_posted_at_default_callback_uploads_only_after_time_confirmation() -> None:
    service, _daily_plan, upload = _service()
    service.handle_telegram_entry_handoff(_callback_handoff("plan_demo:upload_airtable:rec-youtube"))

    result = service.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:posted_at_default:rec-youtube")
    )

    assert result.decision == "plan_demo_posted_at"
    assert len(upload.upload_calls) == 1
    assert upload.upload_calls[0]["snapshot"].record_id == "rec-youtube"
    assert len(upload.posted_calls) == 1
    assert upload.posted_calls[0]["posted_at_local"].endswith("20:30")
    assert "Posted at local:" in (result.formatter_payload.edit_message_text or "")


def test_posted_at_confirmation_schedules_analytics_3d_reminder_for_uploaded_platform() -> None:
    store = PlanReminderStore()
    service, _daily_plan, upload = _service(plan_reminder_store=store)
    result = service.handle_telegram_entry_handoff(_callback_handoff("plan_demo:upload_airtable:rec-tiktok"))

    assert result.decision == "plan_demo_upload"
    assert store.size() == 1
    reminder = next(iter(store._store.values()))
    assert reminder.key == "analytics_3d:rec-tiktok"
    assert reminder.platform == "tiktok"
    assert reminder.record_id == "rec-tiktok"
    assert reminder.reminder_type == "analytics_3d"
    assert reminder.analytics_record_id == "recAnalytics1"
    assert reminder.analytics_table_id == "tblTikTok"
    assert "Analytics-Erinnerung" in reminder.context_text


def test_youtube_posted_at_confirmation_schedules_analytics_3d_reminder_after_actual_upload() -> None:
    store = PlanReminderStore()
    service, _daily_plan, upload = _service(plan_reminder_store=store)
    service.handle_telegram_entry_handoff(_callback_handoff("plan_demo:upload_airtable:rec-youtube"))

    result = service.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:posted_at_default:rec-youtube")
    )

    assert result.decision == "plan_demo_posted_at"
    assert len(upload.upload_calls) == 1
    assert store.size() == 1
    reminder = next(iter(store._store.values()))
    assert reminder.key == "analytics_3d:rec-youtube"
    assert reminder.platform == "youtube_short"
