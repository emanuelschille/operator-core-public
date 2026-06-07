from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from operator_core.core.analysis_foundation.service import AnalysisFoundationService
from operator_core.integrations.analytics_service import AnalyticsContext
from operator_core.integrations.operational_knowledge_service import (
    OperationalKnowledgeContext,
    OperationalKnowledgeRow,
)
from operator_core.integrations.platform_signal_service import PlatformContext
from operator_core.projects.docs import ProjectDoc, ProjectDocsLoader


class _DocsLoader(ProjectDocsLoader):
    def __init__(self, project_state: str, content_rules: str) -> None:
        self._docs = {
            "project_state": ProjectDoc(
                project_key="everydayengel",
                doc_type="project_state",
                content=project_state,
                path=__import__("pathlib").Path("project-state.md"),
            ),
            "content_rules": ProjectDoc(
                project_key="everydayengel",
                doc_type="content_rules",
                content=content_rules,
                path=__import__("pathlib").Path("content-rules.md"),
            ),
        }

    def load(self, project_key: str, doc_type: str):  # type: ignore[override]
        return self._docs[doc_type]


def test_analysis_foundation_service_builds_platform_and_cross_platform_snapshots() -> None:
    docs_loader = _DocsLoader(
        project_state="""
## 4. Current Phase
live operational. Decision quality is the current bottleneck.

## 6. Active Audience Assumption
German-speaking women 23 to 38.

## 7. Active Content Direction
- everyday situations
- small routines

## 11. Current Operational Priorities
- decision quality
- evidence visibility
""",
        content_rules="""
## 4. Active Content Pillars
- everyday life
- honest product experience

## 21. What Content Should Avoid Right Now
Avoid generic influencer content.
""",
    )
    analytics_loader = MagicMock()
    analytics_loader.load_recent.return_value = AnalyticsContext(
        hook_examples=("Hook A", "Hook B"),
        dominant_cta="community_question",
        gap="noch keine Serie",
        cta_count=4,
    )
    ok_loader = MagicMock()
    ok_loader.load_active.return_value = OperationalKnowledgeContext(
        rows=(
            OperationalKnowledgeRow(
                key="posting_schedule_tiktok_sunday",
                label="TikTok Sunday",
                value='{"platform":"tiktok","weekday":"sunday","timezone":"Europe/Berlin","enabled":true,"time_local":"20:02","condition":"","note":""}',
                category="posting",
                status="active",
            ),
            OperationalKnowledgeRow(
                key="posting_schedule_facebook_reel_sunday",
                label="Facebook Sunday",
                value='{"platform":"facebook_reel","weekday":"sunday","timezone":"Europe/Berlin","enabled":true,"time_local":"18:02","condition":"","note":""}',
                category="posting",
                status="active",
            ),
            OperationalKnowledgeRow(
                key="posting_schedule_instagram_reel_sunday",
                label="Instagram Sunday",
                value='{"platform":"instagram_reel","weekday":"sunday","timezone":"Europe/Berlin","enabled":true,"time_local":"19:02","condition":"","note":""}',
                category="posting",
                status="active",
            ),
            OperationalKnowledgeRow(
                key="posting_schedule_youtube_short_sunday",
                label="YouTube Sunday",
                value='{"platform":"youtube_short","weekday":"sunday","timezone":"Europe/Berlin","enabled":true,"time_local":"20:31","condition":"","note":""}',
                category="posting",
                status="active",
            ),
        )
    )
    platform_loader = MagicMock()
    platform_loader.load_all.return_value = {
        "tiktok": PlatformContext(
            platform_key="tiktok",
            table_id="tblTikTok",
            post_count=8,
            dominant_cta="community_question",
            gap="noch keine Serie",
            hook_examples=("Hook A",),
            dominant_format="short_video",
            format_examples=("short_video",),
            numeric_summary_lines=("Views: Ø 1200 | best 2400 | Felder: views",),
            numeric_fields_used=("views",),
        )
    }

    service = AnalysisFoundationService(
        docs_loader=docs_loader,
        analytics_loader=analytics_loader,
        operational_knowledge_loader=ok_loader,
        platform_signal_loader=platform_loader,
        now_provider=lambda: datetime(2026, 4, 19, 8, 0, tzinfo=timezone.utc),
    )

    result = service.handle(
        project_key="everydayengel",
        action_type="analysis_snapshot",
        command_body="",
    )

    assert len(result.analysis_snapshots) == 5
    platform_snapshots = [s for s in result.analysis_snapshots if s.scope == "platform"]
    cross_snapshots = [s for s in result.analysis_snapshots if s.scope == "cross_platform"]
    assert len(platform_snapshots) == 4
    assert len(cross_snapshots) == 1
    assert any(s.platform_key == "tiktok" and s.posting_context["time_local"] == "20:02" for s in platform_snapshots)
    assert result.writer_brief.provider_name == "openai"
    assert result.evidence_pack.snapshot_ids == tuple(s.snapshot_id for s in result.analysis_snapshots)
    assert any("TikTok: 20:02" in line for line in result.evidence_pack.evidence_lines)


def test_analysis_foundation_service_preserves_skip_schedule_in_evidence() -> None:
    docs_loader = _DocsLoader(
        project_state="## 4. Current Phase\nlive operational.\n",
        content_rules="## 21. What Content Should Avoid Right Now\nAvoid generic content.\n",
    )
    analytics_loader = MagicMock()
    analytics_loader.load_recent.return_value = AnalyticsContext(hook_examples=(), dominant_cta="", gap="")
    ok_loader = MagicMock()
    ok_loader.load_active.return_value = OperationalKnowledgeContext(
        rows=(
            OperationalKnowledgeRow(
                key="posting_schedule_facebook_reel_sunday",
                label="Facebook Sunday",
                value='{"platform":"facebook_reel","weekday":"sunday","timezone":"Europe/Berlin","enabled":false,"time_local":"","condition":"skip","note":"auslassen"}',
                category="posting",
                status="active",
            ),
        )
    )
    platform_loader = MagicMock()
    platform_loader.load_all.return_value = {}

    service = AnalysisFoundationService(
        docs_loader=docs_loader,
        analytics_loader=analytics_loader,
        operational_knowledge_loader=ok_loader,
        platform_signal_loader=platform_loader,
        now_provider=lambda: datetime(2026, 4, 19, 8, 0, tzinfo=timezone.utc),
    )

    result = service.handle(
        project_key="everydayengel",
        action_type="analysis_snapshot",
        command_body="",
    )

    assert any("Facebook: skip (skip)" == line for line in result.evidence_pack.evidence_lines)
