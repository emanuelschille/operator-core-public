"""
TDD tests for the blind-review scoring layer.

Written before any implementation. Each test covers one behavior.
Run order: all fail → implement → all pass.
"""
from __future__ import annotations

import json

from operator_core.core.evaluation.models import (
    BlindReviewEntry,
    BlindReviewExport,
    BlindReviewLinkage,
)
from operator_core.core.evaluation.review_models import (
    ReviewCriterion,
    ReviewEntry,
    ReviewSession,
)
from operator_core.core.evaluation.review_service import ReviewService


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_blind_export() -> BlindReviewExport:
    """Minimal BlindReviewExport with two candidates (A and B)."""
    return BlindReviewExport(
        export_id="bre_test_01",
        created_at="2026-04-15T10:00:00+00:00",
        reviewer_entries=(
            BlindReviewEntry(
                blind_entry_id="entry_candidate_a_aaaa",
                review_case_id="review_case_01",
                source_flow="caption",
                target_platform="instagram",
                generated_output={"items": ["Handy weg. Tee an."]},
                benchmark_run_id="br_test_01",
                reviewer_label="Candidate A",
            ),
            BlindReviewEntry(
                blind_entry_id="entry_candidate_b_bbbb",
                review_case_id="review_case_01",
                source_flow="caption",
                target_platform="instagram",
                generated_output={"items": ["Bildschirm aus. Stille rein."]},
                benchmark_run_id="br_test_01",
                reviewer_label="Candidate B",
            ),
        ),
        internal_linkage=(
            BlindReviewLinkage(
                blind_entry_id="entry_candidate_a_aaaa",
                review_case_id="review_case_01",
                evaluation_case_id="ec_test_01",
                benchmark_run_id="br_test_01",
                candidate_id="cand_aaaa",
                provider_name="openai",
                model_name="gpt-4o",
                task_role="writer",
                job_id="job_01",
                run_id="run_01",
                selected_snapshot_ids=("as_platform", "as_cross"),
                writer_brief_id="wb_01",
                evidence_pack_id="ep_01",
            ),
            BlindReviewLinkage(
                blind_entry_id="entry_candidate_b_bbbb",
                review_case_id="review_case_01",
                evaluation_case_id="ec_test_01",
                benchmark_run_id="br_test_01",
                candidate_id="cand_bbbb",
                provider_name="anthropic",
                model_name="claude-sonnet-4-6",
                task_role="benchmark_writer",
                job_id="job_01",
                run_id="run_01",
                selected_snapshot_ids=("as_platform", "as_cross"),
                writer_brief_id="wb_01",
                evidence_pack_id="ep_01",
            ),
        ),
        evaluation_case_id="ec_test_01",
        benchmark_run_id="br_test_01",
    )


def _sample_payload() -> dict:
    return {
        "benchmark_run_id": "br_test_01",
        "evaluation_case_id": "ec_test_01",
        "reviewer_id": "human_01",
        "entries": {
            "Candidate A": {
                "naturalness": 4,
                "fit": 5,
                "less_ai_like": 3,
                "usability": 4,
                "overall": 4,
                "notes": "Very natural tone",
            },
            "Candidate B": {
                "naturalness": 3,
                "fit": 4,
                "less_ai_like": 4,
                "usability": 3,
                "overall": 3,
            },
        },
        "winner": "Candidate A",
        "created_at": "2026-04-15T10:30:00+00:00",
    }


# ---------------------------------------------------------------------------
# Model contract tests
# ---------------------------------------------------------------------------

def test_review_criterion_holds_key_label_score() -> None:
    """ReviewCriterion stores criterion_key, criterion_label, score, optional notes."""
    criterion = ReviewCriterion(
        criterion_key="naturalness",
        criterion_label="Natürlichkeit",
        score=4,
    )
    assert criterion.criterion_key == "naturalness"
    assert criterion.criterion_label == "Natürlichkeit"
    assert criterion.score == 4
    assert criterion.notes is None


def test_review_entry_links_to_session_and_captures_criteria() -> None:
    """ReviewEntry stores review_session_id, reviewer_label, and criteria tuple."""
    criterion = ReviewCriterion(
        criterion_key="overall",
        criterion_label="Gesamtpräferenz",
        score=5,
    )
    entry = ReviewEntry(
        entry_id="re_aabbcc",
        review_session_id="rs_test01",
        reviewer_label="Candidate A",
        criteria=(criterion,),
    )
    assert entry.entry_id == "re_aabbcc"
    assert entry.review_session_id == "rs_test01"
    assert entry.reviewer_label == "Candidate A"
    assert len(entry.criteria) == 1
    assert entry.criteria[0].score == 5
    assert entry.notes is None


def test_review_session_holds_run_case_and_entries() -> None:
    """ReviewSession stores benchmark_run_id, evaluation_case_id, reviewer_id, entries."""
    session = ReviewSession(
        review_session_id="rs_aabbccdd",
        benchmark_run_id="br_test_01",
        evaluation_case_id="ec_test_01",
        reviewer_id="human_01",
        entries=(),
        winner_reviewer_label="Candidate A",
        created_at="2026-04-15T10:30:00+00:00",
    )
    assert session.review_session_id == "rs_aabbccdd"
    assert session.benchmark_run_id == "br_test_01"
    assert session.evaluation_case_id == "ec_test_01"
    assert session.reviewer_id == "human_01"
    assert session.winner_reviewer_label == "Candidate A"


# ---------------------------------------------------------------------------
# ReviewService.import_results tests
# ---------------------------------------------------------------------------

def test_review_service_import_creates_session_with_correct_ids() -> None:
    """import_results produces a ReviewSession with rs_ prefixed id and correct linkage fields."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    assert session.review_session_id.startswith("rs_")
    assert session.benchmark_run_id == "br_test_01"
    assert session.evaluation_case_id == "ec_test_01"
    assert session.reviewer_id == "human_01"


def test_review_service_import_creates_entry_per_candidate() -> None:
    """import_results creates one ReviewEntry per entry in the payload."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    assert len(session.entries) == 2
    labels = {e.reviewer_label for e in session.entries}
    assert labels == {"Candidate A", "Candidate B"}


def test_review_service_import_captures_all_five_criteria_per_entry() -> None:
    """Each ReviewEntry gets exactly 5 ReviewCriterion objects for the standard rubric."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    for entry in session.entries:
        assert len(entry.criteria) == 5
        keys = {c.criterion_key for c in entry.criteria}
        assert keys == {"naturalness", "fit", "less_ai_like", "usability", "overall"}


def test_review_service_import_stores_correct_scores() -> None:
    """Scores from payload are stored correctly on the right criteria."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    entry_a = next(e for e in session.entries if e.reviewer_label == "Candidate A")
    scores = {c.criterion_key: c.score for c in entry_a.criteria}
    assert scores["naturalness"] == 4
    assert scores["fit"] == 5
    assert scores["less_ai_like"] == 3
    assert scores["usability"] == 4
    assert scores["overall"] == 4


def test_review_service_import_preserves_optional_notes() -> None:
    """Notes field on a candidate entry is stored when present."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    entry_a = next(e for e in session.entries if e.reviewer_label == "Candidate A")
    assert entry_a.notes == "Very natural tone"


def test_review_service_import_entry_without_notes_has_none() -> None:
    """Entry notes default to None when not in payload."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    entry_b = next(e for e in session.entries if e.reviewer_label == "Candidate B")
    assert entry_b.notes is None


def test_review_service_import_sets_winner() -> None:
    """winner field from payload is stored as winner_reviewer_label."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    assert session.winner_reviewer_label == "Candidate A"


def test_review_service_import_with_no_winner_stores_none() -> None:
    """winner_reviewer_label is None when payload omits 'winner'."""
    service = ReviewService()
    payload = _sample_payload()
    del payload["winner"]
    session = service.import_results(payload)

    assert session.winner_reviewer_label is None


def test_review_service_import_uses_payload_created_at() -> None:
    """created_at from payload is stored on the session."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    assert session.created_at == "2026-04-15T10:30:00+00:00"


def test_review_service_import_kwarg_created_at_overrides_payload() -> None:
    """created_at kwarg overrides the value in the payload dict."""
    service = ReviewService()
    session = service.import_results(
        _sample_payload(), created_at="2026-04-15T11:00:00+00:00"
    )
    assert session.created_at == "2026-04-15T11:00:00+00:00"


def test_review_service_import_entry_ids_are_stable_and_prefixed() -> None:
    """Each ReviewEntry.entry_id starts with 're_' and is deterministic."""
    service = ReviewService()
    session1 = service.import_results(_sample_payload())
    session2 = service.import_results(_sample_payload())

    ids1 = {e.entry_id for e in session1.entries}
    ids2 = {e.entry_id for e in session2.entries}
    for entry_id in ids1:
        assert entry_id.startswith("re_")
    assert ids1 == ids2


# ---------------------------------------------------------------------------
# Reviewer-facing structure must not expose provider info
# ---------------------------------------------------------------------------

def test_review_session_has_no_provider_info() -> None:
    """ReviewSession and its entries must not contain provider_name or model_name."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    session_repr = repr(session)
    assert "provider_name" not in session_repr
    assert "model_name" not in session_repr
    assert "openai" not in session_repr
    assert "anthropic" not in session_repr
    assert "gpt" not in session_repr
    assert "claude" not in session_repr


# ---------------------------------------------------------------------------
# JSON import
# ---------------------------------------------------------------------------

def test_review_service_imports_from_json_string() -> None:
    """import_results_from_json parses a JSON string and returns a ReviewSession."""
    service = ReviewService()
    json_str = json.dumps(_sample_payload())
    session = service.import_results_from_json(json_str)

    assert session.benchmark_run_id == "br_test_01"
    assert len(session.entries) == 2
    assert session.winner_reviewer_label == "Candidate A"


# ---------------------------------------------------------------------------
# Internal linkage resolution
# ---------------------------------------------------------------------------

def test_resolve_internal_linkage_maps_reviewer_label_to_provider_info() -> None:
    """resolve_internal_linkage returns a dict keyed by reviewer_label with full provider info."""
    service = ReviewService()
    session = service.import_results(_sample_payload())
    export = _make_blind_export()

    linkage = service.resolve_internal_linkage(session, export)

    assert "Candidate A" in linkage
    assert "Candidate B" in linkage
    assert linkage["Candidate A"]["provider_name"] == "openai"
    assert linkage["Candidate A"]["model_name"] == "gpt-4o"
    assert linkage["Candidate B"]["provider_name"] == "anthropic"
    assert linkage["Candidate B"]["model_name"] == "claude-sonnet-4-6"


def test_resolve_internal_linkage_includes_candidate_and_case_ids() -> None:
    """Linkage result contains candidate_id and evaluation_case_id for full traceability."""
    service = ReviewService()
    session = service.import_results(_sample_payload())
    export = _make_blind_export()

    linkage = service.resolve_internal_linkage(session, export)

    assert linkage["Candidate A"]["candidate_id"] == "cand_aaaa"
    assert linkage["Candidate A"]["evaluation_case_id"] == "ec_test_01"
    assert linkage["Candidate B"]["candidate_id"] == "cand_bbbb"


def test_resolve_internal_linkage_includes_writer_brief_and_evidence_pack() -> None:
    """Linkage result carries writer_brief_id and evidence_pack_id."""
    service = ReviewService()
    session = service.import_results(_sample_payload())
    export = _make_blind_export()

    linkage = service.resolve_internal_linkage(session, export)

    assert linkage["Candidate A"]["writer_brief_id"] == "wb_01"
    assert linkage["Candidate A"]["evidence_pack_id"] == "ep_01"


def test_resolve_internal_linkage_returns_empty_for_unknown_label() -> None:
    """Labels in the session that have no match in the export get an empty dict."""
    service = ReviewService()
    payload = _sample_payload()
    # Add a label that doesn't exist in the export
    payload["entries"]["Candidate Z"] = {"naturalness": 1, "fit": 1, "less_ai_like": 1, "usability": 1, "overall": 1}
    session = service.import_results(payload)
    export = _make_blind_export()

    linkage = service.resolve_internal_linkage(session, export)

    assert linkage.get("Candidate Z", {}) == {}


def test_resolve_internal_linkage_does_not_modify_review_session() -> None:
    """Calling resolve_internal_linkage does not add provider info to the session."""
    service = ReviewService()
    session = service.import_results(_sample_payload())
    export = _make_blind_export()

    _ = service.resolve_internal_linkage(session, export)

    # Session still has no provider info
    session_repr = repr(session)
    assert "openai" not in session_repr
    assert "anthropic" not in session_repr


# ---------------------------------------------------------------------------
# Rubric labels
# ---------------------------------------------------------------------------

def test_review_service_criterion_labels_are_human_readable() -> None:
    """Criteria imported from dict get human-readable German labels, not raw keys."""
    service = ReviewService()
    session = service.import_results(_sample_payload())

    entry_a = next(e for e in session.entries if e.reviewer_label == "Candidate A")
    labels = {c.criterion_key: c.criterion_label for c in entry_a.criteria}

    assert labels["naturalness"] != "naturalness"
    assert labels["overall"] != "overall"
    assert len(labels["naturalness"]) > 5
    assert len(labels["overall"]) > 5
