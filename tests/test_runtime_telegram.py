from __future__ import annotations

from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.runtime import OperatorRuntime


def _make_ctx(*, telegram_enabled: bool = False) -> BootstrapContext:
    tg_kwargs: dict = {"enabled": telegram_enabled}
    if telegram_enabled:
        tg_kwargs.update(
            bot_token="fake-token",
            allowed_user_ids=("123",),
            allowed_chat_ids=(),
        )
    else:
        tg_kwargs.update(
            bot_token="",
            allowed_user_ids=(),
            allowed_chat_ids=(),
        )

    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="INFO",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(**tg_kwargs),
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


def test_runtime_does_not_start_telegram_when_disabled() -> None:
    ctx = _make_ctx(telegram_enabled=False)
    runtime = OperatorRuntime(bootstrap_context=ctx)

    with patch.object(runtime, "_start_telegram_polling") as mock_start:
        runtime.stop_event.set()
        runtime.run()
        mock_start.assert_not_called()


def test_runtime_starts_telegram_when_enabled() -> None:
    ctx = _make_ctx(telegram_enabled=True)
    runtime = OperatorRuntime(bootstrap_context=ctx)

    with patch.object(runtime, "_start_telegram_polling") as mock_start:
        runtime.stop_event.set()
        runtime.run()
        mock_start.assert_called_once()


def test_start_telegram_polling_launches_daemon_thread() -> None:
    ctx = _make_ctx(telegram_enabled=True)
    runtime = OperatorRuntime(bootstrap_context=ctx)

    class FakePoller:
        def run_until_stopped(self, stop_event: Event) -> None:
            stop_event.wait()

    fake_poller = FakePoller()

    with (
        patch("operator_core.interfaces.telegram.poller.TelegramPoller", return_value=fake_poller),
        patch("operator_core.integrations.telegram_service.TelegramService"),
        patch("operator_core.core.request_flow.service.RequestFlowService"),
        patch("operator_core.core.response_formatter.service.ResponseFormatterService"),
        patch("operator_core.core.backbone.execution_service.ExecutionService"),
        patch("operator_core.core.backbone.job_service.JobService"),
        patch("operator_core.core.backbone.run_service.RunService"),
        patch("operator_core.core.backbone.event_log_service.EventLogService"),
        patch("operator_core.core.backbone.repositories.InMemoryJobRepository"),
        patch("operator_core.core.backbone.repositories.InMemoryRunRepository"),
        patch("operator_core.core.backbone.repositories.InMemoryEventRepository"),
        patch("operator_core.projects.docs.ProjectDocsLoader"),
        patch("operator_core.runtime.Thread") as mock_thread_cls,
    ):
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        runtime._start_telegram_polling()

        assert mock_thread_cls.call_count >= 1
        first_call_kwargs = mock_thread_cls.call_args_list[0][1]
        assert first_call_kwargs.get("daemon") is True
        assert first_call_kwargs.get("name") == "telegram-poller"
        assert mock_thread.start.call_count >= 1


def test_start_telegram_polling_registers_slash_commands_before_thread_start() -> None:
    ctx = _make_ctx(telegram_enabled=True)
    runtime = OperatorRuntime(bootstrap_context=ctx)

    class FakePoller:
        def run_until_stopped(self, stop_event: Event) -> None:
            stop_event.wait()

    fake_poller = FakePoller()
    telegram_service = MagicMock()

    with (
        patch("operator_core.interfaces.telegram.poller.TelegramPoller", return_value=fake_poller),
        patch("operator_core.integrations.telegram_service.TelegramService", return_value=telegram_service),
        patch("operator_core.core.request_flow.service.RequestFlowService"),
        patch("operator_core.core.response_formatter.service.ResponseFormatterService"),
        patch("operator_core.core.backbone.execution_service.ExecutionService"),
        patch("operator_core.core.backbone.job_service.JobService"),
        patch("operator_core.core.backbone.run_service.RunService"),
        patch("operator_core.core.backbone.event_log_service.EventLogService"),
        patch("operator_core.core.backbone.repositories.InMemoryJobRepository"),
        patch("operator_core.core.backbone.repositories.InMemoryRunRepository"),
        patch("operator_core.core.backbone.repositories.InMemoryEventRepository"),
        patch("operator_core.projects.docs.ProjectDocsLoader"),
        patch("operator_core.runtime.Thread") as mock_thread_cls,
    ):
        runtime._start_telegram_polling()

        telegram_service.set_my_commands.assert_called_once_with(
            commands=[
                {"command": "menu", "description": "Menü öffnen"},
                {"command": "plan_demo", "description": "Tagesplan ansehen"},
                {"command": "idea", "description": "Eine Idee"},
                {"command": "vollauto", "description": "Voll Auto"},
                {"command": "serie", "description": "Serie/Thema"},
                {"command": "title", "description": "Title"},
                {"command": "hook", "description": "Hook erstellen"},
                {"command": "cta", "description": "CTA erstellen"},
                {"command": "caption", "description": "Caption erstellen"},
                {"command": "status", "description": "📊 Projekt-Stand"},
            ]
        )
        mock_thread_cls.return_value.start.assert_called_once()
