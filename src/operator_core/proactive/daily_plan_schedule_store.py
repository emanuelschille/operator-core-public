from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path


class DailyPlanScheduleStore:
    """Persisted sent-key store for scheduled daily plan messages."""

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._file_path = Path(file_path) if file_path is not None else None
        self._lock = threading.Lock()
        self._sent: set[str] = set()
        self._load()

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._sent

    def mark_sent(self, key: str) -> None:
        with self._lock:
            self._sent.add(key)
            self._save_locked()

    def prune_before(self, cutoff_date: date) -> None:
        cutoff = cutoff_date.isoformat()
        with self._lock:
            retained = {
                key
                for key in self._sent
                if len(key.split(":")) < 3 or key.split(":")[1] >= cutoff
            }
            if retained != self._sent:
                self._sent = retained
                self._save_locked()

    def _load(self) -> None:
        if self._file_path is None or not self._file_path.exists():
            return
        raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return
        self._sent = {str(item).strip() for item in raw if str(item).strip()}

    def _save_locked(self) -> None:
        if self._file_path is None:
            return
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps(sorted(self._sent), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
