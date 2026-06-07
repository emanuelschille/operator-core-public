"""
Tests for PlatformSignalLoader.

Covers:
  - canonical OK keys map to canonical live platform keys
  - missing/unconfigured keys are ignored
  - a failed platform-table read is skipped
  - post_count is derived from record count
  - gap is derived from per-platform CTA values
"""
from __future__ import annotations

from unittest.mock import MagicMock

from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList
from operator_core.integrations.operational_knowledge_service import (
    OperationalKnowledgeContext,
    OperationalKnowledgeRow,
)
from operator_core.integrations.platform_signal_service import PlatformSignalLoader


def _ok_row(key: str, value: str) -> OperationalKnowledgeRow:
    return OperationalKnowledgeRow(
        key=key,
        label=key,
        value=value,
        category="posting",
        status="active",
    )


def _record(
    hook: str = "Hook A",
    cta: str = "Community-Frage",
    format_typ: str = "Talking Head",
) -> AirtableRecord:
    return AirtableRecord(
        record_id="recX",
        fields={"hook_kurz": hook, "cta_typ": cta, "format_typ": format_typ},
        created_time="2026-04-01T10:00:00.000Z",
    )


def _record_list(*records: AirtableRecord) -> AirtableRecordList:
    return AirtableRecordList(records=tuple(records))


def _ok_loader(rows: list[OperationalKnowledgeRow]) -> MagicMock:
    loader = MagicMock()
    loader.load_active.return_value = OperationalKnowledgeContext(rows=tuple(rows))
    return loader


def test_load_all_uses_only_canonical_platform_keys() -> None:
    ok = _ok_loader([
        _ok_row("analytics_table_tiktok", "tblTIKTOK"),
        _ok_row("analytics_table_instagram_reel", "tblINSTAGRAM"),
        _ok_row("analytics_table_instagram", "tblOLD"),
    ])
    airtable = MagicMock()
    airtable.list_records.side_effect = [
        _record_list(_record(), _record()),
        _record_list(_record()),
    ]

    result = PlatformSignalLoader(airtable, ok).load_all(ok_project_key="everydayengel")

    assert set(result) == {"tiktok", "instagram_reel"}
    assert "instagram" not in result
    assert result["tiktok"].table_id == "tblTIKTOK"


def test_load_all_skips_platform_when_table_read_fails() -> None:
    ok = _ok_loader([
        _ok_row("analytics_table_tiktok", "tblTIKTOK"),
        _ok_row("analytics_table_instagram_reel", "tblINSTAGRAM"),
    ])
    airtable = MagicMock()
    airtable.list_records.side_effect = [
        _record_list(_record()),
        RuntimeError("timeout"),
    ]

    result = PlatformSignalLoader(airtable, ok).load_all(ok_project_key="everydayengel")

    assert "tiktok" in result
    assert "instagram_reel" not in result


def test_load_all_returns_empty_when_ok_load_fails() -> None:
    ok = MagicMock()
    ok.load_active.side_effect = RuntimeError("airtable down")
    airtable = MagicMock()

    result = PlatformSignalLoader(airtable, ok).load_all(ok_project_key="everydayengel")

    assert result == {}


def test_load_all_derives_post_count_and_gap() -> None:
    ok = _ok_loader([_ok_row("analytics_table_youtube_short", "tblYT")])
    airtable = MagicMock()
    airtable.list_records.return_value = _record_list(
        _record("Hook A", "Community-Frage"),
        _record("Hook B", "Community-Frage"),
        _record("Hook C", "Community-Frage"),
    )

    result = PlatformSignalLoader(airtable, ok).load_all(ok_project_key="everydayengel")

    ctx = result["youtube_short"]
    assert ctx.table_id == "tblYT"
    assert ctx.post_count == 3
    assert ctx.gap != ""
    assert ctx.hook_examples == ("Hook A", "Hook B")
    assert ctx.dominant_format == "Talking Head"
    assert ctx.format_examples == ("Talking Head", "Talking Head")


def test_load_all_builds_numeric_summary_from_platform_metrics() -> None:
    ok = _ok_loader([_ok_row("analytics_table_youtube_short", "tblYT")])
    airtable = MagicMock()
    airtable.list_records.return_value = _record_list(
        AirtableRecord(
            record_id="rec1",
            fields={
                "hook_kurz": "Hook A",
                "cta_typ": "Community-Frage",
                "format_typ": "YouTube Short",
                "views": 1000,
                "likes": 110,
                "comments": 15,
                "completion_rate": "42.5%",
                "avg watch": 17.2,
                "swiped_pct_72h": 38.5,
            },
            created_time="2026-04-02T10:00:00.000Z",
        ),
        AirtableRecord(
            record_id="rec2",
            fields={
                "hook_kurz": "Hook B",
                "cta_typ": "Community-Frage",
                "format_typ": "YouTube Short",
                "views": 800,
                "likes": 95,
                "comments": 12,
                "completion_rate": "39.0%",
                "avg watch": 15.4,
                "swiped_pct_72h": 41.0,
            },
            created_time="2026-04-01T10:00:00.000Z",
        ),
    )

    result = PlatformSignalLoader(airtable, ok).load_all(ok_project_key="everydayengel")

    ctx = result["youtube_short"]
    assert ctx.numeric_fields_used == (
        "avg watch",
        "comments",
        "completion_rate",
        "likes",
        "swiped_pct_72h",
        "views",
    )
    assert any("Views: Ø 900" in line for line in ctx.numeric_summary_lines)
    assert any("Likes/Reactions: Ø 102" in line for line in ctx.numeric_summary_lines)
    assert any("Completion: Ø 40.8%" in line for line in ctx.numeric_summary_lines)
    assert any("Retention: Ø 39.8%" in line for line in ctx.numeric_summary_lines)
