from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from operator_core.integrations.airtable_service import AirtableService
    from operator_core.integrations.operational_knowledge_service import (
        OperationalKnowledgeLoader,
        PostingScheduleRule,
    )
    from operator_core.integrations.platform_signal_service import PlatformSignalLoader
    from operator_core.integrations.weekly_analysis_persistence import WeeklyAnalysisPersistenceService

_log = logging.getLogger("operator_core.proactive.posting_recommender")

_CONTENT_DRAFTS_TABLE = "Content Drafts"

_DEFAULT_GAP_DAYS = 3

_DEFAULT_POSTING_TIMES: dict[str, str] = {
    "tiktok": "20:00",
    "instagram_reel": "19:00",
    "facebook_reel": "18:00",
    "youtube_short": "20:30",
}

_PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram_reel": "Instagram",
    "facebook_reel": "Facebook",
    "youtube_short": "YouTube",
}

# Operational Knowledge key for global gap rule
_OK_GAP_KEY = "posting_gap_days"

# Operational Knowledge keys for per-platform posting times
_OK_TIME_KEYS: dict[str, str] = {
    "tiktok": "posting_time_tiktok",
    "instagram_reel": "posting_time_instagram",
    "facebook_reel": "posting_time_facebook",
    "youtube_short": "posting_time_youtube",
}

_ELIGIBLE_STAGES = frozenset({"drafted", "ready_to_produce", "produced"})

# Empty string covers drafts where readiness_check is not set (treated as not_required)
_ELIGIBLE_APPROVAL_STATES = frozenset({"approved", "not_required", ""})

_HOOK_PREVIEW_MAX = 45


@dataclass(frozen=True)
class PostingCandidate:
    record_id: str
    platform: str
    hook_preview: str
    content_stage: str
    content_format: str
    has_body: bool
    days_ready: int
    days_since_last_post: int  # -1 means this platform has never been posted to
    posting_time: str           # HH:MM string, e.g. "20:00"
    posting_condition: str = ""
    posting_note: str = ""


@dataclass(frozen=True)
class PostingRecommendation:
    candidate: PostingCandidate
    telegram_message: str
    candidate_count: int = 1


class PostingRecommender:
    """Deterministic posting recommendation engine (Phase 2.5).

    Reads eligible Content Drafts from Airtable, applies rule-based candidate
    selection, and returns a Telegram-ready recommendation or None.

    Rules applied in order:
      1. Eligibility filter: stage, hook, readiness_check
      2. Platform gap check: posted_at on Content Drafts, gap_days from Operational Knowledge
      3. Rank: produced > ready_to_produce > drafted, then older draft first (created_time ascending)
      4. Assign posting time from Operational Knowledge schedule

    Never raises — returns None on any error so callers degrade gracefully.
    """

    def __init__(
        self,
        airtable_svc: "AirtableService",
        ok_loader: "OperationalKnowledgeLoader",
        platform_signal_loader: "PlatformSignalLoader | None" = None,
        weekly_analysis_loader: "WeeklyAnalysisPersistenceService | None" = None,
    ) -> None:
        self._airtable_svc = airtable_svc
        self._ok_loader = ok_loader
        self._platform_loader = platform_signal_loader
        self._weekly_analysis_loader = weekly_analysis_loader

    def eligible_draft_count(self, *, project_key: str) -> int:
        """Return number of eligible drafts regardless of posting gap.

        Used by the daily plan to decide between 'draft instead' and 'skip'.
        Never raises — returns 0 on any error.
        """
        return len(self._load_eligible_drafts(project_key=project_key))

    def recommend(self, *, project_key: str) -> PostingRecommendation | None:
        """Return the best posting recommendation or None.

        Returns None when:
        - no eligible drafts exist
        - no platform is currently due
        - any Airtable error occurs
        """
        try:
            return self._recommend(project_key=project_key)
        except Exception as exc:
            _log.warning("posting_recommender: unexpected error | error=%s", exc)
            return None

    def _recommend(self, *, project_key: str) -> PostingRecommendation | None:
        gap_days, posting_schedules = self._load_config(project_key=project_key)

        eligible = self._load_eligible_drafts(project_key=project_key)
        if not eligible:
            _log.debug("posting_recommender: no eligible drafts found")
            return None

        last_posted = self._load_last_posted_per_platform(project_key=project_key)
        platform_contexts: dict[str, object] = {}
        if self._platform_loader is not None:
            try:
                platform_contexts = self._platform_loader.load_all(ok_project_key=project_key)
            except Exception as exc:
                _log.warning("posting_recommender: platform signal load failed | error=%s", exc)

        now_utc = datetime.now(tz=timezone.utc)
        due: list[tuple[object, str, int]] = []  # (record, platform, days_since)

        for record in eligible:
            platform = str(record.fields.get("platform") or "").strip().lower()
            if not platform:
                platform = "tiktok"
                _log.debug(
                    "posting_recommender: missing platform fallback applied | record_id=%s fallback=%s",
                    record.record_id,
                    platform,
                )
            schedule = posting_schedules.get(platform)
            if schedule is not None and not schedule.enabled:
                _log.debug(
                    "posting_recommender: platform disabled for weekday | platform=%s weekday=%s condition=%s",
                    platform,
                    schedule.weekday,
                    schedule.condition,
                )
                continue
            last_dt = last_posted.get(platform)
            if last_dt is None:
                # Never posted on this platform — always due
                days_since: int = -1
                is_due = True
            else:
                days_since = (now_utc - last_dt).days
                is_due = days_since >= gap_days
            if is_due:
                due.append((record, platform, days_since))

        if not due:
            _log.debug(
                "posting_recommender: no platform currently due | gap_days=%d", gap_days
            )
            return None

        due_platforms = {platform for _record, platform, _days in due}
        use_platform_tiebreak = (
            len(due_platforms) > 1
            and all(platform in platform_contexts for platform in due_platforms)
        )

        # Rank: produced first, then by created_time ascending (oldest first)
        def _rank_key(item: tuple) -> tuple:
            record, platform, _days = item
            stage = str(record.fields.get("stage") or "").strip()
            stage_order = {
                "produced": 0,
                "ready_to_produce": 1,
                "drafted": 2,
            }.get(stage, 99)
            post_count = 0
            if use_platform_tiebreak:
                ctx = platform_contexts.get(platform)
                post_count = getattr(ctx, "post_count", 0)
            return (stage_order, post_count, record.created_time or "")

        due.sort(key=_rank_key)
        best_record, best_platform, best_days_since = due[0]

        platform_evidence = ""
        best_stage = str(best_record.fields.get("stage") or "").strip()
        same_stage_due = [
            (record, platform, days_since)
            for record, platform, days_since in due
            if str(record.fields.get("stage") or "").strip() == best_stage
        ]
        if use_platform_tiebreak and len(same_stage_due) > 1 and best_platform in platform_contexts:
            best_post_count = getattr(platform_contexts[best_platform], "post_count", None)
            competing_counts = [
                getattr(platform_contexts[platform], "post_count", None)
                for _record, platform, _days in same_stage_due
                if platform != best_platform and platform in platform_contexts
            ]
            competing_counts = [count for count in competing_counts if count is not None]
            if (
                best_post_count is not None
                and competing_counts
                and any(best_post_count < other_count for other_count in competing_counts)
            ):
                winner_label = _PLATFORM_LABELS.get(best_platform, best_platform)
                runner_up_post_count = max(competing_counts)
                platform_evidence = (
                    f"• {winner_label} am wenigsten bespielt ({best_post_count} Posts)"
                    f" — andere Plattform hat {runner_up_post_count}"
                )

        # Build hook preview
        hook = str(best_record.fields.get("hook") or "").strip()
        if len(hook) > _HOOK_PREVIEW_MAX:
            hook_preview = hook[:_HOOK_PREVIEW_MAX].rstrip() + "…"
        elif hook:
            hook_preview = hook
        else:
            main_point = str(
                best_record.fields.get("main_point") or best_record.record_id
            ).strip()
            hook_preview = main_point[:_HOOK_PREVIEW_MAX]

        content_stage = str(best_record.fields.get("stage") or "").strip()
        content_format = str(best_record.fields.get("format") or "").strip()
        has_body = bool(str(best_record.fields.get("body") or "").strip())
        schedule = posting_schedules.get(best_platform)
        posting_time = schedule.time_local if schedule is not None else ""

        created_dt = _parse_iso(best_record.created_time or "")
        days_ready = (now_utc - created_dt).days if created_dt else 0

        candidate = PostingCandidate(
            record_id=best_record.record_id,
            platform=best_platform,
            hook_preview=hook_preview,
            content_stage=content_stage,
            content_format=content_format,
            has_body=has_body,
            days_ready=days_ready,
            days_since_last_post=best_days_since,
            posting_time=posting_time,
            posting_condition=schedule.condition if schedule is not None else "",
            posting_note=schedule.note if schedule is not None else "",
        )

        weekly = self._load_fresh_weekly_analysis(project_key=project_key)
        weekly_block = self._build_weekly_analysis_block(weekly)

        message = _format_recommendation_message(
            candidate,
            candidate_count=len(due),
            platform_evidence=platform_evidence,
            weekly_block=weekly_block,
        )
        _log.debug(
            "posting_recommender: recommendation built | record_id=%s platform=%s",
            candidate.record_id,
            candidate.platform,
        )
        return PostingRecommendation(
            candidate=candidate,
            telegram_message=message,
            candidate_count=len(due),
        )

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def _load_config(self, *, project_key: str) -> tuple[int, dict[str, "PostingScheduleRule"]]:
        """Read gap_days and posting schedule from Operational Knowledge.

        Falls back to module-level defaults on any error or missing row.
        Never raises.
        """
        ok_ctx = self._ok_loader.load_active(project_key=project_key)

        gap_days = _DEFAULT_GAP_DAYS
        for row in ok_ctx.rows:
            if row.key == _OK_GAP_KEY:
                try:
                    gap_days = int(row.value.strip().split()[0])
                except (ValueError, AttributeError, IndexError):
                    _log.warning(
                        "posting_recommender: invalid posting_gap_days | value=%r", row.value
                    )
                break

        weekday = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%A").lower()
        posting_schedules: dict[str, PostingScheduleRule] = {}
        for platform, ok_key in _OK_TIME_KEYS.items():
            posting_schedules[platform] = ok_ctx.resolve_posting_schedule(
                platform=platform,
                weekday=weekday,
                fallback_key=ok_key,
                default_time=_DEFAULT_POSTING_TIMES.get(platform, ""),
            )

        return gap_days, posting_schedules

    # ------------------------------------------------------------------
    # Airtable reads
    # ------------------------------------------------------------------

    def _load_eligible_drafts(self, *, project_key: str) -> list:
        """Return Content Drafts that pass the eligibility filter. Empty list on error."""
        try:
            result = self._airtable_svc.find_records(
                _CONTENT_DRAFTS_TABLE,
                filter_formula='OR({stage} = "drafted", {stage} = "ready_to_produce", {stage} = "produced")',
                project_key=project_key,
                max_records=50,
                fields=(
                    "stage",
                    "format",
                    "platform",
                    "hook",
                    "body",
                    "readiness_check",
                    "posted_at",
                    "main_point",
                ),
            )
        except Exception as exc:
            _log.warning("posting_recommender: eligible draft load failed | error=%s", exc)
            return []

        eligible = []
        for record in result.records:
            if not str(record.fields.get("hook") or "").strip():
                continue
            approval = str(record.fields.get("readiness_check") or "").strip().lower()
            if approval not in _ELIGIBLE_APPROVAL_STATES:
                continue
            eligible.append(record)

        _log.debug("posting_recommender: %d eligible drafts | project=%s", len(eligible), project_key)
        return eligible

    def _load_last_posted_per_platform(self, *, project_key: str) -> dict[str, datetime]:
        """Return most recent posted_at datetime per platform. Empty dict on error.

        Reads Content Drafts with stage='posted' and groups by platform using the
        posted_at field. If posted_at is absent (field not yet in Airtable), the
        platform is treated as never posted — callers receive an empty dict and apply
        the "never posted = platform is due" fallback.
        """
        try:
            result = self._airtable_svc.find_records(
                _CONTENT_DRAFTS_TABLE,
                filter_formula='{stage} = "posted"',
                project_key=project_key,
                max_records=100,
                fields=("platform", "posted_at"),
            )
        except Exception as exc:
            _log.warning(
                "posting_recommender: posted draft load failed | error=%s", exc
            )
            return {}

        per_platform: dict[str, datetime] = {}
        for record in result.records:
            platform = str(record.fields.get("platform") or "").strip().lower()
            if not platform:
                continue
            posted_at_str = str(record.fields.get("posted_at") or "").strip()
            if not posted_at_str:
                continue
            dt = _parse_iso(posted_at_str)
            if dt is None:
                continue
            if platform not in per_platform or dt > per_platform[platform]:
                per_platform[platform] = dt

        return per_platform

    # ------------------------------------------------------------------
    # Weekly Analysis
    # ------------------------------------------------------------------

    def _load_fresh_weekly_analysis(self, *, project_key: str) -> WeeklyAnalysisArtifact | None:
        """Load latest weekly analysis with 10-day staleness guard."""
        if self._weekly_analysis_loader is None:
            return None
        try:
            weekly = self._weekly_analysis_loader.load_latest(project_key=project_key)
            if weekly is None:
                return None

            gen_dt = _parse_iso(weekly.generated_at)
            if gen_dt is None:
                return None
            
            if gen_dt.tzinfo is None:
                gen_dt = gen_dt.replace(tzinfo=timezone.utc)

            age = datetime.now(timezone.utc) - gen_dt
            if age > timedelta(days=10):
                _log.info(
                    "ignoring stale weekly analysis in recommender | project=%s analysis_id=%s age_days=%s",
                    project_key,
                    weekly.analysis_id,
                    age.days,
                )
                return None
            return weekly
        except Exception as exc:
            _log.warning("recommender: weekly analysis load failed | error=%s", exc)
            return None

    def _build_weekly_analysis_block(self, artifact: WeeklyAnalysisArtifact | None) -> str:
        if not artifact:
            return ""
        
        lines = ["Strategische Leitplanken:"]
        if artifact.key_winners:
            lines.append("• Bevorzugt: " + ", ".join(artifact.key_winners[:2]))
        if artifact.weak_patterns:
            lines.append("• Vermeiden: " + ", ".join(artifact.weak_patterns[:2]))
        if artifact.recommended_content_directions:
            lines.append("• Richtung: " + ", ".join(artifact.recommended_content_directions[:2]))
        
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_recommendation_message(
    c: PostingCandidate,
    candidate_count: int = 1,
    platform_evidence: str = "",
    weekly_block: str = "",
) -> str:
    platform_label = _PLATFORM_LABELS.get(c.platform, c.platform)

    lines = [
        "📋 Posting-Empfehlung",
        "",
        f'🎬 \u201e{c.hook_preview}\u201c',
        f"📱 {platform_label} → heute {c.posting_time} Uhr",
        "",
        "Warum:",
    ]

    ready_word = "Tag" if c.days_ready == 1 else "Tagen"
    lines.append(f"• Entwurf seit {c.days_ready} {ready_word} bereit ({c.content_stage})")

    if c.days_since_last_post == -1:
        lines.append(f"• {platform_label} noch nie gepostet")
    else:
        since_word = "Tag" if c.days_since_last_post == 1 else "Tagen"
        lines.append(
            f"• {platform_label} zuletzt vor {c.days_since_last_post} {since_word} gepostet"
        )

    if c.content_format:
        lines.append(f"• Format: {c.content_format}")
    if c.has_body:
        lines.append("• Hook und Body vorhanden")
    else:
        lines.append("• Hook vorhanden, Body optional/leer")
    if c.posting_note:
        lines.append(f"• Hinweis: {c.posting_note}")
    elif c.posting_condition:
        lines.append(f"• Bedingung: {c.posting_condition}")
    if candidate_count > 1:
        lines.append(f"• Ausgewählt aus {candidate_count} passenden Entwürfen (ältester zuerst)")
    if platform_evidence:
        lines.append(platform_evidence)
    
    if weekly_block:
        lines.append("")
        lines.append(weekly_block)

    lines.append("")
    lines.append(f"/confirm {c.record_id} · /skip")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO 8601 datetime string. Returns None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
