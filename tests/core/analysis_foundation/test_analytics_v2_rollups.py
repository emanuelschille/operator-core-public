from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from operator_core.core.analysis_foundation.v2.models import ScoredPost, PostDerivedMetrics
from operator_core.core.analysis_foundation.v2.scoring import ScoringEngine

def _make_scored_post(id: str, cta: str, score: float) -> ScoredPost:
    return ScoredPost(
        content_id=id,
        posted_at=datetime.now(),
        platform="tiktok",
        raw_metrics={},
        derived_metrics=PostDerivedMetrics(),
        scores={},
        total_score=score,
        metadata={"cta_typ": cta}
    )

def test_calculate_platform_rollup_groups_and_scores() -> None:
    engine = ScoringEngine()
    
    # 5 posts for CTA "Frage" (Eligible)
    # 2 posts for CTA "Link" (Insufficient)
    posts = [
        _make_scored_post("p1", "Frage", 1.0),
        _make_scored_post("p2", "Frage", 1.2),
        _make_scored_post("p3", "Frage", 0.8),
        _make_scored_post("p4", "Frage", 1.5),
        _make_scored_post("p5", "Frage", 1.0), # Median should be 1.0
        
        _make_scored_post("p6", "Link", 2.0),
        _make_scored_post("p7", "Link", 2.2),
    ]
    
    rollup = engine.calculate_platform_rollup("tiktok", posts, min_n=5)
    
    cta_rollups = {c.value: c for c in rollup.categories["cta"]}
    
    assert "Frage" in cta_rollups
    assert cta_rollups["Frage"].post_count == 5
    assert cta_rollups["Frage"].median_total_score == 1.0
    assert cta_rollups["Frage"].is_eligible is True
    
    assert "Link" in cta_rollups
    assert cta_rollups["Link"].post_count == 2
    assert cta_rollups["Link"].is_eligible is False

def test_calculate_platform_rollup_sorting() -> None:
    engine = ScoringEngine()
    
    # All eligible, different scores
    posts = []
    for i in range(5): posts.append(_make_scored_post(f"a{i}", "A", 0.5))
    for i in range(5): posts.append(_make_scored_post(f"b{i}", "B", 1.5))
    for i in range(2): posts.append(_make_scored_post(f"c{i}", "C", 2.5)) # High score but insufficient
    
    rollup = engine.calculate_platform_rollup("tiktok", posts, min_n=5)
    
    ctas = rollup.categories["cta"]
    # Order should be B (eligible, 1.5), A (eligible, 0.5), C (ineligible)
    assert ctas[0].value == "B"
    assert ctas[1].value == "A"
    assert ctas[2].value == "C"

def test_rollup_prioritizes_normalized_fields() -> None:
    engine = ScoringEngine()
    
    post = ScoredPost(
        content_id="p1", posted_at=datetime.now(), platform="tiktok",
        raw_metrics={}, derived_metrics=PostDerivedMetrics(), scores={},
        total_score=1.0,
        metadata={
            "cta_typ": "Raw Question", 
            "cta_typ_norm": "community_question"
        }
    )
    
    rollup = engine.calculate_platform_rollup("tiktok", [post], min_n=1)
    
    # Dimension "cta" should use the norm value
    cta_values = [c.value for c in rollup.categories["cta"]]
    assert "community_question" in cta_values
    assert "Raw Question" not in cta_values

def test_rollup_skips_raw_hook_kurz() -> None:
    engine = ScoringEngine()
    
    post = ScoredPost(
        content_id="p1", posted_at=datetime.now(), platform="tiktok",
        raw_metrics={}, derived_metrics=PostDerivedMetrics(), scores={},
        total_score=1.0,
        metadata={
            "hook_kurz": "Raw Hook Text",
            "hook_pattern": "question"
        }
    )
    
    rollup = engine.calculate_platform_rollup("tiktok", [post], min_n=1)
    
    # "hook" dimension should only contain "question", not the raw text
    hook_values = [c.value for c in rollup.categories["hook"]]
    assert "question" in hook_values
    assert "Raw Hook Text" not in hook_values
