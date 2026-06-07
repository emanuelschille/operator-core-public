from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from operator_core.config import Settings
    from operator_core.integrations.airtable_service import AirtableService
    from operator_core.integrations.telegram_service import TelegramService
    from operator_core.integrations.platform_signal_service import PlatformSignalLoader
    from operator_core.proactive.pending_store import ProactivePendingStore
    from operator_core.proactive.posting_recommender import PostingRecommender

from operator_core.integrations.analytics_service import AnalyticsLoader

_log = logging.getLogger("operator_core.proactive.checker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POSTING_GAP_DAYS = 2
_RECENT_DRAFT_DAYS = 3
_COOLDOWN_HOURS = 24
_CHECK_HOUR = 9  # 09:00 local time
_CHECK_TZ = ZoneInfo("Europe/Berlin")

_ANALYTICS_TABLE = "tblUJH1sZOIVmNkAn"
_CONTENT_IDEAS_TABLE = "Content Ideas"
_CONTENT_DRAFTS_TABLE = "Content Drafts"

_STALE_DRAFT_DAYS = 14

# Phase 2.7 warning thresholds
_W1_GAP_DAYS = 7
_W2_IDLE_DAYS = 10
_W3_STALE_DAYS = 28
_W1_SUPPRESSION_HOURS = 48
_W2_SUPPRESSION_HOURS = 48
_W3_SUPPRESSION_HOURS = 72

_PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram_reel": "Instagram",
    "facebook_reel": "Facebook",
    "youtube_short": "YouTube",
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TriggerResult:
    fired: bool
    message: str = ""
    # approval-needed fields — populated only by StaleDraftTrigger
    record_id: str = ""
    display_text: str = ""
    days_stale: int = 0
    stale_count: int = 0
    # Phase 2.7: extra context for warning-layer decisions
    days_since: int = 0        # populated by PostingGapTrigger (days since last post)
    idle_draft_days: int = 0   # populated by BacklogReadinessTrigger (days since last draft)


# ---------------------------------------------------------------------------
# Suppression store
# ---------------------------------------------------------------------------

class SuppressionStore:
    """In-memory cooldown store. Resets on service restart — acceptable for MVP."""

    def __init__(self, cooldown_hours: int = _COOLDOWN_HOURS) -> None:
        self._cooldown = timedelta(hours=cooldown_hours)
        self._last_sent: dict[str, datetime] = {}

    def is_suppressed(self, key: str) -> bool:
        last = self._last_sent.get(key)
        if last is None:
            return False
        return (datetime.now(tz=timezone.utc) - last) < self._cooldown

    def record_sent(self, key: str) -> None:
        self._last_sent[key] = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

class PostingGapTrigger:
    """Fires when the most recent analytics record is older than the threshold.

    Returns fired=False (silently) if analytics data is unavailable or has no
    records — never raises.
    """

    def __init__(self, gap_days: int = _POSTING_GAP_DAYS) -> None:
        self.gap_days = gap_days

    def evaluate(self, airtable_svc: "AirtableService") -> TriggerResult:
        try:
            record_list = airtable_svc.list_records(
                _ANALYTICS_TABLE,
                project_key="analytics",
                fields=("hook_kurz",),
                max_records=20,
            )
        except Exception as exc:
            _log.warning("posting_gap_trigger: airtable read failed | error=%s", exc)
            return TriggerResult(fired=False)

        if not record_list.records:
            _log.debug("posting_gap_trigger: no analytics records — skipping")
            return TriggerResult(fired=False)

        latest_dt = _most_recent_created_time(record_list.records)
        if latest_dt is None:
            _log.debug("posting_gap_trigger: no parseable created_time — skipping")
            return TriggerResult(fired=False)

        now_utc = datetime.now(tz=timezone.utc)
        days_since = (now_utc - latest_dt).days

        _log.debug("posting_gap_trigger: days_since=%d threshold=%d", days_since, self.gap_days)

        if days_since > self.gap_days:
            return TriggerResult(
                fired=True,
                message=f"Du hast seit {days_since} {'Tag' if days_since == 1 else 'Tagen'} nichts gepostet.",
                days_since=days_since,
            )
        return TriggerResult(fired=False)


class BacklogReadinessTrigger:
    """Fires when there are ready ideas and no recent draft activity.

    Returns fired=False if data is unavailable — never raises.
    """

    def __init__(self, recent_draft_days: int = _RECENT_DRAFT_DAYS) -> None:
        self.recent_draft_days = recent_draft_days

    def evaluate(self, airtable_svc: "AirtableService") -> TriggerResult:
        try:
            ready_list = airtable_svc.find_records(
                _CONTENT_IDEAS_TABLE,
                filter_formula='{stage} = "ready_to_produce"',
                max_records=10,
                fields=("stage",),
            )
        except Exception as exc:
            _log.warning("backlog_readiness_trigger: ideas read failed | error=%s", exc)
            return TriggerResult(fired=False)

        ready_count = len(ready_list.records)
        if ready_count == 0:
            return TriggerResult(fired=False)

        try:
            draft_list = airtable_svc.list_records(
                _CONTENT_DRAFTS_TABLE,
                max_records=10,
                fields=("stage",),
            )
        except Exception as exc:
            _log.warning("backlog_readiness_trigger: drafts read failed | error=%s", exc)
            return TriggerResult(fired=False)

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self.recent_draft_days)
        recent_drafts = [
            r for r in draft_list.records
            if _parse_created_time(r.created_time or "") is not None
            and (_parse_created_time(r.created_time or "") or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
        ]

        if recent_drafts:
            _log.debug(
                "backlog_readiness_trigger: %d recent drafts — skipping", len(recent_drafts)
            )
            return TriggerResult(fired=False)

        # Compute idle_draft_days: days since the most recent draft was created
        now_utc = datetime.now(tz=timezone.utc)
        most_recent_draft_dt: datetime | None = None
        for r in draft_list.records:
            dt = _parse_created_time(r.created_time or "")
            if dt is not None and (most_recent_draft_dt is None or dt > most_recent_draft_dt):
                most_recent_draft_dt = dt
        idle_days = (now_utc - most_recent_draft_dt).days if most_recent_draft_dt else 999

        idea_word = "Idee" if ready_count == 1 else "Ideen"
        return TriggerResult(
            fired=True,
            message=f"Du hast {ready_count} fertige {idea_word} im Backlog.",
            idle_draft_days=idle_days,
        )


class AnalyticsGapTrigger:
    """Fires when recent posts share the same CTA type and the sample is large enough.

    If a PlatformSignalLoader is configured, evaluate per-platform first and name
    the specific platform in the message. Falls back to the original global
    AnalyticsLoader path when platform contexts are unavailable.
    """

    def __init__(
        self,
        min_cta_records: int = 3,
        platform_signal_loader: "PlatformSignalLoader | None" = None,
        ok_project_key: str = "everydayengel",
    ) -> None:
        self.min_cta_records = min_cta_records
        self._platform_loader = platform_signal_loader
        self._ok_project_key = ok_project_key

    def evaluate(self, airtable_svc: "AirtableService") -> TriggerResult:
        if self._platform_loader is not None:
            try:
                platform_contexts = self._platform_loader.load_all(
                    ok_project_key=self._ok_project_key
                )
            except Exception as exc:
                _log.warning("analytics_gap_trigger: platform load failed | error=%s", exc)
                platform_contexts = {}

            if platform_contexts:
                return self._evaluate_platform_aware(platform_contexts)

        return self._evaluate_global(airtable_svc)

    def _evaluate_platform_aware(self, platform_contexts: dict[str, object]) -> TriggerResult:
        for platform_key, ctx in platform_contexts.items():
            post_count = getattr(ctx, "post_count", 0)
            gap = str(getattr(ctx, "gap", "") or "")
            if post_count < self.min_cta_records:
                _log.debug(
                    "analytics_gap_trigger: insufficient sample | platform=%s count=%d min=%d",
                    platform_key,
                    post_count,
                    self.min_cta_records,
                )
                continue
            if not gap:
                continue

            platform_label = _PLATFORM_LABELS.get(platform_key, platform_key)
            message = (
                f"Deine {platform_label}-Posts haben alle denselben CTA-Typ "
                f"(aus {post_count} Posts). "
                f"{gap[0].upper()}{gap[1:]}. "
                f"Schick /idea für eine neue Variante."
            )
            return TriggerResult(fired=True, message=message)

        return TriggerResult(fired=False)

    def _evaluate_global(self, airtable_svc: "AirtableService") -> TriggerResult:
        try:
            ctx = AnalyticsLoader(airtable_svc).load_recent()
        except Exception as exc:
            _log.warning("analytics_gap_trigger: load failed | error=%s", exc)
            return TriggerResult(fired=False)

        if ctx.cta_count < self.min_cta_records:
            _log.debug(
                "analytics_gap_trigger: insufficient sample | cta_count=%d min=%d",
                ctx.cta_count,
                self.min_cta_records,
            )
            return TriggerResult(fired=False)

        if not ctx.gap:
            return TriggerResult(fired=False)

        message = (
            f"Deine letzten Posts haben alle denselben CTA-Typ (aus {ctx.cta_count} Posts). "
            f"{ctx.gap[0].upper()}{ctx.gap[1:]}. "
            f"Schick /idea für eine neue Variante."
        )
        return TriggerResult(fired=True, message=message)


class StaleDraftTrigger:
    """Fires when a Content Draft has been in 'drafted' or 'ready_to_produce' stage
    for longer than `stale_days` without being published or archived.

    Returns exactly one TriggerResult per evaluation — the oldest stale draft.
    Returns fired=False on any error — never raises.
    """

    def __init__(self, stale_days: int = _STALE_DRAFT_DAYS) -> None:
        self.stale_days = stale_days

    def evaluate(self, airtable_svc: "AirtableService") -> TriggerResult:
        try:
            draft_list = airtable_svc.find_records(
                _CONTENT_DRAFTS_TABLE,
                filter_formula='OR({stage} = "drafted", {stage} = "ready_to_produce")',
                max_records=50,
                fields=("stage", "main_point"),
            )
        except Exception as exc:
            _log.warning("stale_draft_trigger: airtable read failed | error=%s", exc)
            return TriggerResult(fired=False)

        if not draft_list.records:
            return TriggerResult(fired=False)

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self.stale_days)
        stale_candidates: list[tuple[datetime, object]] = []

        for record in draft_list.records:
            dt = _parse_created_time(record.created_time or "")
            if dt is not None and dt < cutoff:
                stale_candidates.append((dt, record))

        if not stale_candidates:
            return TriggerResult(fired=False)

        # Pick the oldest stale draft
        stale_candidates.sort(key=lambda x: x[0])
        oldest_dt, oldest_record = stale_candidates[0]
        days_stale = (datetime.now(tz=timezone.utc) - oldest_dt).days
        stale_count = len(stale_candidates)

        record_id: str = getattr(oldest_record, "record_id", "") or ""
        title_fields = getattr(oldest_record, "fields", {}) or {}
        title = str(title_fields.get("main_point", "") or "").strip()
        display_text = title if title else record_id

        context = f" — ältester von {stale_count} Entwürfen" if stale_count > 1 else ""
        message = (
            f"Entwurf \"{display_text}\" liegt seit {days_stale} Tagen unverändert{context}. "
            f"Als veraltet markieren? Antworte auf diese Nachricht mit /confirm oder /reject."
        )

        return TriggerResult(
            fired=True,
            message=message,
            record_id=record_id,
            display_text=display_text,
            days_stale=days_stale,
            stale_count=stale_count,
        )


# ---------------------------------------------------------------------------
# Proactive Checker Service
# ---------------------------------------------------------------------------

class ProactiveCheckerService:
    """Runs a daily proactive check and sends a Telegram nudge when conditions are met.

    Thread safety:
    - Uses only stateless AirtableService and TelegramService calls (no shared mutable state).
    - Full try/except around the loop body — exceptions never propagate to the runtime.
    - Respects stop_event via Event.wait() instead of time.sleep().
    """

    def __init__(
        self,
        *,
        airtable_svc: "AirtableService",
        telegram_svc: "TelegramService",
        settings: "Settings",
        posting_gap_trigger: PostingGapTrigger | None = None,
        backlog_readiness_trigger: BacklogReadinessTrigger | None = None,
        suppression_store: SuppressionStore | None = None,
        analytics_gap_trigger: AnalyticsGapTrigger | None = None,
        analytics_gap_suppression: SuppressionStore | None = None,
        stale_draft_trigger: StaleDraftTrigger | None = None,
        stale_draft_suppression: SuppressionStore | None = None,
        pending_store: "ProactivePendingStore | None" = None,
        posting_recommender: "PostingRecommender | None" = None,
        # Phase 2.7 warning-layer suppression stores
        w1_suppression: SuppressionStore | None = None,
        w2_suppression: SuppressionStore | None = None,
        w3_suppression: SuppressionStore | None = None,
    ) -> None:
        self._airtable_svc = airtable_svc
        self._telegram_svc = telegram_svc
        self._settings = settings
        self._posting_gap = posting_gap_trigger or PostingGapTrigger()
        self._backlog_readiness = backlog_readiness_trigger or BacklogReadinessTrigger()
        self._suppression = suppression_store or SuppressionStore()
        self._analytics_gap = analytics_gap_trigger or AnalyticsGapTrigger()
        self._analytics_gap_suppression = analytics_gap_suppression or SuppressionStore(cooldown_hours=72)
        self._stale_draft = stale_draft_trigger or StaleDraftTrigger()
        self._stale_draft_suppression = stale_draft_suppression or SuppressionStore(cooldown_hours=168)
        self._pending_store = pending_store
        self._recommender = posting_recommender
        self._w1_suppression = w1_suppression or SuppressionStore(cooldown_hours=_W1_SUPPRESSION_HOURS)
        self._w2_suppression = w2_suppression or SuppressionStore(cooldown_hours=_W2_SUPPRESSION_HOURS)
        self._w3_suppression = w3_suppression or SuppressionStore(cooldown_hours=_W3_SUPPRESSION_HOURS)

    def run_until_stopped(self, stop_event: Event) -> None:
        _log.info("proactive checker starting")
        while not stop_event.is_set():
            try:
                wait_seconds = _seconds_until_target(hour=_CHECK_HOUR, tz=_CHECK_TZ)
                _log.debug("proactive checker sleeping for %.0f seconds", wait_seconds)
                stop_event.wait(timeout=wait_seconds)

                if stop_event.is_set():
                    break

                self._run_check()
            except Exception as exc:
                _log.error("proactive checker loop error | error=%s", exc)
                # Brief back-off before retrying to avoid tight error loops
                stop_event.wait(timeout=60)

        _log.info("proactive checker stopped")

    def _run_check(self) -> None:
        _NUDGE_KEY = "daily_nudge"
        _ANALYTICS_KEY = "analytics_gap"

        if self._suppression.is_suppressed(_NUDGE_KEY):
            _log.debug("proactive checker suppressed — skipping")
            return

        gap_result = self._posting_gap.evaluate(self._airtable_svc)
        backlog_result = self._backlog_readiness.evaluate(self._airtable_svc)

        # Analytics gap has its own 72h suppression — evaluated independently
        analytics_result = TriggerResult(fired=False)
        if not self._analytics_gap_suppression.is_suppressed(_ANALYTICS_KEY):
            analytics_result = self._analytics_gap.evaluate(self._airtable_svc)
        else:
            _log.debug("proactive checker: analytics_gap suppressed — skipping analytics trigger")

        # Evaluate stale draft once — used for both W3 bundle logic and standalone proposal
        stale_result = self._stale_draft.evaluate(self._airtable_svc)

        if not gap_result.fired and not backlog_result.fired and not analytics_result.fired:
            _log.debug("proactive checker: no triggers fired")
        elif self._pending_store is not None and self._pending_store.has_active():
            _log.debug("proactive checker: active proposal pending — bundle suppressed")
        else:
            message_parts = ["Guten Morgen 👋"]

            # Phase 2.5/2.6: recommendation wins over all gap messages.
            # Phase 2.7: W1 (⚠️) replaces ℹ️ when gap >= 7 days and not W1-suppressed.
            recommendation = None
            w1_used = False
            if gap_result.fired:
                if self._recommender is not None:
                    recommendation = self._recommender.recommend(
                        project_key=self._settings.app.active_project
                    )
                if recommendation is not None:
                    message_parts.append(recommendation.telegram_message)
                elif (
                    gap_result.days_since >= _W1_GAP_DAYS
                    and not self._w1_suppression.is_suppressed("extended_posting_gap")
                ):
                    w1_used = True
                    message_parts.append(
                        "⚠️ " + gap_result.message + f" · Über Normalschwelle ({_W1_GAP_DAYS} Tage)"
                    )
                else:
                    message_parts.append("ℹ️ " + gap_result.message)

            # Phase 2.7: W2 (⚠️) replaces 💡 when backlog idle >= 10 days and not W2-suppressed.
            w2_used = False
            if backlog_result.fired:
                if (
                    backlog_result.idle_draft_days >= _W2_IDLE_DAYS
                    and not self._w2_suppression.is_suppressed("idle_backlog")
                ):
                    w2_used = True
                    message_parts.append(
                        "⚠️ " + backlog_result.message
                        + f" · Kein Entwurf seit {backlog_result.idle_draft_days} Tagen"
                    )
                else:
                    message_parts.append("💡 " + backlog_result.message)

            if analytics_result.fired:
                message_parts.append("ℹ️ " + analytics_result.message)

            # Phase 2.7: W3 — persistent stale draft in bundle only.
            # Fires when: days_stale >= 28, standalone suppression IS active (proposal already sent),
            # and W3 itself is not suppressed.
            # Skipped when standalone is eligible to fire (suppression expired).
            w3_used = False
            if (
                stale_result.fired
                and stale_result.record_id
                and stale_result.days_stale >= _W3_STALE_DAYS
                and self._stale_draft_suppression.is_suppressed(
                    f"stale_proposed_{stale_result.record_id}"
                )
                and not self._w3_suppression.is_suppressed(
                    f"persistent_stale_{stale_result.record_id}"
                )
            ):
                w3_used = True
                message_parts.append(
                    f"⚠️ \"{stale_result.display_text}\" liegt seit {stale_result.days_stale} Tagen"
                    f" unerledigt. · Anfrage bereits gestellt"
                )

            # /draft closing: only when gap fired without a recommendation, or when backlog fired.
            # A specific recommendation already carries its own /confirm CTA.
            if (gap_result.fired and recommendation is None) or backlog_result.fired:
                message_parts.append("→ /draft")

            text = "\n".join(message_parts)
            sent_id = self._send_to_allowed_users(text)

            if sent_id is not None:
                self._suppression.record_sent(_NUDGE_KEY)
                if analytics_result.fired:
                    self._analytics_gap_suppression.record_sent(_ANALYTICS_KEY)
                if w1_used:
                    self._w1_suppression.record_sent("extended_posting_gap")
                if w2_used:
                    self._w2_suppression.record_sent("idle_backlog")
                if w3_used:
                    self._w3_suppression.record_sent(
                        f"persistent_stale_{stale_result.record_id}"
                    )
                _log.info(
                    "proactive checker nudge sent | gap_fired=%s backlog_fired=%s"
                    " analytics_gap_fired=%s w1=%s w2=%s w3=%s",
                    gap_result.fired,
                    backlog_result.fired,
                    analytics_result.fired,
                    w1_used,
                    w2_used,
                    w3_used,
                )

        # Stale draft trigger — standalone proposal, uses cached stale_result
        self._run_stale_draft_check(stale_result)

    def _run_stale_draft_check(self, stale_result: TriggerResult | None = None) -> None:
        """Evaluate stale draft trigger and send a standalone approval-needed proposal."""
        # Phase 2.8: Disabled live stale-draft decision flow as it is currently too noisy/confusing.
        return

        if self._pending_store is None:
            return

        # Phase 2.6: one active decision at a time — skip if a proposal is already pending.
        if self._pending_store.has_active():
            _log.debug("stale_draft_trigger: active proposal already pending — skipping")
            return

        if stale_result is None:
            stale_result = self._stale_draft.evaluate(self._airtable_svc)
        if not stale_result.fired or not stale_result.record_id:
            return

        suppression_key = f"stale_proposed_{stale_result.record_id}"
        if self._stale_draft_suppression.is_suppressed(suppression_key):
            _log.debug(
                "stale_draft_trigger: suppressed | record_id=%s", stale_result.record_id
            )
            return

        # Phase 2.6: build the formatted action-required message from stale_result fields.
        context = (
            f" — ältester von {stale_result.stale_count} Entwürfen"
            if stale_result.stale_count > 1
            else ""
        )
        proposal_text = (
            f"🔴 Entscheidung nötig\n\n"
            f"\"{stale_result.display_text}\" liegt seit {stale_result.days_stale} Tagen"
            f" unverändert{context}.\n"
            f"Als veraltet markieren? Antworte auf diese Nachricht:\n\n"
            f"/confirm   als veraltet markieren\n"
            f"/reject    vorerst behalten"
        )
        sent_id = self._send_to_allowed_users(proposal_text)
        if sent_id is None:
            return

        from operator_core.proactive.pending_store import PendingProposal
        proposal = PendingProposal(
            action_type="mark_stale",
            record_id=stale_result.record_id,
            display_text=stale_result.display_text,
            proposed_stage="stale",
            days_stale=stale_result.days_stale,
            sent_message_id=sent_id,
            created_at=datetime.now(tz=timezone.utc),
        )
        self._pending_store.put(proposal)
        self._stale_draft_suppression.record_sent(suppression_key)
        _log.info(
            "stale_draft_trigger: proposal sent | record_id=%s sent_message_id=%s days_stale=%d",
            stale_result.record_id,
            sent_id,
            stale_result.days_stale,
        )

    def _send_to_allowed_users(self, text: str) -> int | None:
        """Send message to all allowed user IDs.

        Returns the message_id of the first successful send, or None if all failed.
        message_id is used to key pending proposals for reply_to binding.
        """
        first_message_id: int | None = None
        seen: set[int] = set()
        for user_id_str in self._settings.telegram.allowed_user_ids:
            try:
                chat_id = int(user_id_str)
            except (ValueError, TypeError):
                _log.warning("proactive checker: invalid user_id in allowlist | value=%r", user_id_str)
                continue
            if chat_id in seen:
                continue
            seen.add(chat_id)
            try:
                response = self._telegram_svc.send_message(chat_id=chat_id, text=text)
                if first_message_id is None:
                    first_message_id = (response or {}).get("result", {}).get("message_id")
            except Exception as exc:
                _log.error("proactive checker: send failed | user_id=%s error=%s", user_id_str, exc)
        return first_message_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seconds_until_target(*, hour: int, tz: ZoneInfo) -> float:
    """Return seconds until the next occurrence of `hour:00` in the given timezone."""
    now = datetime.now(tz=tz)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


def _parse_created_time(ts: str) -> datetime | None:
    """Parse an Airtable ISO 8601 timestamp. Returns None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _most_recent_created_time(
    records: tuple,
) -> datetime | None:
    """Return the most recent created_time across records. Returns None if none parseable."""
    parsed = [
        dt
        for r in records
        if (dt := _parse_created_time(r.created_time or "")) is not None
    ]
    return max(parsed) if parsed else None
