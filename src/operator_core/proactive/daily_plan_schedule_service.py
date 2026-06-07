from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from threading import Event
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from operator_core.core.request_flow.daily_plan_message_store import DailyPlanMessageStore
from operator_core.integrations.daily_plan_service import TodayPlanSnapshot, normalize_bereit_value

if TYPE_CHECKING:
    from operator_core.config import Settings
    from operator_core.integrations.daily_plan_service import DailyPlanService
    from operator_core.integrations.telegram_service import TelegramService
    from operator_core.proactive.daily_plan_schedule_store import DailyPlanScheduleStore
    from operator_core.proactive.posting_recommender import PostingRecommender

_log = logging.getLogger("operator_core.proactive.daily_plan_schedule_service")

_CHECK_INTERVAL_SECONDS = 60
_SCHEDULE_TZ = ZoneInfo("Europe/Berlin")
_SLOT_TIMES: tuple[tuple[int, int, str], ...] = (
    (6, 30, "0630"),
    (9, 0, "0900"),
)
_PLATFORM_ORDER = ("tiktok", "instagram_reel", "facebook_reel", "youtube_short")
_PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram_reel": "Instagram",
    "facebook_reel": "Facebook",
    "youtube_short": "YouTube",
}


@dataclass(frozen=True)
class ScheduledSlot:
    key: str
    when_local: datetime


class DailyPlanScheduleService:
    def __init__(
        self,
        *,
        daily_plan_service: "DailyPlanService",
        posting_recommender: "PostingRecommender | None",
        telegram_svc: "TelegramService",
        settings: "Settings",
        schedule_store: "DailyPlanScheduleStore",
        daily_plan_message_store: DailyPlanMessageStore,
        project_key: str = "everydayengel",
        check_tz: ZoneInfo = _SCHEDULE_TZ,
    ) -> None:
        self._daily_plan_svc = daily_plan_service
        self._recommender = posting_recommender
        self._telegram_svc = telegram_svc
        self._settings = settings
        self._schedule_store = schedule_store
        self._daily_plan_message_store = daily_plan_message_store
        self._project_key = project_key
        self._check_tz = check_tz

    def run_until_stopped(self, stop_event: Event) -> None:
        _log.info("daily plan schedule service starting | project=%s", self._project_key)
        while not stop_event.is_set():
            try:
                self._check_due()
            except Exception as exc:
                _log.error("daily plan schedule service loop error | error=%s", exc)
            stop_event.wait(timeout=_CHECK_INTERVAL_SECONDS)
        _log.info("daily plan schedule service stopped")

    def _check_due(self, *, now_local: datetime | None = None) -> None:
        now_local = now_local or datetime.now(tz=self._check_tz)
        self._schedule_store.prune_before((now_local.date() - timedelta(days=2)))
        snapshots = self._build_today_snapshots(date_value=now_local.date())
        due_slots = [
            ScheduledSlot(
                key=f"daily_plan:{now_local.date().isoformat()}:{slot_key}",
                when_local=now_local.replace(hour=hour, minute=minute, second=0, microsecond=0),
            )
            for hour, minute, slot_key in _SLOT_TIMES
            if now_local >= now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        ]
        for slot in due_slots:
            self._send_slot(slot=slot, snapshots=snapshots)

    def _build_today_snapshots(self, *, date_value: date) -> tuple[TodayPlanSnapshot, ...]:
        date_str = date_value.isoformat()
        recommendation = self._recommender.recommend(project_key=self._project_key) if self._recommender else None
        recommended_candidate = recommendation.candidate if recommendation is not None else None
        candidate_count = recommendation.candidate_count if recommendation is not None else 0

        for platform in _PLATFORM_ORDER:
            plan_type = "post"
            platform_candidate_record_id: str | None = None
            platform_candidate_count = 0
            if recommended_candidate is not None and getattr(recommended_candidate, "platform", "") == platform:
                platform_candidate_record_id = str(getattr(recommended_candidate, "record_id", "") or "").strip() or None
                platform_candidate_count = candidate_count
            self._daily_plan_svc.upsert_plan(
                project_key=self._project_key,
                date=date_str,
                platform=platform,
                plan_type=plan_type,
                candidate_record_id=platform_candidate_record_id,
                candidate_count=platform_candidate_count,
            )

        stored = {
            snapshot.platform or "": snapshot
            for snapshot in self._daily_plan_svc.list_today_plans(
                project_key=self._project_key,
                date=date_str,
            )
        }
        return tuple(
            stored[platform]
            for platform in _PLATFORM_ORDER
            if platform in stored
        )

    def _send_slot(self, *, slot: ScheduledSlot, snapshots: tuple[TodayPlanSnapshot, ...]) -> None:
        targets = self._target_chat_ids()
        if not targets:
            _log.warning("daily plan schedule: no allowed telegram targets configured")
            return

        for chat_id in targets:
            for snapshot in snapshots:
                platform = snapshot.platform or ""
                sent_key = f"{slot.key}:{platform}:{chat_id}"
                if self._schedule_store.has(sent_key):
                    continue
                text = _build_platform_plan_text(snapshot)
                reply_markup = _build_plan_demo_reply_markup(snapshot.record_id)
                try:
                    payload = self._telegram_svc.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                except Exception as exc:
                    _log.error(
                        "daily plan scheduled send failed | slot=%s platform=%s chat_id=%s error=%s",
                        slot.key,
                        platform,
                        chat_id,
                        exc,
                    )
                    continue
                message_id = ((payload or {}).get("result") or {}).get("message_id")
                if isinstance(message_id, int):
                    self._daily_plan_message_store.put(
                        chat_id=chat_id,
                        message_id=message_id,
                        record_id=snapshot.record_id,
                    )
                self._schedule_store.mark_sent(sent_key)
                _log.info(
                    "daily plan scheduled send | slot=%s platform=%s chat_id=%s record_id=%s",
                    slot.key,
                    platform,
                    chat_id,
                    snapshot.record_id,
                )

    def _target_chat_ids(self) -> tuple[int, ...]:
        seen: set[int] = set()
        ordered: list[int] = []
        for raw in self._settings.telegram.allowed_user_ids:
            try:
                chat_id = int(raw)
            except (TypeError, ValueError):
                continue
            if chat_id in seen:
                continue
            seen.add(chat_id)
            ordered.append(chat_id)
        return tuple(ordered)


def _build_plan_demo_reply_markup(record_id: str) -> dict:
    suffix = f":{record_id}" if record_id else ""
    return {
        "inline_keyboard": [
            [
                {"text": "⏭ Heute auslassen", "callback_data": f"plan_demo:skip_today{suffix}"},
                {"text": "🪄 Automatisch ergänzen", "callback_data": f"plan_demo:auto_fill{suffix}"},
            ],
            [
                {"text": "🧹 Auswahl leeren", "callback_data": f"plan_demo:clear_selection{suffix}"},
                {"text": "🔄 Ersetzen", "callback_data": f"plan_demo:replace_field_select{suffix}"},
            ],
            [
                {"text": "⬆️ Upload in Airtable", "callback_data": f"plan_demo:upload_airtable{suffix}"},
            ],
            [
                {"text": "⏰ In 15 Min. erinnern", "callback_data": f"plan_demo:remind_15m{suffix}"},
            ],
        ]
    }


def _build_platform_plan_text(snapshot: TodayPlanSnapshot) -> str:
    platform = snapshot.platform or ""
    platform_label = _PLATFORM_LABELS.get(platform, platform or "Plattform")
    status = "heute auslassen" if snapshot.decision == "skip" else (snapshot.decision or "offen")
    lines = [
        f"📋 Tagesplan · {platform_label}",
        "",
        f"Status: {status}",
        f"Serie/Thema: {snapshot.serie_thema or '—'}",
        f"Title: {snapshot.title_raw or '—'}",
        f"Hook: {snapshot.hook or '—'}",
        f"CTA: {snapshot.cta or '—'}",
        f"Caption: {snapshot.caption or '—'}",
        f"Format: {snapshot.format_typ or '—'}",
        f"Bereit: {normalize_bereit_value(snapshot.bereit) or '—'}",
    ]
    return "\n".join(lines)
