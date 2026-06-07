"""
Tests for CorrectionCapture, CommercialClass, and classify_commercial.

Grounded in:
  docs/everydayengel/correction_capture_taxonomy.md
  docs/everydayengel/content_commercial_classification.md
"""
from __future__ import annotations

import pytest
from datetime import timezone
from unittest.mock import MagicMock

from operator_core.core.content_ops.correction_capture import (
    CommercialClass,
    CorrectionCaptureStore,
    CorrectionReasonTag,
    CorrectionRecord,
    CorrectionStatus,
    classify_commercial,
)
from operator_core.core.content_ops.models import ContentOpResult


# ---------------------------------------------------------------------------
# CorrectionStatus — locked enum
# ---------------------------------------------------------------------------

def test_correction_status_values() -> None:
    assert CorrectionStatus.accepted_as_is.value == "accepted_as_is"
    assert CorrectionStatus.accepted_with_edits.value == "accepted_with_edits"
    assert CorrectionStatus.rejected.value == "rejected"


def test_correction_status_rejects_free_text() -> None:
    with pytest.raises(ValueError):
        CorrectionStatus("gut aber falsch")


def test_correction_status_from_valid_string() -> None:
    assert CorrectionStatus("rejected") is CorrectionStatus.rejected


# ---------------------------------------------------------------------------
# CorrectionReasonTag — locked enum
# ---------------------------------------------------------------------------

def test_reason_tag_covers_full_taxonomy() -> None:
    expected = {
        "none", "too_literal", "too_free", "moment_missed",
        "tone_off", "not_julia", "too_broad", "too_loud",
        "too_producty", "weak_hook", "weak_clarity",
        "good_but_wrong_platform",
    }
    actual = {t.value for t in CorrectionReasonTag}
    assert actual == expected


def test_reason_tag_rejects_free_text() -> None:
    with pytest.raises(ValueError):
        CorrectionReasonTag("klingt komisch")


def test_reason_tag_from_valid_string() -> None:
    assert CorrectionReasonTag("tone_off") is CorrectionReasonTag.tone_off


# ---------------------------------------------------------------------------
# CommercialClass — locked enum
# ---------------------------------------------------------------------------

def test_commercial_class_covers_full_taxonomy() -> None:
    expected = {
        "trust_building", "product_near", "recommendation_ready",
        "direct_offer", "off_thesis_or_monetization_waste",
    }
    actual = {c.value for c in CommercialClass}
    assert actual == expected


def test_commercial_class_rejects_free_text() -> None:
    with pytest.raises(ValueError):
        CommercialClass("irgendwas")


# ---------------------------------------------------------------------------
# CorrectionRecord — creation and snapshot
# ---------------------------------------------------------------------------

def test_correction_record_accepted_as_is() -> None:
    rec = CorrectionRecord(
        record_id="rec-001",
        project_key="everydayengel",
        action_type="idea",
        proposal_id="prop-001",
        prompt="beim Kochen schwindelig",
        bot_output="Beim Kochen merke ich, dass ich mich hinsetzen muss.",
        status=CorrectionStatus.accepted_as_is,
        commercial_class=CommercialClass.trust_building,
    )
    assert rec.status is CorrectionStatus.accepted_as_is
    assert rec.reason_tag is CorrectionReasonTag.none
    assert rec.corrected_output is None
    assert rec.commercial_class is CommercialClass.trust_building


def test_correction_record_rejected_with_reason() -> None:
    rec = CorrectionRecord(
        record_id="rec-002",
        project_key="everydayengel",
        action_type="idea",
        proposal_id="prop-002",
        prompt="beim Kochen",
        bot_output="Beim Kochen — das klassische Alltagsproblem vieler Schwangerer.",
        status=CorrectionStatus.rejected,
        reason_tag=CorrectionReasonTag.too_broad,
        commercial_class=CommercialClass.trust_building,
    )
    assert rec.status is CorrectionStatus.rejected
    assert rec.reason_tag is CorrectionReasonTag.too_broad


def test_correction_record_accepted_with_edits_has_corrected_output() -> None:
    rec = CorrectionRecord(
        record_id="rec-003",
        project_key="everydayengel",
        action_type="idea",
        proposal_id="prop-003",
        prompt="",
        bot_output="Ich merke, wie sich alles verändert.",
        status=CorrectionStatus.accepted_with_edits,
        reason_tag=CorrectionReasonTag.too_free,
        corrected_output="Heute beim Aufstehen gemerkt: mein Körper braucht jetzt länger.",
        commercial_class=CommercialClass.trust_building,
    )
    assert rec.corrected_output is not None
    assert "Körper" in rec.corrected_output


def test_correction_record_to_snapshot_roundtrip() -> None:
    rec = CorrectionRecord(
        record_id="rec-004",
        project_key="everydayengel",
        action_type="idea",
        proposal_id="prop-004",
        prompt="schuhe anziehen",
        bot_output="Schuhe anziehen ist plötzlich keine Kleinigkeit mehr.",
        status=CorrectionStatus.accepted_as_is,
        commercial_class=CommercialClass.trust_building,
    )
    snap = rec.to_snapshot()
    assert snap["record_id"] == "rec-004"
    assert snap["proposal_id"] == "prop-004"
    assert snap["status"] == "accepted_as_is"
    assert snap["reason_tag"] == "none"
    assert snap["commercial_class"] == "trust_building"
    assert snap["corrected_output"] is None
    assert "created_at" in snap


def test_correction_record_snapshot_has_all_fields() -> None:
    rec = CorrectionRecord(
        record_id="rec-005",
        project_key="everydayengel",
        action_type="idea",
        proposal_id="prop-005",
        prompt="test",
        bot_output="test output",
        status=CorrectionStatus.rejected,
        reason_tag=CorrectionReasonTag.tone_off,
        commercial_class=CommercialClass.product_near,
        corrected_output="better output",
    )
    snap = rec.to_snapshot()
    required_keys = {
        "record_id", "project_key", "action_type", "proposal_id", "prompt",
        "bot_output", "status", "commercial_class", "reason_tag",
        "corrected_output", "supersedes_record_id", "created_at",
    }
    assert required_keys <= snap.keys()


# ---------------------------------------------------------------------------
# CorrectionCaptureStore
# ---------------------------------------------------------------------------

def test_store_record_and_retrieve() -> None:
    store = CorrectionCaptureStore()
    rec = CorrectionRecord(
        record_id="r1",
        project_key="everydayengel",
        action_type="idea",
        proposal_id="prop-r1",
        prompt="test",
        bot_output="output",
        status=CorrectionStatus.accepted_as_is,
    )
    store.record(rec)
    assert store.get("r1") is rec
    assert len(store) == 1


def test_store_list_by_project() -> None:
    store = CorrectionCaptureStore()
    for i, proj in enumerate(["everydayengel", "other", "everydayengel"]):
        store.record(CorrectionRecord(
            record_id=f"r{i}",
            project_key=proj,
            action_type="idea",
            proposal_id=f"prop-{i}",
            prompt="",
            bot_output=f"output {i}",
            status=CorrectionStatus.accepted_as_is,
        ))
    results = store.list_by_project("everydayengel")
    assert len(results) == 2
    assert all(r.project_key == "everydayengel" for r in results)


def test_store_list_by_action() -> None:
    store = CorrectionCaptureStore()
    for i, action in enumerate(["idea", "hook", "idea"]):
        store.record(CorrectionRecord(
            record_id=f"r-action-{i}",
            project_key="everydayengel",
            action_type=action,
            proposal_id=f"prop-action-{i}",
            prompt="",
            bot_output=f"output {i}",
            status=CorrectionStatus.accepted_as_is,
        ))
    results = store.list_by_action("everydayengel", "idea")
    assert len(results) == 2


def test_store_get_missing_returns_none() -> None:
    store = CorrectionCaptureStore()
    assert store.get("nonexistent") is None


# ---------------------------------------------------------------------------
# classify_commercial — rule-based classifier
# ---------------------------------------------------------------------------

def test_trust_building_lived_moment() -> None:
    text = "Beim Kochen merke ich inzwischen manchmal plötzlich, dass ich mich hinsetzen muss."
    assert classify_commercial(text) is CommercialClass.trust_building


def test_trust_building_night_observation() -> None:
    text = "Nächte sehen gerade wirklich anders aus. Ich schlafe irgendwie nie ganz durch."
    assert classify_commercial(text) is CommercialClass.trust_building


def test_trust_building_dizziness_moment() -> None:
    text = "Im Supermarkt merke ich manchmal plötzlich, dass ich kurz eine Pause brauche."
    assert classify_commercial(text) is CommercialClass.trust_building


def test_product_near_kissen_signal() -> None:
    text = "Das Schlafen hat sich so verändert — ich brauche inzwischen drei Kissen."
    assert classify_commercial(text) is CommercialClass.product_near


def test_product_near_sitzmoeglichkeit_signal() -> None:
    text = "Seit der Schwangerschaft brauche ich beim Kochen eine Sitzmöglichkeit in der Nähe."
    assert classify_commercial(text) is CommercialClass.product_near


def test_recommendation_ready_signal() -> None:
    text = "Dieses Kissen hat mir die letzten Wochen echt geholfen."
    assert classify_commercial(text) is CommercialClass.recommendation_ready


def test_recommendation_ready_seit_ssw_signal() -> None:
    text = "Ich hab das jetzt seit SSW 28 und es macht einen echten Unterschied."
    assert classify_commercial(text) is CommercialClass.recommendation_ready


def test_direct_offer_anzeige_signal() -> None:
    text = "*Anzeige — Kooperation mit [brand]. Code JULIA10 gibt euch 10%."
    assert classify_commercial(text) is CommercialClass.direct_offer


def test_direct_offer_code_signal() -> None:
    text = "Code JULIA10 gibt euch 10% auf das Kissen."
    assert classify_commercial(text) is CommercialClass.direct_offer


def test_direct_offer_beats_product_near() -> None:
    """direct_offer signal should win over product_near signal (priority ordering)."""
    text = "Mit Kissen schlafen + Code JULIA15 für 15% Rabatt."
    assert classify_commercial(text) is CommercialClass.direct_offer


def test_recommendation_beats_product_near() -> None:
    """recommendation_ready should win over product_near (priority ordering)."""
    text = "Das Kissen — ich kann es nur empfehlen, wirklich geholfen."
    result = classify_commercial(text)
    assert result in (CommercialClass.recommendation_ready, CommercialClass.product_near)


def test_classify_commercial_case_insensitive() -> None:
    text = "ANZEIGE — Kooperation mit einer Marke."
    assert classify_commercial(text) is CommercialClass.direct_offer


# ---------------------------------------------------------------------------
# ContentOpResult.commercial_class field
# ---------------------------------------------------------------------------

def test_content_op_result_has_commercial_class_field() -> None:
    result = ContentOpResult(
        lane_name="content_ops",
        project_key="everydayengel",
        action_type="idea",
        command_body="beim Kochen",
        title="Content idea",
        summary="Generated.",
        items=("Beim Kochen merke ich plötzlich, dass ich sitzen muss.",),
        commercial_class="trust_building",
    )
    assert result.commercial_class == "trust_building"


def test_content_op_result_commercial_class_defaults_none() -> None:
    result = ContentOpResult(
        lane_name="content_ops",
        project_key="everydayengel",
        action_type="idea",
        command_body="",
        title="Idea",
        summary="Done.",
        items=(),
    )
    assert result.commercial_class is None


def test_content_op_result_snapshot_includes_commercial_class() -> None:
    result = ContentOpResult(
        lane_name="content_ops",
        project_key="everydayengel",
        action_type="idea",
        command_body="test",
        title="Idea",
        summary="Done.",
        items=("Some idea.",),
        commercial_class="product_near",
    )
    snap = result.to_snapshot()
    assert snap["commercial_class"] == "product_near"


# ---------------------------------------------------------------------------
# generate_idea_from_foundation emits commercial_class
# ---------------------------------------------------------------------------

def _make_idea_svc() -> object:
    from operator_core.core.content_ops.service import ContentOpsService

    mock_resp = MagicMock()
    mock_resp.output_text = (
        "Beim Kochen merke ich inzwischen manchmal plötzlich, "
        "dass ich mich hinsetzen muss, weil mir schwindelig wird."
    )
    mock_resp.model = "gpt-test"

    mock_openai = MagicMock()
    mock_openai.complete_messages.return_value = mock_resp

    return ContentOpsService(
        docs_loader=MagicMock(),
        openai_service=mock_openai,
    )


def _make_foundation_result() -> object:
    from operator_core.core.analysis_foundation.models import (
        AnalysisFoundationResult, AnalysisSnapshot, EvidencePack, ModelExecutionMeta, WriterBrief,
    )
    meta = ModelExecutionMeta(
        provider_name="openai", model_name="gpt-test",
        task_role="analysis_control", status="prepared",
    )
    snapshot = AnalysisSnapshot(
        snapshot_id="as-test",
        project_key="everydayengel",
        scope="platform",
        created_at="2026-04-22T00:00:00+00:00",
        title="Test snapshot",
        summary="Test",
        platform_key="tiktok",
        analytics_summary_lines=(),
        rule_summary_lines=(),
        source_refs=(),
    )
    brief = WriterBrief(
        brief_id="wb-test",
        project_key="everydayengel",
        created_at="2026-04-22T00:00:00Z",
        objective="Test",
        audience="Schwangere",
        constraints=(),
        source_snapshot_ids=("as-test",),
        provider_name="openai",
        model_name="gpt-test",
        task_role="writer",
        execution_meta=meta,
    )
    evidence_pack = EvidencePack(
        evidence_pack_id="ep-test",
        project_key="everydayengel",
        created_at="2026-04-22T00:00:00Z",
        summary="Test evidence",
        snapshot_ids=("as-test",),
        source_refs=(),
        evidence_lines=(),
    )
    return AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Test",
        summary="Test",
        analysis_snapshots=(snapshot,),
        writer_brief=brief,
        evidence_pack=evidence_pack,
        execution_meta=meta,
    )


def test_generate_idea_emits_commercial_class() -> None:
    svc = _make_idea_svc()
    foundation_result = _make_foundation_result()

    from operator_core.core.content_ops.service import ContentOpsService
    result = ContentOpsService.generate_idea_from_foundation(
        svc,
        project_key="everydayengel",
        command_body="beim Kochen schwindelig",
        foundation_result=foundation_result,
    )
    assert result.content_result.commercial_class is not None
    assert result.content_result.commercial_class in {c.value for c in CommercialClass}


def test_generate_idea_trust_building_for_lived_moment() -> None:
    svc = _make_idea_svc()
    foundation_result = _make_foundation_result()

    from operator_core.core.content_ops.service import ContentOpsService
    result = ContentOpsService.generate_idea_from_foundation(
        svc,
        project_key="everydayengel",
        command_body="beim Kochen schwindelig",
        foundation_result=foundation_result,
    )
    assert result.content_result.commercial_class == CommercialClass.trust_building.value
