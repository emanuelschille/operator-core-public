"""
Tests for /idea, /draft, /hook, and /caption + OperationalKnowledgeLoader integration.

Covers (/idea):
  - when OK loader is wired and returns rows: system prompt contains OK block
  - when OK loader is None: system prompt is unchanged (regression guard)
  - when OK loader returns empty context: system prompt is unchanged
  - when OK loader raises: /idea still produces a result (graceful fallback)

Covers (/draft):
  - when OK loader is wired and returns rows: system prompt contains OK block
  - when OK loader is None: system prompt is unchanged (regression guard)
  - when OK loader returns empty context: system prompt is unchanged
  - when OK loader raises: /draft still produces a result (graceful fallback)

Covers (/hook):
  - when OK loader is wired and returns rows: system prompt contains OK block
  - when OK loader is None: system prompt is unchanged (regression guard)
  - when OK loader returns empty context: system prompt is unchanged
  - when OK loader raises: /hook still produces a result (graceful fallback)

Covers (/caption):
  - when OK loader is wired and returns rows: system prompt contains OK block
  - when OK loader is None: system prompt is unchanged (regression guard)
  - when OK loader returns empty context: system prompt is unchanged
  - when OK loader raises: /caption still produces a result (graceful fallback)
"""
from __future__ import annotations

from typing import Any

import pytest

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.core.content_ops.service import ContentOpsService
from operator_core.integrations.airtable_service import AirtableService
from operator_core.integrations.openai_service import OpenAIService
from operator_core.integrations.operational_knowledge_service import (
    OperationalKnowledgeLoader,
)
from operator_core.projects.docs import ProjectDocsLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IDEA_RESPONSE = (
    "Titel: Morgenroutine ohne Stress\n"
    "Pillar: Kleine Routinen\n"
    "Angle: Julia zeigt ihre 10-Minuten-Routine\n"
    "Hook: Was wäre wenn dein Morgen besser läuft?\n"
    "Format: Direct-to-camera"
)

_DRAFT_RESPONSE = (
    "Hauptpunkt: Morgenroutine in 10 Minuten\n"
    "Hook: Was wenn dein Morgen ruhiger wäre?\n"
    "Body: Drei einfache Schritte\n"
    "CTA-Richtung: Soft empfehlen\n"
    "Format: Direct-to-camera\n"
    "Bereit-Check: Produktionsreif"
)

_HOOK_RESPONSE = (
    "Hook-Typ: Neugier\n"
    "Eröffnung: Was wäre wenn dein Morgen ohne Chaos startet?\n"
    "Versprechen: Drei kleine Schritte machen deinen Start sofort klarer\n"
    "Format: Direct-to-camera\n"
    "Stärke-Check: Neugier plus klares Versprechen"
)

_CAPTION_RESPONSE = (
    "Caption: Kleine Veränderungen machen den Morgen oft leichter.\n"
    "CTA-Richtung: Wiedererkennung\n"
    "Ton-Check: Natürlich und direkt\n"
    "Länge-Check: Kurz genug für Reels"
)

_OK_RECORDS = [
    {
        "id": "recA",
        "fields": {
            "Key": "content_priority_current",
            "Label": "Aktueller Inhaltsfokus",
            "Value": "Alltag mit Wiedererkennungswert.",
            "Category": "priorities",
            "Status": "active",
        },
    },
    {
        "id": "recB",
        "fields": {
            "Key": "platform_primary",
            "Label": "Hauptplattform",
            "Value": "TikTok und Instagram Reels.",
            "Category": "platform",
            "Status": "active",
        },
    },
]


def _make_bootstrap(*, openai_enabled: bool = True) -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="WARNING",
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
            enabled=True,
            api_key="pat-test",
            project_base_ids={"everydayengel": "appTestBase123"},
        ),
        openai=OpenAISettings(
            enabled=openai_enabled,
            api_key="sk-test" if openai_enabled else "",
            model="gpt-test",
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )
    return BootstrapContext(
        settings=settings,
        runtime_path=__import__("pathlib").Path("projects/everydayengel/runtime.yaml"),
        project_runtime={
            "project_key": "everydayengel",
            "display_name": "everydayengel",
            "status": "active",
            "primary_interface": "telegram",
            "human_in_the_loop": "true",
        },
    )


def _make_capturing_openai_transport(captured: dict[str, str]):
    """Returns a transport that captures the system prompt and returns a valid idea response."""
    def transport(method, url, headers, body, timeout):
        for msg in (body.get("input") or []):
            if msg.get("role") == "system":
                for block in (msg.get("content") or []):
                    if block.get("type") == "input_text":
                        captured["system_prompt"] = block.get("text", "")
        return 200, {
            "model": "gpt-test",
            "output": [{"content": [{"type": "output_text", "text": _IDEA_RESPONSE}]}],
        }
    return transport


def _ok_transport_ok(method, url, headers, body):
    return 200, {"records": _OK_RECORDS}


def _ok_transport_empty(method, url, headers, body):
    return 200, {"records": []}


def _ok_transport_error(method, url, headers, body):
    return 500, {"error": {"message": "server error"}}


# ---------------------------------------------------------------------------
# Tests: OK block is injected into system prompt when loader is wired
# ---------------------------------------------------------------------------

def test_idea_system_prompt_contains_ok_block_when_loader_wired(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_openai_transport(captured))
    airtable_svc = AirtableService(ctx, transport=_ok_transport_ok)
    ok_loader = OperationalKnowledgeLoader(airtable_svc)

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    service.handle(project_key="everydayengel", action_type="idea", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln (aktuell bindend):" in prompt
    assert "Aktueller Inhaltsfokus: Alltag mit Wiedererkennungswert." in prompt
    assert "Hauptplattform: TikTok und Instagram Reels." in prompt


def test_idea_ok_block_appears_between_context_and_task(
    docs_loader: "ProjectDocsLoader",
) -> None:
    """The OK block must sit between Projekt-Kontext and Aufgabe."""
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_openai_transport(captured))
    airtable_svc = AirtableService(ctx, transport=_ok_transport_ok)
    ok_loader = OperationalKnowledgeLoader(airtable_svc)

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    service.handle(project_key="everydayengel", action_type="idea", command_body="test")

    prompt = captured.get("system_prompt", "")
    ok_pos = prompt.find("Operative Wissensregeln")
    context_pos = prompt.find("Projekt-Kontext")
    aufgabe_pos = prompt.find("Aufgabe:")

    assert context_pos != -1
    assert ok_pos != -1
    assert aufgabe_pos != -1
    assert context_pos < ok_pos < aufgabe_pos


# ---------------------------------------------------------------------------
# Tests: no OK block when loader is None
# ---------------------------------------------------------------------------

def test_idea_system_prompt_has_no_ok_block_when_loader_is_none(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_openai_transport(captured))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        # no operational_knowledge_loader
    )
    service.handle(project_key="everydayengel", action_type="idea", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln" not in prompt


# ---------------------------------------------------------------------------
# Tests: no OK block when loader returns empty context
# ---------------------------------------------------------------------------

def test_idea_system_prompt_has_no_ok_block_when_table_empty(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_openai_transport(captured))
    airtable_svc = AirtableService(ctx, transport=_ok_transport_empty)
    ok_loader = OperationalKnowledgeLoader(airtable_svc)

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    service.handle(project_key="everydayengel", action_type="idea", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln" not in prompt


# ---------------------------------------------------------------------------
# Tests: loader Airtable error → idea still generates normally
# ---------------------------------------------------------------------------

def test_idea_still_generates_when_ok_loader_airtable_fails(
    docs_loader: "ProjectDocsLoader",
) -> None:
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(
        ctx,
        transport=lambda m, u, h, b, t: (
            200,
            {"model": "gpt-test", "output": [{"content": [{"type": "output_text", "text": _IDEA_RESPONSE}]}]},
        ),
    )
    airtable_svc = AirtableService(ctx, transport=_ok_transport_error)
    ok_loader = OperationalKnowledgeLoader(airtable_svc)

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    result = service.handle(project_key="everydayengel", action_type="idea", command_body="test")

    assert result.summary == "Idee generiert."
    assert result.openai_used is True


def _make_capturing_draft_transport(captured: dict[str, str]):
    """Returns a transport that captures the system prompt and returns a valid draft response."""
    def transport(method, url, headers, body, timeout):
        for msg in (body.get("input") or []):
            if msg.get("role") == "system":
                for block in (msg.get("content") or []):
                    if block.get("type") == "input_text":
                        captured["system_prompt"] = block.get("text", "")
        return 200, {
            "model": "gpt-test",
            "output": [{"content": [{"type": "output_text", "text": _DRAFT_RESPONSE}]}],
        }
    return transport


def _make_capturing_hook_transport(captured: dict[str, str]):
    """Returns a transport that captures the system prompt and returns a valid hook response."""
    def transport(method, url, headers, body, timeout):
        for msg in (body.get("input") or []):
            if msg.get("role") == "system":
                for block in (msg.get("content") or []):
                    if block.get("type") == "input_text":
                        captured["system_prompt"] = block.get("text", "")
        return 200, {
            "model": "gpt-test",
            "output": [{"content": [{"type": "output_text", "text": _HOOK_RESPONSE}]}],
        }
    return transport


def _make_capturing_caption_transport(captured: dict[str, str]):
    """Returns a transport that captures the system prompt and returns a valid caption response."""
    def transport(method, url, headers, body, timeout):
        for msg in (body.get("input") or []):
            if msg.get("role") == "system":
                for block in (msg.get("content") or []):
                    if block.get("type") == "input_text":
                        captured["system_prompt"] = block.get("text", "")
        return 200, {
            "model": "gpt-test",
            "output": [{"content": [{"type": "output_text", "text": _CAPTION_RESPONSE}]}],
        }
    return transport


# ---------------------------------------------------------------------------
# Tests: /draft OK injection — OK block injected when loader wired
# ---------------------------------------------------------------------------

def test_draft_system_prompt_contains_ok_block_when_loader_wired(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_draft_transport(captured))
    airtable_svc = AirtableService(ctx, transport=_ok_transport_ok)
    ok_loader = OperationalKnowledgeLoader(airtable_svc)

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    service.handle(project_key="everydayengel", action_type="draft", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln (aktuell bindend):" in prompt
    assert "Aktueller Inhaltsfokus: Alltag mit Wiedererkennungswert." in prompt


def test_draft_system_prompt_has_no_ok_block_when_loader_is_none(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_draft_transport(captured))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        # no operational_knowledge_loader
    )
    service.handle(project_key="everydayengel", action_type="draft", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln" not in prompt


def test_draft_system_prompt_has_no_ok_block_when_table_empty(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_draft_transport(captured))
    airtable_svc = AirtableService(ctx, transport=_ok_transport_empty)
    ok_loader = OperationalKnowledgeLoader(airtable_svc)

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    service.handle(project_key="everydayengel", action_type="draft", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln" not in prompt


def test_draft_still_generates_when_ok_loader_airtable_fails(
    docs_loader: "ProjectDocsLoader",
) -> None:
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(
        ctx,
        transport=lambda m, u, h, b, t: (
            200,
            {"model": "gpt-test", "output": [{"content": [{"type": "output_text", "text": _DRAFT_RESPONSE}]}]},
        ),
    )
    airtable_svc = AirtableService(ctx, transport=_ok_transport_error)
    ok_loader = OperationalKnowledgeLoader(airtable_svc)

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    result = service.handle(project_key="everydayengel", action_type="draft", command_body="test")

    assert result.summary == "Entwurf generiert."
    assert result.openai_used is True


# ---------------------------------------------------------------------------
# Tests: /hook OK injection
# ---------------------------------------------------------------------------

def test_draft_completes_normally_when_ok_loader_wired(docs_loader: "ProjectDocsLoader") -> None:
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(
        ctx,
        transport=lambda m, u, h, b, t: (
            200,
            {"model": "gpt-test", "output": [{"content": [{"type": "output_text", "text": _DRAFT_RESPONSE}]}]},
        ),
    )
    ok_loader = OperationalKnowledgeLoader(AirtableService(ctx, transport=_ok_transport_ok))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    result = service.handle(project_key="everydayengel", action_type="draft", command_body="test")
    assert result.action_type == "draft"
    assert result.openai_used is True


def test_hook_system_prompt_contains_ok_block_when_loader_wired(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_hook_transport(captured))
    ok_loader = OperationalKnowledgeLoader(AirtableService(ctx, transport=_ok_transport_ok))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    service.handle(project_key="everydayengel", action_type="hook", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln (aktuell bindend):" in prompt
    assert "Aktueller Inhaltsfokus: Alltag mit Wiedererkennungswert." in prompt


def test_hook_system_prompt_has_no_ok_block_when_loader_is_none(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_hook_transport(captured))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
    )
    service.handle(project_key="everydayengel", action_type="hook", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln" not in prompt


def test_hook_system_prompt_has_no_ok_block_when_table_empty(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_hook_transport(captured))
    ok_loader = OperationalKnowledgeLoader(AirtableService(ctx, transport=_ok_transport_empty))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    service.handle(project_key="everydayengel", action_type="hook", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln" not in prompt


def test_hook_still_generates_when_ok_loader_airtable_fails(
    docs_loader: "ProjectDocsLoader",
) -> None:
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(
        ctx,
        transport=lambda m, u, h, b, t: (
            200,
            {"model": "gpt-test", "output": [{"content": [{"type": "output_text", "text": _HOOK_RESPONSE}]}]},
        ),
    )
    ok_loader = OperationalKnowledgeLoader(AirtableService(ctx, transport=_ok_transport_error))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    result = service.handle(project_key="everydayengel", action_type="hook", command_body="test")

    assert result.summary == "Hook generiert."
    assert result.openai_used is True


# ---------------------------------------------------------------------------
# Tests: /caption OK injection
# ---------------------------------------------------------------------------

def test_caption_system_prompt_contains_ok_block_when_loader_wired(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_caption_transport(captured))
    ok_loader = OperationalKnowledgeLoader(AirtableService(ctx, transport=_ok_transport_ok))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    service.handle(project_key="everydayengel", action_type="caption", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln (aktuell bindend):" in prompt
    assert "Aktueller Inhaltsfokus: Alltag mit Wiedererkennungswert." in prompt


def test_caption_system_prompt_has_no_ok_block_when_loader_is_none(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_caption_transport(captured))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
    )
    service.handle(project_key="everydayengel", action_type="caption", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln" not in prompt


def test_caption_system_prompt_has_no_ok_block_when_table_empty(
    docs_loader: "ProjectDocsLoader",
) -> None:
    captured: dict[str, str] = {}
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(ctx, transport=_make_capturing_caption_transport(captured))
    ok_loader = OperationalKnowledgeLoader(AirtableService(ctx, transport=_ok_transport_empty))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    service.handle(project_key="everydayengel", action_type="caption", command_body="test")

    prompt = captured.get("system_prompt", "")
    assert "Operative Wissensregeln" not in prompt


def test_caption_still_generates_when_ok_loader_airtable_fails(
    docs_loader: "ProjectDocsLoader",
) -> None:
    ctx = _make_bootstrap()
    openai_svc = OpenAIService(
        ctx,
        transport=lambda m, u, h, b, t: (
            200,
            {"model": "gpt-test", "output": [{"content": [{"type": "output_text", "text": _CAPTION_RESPONSE}]}]},
        ),
    )
    ok_loader = OperationalKnowledgeLoader(AirtableService(ctx, transport=_ok_transport_error))

    service = ContentOpsService(
        docs_loader=docs_loader,
        openai_service=openai_svc,
        operational_knowledge_loader=ok_loader,
    )
    result = service.handle(project_key="everydayengel", action_type="caption", command_body="test")

    assert result.summary == "Caption generiert."
    assert result.openai_used is True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def docs_loader() -> ProjectDocsLoader:
    return ProjectDocsLoader()
