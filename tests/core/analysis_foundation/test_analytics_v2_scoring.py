from __future__ import annotations

from datetime import datetime, timezone
from statistics import median

import pytest

from operator_core.core.analysis_foundation.v2.models import ScoredPost, PostDerivedMetrics
from operator_core.core.analysis_foundation.v2.scoring import (
    calculate_mad,
    compute_derived_metrics,
    ScoringEngine,
)

def test_calculate_mad() -> None:
    data = [1, 2, 3, 4, 10]
    # Median is 3
    # Absolute deviations: [2, 1, 0, 1, 7]
    # Sorted deviations: [0, 1, 1, 2, 7]
    # Median deviation is 1
    assert calculate_mad(data) == 1.0
    assert calculate_mad([]) == 0.0

def test_compute_derived_metrics() -> None:
    raw = {
        "views_72h": 1000,
        "likes_72h": 50,
        "saves_72h": 10,
        "avg_watch_sec_72h": 15,
        "duration_sec": 30,
    }
    derived = compute_derived_metrics(raw, "tiktok")
    
    assert derived.completion_rate == 0.5
    assert derived.engagement_rate == 0.06 # (50 + 10) / 1000
    assert derived.save_rate == 0.01 # 10 / 1000
    assert derived.view_efficiency is None # No followers provided

def test_compute_derived_metrics_zero_division() -> None:
    raw = {
        "views_72h": 0,
        "duration_sec": 0,
    }
    derived = compute_derived_metrics(raw, "tiktok")
    assert derived.completion_rate is None
    assert derived.engagement_rate is None

def test_scoring_engine_calculate_baseline() -> None:
    engine = ScoringEngine()
    
    posts = [
        ScoredPost(
            content_id="p1", posted_at=datetime.now(), platform="tiktok",
            raw_metrics={"views_72h": 100},
            derived_metrics=PostDerivedMetrics(completion_rate=0.5),
            scores={}
        ),
        ScoredPost(
            content_id="p2", posted_at=datetime.now(), platform="tiktok",
            raw_metrics={"views_72h": 200},
            derived_metrics=PostDerivedMetrics(completion_rate=0.1),
            scores={}
        ),
        ScoredPost(
            content_id="p3", posted_at=datetime.now(), platform="tiktok",
            raw_metrics={"views_72h": 300},
            derived_metrics=PostDerivedMetrics(completion_rate=0.9),
            scores={}
        ),
    ]
    
    baseline = engine.calculate_baseline("tiktok", posts)
    
    assert baseline.medians["views_72h"] == 200.0
    assert baseline.medians["completion_rate"] == 0.5
    assert baseline.record_count == 3

def test_scoring_engine_score_post() -> None:
    engine = ScoringEngine()
    
    baseline = MagicMock()
    baseline.medians = {"views_72h": 100.0, "completion_rate": 0.5, "engagement_rate": 0.1, "save_rate": 0.02}
    baseline.mads = {"views_72h": 10.0, "completion_rate": 0.1, "engagement_rate": 0.01, "save_rate": 0.005}
    
    post = ScoredPost(
        content_id="test", posted_at=datetime.now(), platform="tiktok",
        raw_metrics={"views_72h": 200.0}, # 2x average
        derived_metrics=PostDerivedMetrics(completion_rate=0.5, engagement_rate=0.1, save_rate=0.02), # All others at median
        scores={}
    )
    
    scored = engine.score_post(post, baseline)
    
    # Views component: rel_perf = 2.0. Score = 2.0
    assert scored.scores["views_72h"].relative_performance == 2.0
    assert scored.scores["views_72h"].score == 2.0
    
    # Total score calculation (weights: Views 0.25, Comp 0.35, Eng 0.25, Save 0.15):
    # views: 2.0 * 0.25 = 0.5
    # comp: 1.0 * 0.35 = 0.35
    # eng: 1.0 * 0.25 = 0.25
    # save: 1.0 * 0.15 = 0.15
    # Total = 0.5 + 0.35 + 0.25 + 0.15 = 1.25
    assert pytest.approx(scored.total_score) == 1.25

def test_scoring_engine_score_post_normalization() -> None:
    engine = ScoringEngine()
    
    baseline = MagicMock()
    baseline.medians = {"views_72h": 100.0, "completion_rate": 0.5, "engagement_rate": 0.1, "save_rate": 0.02}
    baseline.mads = {"views_72h": 10.0, "completion_rate": 0.1, "engagement_rate": 0.01, "save_rate": 0.005}
    
    # Post with missing metrics
    post = ScoredPost(
        content_id="test", posted_at=datetime.now(), platform="tiktok",
        raw_metrics={"views_72h": 200.0}, # 2x average
        derived_metrics=PostDerivedMetrics(completion_rate=0.5), # Median
        # engagement_rate and save_rate are MISSING
        scores={}
    )
    
    scored = engine.score_post(post, baseline)
    
    # Weights used: Views 0.25, Comp 0.35 (Sum = 0.6)
    # weighted_sum: (2.0 * 0.25) + (1.0 * 0.35) = 0.5 + 0.35 = 0.85
    # Normalized: 0.85 / 0.6 = 1.4166...
    assert pytest.approx(scored.total_score) == 0.85 / 0.6
    assert len(scored.scores) == 2

from unittest.mock import MagicMock
