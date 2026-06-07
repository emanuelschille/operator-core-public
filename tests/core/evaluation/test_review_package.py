"""
TDD tests for the blind-review package builder and roundtrip import.

Written before any implementation (RED phase).
Covers: package creation, template generation, full roundtrip import, linkage.
"""
from __future__ import annotations

import copy

from operator_core.core.evaluation.models import (
    BlindReviewEntry,
    BlindReviewExport,
    BlindReviewLinkage,
)
from operator_core.core.evaluation.review_models import (
    ReviewPackage,
    ReviewPackageCandidate,
)
from operator_core.core.evaluation.review_service import ReviewService


# ---------------------------------------------------------------------------
# shared fixture
# ---------------------------------------------------------------------------

def _make_export_four_candidates() -> BlindReviewExport:
    """BlindReviewExport with four candidates matching a real caption run."""

    def _entry(label: str, bid: str, items: list[str]) -> BlindReviewEntry:
        return BlindReviewEntry(
            blind_entry_id=bid,
            review_case_id="review_case_caption",
            source_flow="caption",
            target_platform="instagram",
            generated_output={"action_type": "caption", "items": items},
            benchmark_run_id="br_caption_01",
            reviewer_label=label,
        )

    def _link(label: str, bid: str, provider: str, model: str, cid: str) -> BlindReviewLinkage:
        return BlindReviewLinkage(
            blind_entry_id=bid,
            review_case_id="review_case_caption",
            evaluation_case_id="ec_caption_01",
            benchmark_run_id="br_caption_01",
            candidate_id=cid,
            provider_name=provider,
            model_name=model,
            task_role="benchmark_writer",
            job_id="job_01",
            run_id="run_01",
            selected_snapshot_ids=("as_platform", "as_cross"),
            writer_brief_id="wb_caption_01",
            evidence_pack_id="ep_caption_01",
        )

    return BlindReviewExport(
        export_id="bre_caption_01",
        created_at="2026-04-15T12:00:00+00:00",
        reviewer_entries=(
            _entry("Candidate A", "entry_a_aaaa", ["Handy weg. Tee an.", "Klingt simpel."]),
            _entry("Candidate B", "entry_b_bbbb", ["Bildschirm aus. Stille rein."]),
            _entry("Candidate C", "entry_c_cccc", ["Abends runterfahren statt weiter funktionieren."]),
            _entry("Candidate D", "entry_d_dddd", ["Vier Minuten, die meinen Abend verändern."]),
        ),
        internal_linkage=(
            _link("Candidate A", "entry_a_aaaa", "openai", "gpt-4o", "cand_aaaa"),
            _link("Candidate B", "entry_b_bbbb", "openai", "gpt-4o-2024-08-06", "cand_bbbb"),
            _link("Candidate C", "entry_c_cccc", "openai", "gpt-5.4-mini", "cand_cccc"),
            _link("Candidate D", "entry_d_dddd", "anthropic", "claude-sonnet-4-6", "cand_dddd"),
        ),
        evaluation_case_id="ec_caption_01",
        benchmark_run_id="br_caption_01",
    )


# ---------------------------------------------------------------------------
# ReviewPackageCandidate model contract
# ---------------------------------------------------------------------------

def test_review_package_candidate_holds_label_flow_platform_items() -> None:
    """ReviewPackageCandidate stores reviewer_label, source_flow, target_platform, content_items."""
    candidate = ReviewPackageCandidate(
        reviewer_label="Candidate A",
        source_flow="caption",
        target_platform="instagram",
        content_items=("Handy weg. Tee an.",),
    )
    assert candidate.reviewer_label == "Candidate A"
    assert candidate.source_flow == "caption"
    assert candidate.target_platform == "instagram"
    assert candidate.content_items == ("Handy weg. Tee an.",)


# ---------------------------------------------------------------------------
# ReviewPackage model contract
# ---------------------------------------------------------------------------

def test_review_package_holds_ids_and_candidates() -> None:
    """ReviewPackage stores package_id, benchmark_run_id, evaluation_case_id, candidates."""
    pkg = ReviewPackage(
        package_id="rp_test01",
        benchmark_run_id="br_01",
        evaluation_case_id="ec_01",
        created_at="2026-04-15T12:00:00+00:00",
        candidates=(),
        rubric_labels={},
        import_template={},
    )
    assert pkg.package_id == "rp_test01"
    assert pkg.benchmark_run_id == "br_01"
    assert pkg.evaluation_case_id == "ec_01"


# ---------------------------------------------------------------------------
# build_review_package behaviour
# ---------------------------------------------------------------------------

def test_build_review_package_assigns_rp_prefixed_id() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    assert pkg.package_id.startswith("rp_")


def test_build_review_package_carries_benchmark_and_case_ids() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    assert pkg.benchmark_run_id == "br_caption_01"
    assert pkg.evaluation_case_id == "ec_caption_01"


def test_build_review_package_has_one_candidate_per_reviewer_entry() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    assert len(pkg.candidates) == 4
    labels = [c.reviewer_label for c in pkg.candidates]
    assert labels == ["Candidate A", "Candidate B", "Candidate C", "Candidate D"]


def test_build_review_package_candidate_carries_content_items() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    cand_a = next(c for c in pkg.candidates if c.reviewer_label == "Candidate A")
    assert "Handy weg. Tee an." in cand_a.content_items
    assert "Klingt simpel." in cand_a.content_items


def test_build_review_package_candidate_carries_flow_and_platform() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    for cand in pkg.candidates:
        assert cand.source_flow == "caption"
        assert cand.target_platform == "instagram"


def test_build_review_package_candidates_have_no_provider_info() -> None:
    """The reviewer-facing package must not leak provider or model names."""
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    pkg_repr = repr(pkg)
    assert "openai" not in pkg_repr
    assert "anthropic" not in pkg_repr
    assert "gpt" not in pkg_repr
    assert "claude" not in pkg_repr
    assert "gpt-4o" not in pkg_repr
    assert "claude-sonnet" not in pkg_repr


def test_build_review_package_includes_rubric_labels() -> None:
    """Package rubric_labels maps all five criterion keys to human-readable labels."""
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    assert isinstance(pkg.rubric_labels, dict)
    for key in ("naturalness", "fit", "less_ai_like", "usability", "overall"):
        assert key in pkg.rubric_labels
        assert len(pkg.rubric_labels[key]) > 3


# ---------------------------------------------------------------------------
# import_template in the package
# ---------------------------------------------------------------------------

def test_build_review_package_template_has_correct_top_level_keys() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    tmpl = pkg.import_template
    for key in ("benchmark_run_id", "evaluation_case_id", "reviewer_id", "entries", "winner"):
        assert key in tmpl, f"Missing key: {key}"


def test_build_review_package_template_ids_match_export() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    tmpl = pkg.import_template
    assert tmpl["benchmark_run_id"] == "br_caption_01"
    assert tmpl["evaluation_case_id"] == "ec_caption_01"


def test_build_review_package_template_reviewer_id_is_blank() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    assert pkg.import_template["reviewer_id"] == ""


def test_build_review_package_template_winner_is_blank() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    assert pkg.import_template["winner"] == ""


def test_build_review_package_template_entries_has_all_candidate_labels() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    entries = pkg.import_template["entries"]
    assert set(entries.keys()) == {"Candidate A", "Candidate B", "Candidate C", "Candidate D"}


def test_build_review_package_template_entry_has_all_five_criteria_slots() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    for entry_data in pkg.import_template["entries"].values():
        for key in ("naturalness", "fit", "less_ai_like", "usability", "overall"):
            assert key in entry_data, f"Missing criterion slot: {key}"


def test_build_review_package_template_score_slots_are_zero() -> None:
    """Unfilled score slots are 0, signalling 'not yet scored'."""
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    for entry_data in pkg.import_template["entries"].values():
        for key in ("naturalness", "fit", "less_ai_like", "usability", "overall"):
            assert entry_data[key] == 0


def test_build_review_package_template_notes_slot_is_empty_string() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    for entry_data in pkg.import_template["entries"].values():
        assert entry_data.get("notes") == ""


# ---------------------------------------------------------------------------
# Full roundtrip: fill template → import → session + linkage
# ---------------------------------------------------------------------------

def _fill_template(template: dict) -> dict:
    """Simulate a human reviewer filling in the template."""
    filled = copy.deepcopy(template)
    filled["reviewer_id"] = "julia_01"
    filled["winner"] = "Candidate D"
    scores = {
        "Candidate A": (4, 5, 3, 4, 4),
        "Candidate B": (3, 4, 4, 3, 3),
        "Candidate C": (4, 4, 4, 4, 4),
        "Candidate D": (5, 5, 5, 5, 5),
    }
    keys = ("naturalness", "fit", "less_ai_like", "usability", "overall")
    for label, score_tuple in scores.items():
        for criterion_key, score in zip(keys, score_tuple):
            filled["entries"][label][criterion_key] = score
    filled["entries"]["Candidate D"]["notes"] = "Klingt am natürlichsten."
    return filled


def test_roundtrip_import_produces_review_session_with_correct_run_and_case() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    filled = _fill_template(pkg.import_template)
    session = service.import_results(filled)

    assert session.benchmark_run_id == "br_caption_01"
    assert session.evaluation_case_id == "ec_caption_01"
    assert session.reviewer_id == "julia_01"


def test_roundtrip_import_session_has_four_entries() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    filled = _fill_template(pkg.import_template)
    session = service.import_results(filled)

    assert len(session.entries) == 4


def test_roundtrip_import_session_records_winner() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    filled = _fill_template(pkg.import_template)
    session = service.import_results(filled)

    assert session.winner_reviewer_label == "Candidate D"


def test_roundtrip_import_session_scores_are_correct() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    filled = _fill_template(pkg.import_template)
    session = service.import_results(filled)

    entry_d = next(e for e in session.entries if e.reviewer_label == "Candidate D")
    scores = {c.criterion_key: c.score for c in entry_d.criteria}
    assert scores["naturalness"] == 5
    assert scores["overall"] == 5

    entry_a = next(e for e in session.entries if e.reviewer_label == "Candidate A")
    scores_a = {c.criterion_key: c.score for c in entry_a.criteria}
    assert scores_a["naturalness"] == 4
    assert scores_a["fit"] == 5


def test_roundtrip_import_session_preserves_notes() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    filled = _fill_template(pkg.import_template)
    session = service.import_results(filled)

    entry_d = next(e for e in session.entries if e.reviewer_label == "Candidate D")
    assert entry_d.notes == "Klingt am natürlichsten."


def test_roundtrip_linkage_resolves_provider_after_import() -> None:
    """After a roundtrip import, resolve_internal_linkage returns provider info."""
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    filled = _fill_template(pkg.import_template)
    session = service.import_results(filled)

    linkage = service.resolve_internal_linkage(session, export)

    assert linkage["Candidate A"]["provider_name"] == "openai"
    assert linkage["Candidate A"]["model_name"] == "gpt-4o"
    assert linkage["Candidate D"]["provider_name"] == "anthropic"
    assert linkage["Candidate D"]["model_name"] == "claude-sonnet-4-6"


def test_roundtrip_linkage_has_candidate_and_brief_ids() -> None:
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    filled = _fill_template(pkg.import_template)
    session = service.import_results(filled)

    linkage = service.resolve_internal_linkage(session, export)

    assert linkage["Candidate A"]["candidate_id"] == "cand_aaaa"
    assert linkage["Candidate A"]["writer_brief_id"] == "wb_caption_01"
    assert linkage["Candidate D"]["candidate_id"] == "cand_dddd"


def test_roundtrip_session_has_no_provider_info_after_import() -> None:
    """Even after a full roundtrip the session itself must contain no provider info."""
    service = ReviewService()
    export = _make_export_four_candidates()
    pkg = service.build_review_package(export)
    filled = _fill_template(pkg.import_template)
    session = service.import_results(filled)

    session_repr = repr(session)
    assert "openai" not in session_repr
    assert "anthropic" not in session_repr
    assert "gpt-4o" not in session_repr
    assert "claude-sonnet" not in session_repr
