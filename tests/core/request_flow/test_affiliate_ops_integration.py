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


def test_offer_match_request_flows_through_affiliate_ops() -> None:
    request_flow_service, job_repo, run_repo, event_repo = build_service()
    update_payload = {
        "update_id": 2401,
        "message": {
            "message_id": 3401,
            "text": "/offer_match stillkissen partnerprogramm",
            "chat": {"id": 4401, "type": "private"},
            "from": {"id": 5401, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.execution_result is not None
    assert result.execution_result.result_summary == "Offer-Match-Stub vorbereitet."
    run = run_repo.get(result.execution_result.run_id)
    assert run is not None
    assert run.output_snapshot["lane_name"] == "affiliate_ops"
    assert run.output_snapshot["action_type"] == "offer_match"
    assert len(event_repo.list_for_entity("everydayengel", "run", run.run_id)) == 3
    assert job_repo.get(result.execution_result.job_id) is not None


def test_product_fit_request_flows_through_affiliate_ops() -> None:
    request_flow_service, _, run_repo, _ = build_service()
    update_payload = {
        "update_id": 2402,
        "message": {
            "message_id": 3402,
            "text": "product_fit baby trage alltag",
            "chat": {"id": 4402, "type": "private"},
            "from": {"id": 5402, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.execution_result is not None
    assert result.execution_result.result_summary == "Product-Fit-Stub vorbereitet."
    run = run_repo.get(result.execution_result.run_id)
    assert run is not None
    assert run.output_snapshot["action_type"] == "product_fit"


def test_non_affiliate_known_command_is_not_sent_to_affiliate_ops() -> None:
    request_flow_service, _, run_repo, _ = build_service()
    update_payload = {
        "update_id": 2403,
        "message": {
            "message_id": 3403,
            "text": "/rules nur harte grenzen",
            "chat": {"id": 4403, "type": "private"},
            "from": {"id": 5403, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)

    assert result.was_executed is True
    assert result.execution_result is not None
    run = run_repo.get(result.execution_result.run_id)
    assert run is not None
    assert run.output_snapshot.get("lane_name") != "affiliate_ops"
