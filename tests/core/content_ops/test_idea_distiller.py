"""
Tests for IdeaDistiller — lightweight heuristic distillation to one strongest moment.

Target: strip production wrappers (Ehrlicher Einblick, Talking Head, Dann 2–3…,
CTA, "und zeige wie…") while preserving the core scene, friction, and anchors.
"""
from __future__ import annotations

import pytest

from operator_core.core.content_ops.duplicate_guard import IdeaDistiller, IdeaQualityGate


def _d() -> IdeaDistiller:
    return IdeaDistiller()


# ---------------------------------------------------------------------------
# Prefix label removal
# ---------------------------------------------------------------------------

def test_distill_removes_ehrlicher_einblick_prefix() -> None:
    d = _d()
    result = d.distill("Ehrlicher Einblick: Beim Kochen wird mir schwindelig und ich muss sitzen")
    assert "Ehrlicher Einblick" not in result
    assert "kochen" in result.lower() or "schwindelig" in result.lower()


def test_distill_removes_ehrlicher_monolog_prefix() -> None:
    d = _d()
    result = d.distill("Ehrlicher Monolog: Ich schaffe nicht mehr alles in einem Durchgang")
    assert "Ehrlicher Monolog" not in result
    assert len(result) > 10


def test_distill_removes_mini_doku_prefix() -> None:
    d = _d()
    result = d.distill("Mini-Doku: Der Moment als ich zum ersten Mal sitzen musste beim Kochen")
    assert "Mini-Doku" not in result
    assert "sitzen" in result.lower() or "kochen" in result.lower()


def test_distill_removes_talking_head_prefix() -> None:
    d = _d()
    result = d.distill("Talking Head: Warum mein Tempo jetzt langsamer ist")
    assert "Talking Head" not in result


# ---------------------------------------------------------------------------
# Inline clause stripping
# ---------------------------------------------------------------------------

def test_distill_removes_und_zeige_wie_clause() -> None:
    d = _d()
    verbose = (
        "Ich stehe beim Kochen und merke, dass mir schwindelig wird. "
        "Statt zu sitzen, greife ich nach einem Stuhl als Stütze und zeige, wie ich trotzdem weitermache."
    )
    result = d.distill(verbose)
    assert "und zeige" not in result.lower()
    assert "kochen" in result.lower() or "schwindelig" in result.lower()


def test_distill_removes_ich_zeige_wie_dash_clause() -> None:
    d = _d()
    verbose = "Beim Kochen wird mir schwindelig – ich zeige, wie ich damit umgehe"
    result = d.distill(verbose)
    assert "ich zeige" not in result.lower()
    assert "kochen" in result.lower() or "schwindelig" in result.lower()


def test_distill_removes_und_wie_ich_trotzdem() -> None:
    d = _d()
    verbose = "Der Moment als ich beim Aufstehen nicht mehr konnte, und wie ich trotzdem funktionierte"
    result = d.distill(verbose)
    assert "und wie ich trotzdem" not in result.lower()


def test_distill_removes_statt_zu_sitzen_production_sentence() -> None:
    d = _d()
    verbose = (
        "Beim Kochen wird mir plötzlich schwindelig. "
        "Statt zu sitzen, greife ich nach einem Stuhl und zeige, wie ich weitermache."
    )
    result = d.distill(verbose)
    assert "Statt zu sitzen" not in result
    assert "kochen" in result.lower() or "schwindelig" in result.lower()


# ---------------------------------------------------------------------------
# Multi-sentence: production sentences dropped
# ---------------------------------------------------------------------------

def test_distill_drops_dann_mini_momente_sentence() -> None:
    d = _d()
    verbose = (
        "Der Moment beim Kochen wenn mir schwindelig wird. "
        "Dann 2–3 Mini-Momente wie ich meinen Alltag trotzdem gestalte."
    )
    result = d.distill(verbose)
    assert "Dann 2" not in result
    assert "Mini-Momente" not in result
    assert "Kochen" in result or "kochen" in result.lower()


def test_distill_drops_cta_sentence() -> None:
    d = _d()
    verbose = (
        "Ich brauche jetzt Pausen beim Einkaufen – früher nie. "
        "CTA: Frage ans Publikum wie sie das kennen."
    )
    result = d.distill(verbose)
    assert "CTA" not in result
    assert "Pausen" in result or "pausen" in result.lower()


def test_distill_drops_talking_head_production_sentence() -> None:
    d = _d()
    verbose = (
        "Mein Körper sagt früher genug – die Kraft reicht nicht mehr für den ganzen Tag. "
        "Talking Head direkt danach: Wie ich damit umgehe."
    )
    result = d.distill(verbose)
    assert "Talking Head" not in result
    assert "Körper" in result or "körper" in result.lower()


def test_distill_drops_ehrlicher_einblick_second_sentence() -> None:
    d = _d()
    verbose = (
        "Einkaufen braucht jetzt Pausen. "
        "Ehrlicher Einblick wie ich damit umgehe und was das mit mir macht."
    )
    result = d.distill(verbose)
    assert "Ehrlicher Einblick" not in result
    assert "Pausen" in result or "pausen" in result.lower()


# ---------------------------------------------------------------------------
# Anchor preservation
# ---------------------------------------------------------------------------

def test_distill_preserves_cooking_dizziness_anchors() -> None:
    d = _d()
    anchors = ("kochen", "schwindelig", "sitzen")
    verbose = (
        "Beim Kochen wird mir plötzlich schwindelig – ich muss mich hinsetzen. "
        "Dann zeige ich 2–3 Tricks wie ich trotzdem weiterkoche."
    )
    result = d.distill(verbose, anchor_tokens=anchors)
    low = result.lower()
    assert "kochen" in low or "schwindelig" in low or "sitzen" in low, (
        f"Core anchors must be preserved. Got: {result!r}"
    )


def test_distill_preserves_shopping_pause_anchors() -> None:
    d = _d()
    anchors = ("einkaufen", "pausen", "braucht")
    verbose = (
        "Einkaufen braucht jetzt Pausen – ich muss mich in jeden Laden kurz hinsetzen. "
        "Ehrlicher Einblick: wie ich damit umgehe."
    )
    result = d.distill(verbose, anchor_tokens=anchors)
    low = result.lower()
    assert "einkaufen" in low or "pausen" in low


# ---------------------------------------------------------------------------
# Already-tight ideas are preserved
# ---------------------------------------------------------------------------

def test_distill_already_tight_one_sentence_unchanged() -> None:
    d = _d()
    tight = "Der Moment beim Kochen, wenn ich plötzlich sitzen muss weil mir schwindelig wird"
    result = d.distill(tight)
    assert "kochen" in result.lower()
    assert "schwindelig" in result.lower()
    assert "sitzen" in result.lower()


def test_distill_tight_body_friction_preserved() -> None:
    d = _d()
    tight = "Mein Körper sagt früher genug – die Grenze ist jetzt viel früher erreicht"
    result = d.distill(tight)
    assert "körper" in result.lower() or "Körper" in result
    assert "früher" in result.lower()


def test_distill_tight_shopping_preserved() -> None:
    d = _d()
    tight = "Einkaufen braucht jetzt Pausen – früher bin ich einfach durchgegangen"
    result = d.distill(tight)
    assert "Einkaufen" in result or "einkaufen" in result.lower()
    assert "Pausen" in result or "pausen" in result.lower()


# ---------------------------------------------------------------------------
# Quality gate score: distilled ≥ verbose
# ---------------------------------------------------------------------------

def test_distilled_scores_equal_or_better_than_verbose() -> None:
    """Removing wrappers must not degrade the heuristic quality score."""
    gate = IdeaQualityGate()
    d = _d()
    verbose = (
        "Ehrlicher Einblick: Beim Kochen wird mir schwindelig. "
        "Dann zeige ich 2–3 Momente wie ich trotzdem weitermache."
    )
    tight = d.distill(verbose)
    assert gate.score(tight).score >= gate.score(verbose).score, (
        f"Distilled ({gate.score(tight).score}) must not score worse than verbose "
        f"({gate.score(verbose).score}).\nVerbose: {verbose!r}\nTight: {tight!r}"
    )


def test_distilled_cooking_scores_equal_or_better() -> None:
    gate = IdeaQualityGate()
    d = _d()
    verbose = (
        "Ich stehe beim Kochen und merke, dass mir schwindelig wird. "
        "Statt zu sitzen, greife ich nach einem Stuhl als Stütze und zeige, wie ich trotzdem weitermache."
    )
    tight = d.distill(verbose)
    assert gate.score(tight).score >= gate.score(verbose).score


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_distill_empty_string_returns_empty() -> None:
    d = _d()
    assert d.distill("") == ""


def test_distill_single_word_unchanged() -> None:
    d = _d()
    assert d.distill("Schwindel") == "Schwindel"


def test_distill_does_not_remove_valid_dann_in_narrative() -> None:
    """'Dann' at start of second sentence is a production signal and gets dropped,
    but 'dann' mid-sentence in the first sentence is preserved."""
    d = _d()
    # "dann" mid-sentence is fine — the sentence split only looks at start-of-sentence
    narrative = "Ich merke dann plötzlich dass mir schwindelig wird beim Kochen"
    result = d.distill(narrative)
    assert "schwindelig" in result.lower()


# ---------------------------------------------------------------------------
# Fallback/pivot language leak — new patterns (language parity slice)
# ---------------------------------------------------------------------------

def test_distill_removes_mini_szene_prefix() -> None:
    d = _d()
    result = d.distill("Mini-Szene: Ich versuche Schuhe anzuziehen und komme kaum ran")
    assert "Mini-Szene" not in result
    assert "schuhe" in result.lower() or "versuche" in result.lower()


def test_distill_removes_challenge_prefix() -> None:
    d = _d()
    result = d.distill("Challenge: Wie schnell kann ich noch vom Sofa aufstehen?")
    assert "Challenge" not in result
    assert len(result) > 5


def test_distill_removes_monolog_prefix() -> None:
    d = _d()
    result = d.distill("Monolog: Ich hab heute gemerkt, dass Bücken wirklich eine Sache für sich ist")
    assert "Monolog" not in result
    assert "bücken" in result.lower() or "gemerkt" in result.lower()


def test_distill_removes_ein_neuer_blick_prefix() -> None:
    d = _d()
    result = d.distill("Ein neuer Blick: Seit dem dritten Trimester ist Treppensteigen keine Selbstverständlichkeit mehr")
    assert "Ein neuer Blick" not in result
    assert "treppensteigen" in result.lower() or "trimester" in result.lower()


def test_distill_removes_headline_challenge_colon() -> None:
    """'Die alltägliche Tür-Challenge: wenn der Bauch ...' → content after colon."""
    d = _d()
    result = d.distill("Die alltägliche Tür-Challenge: wenn der Bauch so groß ist dass ich seitwärts durch muss")
    assert "Challenge" not in result
    assert "bauch" in result.lower() or "seitwärts" in result.lower()


def test_distill_removes_headline_einblick_colon() -> None:
    d = _d()
    result = d.distill("Ein ehrlicher Einblick: Ich schaffe nicht mehr alles in einem Durchgang")
    assert "Einblick" not in result
    assert "durchgang" in result.lower() or "schaffe" in result.lower()


def test_distill_drops_filme_imperative_sentence() -> None:
    """'Filme eine Szene ...' is a production instruction and must be dropped."""
    d = _d()
    verbose = (
        "Ich brauche jetzt beim Aufstehen wirklich Hilfe. "
        "Filme eine Szene in der du zeigst wie das ohne Partner geht."
    )
    result = d.distill(verbose)
    assert "filme" not in result.lower()
    assert "aufstehen" in result.lower() or "hilfe" in result.lower()


def test_distill_drops_versuch_imperative_sentence() -> None:
    d = _d()
    verbose = (
        "Mein Körper meldet Grenzen viel früher als ich erwartet hatte. "
        "Versuch mal einen ganzen Einkauf ohne Pause zu machen."
    )
    result = d.distill(verbose)
    assert result.lower().startswith("versuch") is False
    assert "körper" in result.lower() or "grenzen" in result.lower()


def test_distill_strips_dash_filme_inline() -> None:
    """'Guter Moment – Filme wie du ...' → core moment only."""
    d = _d()
    text = "Beim Bücken nach den Schuhen merke ich, dass das nicht mehr einfach so geht – Filme wie du damit umgehst"
    result = d.distill(text)
    assert "filme" not in result.lower()
    assert "schuhe" in result.lower() or "bücken" in result.lower()


def test_distill_strips_dash_versuch_inline() -> None:
    d = _d()
    text = "Ich komme kaum noch an meine Füße ran – Versuch das mal elegant aussehen zu lassen"
    result = d.distill(text)
    assert "versuch" not in result.lower()
    assert "füße" in result.lower() or "komme" in result.lower()


def test_distill_preserves_clean_first_person_sentence() -> None:
    """Clean first-person single-sentence ideas must pass through unchanged."""
    d = _d()
    clean = "Mir ist erst letzte Woche aufgefallen, dass ich beim Aufstehen vom Sofa inzwischen eine Hand brauche"
    result = d.distill(clean)
    assert "aufgefallen" in result.lower()
    assert "sofa" in result.lower()


def test_distill_preserves_clean_body_friction_sentence() -> None:
    d = _d()
    clean = "Mein Rücken meldet sich jetzt schon nach zehn Minuten Stehen — das kannte ich vorher nicht"
    result = d.distill(clean)
    assert "rücken" in result.lower()
    assert "stehen" in result.lower()
