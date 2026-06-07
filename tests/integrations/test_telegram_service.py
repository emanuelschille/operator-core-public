from __future__ import annotations

from pathlib import Path

import pytest

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.integrations.telegram_service import (
    TelegramAPIError,
    TelegramConfigError,
    TelegramService,
    TelegramTransportError,
)


def _make_ctx(
    *,
    enabled: bool = True,
    bot_token: str = "test-token",
    allowed_user_ids: tuple[str, ...] = ("123",),
    allowed_chat_ids: tuple[str, ...] = (),
) -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="INFO",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(
            enabled=enabled,
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            allowed_chat_ids=allowed_chat_ids,
        ),
        airtable=AirtableSettings(
            enabled=False,
            api_key="",
            project_base_ids={"everydayengel": ""},
        ),
        openai=OpenAISettings(
            enabled=False,
            api_key="",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )
    return BootstrapContext(
        settings=settings,
        runtime_path=Path("projects/everydayengel/runtime.yaml"),
        project_runtime={
            "project_key": "everydayengel",
            "display_name": "Everyday Engel",
            "status": "active",
            "primary_interface": "telegram",
            "human_in_the_loop": "true",
        },
    )


def _ok_transport(payload: dict) -> object:
    def transport(method, url, headers, body):
        return 200, {"ok": True, **payload}
    return transport


def _error_transport(status: int, description: str = "bad request") -> object:
    def transport(method, url, headers, body):
        return status, {"ok": False, "description": description}
    return transport


# ── get_updates ──────────────────────────────────────────────────────────────

def test_get_updates_returns_list() -> None:
    updates = [{"update_id": 1, "message": {"text": "hi"}}]
    svc = TelegramService(_make_ctx(), transport=_ok_transport({"result": updates}))
    result = svc.get_updates(offset=0)
    assert result == updates


def test_get_updates_empty_result() -> None:
    svc = TelegramService(_make_ctx(), transport=_ok_transport({"result": []}))
    assert svc.get_updates() == []


def test_get_updates_missing_result_key() -> None:
    svc = TelegramService(_make_ctx(), transport=_ok_transport({}))
    assert svc.get_updates() == []


def test_get_updates_raises_when_disabled() -> None:
    svc = TelegramService(_make_ctx(enabled=False), transport=_ok_transport({"result": []}))
    with pytest.raises(TelegramConfigError, match="disabled"):
        svc.get_updates()


def test_get_updates_raises_when_no_token() -> None:
    svc = TelegramService(_make_ctx(bot_token=""), transport=_ok_transport({"result": []}))
    with pytest.raises(TelegramConfigError, match="token"):
        svc.get_updates()


def test_get_updates_raises_api_error() -> None:
    svc = TelegramService(_make_ctx(), transport=_error_transport(401, "Unauthorized"))
    with pytest.raises(TelegramAPIError) as exc_info:
        svc.get_updates()
    assert exc_info.value.status_code == 401
    assert "Unauthorized" in str(exc_info.value)


def test_get_updates_url_contains_offset_and_timeout() -> None:
    captured: list[str] = []

    def transport(method, url, headers, body):
        captured.append(url)
        return 200, {"ok": True, "result": []}

    svc = TelegramService(_make_ctx(), transport=transport)
    svc.get_updates(offset=42, timeout=15)
    assert "offset=42" in captured[0]
    assert "timeout=15" in captured[0]


def test_get_updates_url_has_token() -> None:
    captured: list[str] = []

    def transport(method, url, headers, body):
        captured.append(url)
        return 200, {"ok": True, "result": []}

    svc = TelegramService(_make_ctx(bot_token="abc123"), transport=transport)
    svc.get_updates()
    assert "botabc123" in captured[0]


# ── send_message ─────────────────────────────────────────────────────────────

def test_send_message_sends_correct_body() -> None:
    captured: list[dict] = []

    def transport(method, url, headers, body):
        captured.append({"method": method, "body": body})
        return 200, {"ok": True, "result": {"message_id": 99}}

    svc = TelegramService(_make_ctx(), transport=transport)
    svc.send_message(chat_id=555, text="hello")

    assert captured[0]["method"] == "POST"
    assert captured[0]["body"]["chat_id"] == 555
    assert captured[0]["body"]["text"] == "hello"
    assert captured[0]["body"]["disable_web_page_preview"] is True


def test_send_message_with_reply_and_parse_mode() -> None:
    captured: list[dict] = []

    def transport(method, url, headers, body):
        captured.append(body)
        return 200, {"ok": True, "result": {}}

    svc = TelegramService(_make_ctx(), transport=transport)
    svc.send_message(chat_id=1, text="yo", reply_to_message_id=7, parse_mode="Markdown")

    assert captured[0]["reply_to_message_id"] == 7
    assert captured[0]["parse_mode"] == "Markdown"


def test_send_message_with_reply_markup() -> None:
    captured: list[dict] = []

    def transport(method, url, headers, body):
        captured.append(body)
        return 200, {"ok": True, "result": {}}

    svc = TelegramService(_make_ctx(), transport=transport)
    svc.send_message(
        chat_id=1,
        text="yo",
        reply_markup={"inline_keyboard": [[{"text": "A", "callback_data": "x"}]]},
    )

    assert captured[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "x"


def test_send_message_no_reply_to_omits_key() -> None:
    captured: list[dict] = []

    def transport(method, url, headers, body):
        captured.append(body)
        return 200, {"ok": True, "result": {}}

    svc = TelegramService(_make_ctx(), transport=transport)
    svc.send_message(chat_id=1, text="yo")

    assert "reply_to_message_id" not in captured[0]
    assert "parse_mode" not in captured[0]


def test_send_message_raises_when_disabled() -> None:
    svc = TelegramService(_make_ctx(enabled=False), transport=_ok_transport({}))
    with pytest.raises(TelegramConfigError, match="disabled"):
        svc.send_message(chat_id=1, text="hi")


def test_send_message_raises_api_error() -> None:
    svc = TelegramService(_make_ctx(), transport=_error_transport(400, "Bad Request"))
    with pytest.raises(TelegramAPIError) as exc_info:
        svc.send_message(chat_id=1, text="hi")
    assert exc_info.value.status_code == 400


def test_answer_callback_query_sends_correct_body() -> None:
    captured: list[dict] = []

    def transport(method, url, headers, body):
        captured.append({"method": method, "url": url, "body": body})
        return 200, {"ok": True, "result": True}

    svc = TelegramService(_make_ctx(), transport=transport)
    svc.answer_callback_query(callback_query_id="abc123", text="done")

    assert captured[0]["method"] == "POST"
    assert "answerCallbackQuery" in captured[0]["url"]
    assert captured[0]["body"]["callback_query_id"] == "abc123"
    assert captured[0]["body"]["text"] == "done"


def test_edit_message_text_sends_correct_body() -> None:
    captured: list[dict] = []

    def transport(method, url, headers, body):
        captured.append({"method": method, "url": url, "body": body})
        return 200, {"ok": True, "result": True}

    svc = TelegramService(_make_ctx(), transport=transport)
    svc.edit_message_text(
        chat_id=555,
        message_id=42,
        text="updated",
        reply_markup={"inline_keyboard": []},
    )

    assert captured[0]["method"] == "POST"
    assert "editMessageText" in captured[0]["url"]
    assert captured[0]["body"]["chat_id"] == 555
    assert captured[0]["body"]["message_id"] == 42
    assert captured[0]["body"]["text"] == "updated"
    assert captured[0]["body"]["reply_markup"] == {"inline_keyboard": []}


def test_set_my_commands_sends_correct_body() -> None:
    captured: list[dict] = []

    def transport(method, url, headers, body):
        captured.append({"method": method, "url": url, "body": body})
        return 200, {"ok": True, "result": True}

    svc = TelegramService(_make_ctx(), transport=transport)
    svc.set_my_commands(
        commands=[
            {"command": "menu", "description": "Menü öffnen"},
            {"command": "draft", "description": "Entwurf erstellen"},
        ]
    )

    assert captured[0]["method"] == "POST"
    assert "setMyCommands" in captured[0]["url"]
    assert captured[0]["body"] == {
        "commands": [
            {"command": "menu", "description": "Menü öffnen"},
            {"command": "draft", "description": "Entwurf erstellen"},
        ]
    }


# ── transport error ───────────────────────────────────────────────────────────

def test_transport_error_propagates() -> None:
    def broken_transport(method, url, headers, body):
        raise TelegramTransportError("Network unreachable")

    svc = TelegramService(_make_ctx(), transport=broken_transport)
    with pytest.raises(TelegramTransportError, match="Network unreachable"):
        svc.get_updates()
