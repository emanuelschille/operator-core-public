from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Final

_log = logging.getLogger("operator_core.core.routing.writer_routing")


class WriterFlowType(Enum):
    """
    Categorization of content generation flows based on their requirements.
    """
    NUANCED = "nuanced"      # Requires higher tone sensitivity, emotional resonance, or complex logic.
    STRUCTURED = "structured"  # Short, punchy, or highly constrained lists where brevity is key.
    GENERIC = "generic"      # Standard tasks with balanced requirements.


@dataclass(frozen=True)
class WriterRoutingPolicy:
    """
    Specification for writer routing decisions.
    """
    policy_id: str
    flow_type: WriterFlowType
    default_writer: str      # Recommended stable/high-quality model (e.g., gpt-5.4)
    fast_writer: str | None   # Recommended cost-effective/fast model (e.g., gpt-5.4-mini)
    rationale: str


# --- Routing Specification (Internal Decision Layer) ---

# Based on 2026-04-15 Benchmark Evidence:
# - gpt-5.4 won decisively in HOOK and CTA (Nuanced).
# - gpt-5.4-mini won in SERIE, CAPTION, and TITLE (Structured/Lists).

_POLICIES: Final[dict[str, WriterRoutingPolicy]] = {
    "hook": WriterRoutingPolicy(
        policy_id="hook",
        flow_type=WriterFlowType.NUANCED,
        default_writer="gpt-5.4",
        fast_writer="gpt-5.4-mini",
        rationale="Nuanced hooks benefit from gpt-5.4's higher relational depth and subtle German phrasing."
    ),
    "cta": WriterRoutingPolicy(
        policy_id="cta",
        flow_type=WriterFlowType.NUANCED,
        default_writer="gpt-5.4",
        fast_writer="gpt-5.4-mini",
        rationale="CTAs require precision and a 'trust-only' feel where gpt-5.4 outscored mini in Batch 2."
    ),
    "idea": WriterRoutingPolicy(
        policy_id="idea",
        flow_type=WriterFlowType.NUANCED,
        default_writer="gpt-5.4",
        fast_writer="gpt-5.4-mini",
        rationale="Content ideas involve multi-step reasoning and project-alignment where safety is preferred."
    ),
    "mutation": WriterRoutingPolicy(
        policy_id="mutation",
        flow_type=WriterFlowType.NUANCED,
        default_writer="gpt-5.4",
        fast_writer="gpt-5.4-mini",
        rationale="Rewrites/Follow-ups depend on context preservation and subtle instruction following."
    ),
    "serie": WriterRoutingPolicy(
        policy_id="serie",
        flow_type=WriterFlowType.STRUCTURED,
        default_writer="gpt-5.4",
        fast_writer="gpt-5.4-mini",
        rationale="Series are structured lists where gpt-5.4-mini proved highly effective and punchy."
    ),
    "title": WriterRoutingPolicy(
        policy_id="title",
        flow_type=WriterFlowType.STRUCTURED,
        default_writer="gpt-5.4",
        fast_writer="gpt-5.4-mini",
        rationale="Titles benefit from the 'compressed' variant of mini, yielding shorter, clearer results."
    ),
    "caption": WriterRoutingPolicy(
        policy_id="caption",
        flow_type=WriterFlowType.STRUCTURED,
        default_writer="gpt-5.4",
        fast_writer="gpt-5.4-mini",
        rationale="Captions are a hybrid case, but evidence shows mini is strong and usable for everydayengel."
    ),
    "generic": WriterRoutingPolicy(
        policy_id="generic",
        flow_type=WriterFlowType.GENERIC,
        default_writer="gpt-5.4",
        fast_writer=None,
        rationale="Fallback for unclassified flows."
    ),
}


class WriterRoutingService:
    """
    Helper to look up recommended writer routes based on current policy.
    Does NOT wire into live generation yet.
    """

    def get_policy(self, flow_name: str) -> WriterRoutingPolicy:
        """
        Return the policy for a given flow/command name.
        """
        normalized = flow_name.strip().lower().lstrip("/")
        
        # Mapping common aliases
        mapping = {
            "draft": "idea",
            "vollauto": "idea",
            "follow_up": "mutation",
            "rewrite": "mutation",
            "mutation": "mutation",
        }
        
        policy_key = mapping.get(normalized, normalized)
        policy = _POLICIES.get(policy_key, _POLICIES["generic"])
        
        _log.debug("Resolved routing policy for flow '%s' -> '%s'", flow_name, policy.policy_id)
        return policy

    def get_recommended_model(self, flow_name: str, prefer_fast: bool = False) -> str:
        """
        Return the recommended model name according to current evidence-based policy.
        """
        policy = self.get_policy(flow_name)
        if prefer_fast and policy.fast_writer:
            return policy.fast_writer
        return policy.default_writer
