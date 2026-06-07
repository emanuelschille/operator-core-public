from __future__ import annotations

from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate, IdeaQualityScore


def _gate() -> IdeaQualityGate:
    return IdeaQualityGate()


# --- score() ---

def test_micro_observation_beats_lifestyle_listicle() -> None:
    gate = _gate()
    micro = gate.score("Heute morgen hab ich zum ersten Mal gespürt wie er sich dreht – einfach surreal")
    lifestyle = gate.score("Tipps für eine gesunde Ernährung in der Schwangerschaft")
    assert micro.score > lifestyle.score


def test_shopping_idea_is_penalised() -> None:
    gate = _gate()
    scored = gate.score("Was ich im Supermarkt kaufe für die Schwangerschaft")
    assert "shopping" in scored.penalty_hits
    assert scored.score < 0


def test_concrete_body_friction_is_rewarded() -> None:
    gate = _gate()
    scored = gate.score("Mein Rücken drückt plötzlich so stark dass ich nicht mehr sitze")
    assert scored.score > 0
    assert len(scored.reward_hits) >= 2


def test_abstract_concept_idea_is_penalised() -> None:
    gate = _gate()
    scored = gate.score("Verschiedene Aspekte und Perspektiven einer Schwangerschaft im Überblick")
    assert scored.score < 0
    assert "abstract" in scored.penalty_hits


def test_wardrobe_idea_is_penalised() -> None:
    gate = _gate()
    scored = gate.score("Schwangerschafts-Outfit-Ideen und Kleidung für den Babybauch")
    assert "wardrobe" in scored.penalty_hits


def test_jeans_and_kleiderschrank_are_penalised_as_wardrobe_drift() -> None:
    gate = _gate()
    scored = gate.score("Der Tag an dem ich meine Lieblingsjeans gegen Umstandsjeans im Kleiderschrank getauscht habe")
    assert "wardrobe" in scored.penalty_hits


def test_room_setup_drift_is_penalised() -> None:
    gate = _gate()
    scored = gate.score("Kreative Ordnungshelfer für kleine Räume und Deko im Babyzimmer")
    assert "room_setup" in scored.penalty_hits


def test_discovery_hook_is_rewarded() -> None:
    gate = _gate()
    scored = gate.score("Das wusste ich nicht: niemand hat mir gesagt dass Sodbrennen so schlimm wird")
    assert "discovery_hook" in scored.reward_hits
    assert scored.score > 0


# --- pick_best() ---

def test_pick_best_selects_highest_score() -> None:
    gate = _gate()
    candidates = [
        "Tipps für einen schönen Schwangerschafts-Look mit der richtigen Kleidung",
        "Heute morgen zum ersten Mal gespürt wie er tritt – einfach überwältigend",
        "Allgemeine Strategien und Methoden für eine gesunde Schwangerschaft",
    ]
    best, score = gate.pick_best(candidates)
    assert "tritt" in best or "gespürt" in best
    assert score >= 0


def test_pick_best_raises_on_empty() -> None:
    import pytest
    gate = _gate()
    with pytest.raises(ValueError):
        gate.pick_best([])


def test_minimum_winner_score_constant() -> None:
    assert IdeaQualityGate.MINIMUM_WINNER_SCORE == -1.0


def test_score_returns_correct_dataclass_type() -> None:
    gate = _gate()
    result = gate.score("Heute morgen Rücken weh")
    assert isinstance(result, IdeaQualityScore)
    assert isinstance(result.reward_hits, tuple)
    assert isinstance(result.penalty_hits, tuple)


# --- Anchor preservation ---

def test_extract_prompt_anchors_keeps_scene_words() -> None:
    gate = _gate()
    anchors = gate.extract_prompt_anchors(
        "ich muss seit der schwangerschaft beim kochen plötzlich sitzen weil mir schwindelig wird"
    )
    assert "kochen" in anchors
    assert "schwindelig" in anchors
    assert "sitzen" in anchors
    # stop words filtered
    assert "beim" not in anchors
    assert "weil" not in anchors
    assert "ich" not in anchors


def test_broad_prompt_returns_empty_or_minimal_anchors() -> None:
    gate = _gate()
    anchors = gate.extract_prompt_anchors("neue idee")
    # "neue" and "idee" are stop words → no useful anchors
    assert len(anchors) == 0


def test_anchor_score_rewards_scene_match() -> None:
    gate = _gate()
    anchors = gate.extract_prompt_anchors(
        "beim kochen schwindelig sitzen"
    )
    cooking_candidate = "Der Moment beim Kochen wo mir schwindelig wurde und ich sitzen musste"
    shoes_candidate = "Ich kann meine Schuhe nicht mehr binden weil der Bauch zu groß ist"

    cooking_bonus = gate.anchor_score(cooking_candidate, anchors)
    shoes_bonus = gate.anchor_score(shoes_candidate, anchors)

    assert cooking_bonus > shoes_bonus


def test_pick_best_with_anchors_selects_scene_preserving_candidate() -> None:
    gate = _gate()
    anchors = gate.extract_prompt_anchors(
        "beim kochen schwindelig sitzen schwangerschaft"
    )
    candidates = [
        "Ich kann meine Schuhe nicht mehr anziehen – der Bauch ist einfach zu groß",
        "Der Moment beim Kochen wo mir schwindelig wurde und ich plötzlich sitzen musste",
        "Kleidung kaufen für die Schwangerschaft – was wirklich noch passt",
    ]
    best, score = gate.pick_best(candidates, anchor_tokens=anchors)
    assert "Kochen" in best or "kochen" in best.lower()
    assert "schwindelig" in best.lower() or "schwindelig" in best


def test_no_anchor_tokens_means_no_bias() -> None:
    gate = _gate()
    # Without anchors, pick_best behaves as heuristic-only
    candidates = [
        "Heute morgen zum ersten Mal gespürt wie er tritt",  # +2 reward
        "Kochen schwindelig sitzen beim",                    # neutral, scene-match irrelevant
    ]
    best, _ = gate.pick_best(candidates, anchor_tokens=())
    assert "gespürt" in best  # heuristic winner, not anchor winner


def test_concrete_prompt_anchor_beats_generic_high_quality_idea() -> None:
    """
    Even a moderately rewarded generic idea should lose to a scene-anchored
    candidate when the user prompt is concrete enough.
    """
    gate = _gate()
    anchors = gate.extract_prompt_anchors(
        "beim kochen plötzlich schwindelig werden musste sitzen"
    )
    # This candidate has 2 reward hits (surprise_moment + shift_marker) but no anchors
    generic_good = "Plötzlich verändert sich alles – was ich nicht mehr kann seit der Schwangerschaft"
    # This candidate has fewer reward hits but strong anchor overlap
    anchored = "Beim Kochen wird mir schwindelig – seitdem koche ich nur noch sitzend"

    generic_total = gate.score(generic_good).score + gate.anchor_score(generic_good, anchors)
    anchored_total = gate.score(anchored).score + gate.anchor_score(anchored, anchors)

    assert anchored_total >= generic_total, (
        f"Anchored idea ({anchored_total:.2f}) should beat generic ({generic_total:.2f}) "
        "when user gave a concrete cooking/dizziness scene."
    )
