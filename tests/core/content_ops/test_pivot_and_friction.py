"""
Focused tests for theme pivot quality and concrete friction preservation.

Two remaining weak lanes being fixed:
1. Shopping/pause prompts drifting to adjacent sub-scenes (elevator, encounters)
2. Theme pivots on saturated topics producing broad umbrella concepts
   instead of sharp Master-like micro-observations.
"""
from __future__ import annotations

import pytest

from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate


# ---------------------------------------------------------------------------
# New penalty patterns: pivot anti-patterns
# ---------------------------------------------------------------------------

def test_versteckte_helden_is_penalized_as_pivot_hero_concept() -> None:
    gate = IdeaQualityGate()
    result = gate.score("Die versteckten Helden der Schwangerschaft – was wirklich hilft")
    assert "pivot_hero_concept" in result.penalty_hits, (
        f"'versteckte Helden' must trigger pivot_hero_concept penalty. Got: {result.penalty_hits}"
    )
    assert result.score < 0


def test_stille_helfer_is_penalized_as_pivot_hero_concept() -> None:
    gate = IdeaQualityGate()
    result = gate.score("Stille Helfer im Schwangerschaftsalltag – Dinge die ich jetzt brauche")
    assert "pivot_hero_concept" in result.penalty_hits
    assert result.score < 0


def test_unverzichtbar_is_penalized_as_object_collection() -> None:
    gate = IdeaQualityGate()
    result = gate.score("Dinge die plötzlich unverzichtbar werden in der Schwangerschaft")
    assert "object_collection" in result.penalty_hits, (
        f"'unverzichtbar' must trigger object_collection penalty. Got: {result.penalty_hits}"
    )
    assert result.score < 0


def test_schwangerschaftsalltag_is_penalized_as_broad_pregnancy() -> None:
    gate = IdeaQualityGate()
    result = gate.score("Im Schwangerschaftsalltag gibt es viele kleine Veränderungen")
    assert "broad_pregnancy" in result.penalty_hits
    assert result.score < 0


# ---------------------------------------------------------------------------
# Pivot quality: concrete moment beats umbrella concept
# ---------------------------------------------------------------------------

def test_concrete_pivot_beats_versteckte_helden() -> None:
    gate = IdeaQualityGate()
    concrete = gate.score("Beim Aufstehen aus dem Sofa brauche ich jetzt eine Strategie")
    umbrella = gate.score("Die versteckten Helden der Schwangerschaft im Überblick")
    assert concrete.score > umbrella.score, (
        f"Concrete pivot ({concrete.score}) should beat 'versteckte Helden' ({umbrella.score})"
    )


def test_concrete_pivot_beats_object_collection() -> None:
    gate = IdeaQualityGate()
    concrete = gate.score("Zum ersten Mal hab ich beim Bücken wirklich Hilfe gebraucht")
    collection = gate.score("Dinge die plötzlich unverzichtbar werden in der Schwangerschaft")
    assert concrete.score > collection.score


def test_master_like_pivot_beats_schwangerschaftsalltag_wrapper() -> None:
    gate = IdeaQualityGate()
    master = gate.score("Meine Handtasche passt nicht mehr über den Bauch – ich trage sie jetzt anders")
    broad = gate.score("Im Schwangerschaftsalltag verändert sich vieles – verschiedene Aspekte beleuchtet")
    assert master.score > broad.score


# ---------------------------------------------------------------------------
# Sharpen mode: anchor weight increase (max 6.0 in sharpen mode)
# ---------------------------------------------------------------------------

def test_sharpen_anchor_score_max_is_6() -> None:
    gate = IdeaQualityGate()
    anchors = ("einkaufen", "pausen", "braucht", "schwangerschaft", "sitzen", "jetzt")
    candidate = "einkaufen pausen braucht schwangerschaft sitzen jetzt"
    result = gate.anchor_score(candidate, anchors, sharpen_mode=True)
    assert result <= 6.0


def test_sharpen_anchor_higher_than_generate_for_same_candidate() -> None:
    gate = IdeaQualityGate()
    anchors = ("einkaufen", "pausen", "braucht")
    candidate = "Einkaufen braucht jetzt Pausen – ich setze mich in jeden Laden kurz hin"
    sharpen = gate.anchor_score(candidate, anchors, sharpen_mode=True)
    generate = gate.anchor_score(candidate, anchors, sharpen_mode=False)
    assert sharpen > generate


# ---------------------------------------------------------------------------
# Shopping+pause stays in scene — not elevator/encounters
# ---------------------------------------------------------------------------

def test_sharpen_shopping_pause_candidate_beats_elevator_candidate() -> None:
    """
    With einkaufen+pausen+braucht anchors in SHARPEN MODE,
    the shopping-pause candidate must decisively beat the elevator candidate
    even if the elevator candidate has comparable heuristic signals.
    """
    gate = IdeaQualityGate()
    anchors = gate.extract_prompt_anchors(
        "einkaufen braucht jetzt pausen schwangerschaft"
    )
    assert IdeaQualityGate.is_concrete_prompt(anchors), "Should be sharpen mode"

    shopping_pause = "Einkaufen braucht jetzt Pausen – ich setze mich in jeden Laden kurz hin"
    elevator = "Im Laden brauche ich plötzlich den Aufzug – Treppen gehen nicht mehr so einfach"

    shopping_total = (
        gate.score(shopping_pause).score
        + gate.anchor_score(shopping_pause, anchors, sharpen_mode=True)
    )
    elevator_total = (
        gate.score(elevator).score
        + gate.anchor_score(elevator, anchors, sharpen_mode=True)
    )

    assert shopping_total > elevator_total, (
        f"Shopping+pause candidate ({shopping_total:.2f}) should beat elevator ({elevator_total:.2f}) "
        "when user prompt describes shopping+pauses"
    )


def test_sharpen_shopping_pause_beats_encounters_candidate() -> None:
    gate = IdeaQualityGate()
    anchors = gate.extract_prompt_anchors(
        "einkaufen braucht jetzt pausen schwangerschaft"
    )
    shopping_pause = "Einkaufen braucht jetzt Pausen – ich setze mich in jeden Laden kurz hin"
    encounters = "Im Supermarkt werde ich plötzlich angesprochen – jeder hat einen Tipp"

    shopping_total = (
        gate.score(shopping_pause).score
        + gate.anchor_score(shopping_pause, anchors, sharpen_mode=True)
    )
    encounters_total = (
        gate.score(encounters).score
        + gate.anchor_score(encounters, anchors, sharpen_mode=True)
    )

    assert shopping_total > encounters_total


def test_pick_best_sharpen_shopping_pause_over_adjacent_scene() -> None:
    gate = IdeaQualityGate()
    anchors = gate.extract_prompt_anchors(
        "einkaufen pausen braucht jetzt schwangerschaft"
    )
    shopping_pause = "Einkaufen braucht jetzt Pausen – ich setze mich in jeden Laden kurz hin"
    elevator = "Im Laden brauche ich plötzlich den Aufzug – Treppen sind nicht mehr so einfach"
    encounters = "Im Supermarkt werde ich ständig angesprochen – jeder hat einen Tipp"

    best, _ = gate.pick_best(
        [shopping_pause, elevator, encounters],
        anchor_tokens=anchors,
        sharpen_mode=True,
    )
    assert best == shopping_pause, (
        f"pick_best should choose shopping+pause candidate, got: {best!r}"
    )


# ---------------------------------------------------------------------------
# Müdigkeit pivot: concrete moment, not umbrella
# ---------------------------------------------------------------------------

def test_mued_pivot_concrete_beats_umbrella() -> None:
    """
    When pivoting away from Müdigkeit, a concrete body-friction moment
    must score higher than an umbrella/hero concept.
    """
    gate = IdeaQualityGate()
    concrete_pivot = gate.score(
        "Beim Aufstehen aus dem Sofa brauche ich jetzt eine Strategie – "
        "kein Schwung mehr möglich"
    )
    umbrella = gate.score(
        "Die versteckten Helden der Schwangerschaft – "
        "kleine Dinge die unverzichtbar werden"
    )
    assert concrete_pivot.score > umbrella.score, (
        f"Concrete pivot ({concrete_pivot.score}) must beat umbrella ({umbrella.score})"
    )


def test_broad_pivot_with_multiple_antipatterns_scores_very_negative() -> None:
    """'versteckte Helden' + 'verschiedene' + 'unverzichtbar' stacks multiple penalties."""
    gate = IdeaQualityGate()
    result = gate.score(
        "Versteckte Helden und unverzichtbare Begleiter – verschiedene Aspekte im Überblick"
    )
    # Expect at least 2 penalty categories: pivot_hero_concept, object_collection,
    # and likely abstract / multicore_blur
    assert len(result.penalty_hits) >= 2
    assert result.score < -1
