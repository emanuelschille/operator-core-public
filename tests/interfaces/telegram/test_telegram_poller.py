from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.interfaces.telegram.poller import TelegramPoller, is_update_allowed


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_ctx(
    *,
    allowed_user_ids: tuple[str, ...] = ("111",),
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
            enabled=True,
            bot_token="tok",
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


def _make_update(
    *,
    update_id: int = 1,
    user_id: int = 111,
    chat_id: int = 999,
    text: str = "idea give me an idea",
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "from": {"id": user_id, "username": "testuser"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _make_callback_update(
    *,
    update_id: int = 2,
    user_id: int = 111,
    chat_id: int = 999,
    callback_data: str = "plan_demo:execute_today",
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cbq-1",
            "from": {"id": user_id, "username": "testuser"},
            "data": callback_data,
            "message": {
                "message_id": update_id * 10,
                "chat": {"id": chat_id, "type": "private"},
                "text": "📋 Tagesplan",
            },
        },
    }


def _make_poller(ctx: BootstrapContext) -> tuple[TelegramPoller, MagicMock, MagicMock, MagicMock]:
    telegram_svc = MagicMock()
    request_flow_svc = MagicMock()
    formatter_svc = MagicMock()

    poller = TelegramPoller(
        bootstrap_context=ctx,
        telegram_service=telegram_svc,
        request_flow_service=request_flow_svc,
        formatter_service=formatter_svc,
    )
    return poller, telegram_svc, request_flow_svc, formatter_svc


# ── is_update_allowed ─────────────────────────────────────────────────────────

def test_allowed_by_user_id() -> None:
    assert is_update_allowed(
        user_id=111,
        chat_id=999,
        allowed_user_ids=("111",),
        allowed_chat_ids=(),
    )


def test_allowed_by_chat_id() -> None:
    assert is_update_allowed(
        user_id=111,
        chat_id=999,
        allowed_user_ids=(),
        allowed_chat_ids=("999",),
    )


def test_blocked_when_neither_matches() -> None:
    assert not is_update_allowed(
        user_id=999,
        chat_id=888,
        allowed_user_ids=("111",),
        allowed_chat_ids=("222",),
    )


def test_blocked_when_user_id_none_and_only_user_ids_configured() -> None:
    assert not is_update_allowed(
        user_id=None,
        chat_id=None,
        allowed_user_ids=("111",),
        allowed_chat_ids=(),
    )


def test_allowed_by_chat_id_when_user_id_is_none() -> None:
    assert is_update_allowed(
        user_id=None,
        chat_id=555,
        allowed_user_ids=("111",),
        allowed_chat_ids=("555",),
    )


def test_both_lists_empty_blocks() -> None:
    assert not is_update_allowed(
        user_id=111,
        chat_id=999,
        allowed_user_ids=(),
        allowed_chat_ids=(),
    )


# ── _handle_update: blocked ───────────────────────────────────────────────────

def test_blocked_user_does_not_reach_request_flow() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",), allowed_chat_ids=())
    poller, _, request_flow_svc, _ = _make_poller(ctx)

    blocked_update = _make_update(user_id=999, chat_id=888)
    poller._handle_update(blocked_update)

    request_flow_svc.handle_telegram_entry_handoff.assert_not_called()


def test_blocked_user_does_not_trigger_send_message() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",), allowed_chat_ids=())
    poller, telegram_svc, _, _ = _make_poller(ctx)

    poller._handle_update(_make_update(user_id=999, chat_id=888))

    telegram_svc.send_message.assert_not_called()


# ── _handle_update: allowed ───────────────────────────────────────────────────

def test_allowed_user_reaches_request_flow() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, _, request_flow_svc, formatter_svc = _make_poller(ctx)

    mock_result = MagicMock()
    request_flow_svc.handle_telegram_entry_handoff.return_value = mock_result

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = "✅ done"
    mock_formatted.reply_to_message_id = 10
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_update(user_id=111, chat_id=999))

    request_flow_svc.handle_telegram_entry_handoff.assert_called_once()


def test_allowed_chat_id_reaches_request_flow() -> None:
    ctx = _make_ctx(allowed_user_ids=(), allowed_chat_ids=("999",))
    poller, _, request_flow_svc, formatter_svc = _make_poller(ctx)

    mock_result = MagicMock()
    request_flow_svc.handle_telegram_entry_handoff.return_value = mock_result

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = "ok"
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_update(user_id=222, chat_id=999))

    request_flow_svc.handle_telegram_entry_handoff.assert_called_once()


def test_send_message_called_with_formatted_reply() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()
    telegram_svc.send_message.return_value = {}

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = "✅ Anfrage verarbeitet"
    mock_formatted.reply_to_message_id = 10
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_update(user_id=111, chat_id=999))

    telegram_svc.send_message.assert_called_once_with(
        chat_id=999,
        text="✅ Anfrage verarbeitet",
        reply_to_message_id=10,
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup=None,
    )


def test_send_message_passes_menu_keyboard_markup() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()
    telegram_svc.send_message.return_value = {}

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = "✅ Anfrage verarbeitet"
    mock_formatted.reply_to_message_id = 10
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = {
        "keyboard": [
            [{"text": "📋 Tagesplan"}, {"text": "⏳ Status prüfen"}],
            [{"text": "💡 Neue Idee"}, {"text": "📝 Entwurf aus Idee"}],
            [{"text": "🎣 Hook erstellen"}, {"text": "💬 Caption erstellen"}],
            [{"text": "☰ Menü"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_update(user_id=111, chat_id=999))

    telegram_svc.send_message.assert_called_once_with(
        chat_id=999,
        text="✅ Anfrage verarbeitet",
        reply_to_message_id=10,
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup={
            "keyboard": [
                [{"text": "📋 Tagesplan"}, {"text": "⏳ Status prüfen"}],
                [{"text": "💡 Neue Idee"}, {"text": "📝 Entwurf aus Idee"}],
                [{"text": "🎣 Hook erstellen"}, {"text": "💬 Caption erstellen"}],
                [{"text": "☰ Menü"}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        },
    )


def test_no_send_when_chat_id_is_none() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = None
    mock_formatted.text = "ok"
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_update(user_id=111))

    telegram_svc.send_message.assert_not_called()


def test_no_send_when_text_is_empty() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = ""
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_update(user_id=111))

    telegram_svc.send_message.assert_not_called()


def test_callback_update_reaches_request_flow() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, _, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = ""
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = None
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "Heute so ausführen"
    mock_formatted.send_response = False
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update())

    request_flow_svc.handle_telegram_entry_handoff.assert_called_once()


def test_callback_update_answers_callback_query() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = ""
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = None
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "Heute so ausführen"
    mock_formatted.send_response = False
    mock_formatted.edit_message_id = 20
    mock_formatted.edit_message_text = "📋 Tagesplan\n✅ Auswahl gespeichert: Heute so ausführen"
    mock_formatted.edit_reply_markup = {
        "inline_keyboard": [[{"text": "🔁 Auswahl ändern", "callback_data": "plan_demo:change_selection"}]]
    }
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update())

    telegram_svc.answer_callback_query.assert_called_once_with(
        callback_query_id="cbq-1",
        text="Heute so ausführen",
    )
    telegram_svc.edit_message_text.assert_called_once_with(
        chat_id=999,
        message_id=20,
        text="📋 Tagesplan\n✅ Auswahl gespeichert: Heute so ausführen",
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup={
            "inline_keyboard": [[{"text": "🔁 Auswahl ändern", "callback_data": "plan_demo:change_selection"}]]
        },
    )


def test_callback_answer_failure_does_not_block_response_delivery() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()
    telegram_svc.answer_callback_query.side_effect = RuntimeError("query is too old")
    telegram_svc.send_message.return_value = {}

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = "💡 Idee\n\n• Beim Kochen merke ich, dass ich mich hinsetzen muss."
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = {"inline_keyboard": []}
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "TikTok"
    mock_formatted.send_response = True
    mock_formatted.edit_message_id = 20
    mock_formatted.edit_message_text = "✅ Plattform gewählt: TikTok"
    mock_formatted.edit_reply_markup = None
    mock_formatted.additional_responses = ()
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update())

    telegram_svc.answer_callback_query.assert_called_once()
    telegram_svc.edit_message_text.assert_called_once()
    telegram_svc.send_message.assert_called_once()


def test_platform_continue_callback_confirms_before_replay_and_sends_idea_separately() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)
    telegram_svc.send_message.return_value = {}

    def _handle_after_immediate_confirmation(_handoff):
        telegram_svc.answer_callback_query.assert_called_once_with(
            callback_query_id="cbq-1",
            text="TikTok",
        )
        telegram_svc.edit_message_text.assert_called_once_with(
            chat_id=999,
            message_id=20,
            text="✅ Plattform gewählt: TikTok",
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup=None,
        )
        return MagicMock()

    request_flow_svc.handle_telegram_entry_handoff.side_effect = _handle_after_immediate_confirmation

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = "💡 Idee\n\nBeim Kochen merke ich, dass ich mich hinsetzen muss."
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = {"inline_keyboard": []}
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "TikTok"
    mock_formatted.send_response = True
    mock_formatted.edit_message_id = 20
    mock_formatted.edit_message_text = "✅ Plattform gewählt: TikTok"
    mock_formatted.edit_reply_markup = None
    mock_formatted.additional_responses = ()
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update(callback_data="platform_mode:continue:tiktok"))

    assert telegram_svc.answer_callback_query.call_count == 1
    assert telegram_svc.edit_message_text.call_count == 1
    telegram_svc.send_message.assert_called_once_with(
        chat_id=999,
        text="💡 Idee\n\nBeim Kochen merke ich, dass ich mich hinsetzen muss.",
        reply_to_message_id=None,
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup={"inline_keyboard": []},
    )


@pytest.mark.parametrize(
    ("callback_data", "label"),
    [
        ("content_ops:idea_fresh:proposal-1", "Frischer"),
        ("content_ops:idea_angle:proposal-1", "Neuer Winkel"),
    ],
)
def test_idea_fallback_recovery_callback_confirms_before_rerun(
    callback_data: str,
    label: str,
) -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)
    telegram_svc.send_message.return_value = {}
    edit_text = f"↻ {label} wird erstellt."

    def _handle_after_immediate_confirmation(_handoff):
        telegram_svc.answer_callback_query.assert_called_once_with(
            callback_query_id="cbq-1",
            text=label,
        )
        telegram_svc.edit_message_text.assert_called_once_with(
            chat_id=999,
            message_id=20,
            text=edit_text,
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup=None,
        )
        return MagicMock()

    request_flow_svc.handle_telegram_entry_handoff.side_effect = _handle_after_immediate_confirmation

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = "💡 Idee\n\nBeim Kochen waehle ich bewusst einen anderen Moment."
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = {"inline_keyboard": []}
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = label
    mock_formatted.send_response = True
    mock_formatted.edit_message_id = 20
    mock_formatted.edit_message_text = edit_text
    mock_formatted.edit_reply_markup = None
    mock_formatted.additional_responses = ()
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update(callback_data=callback_data))

    assert telegram_svc.answer_callback_query.call_count == 1
    assert telegram_svc.edit_message_text.call_count == 1
    telegram_svc.send_message.assert_called_once_with(
        chat_id=999,
        text="💡 Idee\n\nBeim Kochen waehle ich bewusst einen anderen Moment.",
        reply_to_message_id=None,
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup={"inline_keyboard": []},
    )


def test_platform_continue_stale_callback_final_edit_is_not_suppressed() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = ""
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = None
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "Auswahl abgelaufen"
    mock_formatted.send_response = False
    mock_formatted.edit_message_id = 20
    mock_formatted.edit_message_text = "ℹ️ Plattformauswahl abgelaufen. Bitte Aktion erneut starten."
    mock_formatted.edit_reply_markup = None
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update(callback_data="platform_mode:continue:tiktok"))

    assert telegram_svc.edit_message_text.call_args_list == [
        call(
            chat_id=999,
            message_id=20,
            text="✅ Plattform gewählt: TikTok",
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup=None,
        ),
        call(
            chat_id=999,
            message_id=20,
            text="ℹ️ Plattformauswahl abgelaufen. Bitte Aktion erneut starten.",
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup=None,
        ),
    ]
    telegram_svc.send_message.assert_not_called()


def test_poller_sends_menu_keyboard_refresh_sequence() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()
    telegram_svc.send_message.return_value = {}

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = "⌨️ Menü wird aktualisiert."
    mock_formatted.reply_to_message_id = 10
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = {"remove_keyboard": True}
    mock_formatted.additional_responses = (
        MagicMock(
            text="⌨️ Menü aktualisiert.",
            reply_to_message_id=None,
            reply_markup={
                "keyboard": [
                    [{"text": "📋 Tagesplan"}, {"text": "💡 Neue Idee"}],
                    [{"text": "📝 Voll Auto"}, {"text": "🎯 Modus"}],
                    [{"text": "☰ Menü"}],
                ],
                "resize_keyboard": True,
                "is_persistent": True,
            },
        ),
        MagicMock(
            text="☰ Menü\n\nWähle eine Aktion.",
            reply_to_message_id=None,
            reply_markup={
                "inline_keyboard": [[{"text": "📋 Tagesplan", "callback_data": "menu:plan"}]]
            },
        ),
    )
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_update(user_id=111, chat_id=999, text="/menu"))

    assert telegram_svc.send_message.call_count == 3
    assert telegram_svc.send_message.call_args_list[0] == call(
        chat_id=999,
        text="⌨️ Menü wird aktualisiert.",
        reply_to_message_id=10,
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup={"remove_keyboard": True},
    )
    assert telegram_svc.send_message.call_args_list[1] == call(
        chat_id=999,
        text="⌨️ Menü aktualisiert.",
        reply_to_message_id=None,
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup={
            "keyboard": [
                [{"text": "📋 Tagesplan"}, {"text": "💡 Neue Idee"}],
                [{"text": "📝 Voll Auto"}, {"text": "🎯 Modus"}],
                [{"text": "☰ Menü"}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        },
    )
    assert telegram_svc.send_message.call_args_list[2] == call(
        chat_id=999,
        text="☰ Menü\n\nWähle eine Aktion.",
        reply_to_message_id=None,
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup={
            "inline_keyboard": [[{"text": "📋 Tagesplan", "callback_data": "menu:plan"}]]
        },
    )


def test_poller_registers_sent_daily_plan_message_with_request_flow_service() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()
    telegram_svc.send_message.return_value = {"result": {"message_id": 321}}

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = "📋 Tagesplan · YouTube"
    mock_formatted.reply_to_message_id = 10
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = {
        "inline_keyboard": [
            [
                {"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-youtube"},
                {"text": "🪄 Automatisch ergänzen", "callback_data": "plan_demo:auto_fill:rec-youtube"},
            ]
        ]
    }
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_update(user_id=111, chat_id=999, text="/plan_demo"))

    request_flow_svc.register_daily_plan_message.assert_called_once_with(
        chat_id=999,
        message_id=321,
        record_id="rec-youtube",
    )


def test_poller_registers_edited_daily_plan_message_with_request_flow_service() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = ""
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = None
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "Gespeichert"
    mock_formatted.send_response = False
    mock_formatted.edit_message_id = 555
    mock_formatted.edit_message_text = "📋 Tagesplan · YouTube"
    mock_formatted.edit_reply_markup = {
        "inline_keyboard": [
            [
                {"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-youtube"},
            ]
        ]
    }
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update(callback_data="plan_demo:auto_fill:rec-youtube"))

    request_flow_svc.register_daily_plan_message.assert_called_once_with(
        chat_id=999,
        message_id=555,
        record_id="rec-youtube",
    )


def test_menu_callback_edit_removes_duplicate_menu_button() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = ""
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = None
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "Entwurf erstellen"
    mock_formatted.send_response = False
    mock_formatted.edit_message_id = 20
    mock_formatted.edit_message_text = "📝 Entwurf erstellen\n\nBereich gewählt.\nAls Nächstes kannst du einen Entwurf anstoßen."
    mock_formatted.edit_reply_markup = None
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update(callback_data="menu:draft"))

    telegram_svc.edit_message_text.assert_called_once_with(
        chat_id=999,
        message_id=20,
        text="📝 Entwurf erstellen\n\nBereich gewählt.\nAls Nächstes kannst du einen Entwurf anstoßen.",
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup=None,
    )


def test_callback_update_does_not_send_chat_message_when_callback_only() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = ""
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = None
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "Heute so ausführen"
    mock_formatted.send_response = False
    mock_formatted.edit_message_id = 20
    mock_formatted.edit_message_text = "📋 Tagesplan\n✅ Auswahl gespeichert: Heute so ausführen"
    mock_formatted.edit_reply_markup = {
        "inline_keyboard": [[{"text": "🔁 Auswahl ändern", "callback_data": "plan_demo:change_selection"}]]
    }
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update())

    telegram_svc.send_message.assert_not_called()
    telegram_svc.answer_callback_query.assert_called_once_with(
        callback_query_id="cbq-1",
        text="Heute so ausführen",
    )


def test_callback_update_removes_buttons_via_message_edit() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = ""
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = None
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "Heute auslassen"
    mock_formatted.send_response = False
    mock_formatted.edit_message_id = 20
    mock_formatted.edit_message_text = "📋 Tagesplan\n✅ Auswahl gespeichert: Heute auslassen"
    mock_formatted.edit_reply_markup = {
        "inline_keyboard": [[{"text": "🔁 Auswahl ändern", "callback_data": "plan_demo:change_selection"}]]
    }
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update(callback_data="plan_demo:skip_today"))

    telegram_svc.edit_message_text.assert_called_once_with(
        chat_id=999,
        message_id=20,
        text="📋 Tagesplan\n✅ Auswahl gespeichert: Heute auslassen",
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup={
            "inline_keyboard": [[{"text": "🔁 Auswahl ändern", "callback_data": "plan_demo:change_selection"}]]
        },
    )


def test_change_selection_restores_original_buttons_via_message_edit() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = 999
    mock_formatted.text = ""
    mock_formatted.reply_to_message_id = None
    mock_formatted.parse_mode = None
    mock_formatted.disable_web_page_preview = True
    mock_formatted.reply_markup = None
    mock_formatted.callback_query_id = "cbq-1"
    mock_formatted.callback_answer_text = "Auswahl zurückgesetzt"
    mock_formatted.send_response = False
    mock_formatted.edit_message_id = 20
    mock_formatted.edit_message_text = "📋 Tagesplan\n\nHeute posten: ja"
    mock_formatted.edit_reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Heute so ausführen", "callback_data": "plan_demo:execute_today"},
                {"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today"},
            ]
        ]
    }
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    poller._handle_update(_make_callback_update(callback_data="plan_demo:change_selection"))

    telegram_svc.send_message.assert_not_called()
    telegram_svc.answer_callback_query.assert_called_once_with(
        callback_query_id="cbq-1",
        text="Auswahl zurückgesetzt",
    )
    telegram_svc.edit_message_text.assert_called_once_with(
        chat_id=999,
        message_id=20,
        text="📋 Tagesplan\n\nHeute posten: ja",
        parse_mode=None,
        disable_web_page_preview=True,
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "✅ Heute so ausführen", "callback_data": "plan_demo:execute_today"},
                    {"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today"},
                ]
            ]
        },
    )


# ── _handle_update: malformed ─────────────────────────────────────────────────

def test_malformed_update_does_not_crash() -> None:
    ctx = _make_ctx()
    poller, _, request_flow_svc, _ = _make_poller(ctx)

    poller._handle_update({"update_id": 99})  # no message key

    request_flow_svc.handle_telegram_entry_handoff.assert_not_called()


def test_request_flow_exception_does_not_crash_poller() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, _ = _make_poller(ctx)

    request_flow_svc.handle_telegram_entry_handoff.side_effect = RuntimeError("boom")

    poller._handle_update(_make_update(user_id=111))

    telegram_svc.send_message.assert_not_called()


# ── run_until_stopped ─────────────────────────────────────────────────────────

def test_run_until_stopped_processes_update_and_advances_offset() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, request_flow_svc, formatter_svc = _make_poller(ctx)

    update = _make_update(update_id=5, user_id=111)

    call_count = 0

    def fake_get_updates(*, offset=None, timeout=30):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [update]
        stop_event.set()
        return []

    telegram_svc.get_updates.side_effect = fake_get_updates
    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()

    mock_formatted = MagicMock()
    mock_formatted.chat_id = None
    mock_formatted.text = ""
    formatter_svc.format_request_flow_result.return_value = mock_formatted

    stop_event = Event()
    poller.run_until_stopped(stop_event)

    assert request_flow_svc.handle_telegram_entry_handoff.call_count == 1
    # second call should have offset=6
    second_call_kwargs = telegram_svc.get_updates.call_args_list[1][1]
    assert second_call_kwargs["offset"] == 6


def test_run_until_stopped_recovers_from_get_updates_error() -> None:
    ctx = _make_ctx(allowed_user_ids=("111",))
    poller, telegram_svc, _, _ = _make_poller(ctx)

    from operator_core.integrations.telegram_service import TelegramTransportError

    call_count = 0
    stop_event = Event()

    def fake_get_updates(*, offset=None, timeout=30):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TelegramTransportError("timeout")
        stop_event.set()
        return []

    telegram_svc.get_updates.side_effect = fake_get_updates

    # Should not raise despite the transport error
    poller.run_until_stopped(stop_event)
    assert call_count == 2
