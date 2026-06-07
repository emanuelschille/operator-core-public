from __future__ import annotations

from datetime import datetime
import pytest

from operator_core.core.analysis_foundation.v2.models import ScoredPost, PostDerivedMetrics
from operator_core.core.analysis_foundation.v2.calibration import TaxonomyAuditor

def _make_post(plat: str, fmt: str, serie: str) -> ScoredPost:
    return ScoredPost(
        content_id="test", posted_at=datetime.now(), platform=plat,
        raw_metrics={}, derived_metrics=PostDerivedMetrics(), scores={},
        metadata={"format_typ_norm": fmt, "serie_thema_norm": serie}
    )

def test_taxonomy_auditor_sample_shape() -> None:
    auditor = TaxonomyAuditor()
    posts = [
        _make_post("tiktok", "talking_head", "routinen"),
        _make_post("tiktok", "talking_head", "gedanken"),
        _make_post("instagram", "b_roll", "routinen"),
    ]
    
    shape = auditor.get_sample_shape(posts)
    assert shape["total"] == 3
    assert shape["by_platform"]["tiktok"] == 2
    assert shape["categories"]["tiktok"]["format_typ_norm"]["talking_head"] == 2

def test_taxonomy_auditor_cross_tab() -> None:
    auditor = TaxonomyAuditor()
    posts = [
        _make_post("tiktok", "talking_head", "routinen"),
        _make_post("tiktok", "talking_head", "routinen"),
        _make_post("tiktok", "talking_head", "gedanken"),
        _make_post("tiktok", "b_roll", "gedanken"),
    ]
    
    ct = auditor.get_cross_tab(posts, "format_typ_norm", "serie_thema_norm")
    assert ct["talking_head"]["routinen"] == 2
    assert ct["talking_head"]["gedanken"] == 1
    assert ct["b_roll"]["gedanken"] == 1

def test_taxonomy_auditor_double_question_check() -> None:
    auditor = TaxonomyAuditor()
    posts = [
        # Double question
        ScoredPost(
            content_id="dq1", posted_at=datetime.now(), platform="tiktok",
            raw_metrics={}, derived_metrics=PostDerivedMetrics(), scores={},
            metadata={"hook_kurz": "Warum?", "cta_typ_norm": "community_question", "cta_typ": "Was meinst du?"}
        ),
        # Single question (hook only)
        ScoredPost(
            content_id="sq1", posted_at=datetime.now(), platform="tiktok",
            raw_metrics={}, derived_metrics=PostDerivedMetrics(), scores={},
            metadata={"hook_kurz": "Wie?", "cta_typ_norm": "none"}
        ),
    ]
    
    check = auditor.get_direct_question_vs_cta_check(posts)
    assert check["double_question_count"] == 1
    assert check["examples"][0]["id"] == "dq1"

def test_taxonomy_auditor_stratified_calibration_sample() -> None:
    auditor = TaxonomyAuditor()
    posts = []
    # Create 10 posts for TikTok and 10 for Instagram
    for i in range(10):
        posts.append(ScoredPost(
            content_id=f"t{i}", posted_at=datetime.now(), platform="tiktok",
            raw_metrics={}, derived_metrics=PostDerivedMetrics(), scores={},
            metadata={"serie_thema_norm": "alltag"}
        ))
        posts.append(ScoredPost(
            content_id=f"i{i}", posted_at=datetime.now(), platform="instagram",
            raw_metrics={}, derived_metrics=PostDerivedMetrics(), scores={},
            metadata={"serie_thema_norm": "alltag"}
        ))
    
    # Request 4 posts (should be 2 from each platform if possible)
    sample = auditor.generate_calibration_sample(posts, target_n=4)
    
    assert len(sample) == 4
    plats = [s["platform"] for s in sample]
    assert plats.count("tiktok") == 2
    assert plats.count("instagram") == 2

def test_taxonomy_auditor_unique_content_sampling() -> None:
    auditor = TaxonomyAuditor()
    posts = [
        # Same content on 3 platforms
        _make_post("tiktok", "fmt", "s1"),
        _make_post("instagram", "fmt", "s1"),
        _make_post("facebook", "fmt", "s1"),
        # Different content
        _make_post("tiktok", "fmt", "s2"),
    ]
    # Set IDs/hooks to simulate same content
    for i in range(3):
        # We need to reach into the internal structure for this mock setup
        # but ScoredPost is a frozen dataclass, so we just recreate
        posts[i] = dataclasses.replace(posts[i], content_id="C01", metadata={"hook_kurz": "Hook 1"})
    posts[3] = dataclasses.replace(posts[3], content_id="C02", metadata={"hook_kurz": "Hook 2"})
    
    sample = auditor.generate_calibration_sample(posts, target_n=10)
    
    assert len(sample) == 2
    ids = [s["content_id"] for s in sample]
    assert "C01" in ids
    assert "C02" in ids
    assert len(set(ids)) == 2

import dataclasses
