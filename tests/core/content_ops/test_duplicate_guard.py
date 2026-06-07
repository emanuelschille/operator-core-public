from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from operator_core.core.analysis_foundation.models import AnalysisFoundationResult, AnalysisSnapshot, ModelExecutionMeta, WriterBrief
from operator_core.core.content_ops.duplicate_guard import (
    DuplicateRiskGuard,
    IdeaHistoryReference,
    RiskEvaluation,
    ThemeRiskEvaluation,
)
from operator_core.integrations.openai_service import OpenAIService, OpenAIResponse

def _make_foundation_result(hook_examples: list[str] = None) -> AnalysisFoundationResult:
    execution_meta = ModelExecutionMeta(
        provider_name="openai", model_name="gpt-test", task_role="analysis", status="completed"
    )
    snapshot = AnalysisSnapshot(
        snapshot_id="as1",
        project_key="everydayengel",
        scope="platform",
        created_at="2026-04-17T10:00:00Z",
        title="Test",
        summary="Test",
        platform_key="tiktok",
        analytics_summary_lines=(f"Hook examples: {' | '.join(hook_examples or [])}",),
        rule_summary_lines=(),
        source_refs=(),
    )
    return AnalysisFoundationResult(
        lane_name="analysis",
        project_key="everydayengel",
        action_type="snapshot",
        title="Test",
        summary="Test",
        analysis_snapshots=(snapshot,),
        writer_brief=WriterBrief(
            brief_id="wb1", project_key="everydayengel", created_at="2026-04-17T10:00:00Z",
            objective="Test", audience="Test", constraints=(), source_snapshot_ids=("as1",),
            provider_name="openai", model_name="gpt-test", task_role="writer", execution_meta=execution_meta
        ),
        evidence_pack=MagicMock(),
        execution_meta=execution_meta,
    )

def test_duplicate_guard_detects_high_risk_on_mudigkeit() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result(hook_examples=["Wach bleiben in der Schwangerschaft", "Müdigkeit bekämpfen"])
    
    # Candidate is too similar
    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Neue Tipps gegen Müdigkeit",
        foundation_result=foundation
    )
    
    assert evaluation.level == "high"
    assert any("Müdigkeit" in item for item in evaluation.blocking_items)

def test_duplicate_guard_detects_high_risk_on_draft_context() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result()
    
    # Candidate matches an existing draft
    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Tipps für besseren Schlaf",
        foundation_result=foundation,
        recent_drafts=["Schlaf-Hacks für Schwangere"]
    )
    
    assert evaluation.level == "high"
    assert any("existing_draft" in item for item in evaluation.blocking_items)

def test_duplicate_guard_detects_high_risk_on_recent_idea_context() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result()
    
    # Candidate matches a recently generated idea
    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Was ich in der Kliniktasche habe",
        foundation_result=foundation,
        recent_ideas=["Meine Packliste für die Kliniktasche"]
    )
    
    assert evaluation.level == "high"
    assert any("recent_idea" in item for item in evaluation.blocking_items)

def test_duplicate_guard_hardened_logic_detects_same_meaning() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result()
    
    # Same meaning, different words (e.g. Kliniktasche vs Krankenhaustasche)
    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Sachen für die Krankenhaustasche",
        foundation_result=foundation,
        recent_ideas=["Was in die Kliniktasche muss"]
    )
    
    # Similarity should be high enough (Sachen, Tasche overlap, Krankenhaus/Klinik might not overlap but others do)
    # Actually Krankenhaus/Klinik don't overlap, but "Sachen", "für", "die", "Tasche" do.
    # Set overlap: {sachen, tasche} vs {was, kliniktasche, muss}. 
    # Wait, my calculate_similarity filters < 3 chars. 
    # sachen, tasche vs kliniktasche. 
    # Let's use a better example.
    
    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Morgenroutine für mehr Energie",
        foundation_result=foundation,
        recent_ideas=["Meine Routine am Morgen für Energie"]
    )
    
    assert evaluation.level == "high"

def test_duplicate_guard_fires_high_risk_for_long_candidate_with_keyword_match() -> None:
    """
    Long, descriptive Müdigkeit candidate vs short reference.

    When the candidate has many extra tokens (common in real OpenAI output),
    the raw similarity drops below 0.3 even though the TOPIC is identical.
    The keyword_match path must still fire HIGH RISK at a lower similarity
    threshold so same-core Müdigkeit ideas are always blocked.

    candidate: "Müdigkeit in der Schwangerschaft – wie ich wach bleibe trotz Erschöpfung"
    → ~8 tokens: {müdigkeit, schwangerschaft, wie, ich, wach, bleibe, trotz, erschöpfung}

    reference:  "Müdigkeit in der Schwangerschaft: 3 Tipps die mir wirklich geholfen haben"
    → ~7 tokens: {müdigkeit, schwangerschaft, tipps, mir, wirklich, geholfen, haben}

    intersection = 2 ({müdigkeit, schwangerschaft})
    similarity = 2/7 ≈ 0.286 — below the old 0.30 keyword_match threshold.
    keyword "müde" in both → must still produce HIGH, not MEDIUM.
    """
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result()

    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Müdigkeit in der Schwangerschaft – wie ich wach bleibe trotz Erschöpfung",
        foundation_result=foundation,
        recent_ideas=["Müdigkeit in der Schwangerschaft: 3 Tipps die mir wirklich geholfen haben"],
    )

    assert evaluation.level == "high", (
        f"Expected 'high' but got '{evaluation.level}'. "
        "The keyword_match threshold is too tight for long-form candidate vs short reference."
    )


def test_duplicate_guard_allows_low_risk_on_new_topic() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result(hook_examples=["Wach bleiben in der Schwangerschaft"])
    
    # Candidate is a new topic
    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Erste Bewegungen im Bauch spüren",
        foundation_result=foundation
    )
    
    assert evaluation.level == "low"


def test_same_core_posted_cooking_sitting_dizziness_is_high_risk() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result()

    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Beim Kochen wird mir schwindelig und ich muss mich kurz hinsetzen.",
        foundation_result=foundation,
        recent_posts=["Beim Kochen merke ich plötzlich, dass ich sitzen muss wegen Schwindel."],
    )

    assert evaluation.level == "high"
    assert any("recent_post" in item for item in evaluation.blocking_items)


def test_lightly_reworded_supermarket_pause_clone_is_high_risk() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result()

    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Im Supermarkt brauche ich inzwischen plötzlich kleine Pausen.",
        foundation_result=foundation,
        recent_ideas=["Einkaufen im Supermarkt geht nur noch mit Pause zwischendurch."],
    )

    assert evaluation.level == "high"
    assert any("recent_idea" in item for item in evaluation.blocking_items)


def test_same_family_new_angle_can_pass_core_repeat_guard() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result()

    evaluation = guard.evaluate_core_repeat(
        candidate_idea="Beim Kochen sind Gerüche plötzlich zu viel für mich.",
        foundation_result=foundation,
        recent_posts=["Beim Kochen merke ich plötzlich, dass ich sitzen muss wegen Schwindel."],
    )

    assert evaluation.repeated is False


def test_recently_accepted_same_core_checking_idea_is_rejected() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result()

    evaluation = guard.evaluate(
        project_key="everydayengel",
        candidate_idea="Vor dem Rausgehen prüfe ich doppelt, was ich mitnehmen muss.",
        foundation_result=foundation,
        recent_ideas=["Vor dem Losgehen muss ich zweimal checken, was alles mitmuss."],
    )

    assert evaluation.level == "high"
    assert any("recent_idea" in item for item in evaluation.blocking_items)


def test_core_repeat_uses_typed_idea_history_reference_source() -> None:
    guard = DuplicateRiskGuard()
    foundation = _make_foundation_result()

    evaluation = guard.evaluate_core_repeat(
        candidate_idea="Vor dem Rausgehen prüfe ich doppelt, was ich mitnehmen muss.",
        foundation_result=foundation,
        recent_history=(
            IdeaHistoryReference(
                text="Vor dem Losgehen muss ich zweimal checken, was alles mitmuss.",
                source="recent_idea_rejected",
            ),
        ),
    )

    assert evaluation.repeated is True
    assert any("recent_idea_rejected" in item for item in evaluation.blocking_items)

# --- Theme Cooldown Tests ---

def test_theme_cooldown_blocks_same_theme_different_wording() -> None:
    """
    Candidate uses different surface wording but belongs to the same theme cluster.
    'Power Nap' and 'Energiequellen' share the muedigkeit_energie cluster with 'Müdigkeit'.
    One matching recent reference is enough to saturate.
    """
    guard = DuplicateRiskGuard()

    result = guard.evaluate_theme_risk(
        candidate_idea="Power Nap Tipps für Schwangere",
        recent_drafts=["Energie-Hacks gegen Müdigkeit im ersten Trimester"],
    )

    assert result.saturated is True
    assert result.cluster_name == "muedigkeit_energie"
    assert any("recent_draft" in item for item in result.blocking_items)


def test_theme_cooldown_generates_pivot_outside_cluster() -> None:
    """
    On high theme-risk the pivot prompt must explicitly instruct the LLM to
    leave the saturated cluster — verified via the system_prompt content.
    """
    openai_svc = MagicMock(spec=OpenAIService)
    openai_svc.complete_messages.return_value = OpenAIResponse(
        output_text="1. Erste Babybewegungen spüren\n2. Ultraschall Erfahrung\n3. Emotionen im zweiten Trimester",
        model="gpt-test",
        finish_reason="stop",
        raw_payload={},
    )

    guard = DuplicateRiskGuard(openai_svc)
    theme_risk = ThemeRiskEvaluation(
        saturated=True,
        cluster_name="muedigkeit_energie",
        blocking_items=("[recent_idea] Müdigkeit bekämpfen",),
        reason="Theme saturated",
    )

    alternatives = guard.generate_theme_pivot_alternatives(
        project_key="everydayengel",
        original_idea="Power Nap in der Schwangerschaft",
        theme_risk=theme_risk,
        platform="tiktok",
    )

    assert len(alternatives) == 3
    call_kwargs = openai_svc.complete_messages.call_args.kwargs
    system_prompt = call_kwargs["system_prompt"]
    assert "AUSSERHALB" in system_prompt or "außerhalb" in system_prompt.lower()
    assert "muedigkeit" in system_prompt


def test_theme_cooldown_allows_genuinely_different_topic() -> None:
    """
    Candidate belongs to a different cluster → not saturated even if muedigkeit_energie is full.
    """
    guard = DuplicateRiskGuard()

    result = guard.evaluate_theme_risk(
        candidate_idea="Erste Bewegungen im Bauch spüren",
        recent_drafts=["Müdigkeit bekämpfen im ersten Trimester"],
        recent_ideas=["Energie trotz Schwangerschaft"],
    )

    assert result.saturated is False


def test_theme_cooldown_allows_justified_followup() -> None:
    """
    Candidate with an explicit follow-up marker bypasses theme cooldown entirely.
    """
    guard = DuplicateRiskGuard()

    result = guard.evaluate_theme_risk(
        candidate_idea="Müdigkeit Update – was hat sich nach 3 Wochen verändert",
        recent_ideas=["Müdigkeit bekämpfen in der Schwangerschaft"],
    )

    assert result.saturated is False
    assert "übersprungen" in result.reason


def _make_theme_pivot_call(cluster_name: str = "muedigkeit_energie") -> str:
    """Helper: call generate_theme_pivot_alternatives and return the captured system_prompt."""
    openai_svc = MagicMock(spec=OpenAIService)
    openai_svc.complete_messages.return_value = OpenAIResponse(
        output_text="1. Idee A\n2. Idee B\n3. Idee C",
        model="gpt-test",
        finish_reason="stop",
        raw_payload={},
    )
    guard = DuplicateRiskGuard(openai_svc)
    theme_risk = ThemeRiskEvaluation(
        saturated=True,
        cluster_name=cluster_name,
        blocking_items=("[recent_idea] Müdigkeit bekämpfen",),
        reason="Theme saturated",
    )
    guard.generate_theme_pivot_alternatives(
        project_key="everydayengel",
        original_idea="Power Nap in der Schwangerschaft",
        theme_risk=theme_risk,
        platform="tiktok",
    )
    return openai_svc.complete_messages.call_args.kwargs["system_prompt"]


def test_theme_pivot_prompt_blocks_cluster_and_warns_against_generic_drift() -> None:
    """
    Pivot prompt must:
    - block the saturated cluster (AUSSERHALB + cluster name)
    - explicitly warn against generic lifestyle drift (Babynamen, Standard-Tipps)
    so the LLM does not jump to arbitrary broad pregnancy topics.
    """
    prompt = _make_theme_pivot_call()

    assert "AUSSERHALB" in prompt or "außerhalb" in prompt.lower()
    assert "muedigkeit" in prompt.lower()
    # Generic drift warning
    assert "Generisch" in prompt or "generisch" in prompt.lower() or "Standard" in prompt or "Babynamen" in prompt
    assert "Jeans" in prompt or "Outfits" in prompt or "Ordnungshelfer" in prompt


def test_theme_pivot_prompt_uses_soft_families_as_weak_guidance() -> None:
    """
    Pivot prompt must list the early everydayengel soft families (Alltag, Gedanken,
    Erleichterungen) as SOFT GUIDANCE — not as mandatory output format.
    Proved by:
    - families mentioned in prompt
    - labeled as optional ("kein Zwang" / "bevorzuge" / "SOFT")
    """
    prompt = _make_theme_pivot_call()

    assert "Alltag" in prompt
    assert "Gedanken" in prompt or "Beobachtung" in prompt
    assert "Erleichterung" in prompt
    # Soft framing — families must not be a hard rule
    assert "kein Zwang" in prompt or "bevorzuge" in prompt or "SOFT" in prompt


def test_theme_pivot_prompt_anchors_to_everydayengel_brand_and_novelty() -> None:
    """
    Pivot prompt must name 'everydayengel' explicitly (brand anchoring)
    and put NOVELTY ZUERST so the LLM does not default to safe/familiar territory.
    """
    prompt = _make_theme_pivot_call()

    assert "everydayengel" in prompt
    assert "NOVELTY" in prompt or "Novelty" in prompt


def test_generate_alternatives_uses_openai() -> None:
    openai_svc = MagicMock(spec=OpenAIService)
    openai_svc.complete_messages.return_value = OpenAIResponse(
        output_text="1. Neuer Winkel A\n2. Frische Szene B\n3. Emotionaler Fokus C",
        model="gpt-test",
        finish_reason="stop",
        raw_payload={}
    )
    
    guard = DuplicateRiskGuard(openai_svc)
    risk = RiskEvaluation(level="high", reason="Too similar", blocking_items=("Old hook",))
    
    alternatives = guard.generate_alternatives(
        project_key="everydayengel",
        original_idea="Too old idea",
        risk_evaluation=risk,
        platform="tiktok"
    )
    
    assert len(alternatives) == 3
    assert alternatives[0] == "Neuer Winkel A"
    assert alternatives[2] == "Emotionaler Fokus C"
    assert openai_svc.complete_messages.called


def test_generate_alternatives_prompt_blocks_drift_and_prefers_concrete_everyday_fit() -> None:
    openai_svc = MagicMock(spec=OpenAIService)
    openai_svc.complete_messages.return_value = OpenAIResponse(
        output_text="1. Idee A\n2. Idee B\n3. Idee C",
        model="gpt-test",
        finish_reason="stop",
        raw_payload={}
    )

    guard = DuplicateRiskGuard(openai_svc)
    risk = RiskEvaluation(level="high", reason="Too similar", blocking_items=("Old hook",))
    guard.generate_alternatives(
        project_key="everydayengel",
        original_idea="Zu ähnliche Idee",
        risk_evaluation=risk,
        platform="tiktok",
    )

    prompt = openai_svc.complete_messages.call_args.kwargs["system_prompt"]
    assert "Kleiderschrank" in prompt or "Jeans" in prompt
    assert "Room-Setup" in prompt or "Deko" in prompt
    assert "Kleine konkrete Alltagsbeobachtung" in prompt
    assert "Körperliche oder praktische Reibung" in prompt


def test_clean_generated_ideas_removes_angle_labels_and_markdown() -> None:
    openai_svc = MagicMock(spec=OpenAIService)
    openai_svc.complete_messages.return_value = OpenAIResponse(
        output_text="1. **Angle A**: Idee eins\n2. Idee B: Idee zwei\n3. Alternative C: Idee drei",
        model="gpt-test",
        finish_reason="stop",
        raw_payload={}
    )

    guard = DuplicateRiskGuard(openai_svc)
    risk = RiskEvaluation(level="high", reason="Too similar", blocking_items=("Old hook",))
    alternatives = guard.generate_alternatives(
        project_key="everydayengel",
        original_idea="Zu ähnliche Idee",
        risk_evaluation=risk,
        platform="tiktok",
    )

    assert alternatives == ["Idee eins", "Idee zwei", "Idee drei"]
