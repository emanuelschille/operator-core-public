"""
Tests for MIRROR vs IDEATION mode classification and prompt behavior.

MIRROR: user prompt contains first-person language ('ich', 'mir', 'mein*')
        → preserve the concrete lived moment faithfully, no word substitution.
IDEATION: empty, topic-list, or no first-person anchor
        → freely generate a fresh micro-observation.
"""
from __future__ import annotations

from operator_core.core.analysis_foundation.models import (
    AnalysisFoundationResult,
    AnalysisSnapshot,
    ModelExecutionMeta,
    WriterBrief,
)
from operator_core.core.content_ops.duplicate_guard import IdeaQualityGate
from operator_core.core.content_ops.service import ContentOpsService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_brief() -> WriterBrief:
    meta = ModelExecutionMeta(
        provider_name="openai", model_name="gpt-test", task_role="writer", status="completed"
    )
    return WriterBrief(
        brief_id="wb-test",
        project_key="everydayengel",
        created_at="2026-04-21T00:00:00Z",
        objective="Test objective",
        audience="Schwangere Frauen",
        constraints=(),
        source_snapshot_ids=(),
        provider_name="openai",
        model_name="gpt-test",
        task_role="writer",
        execution_meta=meta,
    )


def _build_prompt(*, mirror: bool, anchors: tuple[str, ...] = ()) -> str:
    svc = object.__new__(ContentOpsService)
    return svc._build_foundation_idea_system_prompt(
        platform="tiktok",
        writer_brief=_make_brief(),
        selected_snapshots=(),
        weekly_analysis=None,
        sharpen_mode=mirror,
        anchor_tokens=anchors,
    )


# ---------------------------------------------------------------------------
# classify_idea_mode — spec examples
# ---------------------------------------------------------------------------

def test_empty_prompt_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode("") == "ideation"


def test_whitespace_only_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode("   ") == "ideation"


def test_keyword_list_muedigkeit_is_ideation() -> None:
    """Topic nouns without first-person → IDEATION, even with ≥3 tokens."""
    assert IdeaQualityGate.classify_idea_mode(
        "Müdigkeit wach bleiben Erschöpfung Schwangerschaft"
    ) == "ideation"


def test_ich_muss_beim_kochen_sitzen_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "ich muss beim kochen plötzlich sitzen weil mir schwindelig wird"
    ) == "mirror"


def test_warum_ich_beim_einkaufen_pause_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "warum ich seit der schwangerschaft beim einkaufen plötzlich viel öfter pause machen muss"
    ) == "mirror"


def test_seit_schwangerschaft_ueberlege_ich_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "seit der schwangerschaft überlege ich vor dem rausgehen plötzlich doppelt was ich alles mitnehmen muss"
    ) == "mirror"


# ---------------------------------------------------------------------------
# classify_idea_mode — first-person triggers
# ---------------------------------------------------------------------------

def test_mir_alone_triggers_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode("mir wird beim Stehen schwindelig") == "mirror"


def test_mein_triggers_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode("mein Rücken tut weh") == "mirror"


def test_meine_triggers_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode("meine Füße schwellen abends an") == "mirror"


def test_meinen_triggers_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode("ich kann meinen Rücken kaum spüren") == "mirror"


def test_meiner_triggers_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode("meiner Meinung nach verändert sich alles") == "mirror"


# ---------------------------------------------------------------------------
# classify_idea_mode — no first-person → IDEATION
# ---------------------------------------------------------------------------

def test_body_topic_no_first_person_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode("Rücken Schmerzen Schwangerschaft") == "ideation"


def test_scene_keywords_no_first_person_is_ideation() -> None:
    """Keywords describing a scene but no 'ich'/'mir'/'mein*' → IDEATION."""
    assert IdeaQualityGate.classify_idea_mode("beim Kochen Schwindel") == "ideation"


def test_single_noun_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode("Erschöpfung") == "ideation"


def test_einkaufen_pausen_no_first_person_is_ideation() -> None:
    """Previously this triggered sharpen via token count; now correctly IDEATION."""
    assert IdeaQualityGate.classify_idea_mode(
        "einkaufen braucht jetzt pausen schwangerschaft"
    ) == "ideation"


# ---------------------------------------------------------------------------
# Prompt builder — MIRROR mode content
# ---------------------------------------------------------------------------

def test_mirror_prompt_contains_mirror_label() -> None:
    prompt = _build_prompt(mirror=True)
    assert "MIRROR" in prompt


def test_mirror_prompt_contains_no_substitute_rule() -> None:
    """MIRROR must explicitly forbid substituting the user's concrete words."""
    prompt = _build_prompt(mirror=True, anchors=("kochen", "sitzen", "schwindelig"))
    assert "ersetze" in prompt.lower() or "NICHT" in prompt


def test_mirror_prompt_blocks_raw_echo() -> None:
    prompt = _build_prompt(mirror=True, anchors=("kochen", "sitzen", "schwindelig"))
    assert "Roh-Echo" in prompt
    assert "Ich muss" in prompt


def test_mirror_prompt_has_sitzen_example() -> None:
    """The sitzen/anlehnen example must be in the MIRROR prompt."""
    prompt = _build_prompt(mirror=True)
    assert "sitzen" in prompt
    assert "anlehnen" in prompt


def test_mirror_prompt_includes_anchor_hint() -> None:
    """Anchor tokens must be surfaced in the MIRROR prompt."""
    prompt = _build_prompt(mirror=True, anchors=("kochen", "sitzen", "schwindelig"))
    assert "kochen" in prompt
    assert "sitzen" in prompt


def test_mirror_prompt_does_not_contain_ideation_label() -> None:
    prompt = _build_prompt(mirror=True)
    assert "IDEATION" not in prompt


# ---------------------------------------------------------------------------
# Prompt builder — IDEATION mode content
# ---------------------------------------------------------------------------

def test_ideation_prompt_contains_ideation_label() -> None:
    prompt = _build_prompt(mirror=False)
    assert "IDEATION" in prompt


def test_ideation_prompt_does_not_contain_mirror_label() -> None:
    prompt = _build_prompt(mirror=False)
    assert "MIRROR" not in prompt


def test_ideation_prompt_contains_positive_examples() -> None:
    """IDEATION must have ✓ style examples to anchor the target voice."""
    prompt = _build_prompt(mirror=False)
    assert "✓" in prompt


def test_ideation_prompt_contains_nicht_so_examples() -> None:
    """IDEATION must have ✗ anti-examples."""
    prompt = _build_prompt(mirror=False)
    assert "✗" in prompt


# ---------------------------------------------------------------------------
# Regression: critical concrete prompts still MIRROR
# ---------------------------------------------------------------------------

def test_regression_rucken_beim_kochen_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "ich spüre meinen Rücken beim Kochen immer stärker"
    ) == "mirror"


def test_regression_schwindel_aufstehen_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "mir wird schwindelig wenn ich aufstehe"
    ) == "mirror"


def test_regression_einkaufen_sitzen_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "ich muss beim Einkaufen immer öfter sitzen"
    ) == "mirror"


# ---------------------------------------------------------------------------
# Regression: broad/empty prompts still IDEATION
# ---------------------------------------------------------------------------

def test_regression_neue_idee_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode("neue Idee") == "ideation"


def test_regression_broad_muedigkeit_keywords_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "Müdigkeit Erschöpfung Energie Schlaf"
    ) == "ideation"


def test_regression_rucken_keywords_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "Rücken Schmerzen SSW30"
    ) == "ideation"


# ---------------------------------------------------------------------------
# Non-first-person concrete moments — new scene+verb rule (MIRROR)
# ---------------------------------------------------------------------------

def test_beim_kochen_sitzen_wegen_schwindel_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "beim kochen plötzlich sitzen wegen schwindel"
    ) == "mirror"


def test_im_supermarkt_pause_brauchen_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "im supermarkt plötzlich pause brauchen"
    ) == "mirror"


def test_vor_rausgehen_checken_mitmuss_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "vor dem rausgehen doppelt checken was mitmuss"
    ) == "mirror"


def test_ruecken_meldet_nach_zehn_minuten_stehen_is_mirror() -> None:
    assert IdeaQualityGate.classify_idea_mode(
        "rücken meldet sich nach zehn minuten stehen"
    ) == "mirror"


# ---------------------------------------------------------------------------
# Broad keyword prompts — no scene+verb combo → IDEATION
# ---------------------------------------------------------------------------

def test_ruecken_schmerzen_ssw30_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode("Rücken Schmerzen SSW30") == "ideation"


def test_schwangerschaft_alltag_muede_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode("Schwangerschaft Alltag müde") == "ideation"


# ---------------------------------------------------------------------------
# Edge: scene connector alone (no concrete verb) → IDEATION
# ---------------------------------------------------------------------------

def test_beim_kochen_schwindel_no_verb_is_ideation() -> None:
    """scene connector present but symptom noun only, no concrete verb → IDEATION"""
    assert IdeaQualityGate.classify_idea_mode("beim Kochen Schwindel") == "ideation"


# Edge: concrete verb alone (no scene connector) → IDEATION
def test_sitzen_alone_no_scene_is_ideation() -> None:
    assert IdeaQualityGate.classify_idea_mode("sitzen Erschöpfung Schwangerschaft") == "ideation"


# ---------------------------------------------------------------------------
# mirror_fidelity_score
# ---------------------------------------------------------------------------

def test_fidelity_sitzen_exact_match() -> None:
    assert IdeaQualityGate.mirror_fidelity_score(
        "Beim Kochen muss ich plötzlich sitzen", ("sitzen",)
    ) == 1.0


def test_fidelity_sitzen_near_form_hinsetzen() -> None:
    assert IdeaQualityGate.mirror_fidelity_score(
        "Beim Kochen muss ich mich plötzlich hinsetzen", ("sitzen",)
    ) == 1.0


def test_fidelity_sitzen_drift_abstuetzen_fails() -> None:
    assert IdeaQualityGate.mirror_fidelity_score(
        "Ich muss mich beim Kochen abstützen", ("sitzen",)
    ) == 0.0


def test_fidelity_pause_brauchen_drift_fails() -> None:
    """Both 'pause' and 'brauchen' anchors — output with only 'abstützen' → 0.0."""
    assert IdeaQualityGate.mirror_fidelity_score(
        "Im Supermarkt muss ich mich abstützen",
        ("supermarkt", "plötzlich", "pause", "brauchen"),
    ) < IdeaQualityGate.MIRROR_FIDELITY_THRESHOLD


def test_fidelity_checken_mitmuss_collapsed_fails() -> None:
    """Output invents specific object (house key) instead of preserving 'checken'/'mitmuss'."""
    assert IdeaQualityGate.mirror_fidelity_score(
        "Ich überprüfe ob mein Haustürschlüssel eingepackt ist",
        ("rausgehen", "doppelt", "checken", "mitmuss"),
    ) < IdeaQualityGate.MIRROR_FIDELITY_THRESHOLD


def test_fidelity_meldet_stehen_passes() -> None:
    assert IdeaQualityGate.mirror_fidelity_score(
        "Mein Rücken meldet sich nach zehn Minuten Stehen",
        ("rücken", "meldet", "sich", "nach", "zehn", "minuten", "stehen"),
    ) == 1.0


def test_fidelity_empty_anchors_returns_1() -> None:
    assert IdeaQualityGate.mirror_fidelity_score("beliebiger Satz", ()) == 1.0


def test_fidelity_scene_detail_anchors_are_checked() -> None:
    assert IdeaQualityGate.mirror_fidelity_score(
        "Beim Kochen muss ich plötzlich sitzen, weil mir schwindelig wird",
        ("kochen", "sitzen", "schwindel"),
    ) == 1.0


def test_fidelity_kochen_sitzen_schwindel_rejects_support_drift() -> None:
    assert IdeaQualityGate.mirror_fidelity_score(
        "Beim Kochen brauche ich plötzlich eine Stütze, weil mir schwindelig wird",
        ("kochen", "sitzen", "schwindel"),
    ) < IdeaQualityGate.MIRROR_FIDELITY_THRESHOLD


def test_fidelity_pause_in_supermarkt_passes() -> None:
    assert IdeaQualityGate.mirror_fidelity_score(
        "Im Supermarkt brauche ich plötzlich eine Pause",
        ("supermarkt", "plötzlich", "pause", "brauchen"),
    ) == 1.0


def test_fidelity_checken_mitmuss_near_forms_pass() -> None:
    assert IdeaQualityGate.mirror_fidelity_score(
        "Vor dem Rausgehen prüfe ich doppelt, was mit muss",
        ("rausgehen", "doppelt", "checken", "mitmuss"),
    ) == 1.0


def test_mirror_prompt_echo_detects_raw_wrapped_prompt() -> None:
    assert IdeaQualityGate.is_mirror_prompt_echo(
        "Ich muss beim Kochen plötzlich sitzen wegen Schwindel",
        "beim Kochen plötzlich sitzen wegen Schwindel",
    )


def test_mirror_prompt_echo_allows_natural_same_anchor_sentence() -> None:
    assert not IdeaQualityGate.is_mirror_prompt_echo(
        "Beim Kochen muss ich mich plötzlich hinsetzen, weil mir schwindelig wird",
        "beim Kochen plötzlich sitzen wegen Schwindel",
    )
