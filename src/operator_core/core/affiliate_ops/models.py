from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_AFFILIATE_ACTIONS = (
    "offer_match",
    "product_fit",
    "cta_direction",
    "monetization_fit",
    "recommendation_ready",
)


@dataclass(frozen=True)
class AffiliateOpResult:
    lane_name: str
    project_key: str
    action_type: str
    command_body: str
    title: str
    summary: str
    items: tuple[str, ...]

    def to_snapshot(self) -> dict[str, object]:
        return {
            "lane_name": self.lane_name,
            "project_key": self.project_key,
            "action_type": self.action_type,
            "command_body": self.command_body,
            "title": self.title,
            "summary": self.summary,
            "items": list(self.items),
        }
