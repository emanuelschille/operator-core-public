from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from operator_core.core.analysis_foundation.models import (
    AnalysisFoundationResult,
    AnalysisSnapshot,
    EvidencePack,
    ModelExecutionMeta,
    WriterBrief,
)
from operator_core.core.knowledge_ops.doc_reader import extract_section, first_sentences, list_items, trim
from operator_core.integrations.analytics_service import AnalyticsContext
from operator_core.integrations.operational_knowledge_service import (
    OperationalKnowledgeContext,
    PostingScheduleRule,
)
from operator_core.integrations.platform_signal_service import PlatformContext

if TYPE_CHECKING:
    from operator_core.integrations.analytics_service import AnalyticsLoader
    from operator_core.integrations.openai_service import OpenAIService
    from operator_core.integrations.operational_knowledge_service import OperationalKnowledgeLoader
    from operator_core.integrations.platform_signal_service import PlatformSignalLoader
    from operator_core.integrations.weekly_analysis_persistence import WeeklyAnalysisPersistenceService
    from operator_core.projects.docs import ProjectDocsLoader


SUPPORTED_ANALYSIS_ACTIONS = ("analysis_snapshot",)

_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("tiktok", "TikTok"),
    ("instagram_reel", "Instagram"),
    ("facebook_reel", "Facebook"),
    ("youtube_short", "YouTube"),
)

_OK_TIME_KEYS: dict[str, str] = {
    "tiktok": "posting_time_tiktok",
    "instagram_reel": "posting_time_instagram",
    "facebook_reel": "posting_time_facebook",
    "youtube_short": "posting_time_youtube",
}

_DEFAULT_POSTING_TIMES: dict[str, str] = {
    "tiktok": "20:00",
    "instagram_reel": "19:00",
    "facebook_reel": "18:00",
    "youtube_short": "20:30",
}


class AnalysisFoundationService:
    lane_name = "analysis_foundation"

    def __init__(
        self,
        *,
        docs_loader: "ProjectDocsLoader | None" = None,
        analytics_loader: "AnalyticsLoader | None" = None,
        operational_knowledge_loader: "OperationalKnowledgeLoader | None" = None,
        platform_signal_loader: "PlatformSignalLoader | None" = None,
        weekly_analysis_loader: "WeeklyAnalysisPersistenceService | None" = None,
        openai_service: "OpenAIService | None" = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._docs_loader = docs_loader
        self._analytics_loader = analytics_loader
        self._ok_loader = operational_knowledge_loader
        self._platform_signal_loader = platform_signal_loader
        self._weekly_analysis_loader = weekly_analysis_loader
        self._openai_service = openai_service
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def supports(self, action_type: str) -> bool:
        return action_type.strip().lower() in SUPPORTED_ANALYSIS_ACTIONS

    def handle(
        self,
        *,
        project_key: str,
        action_type: str,
        command_body: str,
    ) -> AnalysisFoundationResult:
        normalized_action = action_type.strip().lower()
        if not self.supports(normalized_action):
            raise ValueError(f"unsupported analysis action: {action_type}")

        now_utc = self._now_provider().astimezone(timezone.utc)
        now_berlin = now_utc.astimezone(ZoneInfo("Europe/Berlin"))
        weekday = now_berlin.strftime("%A").lower()

        ok_ctx = (
            self._ok_loader.load_active(project_key=project_key)
            if self._ok_loader is not None
            else OperationalKnowledgeContext(rows=())
        )
        analytics_ctx = (
            self._analytics_loader.load_recent()
            if self._analytics_loader is not None
            else AnalyticsContext(hook_examples=(), dominant_cta="", gap="")
        )
        platform_contexts = (
            self._platform_signal_loader.load_all(ok_project_key=project_key)
            if self._platform_signal_loader is not None
            else {}
        )
        try:
            weekly_analysis = (
                self._weekly_analysis_loader.load_latest(project_key=project_key)
                if self._weekly_analysis_loader is not None
                else None
            )
            # Staleness guard: ignore if older than 10 days
            if weekly_analysis is not None:
                try:
                    # fromisoformat handles Z in 3.11+, but we normalize for safety
                    gen_dt = datetime.fromisoformat(weekly_analysis.generated_at.replace("Z", "+00:00"))
                    if gen_dt.tzinfo is None:
                        gen_dt = gen_dt.replace(tzinfo=timezone.utc)

                    age = now_utc - gen_dt
                    if age > timedelta(days=10):
                        import logging
                        logging.getLogger("operator_core.core.analysis_foundation").info(
                            "ignoring stale weekly analysis | project=%s analysis_id=%s age_days=%s",
                            project_key,
                            weekly_analysis.analysis_id,
                            age.days,
                        )
                        weekly_analysis = None
                except (ValueError, TypeError):
                    # If date is unparseable, treat as unavailable for safety
                    weekly_analysis = None
        except Exception as exc:
            import logging
            logging.getLogger("operator_core.core.analysis_foundation").warning(
                "weekly analysis load failed | project=%s error=%s",
                project_key,
                exc,
            )
            weekly_analysis = None
        rule_context = self._build_rule_context(project_key=project_key)
        provider_name, model_name = self._resolve_default_model_slot()

        snapshots = tuple(
            self._build_platform_snapshot(
                project_key=project_key,
                created_at=now_utc.isoformat(),
                weekday=weekday,
                platform_key=platform_key,
                platform_label=platform_label,
                platform_context=platform_contexts.get(platform_key),
                ok_ctx=ok_ctx,
                rule_context=rule_context,
            )
            for platform_key, platform_label in _PLATFORMS
        )
        cross_snapshot = self._build_cross_platform_snapshot(
            project_key=project_key,
            created_at=now_utc.isoformat(),
            weekday=weekday,
            analytics_ctx=analytics_ctx,
            ok_ctx=ok_ctx,
            snapshots=snapshots,
            rule_context=rule_context,
        )
        all_snapshots = snapshots + (cross_snapshot,)

        execution_meta = ModelExecutionMeta(
            provider_name=provider_name,
            model_name=model_name,
            task_role="analysis_control",
            status="prepared",
            notes=("Slice 1 prepares analysis foundation only.",),
        )
        writer_brief = self._build_writer_brief(
            project_key=project_key,
            created_at=now_utc.isoformat(),
            snapshots=all_snapshots,
            rule_context=rule_context,
            provider_name=provider_name,
            model_name=model_name,
        )
        evidence_pack = self._build_evidence_pack(
            project_key=project_key,
            created_at=now_utc.isoformat(),
            snapshots=all_snapshots,
            analytics_ctx=analytics_ctx,
        )

        return AnalysisFoundationResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type=normalized_action,
            title="Analysis foundation snapshot",
            summary="Analysis snapshots, writer brief, and evidence pack prepared.",
            analysis_snapshots=all_snapshots,
            writer_brief=writer_brief,
            evidence_pack=evidence_pack,
            execution_meta=execution_meta,
            weekly_analysis=weekly_analysis,
        )

    def _build_rule_context(self, *, project_key: str) -> dict[str, object]:
        if self._docs_loader is None:
            return {
                "phase_summary": "",
                "audience_summary": "",
                "direction_items": (),
                "priority_items": (),
                "pillar_items": (),
                "avoid_summary": "",
            }

        state_doc = self._docs_loader.load(project_key, "project_state")
        content_doc = self._docs_loader.load(project_key, "content_rules")

        phase_summary = trim(first_sentences(extract_section(state_doc.content, "Current Phase") or "", 1))
        audience_summary = trim(first_sentences(extract_section(state_doc.content, "Active Audience Assumption") or "", 1))
        direction_items = tuple(list_items(extract_section(state_doc.content, "Active Content Direction") or "", max_items=3))
        priority_items = tuple(list_items(extract_section(state_doc.content, "Current Operational Priorities") or "", max_items=3))
        pillar_items = tuple(list_items(extract_section(content_doc.content, "Active Content Pillars") or "", max_items=3))
        avoid_summary = trim(first_sentences(extract_section(content_doc.content, "What Content Should Avoid Right Now") or "", 1))

        return {
            "phase_summary": phase_summary,
            "audience_summary": audience_summary,
            "direction_items": direction_items,
            "priority_items": priority_items,
            "pillar_items": pillar_items,
            "avoid_summary": avoid_summary,
        }

    def _build_platform_snapshot(
        self,
        *,
        project_key: str,
        created_at: str,
        weekday: str,
        platform_key: str,
        platform_label: str,
        platform_context: PlatformContext | None,
        ok_ctx: OperationalKnowledgeContext,
        rule_context: dict[str, object],
    ) -> AnalysisSnapshot:
        schedule = ok_ctx.resolve_posting_schedule(
            platform=platform_key,
            weekday=weekday,
            fallback_key=_OK_TIME_KEYS.get(platform_key, ""),
            default_time=_DEFAULT_POSTING_TIMES.get(platform_key, ""),
        )
        analytics_lines = list(_build_platform_analytics_lines(platform_context))
        rule_lines = list(_build_rule_lines(rule_context))
        source_refs = [
            "docs:project-state#Current Phase",
            "docs:project-state#Current Operational Priorities",
            "docs:content-rules#Active Content Pillars",
            f"ok:{schedule.source_key}" if schedule.source_key else "ok:default_posting_time",
            f"analytics:platform_signal:{platform_key}",
        ]
        summary = (
            f"{platform_label} snapshot for {weekday}: "
            f"{'skip' if not schedule.enabled else schedule.time_local or 'no time'}"
        )
        return AnalysisSnapshot(
            snapshot_id=f"as_{uuid4().hex}",
            project_key=project_key,
            scope="platform",
            created_at=created_at,
            title=f"{platform_label} analysis snapshot",
            summary=summary,
            platform_key=platform_key,
            analytics_summary_lines=tuple(analytics_lines),
            rule_summary_lines=tuple(rule_lines),
            source_refs=tuple(source_refs),
            posting_context=_schedule_to_snapshot(schedule),
        )

    def _build_cross_platform_snapshot(
        self,
        *,
        project_key: str,
        created_at: str,
        weekday: str,
        analytics_ctx: AnalyticsContext,
        ok_ctx: OperationalKnowledgeContext,
        snapshots: tuple[AnalysisSnapshot, ...],
        rule_context: dict[str, object],
    ) -> AnalysisSnapshot:
        analytics_lines = list(_build_cross_platform_analytics_lines(analytics_ctx))
        analytics_lines.extend(
            _build_platform_rollup_lines(ok_ctx=ok_ctx, weekday=weekday)
        )
        source_refs = [
            "analytics:global_recent",
            "docs:project-state#Active Audience Assumption",
            "docs:content-rules#What Content Should Avoid Right Now",
        ]
        source_refs.extend(snapshot.source_refs[3] for snapshot in snapshots if len(snapshot.source_refs) >= 4)
        return AnalysisSnapshot(
            snapshot_id=f"as_{uuid4().hex}",
            project_key=project_key,
            scope="cross_platform",
            created_at=created_at,
            title="Cross-platform analysis snapshot",
            summary=f"Cross-platform analysis snapshot for {weekday}.",
            analytics_summary_lines=tuple(analytics_lines),
            rule_summary_lines=tuple(_build_rule_lines(rule_context)),
            source_refs=tuple(dict.fromkeys(source_refs)),
            posting_context={"weekday": weekday, "timezone": "Europe/Berlin"},
        )

    def _build_writer_brief(
        self,
        *,
        project_key: str,
        created_at: str,
        snapshots: tuple[AnalysisSnapshot, ...],
        rule_context: dict[str, object],
        provider_name: str,
        model_name: str,
    ) -> WriterBrief:
        audience = str(rule_context.get("audience_summary") or "Deutschsprachige Alltagsaudience.").strip()
        constraints = [
            "Use analysis snapshots as the primary grounding layer.",
            "Keep output traceable to explicit evidence.",
        ]
        avoid_summary = str(rule_context.get("avoid_summary") or "").strip()
        if avoid_summary:
            constraints.append(avoid_summary)
        return WriterBrief(
            brief_id=f"wb_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            objective="Turn explicit analysis into a writer-ready brief for platform-specific short-form output.",
            audience=audience,
            constraints=tuple(constraints),
            source_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in snapshots),
            provider_name=provider_name,
            model_name=model_name,
            task_role="writer",
            execution_meta=ModelExecutionMeta(
                provider_name=provider_name,
                model_name=model_name,
                task_role="writer",
                status="prepared",
                notes=("Writer slot prepared only; no writer execution in slice 1.",),
            ),
        )

    def _build_evidence_pack(
        self,
        *,
        project_key: str,
        created_at: str,
        snapshots: tuple[AnalysisSnapshot, ...],
        analytics_ctx: AnalyticsContext,
    ) -> EvidencePack:
        evidence_lines: list[str] = []
        if analytics_ctx.hook_examples:
            evidence_lines.append("Hook examples: " + " | ".join(analytics_ctx.hook_examples))
        if analytics_ctx.dominant_cta:
            evidence_lines.append(f"Dominant CTA: {analytics_ctx.dominant_cta}")
        if analytics_ctx.gap:
            evidence_lines.append(f"Gap signal: {analytics_ctx.gap}")
        for snapshot in snapshots:
            if snapshot.scope != "platform":
                continue
            posting = snapshot.posting_context
            platform_label = next((label for key, label in _PLATFORMS if key == snapshot.platform_key), snapshot.platform_key)
            if not posting.get("enabled", True):
                evidence_lines.append(f"{platform_label}: skip ({posting.get('condition') or 'disabled'})")
            else:
                condition = str(posting.get("condition") or "").strip()
                note = str(posting.get("note") or "").strip()
                suffix = f" [{condition}]" if condition else ""
                if note:
                    suffix += f" {note}"
                evidence_lines.append(f"{platform_label}: {posting.get('time_local') or '—'}{suffix}".strip())
        source_refs: list[str] = []
        for snapshot in snapshots:
            source_refs.extend(snapshot.source_refs)
        return EvidencePack(
            evidence_pack_id=f"ep_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            summary="Evidence pack linking analysis snapshots to concrete source references.",
            snapshot_ids=tuple(snapshot.snapshot_id for snapshot in snapshots),
            source_refs=tuple(dict.fromkeys(source_refs)),
            evidence_lines=tuple(evidence_lines),
        )

    def _resolve_default_model_slot(self) -> tuple[str, str]:
        if self._openai_service is not None:
            model_name = self._openai_service.bootstrap_context.settings.openai.model.strip()
            if model_name:
                return "openai", model_name
        return "openai", "default-control-analysis"


def _build_rule_lines(rule_context: dict[str, object]) -> tuple[str, ...]:
    lines: list[str] = []
    phase_summary = str(rule_context.get("phase_summary") or "").strip()
    audience_summary = str(rule_context.get("audience_summary") or "").strip()
    if phase_summary:
        lines.append(f"Phase: {phase_summary}")
    if audience_summary:
        lines.append(f"Audience: {audience_summary}")
    direction_items = tuple(str(item) for item in (rule_context.get("direction_items") or ()))
    if direction_items:
        lines.append("Direction: " + ", ".join(direction_items))
    pillar_items = tuple(str(item) for item in (rule_context.get("pillar_items") or ()))
    if pillar_items:
        lines.append("Pillars: " + ", ".join(pillar_items))
    priority_items = tuple(str(item) for item in (rule_context.get("priority_items") or ()))
    if priority_items:
        lines.append("Priorities: " + ", ".join(priority_items))
    avoid_summary = str(rule_context.get("avoid_summary") or "").strip()
    if avoid_summary:
        lines.append(f"Avoid: {avoid_summary}")
    return tuple(lines)


def _build_platform_analytics_lines(platform_context: PlatformContext | None) -> tuple[str, ...]:
    if platform_context is None:
        return ("No platform analytics context available.",)

    lines = [f"Post count: {platform_context.post_count}"]
    if platform_context.dominant_cta:
        lines.append(f"Dominant CTA: {platform_context.dominant_cta}")
    if platform_context.gap:
        lines.append(f"Gap signal: {platform_context.gap}")
    if platform_context.dominant_format:
        lines.append(f"Dominant format: {platform_context.dominant_format}")
    if platform_context.hook_examples:
        lines.append("Hook examples: " + " | ".join(platform_context.hook_examples))
    lines.extend(platform_context.numeric_summary_lines[:2])
    return tuple(lines)


def _build_cross_platform_analytics_lines(analytics_ctx: AnalyticsContext) -> tuple[str, ...]:
    lines: list[str] = []
    if analytics_ctx.hook_examples:
        lines.append("Cross-platform hooks: " + " | ".join(analytics_ctx.hook_examples))
    if analytics_ctx.dominant_cta:
        lines.append(f"Cross-platform dominant CTA: {analytics_ctx.dominant_cta}")
    if analytics_ctx.gap:
        lines.append(f"Cross-platform gap: {analytics_ctx.gap}")
    if analytics_ctx.cta_count:
        lines.append(f"CTA sample count: {analytics_ctx.cta_count}")
    return tuple(lines or ("No cross-platform analytics context available.",))


def _build_platform_rollup_lines(
    *,
    ok_ctx: OperationalKnowledgeContext,
    weekday: str,
) -> tuple[str, ...]:
    lines: list[str] = []
    for platform_key, platform_label in _PLATFORMS:
        schedule = ok_ctx.resolve_posting_schedule(
            platform=platform_key,
            weekday=weekday,
            fallback_key=_OK_TIME_KEYS.get(platform_key, ""),
            default_time=_DEFAULT_POSTING_TIMES.get(platform_key, ""),
        )
        if not schedule.enabled:
            lines.append(f"{platform_label}: skip ({schedule.condition or 'disabled'})")
            continue
        suffix = f" [{schedule.condition}]" if schedule.condition else ""
        lines.append(f"{platform_label}: {schedule.time_local or '—'}{suffix}".strip())
    return tuple(lines)


def _schedule_to_snapshot(schedule: PostingScheduleRule) -> dict[str, object]:
    return {
        "platform": schedule.platform,
        "weekday": schedule.weekday,
        "timezone": schedule.timezone,
        "enabled": schedule.enabled,
        "time_local": schedule.time_local,
        "condition": schedule.condition,
        "note": schedule.note,
        "source_key": schedule.source_key,
        "source": schedule.source,
    }
