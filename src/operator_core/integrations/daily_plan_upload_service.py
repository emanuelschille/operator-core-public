from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

from operator_core.integrations.daily_plan_service import TodayPlanSnapshot, normalize_bereit_value

if TYPE_CHECKING:
    from operator_core.integrations.airtable_service import AirtableService
    from operator_core.integrations.daily_plan_service import DailyPlanService
    from operator_core.integrations.operational_knowledge_service import OperationalKnowledgeLoader


_ANALYTICS_PROJECT_KEY = "analytics"

_OK_ANALYTICS_TABLE_KEYS: dict[str, str] = {
    "tiktok": "analytics_table_tiktok",
    "instagram_reel": "analytics_table_instagram_reel",
    "facebook_reel": "analytics_table_facebook_reel",
    "youtube_short": "analytics_table_youtube_short",
}

_OK_POSTING_TIME_KEYS: dict[str, str] = {
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


@dataclass(frozen=True)
class UploadPlanResult:
    updated_snapshot: TodayPlanSnapshot
    analytics_record_id: str
    analytics_table_id: str
    default_posted_at_local: str


class DailyPlanUploadService:
    def __init__(
        self,
        airtable_service: "AirtableService",
        ok_loader: "OperationalKnowledgeLoader",
        daily_plan_service: "DailyPlanService",
    ) -> None:
        self._airtable_svc = airtable_service
        self._ok_loader = ok_loader
        self._daily_plan_svc = daily_plan_service

    def upload_plan_snapshot(
        self,
        *,
        project_key: str,
        snapshot: TodayPlanSnapshot,
        date: str,
    ) -> UploadPlanResult:
        platform = snapshot.platform or ""
        table_id = self._resolve_platform_table_id(project_key=project_key, platform=platform)
        fields = self._build_platform_fields(project_key=project_key, snapshot=snapshot)

        if snapshot.platform_record_id:
            record = self._airtable_svc.update_record(
                table_id,
                snapshot.platform_record_id,
                fields,
                project_key=_ANALYTICS_PROJECT_KEY,
            )
        else:
            record = self._airtable_svc.create_record(
                table_id,
                fields,
                project_key=_ANALYTICS_PROJECT_KEY,
            )

        updated_snapshot = self._daily_plan_svc.link_uploaded_record(
            project_key=project_key,
            record_id=snapshot.record_id,
            platform_record_id=record.record_id,
            platform_table_id=table_id,
        )
        default_posted_at_local = self.build_default_posted_at_local(
            project_key=project_key,
            platform=platform,
            date=date,
        )
        return UploadPlanResult(
            updated_snapshot=updated_snapshot,
            analytics_record_id=record.record_id,
            analytics_table_id=table_id,
            default_posted_at_local=default_posted_at_local,
        )

    def build_default_posted_at_local(
        self,
        *,
        project_key: str,
        platform: str,
        date: str,
    ) -> str:
        return self._build_default_posted_at_local(
            project_key=project_key,
            platform=platform,
            date=date,
        )

    def set_posted_at_local(
        self,
        *,
        project_key: str,
        snapshot: TodayPlanSnapshot,
        posted_at_local: str,
    ) -> TodayPlanSnapshot:
        if not snapshot.platform_table_id or not snapshot.platform_record_id:
            raise RuntimeError("uploaded analytics linkage missing")

        self._airtable_svc.update_record(
            snapshot.platform_table_id,
            snapshot.platform_record_id,
            {"posted_at_local": posted_at_local},
            project_key=_ANALYTICS_PROJECT_KEY,
        )
        return self._daily_plan_svc.set_posted_at_local(
            project_key=project_key,
            record_id=snapshot.record_id,
            posted_at_local=posted_at_local,
        )

    def _resolve_platform_table_id(self, *, project_key: str, platform: str) -> str:
        ok_key = _OK_ANALYTICS_TABLE_KEYS.get(platform, "")
        if not ok_key:
            raise RuntimeError(f"no analytics table mapping for platform {platform!r}")

        ok_ctx = self._ok_loader.load_active(project_key=project_key)
        for row in ok_ctx.rows:
            if row.key == ok_key and row.value.strip():
                return row.value.strip()
        raise RuntimeError(f"missing analytics table id for platform {platform!r}")

    def _build_default_posted_at_local(
        self,
        *,
        project_key: str,
        platform: str,
        date: str,
    ) -> str:
        ok_ctx = self._ok_loader.load_active(project_key=project_key)
        weekday = datetime.date.fromisoformat(date).strftime("%A").lower()
        schedule = ok_ctx.resolve_posting_schedule(
            platform=platform,
            weekday=weekday,
            fallback_key=_OK_POSTING_TIME_KEYS.get(platform, ""),
            default_time=_DEFAULT_POSTING_TIMES.get(platform, "19:00"),
        )
        time_value = schedule.time_local or _DEFAULT_POSTING_TIMES.get(platform, "19:00")
        return f"{date} {time_value}"

    def _build_platform_fields(
        self,
        *,
        project_key: str,
        snapshot: TodayPlanSnapshot,
    ) -> dict[str, str]:
        fields = {
            "serie_thema": snapshot.serie_thema,
            "titel": snapshot.title_raw,
            "hook_kurz": snapshot.hook,
            "cta_typ": snapshot.cta,
            "caption": snapshot.caption,
            "format_typ": snapshot.format_typ,
            "bereit": normalize_bereit_value(snapshot.bereit),
            "draft_record_id": snapshot.candidate_record_id or "",
        }

        if snapshot.candidate_record_id:
            try:
                draft_record = self._airtable_svc.get_record(
                    "Content Drafts",
                    snapshot.candidate_record_id,
                    project_key=project_key,
                )
                content_id = str(draft_record.fields.get("content_id") or "").strip()
                if content_id:
                    fields["content_id"] = content_id
            except Exception:
                pass

        return {key: value for key, value in fields.items() if value}


def parse_posted_time_input(*, date: str, text: str) -> str | None:
    raw = " ".join(str(text or "").strip().split())
    if not raw:
        return None
    try:
        parsed = datetime.datetime.strptime(raw, "%H:%M")
    except ValueError:
        return None
    return f"{date} {parsed.strftime('%H:%M')}"
