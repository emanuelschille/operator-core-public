from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from operator_core.proactive.plan_reminder_store import PlanReminder, PlanReminderStore


def _make_reminder(
    key: str = "remind_15m:recXXX",
    fire_at: datetime | None = None,
    reminder_type: str = "remind_15m",
    record_id: str = "recXXX",
    platform: str = "tiktok",
) -> PlanReminder:
    if fire_at is None:
        fire_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)  # already due
    return PlanReminder(
        key=key,
        fire_at=fire_at,
        chat_id=12345,
        platform=platform,
        record_id=record_id,
        reminder_type=reminder_type,
        context_text="Test context",
    )


class TestPlanReminderStore:
    def test_schedule_and_due(self) -> None:
        store = PlanReminderStore()
        store.schedule(_make_reminder())
        due = store.due()
        assert len(due) == 1
        assert due[0].key == "remind_15m:recXXX"

    def test_due_removes_reminder(self) -> None:
        store = PlanReminderStore()
        store.schedule(_make_reminder())
        store.due()
        assert store.due() == []
        assert store.size() == 0

    def test_not_due_not_returned(self) -> None:
        store = PlanReminderStore()
        future = datetime.now(tz=timezone.utc) + timedelta(minutes=10)
        store.schedule(_make_reminder(fire_at=future))
        assert store.due() == []
        assert store.size() == 1

    def test_schedule_same_key_replaces(self) -> None:
        store = PlanReminderStore()
        store.schedule(_make_reminder(key="k1", platform="tiktok"))
        store.schedule(_make_reminder(key="k1", platform="instagram_reel"))
        due = store.due()
        assert len(due) == 1
        assert due[0].platform == "instagram_reel"

    def test_cancel_removes_reminder(self) -> None:
        store = PlanReminderStore()
        store.schedule(_make_reminder(key="k2"))
        store.cancel("k2")
        assert store.due() == []
        assert store.size() == 0

    def test_cancel_unknown_key_noop(self) -> None:
        store = PlanReminderStore()
        store.cancel("nonexistent")  # must not raise
        assert store.size() == 0

    def test_multiple_reminders_independent(self) -> None:
        store = PlanReminderStore()
        future = datetime.now(tz=timezone.utc) + timedelta(minutes=10)
        store.schedule(_make_reminder(key="k_due"))
        store.schedule(_make_reminder(key="k_future", fire_at=future))
        due = store.due()
        assert len(due) == 1
        assert due[0].key == "k_due"
        assert store.size() == 1  # k_future still in store

    def test_size_reflects_store_state(self) -> None:
        store = PlanReminderStore()
        assert store.size() == 0
        store.schedule(_make_reminder(key="a"))
        store.schedule(_make_reminder(key="b"))
        assert store.size() == 2
        store.cancel("a")
        assert store.size() == 1

    def test_persistent_store_reloads_scheduled_reminders(self, tmp_path: Path) -> None:
        file_path = tmp_path / "plan_reminders.json"
        original = PlanReminderStore(file_path=file_path)
        reminder = _make_reminder(
            key="analytics_3d:recPersist",
            fire_at=datetime.now(tz=timezone.utc) + timedelta(days=3),
            reminder_type="analytics_3d",
        )
        original.schedule(reminder)

        reloaded = PlanReminderStore(file_path=file_path)

        assert reloaded.size() == 1
        assert reloaded._store["analytics_3d:recPersist"].reminder_type == "analytics_3d"
