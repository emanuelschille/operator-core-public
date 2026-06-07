from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.integrations.airtable_service import AirtableService
from operator_core.integrations.daily_plan_generation_service import DailyPlanGenerationService
from operator_core.integrations.daily_plan_service import DailyPlanService, TodayPlanSnapshot


def _bootstrap() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="WARNING",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(enabled=False, bot_token="", allowed_user_ids=(), allowed_chat_ids=()),
        airtable=AirtableSettings(
            enabled=True,
            api_key="pat-test",
            project_base_ids={"everydayengel": "appTestBase123"},
        ),
        openai=OpenAISettings(
            enabled=False,
            api_key="",
            model="gpt-test",
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )
    return BootstrapContext(
        settings=settings,
        runtime_path=Path("projects/everydayengel/runtime.yaml"),
        project_runtime={
            "project_key": "everydayengel",
            "display_name": "everydayengel",
            "status": "active",
            "primary_interface": "telegram",
            "human_in_the_loop": "true",
        },
    )


def _make_daily_plan_service(transport: Any) -> DailyPlanService:
    airtable = AirtableService(_bootstrap())
    airtable.transport = transport
    return DailyPlanService(airtable)


def _make_openai_service(outputs: list[str]) -> Any:
    """Returns a mock OpenAI service that yields successive output texts."""
    svc = MagicMock()
    responses = iter(outputs)

    def complete_messages(**kwargs: Any) -> Any:
        resp = MagicMock()
        resp.output_text = next(responses)
        return resp

    svc.complete_messages.side_effect = complete_messages
    return svc


def _make_ok_loader(block: str = "") -> Any:
    loader = MagicMock()
    ctx = MagicMock()
    ctx.is_empty.return_value = block == ""
    ctx.to_prompt_block.return_value = block
    loader.load_active.return_value = ctx
    return loader


def _make_docs_loader(content: str) -> Any:
    loader = MagicMock()
    doc = MagicMock()
    doc.content = content
    loader.load.return_value = doc
    return loader


def _patch_transport(record_id: str, patched_fields: dict[str, Any]) -> Any:
    """Transport that accepts a PATCH and echoes back the given patched_fields."""
    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        if method == "PATCH" and url.endswith(f"/{record_id}"):
            return 200, {
                "id": record_id,
                "fields": patched_fields,
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    return transport


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_both_missing_fields_generated_and_patched() -> None:
    """Both serie_thema and caption are missing; generation fills both."""
    record_id = "recGen001"
    snapshot = TodayPlanSnapshot(
        record_id=record_id,
        decision="pending",
        platform="tiktok",
        title_raw="Weihnachtsplätzchen backen",
        hook="So backst du die perfekten Plätzchen",
        cta="Rezept speichern",
        format_typ="TikTok Reel",
    )

    patched_fields = {
        "serie_thema": "Backen & Rezepte",
        "caption": "Die besten Weihnachtsplätzchen! #backen #rezept",
    }
    transport = _patch_transport(record_id, patched_fields)
    daily_plan_svc = _make_daily_plan_service(transport)
    openai_svc = _make_openai_service([
        "Serie/Thema: Backen & Rezepte",
        "Caption: Die besten Weihnachtsplätzchen! #backen #rezept",
    ])
    ok_loader = _make_ok_loader()

    svc = DailyPlanGenerationService(
        daily_plan_service=daily_plan_svc,
        openai_service=openai_svc,
        ok_loader=ok_loader,
    )
    result = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert result.serie_thema == "Backen & Rezepte"
    assert result.caption == "Die besten Weihnachtsplätzchen! #backen #rezept"
    assert openai_svc.complete_messages.call_count == 2


def test_only_missing_field_generated_existing_not_overwritten() -> None:
    """serie_thema already filled; only caption is generated."""
    record_id = "recGen002"
    snapshot = TodayPlanSnapshot(
        record_id=record_id,
        decision="pending",
        platform="instagram_reel",
        serie_thema="Schon gesetzt",
        title_raw="Alltag Morgenroutine",
        hook="Meine Morgenroutine in 60 Sekunden",
        cta="Folge für mehr",
        format_typ="Instagram Reel",
    )

    patched_fields = {"caption": "Starte deinen Tag richtig! #morgenroutine"}
    transport = _patch_transport(record_id, patched_fields)
    daily_plan_svc = _make_daily_plan_service(transport)
    openai_svc = _make_openai_service(["Caption: Starte deinen Tag richtig! #morgenroutine"])
    ok_loader = _make_ok_loader()

    svc = DailyPlanGenerationService(
        daily_plan_service=daily_plan_svc,
        openai_service=openai_svc,
        ok_loader=ok_loader,
    )
    result = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    # serie_thema must remain unchanged
    assert result.serie_thema == "Schon gesetzt"
    # caption was generated
    assert result.caption == "Starte deinen Tag richtig! #morgenroutine"
    # only one OpenAI call (caption only)
    assert openai_svc.complete_messages.call_count == 1


def test_all_fields_filled_no_generation_attempted() -> None:
    """All generation targets already filled; OpenAI is never called."""
    snapshot = TodayPlanSnapshot(
        record_id="recGen003",
        decision="pending",
        platform="tiktok",
        serie_thema="Schon gesetzt",
        format_typ="Talking Head",
        caption="Schon vorhanden",
        title_raw="Titel",
        hook="Hook",
        cta="CTA",
    )

    openai_svc = _make_openai_service([])

    svc = DailyPlanGenerationService(
        daily_plan_service=MagicMock(),
        openai_service=openai_svc,
        ok_loader=None,
    )
    result = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert result is snapshot  # unchanged, no copy
    openai_svc.complete_messages.assert_not_called()


def test_no_openai_service_returns_snapshot_unchanged() -> None:
    """When OpenAI service is None, snapshot is returned as-is."""
    snapshot = TodayPlanSnapshot(
        record_id="recGen004",
        decision="pending",
        platform="tiktok",
        title_raw="Titel",
    )

    svc = DailyPlanGenerationService(
        daily_plan_service=MagicMock(),
        openai_service=None,
        ok_loader=None,
    )
    result = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert result is snapshot


def test_openai_failure_logs_warning_returns_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    """When OpenAI call raises, warn and return original snapshot."""
    import logging

    snapshot = TodayPlanSnapshot(
        record_id="recGen005",
        decision="pending",
        platform="tiktok",
        title_raw="Titel",
        hook="Hook",
    )

    openai_svc = MagicMock()
    openai_svc.complete_messages.side_effect = RuntimeError("timeout")

    svc = DailyPlanGenerationService(
        daily_plan_service=MagicMock(),
        openai_service=openai_svc,
        ok_loader=None,
    )

    with caplog.at_level(logging.WARNING, logger="operator_core.integrations.daily_plan_generation_service"):
        result = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert result is snapshot
    assert any("openai failed" in r.message.lower() for r in caplog.records)


def test_sibling_themes_appear_in_serie_thema_prompt() -> None:
    """Sibling serie_thema values must reach the system prompt for serie_thema generation."""
    record_id = "recGen006"
    snapshot = TodayPlanSnapshot(
        record_id=record_id,
        decision="pending",
        platform="tiktok",
        title_raw="Herbstspaziergang",
        hook="So schön ist der Herbst",
        cta="Was magst du im Herbst?",
        format_typ="Talking Head",
        caption="Schon vorhanden",  # caption is filled, so only serie_thema generated
    )
    siblings = (
        TodayPlanSnapshot(
            record_id="recSib1",
            decision="pending",
            platform="instagram_reel",
            serie_thema="Natur & Abenteuer",
        ),
        TodayPlanSnapshot(
            record_id="recSib2",
            decision="pending",
            platform="youtube_short",
            serie_thema="Outdoor & Sport",
        ),
    )

    captured_prompts: list[str] = []

    openai_svc = MagicMock()

    def complete_messages(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> Any:
        captured_prompts.append(system_prompt)
        resp = MagicMock()
        resp.output_text = "Serie/Thema: Natur & Outdoor"
        return resp

    openai_svc.complete_messages.side_effect = complete_messages

    patched_fields = {"serie_thema": "Natur & Outdoor"}
    transport = _patch_transport(record_id, patched_fields)
    daily_plan_svc = _make_daily_plan_service(transport)

    svc = DailyPlanGenerationService(
        daily_plan_service=daily_plan_svc,
        openai_service=openai_svc,
        ok_loader=None,
    )
    result = svc.fill_missing_fields(
        project_key="everydayengel",
        snapshot=snapshot,
        siblings=siblings,
    )

    assert result.serie_thema == "Natur & Outdoor"
    assert len(captured_prompts) == 1
    assert "Natur & Abenteuer" in captured_prompts[0]
    assert "Outdoor & Sport" in captured_prompts[0]


def test_patch_failure_returns_original_snapshot(caplog: pytest.LogCaptureFixture) -> None:
    """When patch_fields raises, warn and return the pre-generation snapshot."""
    import logging

    record_id = "recGen007"
    snapshot = TodayPlanSnapshot(
        record_id=record_id,
        decision="pending",
        platform="tiktok",
        title_raw="Titel",
        hook="Hook",
        format_typ="Talking Head",
    )

    openai_svc = _make_openai_service([
        "Serie/Thema: Backen",
        "Caption: Super Caption",
    ])

    daily_plan_svc = MagicMock()
    daily_plan_svc.patch_fields.side_effect = RuntimeError("airtable unavailable")

    svc = DailyPlanGenerationService(
        daily_plan_service=daily_plan_svc,
        openai_service=openai_svc,
        ok_loader=None,
    )

    with caplog.at_level(logging.WARNING, logger="operator_core.integrations.daily_plan_generation_service"):
        result = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert result is snapshot
    assert any("patch failed" in r.message.lower() for r in caplog.records)


def test_missing_format_typ_is_generated_with_platform_analytics_context() -> None:
    record_id = "recGen008"
    snapshot = TodayPlanSnapshot(
        record_id=record_id,
        decision="pending",
        platform="youtube_short",
        serie_thema="Gedanken",
        title_raw="Mein Morgen ohne Kaffee",
        hook="So bleibe ich trotzdem wach",
        cta="Was hilft dir?",
        caption="Schon vorhanden",
    )

    captured_system_prompts: list[str] = []
    openai_svc = MagicMock()

    def complete_messages(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> Any:
        captured_system_prompts.append(system_prompt)
        resp = MagicMock()
        resp.output_text = "Format: YouTube Short"
        return resp

    openai_svc.complete_messages.side_effect = complete_messages

    platform_signal_loader = MagicMock()
    platform_signal_loader.load_all.return_value = {
        "youtube_short": MagicMock(
            table_id="tblYTREAL",
            post_count=4,
            dominant_cta="Community-Frage",
            gap="noch keine Serie oder Produkt-CTA – jetzt gut einführbar",
            hook_examples=("Hook A", "Hook B"),
            dominant_format="YouTube Short",
            format_examples=("YouTube Short", "Talking Head"),
            numeric_summary_lines=("Views: Ø 1200 | best 2100 | Felder: views",),
            numeric_fields_used=("views",),
        )
    }

    patched_fields = {"format_typ": "YouTube Short"}
    transport = _patch_transport(record_id, patched_fields)
    daily_plan_svc = _make_daily_plan_service(transport)

    svc = DailyPlanGenerationService(
        daily_plan_service=daily_plan_svc,
        openai_service=openai_svc,
        ok_loader=None,
        platform_signal_loader=platform_signal_loader,
    )
    result = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert result.format_typ == "YouTube Short"
    assert len(captured_system_prompts) == 1
    assert "Aktuelle Plattform-Signale aus der Analytics-Base" in captured_system_prompts[0]
    assert "Häufiges Format: YouTube Short" in captured_system_prompts[0]
    assert "Views: Ø 1200 | best 2100 | Felder: views" in captured_system_prompts[0]


def test_caption_generation_includes_content_rules_and_freshness_token() -> None:
    record_id = "recGen009"
    snapshot = TodayPlanSnapshot(
        record_id=record_id,
        decision="pending",
        platform="instagram_reel",
        serie_thema="Morgenroutine",
        title_raw="Mein Morgen ohne Druck",
        hook="So starte ich ruhiger in den Tag",
        cta="Was hilft dir morgens?",
        format_typ="Instagram Reel",
    )

    captured_system_prompts: list[str] = []
    openai_svc = MagicMock()

    def complete_messages(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> Any:
        captured_system_prompts.append(system_prompt)
        resp = MagicMock()
        resp.output_text = "Caption: Ruhiger starten hilft mir gerade total. Was hilft dir morgens? #morgenroutine"
        return resp

    openai_svc.complete_messages.side_effect = complete_messages

    docs_loader = _make_docs_loader(
        """
## Caption Rules
- kurz
- klar
- nicht überladen

## CTA Content Rule
- leichte Interaktion
- Wiedererkennung

## Primary Content Formats
- Talking Head
- Reel

## Content Tone
- direkt
- ehrlich
"""
    )

    patched_fields = {
        "caption": "Ruhiger starten hilft mir gerade total. Was hilft dir morgens? #morgenroutine"
    }
    transport = _patch_transport(record_id, patched_fields)
    daily_plan_svc = _make_daily_plan_service(transport)

    svc = DailyPlanGenerationService(
        daily_plan_service=daily_plan_svc,
        openai_service=openai_svc,
        ok_loader=None,
        docs_loader=docs_loader,
    )
    result = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert result.caption.startswith("Ruhiger starten")
    assert len(captured_system_prompts) == 1
    assert "Aktuelle Projektregeln aus content_rules" in captured_system_prompts[0]
    assert "Caption-Regeln: kurz | klar | nicht überladen" in captured_system_prompts[0]
    assert "Generierungslauf:" in captured_system_prompts[0]
    assert "Vermeide unnötig ähnliche Wiederholung" in captured_system_prompts[0]


def test_repeated_caption_is_rejected_and_regenerated_for_same_row() -> None:
    record_id = "recGen010"
    snapshot = TodayPlanSnapshot(
        record_id=record_id,
        decision="pending",
        platform="youtube_short",
        serie_thema="Morgenroutine",
        title_raw="Mein Morgen ohne Kaffee",
        hook="So bleibe ich trotzdem wach",
        cta="Was hilft dir?",
        format_typ="YouTube Short",
    )

    patched_payloads: list[dict[str, Any]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        if method == "PATCH" and url.endswith(f"/{record_id}"):
            fields = dict((body or {}).get("fields", {}))
            patched_payloads.append(fields)
            return 200, {
                "id": record_id,
                "fields": fields,
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    daily_plan_svc = _make_daily_plan_service(transport)
    openai_svc = _make_openai_service([
        "Caption: Das ist Variante A",
        "Caption: Das ist Variante A aber leicht anders",
        "Caption: Das ist Variante B",
    ])

    svc = DailyPlanGenerationService(
        daily_plan_service=daily_plan_svc,
        openai_service=openai_svc,
        ok_loader=None,
    )

    first = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)
    second = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert first.caption == "Das ist Variante A"
    assert second.caption == "Das ist Variante B"
    assert patched_payloads[0]["caption"] == "Das ist Variante A"
    assert patched_payloads[1]["caption"] == "Das ist Variante B"
    assert openai_svc.complete_messages.call_count == 3


def test_caption_generation_uses_existing_row_fields_as_locked_context() -> None:
    snapshot = TodayPlanSnapshot(
        record_id="recGen011",
        decision="pending",
        platform="youtube_short",
        serie_thema="Morgenroutine",
        title_raw="Kleine Schritte gegen hektische Morgen",
        hook="So wird dein Start ruhiger",
        cta="Welche Routine hilft dir am meisten?",
        format_typ="YouTube Short",
    )

    captured_system_prompts: list[str] = []
    openai_svc = MagicMock()

    def complete_messages(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> Any:
        captured_system_prompts.append(system_prompt)
        resp = MagicMock()
        resp.output_text = "Caption: Ruhiger starten klappt bei mir mit einer Mini-Routine."
        return resp

    openai_svc.complete_messages.side_effect = complete_messages
    transport = _patch_transport("recGen011", {"caption": "Ruhiger starten klappt bei mir mit einer Mini-Routine."})
    daily_plan_svc = _make_daily_plan_service(transport)

    svc = DailyPlanGenerationService(
        daily_plan_service=daily_plan_svc,
        openai_service=openai_svc,
        ok_loader=None,
    )
    svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert "Bereits gesetzte Tagesplan-Felder" in captured_system_prompts[0]
    assert "- Serie/Thema: Morgenroutine" in captured_system_prompts[0]
    assert "- Title: Kleine Schritte gegen hektische Morgen" in captured_system_prompts[0]
    assert "- CTA: Welche Routine hilft dir am meisten?" in captured_system_prompts[0]


def test_generation_fills_missing_fields_from_existing_caption_context() -> None:
    record_id = "recGen012"
    snapshot = TodayPlanSnapshot(
        record_id=record_id,
        decision="pending",
        platform="youtube_short",
        caption="julias mulias",
    )

    patched_fields = {
        "serie_thema": "Ruhiger Morgen",
        "title_raw": "Ein ruhiger Moment am Morgen",
        "hook": "Wenn dein Morgen kurz leiser wird",
        "cta": "Was hilft dir morgens?",
        "format_typ": "YouTube Short",
    }
    transport = _patch_transport(record_id, patched_fields)
    daily_plan_svc = _make_daily_plan_service(transport)
    openai_svc = _make_openai_service([
        "Serie/Thema: Ruhiger Morgen",
        "Title: Ein ruhiger Moment am Morgen",
        "Hook: Wenn dein Morgen kurz leiser wird",
        "CTA: Was hilft dir morgens?",
        "Format: YouTube Short",
    ])

    svc = DailyPlanGenerationService(
        daily_plan_service=daily_plan_svc,
        openai_service=openai_svc,
        ok_loader=None,
    )
    result = svc.fill_missing_fields(project_key="everydayengel", snapshot=snapshot)

    assert result.caption == "julias mulias"
    assert result.serie_thema == "Ruhiger Morgen"
    assert result.title_raw == "Ein ruhiger Moment am Morgen"
    assert result.hook == "Wenn dein Morgen kurz leiser wird"
    assert result.cta == "Was hilft dir morgens?"
    assert result.format_typ == "YouTube Short"
    assert openai_svc.complete_messages.call_count == 5
