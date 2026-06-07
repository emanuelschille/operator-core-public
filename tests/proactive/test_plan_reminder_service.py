from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from operator_core.proactive.plan_reminder_service import PlanReminderService
from operator_core.proactive.plan_reminder_store import PlanReminder, PlanReminderStore


def _make_reminder(
    key: str = "remind_15m:recXXX",
    reminder_type: str = "remind_15m",
    record_id: str = "recXXX",
    platform: str = "tiktok",
    context_text: str = "Status: offen\nSerie/Thema: Backen",
) -> PlanReminder:
    return PlanReminder(
        key=key,
        fire_at=datetime.now(tz=timezone.utc) - timedelta(seconds=1),
        chat_id=12345,
        platform=platform,
        record_id=record_id,
        reminder_type=reminder_type,
        context_text=context_text,
        analytics_record_id="recAnalytics1" if reminder_type == "analytics_3d" else "",
        analytics_table_id="tblAnalytics1" if reminder_type == "analytics_3d" else "",
    )


def _make_settings(allowed_user_ids: tuple[str, ...] = ("99001",)) -> MagicMock:
    settings = MagicMock()
    settings.telegram.allowed_user_ids = allowed_user_ids
    return settings


def _make_snapshot(
    *,
    posted_at_local: str = "",
    decision: str = "pending",
    platform: str = "tiktok",
    serie_thema: str = "Backen",
    title_raw: str = "Plätzchen",
    bereit: str = "",
    platform_record_id: str = "recAnalytics1",
    platform_table_id: str = "tblAnalytics1",
) -> MagicMock:
    snap = MagicMock()
    snap.posted_at_local = posted_at_local
    snap.decision = decision
    snap.platform = platform
    snap.serie_thema = serie_thema
    snap.title_raw = title_raw
    snap.hook = ""
    snap.cta = ""
    snap.caption = ""
    snap.format_typ = ""
    snap.bereit = bereit
    snap.platform_record_id = platform_record_id
    snap.platform_table_id = platform_table_id
    return snap


# ---------------------------------------------------------------------------
# 15-minute reminder tests
# ---------------------------------------------------------------------------


def test_remind_15m_sends_to_allowed_users() -> None:
    store = PlanReminderStore()
    store.schedule(_make_reminder())

    telegram_svc = MagicMock()
    daily_plan_svc = MagicMock()
    daily_plan_svc.get_plan_record.return_value = _make_snapshot()

    svc = PlanReminderService(
        store=store,
        telegram_svc=telegram_svc,
        settings=_make_settings(("99001",)),
        daily_plan_service=daily_plan_svc,
        project_key="everydayengel",
    )
    svc._check_due()

    telegram_svc.send_message.assert_called_once()
    call_kwargs = telegram_svc.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == 99001
    assert call_kwargs["text"].startswith("📋 Tagesplan · TikTok")
    assert "⏰ Erinnerung nach 15 Minuten." in call_kwargs["text"]
    assert call_kwargs["reply_markup"] is not None  # inline keyboard attached
    reminder_buttons = [
        button["text"]
        for row in call_kwargs["reply_markup"]["inline_keyboard"]
        for button in row
    ]
    assert "⏰ In 15 Min. erinnern" in reminder_buttons


def test_remind_15m_suppressed_when_already_posted() -> None:
    store = PlanReminderStore()
    store.schedule(_make_reminder())

    telegram_svc = MagicMock()
    daily_plan_svc = MagicMock()
    daily_plan_svc.get_plan_record.return_value = _make_snapshot(
        posted_at_local="2026-04-12 20:00"
    )

    svc = PlanReminderService(
        store=store,
        telegram_svc=telegram_svc,
        settings=_make_settings(),
        daily_plan_service=daily_plan_svc,
    )
    svc._check_due()

    telegram_svc.send_message.assert_not_called()


def test_remind_15m_suppressed_when_skipped() -> None:
    store = PlanReminderStore()
    store.schedule(_make_reminder())

    telegram_svc = MagicMock()
    daily_plan_svc = MagicMock()
    daily_plan_svc.get_plan_record.return_value = _make_snapshot(decision="skip")

    svc = PlanReminderService(
        store=store,
        telegram_svc=telegram_svc,
        settings=_make_settings(),
        daily_plan_service=daily_plan_svc,
    )
    svc._check_due()

    telegram_svc.send_message.assert_not_called()


def test_remind_15m_fires_when_snapshot_fetch_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the obsolescence check itself fails, fire the reminder anyway."""
    store = PlanReminderStore()
    store.schedule(_make_reminder())

    telegram_svc = MagicMock()
    daily_plan_svc = MagicMock()
    daily_plan_svc.get_plan_record.side_effect = RuntimeError("airtable down")

    svc = PlanReminderService(
        store=store,
        telegram_svc=telegram_svc,
        settings=_make_settings(),
        daily_plan_service=daily_plan_svc,
    )
    with caplog.at_level(logging.WARNING, logger="operator_core.proactive.plan_reminder_service"):
        svc._check_due()

    telegram_svc.send_message.assert_called_once()
    assert any("snapshot refresh failed" in r.message for r in caplog.records)


def test_remind_15m_uses_fallback_context_text_when_no_daily_plan_svc() -> None:
    """Without DailyPlanService, reminder uses stored context_text."""
    store = PlanReminderStore()
    store.schedule(_make_reminder(context_text="Serie/Thema: Fallback"))

    telegram_svc = MagicMock()

    svc = PlanReminderService(
        store=store,
        telegram_svc=telegram_svc,
        settings=_make_settings(),
        daily_plan_service=None,
    )
    svc._check_due()

    text = telegram_svc.send_message.call_args.kwargs["text"]
    assert "Fallback" in text


# ---------------------------------------------------------------------------
# 3-day analytics reminder tests
# ---------------------------------------------------------------------------


def test_analytics_3d_sends_when_snapshot_still_matches_uploaded_record() -> None:
    store = PlanReminderStore()
    store.schedule(
        _make_reminder(
            key="analytics_3d:recXXX",
            reminder_type="analytics_3d",
            context_text="📊 Analytics-Erinnerung · TikTok\n\nGepostet: 2026-04-12 20:00",
        )
    )

    telegram_svc = MagicMock()
    daily_plan_svc = MagicMock()
    daily_plan_svc.get_plan_record.return_value = _make_snapshot(
        posted_at_local="2026-04-12 20:00"
    )

    svc = PlanReminderService(
        store=store,
        telegram_svc=telegram_svc,
        settings=_make_settings(),
        daily_plan_service=daily_plan_svc,
        project_key="everydayengel",
    )
    svc._check_due()

    telegram_svc.send_message.assert_called_once()
    call_kwargs = telegram_svc.send_message.call_args.kwargs
    assert "📊" in call_kwargs["text"]
    assert call_kwargs["reply_markup"] is None  # no keyboard for analytics reminder
    daily_plan_svc.get_plan_record.assert_called_once()


def test_analytics_3d_suppressed_when_link_changed() -> None:
    store = PlanReminderStore()
    store.schedule(
        _make_reminder(
            key="analytics_3d:recXXX",
            reminder_type="analytics_3d",
            context_text="📊 Analytics-Erinnerung · TikTok\n\nGepostet: 2026-04-12 20:00",
        )
    )

    telegram_svc = MagicMock()
    daily_plan_svc = MagicMock()
    daily_plan_svc.get_plan_record.return_value = _make_snapshot(
        posted_at_local="2026-04-12 20:00",
        platform_record_id="recAnalyticsNEW",
    )

    svc = PlanReminderService(
        store=store,
        telegram_svc=telegram_svc,
        settings=_make_settings(),
        daily_plan_service=daily_plan_svc,
        project_key="everydayengel",
    )
    svc._check_due()

    telegram_svc.send_message.assert_not_called()


def test_analytics_3d_suppressed_when_link_missing() -> None:
    store = PlanReminderStore()
    store.schedule(
        _make_reminder(
            key="analytics_3d:recXXX",
            reminder_type="analytics_3d",
            context_text="📊 Analytics-Erinnerung · TikTok\n\nGepostet: 2026-04-12 20:00",
        )
    )

    telegram_svc = MagicMock()
    daily_plan_svc = MagicMock()
    daily_plan_svc.get_plan_record.return_value = _make_snapshot(
        posted_at_local="2026-04-12 20:00",
        platform_record_id="",
    )

    svc = PlanReminderService(
        store=store,
        telegram_svc=telegram_svc,
        settings=_make_settings(),
        daily_plan_service=daily_plan_svc,
        project_key="everydayengel",
    )
    svc._check_due()

    telegram_svc.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# No due reminders
# ---------------------------------------------------------------------------


def test_no_due_reminders_sends_nothing() -> None:
    store = PlanReminderStore()
    future = datetime.now(tz=timezone.utc) + timedelta(minutes=10)
    store.schedule(
        PlanReminder(
            key="remind_15m:recFUTURE",
            fire_at=future,
            chat_id=99001,
            platform="tiktok",
            record_id="recFUTURE",
            reminder_type="remind_15m",
            context_text="future",
        )
    )

    telegram_svc = MagicMock()
    svc = PlanReminderService(
        store=store,
        telegram_svc=telegram_svc,
        settings=_make_settings(),
    )
    svc._check_due()

    telegram_svc.send_message.assert_not_called()
    assert store.size() == 1  # still in store
