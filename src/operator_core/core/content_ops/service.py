from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import logging
import re
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from operator_core.core.analysis_foundation.models import AnalysisFoundationResult, AnalysisSnapshot, EvidencePack, ModelExecutionMeta, WeeklyAnalysisArtifact, WriterBrief
from operator_core.core.knowledge_ops.doc_reader import (
    extract_section,
    first_sentences,
    list_items,
    trim,
)
from operator_core.core.content_ops.models import ContentOpResult, FoundationCaptionResult, FoundationCtaResult, FoundationDraftResult, FoundationFollowupResult, FoundationHookResult, FoundationIdeaResult, FoundationSerieResult, FoundationTitleResult, SUPPORTED_CONTENT_ACTIONS
from operator_core.core.content_ops.proposal_store import ContentProposal
from operator_core.core.routing.writer_routing import WriterRoutingService
from operator_core.core.content_ops.correction_capture import (
    CommercialClass,
    CommercialClassLog,
    CommercialLogEntry,
    CorrectionFileRepository,
    CorrectionStatus,
    classify_commercial,
)
from operator_core.core.content_ops.duplicate_guard import DuplicateRiskGuard, IdeaDistiller, IdeaHistoryReference, IdeaQualityGate

if TYPE_CHECKING:
    from operator_core.integrations.airtable_service import AirtableService
    from operator_core.integrations.openai_service import OpenAIService
    from operator_core.integrations.operational_knowledge_service import (
        OperationalKnowledgeContext,
        OperationalKnowledgeLoader,
    )
    from operator_core.integrations.analytics_service import (
        AnalyticsContext,
        AnalyticsLoader,
    )
    from operator_core.integrations.platform_signal_service import (
        PlatformContext,
        PlatformSignalLoader,
    )
    from operator_core.integrations.weekly_analysis_persistence import WeeklyAnalysisPersistenceService
    from operator_core.projects.docs import ProjectDocsLoader

_log = logging.getLogger("operator_core.core.content_ops")
_REJECTED_SAME_CORE_FALLBACK_SUMMARY = (
    "Diese Idee wurde gerade in fast diesem Kern verworfen. "
    "Gib mir bitte einen neuen Winkel oder nutze /idea Neue Idee."
)

# Airtable target tables (per operational_semantics object catalog)
_CONTENT_IDEAS_TABLE = "Content Ideas"
_CONTENT_DRAFTS_TABLE = "Content Drafts"
_DAILY_PLANS_TABLE = "Daily Plans"
_CONTENT_HOOKS_TABLE = "Content Hooks"
_CONTENT_CAPTIONS_TABLE = "Content Captions"
_PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram_reel": "Instagram Reels",
    "facebook_reel": "Facebook Reels",
    "youtube_short": "YouTube Shorts",
}
_PLATFORM_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tiktok", ("tiktok",)),
    ("instagram_reel", ("instagram_reel", "instagram", "insta", "reel", "reels")),
    ("facebook_reel", ("facebook_reel", "facebook", "fb")),
    ("youtube_short", ("youtube_short", "youtube", "yt", "short", "shorts")),
)


class UnsupportedContentActionError(ValueError):
    """Raised when a content action is not supported by content_ops."""


@dataclass(frozen=True)
class RecentIdeaHistory:
    suggested: tuple[str, ...] = ()
    accepted: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    planned: tuple[str, ...] = ()
    posted: tuple[str, ...] = ()

    def duplicate_references(self) -> tuple[IdeaHistoryReference, ...]:
        refs: list[IdeaHistoryReference] = []
        refs.extend(IdeaHistoryReference(text=text, source="recent_idea_suggested") for text in self.suggested)
        refs.extend(IdeaHistoryReference(text=text, source="recent_idea_accepted") for text in self.accepted)
        refs.extend(IdeaHistoryReference(text=text, source="recent_idea_rejected") for text in self.rejected)
        refs.extend(IdeaHistoryReference(text=text, source="recent_idea_planned") for text in self.planned)
        refs.extend(IdeaHistoryReference(text=text, source="recent_post") for text in self.posted)
        return tuple(ref for ref in refs if ref.text.strip())

    def theme_references(self) -> tuple[str, ...]:
        return (*self.suggested, *self.accepted, *self.rejected, *self.planned)

    def steering_references(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (
            ("recent rejected idea cores", self.rejected),
            ("recent accepted idea cores", self.accepted),
            ("recent planned idea cores", self.planned),
            ("recent posted idea cores", self.posted),
        )


class ContentOpsService:
    lane_name = "content_ops"

    def __init__(
        self,
        *,
        docs_loader: "ProjectDocsLoader | None" = None,
        openai_service: "OpenAIService | None" = None,
        airtable_service: "AirtableService | None" = None,
        operational_knowledge_loader: "OperationalKnowledgeLoader | None" = None,
        analytics_loader: "AnalyticsLoader | None" = None,
        platform_signal_loader: "PlatformSignalLoader | None" = None,
        weekly_analysis_loader: "WeeklyAnalysisPersistenceService | None" = None,
        correction_repository: CorrectionFileRepository | None = None,
        commercial_class_log: CommercialClassLog | None = None,
        live_actions: frozenset[str] | None = None,
        writer_routing_service: WriterRoutingService | None = None,
    ) -> None:
        self.docs_loader = docs_loader
        self.openai_service = openai_service
        self.airtable_service = airtable_service
        self.operational_knowledge_loader = operational_knowledge_loader
        self.analytics_loader = analytics_loader
        self.platform_signal_loader = platform_signal_loader
        self.weekly_analysis_loader = weekly_analysis_loader
        self.correction_repository = correction_repository
        self._commercial_class_log = commercial_class_log
        self.duplicate_guard = DuplicateRiskGuard(openai_service)
        # None = all actions are live-capable; frozenset = explicit allow-list
        self._live_actions = live_actions
        self.writer_routing_service = writer_routing_service or WriterRoutingService()

    def _integration_active_for(self, action_type: str) -> bool:
        """Return True if live integrations (OpenAI/Airtable) are allowed for this action."""
        if self._live_actions is None:
            return True
        normalized = action_type.strip().lower()
        if normalized in self._live_actions:
            return True
        alias_groups = (
            frozenset({"draft", "vollauto"}),
        )
        for group in alias_groups:
            if normalized in group and group & self._live_actions:
                return True
        return False

    def supports(self, action_type: str) -> bool:
        return action_type.strip().lower() in SUPPORTED_CONTENT_ACTIONS

    def _emit_commercial_log(self, content_result: "ContentOpResult") -> None:
        if self._commercial_class_log is None or not content_result.commercial_class:
            return
        try:
            comm_class = CommercialClass(content_result.commercial_class)
        except ValueError:
            return
        from uuid import uuid4
        entry = CommercialLogEntry(
            record_id=f"clog_{uuid4().hex[:12]}",
            project_key=content_result.project_key,
            action_type=content_result.action_type,
            platform=content_result.platform or "",
            commercial_class=comm_class,
            prompt_excerpt=content_result.command_body[:120],
        )
        self._commercial_class_log.append(entry)

    def can_use_foundation_backed_idea(self) -> bool:
        return (
            self.docs_loader is not None
            and self.openai_service is not None
            and self._integration_active_for("idea")
        )

    def can_use_foundation_backed_vollauto(self) -> bool:
        return (
            self.docs_loader is not None
            and self.openai_service is not None
            and self._integration_active_for("vollauto")
        )

    def can_use_foundation_backed_draft(self) -> bool:
        return (
            self.docs_loader is not None
            and self.openai_service is not None
            and self._integration_active_for("draft")
        )

    def can_use_foundation_backed_caption(self) -> bool:
        return (
            self.docs_loader is not None
            and self.openai_service is not None
            and self._integration_active_for("caption")
        )

    def can_use_foundation_backed_hook(self) -> bool:
        return (
            self.docs_loader is not None
            and self.openai_service is not None
            and self._integration_active_for("hook")
        )

    def can_use_foundation_backed_serie(self) -> bool:
        return (
            self.docs_loader is not None
            and self.openai_service is not None
            and self._integration_active_for("serie")
        )

    def can_use_foundation_backed_title(self) -> bool:
        return (
            self.docs_loader is not None
            and self.openai_service is not None
            and self._integration_active_for("title")
        )

    def can_use_foundation_backed_cta(self) -> bool:
        return (
            self.docs_loader is not None
            and self.openai_service is not None
            and self._integration_active_for("cta")
        )

    def can_use_foundation_backed_followup(self, *, proposal_action_type: str) -> bool:
        return (
            self.docs_loader is not None
            and self.openai_service is not None
            and self._integration_active_for(proposal_action_type)
        )

    def resolve_platform_hint(self, command_body: str) -> tuple[str, str]:
        return self._resolve_platform(command_body)

    def generate_idea_from_foundation(
        self,
        *,
        project_key: str,
        command_body: str,
        foundation_result: AnalysisFoundationResult,
    ) -> FoundationIdeaResult:
        if not self.can_use_foundation_backed_idea():
            raise RuntimeError("foundation-backed /idea requires docs and OpenAI integration")

        platform, normalized_body = self._resolve_platform(command_body)
        selected_snapshots = self._select_idea_snapshots(
            snapshots=foundation_result.analysis_snapshots,
            platform=platform,
        )
        # --- Mode detection: must happen before building system prompt ---
        _quality_gate = IdeaQualityGate()
        _distiller = IdeaDistiller()
        _anchor_tokens = _quality_gate.extract_prompt_anchors(normalized_body)
        # MIRROR MODE: user described a concrete first-person moment → preserve it faithfully.
        # IDEATION MODE: topic list, broad keywords, or empty → freely generate.
        _sharpen_mode = IdeaQualityGate.classify_idea_mode(normalized_body) == "mirror"

        idea_history = self._load_recent_idea_history(project_key)
        history_steering_block = self._build_recent_idea_steering_block(idea_history)

        system_prompt = self._build_foundation_idea_system_prompt(
            platform=platform,
            writer_brief=foundation_result.writer_brief,
            selected_snapshots=selected_snapshots,
            weekly_analysis=foundation_result.weekly_analysis,
            sharpen_mode=_sharpen_mode,
            anchor_tokens=_anchor_tokens,
            history_steering_block=history_steering_block,
        )
        user_prompt = normalized_body.strip() if normalized_body.strip() else "Neue Idee"
        _idea_model = self.writer_routing_service.get_recommended_model("idea")
        recent_posts = list(idea_history.posted)
        recent_drafts = self._fetch_recent_drafts(project_key)
        recent_ideas = list(idea_history.theme_references())
        recent_history_refs = idea_history.duplicate_references()
        response = None
        candidates: list[str] = []
        candidate_text = ""
        best_score = -99.0
        attempts = IdeaQualityGate.MIRROR_MAX_RETRIES + 1 if _sharpen_mode else 1
        blocked_by_rejected_history_in_initial = False
        for attempt in range(1, attempts + 1):
            response = self.openai_service.complete_messages(  # type: ignore[union-attr]
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=_idea_model,
                fallback_to_default=True,
                temperature=0.7,
            )
            candidates = self._parse_idea_candidates(response.output_text)
            selection_candidates = self._filter_same_core_repeat_candidates(
                candidates,
                foundation_result=foundation_result,
                recent_posts=recent_posts,
                recent_drafts=recent_drafts,
                recent_ideas=recent_ideas,
                recent_history=recent_history_refs,
            )
            blocked_by_rejected_history = self._has_rejected_same_core(
                candidates,
                foundation_result=foundation_result,
                recent_history=recent_history_refs,
            )
            blocked_by_rejected_history_in_initial = (
                blocked_by_rejected_history_in_initial or blocked_by_rejected_history
            )
            picker_candidates = selection_candidates or candidates
            if _sharpen_mode and not selection_candidates and blocked_by_rejected_history:
                picker_candidates = []
            candidate_text, best_score = self._pick_fidelity_checked_idea(
                picker_candidates,
                quality_gate=_quality_gate,
                distiller=_distiller,
                anchor_tokens=_anchor_tokens,
                sharpen_mode=_sharpen_mode,
                user_prompt=normalized_body,
            )
            if candidate_text:
                break
            _log.warning(
                "idea_mirror_fidelity: no candidate passed | stage=initial | attempt=%d/%d | candidates=%d",
                attempt, attempts, len(candidates),
            )
        if response is None or (_sharpen_mode and not candidate_text):
            if response is not None and _sharpen_mode and blocked_by_rejected_history_in_initial:
                content_result = ContentOpResult(
                    lane_name=self.lane_name,
                    project_key=project_key,
                    action_type="idea",
                    command_body=normalized_body,
                    title="Content idea",
                    summary=_REJECTED_SAME_CORE_FALLBACK_SUMMARY,
                    items=(),
                    openai_used=True,
                    model_name=response.model,
                    airtable_record_id=None,
                    platform=platform,
                    foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
                    writer_brief_id=foundation_result.writer_brief.brief_id,
                    commercial_class=classify_commercial(normalized_body, action_type="idea").value,
                )
                execution_meta = ModelExecutionMeta(
                    provider_name="openai",
                    model_name=response.model,
                    task_role="writer",
                    status="completed",
                    notes=("Rejected same-core /idea mirror prompt degraded without returning clone.",),
                )
                self._emit_commercial_log(content_result)
                return FoundationIdeaResult(
                    content_result=content_result,
                    selected_snapshots=selected_snapshots,
                    writer_brief=foundation_result.writer_brief,
                    execution_meta=execution_meta,
                )
            raise RuntimeError("MIRROR fidelity gate failed for /idea initial generation")
        _log_quality = logging.getLogger("operator_core.core.content_ops.service")
        _log_quality.info(
            "idea_quality_gate: picked candidate | mode=%s | score=%.1f | candidates=%d | anchors=%d",
            "sharpen" if _sharpen_mode else "generate", best_score, len(candidates), len(_anchor_tokens),
        )

        risk = self.duplicate_guard.evaluate(
            project_key=project_key,
            candidate_idea=candidate_text,
            foundation_result=foundation_result,
            recent_posts=recent_posts,
            recent_drafts=recent_drafts,
            recent_ideas=recent_ideas,
            recent_history=recent_history_refs,
        )

        # Build display items from best candidate; used for Airtable persistence title.
        items: tuple[str, ...] = (f"Idee: {candidate_text}",) if candidate_text else ()

        # Always persist the ORIGINAL idea to Airtable FIRST.
        # This ensures the blocked topic lands in _fetch_recent_ideas on the next run
        # even when the guard replaces items with alternatives for display.
        airtable_record_id = self._try_create_airtable_record(
            project_key=project_key,
            command_body=normalized_body,
            parsed_items=items,  # starts with "Idee:" → title stored correctly
        )

        # Theme-cooldown is evaluated unconditionally — saturation overrides same-cluster
        # duplicate alternatives so the bot never surfaces more of the same problem space.
        theme_risk = self.duplicate_guard.evaluate_theme_risk(
            candidate_idea=candidate_text,
            recent_drafts=recent_drafts,
            recent_ideas=recent_ideas,
        )

        final_summary = "Idee generiert."
        if theme_risk.saturated:
            # Theme saturation wins over same-core duplicate: even if duplicate-risk is
            # high, we must NOT generate same-cluster alternatives — pivot outside instead.
            _log.info(
                "theme_cooldown: saturated (overrides duplicate path) | cluster=%s | duplicate_level=%s",
                theme_risk.cluster_name, risk.level,
            )
            pivot_alternatives: list[str] = []
            eligible_pivots: list[str] = []
            for attempt in range(1, attempts + 1):
                pivot_alternatives = self.duplicate_guard.generate_theme_pivot_alternatives(
                    project_key=project_key,
                    original_idea=candidate_text,
                    theme_risk=theme_risk,
                    platform=platform,
                    prompt_context=normalized_body,
                    sharpen_mode=_sharpen_mode,
                )
                eligible_pivots = [
                    c for c in pivot_alternatives
                    if self.duplicate_guard.is_pivot_eligible(c, theme_risk.cluster_name)
                ]
                if not eligible_pivots:
                    eligible_pivots = pivot_alternatives
                if _sharpen_mode:
                    eligible_pivots = self._filter_mirror_fidelity_candidates(
                        eligible_pivots,
                        distiller=_distiller,
                        anchor_tokens=_anchor_tokens,
                        user_prompt=normalized_body,
                    )
                if eligible_pivots or not _sharpen_mode:
                    break
                _log.warning(
                    "idea_mirror_fidelity: no candidate passed | stage=theme_pivot | attempt=%d/%d | candidates=%d",
                    attempt, attempts, len(pivot_alternatives),
                )
            if _sharpen_mode and pivot_alternatives and not eligible_pivots:
                raise RuntimeError("MIRROR fidelity gate failed for /idea theme pivot")
            if pivot_alternatives:
                # Hard eligibility gate: for clusters with a defined consequence
                # space, only candidates that contain ≥1 consequence keyword (and
                # no anti-consequence keyword) may compete.
                # "Shirt buttoning" and "baby bump in the way" contain neither pause
                # nor rhythm nor body-limit signal → filtered out here.
                if not eligible_pivots:
                    _log.warning(
                        "theme_cooldown: no eligible pivots in consequence space | "
                        "cluster=%s | falling back to all %d",
                        theme_risk.cluster_name, len(pivot_alternatives),
                    )
                    eligible_pivots = pivot_alternatives

                def _pivot_total(candidate: str) -> float:
                    return (
                        _quality_gate.score(candidate).score
                        + _quality_gate.anchor_score(candidate, _anchor_tokens, sharpen_mode=_sharpen_mode)
                        + self.duplicate_guard.consequence_space_score(candidate, theme_risk.cluster_name)
                    )
                best_pivot = max(eligible_pivots, key=_pivot_total)
                best_pivot = _distiller.distill(best_pivot, anchor_tokens=_anchor_tokens)
                pivot_score = _pivot_total(best_pivot)
                _log.info(
                    "theme_cooldown: pivot selected | score=%.1f | eligible=%d/%d | cluster=%s",
                    pivot_score, len(eligible_pivots), len(pivot_alternatives), theme_risk.cluster_name,
                )
                if pivot_score >= IdeaQualityGate.MINIMUM_WINNER_SCORE:
                    items = (best_pivot,)
                    final_summary = (
                        f"Theme-Cluster '{theme_risk.cluster_name}' saturiert. "
                        "Stärkste neue Idee ausgewählt."
                    )
                else:
                    items = tuple(pivot_alternatives)
                    final_summary = (
                        f"Theme-Cluster '{theme_risk.cluster_name}' saturiert. "
                        "3 Alternativen zur Auswahl (alle unter Qualitätsschwelle)."
                    )
            else:
                items = ()
                final_summary = f"Theme-Cluster '{theme_risk.cluster_name}' saturiert."
        elif risk.level == "high":
            _log.info("duplicate_risk: high risk detected | reason=%s", risk.reason)
            alternatives: list[str] = []
            best_alt = ""
            alt_score = -99.0
            retained_faithful_original = False
            blocked_rejected_original = False
            for attempt in range(1, attempts + 1):
                alternatives = self.duplicate_guard.generate_alternatives(
                    project_key=project_key,
                    original_idea=candidate_text,
                    risk_evaluation=risk,
                    platform=platform,
                    prompt_context=normalized_body,
                    sharpen_mode=_sharpen_mode,
                )
                selection_alternatives = self._filter_same_core_repeat_candidates(
                    alternatives,
                    foundation_result=foundation_result,
                    recent_posts=recent_posts,
                    recent_drafts=recent_drafts,
                    recent_ideas=recent_ideas,
                    recent_history=recent_history_refs,
                )
                alternatives_blocked_by_rejected_history = self._has_rejected_same_core(
                    alternatives,
                    foundation_result=foundation_result,
                    recent_history=recent_history_refs,
                )
                picker_alternatives = selection_alternatives or alternatives
                if (
                    _sharpen_mode
                    and not selection_alternatives
                    and alternatives_blocked_by_rejected_history
                ):
                    picker_alternatives = []
                best_alt, alt_score = self._pick_fidelity_checked_idea(
                    picker_alternatives,
                    quality_gate=_quality_gate,
                    distiller=_distiller,
                    anchor_tokens=_anchor_tokens,
                    sharpen_mode=_sharpen_mode,
                    user_prompt=normalized_body,
                )
                if best_alt or not _sharpen_mode:
                    break
                _log.warning(
                    "idea_mirror_fidelity: no candidate passed | stage=duplicate_fallback | attempt=%d/%d | candidates=%d",
                    attempt, attempts, len(alternatives),
                )
            if _sharpen_mode and not best_alt:
                if self._risk_references_rejected_history(risk):
                    _log.warning(
                        "idea_mirror_fidelity: duplicate fallback exhausted | rejected same-core original blocked"
                    )
                    items = ()
                    alternatives = []
                    final_summary = _REJECTED_SAME_CORE_FALLBACK_SUMMARY
                    blocked_rejected_original = True
                else:
                    _log.warning(
                        "idea_mirror_fidelity: duplicate fallback exhausted | retaining faithful original"
                    )
                    _naturalized = self._naturalize_mirror_output(
                        candidate_text,
                        anchor_tokens=_anchor_tokens,
                        user_prompt=normalized_body,
                        model=_idea_model,
                    )
                    items = (_naturalized,)
                    final_summary = "Duplikatsrisiko erkannt. Treue Originalidee beibehalten."
                    alternatives = []
                    retained_faithful_original = True
            if alternatives:
                _log.info(
                    "duplicate_risk: quality gate on alternatives | best_score=%.1f | count=%d",
                    alt_score, len(alternatives),
                )
                if alt_score >= IdeaQualityGate.MINIMUM_WINNER_SCORE:
                    items = (best_alt,)
                    final_summary = f"Duplikatsrisiko erkannt. Stärkster frischer Angle ausgewählt."
                else:
                    items = tuple(alternatives)
                    final_summary = f"Hohes Duplikatsrisiko. 3 Alternativen zur Auswahl (alle unter Qualitätsschwelle)."
            elif not retained_faithful_original and not blocked_rejected_original:
                items = ()
                final_summary = f"Hohes Duplikatsrisiko für '{candidate_text}'."
        _idea_text = items[0] if items else normalized_body
        content_result = ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="idea",
            command_body=normalized_body,
            title="Content idea",
            summary=final_summary,
            items=items,
            openai_used=True,
            model_name=response.model,
            airtable_record_id=airtable_record_id,
            platform=platform,
            foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
            writer_brief_id=foundation_result.writer_brief.brief_id,
            commercial_class=classify_commercial(_idea_text, action_type="idea").value,
        )
        execution_meta = ModelExecutionMeta(
            provider_name="openai",
            model_name=response.model,
            task_role="writer",
            status="completed",
            notes=("Generated /idea from analysis snapshot and writer brief.",),
        )
        self._emit_commercial_log(content_result)
        return FoundationIdeaResult(
            content_result=content_result,
            selected_snapshots=selected_snapshots,
            writer_brief=foundation_result.writer_brief,
            execution_meta=execution_meta,
        )

    def generate_vollauto_from_foundation(
        self,
        *,
        project_key: str,
        command_body: str,
        foundation_result: AnalysisFoundationResult,
    ) -> FoundationDraftResult:
        return self._generate_structured_draft_from_foundation(
            project_key=project_key,
            command_body=command_body,
            foundation_result=foundation_result,
            action_type="vollauto",
        )

    def generate_draft_from_foundation(
        self,
        *,
        project_key: str,
        command_body: str,
        foundation_result: AnalysisFoundationResult,
    ) -> FoundationDraftResult:
        return self._generate_structured_draft_from_foundation(
            project_key=project_key,
            command_body=command_body,
            foundation_result=foundation_result,
            action_type="draft",
        )

    def _generate_structured_draft_from_foundation(
        self,
        *,
        project_key: str,
        command_body: str,
        foundation_result: AnalysisFoundationResult,
        action_type: str,
    ) -> FoundationDraftResult:
        if action_type == "vollauto":
            if not self.can_use_foundation_backed_vollauto():
                raise RuntimeError("foundation-backed /vollauto requires docs and OpenAI integration")
        elif action_type == "draft":
            if not self.can_use_foundation_backed_draft():
                raise RuntimeError("foundation-backed /draft requires docs and OpenAI integration")
        else:
            raise RuntimeError(f"unsupported structured draft foundation action: {action_type}")

        platform, normalized_body = self._resolve_platform(command_body)
        selected_snapshots = self._select_idea_snapshots(
            snapshots=foundation_result.analysis_snapshots,
            platform=platform,
        )
        system_prompt = self._build_foundation_vollauto_system_prompt(
            platform=platform,
            writer_brief=foundation_result.writer_brief,
            selected_snapshots=selected_snapshots,
            weekly_analysis=foundation_result.weekly_analysis,
        )
        user_prompt = normalized_body.strip() if normalized_body.strip() else "Neuer Entwurf"
        _draft_model = self.writer_routing_service.get_recommended_model(action_type)
        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=_draft_model,
            fallback_to_default=True,
            temperature=0.5,
        )
        items = self._parse_draft_response(response.output_text)
        airtable_record_id = self._try_create_draft_airtable_record(
            project_key=project_key,
            command_body=normalized_body,
            parsed_items=items,
        )
        content_result = ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type=action_type,
            command_body=normalized_body,
            title="Content draft",
            summary="Voll Auto generiert." if action_type == "vollauto" else "Entwurf generiert.",
            items=items,
            openai_used=True,
            model_name=response.model,
            airtable_record_id=airtable_record_id,
            platform=platform,
            foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
            writer_brief_id=foundation_result.writer_brief.brief_id,
            commercial_class=classify_commercial(normalized_body, action_type=action_type).value,
        )
        execution_meta = ModelExecutionMeta(
            provider_name="openai",
            model_name=response.model,
            task_role="writer",
            status="completed",
            notes=(f"Generated /{action_type} from analysis snapshot and writer brief.",),
        )
        self._emit_commercial_log(content_result)
        return FoundationDraftResult(
            content_result=content_result,
            selected_snapshots=selected_snapshots,
            writer_brief=foundation_result.writer_brief,
            execution_meta=execution_meta,
        )

    def generate_caption_from_foundation(
        self,
        *,
        project_key: str,
        command_body: str,
        foundation_result: AnalysisFoundationResult,
    ) -> FoundationCaptionResult:
        if not self.can_use_foundation_backed_caption():
            raise RuntimeError("foundation-backed /caption requires docs and OpenAI integration")

        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        doc_context = self._build_caption_doc_context(content=content_doc.content)
        platform, normalized_body = self._resolve_platform(command_body)
        selected_snapshots = self._select_idea_snapshots(
            snapshots=foundation_result.analysis_snapshots,
            platform=platform,
        )
        system_prompt = self._build_foundation_caption_system_prompt(
            platform=platform,
            doc_context=doc_context,
            writer_brief=foundation_result.writer_brief,
            selected_snapshots=selected_snapshots,
            weekly_analysis=foundation_result.weekly_analysis,
        )
        user_prompt = normalized_body.strip() if normalized_body.strip() else "Neue Caption"
        _caption_model = self.writer_routing_service.get_recommended_model("caption", prefer_fast=True)
        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=_caption_model,
            fallback_to_default=True,
            temperature=0.6,
        )
        items = self._parse_caption_response(response.output_text)
        airtable_record_id = self._try_create_caption_airtable_record(
            project_key=project_key,
            command_body=normalized_body,
            parsed_items=items,
        )
        content_result = ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="caption",
            command_body=normalized_body,
            title="Content caption",
            summary="Caption generiert.",
            items=items,
            openai_used=True,
            model_name=response.model,
            airtable_record_id=airtable_record_id,
            platform=platform,
            foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
            writer_brief_id=foundation_result.writer_brief.brief_id,
        )
        execution_meta = ModelExecutionMeta(
            provider_name="openai",
            model_name=response.model,
            task_role="writer",
            status="completed",
            notes=("Generated /caption from analysis snapshot and writer brief.",),
        )
        return FoundationCaptionResult(
            content_result=content_result,
            selected_snapshots=selected_snapshots,
            writer_brief=foundation_result.writer_brief,
            execution_meta=execution_meta,
        )

    def generate_hook_from_foundation(
        self,
        *,
        project_key: str,
        command_body: str,
        foundation_result: AnalysisFoundationResult,
    ) -> FoundationHookResult:
        if not self.can_use_foundation_backed_hook():
            raise RuntimeError("foundation-backed /hook requires docs and OpenAI integration")

        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        doc_context = self._build_hook_doc_context(content=content_doc.content)
        platform, normalized_body = self._resolve_platform(command_body)
        selected_snapshots = self._select_idea_snapshots(
            snapshots=foundation_result.analysis_snapshots,
            platform=platform,
        )
        system_prompt = self._build_foundation_hook_system_prompt(
            platform=platform,
            doc_context=doc_context,
            writer_brief=foundation_result.writer_brief,
            selected_snapshots=selected_snapshots,
            weekly_analysis=foundation_result.weekly_analysis,
        )
        user_prompt = normalized_body.strip() if normalized_body.strip() else "Neuer Hook"
        _hook_model = self.writer_routing_service.get_recommended_model("hook")
        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=_hook_model,
            fallback_to_default=True,
            temperature=0.7,
        )
        items = self._parse_hook_response(response.output_text)
        airtable_record_id = self._try_create_hook_airtable_record(
            project_key=project_key,
            command_body=normalized_body,
            parsed_items=items,
        )
        content_result = ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="hook",
            command_body=normalized_body,
            title="Content hook",
            summary="Hook generiert.",
            items=items,
            openai_used=True,
            model_name=response.model,
            airtable_record_id=airtable_record_id,
            platform=platform,
            foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
            writer_brief_id=foundation_result.writer_brief.brief_id,
        )
        execution_meta = ModelExecutionMeta(
            provider_name="openai",
            model_name=response.model,
            task_role="writer",
            status="completed",
            notes=("Generated /hook from analysis snapshot and writer brief.",),
        )
        return FoundationHookResult(
            content_result=content_result,
            selected_snapshots=selected_snapshots,
            writer_brief=foundation_result.writer_brief,
            execution_meta=execution_meta,
        )

    def generate_serie_from_foundation(
        self,
        *,
        project_key: str,
        command_body: str,
        foundation_result: AnalysisFoundationResult,
    ) -> FoundationSerieResult:
        if not self.can_use_foundation_backed_serie():
            raise RuntimeError("foundation-backed /serie requires docs and OpenAI integration")

        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        platform, normalized_body = self._resolve_platform(command_body)
        rules_block = self._build_content_rules_block(content_doc.content, state_doc.content)
        selected_snapshots = self._select_idea_snapshots(
            snapshots=foundation_result.analysis_snapshots,
            platform=platform,
        )
        system_prompt = self._build_foundation_serie_system_prompt(
            platform=platform,
            rules_block=rules_block,
            writer_brief=foundation_result.writer_brief,
            selected_snapshots=selected_snapshots,
        )
        user_prompt = normalized_body.strip() if normalized_body.strip() else "Neuer Serie/Thema-Vorschlag"
        _serie_model = self.writer_routing_service.get_recommended_model("serie", prefer_fast=True)
        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=_serie_model,
            fallback_to_default=True,
            temperature=0.7,
        )
        value = self._parse_single_key_response(response.output_text, "Serie/Thema")
        content_result = ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="serie",
            command_body=normalized_body,
            title="Serie/Thema",
            summary="Serie/Thema generiert.",
            items=(f"Serie/Thema: {value or self._display_body(normalized_body)}",),
            openai_used=True,
            model_name=response.model,
            platform=platform,
            foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
            writer_brief_id=foundation_result.writer_brief.brief_id,
        )
        execution_meta = ModelExecutionMeta(
            provider_name="openai",
            model_name=response.model,
            task_role="writer",
            status="completed",
            notes=("Generated /serie from analysis snapshot and writer brief.",),
        )
        return FoundationSerieResult(
            content_result=content_result,
            selected_snapshots=selected_snapshots,
            writer_brief=foundation_result.writer_brief,
            execution_meta=execution_meta,
        )

    def generate_title_from_foundation(
        self,
        *,
        project_key: str,
        command_body: str,
        foundation_result: AnalysisFoundationResult,
    ) -> FoundationTitleResult:
        if not self.can_use_foundation_backed_title():
            raise RuntimeError("foundation-backed /title requires docs and OpenAI integration")

        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        platform, normalized_body = self._resolve_platform(command_body)
        rules_block = self._build_content_rules_block(content_doc.content, state_doc.content)
        selected_snapshots = self._select_idea_snapshots(
            snapshots=foundation_result.analysis_snapshots,
            platform=platform,
        )
        system_prompt = self._build_foundation_title_system_prompt(
            platform=platform,
            rules_block=rules_block,
            writer_brief=foundation_result.writer_brief,
            selected_snapshots=selected_snapshots,
        )
        user_prompt = normalized_body.strip() if normalized_body.strip() else "Neuer Title-Vorschlag"
        _title_model = self.writer_routing_service.get_recommended_model("title", prefer_fast=True)
        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=_title_model,
            fallback_to_default=True,
            temperature=0.7,
        )
        value = self._parse_single_key_response(response.output_text, "Title")
        content_result = ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="title",
            command_body=normalized_body,
            title="Title",
            summary="Title generiert.",
            items=(f"Title: {value or self._display_body(normalized_body)}",),
            openai_used=True,
            model_name=response.model,
            platform=platform,
            foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
            writer_brief_id=foundation_result.writer_brief.brief_id,
        )
        execution_meta = ModelExecutionMeta(
            provider_name="openai",
            model_name=response.model,
            task_role="writer",
            status="completed",
            notes=("Generated /title from analysis snapshot and writer brief.",),
        )
        return FoundationTitleResult(
            content_result=content_result,
            selected_snapshots=selected_snapshots,
            writer_brief=foundation_result.writer_brief,
            execution_meta=execution_meta,
        )

    def generate_cta_from_foundation(
        self,
        *,
        project_key: str,
        command_body: str,
        foundation_result: AnalysisFoundationResult,
    ) -> FoundationCtaResult:
        if not self.can_use_foundation_backed_cta():
            raise RuntimeError("foundation-backed /cta requires docs and OpenAI integration")

        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        platform, normalized_body = self._resolve_platform(command_body)
        rules_block = self._build_content_rules_block(content_doc.content, state_doc.content)
        selected_snapshots = self._select_idea_snapshots(
            snapshots=foundation_result.analysis_snapshots,
            platform=platform,
        )
        system_prompt = self._build_foundation_cta_system_prompt(
            platform=platform,
            rules_block=rules_block,
            writer_brief=foundation_result.writer_brief,
            selected_snapshots=selected_snapshots,
            weekly_analysis=foundation_result.weekly_analysis,
        )
        user_prompt = normalized_body.strip() if normalized_body.strip() else "Neuer CTA-Vorschlag"
        _cta_model = self.writer_routing_service.get_recommended_model("cta")
        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=_cta_model,
            fallback_to_default=True,
            temperature=0.7,
        )
        value = self._parse_single_key_response(response.output_text, "CTA")
        content_result = ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="cta",
            command_body=normalized_body,
            title="CTA",
            summary="CTA generiert.",
            items=(f"CTA: {value or self._display_body(normalized_body)}",),
            openai_used=True,
            model_name=response.model,
            platform=platform,
            foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
            writer_brief_id=foundation_result.writer_brief.brief_id,
        )
        execution_meta = ModelExecutionMeta(
            provider_name="openai",
            model_name=response.model,
            task_role="writer",
            status="completed",
            notes=("Generated /cta from analysis snapshot and writer brief.",),
        )
        return FoundationCtaResult(
            content_result=content_result,
            selected_snapshots=selected_snapshots,
            writer_brief=foundation_result.writer_brief,
            execution_meta=execution_meta,
        )

    def generate_followup_from_foundation(
        self,
        *,
        project_key: str,
        proposal: ContentProposal,
        instruction: str,
        foundation_result: AnalysisFoundationResult,
        mutation_mode: str,
    ) -> FoundationFollowupResult:
        if not self.can_use_foundation_backed_followup(proposal_action_type=proposal.action_type):
            raise RuntimeError("foundation-backed follow-up requires docs and OpenAI integration")

        platform_override, normalized_instruction = self._resolve_platform(instruction)
        effective_platform = platform_override or proposal.platform
        proposal_seed_body = self._proposal_source_seed(
            proposal=proposal,
            platform_override=platform_override,
        )
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        rules_block = self._build_content_rules_block(content_doc.content, state_doc.content)
        current_block = self._format_proposal_fields(proposal.fields)
        requires_change = self._followup_requires_change(normalized_instruction)
        selected_snapshots = self._select_idea_snapshots(
            snapshots=foundation_result.analysis_snapshots,
            platform=effective_platform,
        )
        prompts = [
            (
                self._build_foundation_followup_system_prompt(
                    platform=effective_platform,
                    rules_block=rules_block,
                    writer_brief=foundation_result.writer_brief,
                    selected_snapshots=selected_snapshots,
                    current_block=current_block,
                    instruction=normalized_instruction,
                    requires_change=requires_change,
                    forbidden_block="",
                    weekly_analysis=foundation_result.weekly_analysis,
                ),
                0.85,
            )
        ]
        if requires_change and current_block:
            prompts.append(
                (
                    self._build_foundation_followup_system_prompt(
                        platform=effective_platform,
                        rules_block=rules_block,
                        writer_brief=foundation_result.writer_brief,
                        selected_snapshots=selected_snapshots,
                        current_block=current_block,
                        instruction=normalized_instruction,
                        requires_change=requires_change,
                        forbidden_block=(
                            "Der aktuelle Vorschlag darf nicht nur leicht paraphrasiert wiederholt werden.\n"
                            f"Verbotener Ausgangswortlaut:\n{current_block}\n"
                        ),
                        weekly_analysis=foundation_result.weekly_analysis,
                    ),
                    0.95,
                )
            )

        _mutation_model = self.writer_routing_service.get_recommended_model("mutation")
        items: tuple[str, ...] = ()
        parsed_fields: dict[str, str] = {}
        changed = False
        for attempt, (system_prompt, temperature) in enumerate(prompts, start=1):
            response = self.openai_service.complete_messages(  # type: ignore[union-attr]
                system_prompt=system_prompt,
                user_prompt=(
                    f"Aktueller Vorschlag:\n{current_block or 'kein Vorschlag'}\n\n"
                    f"Anweisung:\n{normalized_instruction.strip()}"
                ),
                model=_mutation_model,
                fallback_to_default=True,
                temperature=temperature,
            )
            items = self._parse_followup_response(response.output_text, proposal=proposal)
            parsed_fields = self._extract_structured_fields(items)
            changed = self._proposal_changed_meaningfully(
                proposal=proposal,
                parsed_fields=parsed_fields,
            )
            if not requires_change or changed:
                break
            _log.info(
                "content_ops followup: retrying too-similar foundation result | platform=%s attempt=%s",
                effective_platform,
                attempt,
            )

        content_result = ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="followup",
            command_body=proposal_seed_body,
            title="Follow-up",
            summary="Vorschlag aktualisiert." if changed or requires_change else "Frage beantwortet.",
            items=items,
            openai_used=True,
            model_name=response.model,
            platform=effective_platform,
            foundation_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in selected_snapshots),
            writer_brief_id=foundation_result.writer_brief.brief_id,
        )
        execution_meta = ModelExecutionMeta(
            provider_name="openai",
            model_name=getattr(response, "model", ""),
            task_role="writer",
            status="completed",
            notes=(f"Generated {mutation_mode} follow-up from analysis snapshot and writer brief.",),
        )
        return FoundationFollowupResult(
            content_result=content_result,
            selected_snapshots=selected_snapshots,
            writer_brief=foundation_result.writer_brief,
            execution_meta=execution_meta,
            instruction=normalized_instruction,
            mutation_mode=mutation_mode,
            source_action_type=proposal.action_type,
        )

    def handle(
        self,
        *,
        project_key: str,
        action_type: str,
        command_body: str,
    ) -> ContentOpResult:
        normalized_action = action_type.strip().lower()
        normalized_body = self._normalize(command_body)

        if not self.supports(normalized_action):
            raise UnsupportedContentActionError(
                f"unsupported content action: {action_type}"
            )

        # mark_stale needs no docs or OpenAI — intercept before docs_loader check
        if normalized_action == "mark_stale":
            return self._mark_stale_draft(
                project_key=project_key,
                record_id=normalized_body,
            )
        if normalized_action == "followup":
            raise UnsupportedContentActionError("followup requires explicit proposal context")

        if self.docs_loader is not None:
            lane_result = self._handle_with_docs(
                project_key=project_key,
                action_type=normalized_action,
                command_body=normalized_body,
            )
        else:
            lane_result = self._handle_stub(
                project_key=project_key,
                action_type=normalized_action,
                command_body=normalized_body,
            )

        # Ensure commercial classification is applied to results from handle()
        # This covers all non-foundation paths for /idea, /draft, /vollauto, etc.
        if lane_result.commercial_class is None:
            # Pick a text to classify: first item or command body
            text = lane_result.items[0] if lane_result.items else normalized_body
            lane_result = replace(
                lane_result,
                commercial_class=classify_commercial(text, action_type=normalized_action).value,
            )

        self._emit_commercial_log(lane_result)
        return lane_result

    def follow_up(
        self,
        *,
        project_key: str,
        proposal: ContentProposal,
        instruction: str,
        foundation_result: AnalysisFoundationResult | None = None,
        mutation_mode: str = "followup",
    ) -> ContentOpResult:
        platform_override, normalized_instruction = self._resolve_platform(instruction)
        effective_platform = platform_override or proposal.platform
        proposal_seed_body = self._proposal_source_seed(
            proposal=proposal,
            platform_override=platform_override,
        )
        if foundation_result is not None:
            return self.generate_followup_from_foundation(
                project_key=project_key,
                proposal=proposal,
                instruction=instruction,
                foundation_result=foundation_result,
                mutation_mode=mutation_mode,
            ).content_result
        if self.docs_loader is None or self.openai_service is None:
            return self._build_result(
                project_key=project_key,
                action_type="followup",
                command_body=proposal_seed_body,
                title="Follow-up",
                summary="Vorschlag aktualisiert.",
                items=tuple(f"{k}: {v}" for k, v in proposal.fields.items()),
                platform=effective_platform,
            )

        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        ok_context = self._load_operational_knowledge(project_key)
        platform_context = self._load_platform_signals(project_key=project_key).get(effective_platform)
        weekly_analysis = self._load_fresh_weekly_analysis(project_key=project_key)
        
        ok_block = ok_context.to_prompt_block("priorities", "platform", "posting") if not ok_context.is_empty() else ""
        rules_block = self._build_content_rules_block(content_doc.content, state_doc.content)
        platform_block = self._build_platform_signal_block(platform_context)
        platform_label = _PLATFORM_LABELS.get(effective_platform, effective_platform or "Plattform")
        current_block = self._format_proposal_fields(proposal.fields)
        requires_change = self._followup_requires_change(normalized_instruction)
        freshness_token = str(time.time_ns())
        prompts = [
            (
                self._build_followup_system_prompt(
                    platform_label=platform_label,
                    ok_block=ok_block,
                    platform_block=platform_block,
                    rules_block=rules_block,
                    freshness_token=freshness_token,
                    current_block=current_block,
                    instruction=normalized_instruction,
                    requires_change=requires_change,
                    forbidden_block="",
                    weekly_analysis=weekly_analysis,
                ),
                0.85,
            )
        ]
        if requires_change and current_block:
            prompts.append(
                (
                    self._build_followup_system_prompt(
                        platform_label=platform_label,
                        ok_block=ok_block,
                        platform_block=platform_block,
                        rules_block=rules_block,
                        freshness_token=freshness_token,
                        current_block=current_block,
                        instruction=normalized_instruction,
                        requires_change=requires_change,
                        forbidden_block=(
                            "Der aktuelle Vorschlag darf nicht nur leicht paraphrasiert wiederholt werden.\n"
                            f"Verbotener Ausgangswortlaut:\n{current_block}\n"
                        ),
                        weekly_analysis=weekly_analysis,
                    ),
                    0.95,
                )
            )

        _mutation_model = self.writer_routing_service.get_recommended_model("mutation")
        items: tuple[str, ...] = ()
        parsed_fields: dict[str, str] = {}
        changed = False
        for attempt, (system_prompt, temperature) in enumerate(prompts, start=1):
            response = self.openai_service.complete_messages(  # type: ignore[union-attr]
                system_prompt=system_prompt,
                user_prompt=(
                    f"Aktueller Vorschlag:\n{current_block or 'kein Vorschlag'}\n\n"
                    f"Anweisung:\n{normalized_instruction.strip()}"
                ),
                model=_mutation_model,
                fallback_to_default=True,
                temperature=temperature,
            )
            items = self._parse_followup_response(response.output_text, proposal=proposal)
            parsed_fields = self._extract_structured_fields(items)
            changed = self._proposal_changed_meaningfully(
                proposal=proposal,
                parsed_fields=parsed_fields,
            )
            if not requires_change or changed:
                break
            _log.info(
                "content_ops followup: retrying too-similar result | platform=%s attempt=%s",
                effective_platform,
                attempt,
            )
        return self._build_result(
            project_key=project_key,
            action_type="followup",
            command_body=proposal_seed_body,
            title="Follow-up",
            summary="Vorschlag aktualisiert." if changed or requires_change else "Frage beantwortet.",
            items=items,
            platform=effective_platform,
            openai_used=True,
            model_name=response.model,
        )

    def rewrite_proposal(
        self,
        *,
        project_key: str,
        proposal: ContentProposal,
        foundation_result: AnalysisFoundationResult | None = None,
    ) -> ContentOpResult:
        return self.follow_up(
            project_key=project_key,
            proposal=proposal,
            instruction=self._button_followup_instruction(proposal=proposal, mode="rewrite"),
            foundation_result=foundation_result,
            mutation_mode="rewrite",
        )

    def regenerate_proposal(
        self,
        *,
        project_key: str,
        proposal: ContentProposal,
    ) -> ContentOpResult:
        if proposal.action_type in {"idea", "serie", "title", "hook", "cta", "caption", "vollauto", "draft"}:
            return self._regenerate_with_distance(project_key=project_key, proposal=proposal)
        return self.follow_up(
            project_key=project_key,
            proposal=proposal,
            instruction=self._button_followup_instruction(proposal=proposal, mode="regenerate"),
        )

    # ------------------------------------------------------------------
    # Real docs path
    # ------------------------------------------------------------------

    def _handle_with_docs(
        self,
        *,
        project_key: str,
        action_type: str,
        command_body: str,
    ) -> ContentOpResult:
        if action_type == "idea":
            return self._read_idea(project_key, command_body)
        if action_type in {"serie", "title", "cta"}:
            return self._read_single_field(project_key, action_type, command_body)
        if action_type == "hook":
            return self._read_hook(project_key, command_body)
        if action_type == "caption":
            return self._read_caption(project_key, command_body)
        if action_type in {"draft", "vollauto"}:
            return self._read_draft(project_key, command_body, action_type=action_type)
        return self._read_variant(project_key, command_body)

    def _load_operational_knowledge(self, project_key: str) -> "OperationalKnowledgeContext":
        """Load active operational knowledge rows. Returns empty context if loader unavailable."""
        from operator_core.integrations.operational_knowledge_service import _EMPTY_CONTEXT
        if self.operational_knowledge_loader is None:
            return _EMPTY_CONTEXT
        return self.operational_knowledge_loader.load_active(project_key=project_key)

    def _load_analytics(self) -> "AnalyticsContext":
        """Load analytics context for /idea. Returns empty context if loader unavailable."""
        from operator_core.integrations.analytics_service import _EMPTY_CONTEXT as _ANALYTICS_EMPTY
        if self.analytics_loader is None:
            return _ANALYTICS_EMPTY
        return self.analytics_loader.load_recent()

    def _load_fresh_weekly_analysis(self, *, project_key: str) -> WeeklyAnalysisArtifact | None:
        """Load the latest weekly analysis and apply the 10-day staleness guard."""
        if self.weekly_analysis_loader is None:
            return None

        try:
            weekly = self.weekly_analysis_loader.load_latest(project_key=project_key)
            if weekly is None:
                return None

            # fromisoformat handles Z in 3.11+, but we normalize for safety
            gen_dt = datetime.fromisoformat(weekly.generated_at.replace("Z", "+00:00"))
            if gen_dt.tzinfo is None:
                gen_dt = gen_dt.replace(tzinfo=timezone.utc)

            age = datetime.now(timezone.utc) - gen_dt
            if age > timedelta(days=10):
                _log.info(
                    "ignoring stale weekly analysis in mutation | project=%s analysis_id=%s age_days=%s",
                    project_key,
                    weekly.analysis_id,
                    age.days,
                )
                return None

            return weekly
        except Exception as exc:
            _log.warning(
                "weekly analysis load failed in mutation | project=%s error=%s",
                project_key,
                exc,
            )
            return None

    def _load_platform_signals(self, *, project_key: str) -> dict[str, "PlatformContext"]:
        if self.platform_signal_loader is None:
            return {}
        try:
            return self.platform_signal_loader.load_all(ok_project_key=project_key)
        except Exception as exc:
            _log.warning("content_ops platform signals failed | project=%s error=%s", project_key, exc)
            return {}

    def _fetch_recent_drafts(self, project_key: str, limit: int = 20) -> list[str]:
        """Fetch the most recent drafts to serve as duplicate-risk references."""
        if self.airtable_service is None:
            return []
        try:
            result = self.airtable_service.list_records(
                _CONTENT_DRAFTS_TABLE,
                project_key=project_key,
                max_records=50,
                fields=("main_point", "hook"),
            )
            # Sort by created_time descending
            records = sorted(result.records, key=lambda r: r.created_time or "", reverse=True)
            draft_texts = []
            for r in records[:limit]:
                text = str(r.fields.get("hook") or r.fields.get("main_point") or "").strip()
                if text:
                    draft_texts.append(text)
            return draft_texts
        except Exception as exc:
            _log.warning("content_ops guard: failed to fetch recent drafts | error=%s", exc)
            return []

    def _load_recent_idea_history(self, project_key: str, limit: int = 20) -> RecentIdeaHistory:
        """Load durable /idea memory from corrections plus Airtable planning/posting state."""
        suggested = tuple(self._fetch_recent_ideas(project_key, limit=limit))
        accepted, rejected = self._fetch_recent_corrected_ideas(project_key, limit=limit)
        planned = tuple(self._fetch_recent_planned_ideas(project_key, limit=limit))
        posted = tuple(self._fetch_recent_posted_content(project_key, limit=limit))
        return RecentIdeaHistory(
            suggested=suggested,
            accepted=accepted,
            rejected=rejected,
            planned=planned,
            posted=posted,
        )

    def _fetch_recent_corrected_ideas(self, project_key: str, limit: int = 20) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return latest effective accepted/rejected correction outputs for /idea."""
        if self.correction_repository is None:
            return (), ()
        try:
            records = self.correction_repository.latest_effective_by_action(
                project_key=project_key,
                action_type="idea",
                limit=limit * 2,
            )
        except Exception as exc:
            _log.warning("content_ops guard: failed to fetch correction idea history | error=%s", exc)
            return (), ()

        accepted: list[str] = []
        rejected: list[str] = []
        for record in records:
            text = str(record.corrected_output or record.bot_output or "").strip()
            if not text:
                continue
            if record.status in {CorrectionStatus.accepted_as_is, CorrectionStatus.accepted_with_edits}:
                accepted.append(text)
            elif record.status is CorrectionStatus.rejected:
                rejected.append(text)
        return tuple(accepted[:limit]), tuple(rejected[:limit])

    def _fetch_recent_ideas(self, project_key: str, limit: int = 20) -> list[str]:
        """Fetch the most recent ideas to serve as duplicate-risk references."""
        if self.airtable_service is None:
            return []
        try:
            result = self.airtable_service.list_records(
                _CONTENT_IDEAS_TABLE,
                project_key=project_key,
                max_records=50,
                fields=("title",),
            )
            # Sort by created_time descending
            records = sorted(result.records, key=lambda r: r.created_time or "", reverse=True)
            idea_texts = []
            for r in records[:limit]:
                text = str(r.fields.get("title") or "").strip()
                if text:
                    idea_texts.append(text)
            return idea_texts
        except Exception as exc:
            _log.warning("content_ops guard: failed to fetch recent ideas | error=%s", exc)
            return []

    def _fetch_recent_planned_ideas(self, project_key: str, limit: int = 20) -> list[str]:
        """Fetch recent daily-plan selections so /idea avoids planned moments."""
        if self.airtable_service is None:
            return []
        try:
            result = self.airtable_service.list_records(
                _DAILY_PLANS_TABLE,
                project_key=project_key,
                max_records=50,
                filter_formula='OR({decision} = "post", {decision} = "draft")',
                fields=("decision", "title_raw", "hook", "caption", "serie_thema", "date"),
            )
            records = sorted(
                result.records,
                key=lambda r: str(r.fields.get("date") or r.created_time or ""),
                reverse=True,
            )
            planned_texts = []
            for r in records[:limit]:
                text = str(
                    r.fields.get("title_raw")
                    or r.fields.get("hook")
                    or r.fields.get("caption")
                    or r.fields.get("serie_thema")
                    or ""
                ).strip()
                if text:
                    planned_texts.append(text)
            return planned_texts
        except Exception as exc:
            _log.warning("content_ops guard: failed to fetch recent planned ideas | error=%s", exc)
            return []

    def _fetch_recent_posted_content(self, project_key: str, limit: int = 20) -> list[str]:
        """Fetch recently posted draft cores so /idea does not repeat already published moments."""
        if self.airtable_service is None:
            return []
        try:
            result = self.airtable_service.list_records(
                _CONTENT_DRAFTS_TABLE,
                project_key=project_key,
                max_records=50,
                filter_formula='{stage} = "posted"',
                fields=("main_point", "hook", "posted_at"),
            )
            records = sorted(
                result.records,
                key=lambda r: str(r.fields.get("posted_at") or r.created_time or ""),
                reverse=True,
            )
            posted_texts = []
            for r in records[:limit]:
                text = str(
                    r.fields.get("main_point")
                    or r.fields.get("hook")
                    or ""
                ).strip()
                if text:
                    posted_texts.append(text)
            return posted_texts
        except Exception as exc:
            _log.warning("content_ops guard: failed to fetch recent posted content | error=%s", exc)
            return []

    def _build_recent_idea_steering_block(self, history: RecentIdeaHistory) -> str:
        """Build compact prompt guidance so /idea steers away before duplicate gates run."""
        lines: list[str] = []
        for label, values in history.steering_references():
            compact = self._compact_history_values(values, limit=4)
            if compact:
                lines.append(f"- {label}: " + " | ".join(compact))
        if not lines:
            return ""
        return (
            "Recent idea history steering (bindend fuer /idea):\n"
            "Nutze diese Historie, um den naechsten Vorschlag vorab frischer zu machen.\n"
            "- Rejected cores: gleiche Szene/Reibung/Familie aktiv meiden.\n"
            "- Accepted/planned/posted cores: keine Near-Clones; wenn gleiche grobe Familie, dann neue Szene oder neue Reibung.\n"
            "- Gleiche Familie ist erlaubt, wenn der konkrete Angle klar neu ist.\n"
            + "\n".join(lines)
        )

    @staticmethod
    def _compact_history_values(values: tuple[str, ...], *, limit: int) -> tuple[str, ...]:
        compact: list[str] = []
        seen: set[str] = set()
        for raw in values:
            text = re.sub(r"\s+", " ", str(raw or "")).strip()
            text = re.sub(r"^(idee|kandidat\s+\d+)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            if len(text) > 140:
                text = text[:137].rstrip() + "..."
            compact.append(text)
            if len(compact) >= limit:
                break
        return tuple(compact)

    def _read_idea(self, project_key: str, command_body: str) -> ContentOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        platform, normalized_body = self._resolve_platform(command_body)

        # Build context from docs (used for prompt and for docs-only fallback)
        doc_context = self._build_idea_doc_context(
            content=content_doc.content,
            state=state_doc.content,
        )

        # OpenAI path
        if self.openai_service is not None and self._integration_active_for("idea"):
            ok_context = self._load_operational_knowledge(project_key)
            analytics_context = self._load_analytics()
            platform_signals = self._load_platform_signals(project_key=project_key)
            try:
                return self._generate_idea_with_openai(
                    project_key=project_key,
                    command_body=normalized_body,
                    doc_context=doc_context,
                    ok_context=ok_context,
                    analytics_context=analytics_context,
                    platform=platform,
                    platform_context=platform_signals.get(platform),
                )
            except Exception as exc:
                _log.warning(
                    "content_ops idea: OpenAI call failed, falling back to docs path | error=%s",
                    exc,
                )

        # Docs-only path
        pillars_summary = doc_context["pillars_summary"]
        fit_summary = doc_context["fit_summary"]

        return self._build_result(
            project_key=project_key,
            action_type="idea",
            command_body=normalized_body,
            title="Content idea",
            summary="Content-Regeln geladen.",
            items=(
                pillars_summary,
                fit_summary,
                f"Kontext: {self._display_body(normalized_body)}",
            ),
            platform=platform,
        )

    def _build_idea_doc_context(
        self,
        *,
        content: str,
        state: str,
    ) -> dict[str, str]:
        pillars_text = extract_section(content, "Active Content Pillars") or ""
        pillars = list_items(pillars_text, max_items=5)
        pillars_str = " | ".join(pillars) if pillars else "keine"
        pillars_summary = f"Säulen: {pillars_str}"

        tone_text = extract_section(content, "Content Tone") or ""
        tone_str = trim(first_sentences(tone_text, 1)) if tone_text else "natürlich, direkt, nützlich"

        fit_text = extract_section(content, "Pillar Fit Rule") or ""
        fit_summary = trim(first_sentences(fit_text, 1)) if fit_text else ""

        phase_text = extract_section(state, "Current Phase") or ""
        phase_str = trim(first_sentences(phase_text, 1)) if phase_text else "Pre-bot operational phase"

        audience_text = extract_section(state, "Active Audience Assumption") or ""
        audience_str = trim(first_sentences(audience_text, 1)) if audience_text else "deutschsprachige Frauen 23-38"

        return {
            "pillars_str": pillars_str,
            "pillars_summary": pillars_summary,
            "tone_str": tone_str,
            "fit_summary": fit_summary,
            "phase_str": phase_str,
            "audience_str": audience_str,
        }

    def _generate_idea_with_openai(
        self,
        *,
        project_key: str,
        command_body: str,
        doc_context: dict[str, str],
        ok_context: "OperationalKnowledgeContext | None" = None,
        analytics_context: "AnalyticsContext | None" = None,
        platform: str,
        platform_context: "PlatformContext | None" = None,
    ) -> ContentOpResult:
        from operator_core.integrations.operational_knowledge_service import IDEA_CATEGORIES

        ok_block = ""
        if ok_context is not None and not ok_context.is_empty():
            ok_block = ok_context.to_prompt_block(*IDEA_CATEGORIES)

        analysis_block = ""
        if analytics_context is not None and not analytics_context.is_empty():
            analysis_block = analytics_context.to_prompt_block()
        platform_block = self._build_platform_signal_block(platform_context)
        platform_label = _PLATFORM_LABELS.get(platform, "TikTok")

        system_prompt = (
            "Du bist ein strukturierter Content-Assistent für das Projekt everydayengel."
            " Antworte ausschließlich auf Deutsch.\n\n"
            "Projekt-Kontext (bindend):\n"
            f"Plattform: {platform_label}\n"
            f"Säulen: {doc_context['pillars_str']}\n"
            f"Ton: {doc_context['tone_str']}\n"
            f"Phase: {doc_context['phase_str']}\n"
            f"Zielgruppe: {doc_context['audience_str']}\n\n"
            + (f"{ok_block}\n\n" if ok_block else "")
            + (f"{analysis_block}\n\n" if analysis_block else "")
            + (f"{platform_block}\n\n" if platform_block else "")
            + "Aufgabe: Liefere genau eine einzige starke Brainstorming-Idee."
            " Sie soll natürlich formuliert sein, konkret wirken und nicht in internen Säulen- oder Systembegriffen sprechen.\n\n"
            "Antwort exakt in diesem Format, keine weiteren Erklärungen:\n"
            "Idee: <eine konkrete, natürlich formulierte Idee in 1-3 Sätzen>"
        )

        user_prompt = command_body.strip() if command_body.strip() else "Neue Idee"

        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.7,
        )

        items = self._parse_idea_response(response.output_text)
        airtable_record_id = self._try_create_airtable_record(
            project_key=project_key,
            command_body=command_body,
            parsed_items=items,
        )

        return ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="idea",
            command_body=command_body,
            title="Content idea",
            summary="Idee generiert.",
            items=items,
            openai_used=True,
            airtable_record_id=airtable_record_id,
            platform=platform,
        )

    def _parse_idea_response(self, output_text: str) -> tuple[str, ...]:
        """Parse structured key: value lines from OpenAI response into items."""
        keys = ("Idee",)
        result: list[str] = []

        for line in output_text.splitlines():
            stripped = line.strip()
            for key in keys:
                if stripped.lower().startswith(f"{key.lower()}:"):
                    value = stripped[len(key) + 1:].strip()
                    if value:
                        result.append(f"{key}: {value}")
                    break

        if not result:
            # Unexpected format — use raw text truncated as single item
            result.append(trim(output_text.replace("\n", " "), max_chars=180))

        return tuple(result)

    def _parse_idea_candidates(self, output_text: str) -> list[str]:
        """Parse 'Kandidat N:' lines from output, returning plain idea texts (no prefix)."""
        import re as _re
        result = []
        for line in output_text.splitlines():
            stripped = line.strip()
            m = _re.match(r"kandidat\s+\d+\s*:\s*(.+)", stripped, _re.IGNORECASE)
            if m:
                result.append(m.group(1).strip())
        if not result:
            # Fallback: reuse existing parser and strip "Idee:" prefix
            parsed = self._parse_idea_response(output_text)
            return [p.removeprefix("Idee:").strip() for p in parsed]
        return result[:3]

    def _filter_mirror_fidelity_candidates(
        self,
        candidates: list[str],
        *,
        distiller: IdeaDistiller,
        anchor_tokens: tuple[str, ...],
        user_prompt: str = "",
    ) -> list[str]:
        """Keep MIRROR candidates that preserve anchors without echoing the raw prompt."""
        passed: list[str] = []
        for candidate in candidates:
            distilled = distiller.distill(candidate, anchor_tokens=anchor_tokens)
            fidelity = IdeaQualityGate.mirror_fidelity_score(distilled, anchor_tokens)
            if (
                fidelity >= IdeaQualityGate.MIRROR_FIDELITY_THRESHOLD
                and not IdeaQualityGate.is_mirror_prompt_echo(distilled, user_prompt)
            ):
                passed.append(candidate)
        return passed

    @staticmethod
    def _rejected_history_refs(
        recent_history: tuple[IdeaHistoryReference, ...],
    ) -> tuple[IdeaHistoryReference, ...]:
        return tuple(ref for ref in recent_history if ref.source == "recent_idea_rejected")

    def _has_rejected_same_core(
        self,
        candidates: list[str],
        *,
        foundation_result: AnalysisFoundationResult,
        recent_history: tuple[IdeaHistoryReference, ...],
    ) -> bool:
        rejected_history = self._rejected_history_refs(recent_history)
        if not rejected_history:
            return False
        return any(
            self.duplicate_guard.evaluate_core_repeat(
                candidate_idea=candidate,
                foundation_result=foundation_result,
                recent_history=rejected_history,
            ).repeated
            for candidate in candidates
        )

    @staticmethod
    def _risk_references_rejected_history(risk: object) -> bool:
        blocking_items = getattr(risk, "blocking_items", ())
        return any("[recent_idea_rejected]" in str(item) for item in blocking_items)

    def _filter_same_core_repeat_candidates(
        self,
        candidates: list[str],
        *,
        foundation_result: AnalysisFoundationResult,
        recent_posts: list[str],
        recent_drafts: list[str],
        recent_ideas: list[str],
        recent_history: tuple[IdeaHistoryReference, ...] = (),
    ) -> list[str]:
        """Remove candidates that reuse an already posted or recently saved idea core."""
        passed: list[str] = []
        for candidate in candidates:
            repeat = self.duplicate_guard.evaluate_core_repeat(
                candidate_idea=candidate,
                foundation_result=foundation_result,
                recent_posts=recent_posts,
                recent_drafts=recent_drafts,
                recent_ideas=recent_ideas,
                recent_history=recent_history,
            )
            if repeat.repeated:
                _log.info(
                    "idea_core_repeat: candidate blocked | reason=%s | refs=%s",
                    repeat.reason,
                    "; ".join(repeat.blocking_items),
                )
                continue
            passed.append(candidate)
        return passed

    def _pick_fidelity_checked_idea(
        self,
        candidates: list[str],
        *,
        quality_gate: IdeaQualityGate,
        distiller: IdeaDistiller,
        anchor_tokens: tuple[str, ...],
        sharpen_mode: bool,
        user_prompt: str = "",
    ) -> tuple[str, float]:
        """
        Pick the best candidate, but in MIRROR mode first require deterministic
        fidelity after final distillation so a strong internal score cannot drift
        away from the user's concrete prompt details.
        """
        if not candidates:
            return "", -99.0

        eligible = candidates
        if sharpen_mode:
            eligible = self._filter_mirror_fidelity_candidates(
                candidates,
                distiller=distiller,
                anchor_tokens=anchor_tokens,
                user_prompt=user_prompt,
            )
            if not eligible:
                return "", -99.0

        best_idea, best_score = quality_gate.pick_best(
            eligible,
            anchor_tokens=anchor_tokens,
            sharpen_mode=sharpen_mode,
        )
        candidate_text = distiller.distill(best_idea, anchor_tokens=anchor_tokens)
        if sharpen_mode:
            fidelity = IdeaQualityGate.mirror_fidelity_score(candidate_text, anchor_tokens)
            if (
                fidelity < IdeaQualityGate.MIRROR_FIDELITY_THRESHOLD
                or IdeaQualityGate.is_mirror_prompt_echo(candidate_text, user_prompt)
            ):
                return "", -99.0
        return candidate_text, best_score

    def _naturalize_mirror_output(
        self,
        candidate: str,
        *,
        anchor_tokens: tuple[str, ...],
        user_prompt: str,
        model: str,
    ) -> str:
        """
        Apply a tight one-sentence LLM pass to make a retained MIRROR candidate
        read more naturally while keeping all fidelity anchors intact.

        Returns the original unchanged when:
        - openai_service is unavailable
        - the naturalized result fails the fidelity gate
        - the naturalized result is still a raw prompt echo
        - an exception occurs
        """
        if self.openai_service is None:
            return candidate
        anchor_hint = ", ".join(anchor_tokens[:6]) if anchor_tokens else ""
        system = (
            "Du formulierst einen knappen MIRROR-Alltagssatz als natürlichen ersten Satz.\n\n"
            "ZIEL: Gleiche Szene und Kernhandlung, aber als echter gesprochener Satz — "
            "erste Person ('ich'), natürlicher Redefluss.\n\n"
            "ERLAUBTE NATURALISIERUNGEN:\n"
            "- 'wegen X' → 'weil mir X wird' / 'weil ich X merke'\n"
            "- 'was mitmuss' → 'was ich mitnehmen muss'\n"
            "- 'checken' → 'prüfen' / 'nachschauen'\n"
            "- Rahmung: 'merke ich inzwischen manchmal', 'stelle ich fest'\n"
            "- Kleiner Satzumbau für besseren Redefluss\n\n"
            "VERBOTEN:\n"
            "- 'sitzen' → 'abstützen', 'anlehnen', 'stützen'\n"
            "- 'Pause' durch Stütz- oder Bewegungsersatz ersetzen\n"
            "- Neue Objekte, Orte, Personen oder Unterereignisse erfinden\n"
            "- Ratschläge, Tipps oder Produktionsanweisungen\n\n"
            "BEISPIELE (zeigen den Stil — nicht kopieren):\n"
            "Roh: 'Beim Kochen muss ich plötzlich sitzen wegen Schwindel.'\n"
            "Gut: 'Beim Kochen merke ich inzwischen manchmal plötzlich, dass ich mich hinsetzen muss, weil mir schwindelig wird.'\n\n"
            "Roh: 'Im Supermarkt brauche ich plötzlich eine Pause.'\n"
            "Gut: 'Im Supermarkt merke ich manchmal plötzlich, dass ich kurz eine Pause brauche.'\n\n"
            "Roh: 'Bevor ich rausgehe, checke ich doppelt, was mitmuss.'\n"
            "Gut: 'Bevor ich rausgehe, prüfe ich inzwischen doppelt, was ich mitnehmen muss.'\n\n"
            + (f"Ankerwörter (müssen erkennbar bleiben): {anchor_hint}\n" if anchor_hint else "")
            + "\nAntworte NUR mit dem natürlichen Satz. Keine Erklärung."
        )
        try:
            resp = self.openai_service.complete_messages(
                system_prompt=system,
                user_prompt=candidate,
                model=model,
                fallback_to_default=True,
                temperature=0.3,
            )
            naturalized = resp.output_text.strip().strip('"').strip("'").strip()
            # Reject multi-sentence / multi-line outputs — we asked for exactly one sentence.
            if "\n" in naturalized:
                _log.warning("mirror_naturalize: rejected multi-line output | keeping original")
                return candidate
            fidelity = IdeaQualityGate.mirror_fidelity_score(naturalized, anchor_tokens)
            is_echo = IdeaQualityGate.is_mirror_prompt_echo(naturalized, user_prompt)
            if fidelity >= IdeaQualityGate.MIRROR_FIDELITY_THRESHOLD and not is_echo:
                _log.info(
                    "mirror_naturalize: applied | fidelity=%.2f | in_len=%d | out_len=%d",
                    fidelity, len(candidate), len(naturalized),
                )
                return naturalized
            _log.warning(
                "mirror_naturalize: rejected | fidelity=%.2f | echo=%s | keeping original",
                fidelity, is_echo,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("mirror_naturalize: error keeping original | %s", exc)
        return candidate

    def build_idea_evidence_pack(
        self,
        *,
        project_key: str,
        idea_result: FoundationIdeaResult,
    ) -> EvidencePack:
        source_refs: list[str] = []
        for snapshot in idea_result.selected_snapshots:
            source_refs.extend(snapshot.source_refs)

        evidence_lines: list[str] = [
            f"Writer objective: {idea_result.writer_brief.objective}",
            f"Audience: {idea_result.writer_brief.audience}",
        ]
        evidence_lines.extend(
            f"Constraint: {constraint}"
            for constraint in idea_result.writer_brief.constraints[:3]
        )
        evidence_lines.extend(
            f"Snapshot: {snapshot.title} | {snapshot.summary}"
            for snapshot in idea_result.selected_snapshots
        )
        evidence_lines.extend(
            f"Idea output: {item}"
            for item in idea_result.content_result.items[:2]
        )

        created_at = (
            idea_result.selected_snapshots[0].created_at
            if idea_result.selected_snapshots
            else ""
        )
        return EvidencePack(
            evidence_pack_id=f"ep_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            summary="Evidence pack linking grounded analysis snapshots to the generated idea output.",
            snapshot_ids=tuple(snapshot.snapshot_id for snapshot in idea_result.selected_snapshots),
            source_refs=tuple(dict.fromkeys(source_refs)),
            evidence_lines=tuple(evidence_lines),
        )

    def build_vollauto_evidence_pack(
        self,
        *,
        project_key: str,
        draft_result: FoundationDraftResult,
    ) -> EvidencePack:
        return self._build_structured_draft_evidence_pack(
            project_key=project_key,
            draft_result=draft_result,
            action_type="vollauto",
        )

    def build_draft_evidence_pack(
        self,
        *,
        project_key: str,
        draft_result: FoundationDraftResult,
    ) -> EvidencePack:
        return self._build_structured_draft_evidence_pack(
            project_key=project_key,
            draft_result=draft_result,
            action_type="draft",
        )

    def _build_structured_draft_evidence_pack(
        self,
        *,
        project_key: str,
        draft_result: FoundationDraftResult,
        action_type: str,
    ) -> EvidencePack:
        source_refs: list[str] = []
        for snapshot in draft_result.selected_snapshots:
            source_refs.extend(snapshot.source_refs)

        evidence_lines: list[str] = [
            f"Writer objective: {draft_result.writer_brief.objective}",
            f"Audience: {draft_result.writer_brief.audience}",
        ]
        evidence_lines.extend(
            f"Constraint: {constraint}"
            for constraint in draft_result.writer_brief.constraints[:3]
        )
        evidence_lines.extend(
            f"Snapshot: {snapshot.title} | {snapshot.summary}"
            for snapshot in draft_result.selected_snapshots
        )
        evidence_lines.extend(
            f"Draft output: {item}"
            for item in draft_result.content_result.items[:4]
        )

        created_at = (
            draft_result.selected_snapshots[0].created_at
            if draft_result.selected_snapshots
            else ""
        )
        return EvidencePack(
            evidence_pack_id=f"ep_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            summary=f"Evidence pack linking grounded analysis snapshots to the generated {action_type} output.",
            snapshot_ids=tuple(snapshot.snapshot_id for snapshot in draft_result.selected_snapshots),
            source_refs=tuple(dict.fromkeys(source_refs)),
            evidence_lines=tuple(evidence_lines),
        )

    def build_caption_evidence_pack(
        self,
        *,
        project_key: str,
        caption_result: FoundationCaptionResult,
    ) -> EvidencePack:
        source_refs: list[str] = []
        for snapshot in caption_result.selected_snapshots:
            source_refs.extend(snapshot.source_refs)

        evidence_lines: list[str] = [
            f"Writer objective: {caption_result.writer_brief.objective}",
            f"Audience: {caption_result.writer_brief.audience}",
        ]
        evidence_lines.extend(
            f"Constraint: {constraint}"
            for constraint in caption_result.writer_brief.constraints[:3]
        )
        evidence_lines.extend(
            f"Snapshot: {snapshot.title} | {snapshot.summary}"
            for snapshot in caption_result.selected_snapshots
        )
        evidence_lines.extend(
            f"Caption output: {item}"
            for item in caption_result.content_result.items[:4]
        )

        created_at = (
            caption_result.selected_snapshots[0].created_at
            if caption_result.selected_snapshots
            else ""
        )
        return EvidencePack(
            evidence_pack_id=f"ep_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            summary="Evidence pack linking grounded analysis snapshots to the generated caption output.",
            snapshot_ids=tuple(snapshot.snapshot_id for snapshot in caption_result.selected_snapshots),
            source_refs=tuple(dict.fromkeys(source_refs)),
            evidence_lines=tuple(evidence_lines),
        )

    def build_hook_evidence_pack(
        self,
        *,
        project_key: str,
        hook_result: FoundationHookResult,
    ) -> EvidencePack:
        source_refs: list[str] = []
        for snapshot in hook_result.selected_snapshots:
            source_refs.extend(snapshot.source_refs)

        evidence_lines: list[str] = [
            f"Writer objective: {hook_result.writer_brief.objective}",
            f"Audience: {hook_result.writer_brief.audience}",
        ]
        evidence_lines.extend(
            f"Constraint: {constraint}"
            for constraint in hook_result.writer_brief.constraints[:3]
        )
        evidence_lines.extend(
            f"Snapshot: {snapshot.title} | {snapshot.summary}"
            for snapshot in hook_result.selected_snapshots
        )
        evidence_lines.extend(
            f"Hook output: {item}"
            for item in hook_result.content_result.items[:4]
        )

        created_at = (
            hook_result.selected_snapshots[0].created_at
            if hook_result.selected_snapshots
            else ""
        )
        return EvidencePack(
            evidence_pack_id=f"ep_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            summary="Evidence pack linking grounded analysis snapshots to the generated hook output.",
            snapshot_ids=tuple(snapshot.snapshot_id for snapshot in hook_result.selected_snapshots),
            source_refs=tuple(dict.fromkeys(source_refs)),
            evidence_lines=tuple(evidence_lines),
        )

    def build_serie_evidence_pack(
        self,
        *,
        project_key: str,
        serie_result: FoundationSerieResult,
    ) -> EvidencePack:
        source_refs: list[str] = []
        for snapshot in serie_result.selected_snapshots:
            source_refs.extend(snapshot.source_refs)

        evidence_lines: list[str] = [
            f"Writer objective: {serie_result.writer_brief.objective}",
            f"Audience: {serie_result.writer_brief.audience}",
        ]
        evidence_lines.extend(
            f"Constraint: {constraint}"
            for constraint in serie_result.writer_brief.constraints[:3]
        )
        evidence_lines.extend(
            f"Snapshot: {snapshot.title} | {snapshot.summary}"
            for snapshot in serie_result.selected_snapshots
        )
        evidence_lines.extend(
            f"Serie output: {item}"
            for item in serie_result.content_result.items[:2]
        )

        created_at = (
            serie_result.selected_snapshots[0].created_at
            if serie_result.selected_snapshots
            else ""
        )
        return EvidencePack(
            evidence_pack_id=f"ep_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            summary="Evidence pack linking grounded analysis snapshots to the generated serie output.",
            snapshot_ids=tuple(snapshot.snapshot_id for snapshot in serie_result.selected_snapshots),
            source_refs=tuple(dict.fromkeys(source_refs)),
            evidence_lines=tuple(evidence_lines),
        )

    def build_title_evidence_pack(
        self,
        *,
        project_key: str,
        title_result: FoundationTitleResult,
    ) -> EvidencePack:
        source_refs: list[str] = []
        for snapshot in title_result.selected_snapshots:
            source_refs.extend(snapshot.source_refs)

        evidence_lines: list[str] = [
            f"Writer objective: {title_result.writer_brief.objective}",
            f"Audience: {title_result.writer_brief.audience}",
        ]
        evidence_lines.extend(
            f"Constraint: {constraint}"
            for constraint in title_result.writer_brief.constraints[:3]
        )
        evidence_lines.extend(
            f"Snapshot: {snapshot.title} | {snapshot.summary}"
            for snapshot in title_result.selected_snapshots
        )
        evidence_lines.extend(
            f"Title output: {item}"
            for item in title_result.content_result.items[:2]
        )

        created_at = (
            title_result.selected_snapshots[0].created_at
            if title_result.selected_snapshots
            else ""
        )
        return EvidencePack(
            evidence_pack_id=f"ep_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            summary="Evidence pack linking grounded analysis snapshots to the generated title output.",
            snapshot_ids=tuple(snapshot.snapshot_id for snapshot in title_result.selected_snapshots),
            source_refs=tuple(dict.fromkeys(source_refs)),
            evidence_lines=tuple(evidence_lines),
        )

    def build_cta_evidence_pack(
        self,
        *,
        project_key: str,
        cta_result: FoundationCtaResult,
    ) -> EvidencePack:
        source_refs: list[str] = []
        for snapshot in cta_result.selected_snapshots:
            source_refs.extend(snapshot.source_refs)

        evidence_lines: list[str] = [
            f"Writer objective: {cta_result.writer_brief.objective}",
            f"Audience: {cta_result.writer_brief.audience}",
        ]
        evidence_lines.extend(
            f"Constraint: {constraint}"
            for constraint in cta_result.writer_brief.constraints[:3]
        )
        evidence_lines.extend(
            f"Snapshot: {snapshot.title} | {snapshot.summary}"
            for snapshot in cta_result.selected_snapshots
        )
        evidence_lines.extend(
            f"CTA output: {item}"
            for item in cta_result.content_result.items[:2]
        )

        created_at = (
            cta_result.selected_snapshots[0].created_at
            if cta_result.selected_snapshots
            else ""
        )
        return EvidencePack(
            evidence_pack_id=f"ep_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            summary="Evidence pack linking grounded analysis snapshots to the generated cta output.",
            snapshot_ids=tuple(snapshot.snapshot_id for snapshot in cta_result.selected_snapshots),
            source_refs=tuple(dict.fromkeys(source_refs)),
            evidence_lines=tuple(evidence_lines),
        )

    def build_followup_evidence_pack(
        self,
        *,
        project_key: str,
        followup_result: FoundationFollowupResult,
    ) -> EvidencePack:
        source_refs: list[str] = []
        for snapshot in followup_result.selected_snapshots:
            source_refs.extend(snapshot.source_refs)

        evidence_lines: list[str] = [
            f"Writer objective: {followup_result.writer_brief.objective}",
            f"Audience: {followup_result.writer_brief.audience}",
            f"Mutation mode: {followup_result.mutation_mode}",
            f"Source action: {followup_result.source_action_type}",
            f"Instruction: {followup_result.instruction}",
        ]
        evidence_lines.extend(
            f"Constraint: {constraint}"
            for constraint in followup_result.writer_brief.constraints[:3]
        )
        evidence_lines.extend(
            f"Snapshot: {snapshot.title} | {snapshot.summary}"
            for snapshot in followup_result.selected_snapshots
        )
        evidence_lines.extend(
            f"Follow-up output: {item}"
            for item in followup_result.content_result.items[:6]
        )

        created_at = (
            followup_result.selected_snapshots[0].created_at
            if followup_result.selected_snapshots
            else ""
        )
        return EvidencePack(
            evidence_pack_id=f"ep_{uuid4().hex}",
            project_key=project_key,
            created_at=created_at,
            summary="Evidence pack linking grounded analysis snapshots to the generated proposal mutation output.",
            snapshot_ids=tuple(snapshot.snapshot_id for snapshot in followup_result.selected_snapshots),
            source_refs=tuple(dict.fromkeys(source_refs)),
            evidence_lines=tuple(evidence_lines),
        )

    def _try_create_airtable_record(
        self,
        *,
        project_key: str,
        command_body: str,
        parsed_items: tuple[str, ...],
    ) -> str | None:
        if self.airtable_service is None:
            return None

        fields: dict[str, str] = {
            "stage": "raw_idea",
            "project_key": project_key,
            "source_input": command_body or "",
            "created_by": "operator_core",
        }

        for item in parsed_items:
            if item.startswith("Idee:"):
                fields["title"] = item[len("Idee:"):].strip()

        try:
            record = self.airtable_service.create_record(  # type: ignore[union-attr]
                _CONTENT_IDEAS_TABLE,
                fields,
                project_key=project_key,
            )
            _log.info(
                "content_ops idea: airtable record created | project=%s record_id=%s",
                project_key,
                record.record_id,
            )
            return record.record_id
        except Exception as exc:
            _log.warning(
                "content_ops idea: airtable record creation failed | project=%s error=%s",
                project_key,
                exc,
            )
            return None

    def _select_idea_snapshots(
        self,
        *,
        snapshots: tuple[AnalysisSnapshot, ...],
        platform: str,
    ) -> tuple[AnalysisSnapshot, ...]:
        effective_platform = platform or "tiktok"
        selected: list[AnalysisSnapshot] = []
        platform_snapshot = next(
            (
                snapshot
                for snapshot in snapshots
                if snapshot.scope == "platform" and snapshot.platform_key == effective_platform
            ),
            None,
        )
        cross_snapshot = next(
            (snapshot for snapshot in snapshots if snapshot.scope == "cross_platform"),
            None,
        )
        if platform_snapshot is not None:
            selected.append(platform_snapshot)
        if cross_snapshot is not None:
            selected.append(cross_snapshot)
        return tuple(selected or snapshots[:1])

    def _build_foundation_idea_system_prompt(
        self,
        *,
        platform: str,
        writer_brief: WriterBrief,
        selected_snapshots: tuple[AnalysisSnapshot, ...],
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
        sharpen_mode: bool = False,
        anchor_tokens: tuple[str, ...] = (),
        history_steering_block: str = "",
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(platform or "tiktok", "TikTok")
        snapshot_blocks = "\n\n".join(
            self._format_snapshot_for_prompt(snapshot)
            for snapshot in selected_snapshots
        )
        constraints_block = "\n".join(f"- {constraint}" for constraint in writer_brief.constraints)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)

        if sharpen_mode:
            anchor_hint = ", ".join(anchor_tokens[:6]) if anchor_tokens else ""
            mode_block = (
                "MIRROR-MODUS: Die Nutzerin hat einen konkreten persönlichen Moment beschrieben.\n"
                "Deine EINZIGE Aufgabe: Formuliere genau DIESEN Moment in drei klaren ersten Sätzen.\n\n"
                "MIRROR-REGELN (absolut, keine Ausnahmen):\n"
                "- ERHALTE die exakten Substantive und Verben der Nutzerin — ersetze sie NICHT durch Varianten\n"
                "  'sitzen' bleibt 'sitzen' — NICHT 'anlehnen', 'stützen' oder 'Pause machen'\n"
                "  'Schwindel' bleibt 'Schwindel' — NICHT 'Gleichgewichtsprobleme' oder 'Kreislaufprobleme'\n"
                "  'beim Kochen' bleibt 'beim Kochen' — NICHT 'in der Küche' oder 'am Herd'\n"
                "- Bleib in der EXAKTEN Szene und Situation — wechsle NICHT das Setting\n"
                "- Bleib beim SELBEN Körper- oder Alltagsproblem — keine benachbarte Variante\n"
                "- Erste Person ('ich', 'mir', 'mein') — Julias eigene Stimme\n"
                "- Genau ein Satz — kein zweiter Satz, keine Regieanweisung\n"
                "- Kein Roh-Echo des User-Prompts: mache daraus einen natürlichen Satz, ohne Kernwörter zu ersetzen\n"
                + (f"- Kernbegriffe der Nutzerin (müssen im Kandidaten erkennbar sein): {anchor_hint}\n" if anchor_hint else "")
                + "NICHT ERLAUBT:\n"
                "- Das konkrete Wort der Nutzerin durch ein 'ähnliches' ersetzen\n"
                "- Den User-Prompt nur mit 'Ich muss ...' zu wiederholen\n"
                "- Die Szene öffnen, verbreitern oder in benachbarte Lifestyle-Themen überführen\n"
                "- Etwas hinzufügen, das die Nutzerin NICHT erwähnt hat\n\n"
            )
        else:
            mode_block = (
                "IDEATION-MODUS: Kein konkreter User-Moment vorgegeben — generiere frische Ideen.\n\n"
                "STIMME: Schreibe wie Julia selbst denkt — in der ersten Person ('ich', 'mir', 'mein').\n"
                "Kein Content-Sprech, keine Konzepte, kein Reporting-Ton.\n"
                "Kein Satz der klingt wie ein Themen-Briefing oder eine Video-Ankündigung.\n\n"
                "IDEATION-REGELN:\n"
                "Bevorzuge stark:\n"
                "- Eine einzige kleine Alltagsbeobachtung (keine Sammlung mehrerer Dinge)\n"
                "- Konkrete körperliche oder praktische Reibung in einer echten Szene\n"
                "- Veränderte Praktikabilität im Alltag ('das geht jetzt nicht mehr so')\n"
                "- Micro-Observation mit einem klaren Kern — kein Konzept, keine Übersicht\n"
                "Vermeide strikt:\n"
                "- Breite Schwangerschaftskonzepte ('versteckte Helden', 'lustigste Momente', 'Lebensabschnitt')\n"
                "- Lifestyle-Überblick-Ideen ohne konkrete Szene\n"
                "- Shopping / Outfit / Kleiderschrank / Room-Setup\n"
                "- Ideen mit Tipps-Liste-Logik ('5 Dinge die…', 'Tipps für…')\n"
                "- Breite Symptom-Übersichten ohne Verankerung in einer spezifischen Situation\n\n"
                "SO SOLL ES KLINGEN (Zielton — nicht kopieren, nur als Stimmreferenz):\n"
                "✓ 'Mir ist erst letzte Woche aufgefallen, dass ich beim Aufstehen vom Sofa inzwischen eine Hand brauche.'\n"
                "✓ 'Mein Rücken meldet sich jetzt schon nach zehn Minuten Stehen — das kannte ich vorher nicht.'\n"
                "✓ 'Ich hab heute gemerkt, dass Schuhe binden jetzt wirklich eine kleine Planung braucht.'\n\n"
                "NICHT SO (typische Fehler):\n"
                "✗ 'Wie sich der Körper in der Schwangerschaft verändert — ein ehrlicher Einblick'\n"
                "✗ 'Diese kleinen Alltagsmomente die sich plötzlich verändern'\n"
                "✗ 'Julia zeigt, wie sie trotzdem aktiv bleibt'\n\n"
            )

        return (
            "Du hilfst Julia, ehrliche Alltagsmomente aus der Schwangerschaft als TikTok-Ideen zu formulieren."
            " Antworte ausschließlich auf Deutsch.\n\n"
            "Writer-Brief (bindend):\n"
            f"Ziel: {writer_brief.objective}\n"
            f"Zielgruppe: {writer_brief.audience}\n"
            f"Plattform: {platform_label}\n"
            f"Constraints:\n{constraints_block}\n\n"
            "Analysis Snapshots (bindend):\n"
            f"{snapshot_blocks}\n\n"
            f"{weekly_block}"
            + (f"{history_steering_block}\n\n" if history_steering_block else "")
            + mode_block
            + "Antwort exakt in diesem Format, keine weiteren Erklärungen:\n"
            "Kandidat 1: <ein einziger konkreter Satz — kein zweiter Satz, keine Regieanweisung>\n"
            "Kandidat 2: <ein einziger konkreter Satz — kein zweiter Satz, keine Regieanweisung>\n"
            "Kandidat 3: <ein einziger konkreter Satz — kein zweiter Satz, keine Regieanweisung>"
        )

    def _build_foundation_vollauto_system_prompt(
        self,
        *,
        platform: str,
        writer_brief: WriterBrief,
        selected_snapshots: tuple[AnalysisSnapshot, ...],
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(platform or "tiktok", "TikTok")
        snapshot_blocks = "\n\n".join(
            self._format_snapshot_for_prompt(snapshot)
            for snapshot in selected_snapshots
        )
        constraints_block = "\n".join(f"- {constraint}" for constraint in writer_brief.constraints)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)

        return (
            "Du bist ein strukturierter Content-Assistent für das Projekt everydayengel."
            " Antworte ausschließlich auf Deutsch.\n\n"
            "Writer-Brief (bindend):\n"
            f"Ziel: {writer_brief.objective}\n"
            f"Zielgruppe: {writer_brief.audience}\n"
            f"Plattform: {platform_label}\n"
            f"Constraints:\n{constraints_block}\n\n"
            "Analysis Snapshots (bindend):\n"
            f"{snapshot_blocks}\n\n"
            f"{weekly_block}"
            "Aufgabe: Erstelle einen konkreten, produktionsreifen Voll-Auto-Vorschlag."
            " Nutze die Analyse-Snapshots als primäre Grounding-Schicht."
            " Der Vorschlag muss plattformspezifisch, umsetzbar und klar auf belegbare Signale gestützt sein."
            " Keine Meta-Erklärung, keine freien Listen außerhalb des Zielformats.\n\n"
            "Antwort exakt in diesem Format, keine weiteren Erklärungen:\n"
            "Serie/Thema: <kurzes Thema>\n"
            "Title: <Kernaussage in 1 Satz>\n"
            "Hook: <Einstiegszeile oder -bild in 1 Satz>\n"
            "CTA: <weicher oder empfehlender CTA>\n"
            "Caption: <kurze Caption>\n"
            "Format: <Videoformat>\n"
            "Bereit: <kurze Einschätzung der Produktionsreife>"
        )

    def _build_foundation_caption_system_prompt(
        self,
        *,
        platform: str,
        doc_context: dict[str, str],
        writer_brief: WriterBrief,
        selected_snapshots: tuple[AnalysisSnapshot, ...],
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(platform or "tiktok", "TikTok")
        snapshot_blocks = "\n\n".join(
            self._format_snapshot_for_prompt(snapshot)
            for snapshot in selected_snapshots
        )
        constraints_block = "\n".join(f"- {constraint}" for constraint in writer_brief.constraints)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)

        return (
            "Du bist ein strukturierter Content-Assistent für das Projekt everydayengel."
            " Antworte ausschließlich auf Deutsch.\n\n"
            "Writer-Brief (bindend):\n"
            f"Ziel: {writer_brief.objective}\n"
            f"Zielgruppe: {writer_brief.audience}\n"
            f"Plattform: {platform_label}\n"
            f"Constraints:\n{constraints_block}\n\n"
            "Caption-Kontext (bindend):\n"
            f"Ton: {doc_context['tone_str']}\n"
            f"Caption-Regeln: {doc_context['caption_rules_str']}\n"
            f"Caption-Funktion (eine davon): {doc_context['caption_functions_str']}\n"
            f"Erlaubte CTA-Richtungen: {doc_context['cta_str']}\n\n"
            "Analysis Snapshots (bindend):\n"
            f"{snapshot_blocks}\n\n"
            f"{weekly_block}"
            "Aufgabe: Schreibe eine konkrete Caption fuer ein Kurzvideo."
            " Nutze die Analyse-Snapshots als primaere Grounding-Schicht."
            " Die Caption muss kurz und klar sein, den Ton treffen, eine passende Caption-Funktion erfuellen"
            " und einen weichen CTA enthalten."
            " Keine Meta-Erklaerung, keine freien Listen ausserhalb des Zielformats.\n\n"
            "Antwort exakt in diesem Format, keine weiteren Erklaerungen:\n"
            "Caption: <der Caption-Text>\n"
            "CTA-Richtung: <weicher CTA-Typ>\n"
            "Ton-Check: <kurze Einschätzung ob Ton passt>\n"
            "Länge-Check: <passt die Länge zur Plattform>"
        )

    def _build_foundation_hook_system_prompt(
        self,
        *,
        platform: str,
        doc_context: dict[str, str],
        writer_brief: WriterBrief,
        selected_snapshots: tuple[AnalysisSnapshot, ...],
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(platform or "tiktok", "TikTok")
        snapshot_blocks = "\n\n".join(
            self._format_snapshot_for_prompt(snapshot)
            for snapshot in selected_snapshots
        )
        constraints_block = "\n".join(f"- {constraint}" for constraint in writer_brief.constraints)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)

        return (
            "Du bist ein strukturierter Content-Assistent fuer das Projekt everydayengel."
            " Antworte ausschliesslich auf Deutsch.\n\n"
            "Writer-Brief (bindend):\n"
            f"Ziel: {writer_brief.objective}\n"
            f"Zielgruppe: {writer_brief.audience}\n"
            f"Plattform: {platform_label}\n"
            f"Constraints:\n{constraints_block}\n\n"
            "Hook-Kontext (bindend):\n"
            f"Content-Saeulen: {doc_context['pillars_str']}\n"
            f"Ton: {doc_context['tone_str']}\n"
            f"Klarheits-Regeln: {doc_context['clarity_str']}\n"
            f"Hook-Leitlinien: {doc_context['hook_guidelines_str']}\n"
            f"Moegliche Hook-Eroeffnungstypen: {doc_context['hook_types_str']}\n\n"
            "Analysis Snapshots (bindend):\n"
            f"{snapshot_blocks}\n\n"
            f"{weekly_block}"
            "Aufgabe: Entwickle einen konkreten, starken Hook fuer ein Kurzvideo."
            " Nutze die Analyse-Snapshots als primaere Grounding-Schicht."
            " Der Hook muss in den ersten 1-2 Sekunden wirken, einen passenden Eroeffnungstyp verwenden"
            " und den Ton treffen."
            " Keine Meta-Erklaerung, keine freien Listen ausserhalb des Zielformats.\n\n"
            "Antwort exakt in diesem Format, keine weiteren Erklaerungen:\n"
            "Hook-Typ: <einer der Eroeffnungstypen>\n"
            "Eröffnung: <konkrete erste Zeile oder erstes Bild in 1 Satz>\n"
            "Versprechen: <was der Zuschauer gleich bekommt in 1 Satz>\n"
            "Format: <Videoformat>\n"
            "Stärke-Check: <kurze Einschätzung ob der Hook stark genug ist>"
        )

    def _build_foundation_serie_system_prompt(
        self,
        *,
        platform: str,
        rules_block: str,
        writer_brief: WriterBrief,
        selected_snapshots: tuple[AnalysisSnapshot, ...],
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(platform or "tiktok", "TikTok")
        snapshot_blocks = "\n\n".join(
            self._format_snapshot_for_prompt(snapshot)
            for snapshot in selected_snapshots
        )
        constraints_block = "\n".join(f"- {constraint}" for constraint in writer_brief.constraints)
        return (
            "Du bist ein strukturierter Content-Assistent fuer das Projekt everydayengel."
            " Antworte ausschliesslich auf Deutsch.\n\n"
            "Writer-Brief (bindend):\n"
            f"Ziel: {writer_brief.objective}\n"
            f"Zielgruppe: {writer_brief.audience}\n"
            f"Plattform: {platform_label}\n"
            f"Constraints:\n{constraints_block}\n\n"
            + (f"Projekt-Regeln (bindend):\n{rules_block}\n\n" if rules_block else "")
            + "Analysis Snapshots (bindend):\n"
            + f"{snapshot_blocks}\n\n"
            + "Aufgabe: Liefere genau einen plattformpassenden Vorschlag fuer das Feld Serie/Thema."
            + " Nutze die Analyse-Snapshots als primaere Grounding-Schicht."
            + " Keine Meta-Erklaerung, keine Listen ausserhalb des Zielformats.\n\n"
            + "Antwort exakt in diesem Format, keine weiteren Erklaerungen:\n"
            + "Serie/Thema: <Wert>"
        )

    def _build_foundation_title_system_prompt(
        self,
        *,
        platform: str,
        rules_block: str,
        writer_brief: WriterBrief,
        selected_snapshots: tuple[AnalysisSnapshot, ...],
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(platform or "tiktok", "TikTok")
        snapshot_blocks = "\n\n".join(
            self._format_snapshot_for_prompt(snapshot)
            for snapshot in selected_snapshots
        )
        constraints_block = "\n".join(f"- {constraint}" for constraint in writer_brief.constraints)
        return (
            "Du bist ein strukturierter Content-Assistent fuer das Projekt everydayengel."
            " Antworte ausschliesslich auf Deutsch.\n\n"
            "Writer-Brief (bindend):\n"
            f"Ziel: {writer_brief.objective}\n"
            f"Zielgruppe: {writer_brief.audience}\n"
            f"Plattform: {platform_label}\n"
            f"Constraints:\n{constraints_block}\n\n"
            + (f"Projekt-Regeln (bindend):\n{rules_block}\n\n" if rules_block else "")
            + "Analysis Snapshots (bindend):\n"
            + f"{snapshot_blocks}\n\n"
            + "Aufgabe: Liefere genau einen plattformpassenden Vorschlag fuer das Feld Title."
            + " Nutze die Analyse-Snapshots als primaere Grounding-Schicht."
            + " Keine Meta-Erklaerung, keine Listen ausserhalb des Zielformats.\n\n"
            + "Antwort exakt in diesem Format, keine weiteren Erklaerungen:\n"
            + "Title: <Wert>"
        )

    def _build_foundation_cta_system_prompt(
        self,
        *,
        platform: str,
        rules_block: str,
        writer_brief: WriterBrief,
        selected_snapshots: tuple[AnalysisSnapshot, ...],
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(platform or "tiktok", "TikTok")
        snapshot_blocks = "\n\n".join(
            self._format_snapshot_for_prompt(snapshot)
            for snapshot in selected_snapshots
        )
        constraints_block = "\n".join(f"- {constraint}" for constraint in writer_brief.constraints)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)

        return (
            "Du bist ein strukturierter Content-Assistent fuer das Projekt everydayengel."
            " Antworte ausschliesslich auf Deutsch.\n\n"
            "Writer-Brief (bindend):\n"
            f"Ziel: {writer_brief.objective}\n"
            f"Zielgruppe: {writer_brief.audience}\n"
            f"Plattform: {platform_label}\n"
            f"Constraints:\n{constraints_block}\n\n"
            + (f"Projekt-Regeln (bindend):\n{rules_block}\n\n" if rules_block else "")
            + "Analysis Snapshots (bindend):\n"
            + f"{snapshot_blocks}\n\n"
            + f"{weekly_block}"
            + "Aufgabe: Liefere genau einen plattformpassenden Vorschlag fuer das Feld CTA."
            + " Nutze die Analyse-Snapshots als primaere Grounding-Schicht."
            + " Keine Meta-Erklaerung, keine Listen ausserhalb des Zielformats.\n\n"
            + "Antwort exakt in diesem Format, keine weiteren Erklaerungen:\n"
            + "CTA: <Wert>"
        )

    def _format_snapshot_for_prompt(self, snapshot: AnalysisSnapshot) -> str:
        analytics_lines = "\n".join(f"- {line}" for line in snapshot.analytics_summary_lines[:4])
        rule_lines = "\n".join(f"- {line}" for line in snapshot.rule_summary_lines[:4])
        return (
            f"{snapshot.title}\n"
            f"Summary: {snapshot.summary}\n"
            f"Analytics:\n{analytics_lines or '- keine'}\n"
            f"Rules:\n{rule_lines or '- keine'}"
        )

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
        
        return "\n".join(lines) + "\n\n"

    def _read_hook(self, project_key: str, command_body: str) -> ContentOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        content = content_doc.content

        doc_context = self._build_hook_doc_context(content=content)

        # OpenAI path
        if self.openai_service is not None and self._integration_active_for("hook"):
            ok_context = self._load_operational_knowledge(project_key)
            try:
                return self._generate_hook_with_openai(
                    project_key=project_key,
                    command_body=command_body,
                    doc_context=doc_context,
                    ok_context=ok_context,
                )
            except Exception as exc:
                _log.warning(
                    "content_ops hook: OpenAI call failed, falling back to docs path | error=%s",
                    exc,
                )

        # Docs-only path
        return self._build_result(
            project_key=project_key,
            action_type="hook",
            command_body=command_body,
            title="Content hook",
            summary="Hook-Regeln geladen.",
            items=(
                doc_context["hook_rules_summary"],
                doc_context["tone_summary"],
                f"Kontext: {self._display_body(command_body)}",
            ),
        )

    def _build_hook_doc_context(self, *, content: str) -> dict[str, str]:
        hook_text = extract_section(content, "Hook Rules") or ""
        # Capture all list items: first group = timing/structure rules, second = opening types
        all_hook_items = list_items(hook_text, max_items=15)
        hook_rules_summary = (
            "Hook-Regeln: " + " | ".join(all_hook_items[:3])
            if all_hook_items else "Hook-Regeln: keine Doc-Quelle"
        )
        # Items beyond the first 6 structural rules are the hook opening types
        hook_types = all_hook_items[6:] if len(all_hook_items) > 6 else []
        hook_types_str = (
            " | ".join(hook_types) if hook_types
            else "Neugier | Nützlichkeit | Wiedererkennung"
        )
        # Full guidelines string for the prompt (all items)
        hook_guidelines_str = (
            " | ".join(all_hook_items) if all_hook_items
            else "hook in 1-2 Sekunden, ein Kernversprechen"
        )

        tone_text = extract_section(content, "Content Tone") or ""
        tone_items = list_items(tone_text, max_items=4)
        tone_str = ", ".join(tone_items) if tone_items else "natürlich, direkt, nützlich, ehrlich"
        tone_summary = f"Ton: {tone_str}" if tone_str else "Ton: nicht geladen"

        clarity_text = extract_section(content, "Clarity Rules") or ""
        clarity_items = list_items(clarity_text, max_items=3)
        clarity_str = (
            " | ".join(clarity_items) if clarity_items
            else "ein Hauptpunkt, eine klare Aussage"
        )

        pillars_text = extract_section(content, "Active Content Pillars") or ""
        pillars = list_items(pillars_text, max_items=5)
        pillars_str = " | ".join(pillars) if pillars else "Alltag | Routinen | Ehrliche Erfahrungen"

        return {
            "hook_rules_summary": hook_rules_summary,
            "tone_summary": tone_summary,
            "hook_guidelines_str": hook_guidelines_str,
            "hook_types_str": hook_types_str,
            "tone_str": tone_str,
            "clarity_str": clarity_str,
            "pillars_str": pillars_str,
        }

    def _generate_hook_with_openai(
        self,
        *,
        project_key: str,
        command_body: str,
        doc_context: dict[str, str],
        ok_context: "OperationalKnowledgeContext | None" = None,
    ) -> ContentOpResult:
        from operator_core.integrations.operational_knowledge_service import IDEA_CATEGORIES

        ok_block = ""
        if ok_context is not None and not ok_context.is_empty():
            ok_block = ok_context.to_prompt_block(*IDEA_CATEGORIES)

        system_prompt = (
            "Du bist ein strukturierter Content-Assistent für das Projekt everydayengel."
            " Antworte ausschließlich auf Deutsch.\n\n"
            "Projekt-Kontext (bindend):\n"
            f"Content-Säulen: {doc_context['pillars_str']}\n"
            f"Ton: {doc_context['tone_str']}\n"
            f"Klarheits-Regeln: {doc_context['clarity_str']}\n"
            f"Hook-Leitlinien: {doc_context['hook_guidelines_str']}\n"
            f"Mögliche Hook-Eröffnungstypen: {doc_context['hook_types_str']}\n\n"
            f"{ok_block}\n\n" if ok_block else ""
        ) + (
            "Aufgabe: Entwickle einen konkreten, starken Hook für ein Kurzvideo"
            " (TikTok, Reels). Der Hook muss in den ersten 1-2 Sekunden wirken,"
            " einen der Eröffnungstypen verwenden und den Ton treffen.\n\n"
            "Antwort exakt in diesem Format, keine weiteren Erklärungen:\n"
            "Hook-Typ: <einer der Eröffnungstypen>\n"
            "Eröffnung: <konkrete erste Zeile oder erstes Bild in 1 Satz>\n"
            "Versprechen: <was der Zuschauer gleich bekommt in 1 Satz>\n"
            "Format: <Videoformat>\n"
            "Stärke-Check: <kurze Einschätzung ob der Hook stark genug ist>"
        )

        user_prompt = command_body.strip() if command_body.strip() else "Neuer Hook"

        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.7,
        )

        items = self._parse_hook_response(response.output_text)
        airtable_record_id = self._try_create_hook_airtable_record(
            project_key=project_key,
            command_body=command_body,
            parsed_items=items,
        )

        return ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="hook",
            command_body=command_body,
            title="Content hook",
            summary="Hook generiert.",
            items=items,
            openai_used=True,
            airtable_record_id=airtable_record_id,
            platform="",
        )

    def _parse_hook_response(self, output_text: str) -> tuple[str, ...]:
        """Parse structured key: value lines from OpenAI hook response into items."""
        keys = ("Hook-Typ", "Eröffnung", "Versprechen", "Format", "Stärke-Check")
        result: list[str] = []

        for line in output_text.splitlines():
            stripped = line.strip()
            for key in keys:
                if stripped.lower().startswith(f"{key.lower()}:"):
                    value = stripped[len(key) + 1:].strip()
                    if value:
                        result.append(f"{key}: {value}")
                    break

        if not result:
            result.append(trim(output_text.replace("\n", " "), max_chars=180))

        return tuple(result)

    def _try_create_hook_airtable_record(
        self,
        *,
        project_key: str,
        command_body: str,
        parsed_items: tuple[str, ...],
    ) -> str | None:
        if self.airtable_service is None:
            return None

        fields: dict[str, str] = {
            "stage": "raw_idea",
            "project_key": project_key,
            "source_input": command_body or "",
            "created_by": "operator_core",
        }

        for item in parsed_items:
            if item.startswith("Hook-Typ:"):
                fields["hook_type"] = item[len("Hook-Typ:"):].strip()
            elif item.startswith("Eröffnung:"):
                fields["opening"] = item[len("Eröffnung:"):].strip()
            elif item.startswith("Versprechen:"):
                fields["promise"] = item[len("Versprechen:"):].strip()
            elif item.startswith("Format:"):
                fields["format"] = item[len("Format:"):].strip()
            elif item.startswith("Stärke-Check:"):
                fields["strength_check"] = item[len("Stärke-Check:"):].strip()

        try:
            record = self.airtable_service.create_record(  # type: ignore[union-attr]
                _CONTENT_HOOKS_TABLE,
                fields,
                project_key=project_key,
            )
            _log.info(
                "content_ops hook: airtable record created | project=%s record_id=%s",
                project_key,
                record.record_id,
            )
            return record.record_id
        except Exception as exc:
            _log.warning(
                "content_ops hook: airtable record creation failed | project=%s error=%s",
                project_key,
                exc,
            )
            return None

    def _read_caption(self, project_key: str, command_body: str) -> ContentOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        content = content_doc.content

        doc_context = self._build_caption_doc_context(content=content)

        # OpenAI path
        if self.openai_service is not None and self._integration_active_for("caption"):
            ok_context = self._load_operational_knowledge(project_key)
            try:
                return self._generate_caption_with_openai(
                    project_key=project_key,
                    command_body=command_body,
                    doc_context=doc_context,
                    ok_context=ok_context,
                )
            except Exception as exc:
                _log.warning(
                    "content_ops caption: OpenAI call failed, falling back to docs path | error=%s",
                    exc,
                )

        # Docs-only path
        return self._build_result(
            project_key=project_key,
            action_type="caption",
            command_body=command_body,
            title="Content caption",
            summary="Caption-Regeln geladen.",
            items=(
                doc_context["caption_rules_summary"],
                doc_context["cta_summary"],
                f"Kontext: {self._display_body(command_body)}",
            ),
        )

    def _build_caption_doc_context(self, *, content: str) -> dict[str, str]:
        caption_text = extract_section(content, "Caption Rules") or ""
        # First group: structural rules (short, clear, not overloaded, etc.)
        # Second group: caption functions (reinforce, clarify, invite, etc.)
        all_caption_items = list_items(caption_text, max_items=15)
        caption_rules_summary = (
            "Caption-Regeln: " + " | ".join(all_caption_items[:3])
            if all_caption_items else "Caption-Regeln: keine Doc-Quelle"
        )
        # Items beyond the first 6 structural rules are the caption function types
        caption_functions = all_caption_items[6:] if len(all_caption_items) > 6 else []
        caption_functions_str = (
            " | ".join(caption_functions) if caption_functions
            else "Punkt verstärken | Kontext klären | leichte Interaktion einladen"
        )
        caption_rules_str = (
            " | ".join(all_caption_items[:6]) if all_caption_items
            else "kurz, klar, nicht überladen, nicht emotionaler als das Video"
        )

        cta_text = extract_section(content, "CTA Content Rule") or ""
        cta_items = list_items(cta_text, max_items=6)
        cta_str = (
            " | ".join(cta_items) if cta_items
            else "Wiedererkennung | Meinung | leichte Neugier"
        )
        cta_summary = f"CTA-Richtung: {cta_str}" if cta_str else "CTA-Richtung: nicht geladen"

        tone_text = extract_section(content, "Content Tone") or ""
        tone_items = list_items(tone_text, max_items=4)
        tone_str = (
            ", ".join(tone_items) if tone_items
            else "natürlich, direkt, ehrlich, nützlich"
        )

        return {
            "caption_rules_summary": caption_rules_summary,
            "cta_summary": cta_summary,
            "caption_rules_str": caption_rules_str,
            "caption_functions_str": caption_functions_str,
            "cta_str": cta_str,
            "tone_str": tone_str,
        }

    def _generate_caption_with_openai(
        self,
        *,
        project_key: str,
        command_body: str,
        doc_context: dict[str, str],
        ok_context: "OperationalKnowledgeContext | None" = None,
    ) -> ContentOpResult:
        from operator_core.integrations.operational_knowledge_service import IDEA_CATEGORIES

        ok_block = ""
        if ok_context is not None and not ok_context.is_empty():
            ok_block = ok_context.to_prompt_block(*IDEA_CATEGORIES)

        system_prompt = (
            "Du bist ein strukturierter Content-Assistent für das Projekt everydayengel."
            " Antworte ausschließlich auf Deutsch.\n\n"
            "Projekt-Kontext (bindend):\n"
            f"Ton: {doc_context['tone_str']}\n"
            f"Caption-Regeln: {doc_context['caption_rules_str']}\n"
            f"Caption-Funktion (eine davon): {doc_context['caption_functions_str']}\n"
            f"Erlaubte CTA-Richtungen: {doc_context['cta_str']}\n\n"
            f"{ok_block}\n\n" if ok_block else ""
        ) + (
            "Aufgabe: Schreibe eine konkrete Caption für ein Kurzvideo"
            " (TikTok, Reels). Die Caption muss kurz und klar sein,"
            " den Ton treffen, eine der erlaubten Caption-Funktionen erfüllen"
            " und einen weichen CTA enthalten.\n\n"
            "Antwort exakt in diesem Format, keine weiteren Erklärungen:\n"
            "Caption: <der Caption-Text>\n"
            "CTA-Richtung: <weicher CTA-Typ>\n"
            "Ton-Check: <kurze Einschätzung ob Ton passt>\n"
            "Länge-Check: <passt die Länge zur Plattform>"
        )

        user_prompt = command_body.strip() if command_body.strip() else "Neue Caption"

        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.6,
        )

        items = self._parse_caption_response(response.output_text)
        airtable_record_id = self._try_create_caption_airtable_record(
            project_key=project_key,
            command_body=command_body,
            parsed_items=items,
        )

        return ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="caption",
            command_body=command_body,
            title="Content caption",
            summary="Caption generiert.",
            items=items,
            openai_used=True,
            airtable_record_id=airtable_record_id,
            platform="",
        )

    # Fallback caption used when the model returns an unexpected response.
    # Must be post-ready and match everydayengel tone.
    _CAPTION_FALLBACK: tuple[str, ...] = (
        "Caption: Kleine Routinen können deinen Tag verändern. Probier es morgen aus ✨",
        "CTA-Richtung: Wiedererkennung",
    )

    def _parse_caption_response(self, output_text: str) -> tuple[str, ...]:
        """Parse structured key: value lines from OpenAI caption response into items."""
        keys = ("Caption", "CTA-Richtung", "Ton-Check", "Länge-Check")
        result: list[str] = []

        for line in output_text.splitlines():
            stripped = line.strip()
            for key in keys:
                if stripped.lower().startswith(f"{key.lower()}:"):
                    value = stripped[len(key) + 1:].strip()
                    if value:
                        result.append(f"{key}: {value}")
                    break

        # Guard: if no Caption field was found the model returned something unexpected
        # (e.g. a clarifying question). Return a usable generic caption — never leak raw output.
        if not any(item.startswith("Caption:") for item in result):
            return self._CAPTION_FALLBACK

        return tuple(result)

    def _try_create_caption_airtable_record(
        self,
        *,
        project_key: str,
        command_body: str,
        parsed_items: tuple[str, ...],
    ) -> str | None:
        if self.airtable_service is None:
            return None

        fields: dict[str, str] = {
            "stage": "drafted",
            "project_key": project_key,
            "source_input": command_body or "",
            "created_by": "operator_core",
        }

        for item in parsed_items:
            if item.startswith("Caption:"):
                fields["caption_text"] = item[len("Caption:"):].strip()
            elif item.startswith("CTA-Richtung:"):
                fields["cta_direction"] = item[len("CTA-Richtung:"):].strip()
            elif item.startswith("Ton-Check:"):
                fields["tone_check"] = item[len("Ton-Check:"):].strip()
            elif item.startswith("Länge-Check:"):
                fields["length_check"] = item[len("Länge-Check:"):].strip()

        try:
            record = self.airtable_service.create_record(  # type: ignore[union-attr]
                _CONTENT_CAPTIONS_TABLE,
                fields,
                project_key=project_key,
            )
            _log.info(
                "content_ops caption: airtable record created | project=%s record_id=%s",
                project_key,
                record.record_id,
            )
            return record.record_id
        except Exception as exc:
            _log.warning(
                "content_ops caption: airtable record creation failed | project=%s error=%s",
                project_key,
                exc,
            )
            return None

    def _read_draft(self, project_key: str, command_body: str, *, action_type: str = "draft") -> ContentOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        platform, normalized_body = self._resolve_platform(command_body)

        doc_context = self._build_draft_doc_context(
            content=content_doc.content,
            state=state_doc.content,
        )

        # OpenAI path
        if self.openai_service is not None and self._integration_active_for(action_type):
            ok_context = self._load_operational_knowledge(project_key)
            platform_context = self._load_platform_signals(project_key=project_key).get(platform)
            try:
                return self._generate_draft_with_openai(
                    project_key=project_key,
                    command_body=normalized_body,
                    doc_context=doc_context,
                    ok_context=ok_context,
                    platform=platform,
                    platform_context=platform_context,
                    action_type=action_type,
                )
            except Exception as exc:
                _log.warning(
                    "content_ops draft/vollauto: OpenAI call failed, falling back to docs path | error=%s",
                    exc,
                )

        # Docs-only path
        return self._build_result(
            project_key=project_key,
            action_type=action_type,
            command_body=normalized_body,
            title="Content draft",
            summary="Draft-Kontext geladen.",
            items=(
                doc_context["readiness_summary"],
                doc_context["direction_summary"],
                f"Kontext: {self._display_body(normalized_body)}",
            ),
            platform=platform,
        )

    def _build_draft_doc_context(
        self,
        *,
        content: str,
        state: str,
    ) -> dict[str, str]:
        readiness_text = extract_section(content, "Production Readiness Rule") or ""
        readiness_items = list_items(readiness_text, max_items=6)
        readiness_str = " | ".join(readiness_items) if readiness_items else "keine"
        readiness_summary = f"Produktionsreife: {readiness_str}"

        direction_text = extract_section(state, "Active Content Direction") or ""
        direction_items = list_items(direction_text, max_items=3)
        direction_str = ", ".join(direction_items) if direction_items else "keine"
        direction_summary = f"Richtung: {direction_str}"

        tone_text = extract_section(content, "Content Tone") or ""
        tone_items = list_items(tone_text, max_items=4)
        tone_str = ", ".join(tone_items) if tone_items else "natürlich, direkt, nützlich, ehrlich"

        hook_text = extract_section(content, "Hook Rules") or ""
        hook_items = list_items(hook_text, max_items=3)
        hook_str = " | ".join(hook_items) if hook_items else "hook in 1-2 Sekunden, ein Kernversprechen"

        cta_text = extract_section(content, "CTA Content Rule") or ""
        cta_str = trim(first_sentences(cta_text, 1)) if cta_text else "soft CTA bevorzugt"

        return {
            "readiness_str": readiness_str,
            "readiness_summary": readiness_summary,
            "direction_str": direction_str,
            "direction_summary": direction_summary,
            "tone_str": tone_str,
            "hook_str": hook_str,
            "cta_str": cta_str,
        }

    def _generate_draft_with_openai(
        self,
        *,
        project_key: str,
        command_body: str,
        doc_context: dict[str, str],
        ok_context: "OperationalKnowledgeContext | None" = None,
        platform: str = "tiktok",
        platform_context: "PlatformContext | None" = None,
        action_type: str = "draft",
    ) -> ContentOpResult:
        from operator_core.integrations.operational_knowledge_service import IDEA_CATEGORIES

        ok_block = ""
        if ok_context is not None and not ok_context.is_empty():
            ok_block = ok_context.to_prompt_block(*IDEA_CATEGORIES)

        platform_label = _PLATFORM_LABELS.get(platform, "TikTok")
        platform_block = self._build_platform_signal_block(platform_context)
        system_prompt = (
            "Du bist ein strukturierter Content-Assistent für das Projekt everydayengel."
            " Antworte ausschließlich auf Deutsch.\n\n"
            "Projekt-Kontext (bindend):\n"
            f"Plattform: {platform_label}\n"
            f"Content-Richtung: {doc_context['direction_str']}\n"
            f"Ton: {doc_context['tone_str']}\n"
            f"Hook-Regeln: {doc_context['hook_str']}\n"
            f"CTA-Richtung: {doc_context['cta_str']}\n"
            f"Produktionsreife-Checklist: {doc_context['readiness_str']}\n\n"
            + (f"{ok_block}\n\n" if ok_block else "")
            + (f"{platform_block}\n\n" if platform_block else "")
            + "Aufgabe: Erstelle einen konkreten, produktionsreifen Vorschlag basierend auf dem folgenden Kontext."
            " Der Entwurf muss den Ton treffen, die Hook-Regeln einhalten"
            " und alle Produktionsreife-Kriterien erfüllen.\n\n"
            "Antwort exakt in diesem Format, keine weiteren Erklärungen:\n"
            "Serie/Thema: <kurzes Thema>\n"
            "Title: <Kernaussage in 1 Satz>\n"
            "Hook: <Einstiegszeile oder -bild in 1 Satz>\n"
            "CTA: <weicher oder empfehlender CTA>\n"
            "Caption: <kurze Caption>\n"
            "Format: <Videoformat>\n"
            "Bereit: <kurze Einschätzung der Produktionsreife>"
        )

        user_prompt = command_body.strip() if command_body.strip() else "Neuer Entwurf"

        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.5,
        )

        items = self._parse_draft_response(response.output_text)
        airtable_record_id = self._try_create_draft_airtable_record(
            project_key=project_key,
            command_body=command_body,
            parsed_items=items,
        )

        return ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type=action_type,
            command_body=command_body,
            title="Content draft",
            summary="Voll Auto generiert." if action_type == "vollauto" else "Entwurf generiert.",
            items=items,
            openai_used=True,
            airtable_record_id=airtable_record_id,
            platform=platform,
        )

    def _parse_draft_response(self, output_text: str) -> tuple[str, ...]:
        """Parse structured key: value lines from OpenAI draft response into items."""
        keys = (
            "Serie/Thema",
            "Title",
            "Hook",
            "CTA",
            "Caption",
            "Format",
            "Bereit",
            "Hauptpunkt",
            "Body",
            "CTA-Richtung",
            "Bereit-Check",
        )
        result: list[str] = []

        for line in output_text.splitlines():
            stripped = line.strip()
            for key in keys:
                if stripped.lower().startswith(f"{key.lower()}:"):
                    value = stripped[len(key) + 1:].strip()
                    if value:
                        result.append(f"{key}: {value}")
                    break

        if not result:
            result.append(trim(output_text.replace("\n", " "), max_chars=180))

        return tuple(result)

    def _try_create_draft_airtable_record(
        self,
        *,
        project_key: str,
        command_body: str,
        parsed_items: tuple[str, ...],
    ) -> str | None:
        if self.airtable_service is None:
            return None

        fields: dict[str, str] = {
            "stage": "drafted",
            "project_key": project_key,
            "source_input": command_body or "",
            "created_by": "operator_core",
        }

        for item in parsed_items:
            if item.startswith("Serie/Thema:"):
                fields["serie_thema"] = item[len("Serie/Thema:"):].strip()
            elif item.startswith("Hauptpunkt:"):
                fields["main_point"] = item[len("Hauptpunkt:"):].strip()
            elif item.startswith("Title:"):
                fields["main_point"] = item[len("Title:"):].strip()
            elif item.startswith("Hook:"):
                fields["hook"] = item[len("Hook:"):].strip()
            elif item.startswith("CTA-Richtung:"):
                fields["cta_direction"] = item[len("CTA-Richtung:"):].strip()
            elif item.startswith("CTA:"):
                fields["cta_direction"] = item[len("CTA:"):].strip()
            elif item.startswith("Body:"):
                fields["body"] = item[len("Body:"):].strip()
            elif item.startswith("Caption:"):
                fields["body"] = item[len("Caption:"):].strip()
            elif item.startswith("Format:"):
                fields["format"] = item[len("Format:"):].strip()
            elif item.startswith("Bereit-Check:"):
                fields["readiness_check"] = item[len("Bereit-Check:"):].strip()
            elif item.startswith("Bereit:"):
                fields["readiness_check"] = item[len("Bereit:"):].strip()

        try:
            record = self.airtable_service.create_record(  # type: ignore[union-attr]
                _CONTENT_DRAFTS_TABLE,
                fields,
                project_key=project_key,
            )
            _log.info(
                "content_ops draft: airtable record created | project=%s record_id=%s",
                project_key,
                record.record_id,
            )
            return record.record_id
        except Exception as exc:
            _log.warning(
                "content_ops draft: airtable record creation failed | project=%s error=%s",
                project_key,
                exc,
            )
            return None

    def _read_variant(self, project_key: str, command_body: str) -> ContentOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        content = content_doc.content

        reuse_text = extract_section(content, "Reusability Rule") or ""
        reuse_summary = "Wiederverwendbarkeit: " + trim(first_sentences(reuse_text, 1)) if reuse_text else "Wiederverwendbarkeit: keine Doc-Quelle"

        formats_text = extract_section(content, "Primary Content Formats") or ""
        format_items = list_items(formats_text, max_items=2)
        formats_summary = "Formate: " + " | ".join(format_items) if format_items else "Formate: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="variant",
            command_body=command_body,
            title="Content variant",
            summary="Varianten-Kontext geladen.",
            items=(
                reuse_summary,
                formats_summary,
                f"Kontext: {self._display_body(command_body)}",
            ),
        )

    def _read_single_field(self, project_key: str, action_type: str, command_body: str) -> ContentOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        platform, normalized_body = self._resolve_platform(command_body)
        if self.openai_service is None:
            key = {"serie": "Serie/Thema", "title": "Title", "cta": "CTA"}[action_type]
            return self._build_result(
                project_key=project_key,
                action_type=action_type,
                command_body=normalized_body,
                title=key,
                summary="Kontext geladen.",
                items=(f"{key}: {self._display_body(normalized_body)}",),
                platform=platform,
            )

        ok_context = self._load_operational_knowledge(project_key)
        platform_context = self._load_platform_signals(project_key=project_key).get(platform)
        key = {"serie": "Serie/Thema", "title": "Title", "cta": "CTA"}[action_type]
        ok_block = ok_context.to_prompt_block("priorities", "platform", "posting") if not ok_context.is_empty() else ""
        rules_block = self._build_content_rules_block(content_doc.content, state_doc.content)
        platform_block = self._build_platform_signal_block(platform_context)
        platform_label = _PLATFORM_LABELS.get(platform, "TikTok")
        response = self.openai_service.complete_messages(  # type: ignore[union-attr]
            system_prompt=(
                f"Du bist ein Content-Assistent für everydayengel auf {platform_label}.\n"
                + (f"{ok_block}\n\n" if ok_block else "")
                + (f"{platform_block}\n\n" if platform_block else "")
                + (f"{rules_block}\n\n" if rules_block else "")
                + f"Aufgabe: Liefere genau einen plattformpassenden Vorschlag für das Feld {key}.\n"
                + "Antworte ausschließlich auf Deutsch, exakt in diesem Format:\n"
                + f"{key}: <Wert>"
            ),
            user_prompt=normalized_body or f"Neuer {key}-Vorschlag",
            temperature=0.7,
        )
        value = self._parse_single_key_response(response.output_text, key)
        return self._build_result(
            project_key=project_key,
            action_type=action_type,
            command_body=normalized_body,
            title=key,
            summary=f"{key} generiert.",
            items=(f"{key}: {value or self._display_body(normalized_body)}",),
            platform=platform,
            openai_used=True,
        )

    # ------------------------------------------------------------------
    # Stub fallback (no docs_loader)
    # ------------------------------------------------------------------

    def _handle_stub(
        self,
        *,
        project_key: str,
        action_type: str,
        command_body: str,
    ) -> ContentOpResult:
        if action_type == "idea":
            return self._build_result(
                project_key=project_key,
                action_type="idea",
                command_body=command_body,
                title="Content idea",
                summary="Idea-Stub vorbereitet.",
                items=(
                    f"Idee: {self._display_body(command_body)}",
                    "Alternative A: [Stub]",
                    "Alternative B: [Stub]",
                ),
            )

        if action_type == "serie":
            return self._build_result(
                project_key=project_key,
                action_type="serie",
                command_body=command_body,
                title="Serie",
                summary="Serie-Stub vorbereitet.",
                items=("Serie/Thema: Alltag",),
                platform="tiktok",
            )

        if action_type == "title":
            return self._build_result(
                project_key=project_key,
                action_type="title",
                command_body=command_body,
                title="Title",
                summary="Title-Stub vorbereitet.",
                items=("Title: Kleine Routinen entlasten den Morgen spürbar.",),
                platform="tiktok",
            )

        if action_type == "hook":
            return self._build_result(
                project_key=project_key,
                action_type="hook",
                command_body=command_body,
                title="Content hook",
                summary="Hook-Stub vorbereitet.",
                items=(
                    "Ziel: Hook-Ansatz formulieren.",
                    f"Kontext: {self._display_body(command_body)}",
                    "Nächster Schritt: Hook gegen Format testen.",
                ),
            )

        if action_type == "cta":
            return self._build_result(
                project_key=project_key,
                action_type="cta",
                command_body=command_body,
                title="CTA",
                summary="CTA-Stub vorbereitet.",
                items=("CTA: Welche kleine Routine hilft dir morgens wirklich?",),
                platform="tiktok",
            )

        if action_type == "caption":
            return self._build_result(
                project_key=project_key,
                action_type="caption",
                command_body=command_body,
                title="Content caption",
                summary="Caption-Stub vorbereitet.",
                items=(
                    "Ziel: Caption-Grundgerüst anlegen.",
                    f"Kontext: {self._display_body(command_body)}",
                    "Nächster Schritt: CTA und Tonalität festlegen.",
                ),
            )

        if action_type in {"draft", "vollauto"}:
            return self._build_result(
                project_key=project_key,
                action_type=action_type,
                command_body=command_body,
                title="Content draft",
                summary="Voll-Auto-Stub vorbereitet.",
                items=(
                    "Serie/Thema: Alltag",
                    "Title: Kleine Routinen entlasten den Morgen spürbar.",
                    "Hook: Kennst du diesen kleinen Trick für ruhigere Morgen?",
                    "CTA: Welche Mini-Routine hilft dir am meisten?",
                    "Caption: Kleine Schritte machen den Morgen oft leichter. Was hilft dir gerade am meisten?",
                    "Format: Talking Head",
                    "Bereit: Ja, einsatzfähig",
                ),
                platform="tiktok",
            )

        return self._build_result(
            project_key=project_key,
            action_type="variant",
            command_body=command_body,
            title="Content variant",
            summary="Variant-Stub vorbereitet.",
            items=(
                "Ziel: alternative Variante vorbereiten.",
                f"Kontext: {self._display_body(command_body)}",
                "Nächster Schritt: beste Variante auswählen.",
            ),
        )

    # ------------------------------------------------------------------
    # mark_stale action
    # ------------------------------------------------------------------

    def _mark_stale_draft(self, *, project_key: str, record_id: str) -> ContentOpResult:
        """Mark a Content Draft as stale in Airtable. No OpenAI, no docs needed."""
        if not record_id:
            return ContentOpResult(
                lane_name=self.lane_name,
                project_key=project_key,
                action_type="mark_stale",
                command_body=record_id,
                title="Fehler",
                summary="Kein Record-ID angegeben.",
                items=("Kein Entwurf gefunden — bitte erneut versuchen.",),
            )

        if self.airtable_service is not None and self._integration_active_for("mark_stale"):
            try:
                self.airtable_service.update_record(
                    _CONTENT_DRAFTS_TABLE,
                    record_id,
                    {"stage": "stale"},
                )
                _log.info("mark_stale: draft marked stale | record_id=%s", record_id)
            except Exception as exc:
                _log.error("mark_stale: airtable update failed | record_id=%s error=%s", record_id, exc)
                return ContentOpResult(
                    lane_name=self.lane_name,
                    project_key=project_key,
                    action_type="mark_stale",
                    command_body=record_id,
                    title="Fehler",
                    summary=f"Airtable-Fehler: {exc}",
                    items=(f"Entwurf konnte nicht aktualisiert werden: {exc}",),
                    airtable_record_id=record_id,
                    platform="",
                )

        return ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type="mark_stale",
            command_body=record_id,
            title="Entwurf als veraltet markiert",
            summary=f"Entwurf {record_id} wurde als veraltet markiert.",
            items=(f"✓ Entwurf ist jetzt als veraltet markiert.",),
            airtable_record_id=record_id,
            platform="",
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _build_result(
        self,
        *,
        project_key: str,
        action_type: str,
        command_body: str,
        title: str,
        summary: str,
        items: tuple[str, ...],
        platform: str = "",
        openai_used: bool = False,
        model_name: str | None = None,
    ) -> ContentOpResult:
        return ContentOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type=action_type,
            command_body=command_body,
            title=title,
            summary=summary,
            items=items,
            platform=platform,
            openai_used=openai_used,
            model_name=model_name,
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.strip().split())

    @staticmethod
    def _display_body(value: str) -> str:
        if value:
            return value
        return "kein Zusatzkontext"

    def _resolve_platform(self, command_body: str) -> tuple[str, str]:
        normalized = self._normalize(command_body).lower()
        platform = ""
        stripped = command_body.strip()
        for key, aliases in _PLATFORM_ALIASES:
            if any(alias in normalized for alias in aliases):
                platform = key
                lowered = stripped.lower()
                for alias in aliases:
                    lowered = lowered.replace(alias, " ")
                stripped = " ".join(lowered.split())
                break
        return platform, self._normalize(stripped)

    @staticmethod
    def _parse_single_key_response(output_text: str, key: str) -> str:
        for line in output_text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith(f"{key.lower()}:"):
                return stripped[len(key) + 1:].strip()
        return trim(output_text.replace("\n", " "), max_chars=220)

    def _build_platform_signal_block(self, platform_context: "PlatformContext | None") -> str:
        if platform_context is None:
            return ""
        lines = [f"Plattform-Signale: {platform_context.post_count} Posts in der Analytics-Tabelle."]
        if platform_context.dominant_cta:
            lines.append(f"Häufiger CTA: {platform_context.dominant_cta}")
        if platform_context.dominant_format:
            lines.append(f"Häufiges Format: {platform_context.dominant_format}")
        if platform_context.hook_examples:
            lines.append(f"Hook-Beispiele: {', '.join(platform_context.hook_examples)}")
        if platform_context.numeric_summary_lines:
            lines.append("Numerische Performance-Signale:")
            lines.extend(platform_context.numeric_summary_lines[:4])
        return "\n".join(lines)

    def _build_content_rules_block(self, content: str, state: str) -> str:
        direction_text = extract_section(state, "Active Content Direction") or ""
        direction_items = list_items(direction_text, max_items=3)
        tone_text = extract_section(content, "Content Tone") or ""
        tone_items = list_items(tone_text, max_items=4)
        cta_text = extract_section(content, "CTA Content Rule") or ""
        cta_items = list_items(cta_text, max_items=4)
        formats_text = extract_section(content, "Primary Content Formats") or ""
        format_items = list_items(formats_text, max_items=4)
        lines: list[str] = []
        if direction_items:
            lines.append(f"Richtung: {' | '.join(direction_items)}")
        if tone_items:
            lines.append(f"Ton: {' | '.join(tone_items)}")
        if cta_items:
            lines.append(f"CTA-Regeln: {' | '.join(cta_items)}")
        if format_items:
            lines.append(f"Formate: {' | '.join(format_items)}")
        return "\n".join(lines)

    @staticmethod
    def _field_display_name(field_name: str) -> str:
        return {
            "serie_thema": "Serie/Thema",
            "title_raw": "Title",
            "hook": "Hook",
            "cta": "CTA",
            "caption": "Caption",
            "format_typ": "Format",
            "bereit": "Bereit",
        }.get(field_name, field_name)

    def _build_followup_system_prompt(
        self,
        *,
        platform_label: str,
        ok_block: str,
        platform_block: str,
        rules_block: str,
        freshness_token: str,
        current_block: str,
        instruction: str,
        requires_change: bool,
        forbidden_block: str,
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
    ) -> str:
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)
        change_block = (
            "Die Nutzerin verlangt eine Änderung. Mindestens ein relevantes Feld muss sichtbar neu formuliert oder neu ausgerichtet werden.\n"
            "Halte den Themenkern passend, aber vermeide fast gleiche Wiederholung.\n"
            if requires_change
            else "Wenn die Nutzerin eine Begründung oder Analytics-Frage stellt, beantworte sie kurz und lasse die Inhalte passend stabil.\n"
        )
        return (
            f"Du bist ein Content-Assistent für everydayengel auf {platform_label}.\n"
            "Du arbeitest auf einem bestehenden Vorschlag weiter.\n"
            + (f"{ok_block}\n\n" if ok_block else "")
            + (f"{platform_block}\n\n" if platform_block else "")
            + (f"{rules_block}\n\n" if rules_block else "")
            + f"{weekly_block}"
            + f"Aktueller Vorschlag:\n{current_block or 'kein Vorschlag'}\n\n"
            + f"Generierungslauf: {freshness_token}\n"
            + _build_followup_variation_block(instruction=instruction, freshness_token=freshness_token)
            + forbidden_block
            + change_block
            + "Antworte ausschließlich auf Deutsch, exakt mit beliebig vielen dieser Zeilen:\n"
            + "Serie/Thema: ...\nTitle: ...\nHook: ...\nCTA: ...\nCaption: ...\nFormat: ...\nBereit: ...\nAntwort: ...\n"
        )

    def _build_foundation_followup_system_prompt(
        self,
        *,
        platform: str,
        rules_block: str,
        writer_brief: WriterBrief,
        selected_snapshots: tuple[AnalysisSnapshot, ...],
        current_block: str,
        instruction: str,
        requires_change: bool,
        forbidden_block: str,
        weekly_analysis: WeeklyAnalysisArtifact | None = None,
    ) -> str:
        platform_label = _PLATFORM_LABELS.get(platform or "tiktok", "TikTok")
        snapshot_blocks = "\n\n".join(
            self._format_snapshot_for_prompt(snapshot)
            for snapshot in selected_snapshots
        )
        constraints_block = "\n".join(f"- {constraint}" for constraint in writer_brief.constraints)
        weekly_block = self._build_weekly_analysis_prompt_block(weekly_analysis)

        change_block = (
            "Die Nutzerin verlangt eine Änderung. Mindestens ein relevantes Feld muss sichtbar neu formuliert oder neu ausgerichtet werden.\n"
            "Halte den Themenkern passend, aber vermeide fast gleiche Wiederholung.\n"
            if requires_change
            else "Wenn die Nutzerin eine Begründung oder Analytics-Frage stellt, beantworte sie kurz und lasse die Inhalte passend stabil.\n"
        )
        freshness_token = str(time.time_ns())
        return (
            "Du bist ein strukturierter Content-Assistent fuer das Projekt everydayengel."
            " Antworte ausschliesslich auf Deutsch.\n\n"
            "Writer-Brief (bindend):\n"
            f"Ziel: {writer_brief.objective}\n"
            f"Zielgruppe: {writer_brief.audience}\n"
            f"Plattform: {platform_label}\n"
            f"Constraints:\n{constraints_block}\n\n"
            + (f"Projekt-Regeln (bindend):\n{rules_block}\n\n" if rules_block else "")
            + "Analysis Snapshots (bindend):\n"
            + f"{snapshot_blocks}\n\n"
            + f"{weekly_block}"
            + f"Aktueller Vorschlag:\n{current_block or 'kein Vorschlag'}\n\n"
            + f"Generierungslauf: {freshness_token}\n"
            + _build_followup_variation_block(instruction=instruction, freshness_token=freshness_token)
            + forbidden_block
            + change_block
            + "Nutze die Analyse-Snapshots als primaere Grounding-Schicht.\n"
            + "Antworte ausschliesslich mit beliebig vielen dieser Zeilen:\n"
            + "Serie/Thema: ...\nTitle: ...\nHook: ...\nCTA: ...\nCaption: ...\nFormat: ...\nBereit: ...\nAntwort: ...\n"
        )

    def _regenerate_with_distance(
        self,
        *,
        project_key: str,
        proposal: ContentProposal,
    ) -> ContentOpResult:
        directions = self._regeneration_directions(proposal.action_type)
        if not directions:
            directions = ("Wähle eine klar andere, aber passende Variante.",)

        if self.docs_loader is None or self.openai_service is None:
            return self.handle(
                project_key=project_key,
                action_type=proposal.action_type,
                command_body=proposal.source_command_body,
            )

        best_result: ContentOpResult | None = None
        best_distance = -1.0
        for direction in directions[:4]:
            command_body = self._build_regeneration_command_body(proposal=proposal, direction=direction)
            result = self.handle(
                project_key=project_key,
                action_type=proposal.action_type,
                command_body=command_body,
            )
            candidate_fields = self._extract_structured_fields(result.items)
            distance = self._regeneration_score(
                proposal=proposal,
                candidate_fields=candidate_fields,
            )
            if distance > best_distance:
                best_result = result
                best_distance = distance
            if distance >= self._regeneration_distance_threshold(proposal.action_type):
                return result
        return best_result or self.handle(
            project_key=project_key,
            action_type=proposal.action_type,
            command_body=proposal.source_command_body,
        )

    @staticmethod
    def _format_proposal_fields(fields: dict[str, str]) -> str:
        return "\n".join(
            f"{ContentOpsService._field_display_name(key)}: {value}"
            for key, value in fields.items()
            if value
        )

    @staticmethod
    def _followup_requires_change(instruction: str) -> bool:
        normalized = instruction.strip().lower()
        change_markers = (
            "mach ",
            "optimiere",
            "umschreib",
            "schreib es um",
            "gib mir eine andere",
            "andere ",
            "anders",
            "ersetz",
            "ersetze",
            "komplett",
            "direkter",
            "ruhiger",
            "für youtube",
            "für instagram",
            "für tiktok",
            "für facebook",
        )
        explain_markers = ("warum", "hast du", "beachtet", "welche analytics", "wieso")
        if any(marker in normalized for marker in explain_markers):
            return False
        return any(marker in normalized for marker in change_markers)

    def _button_followup_instruction(self, *, proposal: ContentProposal, mode: str) -> str:
        if mode == "rewrite":
            if proposal.action_type == "vollauto":
                return (
                    "Formuliere den aktuellen Voll-Auto-Vorschlag sichtbar neu. "
                    "Behalte Kernidee, Plattform und Richtung bei, aber ändere Wortlaut, Satzlogik und Ton spürbar."
                )
            label = self._field_display_name(
                {
                    "serie": "serie_thema",
                    "title": "title_raw",
                    "hook": "hook",
                    "cta": "cta",
                    "caption": "caption",
                    "idea": "title_raw",
                }.get(proposal.action_type, proposal.action_type)
            )
            return (
                f"Formuliere nur das Feld {label} sichtbar neu. "
                f"Gleiche Idee, gleiche Richtung, aber klar anderer Wortlaut. Antworte nur mit {label}."
            )
        if proposal.action_type == "vollauto":
            return (
                "Erzeuge eine deutlich frischere Voll-Auto-Variante im selben Kontext. "
                "Nutze einen anderen Angle, eine andere CTA-Richtung und eine andere Satzlogik als bisher."
            )
        label = self._field_display_name(
            {
                "serie": "serie_thema",
                "title": "title_raw",
                "hook": "hook",
                "cta": "cta",
                "caption": "caption",
                "idea": "title_raw",
            }.get(proposal.action_type, proposal.action_type)
        )
        return (
            f"Erzeuge nur für {label} eine deutlich frischere Variante im selben Kontext. "
            f"Nutze einen anderen Angle oder eine andere CTA-/Satzlogik als bisher. Antworte nur mit {label}."
        )

    def _extract_structured_fields(self, items: tuple[str, ...]) -> dict[str, str]:
        reverse = {
            self._field_display_name("serie_thema"): "serie_thema",
            self._field_display_name("title_raw"): "title_raw",
            self._field_display_name("hook"): "hook",
            self._field_display_name("cta"): "cta",
            self._field_display_name("caption"): "caption",
            self._field_display_name("format_typ"): "format_typ",
            self._field_display_name("bereit"): "bereit",
        }
        fields: dict[str, str] = {}
        for item in items:
            key, _, value = item.partition(":")
            field_name = reverse.get(key.strip())
            if field_name and value.strip():
                fields[field_name] = value.strip()
        return fields

    def _proposal_source_seed(
        self,
        *,
        proposal: ContentProposal,
        platform_override: str,
    ) -> str:
        seed_body = proposal.source_command_body.strip()
        if not platform_override:
            return seed_body
        _, seed_without_platform = self._resolve_platform(seed_body)
        if seed_without_platform:
            return f"{platform_override} {seed_without_platform}".strip()
        return platform_override

    @staticmethod
    def _proposal_changed_meaningfully(
        *,
        proposal: ContentProposal,
        parsed_fields: dict[str, str],
    ) -> bool:
        for field_name, new_value in parsed_fields.items():
            if _texts_are_meaningfully_different(new_value, proposal.fields.get(field_name, "")):
                return True
        return False

    def _proposal_distance(self, *, proposal: ContentProposal, candidate_fields: dict[str, str]) -> float:
        relevant_fields = tuple(candidate_fields.keys()) or tuple(proposal.fields.keys())
        distances: list[float] = []
        for field_name in relevant_fields:
            new_value = candidate_fields.get(field_name, "")
            old_value = proposal.fields.get(field_name, "")
            distances.append(_text_distance_score(new_value, old_value))
        return sum(distances) / max(len(distances), 1)

    def _regeneration_score(self, *, proposal: ContentProposal, candidate_fields: dict[str, str]) -> float:
        base_distance = self._proposal_distance(
            proposal=proposal,
            candidate_fields=candidate_fields,
        )
        anchor_families = self._proposal_anchor_families(proposal)
        if not anchor_families:
            return base_distance
        candidate_text = " ".join(value for value in candidate_fields.values() if value)
        if not candidate_text.strip():
            return base_distance
        repeated = self._repeated_anchor_family_count(
            candidate_text=candidate_text,
            anchor_families=anchor_families,
        )
        if repeated <= 0:
            return min(1.0, base_distance + 0.08)
        penalty = min(0.5, repeated * 0.18)
        return max(0.0, base_distance - penalty)

    @staticmethod
    def _regeneration_distance_threshold(action_type: str) -> float:
        if action_type in {"vollauto", "draft"}:
            return 0.55
        return 0.5

    @staticmethod
    def _regeneration_directions(action_type: str) -> tuple[str, ...]:
        if action_type in {"vollauto", "draft"}:
            return (
                "Wechsle zu einer beobachtenden Alltags-Perspektive mit weicher CTA-Richtung.",
                "Wechsle zu einer direkt-nützlichen Perspektive mit praktischem Fokus.",
                "Wechsle zu einer fragenden Perspektive mit dialogischer CTA-Richtung.",
                "Wechsle zu einer mini-narrativen Perspektive mit konkreter Szene.",
            )
        if action_type == "caption":
            return (
                "Nutze eine beobachtende Caption mit kleinem Alltagsmoment statt Routinen-Fokus.",
                "Nutze eine dialogische Caption mit Community-Frage statt gleichem Start-/Routine-Kern.",
                "Nutze eine praktischere Caption mit Mini-Erleichterung oder Chaos-reduzierendem Fokus.",
                "Nutze eine persönlichere Caption mit Gewohnheit, Tagesbeginn oder Mini-Trick als neuem Framing.",
            )
        if action_type == "cta":
            return (
                "Wähle eine weichere Reflexions-CTA.",
                "Wähle eine dialogische Community-CTA.",
                "Wähle eine praktischere Mini-Handlungs-CTA.",
                "Wähle eine neugierige Anschluss-CTA.",
            )
        if action_type == "hook":
            return (
                "Wähle einen fragenden Hook.",
                "Wähle einen beobachtenden Hook.",
                "Wähle einen direkt-nützlichen Hook.",
                "Wähle einen mini-narrativen Hook.",
            )
        if action_type in {"serie", "title", "idea"}:
            return (
                "Wähle einen stärker alltagsnahen Angle.",
                "Wähle einen stärker nützlichen Angle.",
                "Wähle einen stärker beobachtenden Angle.",
                "Wähle einen stärker dialogischen Angle.",
            )
        return tuple()

    def _build_regeneration_command_body(self, *, proposal: ContentProposal, direction: str) -> str:
        previous = self._format_proposal_fields(proposal.fields)
        base = proposal.source_command_body.strip()
        avoid_terms = self._proposal_anchor_terms(proposal)
        avoid_block = ""
        if avoid_terms:
            avoid_block = (
                "Vermeide nach Möglichkeit diese bisherigen Framing-Kerne oder Wortfamilien: "
                + ", ".join(avoid_terms)
                + "\n"
            )
        return (
            f"{base}\n\n"
            + f"Neu-generieren-Ziel: {direction}\n"
            + "Halte Plattform, Regeln und Analytics-Kontext ein.\n"
            + "Nutze nicht denselben Wortlaut wie bisher.\n"
            + "Nicht nur paraphrasieren. Suche bewusst einen anderen Angle, Nutzenfokus oder CTA-Fokus.\n"
            + avoid_block
            + f"Bisheriger Vorschlag:\n{previous}"
        ).strip()

    def _proposal_anchor_terms(self, proposal: ContentProposal) -> tuple[str, ...]:
        candidates: list[str] = []
        for value in proposal.fields.values():
            for token in _extract_anchor_tokens(value):
                if token not in candidates:
                    candidates.append(token)
        return tuple(candidates[:6])

    def _proposal_anchor_families(self, proposal: ContentProposal) -> frozenset[str]:
        families: set[str] = set()
        for term in self._proposal_anchor_terms(proposal):
            family = _anchor_family(term)
            if family:
                families.add(family)
        return frozenset(families)

    @staticmethod
    def _repeated_anchor_family_count(*, candidate_text: str, anchor_families: frozenset[str]) -> int:
        if not anchor_families:
            return 0
        candidate_families = {
            family
            for token in _extract_anchor_tokens(candidate_text)
            if (family := _anchor_family(token))
        }
        return len(candidate_families & anchor_families)

    def _parse_followup_response(
        self,
        output_text: str,
        *,
        proposal: ContentProposal,
    ) -> tuple[str, ...]:
        known_keys = ("Serie/Thema", "Title", "Hook", "CTA", "Caption", "Format", "Bereit", "Antwort")
        parsed: dict[str, str] = {}
        for line in output_text.splitlines():
            stripped = line.strip()
            for key in known_keys:
                if stripped.lower().startswith(f"{key.lower()}:"):
                    value = stripped[len(key) + 1:].strip()
                    if value:
                        parsed[key] = value
                    break

        items: list[str] = []
        field_order = ("serie_thema", "title_raw", "hook", "cta", "caption", "format_typ", "bereit")
        reverse = {self._field_display_name(key): key for key in field_order}
        for field_name in field_order:
            display = self._field_display_name(field_name)
            value = parsed.get(display) or proposal.fields.get(field_name, "")
            if value:
                items.append(f"{display}: {value}")
        if parsed.get("Antwort"):
            items.append(f"Antwort: {parsed['Antwort']}")
        if not items:
            for field_name, value in proposal.fields.items():
                if value:
                    items.append(f"{self._field_display_name(field_name)}: {value}")
            items.append(f"Antwort: {trim(output_text.replace(chr(10), ' '), max_chars=220)}")
        return tuple(items)


def _build_followup_variation_block(*, instruction: str, freshness_token: str) -> str:
    directives = (
        "Bevorzuge diesmal eine beobachtende, alltagsnahe Variante.",
        "Bevorzuge diesmal eine dialogische, etwas direktere Variante.",
        "Bevorzuge diesmal eine ruhigere, weichere Variante.",
        "Bevorzuge diesmal eine konkretere, praktischere Variante.",
    )
    basis = f"{instruction}:{freshness_token}"
    return directives[abs(hash(basis)) % len(directives)] + "\n"


def _normalize_similarity_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


_ANCHOR_STOPWORDS = {
    "aber",
    "auch",
    "bald",
    "beim",
    "bereits",
    "besser",
    "deine",
    "deiner",
    "deinem",
    "deinen",
    "deines",
    "dein",
    "deins",
    "deutlich",
    "dieser",
    "diese",
    "dieses",
    "diesen",
    "durch",
    "eine",
    "einer",
    "einem",
    "einen",
    "eines",
    "einfach",
    "etwas",
    "etwas",
    "etwa",
    "euch",
    "fuer",
    "für",
    "ganz",
    "heute",
    "hier",
    "immer",
    "jeden",
    "jeder",
    "jedes",
    "jetzt",
    "kleine",
    "kleiner",
    "kleines",
    "kleinen",
    "kann",
    "kannst",
    "können",
    "macht",
    "mehr",
    "mein",
    "meine",
    "meiner",
    "meinem",
    "meinen",
    "meistens",
    "morgen",
    "morgens",
    "nicht",
    "noch",
    "perfekt",
    "ruhig",
    "ruhiger",
    "ruhige",
    "ruhigen",
    "schon",
    "sehr",
    "sein",
    "seine",
    "seiner",
    "seinem",
    "seinen",
    "sind",
    "start",
    "startet",
    "startest",
    "tag",
    "tage",
    "tages",
    "teil",
    "teile",
    "uns",
    "viele",
    "wieder",
    "wirklich",
}


def _extract_anchor_tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", _normalize_similarity_text(value)):
        if token in _ANCHOR_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    tokens.sort(key=len, reverse=True)
    return tuple(tokens)


def _anchor_family(token: str) -> str:
    normalized = _normalize_similarity_text(token)
    if len(normalized) < 5:
        return ""
    return normalized[:5]


def _texts_are_meaningfully_different(left: str, right: str) -> bool:
    left_norm = _normalize_similarity_text(left)
    right_norm = _normalize_similarity_text(right)
    if not left_norm and not right_norm:
        return False
    if left_norm == right_norm:
        return False
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if not left_tokens or not right_tokens:
        return True
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return overlap < 0.8


def _text_distance_score(left: str, right: str) -> float:
    left_norm = _normalize_similarity_text(left)
    right_norm = _normalize_similarity_text(right)
    if not left_norm and not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 0.0
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if not left_tokens or not right_tokens:
        return 1.0
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return 1.0 - overlap
