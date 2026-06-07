from __future__ import annotations


class PlatformModeStore:
    """Persists the active platform mode per chat/user across commands."""

    def __init__(self) -> None:
        self._modes: dict[str, str] = {}

    def set_mode(self, *, chat_id: str | None, user_id: str | None, platform: str) -> None:
        key = self._key(chat_id=chat_id, user_id=user_id)
        if key is None:
            return
        self._modes[key] = platform.strip().lower()

    def get_mode(self, *, chat_id: str | None, user_id: str | None) -> str | None:
        key = self._key(chat_id=chat_id, user_id=user_id)
        if key is None:
            return None
        return self._modes.get(key)

    def clear_mode(self, *, chat_id: str | None, user_id: str | None) -> None:
        key = self._key(chat_id=chat_id, user_id=user_id)
        if key is None:
            return
        self._modes.pop(key, None)

    @staticmethod
    def _key(*, chat_id: str | None, user_id: str | None) -> str | None:
        if not chat_id or not user_id:
            return None
        return f"{chat_id}:{user_id}"
