from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MetricScore:
    """A score for a single metric relative to a baseline."""
    metric_key: str
    raw_value: float
    baseline_median: float
    baseline_mad: float | None = None
    z_score: float | None = None  # robust z-score (value - median) / MAD
    relative_performance: float | None = None  # value / median
    score: float = 0.0  # Normalized 0.0 to 1.0 or similar


@dataclass(frozen=True)
class PostDerivedMetrics:
    """Computed metrics for a single post."""
    completion_rate: float | None = None
    engagement_rate: float | None = None
    save_rate: float | None = None
    view_efficiency: float | None = None


@dataclass(frozen=True)
class ScoredPost:
    """A single post with its raw metrics, derived metrics, and calculated scores."""
    content_id: str
    posted_at: datetime
    platform: str
    raw_metrics: dict[str, float]
    derived_metrics: PostDerivedMetrics
    scores: dict[str, MetricScore]
    total_score: float = 0.0
    
    # Contextual metadata (taxonomy)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlatformBaseline:
    """The calculated baseline for a platform over a specific period."""
    platform: str
    window_days: int
    record_count: int
    calculation_time: datetime
    medians: dict[str, float]
    mads: dict[str, float]


@dataclass(frozen=True)
class CategoryRollup:
    """Aggregated scores for a specific category (e.g., a specific CTA type)."""
    taxonomy_key: str  # cta_typ, hook_kurz, etc.
    value: str
    post_count: int
    median_total_score: float
    component_medians: dict[str, float]
    score_spread: float | None = None  # Robust spread (IQR or MAD)
    is_eligible: bool = False  # True if post_count >= min_n


@dataclass(frozen=True)
class PlatformRollup:
    """Complete rollup for all taxonomy categories on a single platform."""
    platform: str
    window_days: int
    calculation_time: datetime
    categories: dict[str, tuple[CategoryRollup, ...]]  # keyed by taxonomy_key
