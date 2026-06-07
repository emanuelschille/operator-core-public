"""
Tests for Phase 2.3-A ProactiveCheckerService, triggers, and suppression.

Covers:
  - PostingGapTrigger: fires when gap > threshold
  - PostingGapTrigger: silent when last post is recent
  - PostingGapTrigger: silent when no analytics records exist
  - PostingGapTrigger: silent when Airtable read fails
  - BacklogReadinessTrigger: fires when ready ideas exist and no recent draft
  - BacklogReadinessTrigger: silent when no ready ideas
  - BacklogReadinessTrigger: silent when recent draft exists
  - BacklogReadinessTrigger: silent when Airtable read fails
  - SuppressionStore: blocks send within cooldown
  - SuppressionStore: allows send after cooldown
  - ProactiveCheckerService: sends message when both triggers fire
  - ProactiveCheckerService: no send when neither trigger fires
  - ProactiveCheckerService: suppression prevents duplicate send
  - ProactiveCheckerService: Telegram send failure does not update suppression
  - _seconds_until_target: returns positive value
  - _parse_created_time: handles ISO 8601 Z suffix
  - _parse_created_time: returns None for invalid input
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from operator_core.config import (
    AppSettings,
    AirtableSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.integrations.airtable_service import (
    AirtableRecord,
    AirtableRecordList,
    AirtableServiceError,
)
from operator_core.integrations.telegram_service import TelegramServiceError
from operator_core.integrations.analytics_service import AnalyticsContext
from operator_core.integrations.platform_signal_service import PlatformContext
from operator_core.proactive.checker import (
    AnalyticsGapTrigger,
    BacklogReadinessTrigger,
    PostingGapTrigger,
    ProactiveCheckerService,
    SuppressionStore,
    TriggerResult,
    _parse_created_time,
    _seconds_until_target,
)
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(allowed_user_ids: tuple[str, ...] = ("123456",)) -> Settings:
    return Settings(
        app=AppSettings(
            env="test",
            log_level="WARNING",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(
            enabled=True,
            bot_token="test-token",
            allowed_user_ids=allowed_user_ids,
            allowed_chat_ids=(),
        ),
        airtable=AirtableSettings(
            enabled=True,
            api_key="pat-test",
            project_base_ids={
                "everydayengel": "appTest",
                "analytics": "appAnalytics",
            },
        ),
        openai=OpenAISettings(
            enabled=False,
            api_key="",
            model="",
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )


def _ts(days_ago: int) -> str:
    """Return an Airtable-style ISO 8601 UTC timestamp for N days ago."""
    dt = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _record(created_time: str, fields: dict[str, Any] | None = None) -> AirtableRecord:
    return AirtableRecord(
        record_id="recTest",
        fields=fields or {},
        created_time=created_time,
    )


def _record_list(*records: AirtableRecord) -> AirtableRecordList:
    return AirtableRecordList(records=tuple(records))


# ---------------------------------------------------------------------------
# _parse_created_time
# ---------------------------------------------------------------------------

def test_parse_created_time_z_suffix():
    dt = _parse_created_time("2026-04-01T14:30:00.000Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_parse_created_time_empty_returns_none():
    assert _parse_created_time("") is None


def test_parse_created_time_invalid_returns_none():
    assert _parse_created_time("not-a-date") is None


# ---------------------------------------------------------------------------
# _seconds_until_target
# ---------------------------------------------------------------------------

def test_seconds_until_target_positive():
    tz = ZoneInfo("Europe/Berlin")
    seconds = _seconds_until_target(hour=9, tz=tz)
    assert seconds > 0
    assert seconds <= 86400


# ---------------------------------------------------------------------------
# SuppressionStore
# ---------------------------------------------------------------------------

def test_suppression_not_suppressed_initially():
    store = SuppressionStore(cooldown_hours=24)
    assert store.is_suppressed("nudge") is False


def test_suppression_active_after_record():
    store = SuppressionStore(cooldown_hours=24)
    store.record_sent("nudge")
    assert store.is_suppressed("nudge") is True


def test_suppression_expired_after_cooldown():
    store = SuppressionStore(cooldown_hours=24)
    past = datetime.now(tz=timezone.utc) - timedelta(hours=25)
    store._last_sent["nudge"] = past
    assert store.is_suppressed("nudge") is False


# ---------------------------------------------------------------------------
# PostingGapTrigger
# ---------------------------------------------------------------------------

def test_posting_gap_trigger_fires_when_gap_exceeded():
    airtable = MagicMock()
    airtable.list_records.return_value = _record_list(_record(_ts(3)))
    trigger = PostingGapTrigger(gap_days=2)
    result = trigger.evaluate(airtable)
    assert result.fired is True
    assert "3 Tagen" in result.message


def test_posting_gap_trigger_singular_day_wording():
    airtable = MagicMock()
    airtable.list_records.return_value = _record_list(_record(_ts(2)))
    trigger = PostingGapTrigger(gap_days=0)
    result = trigger.evaluate(airtable)
    assert result.fired is True
    assert "2 Tagen" in result.message

def test_posting_gap_trigger_one_day_wording():
    airtable = MagicMock()
    airtable.list_records.return_value = _record_list(_record(_ts(1)))
    trigger = PostingGapTrigger(gap_days=0)
    result = trigger.evaluate(airtable)
    assert result.fired is True
    assert "1 Tag" in result.message
    assert "Tagen" not in result.message


def test_posting_gap_trigger_silent_when_recent():
    airtable = MagicMock()
    airtable.list_records.return_value = _record_list(_record(_ts(1)))
    trigger = PostingGapTrigger(gap_days=2)
    result = trigger.evaluate(airtable)
    assert result.fired is False


def test_posting_gap_trigger_silent_when_no_records():
    airtable = MagicMock()
    airtable.list_records.return_value = _record_list()
    result = PostingGapTrigger().evaluate(airtable)
    assert result.fired is False


def test_posting_gap_trigger_silent_on_airtable_error():
    airtable = MagicMock()
    airtable.list_records.side_effect = AirtableServiceError("connection failed")
    result = PostingGapTrigger().evaluate(airtable)
    assert result.fired is False


# ---------------------------------------------------------------------------
# BacklogReadinessTrigger
# ---------------------------------------------------------------------------

def _make_backlog_airtable(
    ready_count: int = 1,
    recent_draft_count: int = 0,
    draft_days_ago: int = 1,
) -> MagicMock:
    airtable = MagicMock()

    ready_records = [_record(_ts(5), {"stage": "ready_to_produce"}) for _ in range(ready_count)]
    airtable.find_records.return_value = _record_list(*ready_records)

    draft_records = [_record(_ts(draft_days_ago), {"stage": "drafted"}) for _ in range(recent_draft_count)]
    airtable.list_records.return_value = _record_list(*draft_records)

    return airtable


def test_backlog_trigger_fires_with_ready_ideas_no_recent_draft():
    airtable = _make_backlog_airtable(ready_count=2, recent_draft_count=0)
    result = BacklogReadinessTrigger(recent_draft_days=3).evaluate(airtable)
    assert result.fired is True
    assert "2" in result.message


def test_backlog_trigger_silent_when_no_ready_ideas():
    airtable = _make_backlog_airtable(ready_count=0)
    result = BacklogReadinessTrigger().evaluate(airtable)
    assert result.fired is False


def test_backlog_trigger_silent_when_recent_draft_exists():
    airtable = _make_backlog_airtable(ready_count=1, recent_draft_count=1, draft_days_ago=1)
    result = BacklogReadinessTrigger(recent_draft_days=3).evaluate(airtable)
    assert result.fired is False


def test_backlog_trigger_silent_on_airtable_error():
    airtable = MagicMock()
    airtable.find_records.side_effect = AirtableServiceError("timeout")
    result = BacklogReadinessTrigger().evaluate(airtable)
    assert result.fired is False


# ---------------------------------------------------------------------------
# ProactiveCheckerService
# ---------------------------------------------------------------------------

def _make_checker(
    gap_fired: bool = True,
    backlog_fired: bool = True,
    suppressed: bool = False,
    send_raises: bool = False,
) -> tuple[ProactiveCheckerService, MagicMock, SuppressionStore]:
    airtable = MagicMock()
    telegram = MagicMock()
    settings = _make_settings()

    if send_raises:
        telegram.send_message.side_effect = TelegramServiceError("network error")

    gap_trigger = MagicMock()
    gap_trigger.evaluate.return_value = TriggerResult(
        fired=gap_fired, message="Du hast seit 3 Tagen nichts gepostet." if gap_fired else ""
    )

    backlog_trigger = MagicMock()
    backlog_trigger.evaluate.return_value = TriggerResult(
        fired=backlog_fired, message="Du hast 2 fertige Ideen im Backlog." if backlog_fired else ""
    )

    store = SuppressionStore()
    if suppressed:
        store.record_sent("daily_nudge")

    checker = ProactiveCheckerService(
        airtable_svc=airtable,
        telegram_svc=telegram,
        settings=settings,
        posting_gap_trigger=gap_trigger,
        backlog_readiness_trigger=backlog_trigger,
        suppression_store=store,
    )
    return checker, telegram, store


def test_checker_sends_when_both_triggers_fire():
    checker, telegram, store = _make_checker(gap_fired=True, backlog_fired=True)
    checker._run_check()
    telegram.send_message.assert_called_once()
    args = telegram.send_message.call_args
    assert "Guten Morgen" in args.kwargs["text"]
    assert store.is_suppressed("daily_nudge") is True


def test_checker_sends_when_only_gap_fires():
    checker, telegram, store = _make_checker(gap_fired=True, backlog_fired=False)
    checker._run_check()
    telegram.send_message.assert_called_once()


def test_checker_sends_when_only_backlog_fires():
    checker, telegram, store = _make_checker(gap_fired=False, backlog_fired=True)
    checker._run_check()
    telegram.send_message.assert_called_once()


def test_checker_no_send_when_no_triggers_fire():
    checker, telegram, _ = _make_checker(gap_fired=False, backlog_fired=False)
    checker._run_check()
    telegram.send_message.assert_not_called()


def test_checker_suppressed_no_send():
    checker, telegram, _ = _make_checker(suppressed=True)
    checker._run_check()
    telegram.send_message.assert_not_called()


def test_checker_send_failure_does_not_update_suppression():
    checker, telegram, store = _make_checker(gap_fired=True, send_raises=True)
    checker._run_check()
    assert store.is_suppressed("daily_nudge") is False


def test_checker_stops_on_stop_event():
    checker, telegram, _ = _make_checker(gap_fired=False, backlog_fired=False)
    stop_event = Event()
    stop_event.set()

    with patch.object(checker, "_run_check") as mock_check:
        checker.run_until_stopped(stop_event)

    mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# AnalyticsGapTrigger
# ---------------------------------------------------------------------------

def _make_analytics_ctx(gap: str = "", cta_count: int = 5) -> AnalyticsContext:
    return AnalyticsContext(
        hook_examples=("Hook A", "Hook B") if cta_count >= 2 else (),
        dominant_cta="Community Frage" if cta_count > 0 else "",
        gap=gap,
        cta_count=cta_count,
    )


def _make_analytics_airtable(gap: str = "", cta_count: int = 5) -> MagicMock:
    """Return an airtable mock whose AnalyticsLoader.load_recent() yields the given context."""
    airtable = MagicMock()
    # list_records is what AnalyticsLoader calls internally
    fields = [{"hook_kurz": f"Hook {i}", "cta_typ": "Community Frage"} for i in range(cta_count)]
    from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList
    records = tuple(
        AirtableRecord(record_id=f"rec{i}", fields=f, created_time=_ts(i + 1))
        for i, f in enumerate(fields)
    )
    airtable.list_records.return_value = AirtableRecordList(records=records)
    return airtable


def test_analytics_gap_trigger_fires_when_gap_exists():
    """Fires when gap is non-empty and sample size >= min_cta_records."""
    airtable = _make_analytics_airtable(gap="", cta_count=5)
    # Patch AnalyticsLoader to return a context with a gap signal
    trigger = AnalyticsGapTrigger(min_cta_records=3)
    ctx = _make_analytics_ctx(gap="noch keine Serie oder Produkt-CTA – jetzt gut einführbar", cta_count=5)
    with patch("operator_core.proactive.checker.AnalyticsLoader") as mock_loader_cls:
        mock_loader_cls.return_value.load_recent.return_value = ctx
        result = trigger.evaluate(airtable)
    assert result.fired is True
    assert "denselben CTA-Typ" in result.message
    assert "/idea" in result.message


def test_analytics_gap_trigger_silent_when_gap_empty():
    """Silent when all CTAs are diverse (gap == '')."""
    trigger = AnalyticsGapTrigger(min_cta_records=3)
    ctx = _make_analytics_ctx(gap="", cta_count=5)
    with patch("operator_core.proactive.checker.AnalyticsLoader") as mock_loader_cls:
        mock_loader_cls.return_value.load_recent.return_value = ctx
        result = trigger.evaluate(MagicMock())
    assert result.fired is False


def test_analytics_gap_trigger_silent_when_below_min_sample():
    """Silent when fewer than min_cta_records CTA-bearing records exist."""
    trigger = AnalyticsGapTrigger(min_cta_records=3)
    ctx = _make_analytics_ctx(gap="noch keine Serie", cta_count=2)
    with patch("operator_core.proactive.checker.AnalyticsLoader") as mock_loader_cls:
        mock_loader_cls.return_value.load_recent.return_value = ctx
        result = trigger.evaluate(MagicMock())
    assert result.fired is False


def test_analytics_gap_trigger_silent_when_no_records():
    """Silent when analytics context is empty."""
    trigger = AnalyticsGapTrigger(min_cta_records=3)
    ctx = _make_analytics_ctx(gap="", cta_count=0)
    with patch("operator_core.proactive.checker.AnalyticsLoader") as mock_loader_cls:
        mock_loader_cls.return_value.load_recent.return_value = ctx
        result = trigger.evaluate(MagicMock())
    assert result.fired is False


def test_analytics_gap_trigger_silent_on_error():
    """Silent when AnalyticsLoader raises."""
    trigger = AnalyticsGapTrigger()
    with patch("operator_core.proactive.checker.AnalyticsLoader") as mock_loader_cls:
        mock_loader_cls.return_value.load_recent.side_effect = Exception("network error")
        result = trigger.evaluate(MagicMock())
    assert result.fired is False


def _platform_ctx(platform_key: str, post_count: int, gap: str = "") -> PlatformContext:
    return PlatformContext(
        platform_key=platform_key,
        table_id=f"tbl_{platform_key}",
        post_count=post_count,
        dominant_cta="",
        gap=gap,
        hook_examples=(),
    )


def _make_platform_loader(contexts: dict[str, PlatformContext]) -> MagicMock:
    loader = MagicMock()
    loader.load_all.return_value = contexts
    return loader


def test_analytics_gap_trigger_names_platform_when_platform_gap_found():
    trigger = AnalyticsGapTrigger(
        min_cta_records=3,
        platform_signal_loader=_make_platform_loader({
            "tiktok": _platform_ctx(
                "tiktok",
                post_count=5,
                gap="noch keine Serie oder Produkt-CTA – jetzt gut einführbar",
            ),
        }),
    )

    result = trigger.evaluate(MagicMock())

    assert result.fired is True
    assert "TikTok" in result.message
    assert "aus 5 Posts" in result.message


def test_analytics_gap_trigger_falls_back_to_global_when_platform_contexts_missing():
    trigger = AnalyticsGapTrigger(
        min_cta_records=3,
        platform_signal_loader=_make_platform_loader({}),
    )
    ctx = _make_analytics_ctx(gap="noch keine Serie", cta_count=3)
    with patch("operator_core.proactive.checker.AnalyticsLoader") as mock_loader_cls:
        mock_loader_cls.return_value.load_recent.return_value = ctx
        result = trigger.evaluate(MagicMock())

    assert result.fired is True
    assert "Deine letzten Posts" in result.message


# ---------------------------------------------------------------------------
# ProactiveCheckerService — analytics gap integration
# ---------------------------------------------------------------------------

def _make_checker_with_analytics(
    gap_fired: bool = False,
    backlog_fired: bool = False,
    analytics_fired: bool = True,
    analytics_suppressed: bool = False,
    send_raises: bool = False,
) -> tuple[ProactiveCheckerService, MagicMock, SuppressionStore, SuppressionStore]:
    airtable = MagicMock()
    telegram = MagicMock()
    settings = _make_settings()

    if send_raises:
        telegram.send_message.side_effect = TelegramServiceError("network error")

    gap_trigger = MagicMock()
    gap_trigger.evaluate.return_value = TriggerResult(fired=gap_fired, message="Gap message." if gap_fired else "")

    backlog_trigger = MagicMock()
    backlog_trigger.evaluate.return_value = TriggerResult(fired=backlog_fired, message="Backlog message." if backlog_fired else "")

    analytics_trigger = MagicMock()
    analytics_trigger.evaluate.return_value = TriggerResult(
        fired=analytics_fired,
        message="Deine letzten Posts haben alle denselben CTA-Typ. Schick /idea." if analytics_fired else "",
    )

    daily_store = SuppressionStore(cooldown_hours=24)
    analytics_store = SuppressionStore(cooldown_hours=72)
    if analytics_suppressed:
        analytics_store.record_sent("analytics_gap")

    checker = ProactiveCheckerService(
        airtable_svc=airtable,
        telegram_svc=telegram,
        settings=settings,
        posting_gap_trigger=gap_trigger,
        backlog_readiness_trigger=backlog_trigger,
        suppression_store=daily_store,
        analytics_gap_trigger=analytics_trigger,
        analytics_gap_suppression=analytics_store,
    )
    return checker, telegram, daily_store, analytics_store


def test_checker_sends_when_only_analytics_gap_fires():
    checker, telegram, daily_store, analytics_store = _make_checker_with_analytics(
        gap_fired=False, backlog_fired=False, analytics_fired=True
    )
    checker._run_check()
    telegram.send_message.assert_called_once()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "Guten Morgen" in text
    assert "/idea" in text
    # /draft closing must NOT appear when only analytics trigger fires
    assert "/draft" not in text
    assert daily_store.is_suppressed("daily_nudge") is True
    assert analytics_store.is_suppressed("analytics_gap") is True


def test_checker_analytics_gap_suppressed_no_extra_send():
    checker, telegram, _, analytics_store = _make_checker_with_analytics(
        gap_fired=False, backlog_fired=False, analytics_suppressed=True
    )
    checker._run_check()
    telegram.send_message.assert_not_called()


def test_checker_analytics_gap_suppression_independent_of_daily_nudge():
    """Analytics gap store and daily nudge store are independent: daily suppressed does not affect analytics store state."""
    checker, telegram, daily_store, analytics_store = _make_checker_with_analytics(
        gap_fired=False, backlog_fired=False, analytics_fired=True
    )
    # Pre-suppress daily nudge
    daily_store.record_sent("daily_nudge")
    checker._run_check()
    # Daily suppression blocks the send — analytics store must NOT be updated
    telegram.send_message.assert_not_called()
    assert analytics_store.is_suppressed("analytics_gap") is False


def test_checker_analytics_gap_send_failure_does_not_update_analytics_suppression():
    checker, telegram, daily_store, analytics_store = _make_checker_with_analytics(
        analytics_fired=True, send_raises=True
    )
    checker._run_check()
    assert analytics_store.is_suppressed("analytics_gap") is False
    assert daily_store.is_suppressed("daily_nudge") is False


def test_checker_draft_cta_included_when_timing_and_analytics_both_fire():
    """When gap/backlog AND analytics fire together, /draft closing is still included."""
    checker, telegram, _, _ = _make_checker_with_analytics(
        gap_fired=True, backlog_fired=False, analytics_fired=True
    )
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "/draft" in text
    assert "/idea" in text


# ---------------------------------------------------------------------------
# ProactiveCheckerService — PostingRecommender integration (Phase 2.5)
# ---------------------------------------------------------------------------

def _make_checker_with_recommender(
    gap_fired: bool = True,
    recommendation_message: str | None = None,
) -> tuple[ProactiveCheckerService, MagicMock]:
    """Build a checker with a mocked PostingRecommender."""
    from operator_core.proactive.posting_recommender import PostingRecommender, PostingRecommendation, PostingCandidate
    from datetime import timezone

    airtable = MagicMock()
    telegram = MagicMock()
    telegram.send_message.return_value = {"result": {"message_id": 1}}
    settings = _make_settings()

    gap_trigger = MagicMock()
    gap_trigger.evaluate.return_value = TriggerResult(
        fired=gap_fired,
        message="Du hast seit 3 Tagen nichts gepostet." if gap_fired else "",
    )

    backlog_trigger = MagicMock()
    backlog_trigger.evaluate.return_value = TriggerResult(fired=False, message="")

    recommender = MagicMock(spec=PostingRecommender)
    if recommendation_message is not None:
        candidate = PostingCandidate(
            record_id="recTEST",
            platform="tiktok",
            hook_preview="Test Hook",
            content_stage="ready_to_produce",
            content_format="",
            has_body=False,
            days_ready=5,
            days_since_last_post=4,
            posting_time="20:00",
        )
        recommender.recommend.return_value = PostingRecommendation(
            candidate=candidate,
            telegram_message=recommendation_message,
        )
    else:
        recommender.recommend.return_value = None

    checker = ProactiveCheckerService(
        airtable_svc=airtable,
        telegram_svc=telegram,
        settings=settings,
        posting_gap_trigger=gap_trigger,
        backlog_readiness_trigger=backlog_trigger,
        posting_recommender=recommender,
    )
    return checker, telegram


def test_checker_uses_recommendation_message_when_recommender_returns_one():
    """When gap fires and recommender returns a recommendation, that message is used."""
    rec_msg = "📋 Posting-Empfehlung\n\nTest"
    checker, telegram = _make_checker_with_recommender(gap_fired=True, recommendation_message=rec_msg)
    checker._run_check()
    telegram.send_message.assert_called_once()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "📋 Posting-Empfehlung" in text
    # Generic gap message must NOT appear
    assert "Du hast seit 3 Tagen nichts gepostet." not in text


def test_checker_falls_back_to_generic_gap_message_when_recommender_returns_none():
    """When gap fires and recommender returns None, generic gap message is used."""
    checker, telegram = _make_checker_with_recommender(gap_fired=True, recommendation_message=None)
    checker._run_check()
    telegram.send_message.assert_called_once()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "Du hast seit 3 Tagen nichts gepostet." in text
    assert "/draft" in text


def test_checker_no_draft_cta_when_recommendation_present():
    """/draft closing must not appear when a specific recommendation was delivered."""
    rec_msg = "📋 Posting-Empfehlung\n\n/confirm recTEST · /skip"
    checker, telegram = _make_checker_with_recommender(gap_fired=True, recommendation_message=rec_msg)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "/draft" not in text


# ---------------------------------------------------------------------------
# Phase 2.6 — pending-store active detection and bundle suppression
# ---------------------------------------------------------------------------

from operator_core.proactive.pending_store import ProactivePendingStore, PendingProposal


def _make_checker_with_stale(
    stale_fires: bool = True,
    active_pending: bool = False,
    expired_pending: bool = False,
    gap_fired: bool = False,
    backlog_fired: bool = False,
) -> tuple[ProactiveCheckerService, MagicMock, ProactivePendingStore]:
    airtable = MagicMock()
    telegram = MagicMock()
    telegram.send_message.return_value = {"result": {"message_id": 42}}
    settings = _make_settings()

    stale_trigger = MagicMock()
    stale_trigger.evaluate.return_value = TriggerResult(
        fired=stale_fires,
        message="Entwurf \"Test Draft\" liegt seit 20 Tagen unverändert. /confirm /reject",
        record_id="recSTALE1",
        display_text="Test Draft",
        days_stale=20,
        stale_count=1,
    ) if stale_fires else TriggerResult(fired=False)

    gap_trigger = MagicMock()
    gap_trigger.evaluate.return_value = TriggerResult(
        fired=gap_fired,
        message="Du hast seit 3 Tagen nichts gepostet." if gap_fired else "",
    )

    backlog_trigger = MagicMock()
    backlog_trigger.evaluate.return_value = TriggerResult(
        fired=backlog_fired,
        message="Du hast 2 fertige Ideen im Backlog." if backlog_fired else "",
    )

    pending_store = ProactivePendingStore()
    if active_pending:
        pending_store.put(PendingProposal(
            action_type="mark_stale",
            record_id="recACTIVE",
            display_text="Active Proposal",
            proposed_stage="stale",
            days_stale=25,
            sent_message_id=100,
            created_at=datetime.now(tz=timezone.utc),
        ))
    if expired_pending:
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=50)
        pending_store.put(PendingProposal(
            action_type="mark_stale",
            record_id="recEXPIRED",
            display_text="Expired",
            proposed_stage="stale",
            days_stale=30,
            sent_message_id=200,
            created_at=old_time,
        ))

    stale_suppression = SuppressionStore(cooldown_hours=168)

    checker = ProactiveCheckerService(
        airtable_svc=airtable,
        telegram_svc=telegram,
        settings=settings,
        posting_gap_trigger=gap_trigger,
        backlog_readiness_trigger=backlog_trigger,
        stale_draft_trigger=stale_trigger,
        stale_draft_suppression=stale_suppression,
        pending_store=pending_store,
    )
    return checker, telegram, pending_store


def test_bundle_suppressed_when_active_proposal_exists():
    """Daily bundle must not send when an unresolved active proposal is in the store."""
    checker, telegram, _ = _make_checker_with_stale(
        stale_fires=False, active_pending=True, gap_fired=True, backlog_fired=True
    )
    checker._run_check()
    telegram.send_message.assert_not_called()


def test_bundle_not_suppressed_when_only_expired_proposals_exist():
    """Bundle sends normally when pending store has only expired entries."""
    checker, telegram, _ = _make_checker_with_stale(
        stale_fires=False, expired_pending=True, gap_fired=True, backlog_fired=False
    )
    checker._run_check()
    telegram.send_message.assert_called_once()


def test_stale_proposal_skipped_when_active_proposal_exists():
    """No new stale proposal sent when an active (non-expired) proposal is already pending."""
    checker, telegram, _ = _make_checker_with_stale(
        stale_fires=True, active_pending=True, gap_fired=False, backlog_fired=False
    )
    checker._run_stale_draft_check()
    telegram.send_message.assert_not_called()


def test_stale_proposal_allowed_when_only_expired_in_store():
    """Stale proposal is DISABLED in Phase 2.8."""
    checker, telegram, _ = _make_checker_with_stale(
        stale_fires=True, expired_pending=True, gap_fired=False, backlog_fired=False
    )
    checker._run_stale_draft_check()
    telegram.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 2.6 — category markers in bundle messages
# ---------------------------------------------------------------------------

def test_bundle_backlog_message_has_recommendation_marker():
    """Backlog trigger message is prefixed with 💡 in the bundle."""
    checker, telegram, _ = _make_checker_with_stale(
        stale_fires=False, gap_fired=False, backlog_fired=True
    )
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "💡 Du hast 2 fertige Ideen im Backlog." in text


def test_bundle_gap_fallback_has_informational_marker():
    """Gap fallback message (no recommender) is prefixed with ℹ️ in the bundle."""
    checker, telegram, _ = _make_checker_with_stale(
        stale_fires=False, gap_fired=True, backlog_fired=False
    )
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "ℹ️ Du hast seit 3 Tagen nichts gepostet." in text


def test_bundle_analytics_gap_has_informational_marker():
    """Analytics gap trigger message is prefixed with ℹ️ in the bundle."""
    airtable = MagicMock()
    telegram = MagicMock()
    telegram.send_message.return_value = {"result": {"message_id": 1}}
    settings = _make_settings()

    gap_trigger = MagicMock()
    gap_trigger.evaluate.return_value = TriggerResult(fired=False, message="")
    backlog_trigger = MagicMock()
    backlog_trigger.evaluate.return_value = TriggerResult(fired=False, message="")
    analytics_trigger = MagicMock()
    analytics_trigger.evaluate.return_value = TriggerResult(
        fired=True,
        message="Deine letzten Posts haben alle denselben CTA-Typ. Schick /idea.",
    )

    checker = ProactiveCheckerService(
        airtable_svc=airtable,
        telegram_svc=telegram,
        settings=settings,
        posting_gap_trigger=gap_trigger,
        backlog_readiness_trigger=backlog_trigger,
        analytics_gap_trigger=analytics_trigger,
        pending_store=ProactivePendingStore(),
    )
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "ℹ️ Deine letzten Posts haben alle denselben CTA-Typ." in text


def test_bundle_draft_cta_uses_arrow_format():
    """The /draft CTA uses → /draft format, not the old verbose sentence."""
    checker, telegram, _ = _make_checker_with_stale(
        stale_fires=False, gap_fired=True, backlog_fired=False
    )
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "→ /draft" in text
    assert "Soll ich eine Idee ausarbeiten" not in text


# ---------------------------------------------------------------------------
# Phase 2.6 — stale proposal Telegram message format (DISABLED in 2.8)
# ---------------------------------------------------------------------------

def test_stale_proposal_message_has_action_required_header():
    """Stale proposal is DISABLED in Phase 2.8."""
    checker, telegram, _ = _make_checker_with_stale(stale_fires=True, active_pending=False)
    checker._run_stale_draft_check()
    telegram.send_message.assert_not_called()


def test_stale_proposal_message_has_confirm_with_label():
    """Stale proposal is DISABLED in Phase 2.8."""
    checker, telegram, _ = _make_checker_with_stale(stale_fires=True)
    checker._run_stale_draft_check()
    telegram.send_message.assert_not_called()


def test_stale_proposal_message_has_reject_with_label():
    """Stale proposal is DISABLED in Phase 2.8."""
    checker, telegram, _ = _make_checker_with_stale(stale_fires=True)
    checker._run_stale_draft_check()
    telegram.send_message.assert_not_called()


def test_stale_proposal_contains_display_text_and_days():
    """Stale proposal is DISABLED in Phase 2.8."""
    checker, telegram, _ = _make_checker_with_stale(stale_fires=True)
    checker._run_stale_draft_check()
    telegram.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 2.7 — W1: extended posting gap warning
# ---------------------------------------------------------------------------

def _make_checker_w1(
    days_since: int = 7,
    w1_suppressed: bool = False,
    recommendation_message: str | None = None,
) -> tuple[ProactiveCheckerService, MagicMock, SuppressionStore]:
    from operator_core.proactive.posting_recommender import PostingRecommender, PostingRecommendation, PostingCandidate

    airtable = MagicMock()
    telegram = MagicMock()
    telegram.send_message.return_value = {"result": {"message_id": 1}}
    settings = _make_settings()

    gap_trigger = MagicMock()
    gap_trigger.evaluate.return_value = TriggerResult(
        fired=True,
        message=f"Du hast seit {days_since} Tagen nichts gepostet.",
        days_since=days_since,
    )

    backlog_trigger = MagicMock()
    backlog_trigger.evaluate.return_value = TriggerResult(fired=False, message="")

    stale_trigger = MagicMock()
    stale_trigger.evaluate.return_value = TriggerResult(fired=False)

    w1_store = SuppressionStore(cooldown_hours=48)
    if w1_suppressed:
        w1_store.record_sent("extended_posting_gap")

    recommender = None
    if recommendation_message is not None:
        recommender = MagicMock(spec=PostingRecommender)
        candidate = PostingCandidate(
            record_id="recTEST",
            platform="tiktok",
            hook_preview="Test Hook",
            content_stage="ready_to_produce",
            content_format="",
            has_body=False,
            days_ready=5,
            days_since_last_post=8,
            posting_time="20:00",
        )
        recommender.recommend.return_value = PostingRecommendation(
            candidate=candidate,
            telegram_message=recommendation_message,
        )

    checker = ProactiveCheckerService(
        airtable_svc=airtable,
        telegram_svc=telegram,
        settings=settings,
        posting_gap_trigger=gap_trigger,
        backlog_readiness_trigger=backlog_trigger,
        stale_draft_trigger=stale_trigger,
        w1_suppression=w1_store,
        posting_recommender=recommender,
    )
    return checker, telegram, w1_store


def test_w1_replaces_informational_when_gap_reaches_threshold():
    """W1 (⚠️) replaces ℹ️ when days_since >= 7."""
    checker, telegram, _ = _make_checker_w1(days_since=7)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "⚠️ Du hast seit 7 Tagen nichts gepostet." in text
    assert "ℹ️" not in text


def test_w1_not_triggered_below_threshold():
    """Below 7 days, generic ℹ️ is used (no W1)."""
    checker, telegram, _ = _make_checker_w1(days_since=6)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "ℹ️ Du hast seit 6 Tagen nichts gepostet." in text
    assert "⚠️" not in text


def test_w1_suppressed_falls_back_to_informational():
    """When W1 suppression is active, ℹ️ is used instead of ⚠️."""
    checker, telegram, _ = _make_checker_w1(days_since=10, w1_suppressed=True)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "ℹ️ Du hast seit 10 Tagen nichts gepostet." in text
    assert "⚠️" not in text


def test_w1_suppression_recorded_after_send():
    """W1 suppression key is set after a successful bundle send."""
    checker, telegram, w1_store = _make_checker_w1(days_since=9)
    checker._run_check()
    assert w1_store.is_suppressed("extended_posting_gap") is True


def test_w1_recommendation_wins_over_warning():
    """When PostingRecommender returns a candidate, ⚠️ W1 is not emitted."""
    rec_msg = "📋 Posting-Empfehlung\n\nTest"
    checker, telegram, w1_store = _make_checker_w1(days_since=9, recommendation_message=rec_msg)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "📋 Posting-Empfehlung" in text
    assert "⚠️" not in text
    # W1 suppression must NOT be set when recommendation was used
    assert w1_store.is_suppressed("extended_posting_gap") is False


# ---------------------------------------------------------------------------
# Phase 2.7 — W2: idle backlog warning
# ---------------------------------------------------------------------------

def _make_checker_w2(
    idle_draft_days: int = 10,
    w2_suppressed: bool = False,
) -> tuple[ProactiveCheckerService, MagicMock, SuppressionStore]:
    airtable = MagicMock()
    telegram = MagicMock()
    telegram.send_message.return_value = {"result": {"message_id": 1}}
    settings = _make_settings()

    gap_trigger = MagicMock()
    gap_trigger.evaluate.return_value = TriggerResult(fired=False, message="")

    backlog_trigger = MagicMock()
    backlog_trigger.evaluate.return_value = TriggerResult(
        fired=True,
        message="Du hast 2 fertige Ideen im Backlog.",
        idle_draft_days=idle_draft_days,
    )

    stale_trigger = MagicMock()
    stale_trigger.evaluate.return_value = TriggerResult(fired=False)

    w2_store = SuppressionStore(cooldown_hours=48)
    if w2_suppressed:
        w2_store.record_sent("idle_backlog")

    checker = ProactiveCheckerService(
        airtable_svc=airtable,
        telegram_svc=telegram,
        settings=settings,
        posting_gap_trigger=gap_trigger,
        backlog_readiness_trigger=backlog_trigger,
        stale_draft_trigger=stale_trigger,
        w2_suppression=w2_store,
    )
    return checker, telegram, w2_store


def test_w2_replaces_suggestion_when_idle_reaches_threshold():
    """W2 (⚠️) replaces 💡 when idle_draft_days >= 10."""
    checker, telegram, _ = _make_checker_w2(idle_draft_days=10)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "⚠️ Du hast 2 fertige Ideen im Backlog." in text
    assert "💡" not in text


def test_w2_not_triggered_below_threshold():
    """Below 10 days, generic 💡 is used (no W2)."""
    checker, telegram, _ = _make_checker_w2(idle_draft_days=9)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "💡 Du hast 2 fertige Ideen im Backlog." in text
    assert "⚠️" not in text


def test_w2_suppressed_falls_back_to_suggestion():
    """When W2 suppression is active, 💡 is used instead of ⚠️."""
    checker, telegram, _ = _make_checker_w2(idle_draft_days=15, w2_suppressed=True)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "💡 Du hast 2 fertige Ideen im Backlog." in text
    assert "⚠️" not in text


def test_w2_keeps_draft_cta():
    """⚠️ W2 still includes → /draft CTA."""
    checker, telegram, _ = _make_checker_w2(idle_draft_days=10)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "→ /draft" in text


def test_w2_suppression_recorded_after_send():
    """W2 suppression key is set after a successful bundle send."""
    checker, telegram, w2_store = _make_checker_w2(idle_draft_days=12)
    checker._run_check()
    assert w2_store.is_suppressed("idle_backlog") is True


# ---------------------------------------------------------------------------
# Phase 2.7 — W3: persistent stale draft in bundle
# ---------------------------------------------------------------------------

def _make_checker_w3(
    days_stale: int = 28,
    standalone_suppression_active: bool = True,
    w3_suppressed: bool = False,
    gap_fired: bool = True,
) -> tuple[ProactiveCheckerService, MagicMock, SuppressionStore, SuppressionStore]:
    airtable = MagicMock()
    telegram = MagicMock()
    telegram.send_message.return_value = {"result": {"message_id": 1}}
    settings = _make_settings()

    gap_trigger = MagicMock()
    gap_trigger.evaluate.return_value = TriggerResult(
        fired=gap_fired,
        message="Du hast seit 3 Tagen nichts gepostet.",
        days_since=3,
    )

    backlog_trigger = MagicMock()
    backlog_trigger.evaluate.return_value = TriggerResult(fired=False, message="")

    stale_trigger = MagicMock()
    stale_trigger.evaluate.return_value = TriggerResult(
        fired=True,
        message="",
        record_id="recSTALE99",
        display_text="Alter Entwurf",
        days_stale=days_stale,
        stale_count=1,
    )

    # Standalone stale suppression (168h)
    stale_suppression = SuppressionStore(cooldown_hours=168)
    if standalone_suppression_active:
        stale_suppression.record_sent("stale_proposed_recSTALE99")

    # W3 suppression (72h)
    w3_store = SuppressionStore(cooldown_hours=72)
    if w3_suppressed:
        w3_store.record_sent("persistent_stale_recSTALE99")

    checker = ProactiveCheckerService(
        airtable_svc=airtable,
        telegram_svc=telegram,
        settings=settings,
        posting_gap_trigger=gap_trigger,
        backlog_readiness_trigger=backlog_trigger,
        stale_draft_trigger=stale_trigger,
        stale_draft_suppression=stale_suppression,
        w3_suppression=w3_store,
    )
    return checker, telegram, stale_suppression, w3_store


def test_w3_appears_in_bundle_when_conditions_met():
    """W3 (⚠️) appears in bundle when days_stale >= 28 and standalone suppression is active."""
    checker, telegram, _, _ = _make_checker_w3(days_stale=28, standalone_suppression_active=True)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "⚠️" in text
    assert "Alter Entwurf" in text
    assert "28 Tagen" in text


def test_w3_not_triggered_below_threshold():
    """W3 does not fire when days_stale < 28."""
    checker, telegram, _, _ = _make_checker_w3(days_stale=27, standalone_suppression_active=True)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    # Only ℹ️ from gap, no ⚠️ from W3
    assert "Alter Entwurf" not in text


def test_w3_skipped_when_standalone_eligible():
    """W3 is skipped when standalone suppression is NOT active (standalone would fire)."""
    checker, telegram, _, _ = _make_checker_w3(
        days_stale=30, standalone_suppression_active=False
    )
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "Alter Entwurf" not in text


def test_w3_suppressed_does_not_appear():
    """W3 is skipped when its own suppression is active."""
    checker, telegram, _, _ = _make_checker_w3(
        days_stale=30, standalone_suppression_active=True, w3_suppressed=True
    )
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "Alter Entwurf" not in text


def test_w3_suppression_recorded_after_send():
    """W3 suppression key is set after bundle send."""
    checker, telegram, _, w3_store = _make_checker_w3(
        days_stale=28, standalone_suppression_active=True
    )
    checker._run_check()
    assert w3_store.is_suppressed("persistent_stale_recSTALE99") is True


def test_w3_bundle_only_not_standalone():
    """W3 appears only in the bundle — _run_stale_draft_check is unaffected by W3 logic."""
    # With standalone suppression active, _run_stale_draft_check skips sending
    # (because standalone suppression blocks it, and pending_store is None by default)
    checker, telegram, _, _ = _make_checker_w3(
        days_stale=30, standalone_suppression_active=True
    )
    # Manually check standalone does not send for this suppressed record
    telegram.reset_mock()
    checker._run_stale_draft_check()
    # standalone is suppressed → no send
    telegram.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 2.7 — marker correctness summary
# ---------------------------------------------------------------------------

def test_marker_recommendation_is_double_checkmark():
    """PostingRecommender output is prefixed with 📋 in the bundle."""
    from operator_core.proactive.posting_recommender import PostingRecommender, PostingRecommendation, PostingCandidate
    airtable = MagicMock()
    telegram = MagicMock()
    telegram.send_message.return_value = {"result": {"message_id": 1}}
    settings = _make_settings()

    gap_trigger = MagicMock()
    gap_trigger.evaluate.return_value = TriggerResult(fired=True, message="gap", days_since=3)
    backlog_trigger = MagicMock()
    backlog_trigger.evaluate.return_value = TriggerResult(fired=False)
    stale_trigger = MagicMock()
    stale_trigger.evaluate.return_value = TriggerResult(fired=False)

    recommender = MagicMock(spec=PostingRecommender)
    candidate = PostingCandidate("recX", "tiktok", "Hook", "drafted", "", False, 3, 4, "20:00")
    recommender.recommend.return_value = PostingRecommendation(
        candidate=candidate,
        telegram_message="📋 Posting-Empfehlung\n\nDetails",
    )

    checker = ProactiveCheckerService(
        airtable_svc=airtable,
        telegram_svc=telegram,
        settings=settings,
        posting_gap_trigger=gap_trigger,
        backlog_readiness_trigger=backlog_trigger,
        stale_draft_trigger=stale_trigger,
        posting_recommender=recommender,
    )
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "📋 Posting-Empfehlung" in text
    assert "ℹ️" not in text
    assert "⚠️" not in text


def test_marker_informational_for_analytics_gap():
    """Analytics gap message always uses ℹ️."""
    checker, telegram, daily_store, analytics_store = _make_checker_with_analytics(
        gap_fired=False, backlog_fired=False, analytics_fired=True
    )
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert text.startswith("Guten Morgen")
    # Analytics gap → ℹ️
    lines = text.split("\n")
    analytics_line = next((l for l in lines if "CTA-Typ" in l), "")
    assert analytics_line.startswith("ℹ️")


# ---------------------------------------------------------------------------
# Phase 2.8 — W1 why-this-now suffix
# ---------------------------------------------------------------------------

def test_w1_suffix_includes_threshold_phrase():
    """W1 message includes 'Über Normalschwelle (7 Tage)' suffix."""
    checker, telegram, _ = _make_checker_w1(days_since=7)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "Über Normalschwelle (7 Tage)" in text


def test_w1_informational_has_no_threshold_suffix():
    """Normal ℹ️ gap message (below W1 threshold) does not include threshold suffix."""
    checker, telegram, _ = _make_checker_w1(days_since=6)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "Normalschwelle" not in text


# ---------------------------------------------------------------------------
# Phase 2.8 — W2 why-this-now suffix
# ---------------------------------------------------------------------------

def test_w2_suffix_includes_idle_days():
    """W2 message includes 'Kein Entwurf seit N Tagen' suffix with actual idle days."""
    checker, telegram, _ = _make_checker_w2(idle_draft_days=12)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "Kein Entwurf seit 12 Tagen" in text


def test_w2_informational_has_no_idle_suffix():
    """Normal 💡 backlog message (below W2 threshold) does not include idle suffix."""
    checker, telegram, _ = _make_checker_w2(idle_draft_days=9)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "Kein Entwurf seit" not in text


# ---------------------------------------------------------------------------
# Phase 2.8 — W3 why-this-now suffix
# ---------------------------------------------------------------------------

def test_w3_suffix_includes_request_sent_phrase():
    """W3 message includes 'Anfrage bereits gestellt' suffix."""
    checker, telegram, _, _ = _make_checker_w3(days_stale=28, standalone_suppression_active=True)
    checker._run_check()
    text = telegram.send_message.call_args.kwargs["text"]
    assert "Anfrage bereits gestellt" in text


# ---------------------------------------------------------------------------
# Phase 2.8 — analytics gap sample-size annotation
# ---------------------------------------------------------------------------

def test_analytics_gap_message_includes_sample_size():
    """Analytics gap trigger message includes sample size annotation '(aus N Posts)'."""
    trigger = AnalyticsGapTrigger(min_cta_records=3)
    ctx = _make_analytics_ctx(gap="noch keine Serie oder Produkt-CTA – jetzt gut einführbar", cta_count=5)
    with patch("operator_core.proactive.checker.AnalyticsLoader") as mock_loader_cls:
        mock_loader_cls.return_value.load_recent.return_value = ctx
        result = trigger.evaluate(MagicMock())
    assert result.fired is True
    assert "aus 5 Posts" in result.message
