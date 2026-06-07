from pathlib import Path

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.execution_service import ExecutionService
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.repositories import (
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryRunRepository,
)
from operator_core.core.backbone.run_service import RunService
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.interfaces.telegram.entry_flow import build_telegram_entry_handoff


def build_bootstrap_context() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="dev",
            log_level="INFO",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(
            enabled=False,
            bot_token="",
            allowed_user_ids=(),
            allowed_chat_ids=(),
        ),
        airtable=AirtableSettings(
            enabled=False,
            api_key="",
            project_base_ids={"everydayengel": ""},
        ),
        openai=OpenAISettings(
            enabled=False,
            api_key="",
            model="gpt-5",
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


def build_service():
    job_repo = InMemoryJobRepository()
    run_repo = InMemoryRunRepository()
    event_repo = InMemoryEventRepository()
    execution_service = ExecutionService(
        job_service=JobService(job_repo),
        run_service=RunService(run_repo),
        event_log_service=EventLogService(event_repo),
    )
    return RequestFlowService(execution_service), job_repo, run_repo, event_repo


def test_idea_request_flows_through_content_ops() -> None:
    request_flow_service, job_repo, run_repo, event_repo = build_service()
    update_payload = {
        "update_id": 2001,
        "message": {
            "message_id": 3001,
            "text": "/idea morgenroutine video",
            "chat": {"id": 4001, "type": "private"},
            "from": {"id": 5001, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is False
    assert result.execution_result is None
    assert result.decision == "not_a_command"
    assert "Fuer /idea bitte Plattform waehlen" in result.formatter_payload.message_text
    assert result.formatter_payload.response_reply_markup is not None
    keyboard = result.formatter_payload.response_reply_markup["inline_keyboard"]
    assert any("platform_mode:continue:" in button["callback_data"] for button in keyboard[0])
    assert run_repo.list_by_job("missing") == []
    assert job_repo.list_by_project("everydayengel") == []
    assert len(event_repo.list_for_entity("everydayengel", "run", "missing")) == 0


def test_caption_request_flows_through_content_ops() -> None:
    request_flow_service, _, run_repo, _ = build_service()
    update_payload = {
        "update_id": 2002,
        "message": {
            "message_id": 3002,
            "text": "caption baby trage alltag",
            "chat": {"id": 4002, "type": "private"},
            "from": {"id": 5002, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is False
    assert result.execution_result is None
    assert result.decision == "not_a_command"
    assert "Plattform waehlen" in result.formatter_payload.message_text
    assert result.formatter_payload.response_reply_markup is not None
    assert run_repo.list_by_job("missing") == []


def test_non_content_known_command_is_not_sent_to_content_ops() -> None:
    request_flow_service, _, run_repo, _ = build_service()
    update_payload = {
        "update_id": 2003,
        "message": {
            "message_id": 3003,
            "text": "/review bitte prüfen",
            "chat": {"id": 4003, "type": "private"},
            "from": {"id": 5003, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.execution_result is not None
    run = run_repo.get(result.execution_result.run_id)
    assert run is not None
    assert "lane_name" not in run.output_snapshot
    assert result.execution_result.result_summary is not None
    assert result.execution_result.result_summary.startswith("Processed request ")
