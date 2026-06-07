from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Sequence

from .models import (
    CategoryRollup,
    MetricScore,
    PlatformBaseline,
    PlatformRollup,
    PostDerivedMetrics,
    ScoredPost,
)

_log = logging.getLogger("operator_core.core.analysis_foundation.v2.scoring")


def calculate_mad(data: Sequence[float], center: float | None = None) -> float:
    """Calculate Median Absolute Deviation (MAD)."""
    if not data:
        return 0.0
    if center is None:
        center = median(data)
    deviations = [abs(x - center) for x in data]
    return median(deviations)


def compute_derived_metrics(
    raw_metrics: dict[str, float],
    platform: str,
) -> PostDerivedMetrics:
    """Calculate derived metrics for a single post."""
    views = raw_metrics.get("views_72h", 0.0)
    likes = raw_metrics.get("likes_72h", 0.0)
    saves = raw_metrics.get("saves_72h", 0.0)
    avg_watch = raw_metrics.get("avg_watch_sec_72h", 0.0)
    duration = raw_metrics.get("duration_sec", 0.0)
    followers = raw_metrics.get("followers_at_post_time")  # Might be None

    completion_rate = None
    if duration > 0:
        completion_rate = avg_watch / duration

    engagement_rate = None
    if views > 0:
        engagement_rate = (likes + saves) / views

    save_rate = None
    if views > 0:
        save_rate = saves / views

    view_efficiency = None
    if followers is not None and followers > 0:
        view_efficiency = views / followers

    return PostDerivedMetrics(
        completion_rate=completion_rate,
        engagement_rate=engagement_rate,
        save_rate=save_rate,
        view_efficiency=view_efficiency,
    )


class ScoringEngine:
    """Deterministic scoring engine for post-level analytics."""

    def calculate_baseline(
        self,
        platform: str,
        posts: Sequence[ScoredPost],
        window_days: int = 28,
    ) -> PlatformBaseline:
        """Calculate medians and MADs for all available metrics on a platform."""
        if not posts:
            return PlatformBaseline(
                platform=platform,
                window_days=window_days,
                record_count=0,
                calculation_time=datetime.now(timezone.utc),
                medians={},
                mads={},
            )

        # Metrics we track baselines for
        metric_keys = [
            "views_72h", "likes_72h", "saves_72h", "avg_watch_sec_72h",
            "completion_rate", "engagement_rate", "save_rate", "view_efficiency"
        ]
        
        medians = {}
        mads = {}

        for key in metric_keys:
            values = []
            for post in posts:
                # Check raw metrics first
                val = post.raw_metrics.get(key)
                if val is None:
                    # Check derived metrics
                    val = getattr(post.derived_metrics, key, None)
                
                if val is not None:
                    values.append(val)
            
            if values:
                m = median(values)
                medians[key] = m
                mads[key] = calculate_mad(values, center=m)

        return PlatformBaseline(
            platform=platform,
            window_days=window_days,
            record_count=len(posts),
            calculation_time=datetime.now(timezone.utc),
            medians=medians,
            mads=mads,
        )

    def score_post(
        self,
        post: ScoredPost,
        baseline: PlatformBaseline,
    ) -> ScoredPost:
        """
        Score a post relative to the platform baseline.
        
        Scale:
        - 1.0: Exactly at the platform median.
        - > 1.0: Above average performance (e.g., 2.0 = 2x median).
        - < 1.0: Below average performance.
        
        Handles missing data by re-normalizing weights over active metrics.
        """
        scores = {}
        weighted_sum = 0.0
        active_weight_sum = 0.0

        # Primary indicators with their relative importance
        scoring_weights = {
            "views_72h": 0.25,
            "completion_rate": 0.35,
            "engagement_rate": 0.25,
            "save_rate": 0.15,
        }

        for key, weight in scoring_weights.items():
            val = post.raw_metrics.get(key)
            if val is None:
                val = getattr(post.derived_metrics, key, None)
            
            if val is None:
                continue
                
            median_val = baseline.medians.get(key)
            mad_val = baseline.mads.get(key)

            # We need a non-zero median to compute relative performance
            if median_val is None or median_val <= 0:
                continue

            rel_perf = val / median_val
            z_score = None
            if mad_val and mad_val > 0:
                # Standard robust z-score calculation
                z_score = (val - median_val) / (mad_val * 1.4826)

            # Component score is raw relative performance (clamped at 10.0 for artifact safety)
            component_score = min(rel_perf, 10.0)

            scores[key] = MetricScore(
                metric_key=key,
                raw_value=val,
                baseline_median=median_val,
                baseline_mad=mad_val,
                z_score=z_score,
                relative_performance=rel_perf,
                score=component_score,
            )
            
            weighted_sum += component_score * weight
            active_weight_sum += weight

        # Re-normalize total score if we had active metrics
        total_score = 0.0
        if active_weight_sum > 0:
            total_score = weighted_sum / active_weight_sum

        return ScoredPost(
            content_id=post.content_id,
            posted_at=post.posted_at,
            platform=post.platform,
            raw_metrics=post.raw_metrics,
            derived_metrics=post.derived_metrics,
            scores=scores,
            total_score=total_score,
            metadata=post.metadata,
        )

    def calculate_platform_rollup(
        self,
        platform: str,
        scored_posts: Sequence[ScoredPost],
        min_n: int = 5,
        window_days: int = 28,
    ) -> PlatformRollup:
        """
        Aggregate scored posts into category rollups.
        
        Prioritizes normalized fields: cta_typ_norm, format_typ_norm, serie_thema_norm, hook_pattern.
        Falls back to raw fields (except hook_kurz) if normalized ones are absent.
        """
        # Mapping primary analysis dimensions to their possible field keys (normalized first)
        dimension_map = {
            "cta": ["cta_typ_norm", "cta_typ"],
            "format": ["format_typ_norm", "format_typ"],
            "serie": ["serie_thema_norm", "serie_thema"],
            "hook": ["hook_pattern"], # hook_kurz is raw-only, not for rollup
        }
        
        groups: dict[str, dict[str, list[ScoredPost]]] = {
            dim: defaultdict(list) for dim in dimension_map
        }
        
        for post in scored_posts:
            for dim, field_keys in dimension_map.items():
                # Take first non-empty value from prioritized field keys
                val = None
                for fk in field_keys:
                    candidate = post.metadata.get(fk) or post.raw_metrics.get(fk)
                    if candidate:
                        val = candidate
                        break
                
                if val:
                    norm_val = str(val).strip()
                    if norm_val:
                        groups[dim][norm_val].append(post)
        
        rollups: dict[str, tuple[CategoryRollup, ...]] = {}
        for dim in dimension_map:
            category_list = []
            for val, posts in groups[dim].items():
                post_count = len(posts)
                scores = [p.total_score for p in posts]
                med_score = median(scores) if scores else 0.0
                spread = calculate_mad(scores, center=med_score) if scores else None
                
                # Component medians for deeper insight
                comp_keys = ["views_72h", "completion_rate", "engagement_rate", "save_rate"]
                comp_medians = {}
                for ck in comp_keys:
                    vals = []
                    for p in posts:
                        v = p.raw_metrics.get(ck)
                        if v is None:
                            v = getattr(p.derived_metrics, ck, None)
                        if v is not None:
                            vals.append(v)
                    if vals:
                        comp_medians[ck] = median(vals)
                
                category_list.append(CategoryRollup(
                    taxonomy_key=dim,
                    value=val,
                    post_count=post_count,
                    median_total_score=med_score,
                    component_medians=comp_medians,
                    score_spread=spread,
                    is_eligible=post_count >= min_n
                ))
            
            # Sort by score descending (eligible first)
            rollups[dim] = tuple(sorted(
                category_list,
                key=lambda x: (x.is_eligible, x.median_total_score),
                reverse=True
            ))
            
        return PlatformRollup(
            platform=platform,
            window_days=window_days,
            calculation_time=datetime.now(timezone.utc),
            categories=rollups
        )
