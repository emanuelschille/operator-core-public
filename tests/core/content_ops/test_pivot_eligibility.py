"""
Tests for the hard eligibility gate on saturated-theme pivot candidates.

For known clusters (e.g. muedigkeit_energie), a pivot candidate must contain
at least one consequence-space keyword to be eligible — candidates without it
are filtered out entirely before scoring, not just penalised.

Live failure this fixes:
  - "shirt buttoning" (Hemd zuknöpfen) → no consequence keyword → ineligible
  - "baby bump in the way" (Babybauch im Weg) → no consequence keyword → ineligible
  - "shoes / shoelaces" → anti-consequence keyword → ineligible
"""
from __future__ import annotations

import pytest

from operator_core.core.content_ops.duplicate_guard import DuplicateRiskGuard, IdeaQualityGate


def _guard() -> DuplicateRiskGuard:
    return DuplicateRiskGuard(openai_service=None)


# ---------------------------------------------------------------------------
# Eligible: fatigue consequence-space candidates
# ---------------------------------------------------------------------------

def test_pause_candidate_is_eligible() -> None:
    guard = _guard()
    assert guard.is_pivot_eligible(
        "Ich brauche jetzt mitten im Tag eine Pause – früher war das nie nötig",
        "muedigkeit_energie",
    )


def test_tempo_candidate_is_eligible() -> None:
    guard = _guard()
    assert guard.is_pivot_eligible(
        "Mein Tempo beim Haushalt hat sich halbiert – alles dauert jetzt länger",
        "muedigkeit_energie",
    )


def test_rhythmus_candidate_is_eligible() -> None:
    guard = _guard()
    assert guard.is_pivot_eligible(
        "Mein Tagesrhythmus hat sich komplett verändert",
        "muedigkeit_energie",
    )


def test_kraft_candidate_is_eligible() -> None:
    guard = _guard()
    assert guard.is_pivot_eligible(
        "Die Kraft reicht einfach nicht mehr für den ganzen Tag",
        "muedigkeit_energie",
    )


def test_konzentration_candidate_is_eligible() -> None:
    guard = _guard()
    assert guard.is_pivot_eligible(
        "Meine Konzentration bricht viel früher weg als früher",
        "muedigkeit_energie",
    )


def test_koerper_grenze_candidate_is_eligible() -> None:
    guard = _guard()
    assert guard.is_pivot_eligible(
        "Mein Körper sagt früher genug – die Grenze ist viel früher erreicht",
        "muedigkeit_energie",
    )


def test_langsamer_candidate_is_eligible() -> None:
    guard = _guard()
    assert guard.is_pivot_eligible(
        "Ich bin beim Einkaufen jetzt so viel langsamer als früher",
        "muedigkeit_energie",
    )


# ---------------------------------------------------------------------------
# Ineligible: no consequence keyword (previously passed soft filter)
# ---------------------------------------------------------------------------

def test_shirt_buttoning_is_ineligible() -> None:
    """'Hemd zuknöpfen' is not in fatigue consequence space."""
    guard = _guard()
    assert not guard.is_pivot_eligible(
        "Das Hemd zuknöpfen dauert jetzt ewig – der Bauch ist einfach im Weg",
        "muedigkeit_energie",
    ), "shirt-buttoning idea has no consequence keyword and must be ineligible"


def test_baby_bump_in_the_way_is_ineligible() -> None:
    """'Babybauch ist im Weg' is a generic body-friction, not a fatigue consequence."""
    guard = _guard()
    assert not guard.is_pivot_eligible(
        "Der Babybauch ist immer öfter im Weg – beim Schreiben, beim Essen, überall",
        "muedigkeit_energie",
    ), "baby-bump-in-the-way idea has no consequence keyword and must be ineligible"


def test_generic_body_friction_no_consequence_is_ineligible() -> None:
    guard = _guard()
    assert not guard.is_pivot_eligible(
        "Der Bauch macht viele alltägliche Dinge schwieriger als erwartet",
        "muedigkeit_energie",
    )


# ---------------------------------------------------------------------------
# Ineligible: anti-consequence keywords still block (shoes, wardrobe, bending)
# ---------------------------------------------------------------------------

def test_shoes_candidate_is_ineligible() -> None:
    guard = _guard()
    assert not guard.is_pivot_eligible(
        "Meine Schuhe kann ich nicht mehr selbst binden",
        "muedigkeit_energie",
    )


def test_shoelaces_candidate_is_ineligible() -> None:
    guard = _guard()
    assert not guard.is_pivot_eligible(
        "Schnürsenkel binden ist seit Wochen eine echte Challenge",
        "muedigkeit_energie",
    )


def test_bending_candidate_is_ineligible() -> None:
    guard = _guard()
    assert not guard.is_pivot_eligible(
        "Bücken geht nicht mehr – der Bauch ist im Weg",
        "muedigkeit_energie",
    )


def test_wardrobe_candidate_is_ineligible() -> None:
    guard = _guard()
    assert not guard.is_pivot_eligible(
        "Mein Kleiderschrank hilft mir nicht mehr weiter",
        "muedigkeit_energie",
    )


# ---------------------------------------------------------------------------
# Unknown clusters: all eligible
# ---------------------------------------------------------------------------

def test_unknown_cluster_all_candidates_eligible() -> None:
    guard = _guard()
    # Any candidate is eligible for unknown clusters — no restriction
    assert guard.is_pivot_eligible("Schuhe binden geht nicht mehr", "unknown_cluster")
    assert guard.is_pivot_eligible("Hemd zuknöpfen dauert ewig", "some_other_cluster")


def test_empty_cluster_name_all_eligible() -> None:
    guard = _guard()
    assert guard.is_pivot_eligible("Irgendetwas", "")


# ---------------------------------------------------------------------------
# Hard gate actually filters before scoring
# ---------------------------------------------------------------------------

def test_ineligible_candidate_does_not_win_even_with_high_heuristic() -> None:
    """
    Even if shirt-buttoning has plötzlich/körper/shift signals (high heuristic),
    it must lose because it's filtered out before scoring.
    """
    guard = _guard()
    gate = IdeaQualityGate()
    cluster = "muedigkeit_energie"

    # High-heuristic but ineligible (no consequence keyword)
    shirt = "Das Hemd zuknöpfen geht nicht mehr – plötzlich verändert sich alles"
    # Lower-heuristic but eligible (has "pause")
    pause = "Ich brauche jetzt eine Pause nach dem Einkaufen"

    # Verify eligibility
    assert not guard.is_pivot_eligible(shirt, cluster)
    assert guard.is_pivot_eligible(pause, cluster)

    # Simulate hard gate + scoring
    candidates = [shirt, pause]
    eligible = [c for c in candidates if guard.is_pivot_eligible(c, cluster)]
    assert eligible == [pause], "Only pause candidate should be eligible"

    def _total(c: str) -> float:
        return gate.score(c).score + guard.consequence_space_score(c, cluster)

    best = max(eligible, key=_total)
    assert best == pause


def test_eligible_candidates_compete_by_quality_among_themselves() -> None:
    """Among eligible candidates, the higher-quality one wins."""
    guard = _guard()
    gate = IdeaQualityGate()
    cluster = "muedigkeit_energie"

    # Both eligible (both contain consequence keywords)
    good = "Ich brauche mitten im Tag eine Pause – mein Körper sagt früher genug"
    weaker = "Rhythmus verändert sich in der Schwangerschaft"

    assert guard.is_pivot_eligible(good, cluster)
    assert guard.is_pivot_eligible(weaker, cluster)

    def _total(c: str) -> float:
        return gate.score(c).score + guard.consequence_space_score(c, cluster)

    assert _total(good) > _total(weaker), "Higher-quality eligible candidate should score higher"
