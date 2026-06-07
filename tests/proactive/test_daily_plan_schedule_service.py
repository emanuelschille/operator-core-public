from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from operator_core.core.request_flow.daily_plan_message_store import DailyPlanMessageStore
from operator_core.integrations.daily_plan_service import TodayPlanSnapshot
from operator_core.proactive.daily_plan_schedule_service import DailyPlanScheduleService
from operator_core.proactive.daily_plan_schedule_store import DailyPlanScheduleStore


class _DailyPlanStub:
    def __init__(self) -> None:
        self.upsert_calls: list[dict[str, object]] = []
        self.rows: dict[str, TodayPlanSnapshot] = {}

    def upsert_plan(
        self,
        *,
        project_key: str,
        date: str,
        plan_type: str,
        platform: str | None = None,
        candidate_record_id: str | None = None,
        candidate_count: int | None = None,
    ) -> str:
        self.upsert_calls.append(
            {
                "project_key": project_key,
                "date": date,
                "plan_type": plan_type,
                "platform": platform,
                "candidate_record_id": candidate_record_id,
                "candidate_count": candidate_count,
            }
        )
        assert platform is not None
        record_id = f"rec-{platform}"
        self.rows[platform] = TodayPlanSnapshot(
            record_id=record_id,
            decision="pending",
            platform=platform,
            candidate_record_id=candidate_record_id,
            candidate_count=candidate_count,
            title_raw=f"Title {platform}",
            cta=f"CTA {platform}",
        )
        return record_id

    def list_today_plans(self, *, project_key: str, date: str) -> tuple[TodayPlanSnapshot, ...]:
        return tuple(self.rows.values())


class _TelegramStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._message_id = 100

    def send_message(self, *, chat_id: int, text: str, reply_markup: dict | None = None, **_: object) -> dict:
        self._message_id += 1
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "message_id": self._message_id,
            }
        )
        return {"result": {"message_id": self._message_id}}


class _Settings:
    class telegram:
        allowed_user_ids = ("99001",)


def test_daily_plan_scheduler_sends_once_per_due_slot_and_registers_messages(tmp_path: Path) -> None:
    store = DailyPlanScheduleStore(file_path=tmp_path / "daily_plan_sent.json")
    message_store = DailyPlanMessageStore()
    daily_plan = _DailyPlanStub()
    telegram = _TelegramStub()
    service = DailyPlanScheduleService(
        daily_plan_service=daily_plan,  # type: ignore[arg-type]
        posting_recommender=None,
        telegram_svc=telegram,  # type: ignore[arg-type]
        settings=_Settings(),  # type: ignore[arg-type]
        schedule_store=store,
        daily_plan_message_store=message_store,
        project_key="everydayengel",
    )

    service._check_due(now_local=datetime(2026, 4, 13, 6, 30, tzinfo=ZoneInfo("Europe/Berlin")))

    assert len(telegram.calls) == 4
    assert all(call["reply_markup"] is not None for call in telegram.calls)
    assert message_store.get(chat_id=99001, message_id=int(telegram.calls[0]["message_id"])) == "rec-tiktok"

    service._check_due(now_local=datetime(2026, 4, 13, 6, 45, tzinfo=ZoneInfo("Europe/Berlin")))
    assert len(telegram.calls) == 4


def test_daily_plan_scheduler_sends_second_slot_once_and_store_survives_restart(tmp_path: Path) -> None:
    file_path = tmp_path / "daily_plan_sent.json"
    first_store = DailyPlanScheduleStore(file_path=file_path)
    message_store = DailyPlanMessageStore()
    daily_plan = _DailyPlanStub()
    telegram = _TelegramStub()
    service = DailyPlanScheduleService(
        daily_plan_service=daily_plan,  # type: ignore[arg-type]
        posting_recommender=None,
        telegram_svc=telegram,  # type: ignore[arg-type]
        settings=_Settings(),  # type: ignore[arg-type]
        schedule_store=first_store,
        daily_plan_message_store=message_store,
        project_key="everydayengel",
    )

    service._check_due(now_local=datetime(2026, 4, 13, 9, 0, tzinfo=ZoneInfo("Europe/Berlin")))
    assert len(telegram.calls) == 8

    restarted_store = DailyPlanScheduleStore(file_path=file_path)
    restarted_service = DailyPlanScheduleService(
        daily_plan_service=daily_plan,  # type: ignore[arg-type]
        posting_recommender=None,
        telegram_svc=telegram,  # type: ignore[arg-type]
        settings=_Settings(),  # type: ignore[arg-type]
        schedule_store=restarted_store,
        daily_plan_message_store=message_store,
        project_key="everydayengel",
    )
    restarted_service._check_due(now_local=datetime(2026, 4, 13, 9, 5, tzinfo=ZoneInfo("Europe/Berlin")))

    assert len(telegram.calls) == 8
