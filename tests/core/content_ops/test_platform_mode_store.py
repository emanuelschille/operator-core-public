from __future__ import annotations

import pytest

from operator_core.core.content_ops.platform_mode_store import PlatformModeStore


def test_set_and_get_mode() -> None:
    store = PlatformModeStore()
    store.set_mode(chat_id="100", user_id="200", platform="tiktok")
    assert store.get_mode(chat_id="100", user_id="200") == "tiktok"


def test_get_returns_none_when_not_set() -> None:
    store = PlatformModeStore()
    assert store.get_mode(chat_id="100", user_id="200") is None


def test_set_normalizes_platform_to_lowercase() -> None:
    store = PlatformModeStore()
    store.set_mode(chat_id="100", user_id="200", platform="TikTok")
    assert store.get_mode(chat_id="100", user_id="200") == "tiktok"


def test_clear_removes_mode() -> None:
    store = PlatformModeStore()
    store.set_mode(chat_id="100", user_id="200", platform="instagram_reel")
    store.clear_mode(chat_id="100", user_id="200")
    assert store.get_mode(chat_id="100", user_id="200") is None


def test_clear_noop_when_not_set() -> None:
    store = PlatformModeStore()
    store.clear_mode(chat_id="100", user_id="200")
    assert store.get_mode(chat_id="100", user_id="200") is None


def test_different_users_have_independent_modes() -> None:
    store = PlatformModeStore()
    store.set_mode(chat_id="100", user_id="1", platform="tiktok")
    store.set_mode(chat_id="100", user_id="2", platform="instagram_reel")
    assert store.get_mode(chat_id="100", user_id="1") == "tiktok"
    assert store.get_mode(chat_id="100", user_id="2") == "instagram_reel"


def test_missing_chat_id_returns_none() -> None:
    store = PlatformModeStore()
    store.set_mode(chat_id=None, user_id="200", platform="tiktok")
    assert store.get_mode(chat_id=None, user_id="200") is None


def test_missing_user_id_returns_none() -> None:
    store = PlatformModeStore()
    store.set_mode(chat_id="100", user_id=None, platform="tiktok")
    assert store.get_mode(chat_id="100", user_id=None) is None


def test_overwrite_updates_mode() -> None:
    store = PlatformModeStore()
    store.set_mode(chat_id="100", user_id="200", platform="tiktok")
    store.set_mode(chat_id="100", user_id="200", platform="facebook_reel")
    assert store.get_mode(chat_id="100", user_id="200") == "facebook_reel"
