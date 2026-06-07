"""
Tests for the commercial classification layer.

Covers:
1. CommercialLogEntry survives file round-trip (durable)
2. CommercialClassLog is queryable after restart
3. CommercialMixSummary counts classes correctly
4. /idea path: commercial_class logged via _emit_commercial_log
5. /draft and /vollauto: commercial_class set on ContentOpResult and logged
6. drift_warning fires when zero product_near+ in window
7. Window filter excludes entries older than window_days
8. classify_commercial regression — existing /idea signal not changed
9. Runtime wiring includes CommercialClassLog
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operator_core.core.content_ops.correction_capture import (
    CommercialClass,
    CommercialClassLog,
    CommercialLogEntry,
    CommercialMixSummary,
    classify_commercial,
    summarize_commercial_mix,
)
from operator_core.core.content_ops.models import ContentOpResult
from operator_core.core.content_ops.service import ContentOpsService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    record_id: str = "clog-001",
    project_key: str = "everydayengel",
    action_type: str = "idea",
    commercial_class: CommercialClass = CommercialClass.trust_building,
    created_at: datetime | None = None,
) -> CommercialLogEntry:
    return CommercialLogEntry(
        record_id=record_id,
        project_key=project_key,
        action_type=action_type,
        platform="tiktok",
        commercial_class=commercial_class,
        prompt_excerpt="beim Kochen schwindelig",
        created_at=created_at or datetime.now(timezone.utc),
    )


def _make_content_result(
    action_type: str = "idea",
    commercial_class: str | None = "trust_building",
    command_body: str = "Neue Idee",
    project_key: str = "everydayengel",
) -> ContentOpResult:
    return ContentOpResult(
        lane_name="content_ops",
        project_key=project_key,
        action_type=action_type,
        command_body=command_body,
        title="Test",
        summary="Test summary",
        items=("Beim Kochen merke ich, dass ich sitzen muss.",),
        openai_used=True,
        model_name="gpt-4o",
        platform="tiktok",
        commercial_class=commercial_class,
    )


# ---------------------------------------------------------------------------
# 1. CommercialLogEntry round-trip (durable)
# ---------------------------------------------------------------------------

def test_commercial_log_entry_survives_file_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "commercial_class_log.json"
        log1 = CommercialClassLog(file_path=path)
        entry = _make_entry(
            record_id="clog-rt-1",
            commercial_class=CommercialClass.product_near,
        )
        log1.append(entry)
        assert path.exists()

        log2 = CommercialClassLog(file_path=path)
        all_entries = log2.list_all_by_project("everydayengel")
        assert len(all_entries) == 1
        loaded = all_entries[0]
        assert loaded.record_id == "clog-rt-1"
        assert loaded.commercial_class is CommercialClass.product_near
        assert loaded.project_key == "everydayengel"
        assert loaded.action_type == "idea"
        assert loaded.platform == "tiktok"
        assert loaded.prompt_excerpt == "beim Kochen schwindelig"


# ---------------------------------------------------------------------------
# 2. Queryable after restart
# ---------------------------------------------------------------------------

def test_commercial_log_queryable_after_restart() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "commercial_class_log.json"
        log1 = CommercialClassLog(file_path=path)
        log1.append(_make_entry(record_id="r1", commercial_class=CommercialClass.trust_building))
        log1.append(_make_entry(record_id="r2", commercial_class=CommercialClass.product_near))
        log1.append(_make_entry(record_id="r3", project_key="other_project"))

        log2 = CommercialClassLog(file_path=path)
        results = log2.list_all_by_project("everydayengel")
        assert len(results) == 2
        assert all(r.project_key == "everydayengel" for r in results)
        classes = {r.commercial_class for r in results}
        assert CommercialClass.trust_building in classes
        assert CommercialClass.product_near in classes


# ---------------------------------------------------------------------------
# 3. CommercialMixSummary counts correctly
# ---------------------------------------------------------------------------

def test_commercial_mix_summary_counts_correctly() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "commercial_class_log.json"
        log = CommercialClassLog(file_path=path)
        log.append(_make_entry("r1", commercial_class=CommercialClass.trust_building))
        log.append(_make_entry("r2", commercial_class=CommercialClass.trust_building))
        log.append(_make_entry("r3", commercial_class=CommercialClass.product_near))
        log.append(_make_entry("r4", commercial_class=CommercialClass.recommendation_ready))

        summary = summarize_commercial_mix(log, "everydayengel", window_days=7)
        assert summary.total == 4
        assert summary.trust_building == 2
        assert summary.product_near == 1
        assert summary.recommendation_ready == 1
        assert summary.direct_offer == 0
        assert summary.off_thesis_or_monetization_waste == 0
        assert summary.drift_warning is False


# ---------------------------------------------------------------------------
# 4. _emit_commercial_log called from ContentOpsService
# ---------------------------------------------------------------------------

def test_emit_commercial_log_records_idea_generation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "commercial_class_log.json"
        log = CommercialClassLog(file_path=path)
        svc = ContentOpsService(commercial_class_log=log)

        result = _make_content_result(action_type="idea", commercial_class="trust_building")
        svc._emit_commercial_log(result)

        entries = log.list_all_by_project("everydayengel")
        assert len(entries) == 1
        assert entries[0].action_type == "idea"
        assert entries[0].commercial_class is CommercialClass.trust_building
        assert entries[0].prompt_excerpt == "Neue Idee"


# ---------------------------------------------------------------------------
# 5. /draft and /vollauto: commercial_class present on ContentOpResult
# ---------------------------------------------------------------------------

def test_emit_commercial_log_records_draft_generation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "commercial_class_log.json"
        log = CommercialClassLog(file_path=path)
        svc = ContentOpsService(commercial_class_log=log)

        result = _make_content_result(
            action_type="draft",
            commercial_class="product_near",
            command_body="Schuhe anziehen ist schwierig geworden",
        )
        svc._emit_commercial_log(result)

        entries = log.list_all_by_project("everydayengel")
        assert len(entries) == 1
        assert entries[0].action_type == "draft"
        assert entries[0].commercial_class is CommercialClass.product_near


def test_emit_commercial_log_records_vollauto_generation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "commercial_class_log.json"
        log = CommercialClassLog(file_path=path)
        svc = ContentOpsService(commercial_class_log=log)

        result = _make_content_result(
            action_type="vollauto",
            commercial_class="trust_building",
            command_body="Nächte sehen gerade anders aus",
        )
        svc._emit_commercial_log(result)

        entries = log.list_all_by_project("everydayengel")
        assert len(entries) == 1
        assert entries[0].action_type == "vollauto"


# ---------------------------------------------------------------------------
# 6. drift_warning fires when zero product_near+ in window
# ---------------------------------------------------------------------------

def test_drift_warning_when_all_trust_building() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        log = CommercialClassLog(file_path=Path(tmpdir) / "log.json")
        for i in range(5):
            log.append(_make_entry(record_id=f"r{i}", commercial_class=CommercialClass.trust_building))

        summary = summarize_commercial_mix(log, "everydayengel", window_days=7)
        assert summary.drift_warning is True
        assert summary.trust_building == 5


def test_no_drift_warning_when_product_near_present() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        log = CommercialClassLog(file_path=Path(tmpdir) / "log.json")
        log.append(_make_entry("r1", commercial_class=CommercialClass.trust_building))
        log.append(_make_entry("r2", commercial_class=CommercialClass.product_near))

        summary = summarize_commercial_mix(log, "everydayengel", window_days=7)
        assert summary.drift_warning is False


# ---------------------------------------------------------------------------
# 7. Window filter excludes old entries
# ---------------------------------------------------------------------------

def test_window_filter_excludes_old_entries() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        log = CommercialClassLog(file_path=Path(tmpdir) / "log.json")
        old_date = datetime.now(timezone.utc) - timedelta(days=10)
        recent_date = datetime.now(timezone.utc) - timedelta(days=3)

        log.append(_make_entry("old", commercial_class=CommercialClass.product_near, created_at=old_date))
        log.append(_make_entry("recent", commercial_class=CommercialClass.trust_building, created_at=recent_date))

        summary = summarize_commercial_mix(log, "everydayengel", window_days=7)
        assert summary.total == 1
        assert summary.trust_building == 1
        assert summary.product_near == 0
        assert summary.drift_warning is True


# ---------------------------------------------------------------------------
# 8. classify_commercial regression — existing /idea signals unchanged
# ---------------------------------------------------------------------------

def test_classify_commercial_trust_building_no_product_signal() -> None:
    result = classify_commercial("Heute merke ich zum ersten Mal, dass mir beim Stehen schwindelig wird.")
    assert result is CommercialClass.trust_building


def test_classify_commercial_product_near_kissen_signal() -> None:
    result = classify_commercial("Das Schlafen hat sich verändert — ich brauche inzwischen drei Kissen.")
    assert result is CommercialClass.product_near


def test_classify_commercial_recommendation_ready_signal() -> None:
    result = classify_commercial("Dieses Kissen hat mir die letzten Wochen echt geholfen — seit SSW 28.")
    assert result is CommercialClass.recommendation_ready


def test_classify_commercial_direct_offer_anzeige_signal() -> None:
    result = classify_commercial("*Anzeige — Kooperation mit BrandX. Code JULIA10 gibt 10% Rabatt.")
    assert result is CommercialClass.direct_offer


# ---------------------------------------------------------------------------
# 9. Runtime wiring includes CommercialClassLog
# ---------------------------------------------------------------------------

def test_runtime_wires_commercial_class_log() -> None:
    import inspect
    from operator_core.runtime import OperatorRuntime

    src = inspect.getsource(OperatorRuntime._start_telegram_polling)  # type: ignore[attr-defined]

    assert "CommercialClassLog" in src, (
        "OperatorRuntime._start_telegram_polling must instantiate CommercialClassLog"
    )
    assert "commercial_class_log" in src, (
        "OperatorRuntime._start_telegram_polling must pass commercial_class_log to ExecutionService"
    )
    assert "commercial_class_log.json" in src, (
        "CommercialClassLog must be backed by commercial_class_log.json in runtime state dir"
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_emit_commercial_log_no_log_configured_is_noop() -> None:
    svc = ContentOpsService()
    result = _make_content_result(commercial_class="trust_building")
    svc._emit_commercial_log(result)  # must not raise


def test_emit_commercial_log_none_class_is_noop() -> None:
    log = CommercialClassLog(file_path=None)
    svc = ContentOpsService(commercial_class_log=log)
    result = _make_content_result(commercial_class=None)
    svc._emit_commercial_log(result)
    assert len(log) == 0


def test_commercial_log_no_file_path_in_memory_mode() -> None:
    log = CommercialClassLog(file_path=None)
    log.append(_make_entry("m1"))
    log.append(_make_entry("m2"))
    assert len(log) == 2
    results = log.list_all_by_project("everydayengel")
    assert len(results) == 2


def test_commercial_mix_format_text_contains_all_classes() -> None:
    summary = CommercialMixSummary(
        window_days=7,
        total=4,
        trust_building=2,
        product_near=1,
        recommendation_ready=1,
        direct_offer=0,
        off_thesis_or_monetization_waste=0,
        drift_warning=False,
        drift_hint=None,
    )
    text = summary.format_text()
    assert "Vertrauensaufbau" in text
    assert "Produktnah" in text
    assert "Empfehlungsbereit" in text
    assert "Direktes Angebot" in text
    assert "4 Vorschläge" in text


def test_commercial_mix_format_text_includes_drift_warning() -> None:
    summary = CommercialMixSummary(
        window_days=7,
        total=3,
        trust_building=3,
        product_near=0,
        recommendation_ready=0,
        direct_offer=0,
        off_thesis_or_monetization_waste=0,
        drift_warning=True,
        drift_hint=None,
    )
    text = summary.format_text()
    assert "Drift-Warnung" in text
    assert "Kein produktnaher oder höherer Content in diesem Zeitraum." in text


def test_summarize_commercial_mix_sets_trust_building_drift_hint() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        log = CommercialClassLog(file_path=Path(tmpdir) / "log.json")
        for index in range(4):
            log.append(_make_entry(f"tb{index}", commercial_class=CommercialClass.trust_building))
        summary = summarize_commercial_mix(log, "everydayengel")
    assert summary.drift_hint == "Der Content ist gerade sehr vertrauenslastig. Das ist gut für den Aufbau, aber bald sollten wieder 1–2 produktnähere Ideen dazukommen."


def test_summarize_commercial_mix_sets_recommendation_ready_gap_hint() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        log = CommercialClassLog(file_path=Path(tmpdir) / "log.json")
        for index in range(4):
            log.append(_make_entry(f"tb{index}", commercial_class=CommercialClass.trust_building))
        for index in range(2):
            log.append(_make_entry(f"pn{index}", commercial_class=CommercialClass.product_near))
        summary = summarize_commercial_mix(log, "everydayengel")
    assert summary.drift_hint == "Seit einiger Zeit gab es keine konkrete Empfehlung. Prüfe, ob ein passendes Produkt authentisch in einen Moment passt."
