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
from operator_core.core.backbone.execution_service import ExecutionService, ExecutionStepResult
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.repositories import (
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryRunRepository,
)
from operator_core.core.backbone.run_service import RunService
from operator_core.core.backbone.statuses import JobStatus
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.core.response_formatter.service import ResponseFormatterService
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


def build_request_flow_service(executor=None) -> RequestFlowService:
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
        executor=executor,
    )
    return RequestFlowService(execution_service)


def build_result(text: str, executor=None):
    update_payload = {
        "update_id": 1001,
        "message": {
            "message_id": 2002,
            "text": text,
            "chat": {"id": 3003, "type": "private"},
            "from": {"id": 4004, "username": "julia"},
        },
    }
    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    return build_request_flow_service(executor=executor).handle_telegram_entry_handoff(handoff)


def test_executed_result_is_formatted_for_telegram() -> None:
    formatter = ResponseFormatterService()
    result = build_result("neu erster test")

    formatted = formatter.format_request_flow_result(result)

    assert formatted.decision == "executed"
    assert formatted.chat_id == 3003
    assert formatted.reply_to_message_id == 2002
    assert "✅ Anfrage verarbeitet" in formatted.text
    assert "Projekt: Everyday Engel" in formatted.text
    assert "Befehl: neu" in formatted.text
    assert "Job:" in formatted.text
    assert "Lauf:" in formatted.text
    assert formatted.reply_markup == {
        "keyboard": [
            [{"text": "📋 Tagesplan"}, {"text": "💡 Neue Idee"}],
            [{"text": "📝 Voll Auto"}, {"text": "🎯 Modus"}],
            [{"text": "☰ Menü"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }

def test_menu_result_keeps_inline_buttons_instead_of_default_keyboard() -> None:
    formatter = ResponseFormatterService()
    result = build_result("☰ Menü")

    formatted = formatter.format_request_flow_result(result)

    assert formatted.decision == "menu"
    assert formatted.text == "⌨️ Menü wird aktualisiert."
    assert formatted.reply_markup == {"remove_keyboard": True}
    assert formatted.additional_responses[0].text == "⌨️ Menü aktualisiert."
    assert formatted.additional_responses[0].reply_markup == {
        "keyboard": [
            [{"text": "📋 Tagesplan"}, {"text": "💡 Neue Idee"}],
            [{"text": "📝 Voll Auto"}, {"text": "🎯 Modus"}],
            [{"text": "☰ Menü"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }
    assert formatted.additional_responses[1].text == "☰ Menü\n\nWähle eine Aktion."
    assert formatted.additional_responses[1].reply_markup == {
        "inline_keyboard": [
            [
                {"text": "📋 Tagesplan", "callback_data": "menu:plan"},
                {"text": "📊 Status", "callback_data": "menu:status"},
            ],
            [
                {"text": "💡 Idee", "callback_data": "menu:idea"},
                {"text": "📝 Voll Auto", "callback_data": "menu:vollauto"},
            ],
            [
                {"text": "🧩 Serie/Thema", "callback_data": "menu:serie"},
                {"text": "🏷️ Title", "callback_data": "menu:title"},
            ],
            [
                {"text": "🎣 Hook erstellen", "callback_data": "menu:hook"},
                {"text": "🪝 CTA erstellen", "callback_data": "menu:cta"},
            ],
            [
                {"text": "💬 Caption erstellen", "callback_data": "menu:caption"},
                {"text": "🎯 Modus", "callback_data": "menu:modus"},
            ],
        ]
    }

def test_unknown_command_is_formatted_cleanly() -> None:
    formatter = ResponseFormatterService()
    result = build_result("/nonsense bitte")

    formatted = formatter.format_request_flow_result(result)

    assert formatted.decision == "unknown_command"
    assert "⚠️ Unbekannter Befehl" in formatted.text
    assert "Befehl: nonsense" in formatted.text
    assert "Nutze /menu oder den Button ☰ Menü." in formatted.text


def test_non_command_is_formatted_as_action_selection() -> None:
    formatter = ResponseFormatterService()
    result = build_result("hallo zusammen")

    formatted = formatter.format_request_flow_result(result)

    assert formatted.decision == "free_text_selection"
    assert "📝 Eingabe erkannt" in formatted.text
    assert "Wofür möchtest du das verwenden?" in formatted.text
    assert formatted.reply_markup == {
        "inline_keyboard": [
            [
                {"text": "💡 Idee generieren", "callback_data": "text_action:idea"},
                {"text": "📝 Voll Auto", "callback_data": "text_action:vollauto"},
            ],
            [
                {"text": "🧩 Serie/Thema", "callback_data": "text_action:serie"},
                {"text": "🏷️ Title", "callback_data": "text_action:title"},
            ],
            [
                {"text": "🎣 Hook erstellen", "callback_data": "text_action:hook"},
                {"text": "🪝 CTA erstellen", "callback_data": "text_action:cta"},
            ],
            [
                {"text": "💬 Caption erstellen", "callback_data": "text_action:caption"},
            ],
            [
                {"text": "✖️ Abbrechen", "callback_data": "text_action:cancel"},
            ],
        ]
    }



def test_failure_result_is_formatted_cleanly() -> None:
    def failing_executor(request_context, job, run):
        raise RuntimeError("simulierter fehler")

    formatter = ResponseFormatterService()
    result = build_result("neu bitte scheitern", executor=failing_executor)

    formatted = formatter.format_request_flow_result(result)

    assert formatted.decision == "executed"
    assert "❌ Anfrage fehlgeschlagen" in formatted.text
    assert "Status: fehlgeschlagen" in formatted.text
    assert "Fehler: simulierter fehler" in formatted.text


def test_waiting_for_input_result_is_formatted_cleanly() -> None:
    def waiting_executor(request_context, job, run):
        return ExecutionStepResult(
            output_snapshot={"state": "awaiting_user"},
            result_summary="Bitte kurz bestätigen.",
            job_status=JobStatus.WAITING_FOR_INPUT,
        )

    formatter = ResponseFormatterService()
    result = build_result("neu bitte prüfen", executor=waiting_executor)

    formatted = formatter.format_request_flow_result(result)

    assert formatted.decision == "executed"
    assert "🟡 Anfrage erfasst" in formatted.text
    assert "Status: wartet auf Rückmeldung" in formatted.text
    assert "Hinweis: Bitte kurz bestätigen." in formatted.text


def test_status_result_formats_commercial_mix_compactly() -> None:
    def status_executor(request_context, job, run):
        return ExecutionStepResult(
            output_snapshot={
                "status_type": "commercial_mix",
                "window_days": 7,
                "total": 6,
                "commercial_mix": {
                    "trust_building": 4,
                    "product_near": 2,
                    "recommendation_ready": 0,
                    "direct_offer": 0,
                    "off_thesis_or_monetization_waste": 0,
                },
                "drift_hint": "Seit einiger Zeit gab es keine konkrete Empfehlung. Prüfe, ob ein passendes Produkt authentisch in einen Moment passt.",
            }
        )

    formatter = ResponseFormatterService()
    result = build_result("/status", executor=status_executor)

    formatted = formatter.format_request_flow_result(result)

    assert "📊 Status" in formatted.text
    assert "Letzte 7 Tage: 6 Inhalte eingeordnet" in formatted.text
    assert "• Vertrauensaufbau: 4" in formatted.text
    assert "• Produktnah: 2" in formatted.text
    assert "• Empfehlungsbereit: 0" in formatted.text
    assert "Hinweis:" in formatted.text
    assert "Seit einiger Zeit gab es keine konkrete Empfehlung." in formatted.text

def test_format_content_ops_includes_model_footer():
    """Footer should include the actual model name used."""
    svc = ResponseFormatterService()
    snapshot = {
        "lane_name": "content_ops",
        "action_type": "idea",
        "items": ["Idee: Test Idea"],
        "airtable_record_id": "rec123",
        "model_name": "gpt-5.4-special"
    }
    
    text = svc._format_content_ops(snapshot, job_id="job_abcd_12345678")
    
    # Check for trace line
    assert "🗂 rec123 · …12345678 · gpt-5.4-special" in text
