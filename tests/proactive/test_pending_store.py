from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from operator_core.proactive.pending_store import PendingProposal, ProactivePendingStore


def _make_proposal(sent_message_id: int = 1001, record_id: str = "recABC") -> PendingProposal:
    return PendingProposal(
        action_type="mark_stale",
        record_id=record_id,
        display_text="Test Draft",
        proposed_stage="stale",
        days_stale=20,
        sent_message_id=sent_message_id,
        created_at=datetime.now(tz=timezone.utc),
    )


class TestProactivePendingStore:
    def test_put_and_consume(self):
        store = ProactivePendingStore()
        proposal = _make_proposal()
        store.put(proposal)
        result = store.consume(1001)
        assert result is not None
        assert result.record_id == "recABC"

    def test_consume_removes_entry(self):
        store = ProactivePendingStore()
        store.put(_make_proposal())
        store.consume(1001)
        assert store.consume(1001) is None

    def test_consume_unknown_id_returns_none(self):
        store = ProactivePendingStore()
        assert store.consume(9999) is None

    def test_consume_expired_returns_none(self):
        store = ProactivePendingStore(ttl_hours=1)
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        proposal = PendingProposal(
            action_type="mark_stale",
            record_id="recOLD",
            display_text="Old",
            proposed_stage="stale",
            days_stale=30,
            sent_message_id=2001,
            created_at=old_time,
        )
        store.put(proposal)
        result = store.consume(2001)
        assert result is None

    def test_put_overwrites_existing(self):
        store = ProactivePendingStore()
        store.put(_make_proposal(record_id="recFIRST"))
        store.put(_make_proposal(record_id="recSECOND"))
        result = store.consume(1001)
        assert result is not None
        assert result.record_id == "recSECOND"

    def test_size(self):
        store = ProactivePendingStore()
        assert store.size() == 0
        store.put(_make_proposal(sent_message_id=1001))
        store.put(_make_proposal(sent_message_id=1002))
        assert store.size() == 2
        store.consume(1001)
        assert store.size() == 1

    def test_multiple_proposals_independent(self):
        store = ProactivePendingStore()
        store.put(_make_proposal(sent_message_id=1001, record_id="recA"))
        store.put(_make_proposal(sent_message_id=1002, record_id="recB"))
        assert store.consume(1001).record_id == "recA"
        assert store.consume(1002).record_id == "recB"


class TestProactivePendingStoreHasActive:
    def test_has_active_returns_false_when_empty(self):
        store = ProactivePendingStore()
        assert store.has_active() is False

    def test_has_active_returns_true_with_fresh_proposal(self):
        store = ProactivePendingStore()
        store.put(_make_proposal(sent_message_id=5001))
        assert store.has_active() is True

    def test_has_active_returns_false_when_all_expired(self):
        store = ProactivePendingStore(ttl_hours=1)
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        expired = PendingProposal(
            action_type="mark_stale",
            record_id="recEXP",
            display_text="Expired",
            proposed_stage="stale",
            days_stale=30,
            sent_message_id=9001,
            created_at=old_time,
        )
        store.put(expired)
        assert store.has_active() is False

    def test_has_active_returns_true_when_at_least_one_fresh(self):
        store = ProactivePendingStore(ttl_hours=1)
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        expired = PendingProposal(
            action_type="mark_stale",
            record_id="recEXP",
            display_text="Expired",
            proposed_stage="stale",
            days_stale=30,
            sent_message_id=9001,
            created_at=old_time,
        )
        fresh = _make_proposal(sent_message_id=9002)
        store.put(expired)
        store.put(fresh)
        assert store.has_active() is True

    def test_has_active_unaffected_by_consuming_one_of_two(self):
        store = ProactivePendingStore()
        store.put(_make_proposal(sent_message_id=1, record_id="recA"))
        store.put(_make_proposal(sent_message_id=2, record_id="recB"))
        store.consume(1)
        assert store.has_active() is True
