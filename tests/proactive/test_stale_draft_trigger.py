from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from operator_core.proactive.checker import StaleDraftTrigger


def _make_record(record_id: str, created_time: str, stage: str = "drafted", title: str = "") -> MagicMock:
    r = MagicMock()
    r.record_id = record_id
    r.created_time = created_time
    r.fields = {"stage": stage, "main_point": title} if title else {"stage": stage}
    return r


def _ts(days_ago: int) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat().replace("+00:00", "Z")


def _airtable_svc_returning(records: list) -> MagicMock:
    svc = MagicMock()
    result = MagicMock()
    result.records = records
    svc.find_records.return_value = result
    return svc


class TestStaleDraftTrigger:
    def test_fires_when_draft_old_enough(self):
        trigger = StaleDraftTrigger(stale_days=14)
        records = [_make_record("recABC", _ts(20), title="Mein Entwurf")]
        svc = _airtable_svc_returning(records)
        result = trigger.evaluate(svc)
        assert result.fired is True
        assert result.record_id == "recABC"
        assert result.days_stale >= 20
        assert "Mein Entwurf" in result.display_text

    def test_does_not_fire_when_draft_too_recent(self):
        trigger = StaleDraftTrigger(stale_days=14)
        records = [_make_record("recNEW", _ts(5))]
        svc = _airtable_svc_returning(records)
        result = trigger.evaluate(svc)
        assert result.fired is False

    def test_picks_oldest_draft(self):
        trigger = StaleDraftTrigger(stale_days=14)
        records = [
            _make_record("recNEWER", _ts(16), title="Newer"),
            _make_record("recOLDER", _ts(30), title="Older"),
        ]
        svc = _airtable_svc_returning(records)
        result = trigger.evaluate(svc)
        assert result.fired is True
        assert result.record_id == "recOLDER"

    def test_no_records_returns_not_fired(self):
        trigger = StaleDraftTrigger(stale_days=14)
        svc = _airtable_svc_returning([])
        result = trigger.evaluate(svc)
        assert result.fired is False

    def test_airtable_error_returns_not_fired(self):
        trigger = StaleDraftTrigger(stale_days=14)
        svc = MagicMock()
        svc.find_records.side_effect = Exception("network error")
        result = trigger.evaluate(svc)
        assert result.fired is False

    def test_display_text_falls_back_to_record_id_when_no_title(self):
        trigger = StaleDraftTrigger(stale_days=14)
        records = [_make_record("recNOTITLE", _ts(20))]
        svc = _airtable_svc_returning(records)
        result = trigger.evaluate(svc)
        assert result.fired is True
        assert result.display_text == "recNOTITLE"

    def test_message_contains_confirm_reject_hint(self):
        trigger = StaleDraftTrigger(stale_days=14)
        records = [_make_record("recXYZ", _ts(20), title="Test")]
        svc = _airtable_svc_returning(records)
        result = trigger.evaluate(svc)
        assert "/confirm" in result.message
        assert "/reject" in result.message

    # --- Phase 2.4: stale_count and explanation ---

    def test_stale_count_is_one_for_single_candidate(self):
        trigger = StaleDraftTrigger(stale_days=14)
        records = [_make_record("recA", _ts(20), title="Nur einer")]
        svc = _airtable_svc_returning(records)
        result = trigger.evaluate(svc)
        assert result.stale_count == 1

    def test_message_unchanged_for_single_candidate(self):
        trigger = StaleDraftTrigger(stale_days=14)
        records = [_make_record("recA", _ts(20), title="Nur einer")]
        svc = _airtable_svc_returning(records)
        result = trigger.evaluate(svc)
        assert "ältester von" not in result.message

    def test_stale_count_reflects_multiple_candidates(self):
        trigger = StaleDraftTrigger(stale_days=14)
        records = [
            _make_record("recA", _ts(20), title="Erster"),
            _make_record("recB", _ts(18), title="Zweiter"),
            _make_record("recC", _ts(16), title="Dritter"),
        ]
        svc = _airtable_svc_returning(records)
        result = trigger.evaluate(svc)
        assert result.stale_count == 3

    def test_message_includes_context_for_multiple_candidates(self):
        trigger = StaleDraftTrigger(stale_days=14)
        records = [
            _make_record("recA", _ts(20), title="Erster"),
            _make_record("recB", _ts(18), title="Zweiter"),
        ]
        svc = _airtable_svc_returning(records)
        result = trigger.evaluate(svc)
        assert "ältester von 2 Entwürfen" in result.message
