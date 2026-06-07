from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_core.integrations.airtable_service import AirtableService

_log = logging.getLogger("operator_core.integrations.operational_knowledge")

_TABLE_NAME = "Operational Knowledge"

# /idea read priority order from spec (Section 7)
IDEA_CATEGORIES: tuple[str, ...] = ("priorities", "platform", "posting")
_DEFAULT_POSTING_TIMEZONE = "Europe/Berlin"
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OperationalKnowledgeRow:
    key: str
    label: str
    value: str
    category: str
    status: str


@dataclass(frozen=True)
class PostingScheduleRule:
    platform: str
    weekday: str
    timezone: str
    enabled: bool
    time_local: str
    condition: str
    note: str
    source_key: str
    source: str


@dataclass(frozen=True)
class OperationalKnowledgeContext:
    rows: tuple[OperationalKnowledgeRow, ...]

    def is_empty(self) -> bool:
        return len(self.rows) == 0

    def by_categories(self, *categories: str) -> tuple[OperationalKnowledgeRow, ...]:
        """Return rows matching any of the given categories, in category order."""
        cat_order = {c: i for i, c in enumerate(categories)}
        matching = [r for r in self.rows if r.category in cat_order]
        matching.sort(key=lambda r: cat_order[r.category])
        return tuple(matching)

    def to_prompt_block(self, *categories: str) -> str:
        """Format relevant rows as a named instruction block for prompt injection.

        Returns empty string if no rows match the given categories.
        """
        rows = self.by_categories(*categories)
        if not rows:
            return ""
        lines = ["Operative Wissensregeln (aktuell bindend):"]
        for row in rows:
            lines.append(f"- {row.label}: {row.value}")
        return "\n".join(lines)

    def resolve_posting_schedule(
        self,
        *,
        platform: str,
        weekday: str,
        fallback_key: str = "",
        default_time: str = "",
    ) -> PostingScheduleRule:
        normalized_platform = str(platform or "").strip().lower()
        normalized_weekday = str(weekday or "").strip().lower()
        row_key = f"posting_schedule_{normalized_platform}_{normalized_weekday}"
        fallback_time = self._resolve_fallback_time(
            fallback_key=fallback_key,
            default_time=default_time,
        )

        for row in self.rows:
            if row.key != row_key:
                continue
            parsed = _parse_schedule_value(row.value)
            if parsed is None:
                break

            enabled = _coerce_bool(parsed.get("enabled"), default=True)
            time_local = str(parsed.get("time_local") or "").strip()
            if enabled and not time_local:
                time_local = fallback_time
            return PostingScheduleRule(
                platform=str(parsed.get("platform") or normalized_platform).strip().lower(),
                weekday=str(parsed.get("weekday") or normalized_weekday).strip().lower(),
                timezone=str(parsed.get("timezone") or _DEFAULT_POSTING_TIMEZONE).strip()
                or _DEFAULT_POSTING_TIMEZONE,
                enabled=enabled,
                time_local=time_local,
                condition=str(parsed.get("condition") or "").strip(),
                note=str(parsed.get("note") or "").strip(),
                source_key=row.key,
                source="posting_schedule",
            )

        return PostingScheduleRule(
            platform=normalized_platform,
            weekday=normalized_weekday,
            timezone=_DEFAULT_POSTING_TIMEZONE,
            enabled=True,
            time_local=fallback_time,
            condition="",
            note="",
            source_key=fallback_key,
            source="posting_time_fallback" if fallback_key else "default",
        )

    def _resolve_fallback_time(self, *, fallback_key: str, default_time: str) -> str:
        for row in self.rows:
            if row.key != fallback_key or not row.value.strip():
                continue
            return row.value.strip().split()[0]
        return str(default_time or "").strip()


_EMPTY_CONTEXT = OperationalKnowledgeContext(rows=())


def _parse_schedule_value(value: str) -> Mapping[str, Any] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    return parsed


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in _TRUE_VALUES:
            return True
        if stripped in {"0", "false", "no", "off"}:
            return False
    return default


class OperationalKnowledgeLoader:
    """Reads active rows from the Operational Knowledge Airtable table.

    Never raises — returns empty context on any Airtable error so that all
    callers can treat the loader as a best-effort enrichment, not a hard dep.
    """

    def __init__(self, airtable_service: "AirtableService") -> None:
        self.airtable_service = airtable_service

    def load_active(self, *, project_key: str) -> OperationalKnowledgeContext:
        """Load all active rows. Returns empty context on any failure."""
        try:
            record_list = self.airtable_service.list_records(
                _TABLE_NAME,
                project_key=project_key,
                filter_formula='{Status} = "active"',
            )
        except Exception as exc:
            _log.warning(
                "operational_knowledge: airtable read failed | project=%s error=%s",
                project_key,
                exc,
            )
            return _EMPTY_CONTEXT

        rows: list[OperationalKnowledgeRow] = []
        for record in record_list.records:
            key = str(record.fields.get("Key") or "").strip()
            value = str(record.fields.get("Value") or "").strip()
            if not key or not value:
                continue
            rows.append(
                OperationalKnowledgeRow(
                    key=key,
                    label=str(record.fields.get("Label") or key).strip(),
                    value=value,
                    category=str(record.fields.get("Category") or "").strip(),
                    status=str(record.fields.get("Status") or "").strip(),
                )
            )

        _log.debug(
            "operational_knowledge: loaded %d active rows | project=%s",
            len(rows),
            project_key,
        )
        return OperationalKnowledgeContext(rows=tuple(rows))
