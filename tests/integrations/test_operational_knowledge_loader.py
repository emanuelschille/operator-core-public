"""
Tests for OperationalKnowledgeLoader and OperationalKnowledgeContext.

Covers:
  - load_active with valid Airtable response → returns typed rows
  - load_active with Airtable error → returns empty context (never raises)
  - load_active skips rows missing Key or Value
  - OperationalKnowledgeContext.by_categories filters and orders correctly
  - OperationalKnowledgeContext.to_prompt_block generates correct text
  - to_prompt_block returns empty string when no rows match categories
  - is_empty reflects row count
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
from operator_core.integrations.airtable_service import AirtableService
from operator_core.integrations.operational_knowledge_service import (
    IDEA_CATEGORIES,
    OperationalKnowledgeContext,
    OperationalKnowledgeLoader,
    OperationalKnowledgeRow,
    PostingScheduleRule,
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
    {
        "id": "recC",
        "fields": {
            "Key": "posting_cadence_current",
            "Label": "Aktuelle Posting-Frequenz",
            "Value": "3–4x pro Woche.",
            "Category": "posting",
            "Status": "active",
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


def _transport_incomplete_rows(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    return 200, {
        "records": [
            {"id": "recX", "fields": {"Key": "", "Value": "missing key", "Category": "priorities", "Status": "active"}},
            {"id": "recY", "fields": {"Key": "no_value", "Value": "", "Category": "priorities", "Status": "active"}},
            {"id": "recZ", "fields": {"Key": "valid_key", "Value": "valid value", "Category": "priorities", "Status": "active"}},
        ]
    }


# ---------------------------------------------------------------------------
# Tests: OperationalKnowledgeLoader.load_active
# ---------------------------------------------------------------------------

def test_load_active_returns_rows() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = OperationalKnowledgeLoader(airtable)

    result = loader.load_active(project_key="everydayengel")

    assert not result.is_empty()
    assert len(result.rows) == 3
    keys = {r.key for r in result.rows}
    assert "content_priority_current" in keys
    assert "platform_primary" in keys
    assert "posting_cadence_current" in keys


def test_load_active_returns_correct_field_values() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = OperationalKnowledgeLoader(airtable)

    result = loader.load_active(project_key="everydayengel")

    row = next(r for r in result.rows if r.key == "content_priority_current")
    assert row.label == "Aktueller Inhaltsfokus"
    assert row.value == "Alltag mit Wiedererkennungswert."
    assert row.category == "priorities"
    assert row.status == "active"


def test_load_active_returns_empty_context_on_airtable_error() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_error)
    loader = OperationalKnowledgeLoader(airtable)

    result = loader.load_active(project_key="everydayengel")

    assert result.is_empty()
    assert result.rows == ()


def test_load_active_skips_rows_missing_key_or_value() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_incomplete_rows)
    loader = OperationalKnowledgeLoader(airtable)

    result = loader.load_active(project_key="everydayengel")

    assert len(result.rows) == 1
    assert result.rows[0].key == "valid_key"


def test_load_active_is_empty_when_no_records() -> None:
    def transport_empty(method, url, headers, body):
        return 200, {"records": []}

    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=transport_empty)
    loader = OperationalKnowledgeLoader(airtable)

    result = loader.load_active(project_key="everydayengel")
    assert result.is_empty()


# ---------------------------------------------------------------------------
# Tests: OperationalKnowledgeContext.by_categories
# ---------------------------------------------------------------------------

def test_by_categories_returns_matching_rows() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = OperationalKnowledgeLoader(airtable)
    result = loader.load_active(project_key="everydayengel")

    filtered = result.by_categories("priorities", "platform")

    assert len(filtered) == 2
    categories = {r.category for r in filtered}
    assert categories == {"priorities", "platform"}
    # posting row must not be included
    assert all(r.category != "posting" for r in filtered)


def test_by_categories_preserves_category_order() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = OperationalKnowledgeLoader(airtable)
    result = loader.load_active(project_key="everydayengel")

    # priorities first, then platform, then posting
    ordered = result.by_categories(*IDEA_CATEGORIES)
    categories_in_order = [r.category for r in ordered]
    # all priorities before all platforms before all postings
    assert categories_in_order == sorted(
        categories_in_order, key=lambda c: IDEA_CATEGORIES.index(c)
    )


def test_by_categories_returns_empty_for_unknown_category() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = OperationalKnowledgeLoader(airtable)
    result = loader.load_active(project_key="everydayengel")

    filtered = result.by_categories("unknown_category")
    assert filtered == ()


# ---------------------------------------------------------------------------
# Tests: OperationalKnowledgeContext.to_prompt_block
# ---------------------------------------------------------------------------

def test_to_prompt_block_contains_header() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = OperationalKnowledgeLoader(airtable)
    result = loader.load_active(project_key="everydayengel")

    block = result.to_prompt_block(*IDEA_CATEGORIES)

    assert block.startswith("Operative Wissensregeln (aktuell bindend):")


def test_to_prompt_block_contains_label_and_value() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = OperationalKnowledgeLoader(airtable)
    result = loader.load_active(project_key="everydayengel")

    block = result.to_prompt_block(*IDEA_CATEGORIES)

    assert "Aktueller Inhaltsfokus: Alltag mit Wiedererkennungswert." in block
    assert "Hauptplattform: TikTok und Instagram Reels." in block
    assert "Aktuelle Posting-Frequenz: 3–4x pro Woche." in block


def test_to_prompt_block_returns_empty_string_for_no_matching_rows() -> None:
    result = _EMPTY_CONTEXT
    block = result.to_prompt_block(*IDEA_CATEGORIES)
    assert block == ""


def test_to_prompt_block_returns_empty_string_for_unmatched_categories() -> None:
    ctx = _make_bootstrap()
    airtable = AirtableService(ctx, transport=_transport_ok)
    loader = OperationalKnowledgeLoader(airtable)
    result = loader.load_active(project_key="everydayengel")

    block = result.to_prompt_block("monetization", "audience")
    assert block == ""


# ---------------------------------------------------------------------------
# Tests: is_empty
# ---------------------------------------------------------------------------

def test_is_empty_true_for_no_rows() -> None:
    assert _EMPTY_CONTEXT.is_empty() is True


def test_is_empty_false_when_rows_present() -> None:
    row = OperationalKnowledgeRow(
        key="k", label="L", value="V", category="priorities", status="active"
    )
    ctx = OperationalKnowledgeContext(rows=(row,))
    assert ctx.is_empty() is False


def test_resolve_posting_schedule_prefers_weekday_json_row() -> None:
    ctx = OperationalKnowledgeContext(
        rows=(
            OperationalKnowledgeRow(
                key="posting_schedule_facebook_reel_saturday",
                label="Facebook Samstag",
                value='{"platform":"facebook_reel","weekday":"saturday","timezone":"Europe/Berlin","enabled":true,"time_local":"18:05","condition":"only_if_strong_video","note":"nur wenn starkes Video"}',
                category="posting",
                status="active",
            ),
            OperationalKnowledgeRow(
                key="posting_time_facebook",
                label="Facebook default",
                value="18:00",
                category="posting",
                status="active",
            ),
        )
    )

    result = ctx.resolve_posting_schedule(
        platform="facebook_reel",
        weekday="saturday",
        fallback_key="posting_time_facebook",
        default_time="18:00",
    )

    assert isinstance(result, PostingScheduleRule)
    assert result.source == "posting_schedule"
    assert result.enabled is True
    assert result.time_local == "18:05"
    assert result.condition == "only_if_strong_video"
    assert result.note == "nur wenn starkes Video"


def test_resolve_posting_schedule_preserves_disabled_skip_day() -> None:
    ctx = OperationalKnowledgeContext(
        rows=(
            OperationalKnowledgeRow(
                key="posting_schedule_facebook_reel_thursday",
                label="Facebook Donnerstag",
                value='{"platform":"facebook_reel","weekday":"thursday","timezone":"Europe/Berlin","enabled":false,"time_local":"","condition":"skip","note":"auslassen"}',
                category="posting",
                status="active",
            ),
            OperationalKnowledgeRow(
                key="posting_time_facebook",
                label="Facebook default",
                value="18:00",
                category="posting",
                status="active",
            ),
        )
    )

    result = ctx.resolve_posting_schedule(
        platform="facebook_reel",
        weekday="thursday",
        fallback_key="posting_time_facebook",
        default_time="18:00",
    )

    assert result.source == "posting_schedule"
    assert result.enabled is False
    assert result.time_local == ""
    assert result.condition == "skip"


def test_resolve_posting_schedule_falls_back_to_old_posting_time_key() -> None:
    ctx = OperationalKnowledgeContext(
        rows=(
            OperationalKnowledgeRow(
                key="posting_time_youtube",
                label="YouTube default",
                value="20:30 Uhr Europe/Berlin",
                category="posting",
                status="active",
            ),
        )
    )

    result = ctx.resolve_posting_schedule(
        platform="youtube_short",
        weekday="monday",
        fallback_key="posting_time_youtube",
        default_time="20:30",
    )

    assert result.source == "posting_time_fallback"
    assert result.enabled is True
    assert result.time_local == "20:30"
