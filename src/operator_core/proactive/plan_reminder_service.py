from __future__ import annotations

import logging
from threading import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_core.config import Settings
    from operator_core.integrations.daily_plan_service import DailyPlanService, TodayPlanSnapshot
    from operator_core.integrations.telegram_service import TelegramService
    from operator_core.proactive.plan_reminder_store import PlanReminder, PlanReminderStore

_log = logging.getLogger("operator_core.proactive.plan_reminder_service")

_CHECK_INTERVAL_SECONDS = 60

_PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram_reel": "Instagram",
    "facebook_reel": "Facebook",
    "youtube_short": "YouTube",
}

_STATUS_LABELS: dict[str, str] = {
    "pending": "offen",
    "skip": "heute auslassen",
}


class PlanReminderService:
    """Background service: fires scheduled plan reminders via Telegram.

    Checks PlanReminderStore every 60 seconds. For remind_15m reminders:
    re-fetches the current snapshot and suppresses if the platform was
    already uploaded or explicitly skipped.
    """

    def __init__(
        self,
        *,
        store: "PlanReminderStore",
        telegram_svc: "TelegramService",
        settings: "Settings",
        daily_plan_service: "DailyPlanService | None" = None,
        project_key: str = "everydayengel",
    ) -> None:
        self._store = store
        self._telegram_svc = telegram_svc
        self._settings = settings
        self._daily_plan_svc = daily_plan_service
        self._project_key = project_key

    def run_until_stopped(self, stop_event: Event) -> None:
        _log.info("plan reminder service starting | project=%s", self._project_key)
        while not stop_event.is_set():
            stop_event.wait(timeout=_CHECK_INTERVAL_SECONDS)
            if stop_event.is_set():
                break
            try:
                self._check_due()
            except Exception as exc:
                _log.error("plan reminder service loop error | error=%s", exc)
        _log.info("plan reminder service stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_due(self) -> None:
        due = self._store.due()
        for reminder in due:
            try:
                self._fire(reminder)
            except Exception as exc:
                _log.error(
                    "plan reminder fire failed | key=%s error=%s", reminder.key, exc
                )

    def _fire(self, reminder: "PlanReminder") -> None:
        platform_label = _PLATFORM_LABELS.get(reminder.platform, reminder.platform)
        snapshot: "TodayPlanSnapshot | None" = None

        if reminder.reminder_type == "remind_15m":
            # Refresh snapshot and check obsolescence
            if self._daily_plan_svc is not None:
                try:
                    snapshot = self._daily_plan_svc.get_plan_record(
                        project_key=self._project_key,
                        record_id=reminder.record_id,
                    )
                except Exception as exc:
                    _log.warning(
                        "plan reminder: snapshot refresh failed | key=%s error=%s",
                        reminder.key,
                        exc,
                    )

            if snapshot is not None:
                if snapshot.posted_at_local:
                    _log.info(
                        "plan reminder suppressed (already posted) | key=%s platform=%s",
                        reminder.key,
                        reminder.platform,
                    )
                    return
                if snapshot.decision == "skip":
                    _log.info(
                        "plan reminder suppressed (skipped) | key=%s platform=%s",
                        reminder.key,
                        reminder.platform,
                    )
                    return

            body = (
                _build_plan_message_text(snapshot)
                if snapshot is not None
                else reminder.context_text
            )
            if snapshot is not None:
                text = body + "\n⏰ Erinnerung nach 15 Minuten."
            else:
                text = f"⏰ Erinnerung · {platform_label}\n\n{body}"
            markup: dict | None = _build_plan_reminder_keyboard(reminder.record_id)

        else:  # analytics_3d
            if self._daily_plan_svc is not None:
                try:
                    snapshot = self._daily_plan_svc.get_plan_record(
                        project_key=self._project_key,
                        record_id=reminder.record_id,
                    )
                except Exception as exc:
                    _log.warning(
                        "analytics reminder: snapshot refresh failed | key=%s error=%s",
                        reminder.key,
                        exc,
                    )

            if snapshot is not None:
                if snapshot.decision == "skip":
                    _log.info(
                        "analytics reminder suppressed (skipped) | key=%s platform=%s",
                        reminder.key,
                        reminder.platform,
                    )
                    return
                if not snapshot.posted_at_local:
                    _log.info(
                        "analytics reminder suppressed (posted_at_local cleared) | key=%s platform=%s",
                        reminder.key,
                        reminder.platform,
                    )
                    return
                if not snapshot.platform_record_id:
                    _log.info(
                        "analytics reminder suppressed (analytics link missing) | key=%s platform=%s",
                        reminder.key,
                        reminder.platform,
                    )
                    return
                if (
                    reminder.analytics_record_id
                    and snapshot.platform_record_id != reminder.analytics_record_id
                ):
                    _log.info(
                        "analytics reminder suppressed (analytics link changed) | key=%s platform=%s current=%s scheduled=%s",
                        reminder.key,
                        reminder.platform,
                        snapshot.platform_record_id,
                        reminder.analytics_record_id,
                    )
                    return
                if (
                    reminder.analytics_table_id
                    and snapshot.platform_table_id
                    and snapshot.platform_table_id != reminder.analytics_table_id
                ):
                    _log.info(
                        "analytics reminder suppressed (analytics table changed) | key=%s platform=%s current=%s scheduled=%s",
                        reminder.key,
                        reminder.platform,
                        snapshot.platform_table_id,
                        reminder.analytics_table_id,
                    )
                    return

            text = reminder.context_text
            markup = None

        self._send(reminder, text, markup)

    def _send(self, reminder: "PlanReminder", text: str, markup: dict | None) -> None:
        sent = False
        for user_id_str in self._settings.telegram.allowed_user_ids:
            try:
                chat_id = int(user_id_str)
                self._telegram_svc.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=markup,
                )
                _log.info(
                    "plan reminder sent | key=%s type=%s platform=%s chat_id=%s",
                    reminder.key,
                    reminder.reminder_type,
                    reminder.platform,
                    chat_id,
                )
                sent = True
            except Exception as exc:
                _log.error(
                    "plan reminder send failed | key=%s chat_id=%s error=%s",
                    reminder.key,
                    user_id_str,
                    exc,
                )
        if not sent:
            _log.warning(
                "plan reminder: no successful sends | key=%s allowed_count=%d",
                reminder.key,
                len(self._settings.telegram.allowed_user_ids),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_snapshot_text(snapshot: "TodayPlanSnapshot") -> str:
    """Format a plan snapshot as a compact field list for the reminder body."""
    from operator_core.integrations.daily_plan_service import normalize_bereit_value

    def _dv(v: str) -> str:
        return v or "—"

    status = _STATUS_LABELS.get(snapshot.decision, snapshot.decision or "offen")
    lines = [
        f"Status: {status}",
        f"Serie/Thema: {_dv(snapshot.serie_thema)}",
        f"Title: {_dv(snapshot.title_raw)}",
        f"Hook: {_dv(snapshot.hook)}",
        f"CTA: {_dv(snapshot.cta)}",
        f"Caption: {_dv(snapshot.caption)}",
        f"Format: {_dv(snapshot.format_typ)}",
        f"Bereit: {_dv(normalize_bereit_value(snapshot.bereit))}",
    ]
    return "\n".join(lines)


def _build_plan_message_text(snapshot: "TodayPlanSnapshot") -> str:
    platform_label = _PLATFORM_LABELS.get(snapshot.platform or "", snapshot.platform or "Plattform")
    return f"📋 Tagesplan · {platform_label}\n\n{_format_snapshot_text(snapshot)}"


def _build_plan_reminder_keyboard(record_id: str) -> dict:
    """Inline keyboard for a 15-min reminder — mirrors the plan_demo buttons."""
    s = f":{record_id}" if record_id else ""
    return {
        "inline_keyboard": [
            [
                {"text": "⏭ Heute auslassen", "callback_data": f"plan_demo:skip_today{s}"},
                {"text": "🪄 Automatisch ergänzen", "callback_data": f"plan_demo:auto_fill{s}"},
            ],
            [
                {"text": "🧹 Auswahl leeren", "callback_data": f"plan_demo:clear_selection{s}"},
            ],
            [
                {"text": "⬆️ Upload in Airtable", "callback_data": f"plan_demo:upload_airtable{s}"},
            ],
            [
                {"text": "⏰ In 15 Min. erinnern", "callback_data": f"plan_demo:remind_15m{s}"},
            ],
        ]
    }
