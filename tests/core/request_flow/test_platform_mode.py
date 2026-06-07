from __future__ import annotations

from pathlib import Path

from operator_core.bootstrap import BootstrapContext
from operator_core.config import AirtableSettings, AppSettings, OpenAISettings, Settings, TelegramSettings
from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.execution_service import ExecutionService
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.repositories import (
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryRunRepository,
)
from operator_core.core.backbone.run_service import RunService
from operator_core.core.content_ops.proposal_store import ContentProposal, ContentProposalStore
from operator_core.core.content_ops.platform_mode_store import PlatformModeStore
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.core.response_formatter.service import ResponseFormatterService
from operator_core.interfaces.telegram.entry_flow import build_telegram_entry_handoff


def _ctx() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(env="test", log_level="INFO", runtime_mode="service", active_project="everydayengel"),
        telegram=TelegramSettings(enabled=False, bot_token="", allowed_user_ids=(), allowed_chat_ids=()),
        airtable=AirtableSettings(enabled=False, api_key="", project_base_ids={"everydayengel": ""}),
        openai=OpenAISettings(enabled=False, api_key="", model="gpt-test", base_url="https://api.openai.com/v1", timeout_seconds=30),
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


def _make_service(platform_mode_store: PlatformModeStore | None = None) -> RequestFlowService:
    exec_svc = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    return RequestFlowService(exec_svc, platform_mode_store=platform_mode_store)


def _message_handoff(text: str, chat_id: int = 22, user_id: int = 33) -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": text,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": user_id, "username": "julia"},
            },
        },
        _ctx(),
    )


def _callback_handoff(data: str, chat_id: int = 22, user_id: int = 33) -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": 2,
            "callback_query": {
                "id": "cbq-1",
                "from": {"id": user_id, "username": "julia"},
                "data": data,
                "message": {
                    "message_id": 11,
                    "text": "Modus",
                    "chat": {"id": chat_id, "type": "private"},
                },
            },
        },
        _ctx(),
    )


def test_modus_command_returns_platform_select_markup() -> None:
    svc = _make_service()
    result = svc.handle_telegram_entry_handoff(_message_handoff("/modus"))
    assert result.decision == "modus"
    assert result.formatter_payload.response_reply_markup is not None
    kb = result.formatter_payload.response_reply_markup["inline_keyboard"]
    # First row should have platform buttons
    assert any(
        "platform_mode:set:" in btn["callback_data"]
        for btn in kb[0]
    )


def test_modus_shows_no_active_mode_initially() -> None:
    svc = _make_service()
    result = svc.handle_telegram_entry_handoff(_message_handoff("/modus"))
    assert "Kein Plattform-Modus" in result.formatter_payload.message_text


def test_platform_mode_callback_set_stores_mode() -> None:
    store = PlatformModeStore()
    svc = _make_service(platform_mode_store=store)
    result = svc.handle_telegram_entry_handoff(
        _callback_handoff("platform_mode:set:tiktok")
    )
    assert result.decision == "platform_mode_callback"
    assert store.get_mode(chat_id="22", user_id="33") == "tiktok"
    assert "TikTok" in (result.formatter_payload.edit_message_text or "")


def test_platform_mode_callback_clear_removes_mode() -> None:
    store = PlatformModeStore()
    store.set_mode(chat_id="22", user_id="33", platform="tiktok")
    svc = _make_service(platform_mode_store=store)
    result = svc.handle_telegram_entry_handoff(_callback_handoff("platform_mode:clear"))
    assert result.decision == "platform_mode_callback"
    assert store.get_mode(chat_id="22", user_id="33") is None


def test_content_command_uses_stored_platform_mode() -> None:
    """With platform mode set to TikTok, /vollauto without prefix resolves to tiktok."""
    from operator_core.core.content_ops.models import ContentOpResult

    class _ContentOpsStub:
        last_body: str = ""

        def handle(self, *, project_key, action_type, command_body):
            self.__class__.last_body = command_body
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type=action_type,
                command_body=command_body,
                title="Voll Auto",
                summary="generiert.",
                items=("Serie/Thema: Test", "Title: Testtitel"),
                platform="tiktok",
                openai_used=True,
            )

        def supports(self, action_type):
            return action_type in {"vollauto", "draft"}

        def can_use_foundation_backed_vollauto(self):
            return False

        def resolve_platform_hint(self, command_body):
            return "", command_body

    store = PlatformModeStore()
    store.set_mode(chat_id="22", user_id="33", platform="tiktok")

    exec_svc = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    stub = _ContentOpsStub()
    exec_svc.content_ops_service = stub  # type: ignore[assignment]

    svc = RequestFlowService(exec_svc, platform_mode_store=store)
    result = svc.handle_telegram_entry_handoff(_message_handoff("/vollauto morgenroutine"))

    assert result.was_executed is True
    assert result.decision == "executed"
    # command_body prepended with platform from mode store
    assert "tiktok" in _ContentOpsStub.last_body.lower()


def test_command_without_mode_shows_platform_select() -> None:
    store = PlatformModeStore()
    svc = _make_service(platform_mode_store=store)
    result = svc.handle_telegram_entry_handoff(_message_handoff("/vollauto test"))
    assert result.was_executed is False
    assert result.formatter_payload.response_reply_markup is not None


def test_idea_without_mode_then_platform_selection_replays_original_prompt() -> None:
    from operator_core.core.content_ops.models import ContentOpResult

    class _ContentOpsStub:
        calls: list[tuple[str, str]] = []

        def handle(self, *, project_key, action_type, command_body):
            self.calls.append((action_type, command_body))
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type=action_type,
                command_body=command_body,
                title="Idee",
                summary="generiert.",
                items=("Idee: Beim Kochen merke ich, dass ich mich hinsetzen muss.",),
                platform="tiktok",
                openai_used=True,
            )

        def supports(self, action_type):
            return action_type == "idea"

        def can_use_foundation_backed_idea(self):
            return False

        def resolve_platform_hint(self, command_body):
            return "", command_body

    store = PlatformModeStore()
    exec_svc = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    stub = _ContentOpsStub()
    exec_svc.content_ops_service = stub  # type: ignore[assignment]
    svc = RequestFlowService(exec_svc, platform_mode_store=store)

    prompt_result = svc.handle_telegram_entry_handoff(
        _message_handoff("/idea beim Kochen plötzlich sitzen wegen Schwindel")
    )

    assert prompt_result.was_executed is False
    assert prompt_result.formatter_payload.response_reply_markup is not None
    keyboard = prompt_result.formatter_payload.response_reply_markup["inline_keyboard"]
    assert any("platform_mode:continue:tiktok" == btn["callback_data"] for btn in keyboard[0])

    replay_result = svc.handle_telegram_entry_handoff(_callback_handoff("platform_mode:continue:tiktok"))

    assert replay_result.decision == "executed"
    assert replay_result.was_executed is True
    assert replay_result.formatter_payload.edit_message_text == "✅ Plattform gewählt: TikTok"
    assert replay_result.formatter_payload.response_chat_id == 22
    assert replay_result.formatter_payload.response_reply_to_message_id is None
    assert stub.calls == [("idea", "tiktok beim Kochen plötzlich sitzen wegen Schwindel")]
    assert store.get_mode(chat_id="22", user_id="33") == "tiktok"

    duplicate_result = svc.handle_telegram_entry_handoff(_callback_handoff("platform_mode:continue:tiktok"))
    assert duplicate_result.was_executed is False
    assert "abgelaufen" in (duplicate_result.formatter_payload.edit_message_text or "")
    assert stub.calls == [("idea", "tiktok beim Kochen plötzlich sitzen wegen Schwindel")]


def test_modus_shows_current_active_mode() -> None:
    store = PlatformModeStore()
    store.set_mode(chat_id="22", user_id="33", platform="instagram_reel")
    svc = _make_service(platform_mode_store=store)
    result = svc.handle_telegram_entry_handoff(_message_handoff("/modus"))
    assert "Instagram" in result.formatter_payload.message_text


def test_platform_callback_continues_pending_idea_request() -> None:
    from operator_core.core.content_ops.models import ContentOpResult

    class _ContentOpsStub:
        calls: list[tuple[str, str]] = []

        def handle(self, *, project_key, action_type, command_body):
            self.calls.append((action_type, command_body))
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type=action_type,
                command_body=command_body,
                title="Idee",
                summary="generiert.",
                items=("Idee: Testidee",),
                platform="youtube_short",
                openai_used=True,
            )

        def supports(self, action_type):
            return action_type in {"idea", "vollauto", "draft", "serie", "title", "hook", "cta", "caption"}

        def can_use_foundation_backed_idea(self):
            return False

        def resolve_platform_hint(self, command_body):
            return "", command_body

    store = PlatformModeStore()
    exec_svc = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    stub = _ContentOpsStub()
    exec_svc.content_ops_service = stub  # type: ignore[assignment]
    svc = RequestFlowService(exec_svc, platform_mode_store=store)

    prompt_result = svc.handle_telegram_entry_handoff(_message_handoff("💡 Neue Idee"))
    assert prompt_result.was_executed is False
    kb = prompt_result.formatter_payload.response_reply_markup["inline_keyboard"]
    assert any("platform_mode:continue:" in btn["callback_data"] for btn in kb[0])

    result = svc.handle_telegram_entry_handoff(_callback_handoff("platform_mode:continue:youtube_short"))

    assert result.decision == "executed"
    assert result.was_executed is True
    assert stub.calls == [("idea", "youtube_short")]
    assert store.get_mode(chat_id="22", user_id="33") == "youtube_short"


def test_platform_callback_continues_pending_hook_request_with_body() -> None:
    from operator_core.core.content_ops.models import ContentOpResult

    class _ContentOpsStub:
        calls: list[tuple[str, str]] = []

        def handle(self, *, project_key, action_type, command_body):
            self.calls.append((action_type, command_body))
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type=action_type,
                command_body=command_body,
                title="Hook",
                summary="generiert.",
                items=("Hook: Testhook",),
                platform="tiktok",
                openai_used=True,
            )

        def supports(self, action_type):
            return True

        def can_use_foundation_backed_hook(self):
            return False

        def can_use_foundation_backed_idea(self):
            return False

        def resolve_platform_hint(self, command_body):
            return "", command_body

    exec_svc = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    stub = _ContentOpsStub()
    exec_svc.content_ops_service = stub  # type: ignore[assignment]
    svc = RequestFlowService(exec_svc, platform_mode_store=PlatformModeStore())

    svc.handle_telegram_entry_handoff(_message_handoff("/hook morgenroutine"))
    result = svc.handle_telegram_entry_handoff(_callback_handoff("platform_mode:continue:tiktok"))

    assert result.decision == "executed"
    assert stub.calls == [("hook", "tiktok morgenroutine")]


def test_explicit_modus_selection_does_not_trigger_pending_action() -> None:
    store = PlatformModeStore()
    svc = _make_service(platform_mode_store=store)

    svc.handle_telegram_entry_handoff(_message_handoff("/vollauto test"))
    svc.handle_telegram_entry_handoff(_message_handoff("/modus"))
    result = svc.handle_telegram_entry_handoff(_callback_handoff("platform_mode:set:facebook_reel"))

    assert result.decision == "platform_mode_callback"
    assert result.was_executed is False
    assert "Plattform-Modus gesetzt" in (result.formatter_payload.edit_message_text or "")


def test_continue_callback_without_pending_does_not_set_mode() -> None:
    store = PlatformModeStore()
    svc = _make_service(platform_mode_store=store)

    result = svc.handle_telegram_entry_handoff(_callback_handoff("platform_mode:continue:youtube_short"))

    assert result.decision == "platform_mode_callback"
    assert result.was_executed is False
    assert store.get_mode(chat_id="22", user_id="33") is None
    assert "abgelaufen" in (result.formatter_payload.edit_message_text or "")


def test_content_command_without_mode_does_not_reuse_active_proposal_platform() -> None:
    from operator_core.core.content_ops.models import ContentOpResult

    class _ContentOpsStub:
        calls: list[tuple[str, str]] = []

        def handle(self, *, project_key, action_type, command_body):
            self.calls.append((action_type, command_body))
            return ContentOpResult(
                lane_name="content_ops",
                project_key=project_key,
                action_type=action_type,
                command_body=command_body,
                title="Idee",
                summary="generiert.",
                items=("Idee: Testidee",),
                platform="youtube_short",
                openai_used=True,
            )

        def supports(self, action_type):
            return True

        def can_use_foundation_backed_idea(self):
            return False

        def resolve_platform_hint(self, command_body):
            return "", command_body

    exec_svc = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    stub = _ContentOpsStub()
    exec_svc.content_ops_service = stub  # type: ignore[assignment]
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="proposal-1",
            project_key="everydayengel",
            action_type="caption",
            platform="youtube_short",
            fields={"caption": "Alt"},
            chat_id="22",
            user_id="33",
        )
    )
    svc = RequestFlowService(
        exec_svc,
        platform_mode_store=PlatformModeStore(),
        content_proposal_store=proposal_store,
    )

    result = svc.handle_telegram_entry_handoff(_message_handoff("💡 Neue Idee"))

    assert result.was_executed is False
    assert result.formatter_payload.response_reply_markup is not None
    assert stub.calls == []
