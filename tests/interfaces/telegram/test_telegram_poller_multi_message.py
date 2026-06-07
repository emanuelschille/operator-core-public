from pathlib import Path
from unittest.mock import MagicMock, call

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.core.response_formatter.models import AdditionalFormattedResponse, FormattedResponse
from operator_core.interfaces.telegram.poller import TelegramPoller


def _ctx() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="INFO",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(enabled=True, bot_token="tok", allowed_user_ids=("111",), allowed_chat_ids=()),
        airtable=AirtableSettings(enabled=False, api_key="", project_base_ids={"everydayengel": ""}),
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


def test_poller_sends_additional_platform_messages() -> None:
    telegram_svc = MagicMock()
    request_flow_svc = MagicMock()
    formatter_svc = MagicMock()
    poller = TelegramPoller(
        bootstrap_context=_ctx(),
        telegram_service=telegram_svc,
        request_flow_service=request_flow_svc,
        formatter_service=formatter_svc,
    )

    request_flow_svc.handle_telegram_entry_handoff.return_value = MagicMock()
    formatter_svc.format_request_flow_result.return_value = FormattedResponse(
        decision="plan_demo",
        text="📋 Tagesplan · TikTok",
        chat_id=999,
        reply_to_message_id=10,
        reply_markup={"inline_keyboard": [[{"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-tiktok"}]]},
        additional_responses=(
            AdditionalFormattedResponse(
                text="📋 Tagesplan · Instagram",
                reply_markup={"inline_keyboard": [[{"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-instagram"}]]},
            ),
            AdditionalFormattedResponse(
                text="📋 Tagesplan · Facebook",
                reply_markup={"inline_keyboard": [[{"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-facebook"}]]},
            ),
            AdditionalFormattedResponse(
                text="📋 Tagesplan · YouTube",
                reply_markup={"inline_keyboard": [[{"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-youtube"}]]},
            ),
        ),
    )

    poller._handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 111, "username": "testuser"},
                "chat": {"id": 999, "type": "private"},
                "text": "/plan_demo",
            },
        }
    )

    assert telegram_svc.send_message.call_args_list == [
        call(
            chat_id=999,
            text="📋 Tagesplan · TikTok",
            reply_to_message_id=10,
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup={"inline_keyboard": [[{"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-tiktok"}]]},
        ),
        call(
            chat_id=999,
            text="📋 Tagesplan · Instagram",
            reply_to_message_id=None,
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup={"inline_keyboard": [[{"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-instagram"}]]},
        ),
        call(
            chat_id=999,
            text="📋 Tagesplan · Facebook",
            reply_to_message_id=None,
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup={"inline_keyboard": [[{"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-facebook"}]]},
        ),
        call(
            chat_id=999,
            text="📋 Tagesplan · YouTube",
            reply_to_message_id=None,
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup={"inline_keyboard": [[{"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-youtube"}]]},
        ),
    ]
