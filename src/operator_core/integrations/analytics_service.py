from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_core.integrations.airtable_service import AirtableService

_log = logging.getLogger("operator_core.integrations.analytics")

# Analytics table — pass table ID directly (Airtable accepts both name and ID)
_ANALYTICS_TABLE_ID = "tblUJH1sZOIVmNkAn"

# project_key used to route to the analytics base in settings
_ANALYTICS_PROJECT_KEY = "analytics"

_MAX_HOOK_EXAMPLES = 2
_HOOK_EXAMPLE_MAX_LEN = 60


@dataclass(frozen=True)
class AnalyticsContext:
    hook_examples: tuple[str, ...]
    dominant_cta: str
    gap: str
    cta_count: int = 0

    def is_empty(self) -> bool:
        return not self.hook_examples and not self.dominant_cta

    def to_prompt_block(self) -> str:
        """Return a short, actionable prompt block for /idea injection.

        Returns empty string if no data is available.
        """
        if self.is_empty():
            return ""

        lines = ["Aktuelle Performance Learnings (basierend auf echten Posts):"]

        if self.hook_examples:
            examples_str = ", ".join(f'"{h}"' for h in self.hook_examples)
            lines.append(f"- Stärkster Hook-Stil: persönliche Alltagsbeobachtung ({examples_str})")

        if self.dominant_cta:
            lines.append(f"- Dominanter CTA-Typ: {self.dominant_cta}")

        if self.gap:
            lines.append(f"- Lücke/Chance: {self.gap}")

        return "\n".join(lines)


_EMPTY_CONTEXT = AnalyticsContext(hook_examples=(), dominant_cta="", gap="")


class AnalyticsLoader:
    """Reads published content records from the analytics Airtable base.

    Never raises — returns empty context on any error so that /idea degrades
    gracefully to Phase 2.1 behavior when analytics are unavailable.
    """

    def __init__(self, airtable_service: "AirtableService") -> None:
        self.airtable_service = airtable_service

    def load_recent(self, *, project_key: str = _ANALYTICS_PROJECT_KEY) -> AnalyticsContext:
        """Load recent analytics records. Returns empty context on any failure."""
        try:
            record_list = self.airtable_service.list_records(
                _ANALYTICS_TABLE_ID,
                project_key=project_key,
                fields=("hook_kurz", "cta_typ"),
            )
        except Exception as exc:
            _log.warning(
                "analytics: airtable read failed | error=%s",
                exc,
            )
            return _EMPTY_CONTEXT

        hook_examples: list[str] = []
        cta_values: list[str] = []

        for record in record_list.records:
            hook = str(record.fields.get("hook_kurz") or "").strip()
            cta = str(record.fields.get("cta_typ") or "").strip()

            if hook and len(hook_examples) < _MAX_HOOK_EXAMPLES:
                truncated = hook[:_HOOK_EXAMPLE_MAX_LEN]
                if len(hook) > _HOOK_EXAMPLE_MAX_LEN:
                    truncated = truncated.rstrip() + "…"
                hook_examples.append(truncated)

            if cta:
                cta_values.append(cta)

        dominant_cta = _most_common(cta_values)
        gap = _derive_gap(cta_values)

        _log.debug(
            "analytics: loaded %d records | hook_examples=%d dominant_cta=%r",
            len(record_list.records),
            len(hook_examples),
            dominant_cta,
        )

        return AnalyticsContext(
            hook_examples=tuple(hook_examples),
            dominant_cta=dominant_cta,
            gap=gap,
            cta_count=len(cta_values),
        )


def _most_common(values: list[str]) -> str:
    """Return the most frequently occurring value, or empty string."""
    if not values:
        return ""
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda k: counts[k])


def _derive_gap(cta_values: list[str]) -> str:
    """Derive a gap signal from available CTA data."""
    if not cta_values:
        return ""
    unique_ctas = len(set(cta_values))
    # All CTAs are community questions — no product or series CTA yet
    if unique_ctas == 1:
        return "noch keine Serie oder Produkt-CTA – jetzt gut einführbar"
    return ""
