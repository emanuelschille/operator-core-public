from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .models import ScoredPost, PlatformBaseline, PlatformRollup
from .scoring import compute_derived_metrics, ScoringEngine

if TYPE_CHECKING:
    from operator_core.integrations.airtable_service import AirtableService

_log = logging.getLogger("operator_core.core.analysis_foundation.v2.service")

_ANALYTICS_PROJECT_KEY = "analytics"
_PLATFORM_TABLE_KEYS: dict[str, str] = {
    "tiktok": "TikTok Content",
    "instagram_reel": "Instagram Content",
    "facebook_reel": "Facebook Content",
    "youtube_short": "YouTube Content",
}


class AnalyticsV2Service:
    """Service for V2 analytics scoring and baseline management."""

    def __init__(
        self,
        airtable_svc: "AirtableService",
        scoring_engine: ScoringEngine | None = None,
    ) -> None:
        self._airtable_svc = airtable_svc
        self._scoring_engine = scoring_engine or ScoringEngine()

    def get_scored_posts(
        self,
        platform: str,
        days: int = 28,
    ) -> list[ScoredPost]:
        """Load and score recent posts for a platform."""
        table_name = _PLATFORM_TABLE_KEYS.get(platform)
        if not table_name:
            _log.warning("analytics_v2: unsupported platform | platform=%s", platform)
            return []

        try:
            record_list = self._airtable_svc.list_records(
                table_name,
                project_key=_ANALYTICS_PROJECT_KEY,
            )
        except Exception as exc:
            _log.error("analytics_v2: airtable read failed | platform=%s error=%s", platform, exc)
            return []

        posts = []
        for record in record_list.records:
            scored_post = self._parse_to_scored_post(record, platform)
            if scored_post:
                posts.append(scored_post)

        # Filter by age
        threshold = datetime.now(timezone.utc) - timedelta(days=days)
        recent_posts = [p for p in posts if p.posted_at >= threshold]
        
        if not recent_posts:
            return []

        # Calculate baseline from these recent posts
        baseline = self._scoring_engine.calculate_baseline(platform, recent_posts, window_days=days)
        
        # Score them
        final_posts = []
        for p in recent_posts:
            final_posts.append(self._scoring_engine.score_post(p, baseline))
            
        return sorted(final_posts, key=lambda x: x.posted_at, reverse=True)

    def get_platform_rollup(
        self,
        platform: str,
        days: int = 28,
        min_n: int = 5,
    ) -> PlatformRollup | None:
        """Load, score and rollup categories for a platform."""
        scored_posts = self.get_scored_posts(platform=platform, days=days)
        if not scored_posts:
            return None
            
        return self._scoring_engine.calculate_platform_rollup(
            platform=platform,
            scored_posts=scored_posts,
            min_n=min_n,
            window_days=days
        )

    def _parse_to_scored_post(self, record: any, platform: str) -> ScoredPost | None:
        f = record.fields
        
        content_id = str(f.get("content_id", record.record_id))
        posted_at_str = f.get("posted_at_local")
        if not posted_at_str:
            return None
            
        try:
            posted_at = datetime.fromisoformat(posted_at_str.replace("Z", "+00:00"))
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

        raw_metrics = {
            "views_72h": float(f.get("views_72h") or 0.0),
            "likes_72h": float(f.get("likes_72h") or f.get("reactions_72h") or 0.0),
            "saves_72h": float(f.get("saves_72h") or 0.0),
            "avg_watch_sec_72h": float(f.get("avg_watch_sec_72h") or 0.0),
            "duration_sec": float(f.get("duration_sec") or 0.0),
            # "followers_at_post_time": None # Not available yet
        }
        
        # Extract metadata (taxonomy)
        metadata = {
            "hook_kurz": f.get("hook_kurz"),
            "cta_typ": f.get("cta_typ"),
            "serie_thema": f.get("serie_thema"),
            "format_typ": f.get("format_typ"),
            "cta_typ_norm": f.get("cta_typ_norm"),
            "format_typ_norm": f.get("format_typ_norm"),
            "serie_thema_norm": f.get("serie_thema_norm"),
            "hook_pattern": f.get("hook_pattern"),
        }

        derived = compute_derived_metrics(raw_metrics, platform)

        return ScoredPost(
            content_id=content_id,
            posted_at=posted_at,
            platform=platform,
            raw_metrics=raw_metrics,
            derived_metrics=derived,
            scores={}, # Will be populated by score_post
            metadata=metadata,
        )
