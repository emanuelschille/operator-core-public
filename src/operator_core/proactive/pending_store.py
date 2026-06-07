from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_DEFAULT_TTL_HOURS = 48


@dataclass(frozen=True)
class PendingProposal:
    action_type: str       # e.g. "mark_stale"
    record_id: str
    display_text: str      # human-readable context shown in proposal message
    proposed_stage: str    # e.g. "stale"
    days_stale: int
    sent_message_id: int
    created_at: datetime


class ProactivePendingStore:
    """Thread-safe in-memory store for proactive proposals awaiting /confirm or /reject.

    Keyed by sent_message_id (int) so confirmation must reference the exact proposal
    message via Telegram reply_to_message_id. In-memory only — resets on restart.
    """

    def __init__(self, ttl_hours: int = _DEFAULT_TTL_HOURS) -> None:
        self._ttl = timedelta(hours=ttl_hours)
        self._store: dict[int, PendingProposal] = {}
        self._lock = threading.Lock()

    def put(self, proposal: PendingProposal) -> None:
        """Store a proposal. Overwrites any existing entry for the same sent_message_id."""
        with self._lock:
            self._store[proposal.sent_message_id] = proposal

    def consume(self, sent_message_id: int) -> PendingProposal | None:
        """Atomically retrieve and remove a proposal. Returns None if not found or expired."""
        with self._lock:
            proposal = self._store.get(sent_message_id)
            if proposal is None:
                return None
            if self._is_expired(proposal):
                del self._store[sent_message_id]
                return None
            del self._store[sent_message_id]
            return proposal

    def has_active(self) -> bool:
        """Return True if at least one non-expired proposal exists."""
        now = datetime.now(tz=timezone.utc)
        with self._lock:
            return any((now - p.created_at) <= self._ttl for p in self._store.values())

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def _is_expired(self, proposal: PendingProposal) -> bool:
        return (datetime.now(tz=timezone.utc) - proposal.created_at) > self._ttl
