"""
Tests for GENERATE MODE vs SHARPEN MODE behavior in IdeaQualityGate.

GENERATE MODE: empty / vague prompt — freely produce micro-observations.
SHARPEN MODE: concrete prompt with ≥3 scene tokens — preserve exact scene.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate
from operator_core.core.content_ops.service import ContentOpsService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_svc(output_text: str | None = None) -> ContentOpsService:
    """Minimal ContentOpsService with a mocked openai_service."""
    svc = object.__new__(ContentOpsService)
    if output_text is not None:
        mock_resp = MagicMock()
        mock_resp.output_text = output_text
        mock_resp.model = "gpt-test"
        svc.openai_service = MagicMock()
        svc.openai_service.complete_messages.return_value = mock_resp
    else:
        svc.openai_service = None
    return svc


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------

def test_empty_prompt_is_generate_mode() -> None:
    gate = IdeaQualityGate()
    tokens = gate.extract_prompt_anchors("")
    assert not IdeaQualityGate.is_concrete_prompt(tokens)


def test_vague_prompt_is_generate_mode() -> None:
    gate = IdeaQualityGate()
    tokens = gate.extract_prompt_anchors("neue idee bitte")
    assert not IdeaQualityGate.is_concrete_prompt(tokens)


def test_concrete_cooking_dizziness_prompt_is_sharpen_mode() -> None:
    gate = IdeaQualityGate()
    tokens = gate.extract_prompt_anchors(
        "ich muss seit der schwangerschaft beim kochen plötzlich sitzen weil mir schwindelig wird"
    )
    assert IdeaQualityGate.is_concrete_prompt(tokens)


def test_concrete_shopping_pause_prompt_is_sharpen_mode() -> None:
    gate = IdeaQualityGate()
    tokens = gate.extract_prompt_anchors("einkaufen braucht jetzt pausen schwangerschaft")
    assert IdeaQualityGate.is_concrete_prompt(tokens)


def test_threshold_is_3_tokens() -> None:
    """Exactly 3 scene tokens → sharpen; 2 → generate."""
    gate = IdeaQualityGate()
    two = ("kochen", "schwindelig")
    three = ("kochen", "schwindelig", "sitzen")
    assert not IdeaQualityGate.is_concrete_prompt(two)
    assert IdeaQualityGate.is_concrete_prompt(three)


# ---------------------------------------------------------------------------
# anchor_score: sharpen_mode doubles weight
# ---------------------------------------------------------------------------

def test_sharpen_mode_anchor_weight_higher_than_generate() -> None:
    gate = IdeaQualityGate()
    anchors = ("kochen", "schwindelig", "sitzen")
    candidate = "Beim Kochen wird mir schwindelig – seitdem muss ich sitzen"
    generate_score = gate.anchor_score(candidate, anchors, sharpen_mode=False)
    sharpen_score = gate.anchor_score(candidate, anchors, sharpen_mode=True)
    assert sharpen_score > generate_score


def test_sharpen_mode_anchor_score_capped_at_6() -> None:
    gate = IdeaQualityGate()
    anchors = ("kochen", "schwindelig", "sitzen", "schwangerschaft", "plötzlich", "aufstehen")
    candidate = "kochen schwindelig sitzen schwangerschaft plötzlich aufstehen"
    result = gate.anchor_score(candidate, anchors, sharpen_mode=True)
    assert result <= 6.0


def test_generate_mode_anchor_score_capped_at_2() -> None:
    gate = IdeaQualityGate()
    anchors = ("kochen", "schwindelig", "sitzen", "schwangerschaft", "plötzlich")
    candidate = "kochen schwindelig sitzen schwangerschaft plötzlich"
    result = gate.anchor_score(candidate, anchors, sharpen_mode=False)
    assert result <= 2.0


# ---------------------------------------------------------------------------
# pick_best: SHARPEN MODE keeps candidates in scene
# ---------------------------------------------------------------------------

def test_sharpen_mode_pick_best_prefers_scene_candidate() -> None:
    """
    With cooking+dizziness+sitting anchors in SHARPEN MODE, pick_best must
    prefer the cooking-scene candidate even when the alternative has a higher
    base heuristic score.
    """
    gate = IdeaQualityGate()
    anchors = ("kochen", "schwindelig", "sitzen")
    # This candidate has strong reward signals (surprise + shift) but no anchors
    drifted = "Plötzlich geht nicht mehr — was ich alles nicht mehr kann seit der Schwangerschaft"
    # This candidate has moderate reward but preserves all 3 anchors
    anchored = "Beim Kochen wird mir schwindelig – seitdem koche ich nur noch sitzend"

    best_sharpen, _ = gate.pick_best([drifted, anchored], anchor_tokens=anchors, sharpen_mode=True)
    assert best_sharpen == anchored, (
        f"SHARPEN MODE should pick anchored candidate. Got: {best_sharpen!r}"
    )


def test_generate_mode_picks_highest_heuristic_score() -> None:
    """
    Without anchors (GENERATE MODE), pick_best falls back to pure heuristic
    and selects the highest-scoring idea regardless of scene overlap.
    """
    gate = IdeaQualityGate()
    micro = "Heute morgen zum ersten Mal gespürt wie er sich dreht – einfach surreal"
    broad = "Viele Schwangere kennen ähnliche typische Erfahrungen mit dem Körper"
    best, _ = gate.pick_best([micro, broad], anchor_tokens=(), sharpen_mode=False)
    assert "gespürt" in best or "dreht" in best


# ---------------------------------------------------------------------------
# GENERATE MODE: micro-observation beats broad concept
# ---------------------------------------------------------------------------

def test_generate_mode_concrete_observation_beats_lifestyle_concept() -> None:
    gate = IdeaQualityGate()
    micro = gate.score("Beim Aufstehen zittere ich – seitdem gehe ich langsamer")
    concept = gate.score("Das Schwangerschafts-Lifestyle-Konzept für moderne Frauen")
    assert micro.score > concept.score


def test_generate_mode_body_friction_beats_vague_overview() -> None:
    gate = IdeaQualityGate()
    friction = gate.score("Mein Körper sagt früher genug – heute schon nach 20 Minuten")
    overview = gate.score("Verschiedene Aspekte und Perspektiven der Schwangerschaft im Überblick")
    assert friction.score > overview.score


def test_versteckte_helden_is_penalized() -> None:
    """'Versteckte Helden' phrasing is abstract/broad — should be penalized."""
    gate = IdeaQualityGate()
    result = gate.score("Die versteckten Helden der Schwangerschaft – verschiedene Helfer im Überblick")
    assert result.score < 0


def test_tipps_und_tricks_drift_is_penalized() -> None:
    gate = IdeaQualityGate()
    result = gate.score("Tipps und Tricks für den Alltag in der Schwangerschaft")
    assert "listicle" in result.penalty_hits
    assert result.score < 0


def test_recipe_tips_drift_penalized_as_listicle() -> None:
    gate = IdeaQualityGate()
    result = gate.score("Tipps für einfache Mahlzeiten und Rezepte in der Schwangerschaft")
    assert result.score < 0


# ---------------------------------------------------------------------------
# SHARPEN MODE: prompt scene preserved in scoring
# ---------------------------------------------------------------------------

def test_sharpen_mode_cooking_scene_candidate_beats_shoes_candidate() -> None:
    gate = IdeaQualityGate()
    anchors = gate.extract_prompt_anchors(
        "beim kochen schwindelig werden musste sitzen schwangerschaft"
    )
    cooking = "Der Moment beim Kochen wo mir schwindelig wurde und ich plötzlich sitzen musste"
    shoes = "Ich kann meine Schuhe nicht mehr anziehen – der Bauch ist zu groß"

    cooking_total = gate.score(cooking).score + gate.anchor_score(cooking, anchors, sharpen_mode=True)
    shoes_total = gate.score(shoes).score + gate.anchor_score(shoes, anchors, sharpen_mode=True)

    assert cooking_total > shoes_total, (
        f"Cooking candidate ({cooking_total:.2f}) should beat shoes ({shoes_total:.2f}) "
        "when user prompt is about cooking+dizziness"
    )


def test_sharpen_mode_shopping_pause_stays_in_scene() -> None:
    gate = IdeaQualityGate()
    anchors = gate.extract_prompt_anchors("einkaufen braucht jetzt pausen schwangerschaft")
    in_scene = "Einkaufen braucht jetzt Pausen – ich setze mich jetzt in jeden Laden kurz hin"
    drifted = "Kleidung kaufen für die Schwangerschaft – was wirklich noch passt"

    in_score = gate.score(in_scene).score + gate.anchor_score(in_scene, anchors, sharpen_mode=True)
    drift_score = gate.score(drifted).score + gate.anchor_score(drifted, anchors, sharpen_mode=True)

    assert in_score > drift_score


def test_one_core_idea_beats_multicore_in_both_modes() -> None:
    gate = IdeaQualityGate()
    one_core = gate.score("Der Moment als ich plötzlich beim Aufstehen zitterte")
    multi = gate.score("Einerseits die körperlichen Veränderungen, andererseits die emotionalen Aspekte")
    assert one_core.score > multi.score


# ---------------------------------------------------------------------------
# _naturalize_mirror_output: retained MIRROR naturalization
# ---------------------------------------------------------------------------

_COOKING_PROMPT = "beim kochen plötzlich sitzen wegen schwindel"
_COOKING_ANCHORS = ("kochen", "plötzlich", "sitzen", "wegen", "schwindel")
_SUPERMARKT_PROMPT = "im supermarkt plötzlich pause brauchen"
_SUPERMARKT_ANCHORS = ("supermarkt", "plötzlich", "pause", "brauchen")
_CHECKEN_PROMPT = "vor dem rausgehen doppelt checken was mitmuss"
_CHECKEN_ANCHORS = ("rausgehen", "doppelt", "checken", "mitmuss")


def test_naturalize_returns_original_when_service_is_none() -> None:
    """No openai_service → original returned unchanged."""
    svc = _make_svc(output_text=None)
    candidate = "Beim Kochen muss ich plötzlich sitzen wegen Schwindel."
    result = svc._naturalize_mirror_output(
        candidate, anchor_tokens=_COOKING_ANCHORS, user_prompt=_COOKING_PROMPT, model="gpt-test"
    )
    assert result == candidate


def test_naturalize_cooking_uses_natural_result_when_fidelity_passes() -> None:
    """LLM returns a natural sentence preserving 'sitzen' → naturalized version is used."""
    natural = "Beim Kochen merke ich inzwischen manchmal, dass ich mich plötzlich hinsetzen muss, weil mir schwindelig wird."
    svc = _make_svc(output_text=natural)
    candidate = "Beim Kochen muss ich plötzlich sitzen wegen Schwindel."
    result = svc._naturalize_mirror_output(
        candidate, anchor_tokens=_COOKING_ANCHORS, user_prompt=_COOKING_PROMPT, model="gpt-test"
    )
    assert result == natural
    # Fidelity anchors still present
    result_low = result.lower()
    assert "sitz" in result_low or "hinsetzen" in result_low
    assert "kochen" in result_low


def test_naturalize_cooking_falls_back_when_sitzen_anchor_lost() -> None:
    """LLM drifts to 'abstützen' (not a near-form of 'sitzen') → original kept."""
    drifted = "Beim Kochen muss ich mich manchmal abstützen wegen Schwindel."
    svc = _make_svc(output_text=drifted)
    candidate = "Beim Kochen muss ich plötzlich sitzen wegen Schwindel."
    result = svc._naturalize_mirror_output(
        candidate, anchor_tokens=_COOKING_ANCHORS, user_prompt=_COOKING_PROMPT, model="gpt-test"
    )
    assert result == candidate


def test_naturalize_supermarkt_uses_natural_result_when_pause_preserved() -> None:
    """LLM returns natural sentence preserving 'pause' and 'brauchen' → used."""
    natural = "Im Supermarkt merke ich inzwischen manchmal mitten drin, dass ich kurz Pause brauche."
    svc = _make_svc(output_text=natural)
    candidate = "Im Supermarkt brauche ich plötzlich Pause."
    result = svc._naturalize_mirror_output(
        candidate, anchor_tokens=_SUPERMARKT_ANCHORS, user_prompt=_SUPERMARKT_PROMPT, model="gpt-test"
    )
    assert result == natural
    result_low = result.lower()
    assert "paus" in result_low
    assert "brauch" in result_low


def test_naturalize_supermarkt_falls_back_when_pause_replaced_with_abstuetzen() -> None:
    """'abstützen' replaces 'pause brauchen' → fidelity fails → original kept."""
    drifted = "Im Supermarkt muss ich mich manchmal abstützen."
    svc = _make_svc(output_text=drifted)
    candidate = "Im Supermarkt brauche ich plötzlich Pause."
    result = svc._naturalize_mirror_output(
        candidate, anchor_tokens=_SUPERMARKT_ANCHORS, user_prompt=_SUPERMARKT_PROMPT, model="gpt-test"
    )
    assert result == candidate


def test_naturalize_checken_mitmuss_natural_result_used_when_anchors_preserved() -> None:
    """LLM keeps 'checken' and 'mitmuss' in natural form → used."""
    natural = "Vor dem Rausgehen überprüfe ich inzwischen doppelt, was ich alles mitnehmen muss."
    svc = _make_svc(output_text=natural)
    candidate = "Vor dem Rausgehen muss ich doppelt checken was mitmuss."
    result = svc._naturalize_mirror_output(
        candidate, anchor_tokens=_CHECKEN_ANCHORS, user_prompt=_CHECKEN_PROMPT, model="gpt-test"
    )
    assert result == natural
    result_low = result.lower()
    assert "check" in result_low or "prüf" in result_low
    assert "mitnehm" in result_low or "mitmuss" in result_low


def test_naturalize_checken_falls_back_when_collapsed_to_one_object() -> None:
    """LLM collapses 'mitmuss' into 'Haustürschlüssel' → fidelity fails → original kept."""
    collapsed = "Vor dem Rausgehen überprüfe ich immer, ob der Haustürschlüssel eingepackt ist."
    svc = _make_svc(output_text=collapsed)
    candidate = "Vor dem Rausgehen muss ich doppelt checken was mitmuss."
    result = svc._naturalize_mirror_output(
        candidate, anchor_tokens=_CHECKEN_ANCHORS, user_prompt=_CHECKEN_PROMPT, model="gpt-test"
    )
    assert result == candidate


def test_naturalize_already_natural_sentence_passes_through() -> None:
    """A sentence that is already natural and passes fidelity → naturalized result is used."""
    good = "Beim Kochen merke ich inzwischen, dass ich mich irgendwann hinsetzen muss, weil mir schwindelig wird."
    svc = _make_svc(output_text=good)
    candidate = "Beim Kochen muss ich plötzlich sitzen wegen Schwindel."
    result = svc._naturalize_mirror_output(
        candidate, anchor_tokens=_COOKING_ANCHORS, user_prompt=_COOKING_PROMPT, model="gpt-test"
    )
    assert result == good


def test_naturalize_falls_back_on_exception() -> None:
    """openai_service raises → original returned without crashing."""
    svc = object.__new__(ContentOpsService)
    svc.openai_service = MagicMock()
    svc.openai_service.complete_messages.side_effect = RuntimeError("network error")
    candidate = "Beim Kochen muss ich plötzlich sitzen wegen Schwindel."
    result = svc._naturalize_mirror_output(
        candidate, anchor_tokens=_COOKING_ANCHORS, user_prompt=_COOKING_PROMPT, model="gpt-test"
    )
    assert result == candidate
