from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

_DEFAULT_TTL_HOURS = 48


class DailyPlanMessageStore:
    """Thread-safe in-memory mapping of Telegram plan message ids to Daily Plan record ids."""

    def __init__(self, ttl_hours: int = _DEFAULT_TTL_HOURS) -> None:
        self._ttl = timedelta(hours=ttl_hours)
        self._store: dict[str, tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    def put(self, *, chat_id: int, message_id: int, record_id: str) -> None:
        if not record_id:
            return
        key = self._key(chat_id=chat_id, message_id=message_id)
        with self._lock:
            self._store[key] = (record_id, datetime.now(tz=timezone.utc))

    def get(self, *, chat_id: int, message_id: int) -> str | None:
        key = self._key(chat_id=chat_id, message_id=message_id)
        with self._lock:
            stored = self._store.get(key)
            if stored is None:
                return None
            record_id, created_at = stored
            if datetime.now(tz=timezone.utc) - created_at > self._ttl:
                self._store.pop(key, None)
                return None
            return record_id

    @staticmethod
    def _key(*, chat_id: int, message_id: int) -> str:
        return f"{chat_id}:{message_id}"
