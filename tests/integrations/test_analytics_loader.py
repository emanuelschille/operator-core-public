"""
Tests for AnalyticsLoader and AnalyticsContext.

Covers:
  - load_recent with valid Airtable response → returns typed context
  - load_recent with Airtable error → returns empty context (never raises)
  - load_recent with empty records → returns empty context
  - hook_examples capped at MAX_HOOK_EXAMPLES
  - dominant_cta reflects most common value
  - gap derived when all CTAs identical
  - to_prompt_block returns correct format
  - to_prompt_block returns empty string when context is empty
  - is_empty reflects data presence
"""
from __future__ import annotations

from typing import Any

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.integrations.airtable_service import AirtableService
from operator_core.integrations.analytics_service import (
    AnalyticsContext,
    AnalyticsLoader,
    _EMPTY_CONTEXT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bootstrap() -> BootstrapContext:
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
            project_base_ids={
                "everydayengel": "appTestBase123",
                "analytics": "appAnalyticsBase456",
            },
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
        runtime_path=__import__("pathlib").Path("projects/everydayengel/runtime.yaml"),
        project_runtime={
            "project_key": "everydayengel",
            "display_name": "everydayengel",
            "status": "active",
            "primary_interface": "telegram",
            "human_in_the_loop": "true",
        },
    )


_FAKE_RECORDS = [
    {
        "id": "recC01",
        "fields": {
            "hook_kurz": "Dinge die seit meiner Schwangerschaft plötzlich anders sind",
            "cta_typ": "Welche Dinge haben sich bei euch verändert?",
        },
    },
    {
        "id": "recC02",
        "fields": {
            "hook_kurz": "Eine Sache, die mir in der Schwangerschaft gerade wirklich hilft…",
            "cta_typ": "Was hilft euch so beim Schlaf?",
        },
    },
    {
        "id": "recC03",
        "fields": {
            "hook_kurz": "Seit der Schwangerschaft gehe ich viel vorsichtiger mit meinem Körper um",
            "cta_typ": "Was macht ihr bewusst anders?",
        },
    },
]


def _transport_ok(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    return 200, {"records": _FAKE_RECORDS}


def _transport_error(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    return 500, {"error": {"message": "internal server error"}}


def _transport_empty(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    return 200, {"records": []}


def _transport_single_cta(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    # All 3 records share the same CTA → uniform → gap should be derived
    return 200, {
        "records": [
            {"id": "r1", "fields": {"hook_kurz": "Hook A", "cta_typ": "Community-Frage"}},
            {"id": "r2", "fields": {"hook_kurz": "Hook B", "cta_typ": "Community-Frage"}},
            {"id": "r3", "fields": {"hook_kurz": "Hook C", "cta_typ": "Community-Frage"}},
        ]
    }


# ---------------------------------------------------------------------------
# Tests: AnalyticsLoader.load_recent
# ---------------------------------------------------------------------------

def test_load_recent_returns_hook_examples() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = AnalyticsLoader(airtable)

    result = loader.load_recent()

    assert not result.is_empty()
    # At most 2 hook examples
    assert len(result.hook_examples) == 2
    assert result.hook_examples[0] == "Dinge die seit meiner Schwangerschaft plötzlich anders sind"


def test_load_recent_returns_dominant_cta() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = AnalyticsLoader(airtable)

    result = loader.load_recent()

    assert result.dominant_cta != ""


def test_load_recent_returns_empty_on_error() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_error)
    loader = AnalyticsLoader(airtable)

    result = loader.load_recent()

    assert result.is_empty()
    assert result.hook_examples == ()
    assert result.dominant_cta == ""


def test_load_recent_returns_empty_on_no_records() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_empty)
    loader = AnalyticsLoader(airtable)

    result = loader.load_recent()

    assert result.is_empty()


def test_load_recent_derives_gap_when_cta_uniform() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_single_cta)
    loader = AnalyticsLoader(airtable)

    result = loader.load_recent()

    assert result.gap != ""
    assert "Serie" in result.gap or "Produkt" in result.gap


def test_load_recent_hook_examples_capped_at_two() -> None:
    def transport_many(method, url, headers, body):
        return 200, {
            "records": [
                {"id": f"r{i}", "fields": {"hook_kurz": f"Hook {i}", "cta_typ": "Q?"}}
                for i in range(10)
            ]
        }

    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=transport_many)
    loader = AnalyticsLoader(airtable)

    result = loader.load_recent()

    assert len(result.hook_examples) == 2


def test_load_recent_skips_records_missing_hooks() -> None:
    def transport_no_hooks(method, url, headers, body):
        return 200, {
            "records": [
                {"id": "r1", "fields": {"hook_kurz": "", "cta_typ": "Q1?"}},
                {"id": "r2", "fields": {"hook_kurz": "Valid hook", "cta_typ": "Q2?"}},
            ]
        }

    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=transport_no_hooks)
    loader = AnalyticsLoader(airtable)

    result = loader.load_recent()

    assert len(result.hook_examples) == 1
    assert result.hook_examples[0] == "Valid hook"


# ---------------------------------------------------------------------------
# Tests: AnalyticsContext.to_prompt_block
# ---------------------------------------------------------------------------

def test_to_prompt_block_contains_header() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = AnalyticsLoader(airtable)
    result = loader.load_recent()

    block = result.to_prompt_block()

    assert block.startswith("Aktuelle Performance Learnings")


def test_to_prompt_block_contains_hook_examples() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = AnalyticsLoader(airtable)
    result = loader.load_recent()

    block = result.to_prompt_block()

    assert "Dinge die seit meiner Schwangerschaft" in block


def test_to_prompt_block_contains_cta_line() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = AnalyticsLoader(airtable)
    result = loader.load_recent()

    block = result.to_prompt_block()

    assert "CTA" in block or "Dominanter" in block


def test_to_prompt_block_returns_empty_for_empty_context() -> None:
    block = _EMPTY_CONTEXT.to_prompt_block()
    assert block == ""


# ---------------------------------------------------------------------------
# Tests: is_empty
# ---------------------------------------------------------------------------

def test_is_empty_true_for_empty_context() -> None:
    assert _EMPTY_CONTEXT.is_empty() is True


def test_is_empty_false_when_data_present() -> None:
    ctx = AnalyticsContext(
        hook_examples=("Hook A",),
        dominant_cta="Community-Frage",
        gap="",
    )
    assert ctx.is_empty() is False
