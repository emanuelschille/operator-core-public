from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from operator_core.integrations.airtable_service import AirtableService

_log = logging.getLogger("operator_core.integrations.daily_plan_service")

_TABLE_NAME = "Daily Plans"
_CONTENT_DRAFTS_TABLE = "Content Drafts"
_ANALYTICS_PROJECT_KEY = "analytics"
_DECIDED_STATES = frozenset({"skip", "post", "draft"})
_SELECTION_FIELDS = ("serie_thema", "title_raw", "hook", "cta", "caption", "format_typ", "bereit")
_PLATFORM_ORDER = {
    "tiktok": 0,
    "instagram_reel": 1,
    "facebook_reel": 2,
    "youtube_short": 3,
}
_PLATFORM_FORMAT_TOKENS = {
    "tiktok": ("tiktok",),
    "instagram_reel": ("instagram", "reel", "reels"),
    "facebook_reel": ("facebook", "reel", "reels"),
    "youtube_short": ("youtube", "short", "shorts"),
}
_CONTENT_CONTEXT_FIELDS = ("title_raw", "hook", "cta", "caption")


@dataclass(frozen=True)
class TodayPlanSnapshot:
    """Lightweight read of a stored per-platform Daily Plan row."""

    record_id: str
    decision: str
    plan_type: str | None = None
    platform: str | None = None
    candidate_count: int | None = None
    candidate_record_id: str | None = None
    platform_record_id: str | None = None
    platform_table_id: str | None = None
    posted_at_local: str = ""
    serie_thema: str = ""
    title_raw: str = ""
    hook: str = ""
    cta: str = ""
    caption: str = ""
    format_typ: str = ""
    bereit: str = ""


def _normalize_decision(value: object) -> str:
    return str(value or "").strip() or "pending"


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _first_non_empty(fields: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _normalize_text(fields.get(key))
        if value:
            return value
    return ""


def normalize_bereit_value(value: object) -> str:
    raw = _normalize_text(value)
    if not raw:
        return ""

    normalized = raw.lower().replace("-", " ").replace("_", " ")
    normalized = " ".join(normalized.split())
    if normalized in {"not required", "not_required"}:
        return "Kein Review nötig"
    if normalized == "approved":
        return "Freigegeben"
    if normalized in {"no retry", "no retry needed", "no_retry", "no_retry_needed"}:
        return "Kein Retry"
    return raw


def _analytics_selection_snapshot(fields: dict[str, Any]) -> dict[str, str]:
    result = {
        "serie_thema": _first_non_empty(fields, "serie_thema", "serie", "series_theme", "theme", "thema"),
        "title_raw": _first_non_empty(fields, "title_raw", "titel", "title"),
        "hook": _first_non_empty(fields, "hook", "hook_kurz", "hook_text"),
        "cta": _first_non_empty(fields, "cta", "cta_typ", "cta_direction"),
        "caption": _first_non_empty(fields, "caption", "title_raw", "body", "caption_text"),
        "format_typ": _first_non_empty(fields, "format_typ", "format"),
        "bereit": normalize_bereit_value(
            _first_non_empty(fields, "bereit", "readiness_check", "approval_state")
        ),
    }
    _log.info(
        "daily_plan analytics_selection_snapshot | fields_present=%s mapped_non_empty=%s",
        sorted(fields.keys()),
        {k: v for k, v in result.items() if v},
    )
    return result


def _is_platform_safe_shared_format(value: str, target_platform: str | None) -> bool:
    normalized = _normalize_text(value).lower()
    if not normalized:
        return False

    mentioned_platforms = {
        platform
        for platform, tokens in _PLATFORM_FORMAT_TOKENS.items()
        if any(token in normalized for token in tokens)
    }
    if not mentioned_platforms:
        return True
    if not target_platform:
        return False
    return mentioned_platforms == {target_platform}


def _build_today_lookup_formula(
    *,
    project_key: str,
    date: str,
    platform: str | None = None,
) -> str:
    """Build an Airtable formula that matches a date field by day value.

    Airtable date fields do not reliably match plain string equality against
    YYYY-MM-DD literals. Normalize the field to a day string first, then
    compare against the requested date.
    """
    base_formula = (
        f"AND(DATETIME_FORMAT({{date}}, 'YYYY-MM-DD')=\"{date}\","
        f"{{project}}=\"{project_key}\")"
    )
    normalized_platform = _normalize_text(platform).lower()
    if not normalized_platform:
        return base_formula
    return (
        f"AND(DATETIME_FORMAT({{date}}, 'YYYY-MM-DD')=\"{date}\","
        f"{{project}}=\"{project_key}\","
        f"LOWER({{platform}})=\"{normalized_platform}\")"
    )


def _selection_snapshot(fields: dict[str, Any]) -> dict[str, str]:
    return {
        "serie_thema": _normalize_text(fields.get("serie_thema")),
        "title_raw": _normalize_text(fields.get("title_raw")),
        "hook": _normalize_text(fields.get("hook")),
        "cta": _normalize_text(fields.get("cta")),
        "caption": _normalize_text(fields.get("caption")),
        "format_typ": _normalize_text(fields.get("format_typ")),
        "bereit": normalize_bereit_value(fields.get("bereit")),
    }


def _parse_snapshot(record: Any) -> TodayPlanSnapshot:
    decision = _normalize_decision(record.fields.get("decision"))
    plan_type_raw = _normalize_text(record.fields.get("plan_type"))
    platform_raw = _normalize_text(record.fields.get("platform")).lower()
    candidate_record_id_raw = _normalize_text(record.fields.get("candidate_record_id"))
    platform_record_id_raw = _normalize_text(record.fields.get("platform_record_id"))
    platform_table_id_raw = _normalize_text(record.fields.get("platform_table_id"))
    posted_at_local = _normalize_text(record.fields.get("posted_at_local"))
    raw_count = record.fields.get("candidate_count")
    candidate_count = int(raw_count) if raw_count is not None else None
    selection = _selection_snapshot(record.fields)
    return TodayPlanSnapshot(
        record_id=record.record_id,
        decision=decision,
        plan_type=plan_type_raw if plan_type_raw else None,
        platform=platform_raw if platform_raw else None,
        candidate_count=candidate_count,
        candidate_record_id=candidate_record_id_raw if candidate_record_id_raw else None,
        platform_record_id=platform_record_id_raw if platform_record_id_raw else None,
        platform_table_id=platform_table_id_raw if platform_table_id_raw else None,
        posted_at_local=posted_at_local,
        serie_thema=selection["serie_thema"],
        title_raw=selection["title_raw"],
        hook=selection["hook"],
        cta=selection["cta"],
        caption=selection["caption"],
        format_typ=selection["format_typ"],
        bereit=selection["bereit"],
    )


def _merge_snapshot(current: TodayPlanSnapshot, updated: TodayPlanSnapshot) -> TodayPlanSnapshot:
    merged = dict(current.__dict__)
    for key, value in updated.__dict__.items():
        if key in {"record_id", "decision"}:
            merged[key] = value
            continue
        if value not in ("", None):
            merged[key] = value
    return TodayPlanSnapshot(**merged)


class DailyPlanService:
    """Persists daily plan decisions to Airtable.

    One row per (project_key, date, platform). Decision tracks per-platform
    selection state while editable content fields live on the same row.

    Upsert rule:
    - If no row exists for today+platform: create with decision=pending.
    - If existing row is pending: refresh plan metadata (plan_type, candidate etc).
    - If existing row is already decided (`skip`, `post`, `draft`): return
      record_id unchanged — do NOT overwrite the operator's decision.
    """

    def __init__(self, airtable_service: "AirtableService") -> None:
        self._airtable_svc = airtable_service

    def _find_matching_rows(
        self,
        *,
        project_key: str,
        date: str,
        fields: tuple[str, ...],
        platform: str | None = None,
    ) -> tuple[Any, ...]:
        filter_formula = _build_today_lookup_formula(
            project_key=project_key,
            date=date,
            platform=platform,
        )
        existing = self._airtable_svc.find_records(
            _TABLE_NAME,
            filter_formula=filter_formula,
            project_key=project_key,
            fields=fields,
        )
        return existing.records

    def _select_authoritative_row(
        self,
        *,
        project_key: str,
        date: str,
        records: tuple[Any, ...],
        platform: str | None = None,
    ) -> Any | None:
        if not records:
            return None

        def rank(record: Any) -> tuple[int, str, str]:
            decision = _normalize_decision(record.fields.get("decision"))
            priority = 1 if decision in _DECIDED_STATES else 0
            created_time = str(record.created_time or "")
            return (priority, created_time, record.record_id)

        chosen = max(records, key=rank)
        if len(records) > 1:
            _log.warning(
                "daily_plan duplicate rows detected | project=%s date=%s platform=%s count=%s chosen_record_id=%s chosen_decision=%s",
                project_key,
                date,
                platform or "-",
                len(records),
                chosen.record_id,
                _normalize_decision(chosen.fields.get("decision")),
            )
        return chosen

    def upsert_plan(
        self,
        *,
        project_key: str,
        date: str,
        plan_type: str,
        platform: str | None = None,
        candidate_record_id: str | None = None,
        candidate_count: int | None = None,
    ) -> str:
        """Return the Airtable record_id for today's platform plan, creating if needed.

        Args:
            project_key: project identifier (e.g. "everydayengel")
            date: YYYY-MM-DD string
            plan_type: recommender output — "post", "draft", or "skip"
            platform: platform of the recommendation
            candidate_record_id: Airtable record_id of the recommended draft
            candidate_count: number of eligible drafts at decision time

        Returns:
            Airtable record_id string
        """
        normalized_platform = _normalize_text(platform).lower()
        existing_records = self._find_matching_rows(
            project_key=project_key,
            date=date,
            platform=normalized_platform,
            fields=("date", "project", "platform", "decision"),
        )
        record = self._select_authoritative_row(
            project_key=project_key,
            date=date,
            records=existing_records,
            platform=normalized_platform,
        )

        if record is not None:
            current_decision = _normalize_decision(record.fields.get("decision"))
            if current_decision in _DECIDED_STATES:
                _log.debug(
                    "daily_plan upsert: existing decided row returned | project=%s date=%s decision=%s",
                    project_key,
                    date,
                    current_decision,
                )
                return record.record_id

            # Pending (or empty) — refresh plan metadata
            update_fields: dict[str, Any] = {"plan_type": plan_type}
            if candidate_record_id is not None:
                update_fields["candidate_record_id"] = candidate_record_id
            if normalized_platform:
                update_fields["platform"] = normalized_platform
            if candidate_count is not None:
                update_fields["candidate_count"] = candidate_count

            updated = self._airtable_svc.update_record(
                _TABLE_NAME,
                record.record_id,
                update_fields,
                project_key=project_key,
            )
            _log.debug(
                "daily_plan upsert: pending row refreshed | project=%s date=%s",
                project_key,
                date,
            )
            return updated.record_id

        # No row yet — create
        create_fields: dict[str, Any] = {
            "date": date,
            "project": project_key,
            "plan_type": plan_type,
            "decision": "pending",
        }
        if normalized_platform:
            create_fields["platform"] = normalized_platform
        if candidate_record_id is not None:
            create_fields["candidate_record_id"] = candidate_record_id
        if candidate_count is not None:
            create_fields["candidate_count"] = candidate_count

        created = self._airtable_svc.create_record(
            _TABLE_NAME,
            create_fields,
            project_key=project_key,
        )
        _log.debug(
            "daily_plan upsert: new row created | project=%s date=%s plan_type=%s record_id=%s",
            project_key,
            date,
            plan_type,
            created.record_id,
        )
        return created.record_id

    def update_decision(
        self,
        *,
        project_key: str,
        record_id: str,
        decision: str,
    ) -> None:
        """Update the decision field on an existing daily plan record.

        Args:
            project_key: project identifier
            record_id: Airtable record_id of the daily plan row
            decision: new decision value ("post", "skip", "draft", "pending")
        """
        self._airtable_svc.update_record(
            _TABLE_NAME,
            record_id,
            {"decision": decision},
            project_key=project_key,
        )
        _log.debug(
            "daily_plan update_decision | project=%s record_id=%s decision=%s",
            project_key,
            record_id,
            decision,
        )

    def list_today_plans(
        self,
        *,
        project_key: str,
        date: str,
    ) -> tuple[TodayPlanSnapshot, ...]:
        records = self._find_matching_rows(
            project_key=project_key,
            date=date,
            fields=(
                "date",
                "project",
                "platform",
                "decision",
                "plan_type",
                "candidate_count",
                "candidate_record_id",
                "platform_record_id",
                "platform_table_id",
                "posted_at_local",
                "serie_thema",
                "title_raw",
                "hook",
                "cta",
                "caption",
                "format_typ",
                "bereit",
            ),
        )
        grouped: dict[str, list[Any]] = {}
        for record in records:
            platform = _normalize_text(record.fields.get("platform")).lower() or "unknown"
            grouped.setdefault(platform, []).append(record)

        snapshots: list[TodayPlanSnapshot] = []
        for platform, rows in grouped.items():
            chosen = self._select_authoritative_row(
                project_key=project_key,
                date=date,
                records=tuple(rows),
                platform=platform,
            )
            if chosen is not None:
                snapshots.append(_parse_snapshot(chosen))

        snapshots.sort(
            key=lambda snapshot: (
                _PLATFORM_ORDER.get(snapshot.platform or "", 99),
                snapshot.platform or "",
                snapshot.record_id,
            )
        )
        return tuple(snapshots)

    def get_today_plan(
        self,
        *,
        project_key: str,
        date: str,
        platform: str | None = None,
    ) -> TodayPlanSnapshot | None:
        """Return a snapshot of today's plan row, optionally scoped to one platform.

        Args:
            project_key: project identifier (e.g. "everydayengel")
            date: YYYY-MM-DD string

        Returns:
            TodayPlanSnapshot if a row exists, else None.
        """
        normalized_platform = _normalize_text(platform).lower()
        existing_records = self._find_matching_rows(
            project_key=project_key,
            date=date,
            platform=normalized_platform,
            fields=(
                "date",
                "project",
                "platform",
                "decision",
                "plan_type",
                "candidate_count",
                "candidate_record_id",
                "platform_record_id",
                "platform_table_id",
                "posted_at_local",
                "serie_thema",
                "title_raw",
                "hook",
                "cta",
                "caption",
                "format_typ",
                "bereit",
            ),
        )
        record = self._select_authoritative_row(
            project_key=project_key,
            date=date,
            records=existing_records,
            platform=normalized_platform,
        )
        if record is None:
            return None

        _log.debug(
            "daily_plan get_today_plan | project=%s date=%s platform=%s record_id=%s",
            project_key,
            date,
            normalized_platform or "-",
            record.record_id,
        )
        return _parse_snapshot(record)

    def autofill_selection(
        self,
        *,
        project_key: str,
        record_id: str,
        siblings: tuple[TodayPlanSnapshot, ...] = (),
        excluded_values: dict[str, str] | None = None,
    ) -> TodayPlanSnapshot:
        record = self._airtable_svc.get_record(
            _TABLE_NAME,
            record_id,
            project_key=project_key,
        )
        current = _parse_snapshot(record)
        update_fields: dict[str, Any] = {}
        has_locked_content_context = any(
            getattr(current, field_name, "")
            for field_name in _CONTENT_CONTEXT_FIELDS
        )
        source_values = self._build_autofill_source_values(
            project_key=project_key,
            platform=current.platform,
            platform_record_id=current.platform_record_id,
            platform_table_id=current.platform_table_id,
            candidate_record_id=current.candidate_record_id,
            siblings=siblings,
            excluded_values=excluded_values or {},
        )

        if not current.serie_thema and source_values["serie_thema"] and not has_locked_content_context:
            update_fields["serie_thema"] = source_values["serie_thema"]
        if not current.title_raw and source_values["title_raw"] and not has_locked_content_context:
            update_fields["title_raw"] = source_values["title_raw"]
        if not current.hook and source_values["hook"] and not has_locked_content_context:
            update_fields["hook"] = source_values["hook"]
        if not current.cta and source_values["cta"] and not has_locked_content_context:
            update_fields["cta"] = source_values["cta"]
        if not current.caption and source_values["caption"] and not has_locked_content_context:
            update_fields["caption"] = source_values["caption"]
        if not current.format_typ and source_values["format_typ"]:
            update_fields["format_typ"] = source_values["format_typ"]
        if not current.bereit and source_values["bereit"]:
            update_fields["bereit"] = source_values["bereit"]

        if current.decision == "skip":
            update_fields["decision"] = "pending"

        if not update_fields:
            return current

        updated = self._airtable_svc.update_record(
            _TABLE_NAME,
            record_id,
            update_fields,
            project_key=project_key,
        )
        updated_snapshot = _parse_snapshot(updated)
        return _merge_snapshot(current, updated_snapshot)

    def get_plan_record(
        self,
        *,
        project_key: str,
        record_id: str,
    ) -> TodayPlanSnapshot:
        record = self._airtable_svc.get_record(
            _TABLE_NAME,
            record_id,
            project_key=project_key,
        )
        return _parse_snapshot(record)

    def link_uploaded_record(
        self,
        *,
        project_key: str,
        record_id: str,
        platform_record_id: str,
        platform_table_id: str,
    ) -> TodayPlanSnapshot:
        updated = self._airtable_svc.update_record(
            _TABLE_NAME,
            record_id,
            {
                "platform_record_id": platform_record_id,
                "platform_table_id": platform_table_id,
            },
            project_key=project_key,
        )
        return _parse_snapshot(updated)

    def set_posted_at_local(
        self,
        *,
        project_key: str,
        record_id: str,
        posted_at_local: str,
    ) -> TodayPlanSnapshot:
        updated = self._airtable_svc.update_record(
            _TABLE_NAME,
            record_id,
            {"posted_at_local": posted_at_local},
            project_key=project_key,
        )
        return _parse_snapshot(updated)

    def patch_fields(
        self,
        *,
        project_key: str,
        record_id: str,
        fields: dict[str, Any],
        current: TodayPlanSnapshot,
    ) -> TodayPlanSnapshot:
        """Write a set of fields to the Daily Plan row and return the merged snapshot.

        The caller is responsible for ensuring only empty fields are written.
        """
        updated = self._airtable_svc.update_record(
            _TABLE_NAME,
            record_id,
            fields,
            project_key=project_key,
        )
        updated_snapshot = _parse_snapshot(updated)
        return _merge_snapshot(current, updated_snapshot)

    def clear_selection(
        self,
        *,
        project_key: str,
        record_id: str,
    ) -> TodayPlanSnapshot:
        updated = self._airtable_svc.update_record(
            _TABLE_NAME,
            record_id,
            {
                "decision": "pending",
                "serie_thema": "",
                "title_raw": "",
                "hook": "",
                "cta": "",
                "caption": "",
                "format_typ": "",
                "bereit": "",
            },
            project_key=project_key,
        )
        return _parse_snapshot(updated)

    def _build_autofill_source_values(
        self,
        *,
        project_key: str,
        platform: str | None,
        platform_record_id: str | None,
        platform_table_id: str | None,
        candidate_record_id: str | None,
        siblings: tuple[TodayPlanSnapshot, ...],
        excluded_values: dict[str, str],
    ) -> dict[str, str]:
        candidate_values = {
            "serie_thema": "",
            "title_raw": "",
            "hook": "",
            "cta": "",
            "caption": "",
            "format_typ": "",
            "bereit": "",
        }
        if candidate_record_id:
            try:
                candidate = self._airtable_svc.get_record(
                    _CONTENT_DRAFTS_TABLE,
                    candidate_record_id,
                    project_key=project_key,
                )
                candidate_values = {
                    "serie_thema": _first_non_empty(
                        candidate.fields,
                        "serie_thema",
                        "series_theme",
                        "theme",
                        "thema",
                        "pillar",
                    ),
                    "title_raw": _first_non_empty(
                        candidate.fields,
                        "title_raw",
                        "main_point",
                        "title",
                        "titel",
                        "headline",
                    ),
                    "hook": _first_non_empty(
                        candidate.fields,
                        "hook",
                        "hook_text",
                        "hook_kurz",
                    ),
                    "cta": _first_non_empty(
                        candidate.fields,
                        "cta",
                        "cta_direction",
                        "cta_typ",
                        "call_to_action",
                    ),
                    "caption": _first_non_empty(
                        candidate.fields,
                        "caption",
                        "body",
                        "caption_text",
                        "draft_text",
                        "script_text",
                    ),
                    "format_typ": _first_non_empty(
                        candidate.fields,
                        "format_typ",
                        "format",
                        "draft_type",
                    ),
                    "bereit": normalize_bereit_value(
                        _first_non_empty(
                            candidate.fields,
                            "bereit",
                            "readiness_check",
                            "approval_state",
                            "review_outcome",
                        )
                    ),
                }
            except Exception as exc:
                _log.warning(
                    "daily_plan autofill: candidate lookup failed | project=%s record_id=%s error=%s",
                    project_key,
                    candidate_record_id,
                    exc,
                )

        current_analytics_values = self._load_analytics_source_values(
            platform_record_id=platform_record_id,
            platform_table_id=platform_table_id,
        )

        sibling_candidate_values = {
            "serie_thema": "",
            "title_raw": "",
            "hook": "",
            "cta": "",
            "caption": "",
            "bereit": "",
        }
        sibling_analytics_values = {
            "title_raw": "",
            "hook": "",
            "cta": "",
            "bereit": "",
        }
        sibling_format_value = ""
        for sibling in siblings:
            shares_candidate = bool(
                candidate_record_id and sibling.candidate_record_id == candidate_record_id
            )
            if shares_candidate:
                if not sibling_candidate_values["serie_thema"] and sibling.serie_thema:
                    sibling_candidate_values["serie_thema"] = sibling.serie_thema
                if not sibling_candidate_values["title_raw"] and sibling.title_raw:
                    sibling_candidate_values["title_raw"] = sibling.title_raw
                if not sibling_candidate_values["hook"] and sibling.hook:
                    sibling_candidate_values["hook"] = sibling.hook
                if not sibling_candidate_values["cta"] and sibling.cta:
                    sibling_candidate_values["cta"] = sibling.cta
                if not sibling_candidate_values["caption"] and sibling.caption:
                    sibling_candidate_values["caption"] = sibling.caption
                if not sibling_candidate_values["bereit"] and sibling.bereit:
                    sibling_candidate_values["bereit"] = normalize_bereit_value(sibling.bereit)
                if not sibling_format_value and sibling.format_typ:
                    sibling_format_value = sibling.format_typ
            linked_analytics = self._load_analytics_source_values(
                platform_record_id=sibling.platform_record_id,
                platform_table_id=sibling.platform_table_id,
            )
            if any(linked_analytics.values()):
                if not sibling_analytics_values["title_raw"] and linked_analytics["title_raw"]:
                    sibling_analytics_values["title_raw"] = linked_analytics["title_raw"]
                if not sibling_analytics_values["hook"] and linked_analytics["hook"]:
                    sibling_analytics_values["hook"] = linked_analytics["hook"]
                if not sibling_analytics_values["cta"] and linked_analytics["cta"]:
                    sibling_analytics_values["cta"] = linked_analytics["cta"]
                if not sibling_analytics_values["bereit"] and linked_analytics["bereit"]:
                    sibling_analytics_values["bereit"] = normalize_bereit_value(linked_analytics["bereit"])
                if not sibling_format_value and linked_analytics["format_typ"]:
                    if shares_candidate or _is_platform_safe_shared_format(
                        linked_analytics["format_typ"],
                        platform,
                    ):
                        sibling_format_value = linked_analytics["format_typ"]
        candidate_values = _apply_exclusions(candidate_values, excluded_values)
        current_analytics_values = _apply_exclusions(current_analytics_values, excluded_values)
        sibling_candidate_values = _apply_exclusions(sibling_candidate_values, excluded_values)
        sibling_analytics_values = _apply_exclusions(sibling_analytics_values, excluded_values)

        provenance: dict[str, str] = {}
        result: dict[str, str] = {}
        for field_name in ("serie_thema", "title_raw", "hook", "cta", "caption", "format_typ", "bereit"):
            candidates: tuple[tuple[str, str], ...]
            if field_name in {"serie_thema", "caption"}:
                candidates = (
                    ("candidate", candidate_values.get(field_name, "")),
                    ("analytics", current_analytics_values.get(field_name, "")),
                    ("sibling_candidate", sibling_candidate_values.get(field_name, "")),
                )
            elif field_name == "format_typ":
                candidates = (
                    ("candidate", candidate_values.get(field_name, "")),
                    ("analytics", current_analytics_values.get(field_name, "")),
                    ("sibling_real", sibling_format_value),
                )
            else:
                candidates = (
                    ("candidate", candidate_values.get(field_name, "")),
                    ("analytics", current_analytics_values.get(field_name, "")),
                    ("sibling_candidate", sibling_candidate_values.get(field_name, "")),
                    ("sibling_analytics", sibling_analytics_values.get(field_name, "")),
                )
            chosen_value = ""
            chosen_source = ""
            for source_name, value in candidates:
                if value:
                    chosen_value = value
                    chosen_source = source_name
                    break
            result[field_name] = chosen_value
            provenance[field_name] = chosen_source or "-"
        _log.info(
            "daily_plan autofill sources | platform=%s candidate_fields=%s analytics_fields=%s sibling_fields=%s exclusions=%s final_fields=%s provenance=%s",
            platform or "",
            {k: v for k, v in candidate_values.items() if v},
            {k: v for k, v in current_analytics_values.items() if v},
            (
                {f"candidate:{k}": v for k, v in sibling_candidate_values.items() if v}
                | {f"analytics:{k}": v for k, v in sibling_analytics_values.items() if v}
                | ({"real:format_typ": sibling_format_value} if sibling_format_value else {})
            ),
            {k: v for k, v in excluded_values.items() if v},
            {k: v for k, v in result.items() if v},
            {k: v for k, v in provenance.items() if result.get(k)},
        )
        return result

    def _load_analytics_source_values(
        self,
        *,
        platform_record_id: str | None,
        platform_table_id: str | None,
    ) -> dict[str, str]:
        empty = {
            "serie_thema": "",
            "title_raw": "",
            "hook": "",
            "cta": "",
            "caption": "",
            "format_typ": "",
            "bereit": "",
        }
        if not platform_record_id or not platform_table_id:
            return empty
        try:
            record = self._airtable_svc.get_record(
                platform_table_id,
                platform_record_id,
                project_key=_ANALYTICS_PROJECT_KEY,
            )
        except Exception as exc:
            _log.warning(
                "daily_plan autofill: analytics lookup failed | table=%s record_id=%s error_type=%s error=%s",
                platform_table_id,
                platform_record_id,
                type(exc).__name__,
                exc,
            )
            return empty
        result = _analytics_selection_snapshot(record.fields)
        _log.info(
            "daily_plan autofill: analytics lookup ok | table=%s record_id=%s non_empty=%s",
            platform_table_id,
            platform_record_id,
            {k: v for k, v in result.items() if v},
        )
        return result


def _apply_exclusions(values: dict[str, str], excluded_values: dict[str, str]) -> dict[str, str]:
    result = dict(values)
    for field_name, excluded_value in excluded_values.items():
        if not excluded_value:
            continue
        if _normalize_text(result.get(field_name)) == _normalize_text(excluded_value):
            result[field_name] = ""
    return result
