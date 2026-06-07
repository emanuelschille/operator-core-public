from __future__ import annotations

import dataclasses
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from operator_core.core.analysis_foundation.models import WeeklyAnalysisArtifact

if TYPE_CHECKING:
    from operator_core.projects.docs import ProjectDocsLoader
    from operator_core.integrations.daily_plan_service import DailyPlanService, TodayPlanSnapshot
    from operator_core.integrations.openai_service import OpenAIService
    from operator_core.integrations.operational_knowledge_service import (
        OperationalKnowledgeContext,
        OperationalKnowledgeLoader,
    )
    from operator_core.integrations.platform_signal_service import PlatformContext, PlatformSignalLoader
    from operator_core.integrations.weekly_analysis_persistence import WeeklyAnalysisPersistenceService

_log = logging.getLogger("operator_core.integrations.daily_plan_generation_service")

# Fields that can be AI-generated when still empty after source-based autofill.
# Priority order follows context-building dependencies: theme/title/hook/cta first,
# then format, then caption last when needed.
_GENERATION_TARGETS: tuple[str, ...] = (
    "serie_thema",
    "title_raw",
    "hook",
    "cta",
    "format_typ",
    "caption",
)

_PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram_reel": "Instagram Reels",
    "facebook_reel": "Facebook Reels",
    "youtube_short": "YouTube Shorts",
}


class DailyPlanGenerationService:
    """Second-pass autofill: generates values for fields still empty after source-based fill.

    Principles:
    - Never overwrites existing values.
    - Only generates still-empty targets and uses already-set row values as steering context.
    - Returns snapshot unchanged if OpenAI is unavailable or generation fails.
    - Keeps prompts tight: platform + filled fields + OK rules as context.
    """

    def __init__(
        self,
        daily_plan_service: "DailyPlanService",
        openai_service: "OpenAIService | None",
        ok_loader: "OperationalKnowledgeLoader | None",
        platform_signal_loader: "PlatformSignalLoader | None" = None,
        weekly_analysis_loader: "WeeklyAnalysisPersistenceService | None" = None,
        docs_loader: "ProjectDocsLoader | None" = None,
    ) -> None:
        self._daily_plan_svc = daily_plan_service
        self._openai_svc = openai_service
        self._ok_loader = ok_loader
        self._platform_signal_loader = platform_signal_loader
        self._weekly_analysis_loader = weekly_analysis_loader
        self._docs_loader = docs_loader
        self._last_generated_outputs: dict[tuple[str, str], str] = {}

    def fill_missing_fields(
        self,
        *,
        project_key: str,
        snapshot: "TodayPlanSnapshot",
        siblings: tuple["TodayPlanSnapshot", ...] = (),
    ) -> "TodayPlanSnapshot":
        """Generate values for empty generation targets. Returns snapshot unchanged on any failure."""
        if self._openai_svc is None:
            return snapshot

        missing = [f for f in _GENERATION_TARGETS if not getattr(snapshot, f, "")]
        if not missing:
            return snapshot

        ok_ctx: "OperationalKnowledgeContext | None" = None
        if self._ok_loader is not None:
            try:
                ok_ctx = self._ok_loader.load_active(project_key=project_key)
            except Exception as exc:
                _log.warning("daily_plan_gen: ok_loader failed | project=%s error=%s", project_key, exc)

        platform_ctx: "PlatformContext | None" = None
        if self._platform_signal_loader is not None and snapshot.platform:
            try:
                platform_ctx = self._platform_signal_loader.load_all(ok_project_key=project_key).get(
                    snapshot.platform
                )
                if platform_ctx is not None:
                    _log.info(
                        "daily_plan_gen: platform analytics context loaded | record_id=%s platform=%s post_count=%s dominant_cta=%r dominant_format=%r",
                        snapshot.record_id,
                        snapshot.platform,
                        platform_ctx.post_count,
                        platform_ctx.dominant_cta,
                        platform_ctx.dominant_format,
                    )
            except Exception as exc:
                _log.warning(
                    "daily_plan_gen: platform analytics load failed | project=%s platform=%s error=%s",
                    project_key,
                    snapshot.platform,
                    exc,
                )

        rules_block = ""
        if self._docs_loader is not None:
            try:
                content_doc = self._docs_loader.load(project_key, "content_rules")
                rules_block = _build_content_rules_block(content_doc.content)
                if rules_block:
                    _log.info(
                        "daily_plan_gen: content rules context loaded | record_id=%s sections=%s",
                        snapshot.record_id,
                        "caption/cta/formats/tone",
                    )
            except Exception as exc:
                _log.warning(
                    "daily_plan_gen: docs rules load failed | project=%s error=%s",
                    project_key,
                    exc,
                )

        weekly_analysis = self._load_fresh_weekly_analysis(project_key=project_key)

        freshness_token = _build_freshness_token()

        original_snapshot = snapshot

        generated: dict[str, str] = {}
        previous_generated = {
            field_name: self._last_generated_outputs.get((snapshot.record_id, field_name), "")
            for field_name in missing
        }

        if "serie_thema" in missing:
            value = self._generate_serie_thema(
                snapshot=snapshot,
                siblings=siblings,
                ok_ctx=ok_ctx,
                platform_ctx=platform_ctx,
                rules_block=rules_block,
                weekly_analysis=weekly_analysis,
                freshness_token=freshness_token,
                previous_value=previous_generated.get("serie_thema", ""),
            )
            if value:
                generated["serie_thema"] = value
                snapshot = dataclasses.replace(snapshot, serie_thema=value)
                _log.info(
                    "daily_plan_gen: serie_thema generated | record_id=%s value=%r",
                    snapshot.record_id,
                    value,
                )

        if "title_raw" in missing:
            value = self._generate_title_raw(
                snapshot=snapshot,
                ok_ctx=ok_ctx,
                platform_ctx=platform_ctx,
                rules_block=rules_block,
                weekly_analysis=weekly_analysis,
                freshness_token=freshness_token,
                previous_value=previous_generated.get("title_raw", ""),
            )
            if value:
                generated["title_raw"] = value
                snapshot = dataclasses.replace(snapshot, title_raw=value)
                _log.info(
                    "daily_plan_gen: title_raw generated | record_id=%s value=%r",
                    snapshot.record_id,
                    value,
                )

        if "hook" in missing:
            value = self._generate_hook(
                snapshot=snapshot,
                ok_ctx=ok_ctx,
                platform_ctx=platform_ctx,
                rules_block=rules_block,
                weekly_analysis=weekly_analysis,
                freshness_token=freshness_token,
                previous_value=previous_generated.get("hook", ""),
            )
            if value:
                generated["hook"] = value
                snapshot = dataclasses.replace(snapshot, hook=value)
                _log.info(
                    "daily_plan_gen: hook generated | record_id=%s value=%r",
                    snapshot.record_id,
                    value,
                )

        if "cta" in missing:
            value = self._generate_cta(
                snapshot=snapshot,
                ok_ctx=ok_ctx,
                platform_ctx=platform_ctx,
                rules_block=rules_block,
                weekly_analysis=weekly_analysis,
                freshness_token=freshness_token,
                previous_value=previous_generated.get("cta", ""),
            )
            if value:
                generated["cta"] = value
                snapshot = dataclasses.replace(snapshot, cta=value)
                _log.info(
                    "daily_plan_gen: cta generated | record_id=%s value=%r",
                    snapshot.record_id,
                    value,
                )

        if "format_typ" in missing:
            value = self._generate_format_typ(
                snapshot=snapshot,
                ok_ctx=ok_ctx,
                platform_ctx=platform_ctx,
                rules_block=rules_block,
                weekly_analysis=weekly_analysis,
                freshness_token=freshness_token,
                previous_value=previous_generated.get("format_typ", ""),
            )
            if value:
                generated["format_typ"] = value
                snapshot = dataclasses.replace(snapshot, format_typ=value)
                _log.info(
                    "daily_plan_gen: format_typ generated | record_id=%s value=%r",
                    snapshot.record_id,
                    value,
                )

        if "caption" in missing:
            value = self._generate_caption(
                snapshot=snapshot,
                ok_ctx=ok_ctx,
                platform_ctx=platform_ctx,
                rules_block=rules_block,
                weekly_analysis=weekly_analysis,
                freshness_token=freshness_token,
                previous_value=previous_generated.get("caption", ""),
            )
            if value:
                generated["caption"] = value
                _log.info(
                    "daily_plan_gen: caption generated | record_id=%s length=%s",
                    snapshot.record_id,
                    len(value),
                )

        if not generated:
            return snapshot

        try:
            patched = self._daily_plan_svc.patch_fields(
                project_key=project_key,
                record_id=snapshot.record_id,
                fields=generated,
                current=snapshot,
            )
            for field_name, value in generated.items():
                self._last_generated_outputs[(snapshot.record_id, field_name)] = value
            return patched
        except Exception as exc:
            _log.warning(
                "daily_plan_gen: patch failed | record_id=%s fields=%s error=%s",
                snapshot.record_id,
                list(generated.keys()),
                exc,
            )
            return original_snapshot

    def get_non_repetition_exclusions(self, *, record_id: str) -> dict[str, str]:
        return {
            "serie_thema": self._last_generated_outputs.get((record_id, "serie_thema"), ""),
            "title_raw": self._last_generated_outputs.get((record_id, "title_raw"), ""),
            "hook": self._last_generated_outputs.get((record_id, "hook"), ""),
            "cta": self._last_generated_outputs.get((record_id, "cta"), ""),
            "caption": self._last_generated_outputs.get((record_id, "caption"), ""),
            "format_typ": self._last_generated_outputs.get((record_id, "format_typ"), ""),
        }

    # ------------------------------------------------------------------
    # Internal generators
    # ------------------------------------------------------------------

    def _generate_serie_thema(
        self,
        *,
        snapshot: "TodayPlanSnapshot",
        siblings: tuple["TodayPlanSnapshot", ...],
        ok_ctx: "OperationalKnowledgeContext | None",
        platform_ctx: "PlatformContext | None",
        rules_block: str,
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
        freshness_token: str,
        previous_value: str,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(snapshot.platform or "", snapshot.platform or "Plattform")

        sibling_themes = ", ".join(s.serie_thema for s in siblings if s.serie_thema)

        ok_block = ""
        if ok_ctx is not None and not ok_ctx.is_empty():
            ok_block = ok_ctx.to_prompt_block("priorities", "platform")

        analytics_block = _build_platform_analytics_block(platform_ctx)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)
        variation_block = _build_variation_block(snapshot=snapshot, freshness_token=freshness_token)
        locked_context_block = _build_locked_context_block(snapshot=snapshot, exclude_fields={"serie_thema"})

        system_prompt = (
            f"Du bist ein Content-Tagger für das Projekt everydayengel auf {platform_label}.\n"
            "Aufgabe: Bestimme das passende Serie/Thema für den folgenden Post-Inhalt.\n"
            "Das Serie/Thema ist ein kurzer Begriff (max. 3 Wörter), der den Themenbereich benennt.\n"
            + (f"\n{ok_block}\n" if ok_block else "")
            + (f"\n{analytics_block}\n" if analytics_block else "")
            + (f"\n{weekly_block}\n" if weekly_block else "")
            + (f"\n{rules_block}\n" if rules_block else "")
            + (f"\n{locked_context_block}\n" if locked_context_block else "")
            + (
                f"\nHeutige Themen anderer Plattformen (zur Orientierung): {sibling_themes}\n"
                if sibling_themes
                else ""
            )
            + f"\nGenerierungslauf: {freshness_token}\n"
            + variation_block
            + "\nAntworte ausschließlich auf Deutsch, exakt in diesem Format, keine Erklärungen:\n"
            "Serie/Thema: <Thema>"
        )

        content_lines = []
        if snapshot.title_raw:
            content_lines.append(f"Titel: {snapshot.title_raw}")
        if snapshot.hook:
            content_lines.append(f"Hook: {snapshot.hook}")
        if snapshot.cta:
            content_lines.append(f"CTA: {snapshot.cta}")
        user_prompt = "\n".join(content_lines) if content_lines else "Inhalt nicht verfügbar"

        try:
            return self._complete_non_repeating(
                field_name="serie_thema",
                snapshot=snapshot,
                previous_value=previous_value,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.5,
                parse_key="Serie/Thema",
            )
        except Exception as exc:
            _log.warning("daily_plan_gen: serie_thema openai failed | error=%s", exc)
            return ""

    def _generate_format_typ(
        self,
        *,
        snapshot: "TodayPlanSnapshot",
        ok_ctx: "OperationalKnowledgeContext | None",
        platform_ctx: "PlatformContext | None",
        rules_block: str,
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
        freshness_token: str,
        previous_value: str,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(snapshot.platform or "", snapshot.platform or "Plattform")

        ok_block = ""
        if ok_ctx is not None and not ok_ctx.is_empty():
            ok_block = ok_ctx.to_prompt_block("platform", "posting")
        analytics_block = _build_platform_analytics_block(platform_ctx)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)
        variation_block = _build_variation_block(snapshot=snapshot, freshness_token=freshness_token)
        locked_context_block = _build_locked_context_block(snapshot=snapshot, exclude_fields={"format_typ"})

        system_prompt = (
            f"Du bestimmst das passende Content-Format für {platform_label} im Projekt everydayengel.\n"
            "Aufgabe: Wähle genau ein sinnvolles Format für diesen Post.\n"
            "Nutze nur kurze, sichtbare Formatwerte wie z.B. Talking Head, Reel, Carousel, Story, YouTube Short, Interview.\n"
            "Nenne kein anderes Netzwerk im Format, wenn es nicht zur Zielplattform passt.\n"
            + (f"\n{ok_block}\n" if ok_block else "")
            + (f"\n{analytics_block}\n" if analytics_block else "")
            + (f"\n{weekly_block}\n" if weekly_block else "")
            + (f"\n{rules_block}\n" if rules_block else "")
            + (f"\n{locked_context_block}\n" if locked_context_block else "")
            + f"\nGenerierungslauf: {freshness_token}\n"
            + variation_block
            + "\nAntworte ausschließlich auf Deutsch, exakt in diesem Format, keine Erklärungen:\n"
            "Format: <Format>"
        )

        content_lines = [f"Plattform: {platform_label}"]
        if snapshot.title_raw:
            content_lines.append(f"Titel: {snapshot.title_raw}")
        if snapshot.hook:
            content_lines.append(f"Hook: {snapshot.hook}")
        if snapshot.cta:
            content_lines.append(f"CTA: {snapshot.cta}")
        if snapshot.caption:
            content_lines.append(f"Caption: {snapshot.caption}")
        user_prompt = "\n".join(content_lines)

        try:
            return self._complete_non_repeating(
                field_name="format_typ",
                snapshot=snapshot,
                previous_value=previous_value,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.4,
                parse_key="Format",
            )
        except Exception as exc:
            _log.warning("daily_plan_gen: format_typ openai failed | error=%s", exc)
            return ""

    def _generate_title_raw(
        self,
        *,
        snapshot: "TodayPlanSnapshot",
        ok_ctx: "OperationalKnowledgeContext | None",
        platform_ctx: "PlatformContext | None",
        rules_block: str,
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
        freshness_token: str,
        previous_value: str,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(snapshot.platform or "", snapshot.platform or "Plattform")
        ok_block = ""
        if ok_ctx is not None and not ok_ctx.is_empty():
            ok_block = ok_ctx.to_prompt_block("priorities", "platform", "posting")
        analytics_block = _build_platform_analytics_block(platform_ctx)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)
        variation_block = _build_variation_block(snapshot=snapshot, freshness_token=freshness_token)
        locked_context_block = _build_locked_context_block(snapshot=snapshot, exclude_fields={"title_raw"})

        system_prompt = (
            f"Du bist ein Title-Assistent für {platform_label} im Projekt everydayengel.\n"
            "Aufgabe: Formuliere genau einen passenden Title für den bestehenden Tagesplan-Kontext.\n"
            "Der Title soll zur bereits gesetzten Caption, zum Hook, zur CTA und zum Thema passen, nicht davon wegführen.\n"
            + (f"\n{ok_block}\n" if ok_block else "")
            + (f"\n{analytics_block}\n" if analytics_block else "")
            + (f"\n{weekly_block}\n" if weekly_block else "")
            + (f"\n{rules_block}\n" if rules_block else "")
            + (f"\n{locked_context_block}\n" if locked_context_block else "")
            + f"\nGenerierungslauf: {freshness_token}\n"
            + variation_block
            + "\nAntworte ausschließlich auf Deutsch, exakt in diesem Format, keine Erklärungen:\n"
            "Title: <Title>"
        )

        content_lines = [f"Plattform: {platform_label}"]
        if snapshot.serie_thema:
            content_lines.append(f"Serie/Thema: {snapshot.serie_thema}")
        if snapshot.hook:
            content_lines.append(f"Hook: {snapshot.hook}")
        if snapshot.cta:
            content_lines.append(f"CTA: {snapshot.cta}")
        if snapshot.caption:
            content_lines.append(f"Caption: {snapshot.caption}")
        if snapshot.format_typ:
            content_lines.append(f"Format: {snapshot.format_typ}")
        user_prompt = "\n".join(content_lines)

        try:
            return self._complete_non_repeating(
                field_name="title_raw",
                snapshot=snapshot,
                previous_value=previous_value,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.6,
                parse_key="Title",
            )
        except Exception as exc:
            _log.warning("daily_plan_gen: title_raw openai failed | error=%s", exc)
            return ""

    def _generate_hook(
        self,
        *,
        snapshot: "TodayPlanSnapshot",
        ok_ctx: "OperationalKnowledgeContext | None",
        platform_ctx: "PlatformContext | None",
        rules_block: str,
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
        freshness_token: str,
        previous_value: str,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(snapshot.platform or "", snapshot.platform or "Plattform")
        ok_block = ""
        if ok_ctx is not None and not ok_ctx.is_empty():
            ok_block = ok_ctx.to_prompt_block("priorities", "platform", "posting")
        analytics_block = _build_platform_analytics_block(platform_ctx)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)
        variation_block = _build_variation_block(snapshot=snapshot, freshness_token=freshness_token)
        locked_context_block = _build_locked_context_block(snapshot=snapshot, exclude_fields={"hook"})

        system_prompt = (
            f"Du bist ein Hook-Assistent für {platform_label} im Projekt everydayengel.\n"
            "Aufgabe: Formuliere genau einen Hook, der zum bestehenden Tagesplan-Kontext passt.\n"
            "Der Hook muss sichtbar zur vorhandenen Caption, zum Title und zur CTA passen.\n"
            + (f"\n{ok_block}\n" if ok_block else "")
            + (f"\n{analytics_block}\n" if analytics_block else "")
            + (f"\n{weekly_block}\n" if weekly_block else "")
            + (f"\n{rules_block}\n" if rules_block else "")
            + (f"\n{locked_context_block}\n" if locked_context_block else "")
            + f"\nGenerierungslauf: {freshness_token}\n"
            + variation_block
            + "\nAntworte ausschließlich auf Deutsch, exakt in diesem Format, keine Erklärungen:\n"
            "Hook: <Hook>"
        )

        content_lines = [f"Plattform: {platform_label}"]
        if snapshot.serie_thema:
            content_lines.append(f"Serie/Thema: {snapshot.serie_thema}")
        if snapshot.title_raw:
            content_lines.append(f"Title: {snapshot.title_raw}")
        if snapshot.cta:
            content_lines.append(f"CTA: {snapshot.cta}")
        if snapshot.caption:
            content_lines.append(f"Caption: {snapshot.caption}")
        if snapshot.format_typ:
            content_lines.append(f"Format: {snapshot.format_typ}")
        user_prompt = "\n".join(content_lines)

        try:
            return self._complete_non_repeating(
                field_name="hook",
                snapshot=snapshot,
                previous_value=previous_value,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7,
                parse_key="Hook",
            )
        except Exception as exc:
            _log.warning("daily_plan_gen: hook openai failed | error=%s", exc)
            return ""

    def _generate_cta(
        self,
        *,
        snapshot: "TodayPlanSnapshot",
        ok_ctx: "OperationalKnowledgeContext | None",
        platform_ctx: "PlatformContext | None",
        rules_block: str,
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
        freshness_token: str,
        previous_value: str,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(snapshot.platform or "", snapshot.platform or "Plattform")
        ok_block = ""
        if ok_ctx is not None and not ok_ctx.is_empty():
            ok_block = ok_ctx.to_prompt_block("priorities", "platform", "posting")
        analytics_block = _build_platform_analytics_block(platform_ctx)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)
        variation_block = _build_variation_block(snapshot=snapshot, freshness_token=freshness_token)
        locked_context_block = _build_locked_context_block(snapshot=snapshot, exclude_fields={"cta"})

        system_prompt = (
            f"Du bist ein CTA-Assistent für {platform_label} im Projekt everydayengel.\n"
            "Aufgabe: Formuliere genau eine CTA-Zeile für den bestehenden Tagesplan-Kontext.\n"
            "Die CTA muss zur bereits gesetzten Caption, zum Hook, zum Title und zum Plattform-Kontext passen.\n"
            "Sie soll nicht losgelöst neu wirken.\n"
            + (f"\n{ok_block}\n" if ok_block else "")
            + (f"\n{analytics_block}\n" if analytics_block else "")
            + (f"\n{weekly_block}\n" if weekly_block else "")
            + (f"\n{rules_block}\n" if rules_block else "")
            + (f"\n{locked_context_block}\n" if locked_context_block else "")
            + f"\nGenerierungslauf: {freshness_token}\n"
            + variation_block
            + "\nAntworte ausschließlich auf Deutsch, exakt in diesem Format, keine Erklärungen:\n"
            "CTA: <CTA>"
        )

        content_lines = [f"Plattform: {platform_label}"]
        if snapshot.serie_thema:
            content_lines.append(f"Serie/Thema: {snapshot.serie_thema}")
        if snapshot.title_raw:
            content_lines.append(f"Title: {snapshot.title_raw}")
        if snapshot.hook:
            content_lines.append(f"Hook: {snapshot.hook}")
        if snapshot.caption:
            content_lines.append(f"Caption: {snapshot.caption}")
        if snapshot.format_typ:
            content_lines.append(f"Format: {snapshot.format_typ}")
        user_prompt = "\n".join(content_lines)

        try:
            return self._complete_non_repeating(
                field_name="cta",
                snapshot=snapshot,
                previous_value=previous_value,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.6,
                parse_key="CTA",
            )
        except Exception as exc:
            _log.warning("daily_plan_gen: cta openai failed | error=%s", exc)
            return ""

    def _generate_caption(
        self,
        *,
        snapshot: "TodayPlanSnapshot",
        ok_ctx: "OperationalKnowledgeContext | None",
        platform_ctx: "PlatformContext | None",
        rules_block: str,
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
        freshness_token: str,
        previous_value: str,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(snapshot.platform or "", snapshot.platform or "Plattform")

        ok_block = ""
        if ok_ctx is not None and not ok_ctx.is_empty():
            ok_block = ok_ctx.to_prompt_block("priorities", "platform", "posting")
        analytics_block = _build_platform_analytics_block(platform_ctx)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)
        variation_block = _build_variation_block(snapshot=snapshot, freshness_token=freshness_token)
        locked_context_block = _build_locked_context_block(snapshot=snapshot, exclude_fields={"caption"})

        system_prompt = (
            f"Du bist ein Caption-Assistent für {platform_label} im Projekt everydayengel.\n"
            "Aufgabe: Schreibe eine kurze, passende Caption für das folgende Video.\n"
            "Die Caption soll authentisch klingen, zum Hook passen und den CTA natürlich einbauen.\n"
            "Für TikTok/Reels: sehr kurz (1–2 Sätze + passende Hashtags).\n"
            + (f"\n{ok_block}\n" if ok_block else "")
            + (f"\n{analytics_block}\n" if analytics_block else "")
            + (f"\n{weekly_block}\n" if weekly_block else "")
            + (f"\n{rules_block}\n" if rules_block else "")
            + (f"\n{locked_context_block}\n" if locked_context_block else "")
            + f"\nGenerierungslauf: {freshness_token}\n"
            + variation_block
            + "\nAntworte ausschließlich auf Deutsch, exakt in diesem Format, keine Erklärungen:\n"
            "Caption: <Caption-Text>"
        )

        content_lines = []
        if snapshot.serie_thema:
            content_lines.append(f"Serie/Thema: {snapshot.serie_thema}")
        if snapshot.title_raw:
            content_lines.append(f"Titel: {snapshot.title_raw}")
        if snapshot.hook:
            content_lines.append(f"Hook: {snapshot.hook}")
        if snapshot.cta:
            content_lines.append(f"CTA: {snapshot.cta}")
        if snapshot.format_typ:
            content_lines.append(f"Format: {snapshot.format_typ}")
        user_prompt = "\n".join(content_lines) if content_lines else "Inhalt nicht verfügbar"

        try:
            return self._complete_non_repeating(
                field_name="caption",
                snapshot=snapshot,
                previous_value=previous_value,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.8,
                parse_key="Caption",
            )
        except Exception as exc:
            _log.warning("daily_plan_gen: caption openai failed | error=%s", exc)
            return ""

    def _complete_non_repeating(
        self,
        *,
        field_name: str,
        snapshot: "TodayPlanSnapshot",
        previous_value: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        parse_key: str,
    ) -> str:
        previous_norm = _normalize_generation_text(previous_value)
        prompts = [system_prompt]
        if previous_value:
            prompts[0] = (
                system_prompt
                + "\nVermeide den letzten generierten Vorschlag für dieses Feld."
                + f"\nLetzter Vorschlag: {previous_value}\n"
            )
            prompts.append(
                system_prompt
                + "\nDer letzte Vorschlag wurde zu ähnlich wiederholt und ist jetzt verboten."
                + f"\nVerbotener Vorschlag: {previous_value}\n"
                + "Liefere jetzt eine klar anders formulierte, aber weiterhin passende Variante.\n"
            )
            prompts.append(
                system_prompt
                + "\nDer letzte Vorschlag war weiterhin zu ähnlich."
                + f"\nWeiterhin verboten: {previous_value}\n"
                + "Wähle jetzt bewusst einen anderen Angle, eine andere Satzmelodie oder eine andere CTA-Richtung, ohne den Themenkern zu verlieren.\n"
            )

        for attempt, prompt in enumerate(prompts, start=1):
            response = self._openai_svc.complete_messages(  # type: ignore[union-attr]
                system_prompt=prompt,
                user_prompt=user_prompt,
                temperature=temperature,
            )
            value = _parse_single_field(response.output_text, parse_key)
            if not value:
                continue
            if previous_norm and _is_too_similar(value, previous_value):
                _log.info(
                    "daily_plan_gen: repeated value rejected | record_id=%s field=%s attempt=%s",
                    snapshot.record_id,
                    field_name,
                    attempt,
                )
                continue
            return value
        return ""

    def _load_fresh_weekly_analysis(self, *, project_key: str) -> WeeklyAnalysisArtifact | None:
        """Load the latest weekly analysis and apply the 10-day staleness guard."""
        if self._weekly_analysis_loader is None:
            return None

        try:
            weekly = self._weekly_analysis_loader.load_latest(project_key=project_key)
            if weekly is None:
                return None

            # fromisoformat handles Z in 3.11+, but we normalize for safety
            gen_dt = datetime.fromisoformat(weekly.generated_at.replace("Z", "+00:00"))
            if gen_dt.tzinfo is None:
                gen_dt = gen_dt.replace(tzinfo=timezone.utc)

            age = datetime.now(timezone.utc) - gen_dt
            if age > timedelta(days=10):
                _log.info(
                    "daily_plan_gen: ignoring stale weekly analysis | project=%s analysis_id=%s age_days=%s",
                    project_key,
                    weekly.analysis_id,
                    age.days,
                )
                return None

            return weekly
        except Exception as exc:
            _log.warning(
                "daily_plan_gen: weekly analysis load failed | project=%s error=%s",
                project_key,
                exc,
            )
            return None

    def _build_weekly_analysis_prompt_block(self, artifact: WeeklyAnalysisArtifact | None) -> str:
        if not artifact:
            return ""

        lines = ["Strategische Wochen-Analyse (Leitplanken):"]
        if artifact.key_winners:
            lines.append("Gewinner-Muster: " + " | ".join(artifact.key_winners))
        if artifact.weak_patterns:
            lines.append("Zu vermeiden: " + " | ".join(artifact.weak_patterns))
        if artifact.recommended_content_directions:
            lines.append("Empfohlene Richtungen: " + " | ".join(artifact.recommended_content_directions))
        if artifact.recommended_hook_directions:
            lines.append("Empfohlene Hooks: " + " | ".join(artifact.recommended_hook_directions))
        if artifact.recommended_cta_directions:
            lines.append("Empfohlene CTAs: " + " | ".join(artifact.recommended_cta_directions))

        return "\n".join(lines) + "\n"


def _parse_single_field(output_text: str, key: str) -> str:
    """Extract the value of a single 'Key: value' line from model output."""
    for line in output_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(f"{key.lower()}:"):
            value = stripped[len(key) + 1:].strip()
            if value:
                return value
    return ""


def _build_platform_analytics_block(platform_ctx: "PlatformContext | None") -> str:
    if platform_ctx is None:
        return ""

    lines = [
        "Aktuelle Plattform-Signale aus der Analytics-Base:",
        f"- Anzahl bestehender Posts: {platform_ctx.post_count}",
    ]
    if platform_ctx.hook_examples:
        lines.append(f"- Beispiel-Hooks: {', '.join(platform_ctx.hook_examples)}")
    if platform_ctx.dominant_cta:
        lines.append(f"- Häufiger CTA-Typ: {platform_ctx.dominant_cta}")
    if platform_ctx.dominant_format:
        lines.append(f"- Häufiges Format: {platform_ctx.dominant_format}")
    if platform_ctx.format_examples:
        lines.append(f"- Format-Beispiele: {', '.join(platform_ctx.format_examples)}")
    if platform_ctx.gap:
        lines.append(f"- Lücke/Chance: {platform_ctx.gap}")
    if platform_ctx.numeric_summary_lines:
        lines.append("- Numerische Performance-Signale:")
        lines.extend(f"  - {line}" for line in platform_ctx.numeric_summary_lines)
    return "\n".join(lines)


def _build_content_rules_block(content: str) -> str:
    from operator_core.core.knowledge_ops.doc_reader import extract_section, list_items

    lines = ["Aktuelle Projektregeln aus content_rules:"]

    caption_text = extract_section(content, "Caption Rules") or ""
    caption_items = list_items(caption_text, max_items=4)
    if caption_items:
        lines.append(f"- Caption-Regeln: {' | '.join(caption_items)}")

    cta_text = extract_section(content, "CTA Content Rule") or ""
    cta_items = list_items(cta_text, max_items=4)
    if cta_items:
        lines.append(f"- CTA-Regeln: {' | '.join(cta_items)}")

    formats_text = extract_section(content, "Primary Content Formats") or ""
    format_items = list_items(formats_text, max_items=4)
    if format_items:
        lines.append(f"- Primäre Formate: {' | '.join(format_items)}")

    tone_text = extract_section(content, "Content Tone") or ""
    tone_items = list_items(tone_text, max_items=4)
    if tone_items:
        lines.append(f"- Ton: {' | '.join(tone_items)}")

    return "\n".join(lines) if len(lines) > 1 else ""


def _build_locked_context_block(*, snapshot: "TodayPlanSnapshot", exclude_fields: set[str]) -> str:
    lines: list[str] = []
    field_map = (
        ("serie_thema", "Serie/Thema"),
        ("title_raw", "Title"),
        ("hook", "Hook"),
        ("cta", "CTA"),
        ("caption", "Caption"),
        ("format_typ", "Format"),
        ("bereit", "Bereit"),
    )
    for field_name, label in field_map:
        if field_name in exclude_fields:
            continue
        value = getattr(snapshot, field_name, "") or ""
        if value:
            lines.append(f"- {label}: {value}")
    if not lines:
        return ""
    return "Bereits gesetzte Tagesplan-Felder. Nicht überschreiben, sondern daran anpassen:\n" + "\n".join(lines)


def _build_freshness_token() -> str:
    return str(time.time_ns())


def _build_variation_block(*, snapshot: "TodayPlanSnapshot", freshness_token: str) -> str:
    directives = (
        "Für diesen Lauf bevorzuge eine beobachtende, alltagsnahe Formulierung statt einer neutralen Standardform.",
        "Für diesen Lauf bevorzuge eine fragende, dialogische Formulierung statt einer neutralen Standardform.",
        "Für diesen Lauf bevorzuge eine mini-narrative, konkrete Formulierung statt einer neutralen Standardform.",
        "Für diesen Lauf bevorzuge eine direkt-nützliche, praktische Formulierung statt einer neutralen Standardform.",
    )
    basis = f"{snapshot.record_id}:{snapshot.platform or ''}:{freshness_token}"
    idx = abs(hash(basis)) % len(directives)
    return directives[idx] + "\nVermeide unnötig ähnliche Wiederholung zu einem früheren Lauf, wenn mehrere gute Varianten möglich sind.\n"


def _normalize_generation_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _is_too_similar(left: str, right: str) -> bool:
    left_norm = _normalize_generation_text(left)
    right_norm = _normalize_generation_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    if left_norm in right_norm or right_norm in left_norm:
        return True
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return overlap >= 0.8
