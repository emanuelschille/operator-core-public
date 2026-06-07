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


def test_state_request_flows_through_knowledge_ops() -> None:
    request_flow_service, job_repo, run_repo, event_repo = build_service()
    update_payload = {
        "update_id": 2201,
        "message": {
            "message_id": 3201,
            "text": "/state aktueller stand",
            "chat": {"id": 4201, "type": "private"},
            "from": {"id": 5201, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.execution_result is not None
    assert result.execution_result.result_summary == "Project-State-Stub vorbereitet."
    run = run_repo.get(result.execution_result.run_id)
    assert run is not None
    assert run.output_snapshot["lane_name"] == "knowledge_ops"
    assert run.output_snapshot["action_type"] == "state"
    assert len(event_repo.list_for_entity("everydayengel", "run", run.run_id)) == 3
    assert job_repo.get(result.execution_result.job_id) is not None


def test_rules_request_flows_through_knowledge_ops() -> None:
    request_flow_service, _, run_repo, _ = build_service()
    update_payload = {
        "update_id": 2202,
        "message": {
            "message_id": 3202,
            "text": "rules harte grenzen",
            "chat": {"id": 4202, "type": "private"},
            "from": {"id": 5202, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.execution_result is not None
    assert result.execution_result.result_summary == "Rules-Stub vorbereitet."
    run = run_repo.get(result.execution_result.run_id)
    assert run is not None
    assert run.output_snapshot["action_type"] == "rules"


def test_non_knowledge_known_command_is_not_sent_to_knowledge_ops() -> None:
    request_flow_service, _, run_repo, _ = build_service()
    update_payload = {
        "update_id": 2203,
        "message": {
            "message_id": 3203,
            "text": "/review bitte prüfen",
            "chat": {"id": 4203, "type": "private"},
            "from": {"id": 5203, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.execution_result is not None
    run = run_repo.get(result.execution_result.run_id)
    assert run is not None
    assert run.output_snapshot.get("lane_name") != "knowledge_ops"
