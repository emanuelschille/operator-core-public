from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class PlanReminder:
    """A scheduled reminder for a specific Daily Plan record.

    key — unique string; scheduling the same key replaces the prior entry.
    fire_at — UTC datetime when the reminder becomes due.
    chat_id — Telegram chat to send to.
    platform — platform key ("tiktok", …).
    record_id — Daily Plan Airtable record_id (used for obsolescence check).
    reminder_type — "remind_15m" | "analytics_3d".
    context_text — pre-built body text (refreshed at fire-time when possible).
    analytics_record_id / analytics_table_id — filled only for analytics_3d.
    """

    key: str
    fire_at: datetime
    chat_id: int
    platform: str
    record_id: str
    reminder_type: str
    context_text: str
    analytics_record_id: str = ""
    analytics_table_id: str = ""


class PlanReminderStore:
    """Thread-safe in-memory store for scheduled plan reminders.

    Optionally persists to a JSON file so reminders survive service restarts.
    """

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._store: dict[str, PlanReminder] = {}
        self._lock = threading.Lock()
        self._file_path = Path(file_path) if file_path is not None else None
        self._load()

    def schedule(self, reminder: PlanReminder) -> None:
        """Schedule a reminder. Overwrites any existing reminder with the same key."""
        with self._lock:
            self._store[reminder.key] = reminder
            self._save_locked()

    def cancel(self, key: str) -> None:
        """Cancel a reminder by key. No-op if not present."""
        with self._lock:
            self._store.pop(key, None)
            self._save_locked()

    def due(self) -> list[PlanReminder]:
        """Return all due reminders and atomically remove them from the store."""
        now = datetime.now(tz=timezone.utc)
        with self._lock:
            due = [r for r in self._store.values() if r.fire_at <= now]
            for r in due:
                del self._store[r.key]
            if due:
                self._save_locked()
        return due

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def _load(self) -> None:
        if self._file_path is None or not self._file_path.exists():
            return
        raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return
        loaded: dict[str, PlanReminder] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                reminder = PlanReminder(
                    key=str(item.get("key") or ""),
                    fire_at=datetime.fromisoformat(str(item.get("fire_at") or "")).astimezone(timezone.utc),
                    chat_id=int(item.get("chat_id") or 0),
                    platform=str(item.get("platform") or ""),
                    record_id=str(item.get("record_id") or ""),
                    reminder_type=str(item.get("reminder_type") or ""),
                    context_text=str(item.get("context_text") or ""),
                    analytics_record_id=str(item.get("analytics_record_id") or ""),
                    analytics_table_id=str(item.get("analytics_table_id") or ""),
                )
            except (TypeError, ValueError):
                continue
            if reminder.key:
                loaded[reminder.key] = reminder
        self._store = loaded

    def _save_locked(self) -> None:
        if self._file_path is None:
            return
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "key": reminder.key,
                "fire_at": reminder.fire_at.astimezone(timezone.utc).isoformat(),
                "chat_id": reminder.chat_id,
                "platform": reminder.platform,
                "record_id": reminder.record_id,
                "reminder_type": reminder.reminder_type,
                "context_text": reminder.context_text,
                "analytics_record_id": reminder.analytics_record_id,
                "analytics_table_id": reminder.analytics_table_id,
            }
            for reminder in sorted(self._store.values(), key=lambda item: item.key)
        ]
        self._file_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
