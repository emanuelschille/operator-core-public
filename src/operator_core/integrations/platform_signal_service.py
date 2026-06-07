from __future__ import annotations

import logging
from dataclasses import dataclass
from statistics import mean
from typing import TYPE_CHECKING

from operator_core.integrations.analytics_service import _derive_gap, _most_common

if TYPE_CHECKING:
    from operator_core.integrations.airtable_service import AirtableService
    from operator_core.integrations.operational_knowledge_service import OperationalKnowledgeLoader

_log = logging.getLogger("operator_core.integrations.platform_signal")

_ANALYTICS_PROJECT_KEY = "analytics"
_MAX_HOOK_EXAMPLES = 2
_HOOK_EXAMPLE_MAX_LEN = 60
_MAX_RECENT_RECORDS = 8

_OK_ANALYTICS_TABLE_KEYS: dict[str, str] = {
    "analytics_table_tiktok": "tiktok",
    "analytics_table_instagram_reel": "instagram_reel",
    "analytics_table_facebook_reel": "facebook_reel",
    "analytics_table_youtube_short": "youtube_short",
}


@dataclass(frozen=True)
class PlatformContext:
    platform_key: str
    table_id: str
    post_count: int
    dominant_cta: str
    gap: str
    hook_examples: tuple[str, ...]
    dominant_format: str = ""
    format_examples: tuple[str, ...] = ()
    numeric_summary_lines: tuple[str, ...] = ()
    numeric_fields_used: tuple[str, ...] = ()


_NUMERIC_METRIC_SPECS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("views", ("view", "aufruf"), "Views"),
    ("likes", ("like", "reaction", "react"), "Likes/Reactions"),
    ("comments", ("comment", "kommentar"), "Kommentare"),
    ("shares", ("share", "geteilt", "teilungen"), "Shares"),
    ("saves", ("save", "gespeichert"), "Saves"),
    ("watchtime", ("watch_sec", "watch_time", "watchtime", "avg watch", "durchschnittliche wiedergabezeit"), "Watchtime/Avg Watch"),
    ("completion", ("completion", "complet", "voll", "finished", "viewed_pct", "viewed pct", "watch_pct"), "Completion"),
    ("retention", ("retention", "halte", "bindung", "swiped_pct", "swipe"), "Retention"),
    ("followers_gained", ("followers gained", "follower gained", "new followers", "gewonnene follower"), "Follower gewonnen"),
    ("reach", ("reach", "reached", "accounts reached", "erreichte konten"), "Reach"),
    ("profile_visits", ("profile visit", "profilbesuch", "profile_views"), "Profilbesuche"),
)


class PlatformSignalLoader:
    """Loads per-platform analytics signals from separate Airtable tables.

    Uses only canonical live platform keys. Missing OK rows or unreadable tables
    are skipped silently so callers can treat this loader as best-effort.
    """

    def __init__(
        self,
        airtable_svc: "AirtableService",
        ok_loader: "OperationalKnowledgeLoader",
    ) -> None:
        self._airtable_svc = airtable_svc
        self._ok_loader = ok_loader

    def load_all(self, *, ok_project_key: str) -> dict[str, PlatformContext]:
        try:
            ok_ctx = self._ok_loader.load_active(project_key=ok_project_key)
        except Exception as exc:
            _log.warning("platform_signal: OK load failed | project=%s error=%s", ok_project_key, exc)
            return {}

        contexts: dict[str, PlatformContext] = {}
        for row in ok_ctx.rows:
            platform_key = _OK_ANALYTICS_TABLE_KEYS.get(row.key)
            if platform_key is None:
                continue

            table_id = row.value.strip()
            if not table_id:
                continue

            try:
                record_list = self._airtable_svc.list_records(
                    table_id,
                    project_key=_ANALYTICS_PROJECT_KEY,
                )
            except Exception as exc:
                _log.warning(
                    "platform_signal: platform table read failed | platform=%s table=%s error=%s",
                    platform_key,
                    table_id,
                    exc,
                )
                continue

            records = sorted(
                record_list.records,
                key=lambda r: str(r.created_time or ""),
                reverse=True,
            )
            recent_records = records[:_MAX_RECENT_RECORDS]

            hook_examples: list[str] = []
            cta_values: list[str] = []
            format_examples: list[str] = []
            format_values: list[str] = []
            numeric_samples: dict[str, list[float]] = {key: [] for key, _aliases, _label in _NUMERIC_METRIC_SPECS}
            numeric_field_hits: dict[str, set[str]] = {key: set() for key, _aliases, _label in _NUMERIC_METRIC_SPECS}

            for record in recent_records:
                hook = str(record.fields.get("hook_kurz") or "").strip()
                cta = str(record.fields.get("cta_typ") or "").strip()
                format_typ = str(record.fields.get("format_typ") or "").strip()

                if hook and len(hook_examples) < _MAX_HOOK_EXAMPLES:
                    truncated = hook[:_HOOK_EXAMPLE_MAX_LEN]
                    if len(hook) > _HOOK_EXAMPLE_MAX_LEN:
                        truncated = truncated.rstrip() + "…"
                    hook_examples.append(truncated)

                if cta:
                    cta_values.append(cta)
                if format_typ:
                    format_values.append(format_typ)
                    if len(format_examples) < _MAX_HOOK_EXAMPLES:
                        format_examples.append(format_typ)

                for field_name, raw_value in record.fields.items():
                    metric_key, parsed_value = _classify_numeric_metric(field_name, raw_value)
                    if metric_key and parsed_value is not None:
                        numeric_samples[metric_key].append(parsed_value)
                        numeric_field_hits[metric_key].add(str(field_name))

            numeric_summary_lines: list[str] = []
            numeric_fields_used: list[str] = []
            for metric_key, _aliases, label in _NUMERIC_METRIC_SPECS:
                values = numeric_samples[metric_key]
                if not values:
                    continue
                field_names = sorted(numeric_field_hits[metric_key])
                numeric_fields_used.extend(field_names)
                rounded = _format_metric_value(metric_key, mean(values))
                best = _format_metric_value(metric_key, max(values))
                numeric_summary_lines.append(
                    f"{label}: Ø {rounded} | best {best} | Felder: {', '.join(field_names)}"
                )

            _log.info(
                "platform_signal: context built | platform=%s table=%s post_count=%s numeric_fields=%s",
                platform_key,
                table_id,
                len(records),
                sorted(set(numeric_fields_used)),
            )

            contexts[platform_key] = PlatformContext(
                platform_key=platform_key,
                table_id=table_id,
                post_count=len(records),
                dominant_cta=_most_common(cta_values),
                gap=_derive_gap(cta_values),
                hook_examples=tuple(hook_examples),
                dominant_format=_most_common(format_values),
                format_examples=tuple(format_examples),
                numeric_summary_lines=tuple(numeric_summary_lines),
                numeric_fields_used=tuple(sorted(set(numeric_fields_used))),
            )

        return contexts


def _classify_numeric_metric(field_name: object, raw_value: object) -> tuple[str, float | None]:
    normalized_name = str(field_name or "").strip().lower()
    if not normalized_name:
        return "", None

    parsed_value = _parse_numeric_value(raw_value)
    if parsed_value is None:
        return "", None

    for metric_key, aliases, _label in _NUMERIC_METRIC_SPECS:
        if any(alias in normalized_name for alias in aliases):
            return metric_key, parsed_value
    return "", None


def _parse_numeric_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    raw = str(value or "").strip()
    if not raw:
        return None

    cleaned = raw.replace(",", ".").replace("%", "").replace("s", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format_metric_value(metric_key: str, value: float) -> str:
    if metric_key in {"completion", "retention"}:
        return f"{round(value, 1)}%"
    if metric_key == "watchtime":
        return f"{round(value, 1)}s"
    if abs(value) >= 100:
        return str(int(round(value)))
    return str(round(value, 1))
