"""
Bot-vs-Master benchmark for IdeaQualityGate.

Each MASTER_EXAMPLES entry is shaped like recent strong Master output:
tiny real-life observation, concrete body/practical friction, changed
behaviour, one clear core, high filmability.

Each BOT_ANTI_PATTERNS entry is shaped like weak bot output that has
slipped through in live testing: broad lifestyle summary, wardrobe/shopping
topic, polished concept blur, meta framing, multi-core drift.

The benchmark asserts that every Master example scores ABOVE zero and every
bot anti-pattern scores BELOW zero — so the evaluator reliably distinguishes
them without LLM calls.
"""
from __future__ import annotations

import pytest

from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate


# ---------------------------------------------------------------------------
# Gold reference: Master-level ideas
# ---------------------------------------------------------------------------

MASTER_EXAMPLES: list[tuple[str, str]] = [
    ("where_i_can_sit_now",
     "wo ich jetzt noch sitzen kann"),
    ("socks_feel_different",
     "Socken und Schuhe fühlen sich plötzlich anders an"),
    ("hunger_vs_need_food_now",
     "Der Unterschied zwischen Hunger und Ich brauche jetzt sofort was"),
    ("small_things_need_planning",
     "Kleine Dinge brauchen plötzlich Planung"),
    ("things_that_used_to_work",
     "Dinge die früher praktisch waren und jetzt nicht mehr"),
    ("body_says_enough_sooner",
     "Mein Körper sagt früher genug"),
    ("shopping_needs_pauses",
     "Einkaufen braucht jetzt Pausen"),
]

# ---------------------------------------------------------------------------
# Negative reference: weak bot anti-patterns
# ---------------------------------------------------------------------------

BOT_ANTI_PATTERNS: list[tuple[str, str]] = [
    ("lifestyle_concept",
     "Das Schwangerschafts-Lifestyle-Konzept"),
    ("shopping_as_topic",
     "Einkaufen als großes Schwangerschaftsthema"),
    ("wardrobe_story",
     "Kleidungsgeschichte für Schwangere"),
    ("room_organisation",
     "Zimmer organisieren für das Baby"),
    ("broad_generalisation",
     "Viele Schwangere haben ähnliche Erfahrungen mit dem Körper"),
    ("meta_summary",
     "In diesem Video verschiedene Aspekte beleuchten"),
    ("listicle_shopping",
     "Tipps für den Supermarkt-Einkauf in der Schwangerschaft"),
]


# ---------------------------------------------------------------------------
# Benchmark tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,idea", MASTER_EXAMPLES)
def test_master_example_scores_above_zero(label: str, idea: str) -> None:
    """Every Master-like idea must score > 0 — it carries positive signals."""
    gate = IdeaQualityGate()
    result = gate.score(idea)
    assert result.score > 0, (
        f"[{label}] Expected score > 0 but got {result.score:.1f}.\n"
        f"  idea: {idea!r}\n"
        f"  reward_hits: {result.reward_hits}\n"
        f"  penalty_hits: {result.penalty_hits}\n"
        "  → Add or fix a reward pattern so this Master-like idea is recognised."
    )


@pytest.mark.parametrize("label,idea", BOT_ANTI_PATTERNS)
def test_bot_anti_pattern_scores_below_zero(label: str, idea: str) -> None:
    """Every weak bot anti-pattern must score < 0 — it carries penalty signals."""
    gate = IdeaQualityGate()
    result = gate.score(idea)
    assert result.score < 0, (
        f"[{label}] Expected score < 0 but got {result.score:.1f}.\n"
        f"  idea: {idea!r}\n"
        f"  reward_hits: {result.reward_hits}\n"
        f"  penalty_hits: {result.penalty_hits}\n"
        "  → Add or fix a penalty pattern so this bot anti-pattern is penalised."
    )


def test_master_average_beats_bot_average_by_wide_margin() -> None:
    """Aggregate: Master average score should exceed bot average by ≥ 2.0."""
    gate = IdeaQualityGate()
    master_scores = [gate.score(idea).score for _, idea in MASTER_EXAMPLES]
    bot_scores = [gate.score(idea).score for _, idea in BOT_ANTI_PATTERNS]
    avg_master = sum(master_scores) / len(master_scores)
    avg_bot = sum(bot_scores) / len(bot_scores)
    margin = avg_master - avg_bot
    assert margin >= 2.0, (
        f"Master avg={avg_master:.2f}, Bot avg={avg_bot:.2f}, margin={margin:.2f} < 2.0.\n"
        f"Master scores: {master_scores}\n"
        f"Bot scores: {bot_scores}"
    )


def test_pick_best_always_selects_master_over_bot_in_direct_pairs() -> None:
    """For each (master, bot) pair, pick_best must choose the master candidate."""
    gate = IdeaQualityGate()
    pairs = list(zip(MASTER_EXAMPLES, BOT_ANTI_PATTERNS))
    failures = []
    for (m_label, master), (b_label, bot) in pairs:
        winner, _ = gate.pick_best([master, bot])
        if winner != master:
            failures.append(
                f"  {m_label} vs {b_label}: pick_best chose bot.\n"
                f"    master={master!r} score={gate.score(master).score}\n"
                f"    bot={bot!r} score={gate.score(bot).score}"
            )
    assert not failures, "pick_best chose bot over master in:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Focused quality signals
# ---------------------------------------------------------------------------

def test_micro_observation_beats_lifestyle_blur() -> None:
    gate = IdeaQualityGate()
    micro = gate.score("Beim Kochen wird mir schwindelig – seitdem koche ich nur noch sitzend")
    blur = gate.score("Das Schwangerschaftsleben im Überblick – verschiedene Aspekte und Konzepte")
    assert micro.score > blur.score


def test_one_core_beats_multicore_blur() -> None:
    gate = IdeaQualityGate()
    one_core = gate.score("Der Moment als ich plötzlich beim Aufstehen zitterte")
    multi = gate.score("Einerseits die körperlichen Veränderungen, andererseits die emotionalen Aspekte")
    assert one_core.score > multi.score


def test_novelty_marker_beats_broad_topic() -> None:
    gate = IdeaQualityGate()
    novel = gate.score("Zum ersten Mal gespürt wie er sich dreht – heute morgen beim Frühstück")
    broad = gate.score("Viele Schwangere kennen ähnliche typische Erfahrungen")
    assert novel.score > broad.score


def test_concrete_prompt_with_anchors_beats_scene_drifted_alternative() -> None:
    gate = IdeaQualityGate()
    anchors = gate.extract_prompt_anchors(
        "beim kochen schwindelig sitzen schwangerschaft"
    )
    concrete = "Beim Kochen plötzlich sitzen müssen weil mir schwindelig wird"
    drifted = "Kleidung kaufen für den Supermarkt-Einkauf in der Schwangerschaft"
    best, _ = gate.pick_best([concrete, drifted], anchor_tokens=anchors)
    assert best == concrete


def test_shopping_verb_not_penalised_as_topic() -> None:
    """'einkaufen' as a verb describing behaviour change must not be penalised."""
    gate = IdeaQualityGate()
    result = gate.score("Einkaufen braucht jetzt Pausen")
    assert "shopping" not in result.penalty_hits, (
        f"'Einkaufen braucht jetzt Pausen' should not trigger shopping penalty. "
        f"penalty_hits={result.penalty_hits}"
    )


def test_shopping_listicle_is_penalised() -> None:
    """Explicit shopping-as-topic listicle must be penalised."""
    gate = IdeaQualityGate()
    result = gate.score("Tipps für den Supermarkt-Einkauf in der Schwangerschaft")
    assert result.score < 0
    assert "shopping" in result.penalty_hits or "listicle" in result.penalty_hits
