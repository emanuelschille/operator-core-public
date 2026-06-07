from __future__ import annotations

import logging
import random
from collections import Counter, defaultdict
from typing import Sequence

from .models import ScoredPost

_log = logging.getLogger("operator_core.core.analysis_foundation.v2.calibration")

class TaxonomyAuditor:
    """Deterministic audit helpers for taxonomy data shape and confounds."""
    
    def get_sample_shape(self, posts: Sequence[ScoredPost]) -> dict[str, any]:
        """Summarize post counts per platform and category."""
        platform_counts = Counter(p.platform for p in posts)
        
        category_counts = defaultdict(lambda: defaultdict(Counter))
        for p in posts:
            for key in ["cta_typ_norm", "format_typ_norm", "serie_thema_norm", "hook_pattern"]:
                val = p.metadata.get(key) or "missing"
                category_counts[p.platform][key][val] += 1
                
        return {
            "total": len(posts),
            "by_platform": dict(platform_counts),
            "categories": {plat: {k: dict(v) for k, v in dims.items()} 
                          for plat, dims in category_counts.items()}
        }

    def get_cross_tab(self, posts: Sequence[ScoredPost], dim_a: str, dim_b: str) -> dict[str, dict[str, int]]:
        """Generate a cross-tabulation of two taxonomy dimensions."""
        table = defaultdict(Counter)
        for p in posts:
            val_a = p.metadata.get(dim_a) or "missing"
            val_b = p.metadata.get(dim_b) or "missing"
            table[val_a][val_b] += 1
            
        return {a: dict(b) for a, b in table.items()}

    def get_direct_question_vs_cta_check(self, posts: Sequence[ScoredPost]) -> dict[str, any]:
        """Check for overlap between direct_question hooks and community_question CTAs."""
        results = []
        for p in posts:
            hook_text = str(p.metadata.get("hook_kurz") or "").strip()
            # Check for explicit '?' OR the new pattern if already partially backfilled
            is_question_hook = "?" in hook_text or p.metadata.get("hook_pattern") == "direct_question"
            is_question_cta = p.metadata.get("cta_typ_norm") == "community_question"
            
            if is_question_hook and is_question_cta:
                results.append({
                    "id": p.content_id,
                    "platform": p.platform,
                    "hook": hook_text,
                    "cta": p.metadata.get("cta_typ")
                })
        
        return {
            "double_question_count": len(results),
            "examples": results[:10]
        }

    def generate_calibration_sample(self, posts: Sequence[ScoredPost], target_n: int = 30) -> list[dict[str, any]]:
        """
        Generate a stratified sample of posts for hook_pattern calibration.
        Prioritizes uniqueness of content (hook text / content_id).
        """
        if not posts:
            return []
            
        # 1. Group by content identity (content_id or cleaned hook text)
        unique_content: dict[str, ScoredPost] = {}
        for p in posts:
            # Identity key: preferred content_id, fallback cleaned hook
            hook_text = str(p.metadata.get("hook_kurz") or "").strip()
            identity_key = p.content_id or hook_text
            
            if not identity_key:
                continue
                
            # If we see the same content on multiple platforms, 
            # keep the one that might have more metadata or just the first one.
            if identity_key not in unique_content:
                unique_content[identity_key] = p

        unique_posts = list(unique_content.values())
        
        # 2. Group by platform then series for stratification of the unique set
        stratified_buckets = defaultdict(list)
        for p in unique_posts:
            bucket_key = (p.platform, p.metadata.get("serie_thema_norm", "other"))
            stratified_buckets[bucket_key].append(p)
            
        # 3. Fair round-robin selection from buckets
        all_buckets = sorted(stratified_buckets.keys())
        sample = []
        bucket_iters = {k: iter(sorted(stratified_buckets[k], key=lambda x: x.posted_at)) for k in all_buckets}
        active_buckets = list(all_buckets)
        
        while len(sample) < target_n and active_buckets:
            for k in list(active_buckets):
                try:
                    p = next(bucket_iters[k])
                    sample.append({
                        "content_id": p.content_id,
                        "platform": p.platform,
                        "posted_at": p.posted_at.isoformat(),
                        "hook_kurz": p.metadata.get("hook_kurz"),
                        "format_typ_norm": p.metadata.get("format_typ_norm"),
                        "serie_thema_norm": p.metadata.get("serie_thema_norm"),
                        "cta_typ_norm": p.metadata.get("cta_typ_norm"),
                        "human_label": "",
                        "llm_label": "",
                        "notes": ""
                    })
                    if len(sample) >= target_n:
                        break
                except StopIteration:
                    active_buckets.remove(k)
        
        return sample
