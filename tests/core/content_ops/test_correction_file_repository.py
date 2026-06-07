"""
Tests for CorrectionFileRepository — durable file-backed persistence.

Covers:
- append + reload from file survives fresh object construction
- list_by_project / list_by_action after reload
- all required fields survive round-trip (including commercial_class, reason_tag, corrected_output)
- file_path=None (in-memory only mode) works without errors
- malformed entries in file are silently skipped
- IdeaCorrectionService with correction_repository persists durably
- in-memory store is secondary (file repo is source of truth)
- runtime wiring includes IdeaCorrectionService
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operator_core.core.content_ops.correction_capture import (
    CommercialClass,
    CorrectionCaptureStore,
    CorrectionFileRepository,
    CorrectionReasonTag,
    CorrectionRecord,
    CorrectionStatus,
    IdeaCorrectionService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    record_id: str = "rec-001",
    project_key: str = "everydayengel",
    action_type: str = "idea",
    status: CorrectionStatus = CorrectionStatus.accepted_as_is,
    reason_tag: CorrectionReasonTag = CorrectionReasonTag.none,
    commercial_class: CommercialClass | None = CommercialClass.trust_building,
    corrected_output: str | None = None,
    proposal_id: str = "prop-001",
) -> CorrectionRecord:
    return CorrectionRecord(
        record_id=record_id,
        project_key=project_key,
        action_type=action_type,
        proposal_id=proposal_id,
        prompt="beim Kochen schwindelig",
        bot_output="Beim Kochen merke ich, dass ich sitzen muss.",
        status=status,
        reason_tag=reason_tag,
        commercial_class=commercial_class,
        corrected_output=corrected_output,
    )


# ---------------------------------------------------------------------------
# CorrectionFileRepository — file-path=None (in-memory mode)
# ---------------------------------------------------------------------------

def test_file_repo_no_file_path_append_does_not_crash() -> None:
    repo = CorrectionFileRepository(file_path=None)
    rec = _make_record()
    repo.append(rec)
    assert repo.get(rec.record_id) is rec


def test_file_repo_no_file_path_list_by_project() -> None:
    repo = CorrectionFileRepository(file_path=None)
    repo.append(_make_record(record_id="r1", project_key="everydayengel"))
    repo.append(_make_record(record_id="r2", project_key="other"))
    assert len(repo.list_by_project("everydayengel")) == 1
    assert len(repo.list_by_project("other")) == 1


# ---------------------------------------------------------------------------
# CorrectionFileRepository — file-backed round-trip
# ---------------------------------------------------------------------------

def test_file_repo_survives_process_restart() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        rec = _make_record(
            record_id="rec-restart-1",
            status=CorrectionStatus.rejected,
            reason_tag=CorrectionReasonTag.tone_off,
            commercial_class=CommercialClass.product_near,
        )

        repo1 = CorrectionFileRepository(file_path=path)
        repo1.append(rec)
        assert path.exists()

        # Simulate restart — fresh object, same file
        repo2 = CorrectionFileRepository(file_path=path)
        loaded = repo2.get("rec-restart-1")
        assert loaded is not None
        assert loaded.record_id == "rec-restart-1"
        assert loaded.status is CorrectionStatus.rejected
        assert loaded.reason_tag is CorrectionReasonTag.tone_off
        assert loaded.commercial_class is CommercialClass.product_near
        assert loaded.project_key == "everydayengel"
        assert loaded.action_type == "idea"
        assert loaded.proposal_id == "prop-001"
        assert loaded.prompt == "beim Kochen schwindelig"
        assert loaded.bot_output == "Beim Kochen merke ich, dass ich sitzen muss."


def test_file_repo_all_required_fields_survive_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        rec = _make_record(
            record_id="rec-fields-1",
            status=CorrectionStatus.accepted_with_edits,
            reason_tag=CorrectionReasonTag.too_free,
            commercial_class=CommercialClass.recommendation_ready,
            corrected_output="Rücken meldet sich nach zehn Minuten stehen — jetzt brauch ich kurz Pause.",
        )

        CorrectionFileRepository(file_path=path).append(rec)
        loaded = CorrectionFileRepository(file_path=path).get("rec-fields-1")

        assert loaded is not None
        assert loaded.status is CorrectionStatus.accepted_with_edits
        assert loaded.reason_tag is CorrectionReasonTag.too_free
        assert loaded.commercial_class is CommercialClass.recommendation_ready
        assert loaded.corrected_output == "Rücken meldet sich nach zehn Minuten stehen — jetzt brauch ich kurz Pause."
        assert "proposal_id" in rec.to_snapshot()
        assert "created_at" in rec.to_snapshot()


def test_file_repo_list_by_project_after_reload() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        repo1 = CorrectionFileRepository(file_path=path)
        repo1.append(_make_record(record_id="r1", project_key="everydayengel"))
        repo1.append(_make_record(record_id="r2", project_key="everydayengel"))
        repo1.append(_make_record(record_id="r3", project_key="other"))

        repo2 = CorrectionFileRepository(file_path=path)
        results = repo2.list_by_project("everydayengel")
        assert len(results) == 2
        assert all(r.project_key == "everydayengel" for r in results)


def test_file_repo_list_by_action_after_reload() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        repo1 = CorrectionFileRepository(file_path=path)
        repo1.append(_make_record(record_id="r1", action_type="idea"))
        repo1.append(_make_record(record_id="r2", action_type="idea"))
        repo1.append(_make_record(record_id="r3", action_type="hook"))

        repo2 = CorrectionFileRepository(file_path=path)
        results = repo2.list_by_action("everydayengel", "idea")
        assert len(results) == 2
        assert all(r.action_type == "idea" for r in results)


def test_file_repo_multiple_appends_accumulate() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        repo = CorrectionFileRepository(file_path=path)
        for i in range(5):
            repo.append(_make_record(record_id=f"rec-multi-{i}"))

        repo2 = CorrectionFileRepository(file_path=path)
        assert len(repo2) == 5


def test_file_repo_latest_for_proposal_uses_newest_record() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        repo = CorrectionFileRepository(file_path=path)
        first = _make_record(record_id="rec-first", status=CorrectionStatus.accepted_as_is)
        second = _make_record(
            record_id="rec-second",
            status=CorrectionStatus.rejected,
            reason_tag=CorrectionReasonTag.tone_off,
        )

        repo.append(first)
        repo.append(second)

        latest = CorrectionFileRepository(file_path=path).latest_for_proposal("everydayengel", "prop-001")
        assert latest is not None
        assert latest.record_id == "rec-second"
        assert latest.status is CorrectionStatus.rejected


def test_file_repo_latest_effective_by_action_keeps_newest_per_proposal() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        repo = CorrectionFileRepository(file_path=path)
        repo.append(_make_record(
            record_id="rec-old-prop-1",
            proposal_id="prop-1",
            status=CorrectionStatus.rejected,
        ))
        repo.append(_make_record(
            record_id="rec-new-prop-1",
            proposal_id="prop-1",
            status=CorrectionStatus.accepted_as_is,
        ))
        repo.append(_make_record(
            record_id="rec-prop-2",
            proposal_id="prop-2",
            status=CorrectionStatus.rejected,
        ))

        records = CorrectionFileRepository(file_path=path).latest_effective_by_action(
            project_key="everydayengel",
            action_type="idea",
        )

        assert {record.proposal_id for record in records} == {"prop-1", "prop-2"}
        assert "rec-old-prop-1" not in {record.record_id for record in records}


def test_file_repo_malformed_entries_are_skipped() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        # Write partially valid JSON — one good entry, two bad
        payload = [
            {"record_id": "good-1", "project_key": "everydayengel", "action_type": "idea",
             "status": "accepted_as_is", "prompt": "", "bot_output": ""},
            {"record_id": "", "project_key": "everydayengel"},  # empty record_id — skipped
            {"not_a_record": True},  # missing required keys — skipped
        ]
        path.write_text(json.dumps(payload), encoding="utf-8")

        repo = CorrectionFileRepository(file_path=path)
        assert len(repo) == 1
        assert repo.get("good-1") is not None


def test_file_repo_nonexistent_file_loads_empty() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "does_not_exist.json"
        repo = CorrectionFileRepository(file_path=path)
        assert len(repo) == 0


# ---------------------------------------------------------------------------
# IdeaCorrectionService with CorrectionFileRepository
# ---------------------------------------------------------------------------

def test_idea_correction_service_persists_via_file_repo() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        store = CorrectionCaptureStore()
        event_log = MagicMock()
        file_repo = CorrectionFileRepository(file_path=path)
        svc = IdeaCorrectionService(
            correction_store=store,
            event_log_service=event_log,
            correction_repository=file_repo,
        )

        record = svc.record_correction(
            project_key="everydayengel",
            proposal_id="prop-durable-1",
            prompt="beim Kochen",
            bot_output="Beim Kochen muss ich kurz pausieren.",
            commercial_class="trust_building",
            status=CorrectionStatus.accepted_as_is,
        )

        # Simulate restart — reload from file
        file_repo2 = CorrectionFileRepository(file_path=path)
        loaded = file_repo2.get(record.record_id)
        assert loaded is not None
        assert loaded.status is CorrectionStatus.accepted_as_is
        assert loaded.commercial_class is CommercialClass.trust_building
        assert loaded.proposal_id == "prop-durable-1"


def test_idea_correction_service_supersedes_previous_rating_for_same_proposal() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        file_repo = CorrectionFileRepository(file_path=path)
        svc = IdeaCorrectionService(
            correction_store=CorrectionCaptureStore(),
            event_log_service=MagicMock(),
            correction_repository=file_repo,
        )

        first = svc.record_correction(
            project_key="everydayengel",
            proposal_id="prop-change-rating",
            prompt="test",
            bot_output="output",
            commercial_class=None,
            status=CorrectionStatus.accepted_as_is,
        )
        second = svc.record_correction(
            project_key="everydayengel",
            proposal_id="prop-change-rating",
            prompt="test",
            bot_output="output",
            commercial_class=None,
            status=CorrectionStatus.rejected,
            reason_tag=CorrectionReasonTag.tone_off,
        )

        assert second.supersedes_record_id == first.record_id
        latest = CorrectionFileRepository(file_path=path).latest_for_proposal(
            "everydayengel",
            "prop-change-rating",
        )
        assert latest is not None
        assert latest.record_id == second.record_id
        assert latest.status is CorrectionStatus.rejected


def test_file_repo_is_source_of_truth_in_memory_store_is_secondary() -> None:
    """After restart, file repo has the record; a fresh in-memory store does not."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        store = CorrectionCaptureStore()
        event_log = MagicMock()
        file_repo = CorrectionFileRepository(file_path=path)
        svc = IdeaCorrectionService(
            correction_store=store,
            event_log_service=event_log,
            correction_repository=file_repo,
        )

        record = svc.record_correction(
            project_key="everydayengel",
            proposal_id="prop-secondary",
            prompt="test",
            bot_output="output",
            commercial_class=None,
            status=CorrectionStatus.rejected,
            reason_tag=CorrectionReasonTag.not_julia,
        )

        # In-memory store has it
        assert store.get(record.record_id) is not None

        # Fresh store (simulating restart) does NOT have it
        fresh_store = CorrectionCaptureStore()
        assert fresh_store.get(record.record_id) is None

        # File repo (reloaded) DOES have it
        reloaded_repo = CorrectionFileRepository(file_path=path)
        assert reloaded_repo.get(record.record_id) is not None


def test_idea_correction_service_commercial_class_survives_file_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "idea_corrections.json"
        svc = IdeaCorrectionService(
            correction_store=CorrectionCaptureStore(),
            event_log_service=MagicMock(),
            correction_repository=CorrectionFileRepository(file_path=path),
        )

        record = svc.record_correction(
            project_key="everydayengel",
            proposal_id="prop-cc",
            prompt="Schlafen mit Bauch",
            bot_output="Das Schlafen wird anders.",
            commercial_class="product_near",
            status=CorrectionStatus.rejected,
            reason_tag=CorrectionReasonTag.too_broad,
        )

        reloaded = CorrectionFileRepository(file_path=path).get(record.record_id)
        assert reloaded is not None
        assert reloaded.commercial_class is CommercialClass.product_near
        assert reloaded.reason_tag is CorrectionReasonTag.too_broad


def test_idea_correction_service_without_file_repo_still_works() -> None:
    """correction_repository=None → falls back gracefully to in-memory only."""
    store = CorrectionCaptureStore()
    event_log = MagicMock()
    svc = IdeaCorrectionService(
        correction_store=store,
        event_log_service=event_log,
        correction_repository=None,
    )

    record = svc.record_correction(
        project_key="everydayengel",
        proposal_id="prop-no-repo",
        prompt="test",
        bot_output="output",
        commercial_class=None,
        status=CorrectionStatus.accepted_as_is,
    )
    assert store.get(record.record_id) is not None
    event_log.log_event.assert_called_once()


# ---------------------------------------------------------------------------
# Runtime wiring — IdeaCorrectionService injected in production bootstrap
# ---------------------------------------------------------------------------

def test_runtime_wires_idea_correction_service() -> None:
    """Inspect OperatorRuntime._start_telegram_polling source to confirm
    IdeaCorrectionService is instantiated and passed to RequestFlowService."""
    import inspect
    from operator_core.runtime import OperatorRuntime

    src = inspect.getsource(OperatorRuntime._start_telegram_polling)  # type: ignore[attr-defined]

    assert "IdeaCorrectionService" in src, (
        "OperatorRuntime._start_telegram_polling must instantiate IdeaCorrectionService"
    )
    assert "idea_correction_service" in src, (
        "OperatorRuntime._start_telegram_polling must pass idea_correction_service to RequestFlowService"
    )
    assert "CorrectionFileRepository" in src, (
        "OperatorRuntime._start_telegram_polling must instantiate CorrectionFileRepository"
    )


def test_runtime_initializes_state_dir_before_correction_repository() -> None:
    """Correction persistence must not reference runtime_state_dir before assignment."""
    import inspect
    from operator_core.runtime import OperatorRuntime

    src = inspect.getsource(OperatorRuntime._start_telegram_polling)  # type: ignore[attr-defined]

    state_dir_index = src.index('runtime_state_dir = PROJECT_ROOT / ".runtime"')
    correction_repo_index = src.index("CorrectionFileRepository(")

    assert state_dir_index < correction_repo_index
