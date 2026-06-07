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


def test_content_ops_result_is_formatter_ready() -> None:
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    request_flow_service = RequestFlowService(execution_service)
    formatter = ResponseFormatterService()

    update_payload = {
        "update_id": 2101,
        "message": {
            "message_id": 3101,
            "text": "/caption tiktok stillkissen routine",
            "chat": {"id": 4101, "type": "private"},
            "from": {"id": 5101, "username": "julia"},
        },
    }

    handoff = build_telegram_entry_handoff(update_payload, build_bootstrap_context())
    result = request_flow_service.handle_telegram_entry_handoff(handoff)
    formatted = formatter.format_request_flow_result(result)

    assert formatted.decision == "executed"
    assert formatted.chat_id == 4101
    assert formatted.reply_to_message_id == 3101
    assert "💬 Caption" in formatted.text


# ── _format_content_ops unit tests ───────────────────────────────────────────

@pytest.mark.parametrize("action_type,expected_header", [
    ("idea", "💡 Idee"),
    ("draft", "📝 Entwurf"),
    ("hook", "🎣 Hook"),
    ("caption", "💬 Caption"),
    ("variant", "🔄 Variante"),
])
def test_content_ops_header_per_action_type(action_type: str, expected_header: str) -> None:
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": action_type,
        "items": [],
    }
    text = formatter._format_content_ops(snapshot, job_id="")
    assert text.startswith(expected_header)


def test_content_ops_unknown_action_type_uses_fallback() -> None:
    formatter = ResponseFormatterService()
    snapshot = {"action_type": "unknown_future_type", "items": []}
    text = formatter._format_content_ops(snapshot, job_id="")
    assert text.startswith("✅ unknown_future_type")


def test_content_ops_items_all_shown() -> None:
    formatter = ResponseFormatterService()
    items = [f"Item {i}" for i in range(6)]
    snapshot = {"action_type": "idea", "items": items, "openai_used": True}
    text = formatter._format_content_ops(snapshot, job_id="")
    for item in items:
        assert item in text


def test_content_ops_docs_fallback_does_not_claim_openai_inactive() -> None:
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "vollauto",
        "items": ["Produktionsreife: klar", "Richtung: alltagsnah"],
        "openai_used": False,
    }
    text = formatter._format_content_ops(snapshot, job_id="")
    assert "OpenAI ist auf dem Server nicht aktiv" not in text
    assert "Produktionsreife: klar" in text


def test_content_ops_trace_footer_with_airtable_and_job() -> None:
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "idea",
        "items": ["Titel: Ein toller Titel"],
        "airtable_record_id": "recABCDEFGH",
        "openai_used": True,
    }
    text = formatter._format_content_ops(snapshot, job_id="job_abc12345678")
    assert "🗂" in text
    assert "recABCDEFGH" in text
    assert "…12345678" in text


def test_content_ops_trace_footer_job_only() -> None:
    formatter = ResponseFormatterService()
    snapshot = {"action_type": "hook", "items": [], "openai_used": True}
    text = formatter._format_content_ops(snapshot, job_id="job_xyz99999999")
    assert "🗂" in text
    assert "…99999999" in text[-30:]


def test_content_ops_no_trace_footer_when_no_ids() -> None:
    formatter = ResponseFormatterService()
    snapshot = {"action_type": "caption", "items": ["Caption: Text hier."]}
    text = formatter._format_content_ops(snapshot, job_id="")
    assert "🗂" not in text


def test_content_ops_items_blank_lines_skipped() -> None:
    formatter = ResponseFormatterService()
    snapshot = {"action_type": "draft", "items": ["", "  ", "Hauptpunkt: Guter Punkt"], "openai_used": True}
    text = formatter._format_content_ops(snapshot, job_id="")
    lines = [l for l in text.splitlines() if "•" in l]
    assert len(lines) == 1
    assert "Hauptpunkt: Guter Punkt" in lines[0]


# ── German key translation ────────────────────────────────────────────────────

def test_idea_pillar_translated_to_saule() -> None:
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "idea",
        "items": ["Titel: Morgenritual", "Pillar: 4.1 Alltag", "Angle: Slow morning"],
        "openai_used": True,
    }
    text = formatter._format_content_ops(snapshot, job_id="")
    assert "Pillar" not in text
    assert "Säule: 4.1 Alltag" in text
    assert "Angle" not in text
    assert "Blickwinkel: Slow morning" in text


def test_idea_angle_translated_to_blickwinkel() -> None:
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "idea",
        "items": ["Angle: Slow morning als Selbstfürsorge"],
        "openai_used": True,
    }
    text = formatter._format_content_ops(snapshot, job_id="")
    assert "Angle" not in text
    assert "Blickwinkel: Slow morning als Selbstfürsorge" in text


def test_draft_body_translated_to_text() -> None:
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "draft",
        "items": ["Hauptpunkt: Kernaussage", "Body: Punkt 1 | Punkt 2"],
        "openai_used": True,
    }
    text = formatter._format_content_ops(snapshot, job_id="")
    assert "Body" not in text
    assert "Text: Punkt 1 | Punkt 2" in text


def test_german_keys_unchanged() -> None:
    """Keys not in the relabel dict must pass through unmodified."""
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "hook",
        "items": [
            "Hook-Typ: Neugier",
            "Eröffnung: Weißt du, was mich jeden Morgen rettet?",
            "Versprechen: In 60 Sekunden zeige ich dir meine Routine.",
        ],
        "openai_used": True,
    }
    text = formatter._format_content_ops(snapshot, job_id="")
    assert "Hook-Typ: Neugier" in text
    assert "Eröffnung:" in text
    assert "Versprechen:" in text


def test_starke_check_translated_to_einschatzung() -> None:
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "hook",
        "items": ["Stärke-Check: Stark genug für TikTok"],
        "openai_used": True,
    }
    text = formatter._format_content_ops(snapshot, job_id="")
    assert "Stärke-Check" not in text
    assert "Einschätzung: Stark genug für TikTok" in text


def test_bereit_check_translated_to_bereit() -> None:
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "draft",
        "items": ["Bereit-Check: Ja, bereit für Produktion"],
        "openai_used": True,
    }
    text = formatter._format_content_ops(snapshot, job_id="")
    assert "Bereit-Check" not in text
    assert "Bereit: Ja, bereit für Produktion" in text


def test_caption_cta_richtung_translated_to_aufruf() -> None:
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "caption",
        "items": [
            "Caption: Das ist mein Morgenritual.",
            "CTA-Richtung: Speichern",
            "Ton-Check: passt",
            "Länge-Check: kurz genug",
        ],
        "openai_used": True,
    }
    text = formatter._format_content_ops(snapshot, job_id="")
    assert "Caption: Das ist mein Morgenritual." in text
    assert "CTA-Richtung" not in text
    assert "Aufruf: Speichern" in text
    assert "Ton-Check: passt" in text
    assert "Länge-Check: kurz genug" in text


def test_no_english_system_words_in_content_ops_reply() -> None:
    """The words that Julia complained about must not appear in content_ops output."""
    formatter = ResponseFormatterService()
    snapshot = {
        "action_type": "idea",
        "items": ["Titel: Test", "Pillar: 4.1", "Angle: morning"],
        "openai_used": True,
    }
    text = formatter._format_content_ops(snapshot, job_id="job_abc12345678")
    for forbidden in ("completed", "content_ops", "Content idea", "Projekt", "Befehl",
                      "Status", "Lane", "Typ", "Job:", "Run:"):
        assert forbidden not in text, f"'{forbidden}' must not appear in content_ops reply"


def test_interactive_followup_snapshot_gets_buttons_without_openai_flag() -> None:
    reply_markup = ResponseFormatterService.build_content_ops_reply_markup(
        {
            "action_type": "caption",
            "proposal_id": "job126b",
            "proposal_interactive": True,
            "openai_used": False,
        }
    )

    assert reply_markup == {
        "inline_keyboard": [
            [
                {"text": "✅ In Tagesplan setzen", "callback_data": "content_ops:apply:job126b"},
                {"text": "✖️ Verwerfen", "callback_data": "content_ops:dismiss:job126b"},
            ],
            [
                {"text": "✍️ Umformulieren", "callback_data": "content_ops:rewrite:job126b"},
                {"text": "🔄 Ersetzen", "callback_data": "content_ops:regenerate:job126b"},
            ],
        ]
    }


def test_idea_snapshot_gets_idea_to_draft_buttons() -> None:
    reply_markup = ResponseFormatterService.build_content_ops_reply_markup(
        {
            "action_type": "idea",
            "proposal_id": "job-idea-1",
            "openai_used": True,
            "items": ["Idee: Morgenroutine kurz gezeigt"],
        }
    )

    assert reply_markup == {
        "inline_keyboard": [
            [
                {"text": "📝 Aus Idee Entwurf erstellen", "callback_data": "content_ops:idea_to_draft:job-idea-1"},
                {"text": "✖️ Verwerfen", "callback_data": "content_ops:dismiss:job-idea-1"},
            ],
            [
                {"text": "✅ Gut", "callback_data": "content_ops:accept:job-idea-1"},
                {"text": "❌ Nicht gut", "callback_data": "content_ops:reject:job-idea-1"},
            ],
        ]
    }


def test_idea_snapshot_without_item_gets_no_action_buttons() -> None:
    reply_markup = ResponseFormatterService.build_content_ops_reply_markup(
        {
            "action_type": "idea",
            "proposal_id": "job-idea-empty",
            "openai_used": True,
            "items": [],
        }
    )

    assert reply_markup is None


def test_rejected_same_core_no_item_fallback_gets_recovery_buttons() -> None:
    reply_markup = ResponseFormatterService.build_content_ops_reply_markup(
        {
            "action_type": "idea",
            "proposal_id": "job-idea-fallback",
            "openai_used": True,
            "items": [],
            "summary": (
                "Diese Idee wurde gerade in fast diesem Kern verworfen. "
                "Gib mir bitte einen neuen Winkel oder nutze /idea Neue Idee."
            ),
        }
    )

    assert reply_markup == {
        "inline_keyboard": [
            [
                {"text": "💡 Frischer", "callback_data": "content_ops:idea_fresh:job-idea-fallback"},
                {"text": "🔁 Neuer Winkel", "callback_data": "content_ops:idea_angle:job-idea-fallback"},
            ],
            [
                {"text": "🗑 Verwerfen", "callback_data": "content_ops:dismiss:job-idea-fallback"},
            ],
        ]
    }


def test_content_ops_no_item_uses_clean_summary_instead_of_generic_empty_text() -> None:
    formatter = ResponseFormatterService()
    summary = (
        "Diese Idee wurde gerade in fast diesem Kern verworfen. "
        "Gib mir bitte einen neuen Winkel oder nutze /idea Neue Idee."
    )
    snapshot = {
        "action_type": "idea",
        "items": [],
        "summary": summary,
        "openai_used": True,
    }

    text = formatter._format_content_ops(snapshot, job_id="")

    assert summary in text
    assert "Kein Vorschlag verfügbar" not in text
