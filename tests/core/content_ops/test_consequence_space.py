"""
Tests for consequence-space-aware pivot scoring.

When a theme cluster is saturated (e.g. Müdigkeit/Energie), the pivot must
stay semantically near the original cluster's consequence space — needing
pauses, changed rhythm, body interrupting the day — and NOT drift to
unrelated micro-frictions like shoes, wardrobe, or random body friction.
"""
from __future__ import annotations

import pytest

from operator_core.core.content_ops.duplicate_guard import (
    DuplicateRiskGuard,
    ThemeRiskEvaluation,
    _THEME_CONSEQUENCE_SPACES,
    _THEME_ANTI_CONSEQUENCE,
)


def _guard() -> DuplicateRiskGuard:
    return DuplicateRiskGuard(openai_service=None)


# ---------------------------------------------------------------------------
# consequence_space_score basics
# ---------------------------------------------------------------------------

def test_consequence_score_unknown_cluster_is_zero() -> None:
    guard = _guard()
    result = guard.consequence_space_score("Beim Kochen schwindelig", "nonexistent_cluster")
    assert result == 0.0


def test_consequence_score_bonus_for_mued_pause_candidate() -> None:
    guard = _guard()
    candidate = "Ich brauche jetzt Pausen beim Einkaufen – früher bin ich durch den ganzen Laden"
    score = guard.consequence_space_score(candidate, "muedigkeit_energie")
    assert score == 1.5, f"Expected +1.5 (consequence hit), got {score}"


def test_consequence_score_bonus_for_mued_rhythm_candidate() -> None:
    guard = _guard()
    candidate = "Mein Tagesrhythmus hat sich komplett verändert – ich brauche jetzt früher eine Pause"
    score = guard.consequence_space_score(candidate, "muedigkeit_energie")
    assert score == 1.5


def test_consequence_score_bonus_for_mued_limit_candidate() -> None:
    guard = _guard()
    candidate = "Mein Körper sagt früher genug – die Grenze ist viel früher erreicht als vorher"
    score = guard.consequence_space_score(candidate, "muedigkeit_energie")
    assert score == 1.5


def test_consequence_score_penalty_for_mued_shoes_candidate() -> None:
    guard = _guard()
    candidate = "Ich kann meine Schuhe nicht mehr selbst binden – der Bauch ist zu groß"
    score = guard.consequence_space_score(candidate, "muedigkeit_energie")
    assert score == -1.5, (
        f"Shoes candidate must get -1.5 anti-consequence penalty, got {score}"
    )


def test_consequence_score_penalty_for_mued_wardrobe_candidate() -> None:
    guard = _guard()
    candidate = "Mein Kleiderschrank passt nicht mehr – alles fühlt sich anders an"
    score = guard.consequence_space_score(candidate, "muedigkeit_energie")
    assert score == -1.5


def test_consequence_score_penalty_for_mued_bending_candidate() -> None:
    guard = _guard()
    candidate = "Bücken geht nicht mehr – der Bauch ist einfach im Weg"
    score = guard.consequence_space_score(candidate, "muedigkeit_energie")
    assert score == -1.5


def test_anti_consequence_takes_precedence_over_consequence() -> None:
    """If a candidate has both signals, anti-consequence wins."""
    guard = _guard()
    # "pause" is consequence, "schuhe" is anti-consequence
    candidate = "Ich mache Pausen weil ich meine Schuhe nicht binden kann"
    score = guard.consequence_space_score(candidate, "muedigkeit_energie")
    assert score == -1.5


# ---------------------------------------------------------------------------
# Müdigkeit pivot: consequence-aligned beats unrelated micro-friction
# ---------------------------------------------------------------------------

def test_mued_pivot_consequence_aligned_beats_shoes_candidate() -> None:
    from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate
    guard = _guard()
    gate = IdeaQualityGate()

    pause_candidate = "Ich brauche jetzt mitten im Tag eine Pause – früher war das nie nötig"
    shoes_candidate = "Ich kann meine Schuhe nicht mehr binden – der Bauch ist zu groß"

    def total(c: str) -> float:
        return (
            gate.score(c).score
            + guard.consequence_space_score(c, "muedigkeit_energie")
        )

    assert total(pause_candidate) > total(shoes_candidate), (
        f"Pause candidate ({total(pause_candidate):.2f}) must beat "
        f"shoes ({total(shoes_candidate):.2f}) for muedigkeit_energie pivot"
    )


def test_mued_pivot_rhythm_candidate_beats_wardrobe_candidate() -> None:
    from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate
    guard = _guard()
    gate = IdeaQualityGate()

    rhythm = "Mein Körper gibt früher Schluss – der Rhythmus hat sich komplett verändert"
    wardrobe = "Mein Kleiderschrank und meine Jeans passen nicht mehr"

    def total(c: str) -> float:
        return gate.score(c).score + guard.consequence_space_score(c, "muedigkeit_energie")

    assert total(rhythm) > total(wardrobe)


def test_mued_pivot_concrete_beat_generic_consequence_candidate() -> None:
    """A concrete consequence candidate must beat an abstract broad one."""
    from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate
    guard = _guard()
    gate = IdeaQualityGate()

    concrete = "Früher hab ich den Haushalt an einem Nachmittag geschafft – jetzt brauche ich Pausen"
    broad = "Das Schwangerschaftsleben im Überblick – verschiedene Aspekte und Konzepte"

    def total(c: str) -> float:
        return gate.score(c).score + guard.consequence_space_score(c, "muedigkeit_energie")

    assert total(concrete) > total(broad)


# ---------------------------------------------------------------------------
# pick_best-equivalent for pivots: consequence-aware selection
# ---------------------------------------------------------------------------

def test_consequence_aware_pick_selects_pause_over_shoes_from_three() -> None:
    """
    Given 3 candidates for a muedigkeit_energie pivot, the one in the
    consequence space (pause) must win over shoes and a broad concept.
    """
    from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate
    guard = _guard()
    gate = IdeaQualityGate()
    cluster = "muedigkeit_energie"

    pause = "Ich brauche mitten im Tag eine Pause – früher war das nie der Fall"
    shoes = "Ich kann meine Schuhe nicht mehr selbst binden wegen des Bauches"
    broad = "Verschiedene Aspekte der Schwangerschaft im Überblick – was sich verändert"

    def _total(c: str) -> float:
        return (
            gate.score(c).score
            + guard.consequence_space_score(c, cluster)
        )

    candidates = [shoes, broad, pause]
    best = max(candidates, key=_total)
    assert best == pause, f"Expected pause candidate to win, got: {best!r}"


# ---------------------------------------------------------------------------
# Data completeness
# ---------------------------------------------------------------------------

def test_consequence_space_defined_for_muedigkeit_cluster() -> None:
    assert "muedigkeit_energie" in _THEME_CONSEQUENCE_SPACES
    assert len(_THEME_CONSEQUENCE_SPACES["muedigkeit_energie"]) >= 5


def test_anti_consequence_defined_for_muedigkeit_cluster() -> None:
    assert "muedigkeit_energie" in _THEME_ANTI_CONSEQUENCE
    kws = _THEME_ANTI_CONSEQUENCE["muedigkeit_energie"]
    assert any("schuh" in kw for kw in kws), "Shoes must be in anti-consequence"
    assert any("kleid" in kw or "jeans" in kw for kw in kws), "Wardrobe must be in anti-consequence"


# ---------------------------------------------------------------------------
# Non-pivot contexts unaffected: shopping+pause / cooking+dizziness
# ---------------------------------------------------------------------------

def test_consequence_score_zero_for_no_cluster_match() -> None:
    """For non-saturated contexts (no cluster_name passed), score is 0."""
    guard = _guard()
    # Shopping pause prompt context — not a muedigkeit pivot
    candidate = "Einkaufen braucht jetzt Pausen – ich setze mich in jeden Laden kurz hin"
    # Passing empty cluster string → 0
    assert guard.consequence_space_score(candidate, "") == 0.0


def test_consequence_score_does_not_penalise_cooking_dizziness() -> None:
    """cooking+dizziness is unrelated to muedigkeit anti-consequence."""
    guard = _guard()
    candidate = "Beim Kochen wird mir schwindelig – seitdem koche ich nur noch sitzend"
    # anti-consequence for muedigkeit includes schuhe/kleidung, not kochen
    score = guard.consequence_space_score(candidate, "muedigkeit_energie")
    assert score >= 0.0, "Cooking+dizziness must not be penalised as anti-consequence"
