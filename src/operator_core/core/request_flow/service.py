from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from operator_core.core.backbone.execution_service import ExecutionService
from operator_core.core.backbone.models import RequestContext
from operator_core.core.backbone.statuses import JobStatus
from operator_core.core.content_ops.correction_capture import (
    CorrectionCaptureStore,
    CorrectionReasonTag,
    CorrectionStatus,
    IdeaCorrectionService,
    REASON_TAG_LABELS,
)
from operator_core.core.content_ops.platform_mode_store import PlatformModeStore
from operator_core.core.content_ops.proposal_store import ContentProposal, ContentProposalStore
from operator_core.core.menu_layouts import MENU_OVERLAY_REPLY_MARKUP, PERSISTENT_MENU_REPLY_MARKUP
from operator_core.core.request_flow.daily_plan_message_store import DailyPlanMessageStore
from operator_core.core.request_flow.models import (
    AdditionalFormatterMessage,
    FormatterPayload,
    RequestFlowResult,
)
from operator_core.integrations.daily_plan_service import TodayPlanSnapshot, normalize_bereit_value
from operator_core.integrations.daily_plan_upload_service import parse_posted_time_input
from operator_core.interfaces.telegram.models import TelegramEntryHandoff

if TYPE_CHECKING:
    from operator_core.integrations.daily_plan_generation_service import DailyPlanGenerationService
    from operator_core.integrations.daily_plan_service import DailyPlanService
    from operator_core.integrations.daily_plan_upload_service import DailyPlanUploadService
    from operator_core.proactive.checker import SuppressionStore
    from operator_core.proactive.pending_store import PendingProposal, ProactivePendingStore
    from operator_core.proactive.plan_reminder_store import PlanReminderStore
    from operator_core.proactive.posting_recommender import PostingRecommender

_log = logging.getLogger("operator_core.core.request_flow")

_PLAN_DEMO_LABELS: dict[str, str] = {
    "skip_today": "Heute auslassen",
    "auto_fill": "Automatisch ergänzen",
    "clear_selection": "Auswahl leeren",
    "upload_airtable": "Upload in Airtable",
    "posted_at_default": "Standardzeit übernehmen",
    "remind_15m": "In 15 Min. erinnern",
}

_PLAN_PLATFORM_DEFAULTS: dict[str, dict[str, str | int | None]] = {
    "tiktok": {"plan_type": "post", "candidate_count": 0},
    "instagram_reel": {"plan_type": "post", "candidate_count": 0},
    "facebook_reel": {"plan_type": "post", "candidate_count": 0},
    "youtube_short": {"plan_type": "post", "candidate_count": 0},
}

_PLAN_STATUS_LABELS: dict[str, str] = {
    "pending": "offen",
    "skip": "heute auslassen",
}

def _build_plan_demo_reply_markup(record_id: str) -> dict:
    """Build the per-platform daily plan markup, embedding record_id when available."""
    s = f":{record_id}" if record_id else ""
    return {
        "inline_keyboard": [
            [
                {"text": "⏭ Heute auslassen", "callback_data": f"plan_demo:skip_today{s}"},
                {"text": "🪄 Automatisch ergänzen", "callback_data": f"plan_demo:auto_fill{s}"},
            ],
            [
                {"text": "🧹 Auswahl leeren", "callback_data": f"plan_demo:clear_selection{s}"},
                {"text": "🔄 Ersetzen", "callback_data": f"plan_demo:replace_field_select{s}"},
            ],
            [
                {"text": "⬆️ Upload in Airtable", "callback_data": f"plan_demo:upload_airtable{s}"},
            ],
            [
                {"text": "⏰ In 15 Min. erinnern", "callback_data": f"plan_demo:remind_15m{s}"},
            ],
        ]
    }

_PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram_reel": "Instagram",
    "facebook_reel": "Facebook",
    "youtube_short": "YouTube",
}
_PLATFORM_KEYS_BY_LABEL: dict[str, str] = {
    label.lower(): key for key, label in _PLATFORM_LABELS.items()
}
_USE_RESPONSE_SHELL_REPLY_TO = object()

_REJECT_REASON_CALLBACK_CODES: dict[CorrectionReasonTag, str] = {
    CorrectionReasonTag.too_literal: "tl",
    CorrectionReasonTag.too_free: "tf",
    CorrectionReasonTag.moment_missed: "mm",
    CorrectionReasonTag.tone_off: "to",
    CorrectionReasonTag.not_julia: "nj",
    CorrectionReasonTag.too_broad: "tb",
    CorrectionReasonTag.too_loud: "tlo",
    CorrectionReasonTag.too_producty: "tp",
    CorrectionReasonTag.weak_hook: "wh",
    CorrectionReasonTag.weak_clarity: "wc",
    CorrectionReasonTag.good_but_wrong_platform: "gwp",
}
_REJECT_REASON_CALLBACK_TAGS: dict[str, CorrectionReasonTag] = {
    code: tag for tag, code in _REJECT_REASON_CALLBACK_CODES.items()
}

_DAILY_PLAN_FIELD_LABELS: dict[str, str] = {
    "serie_thema": "Serie/Thema",
    "title_raw": "Title",
    "hook": "Hook",
    "cta": "CTA",
    "caption": "Caption",
    "format_typ": "Format",
    "bereit": "Bereit",
}

_DAILY_PLAN_FIELD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("serie_thema", ("serie/thema", "serie thema", "serie", "thema")),
    ("title_raw", ("title", "titel")),
    ("hook", ("hook",)),
    ("cta", ("cta", "call to action")),
    ("caption", ("caption", "beschreibung")),
    ("format_typ", ("format",)),
    ("bereit", ("bereit", "ready")),
)

_SKIP_PLAN_TEXT = (
    "📋 Tagesplan\n\n"
    "Heute posten: nein\n"
    "Anzahl: 0\n\n"
    "Kein Inhalt bereit."
)

_REJECTED_SAME_CORE_FALLBACK_MARKER = "gerade in fast diesem Kern verworfen"

def _build_post_plan_text(candidate: object, candidate_count: int) -> str:
    platform = str(getattr(candidate, "platform", "") or "")
    platform_label = _PLATFORM_LABELS.get(platform, platform)
    posting_time = str(getattr(candidate, "posting_time", "") or "")
    days_ready = int(getattr(candidate, "days_ready", 0))
    days_since = int(getattr(candidate, "days_since_last_post", 0))

    lines = [
        "📋 Tagesplan",
        "",
        "Heute posten: ja",
        "Anzahl: 1",
        f"Plattform: {platform_label}",
        f"Uhrzeit: {posting_time}",
        "",
        "Warum:",
    ]
    ready_word = "Tag" if days_ready == 1 else "Tagen"
    lines.append(f"• Entwurf seit {days_ready} {ready_word} bereit")
    if days_since == -1:
        lines.append(f"• {platform_label} noch nie gepostet")
    else:
        since_word = "Tag" if days_since == 1 else "Tagen"
        lines.append(f"• {platform_label} zuletzt vor {days_since} {since_word} gepostet")
    if candidate_count > 1:
        lines.append(f"• Ausgewählt aus {candidate_count} passenden Entwürfen (ältester zuerst)")
    return "\n".join(lines)


def _build_draft_plan_text(draft_count: int) -> str:
    draft_word = "Entwurf" if draft_count == 1 else "Entwürfe"
    return (
        "📋 Tagesplan\n\n"
        "Heute posten: nein\n"
        "Anzahl: 0\n\n"
        "Stattdessen:\n"
        f"• {draft_count} {draft_word} im Backlog zum Fertigstellen"
    )


def _display_value(value: str) -> str:
    return value or "—"


def _build_platform_plan_text(snapshot: TodayPlanSnapshot) -> str:
    platform = snapshot.platform or ""
    platform_label = _PLATFORM_LABELS.get(platform, platform or "Plattform")
    status = _PLAN_STATUS_LABELS.get(snapshot.decision, snapshot.decision or "offen")
    lines = [
        f"📋 Tagesplan · {platform_label}",
        "",
        f"Status: {status}",
        f"Serie/Thema: {_display_value(snapshot.serie_thema)}",
        f"Title: {_display_value(snapshot.title_raw)}",
        f"Hook: {_display_value(snapshot.hook)}",
        f"CTA: {_display_value(snapshot.cta)}",
        f"Caption: {_display_value(snapshot.caption)}",
        f"Format: {_display_value(snapshot.format_typ)}",
        f"Bereit: {_display_value(normalize_bereit_value(snapshot.bereit))}",
    ]
    return "\n".join(lines)

_MENU_TEXT = "☰ Menü\n\nWähle eine Aktion."

_MENU_LABELS: dict[str, str] = {
    "plan": "Tagesplan",
    "idea": "Idee",
    "draft": "Voll Auto",
    "serie": "Serie/Thema",
    "title": "Title",
    "vollauto": "Voll Auto",
    "hook": "Hook erstellen",
    "cta": "CTA erstellen",
    "caption": "Caption erstellen",
    "modus": "🎯 Modus",
    "status": "📊 Status",
}

_MENU_COMMAND_ACTIONS = frozenset({"idea", "draft", "serie", "title", "vollauto", "hook", "cta", "caption", "status"})

_TEXT_ACTION_LABELS: dict[str, str] = {
    "idea": "Idee generieren",
    "draft": "Voll Auto generieren",
    "serie": "Serie/Thema generieren",
    "title": "Title generieren",
    "vollauto": "Voll Auto generieren",
    "hook": "Hook erstellen",
    "cta": "CTA erstellen",
    "caption": "Caption erstellen",
    "cancel": "Abbrechen",
}


@dataclass(frozen=True)
class PendingTextSelection:
    text: str
    user_id: str | None
    chat_id: str | None


@dataclass(frozen=True)
class PendingPostedAtCapture:
    record_id: str
    default_posted_at_local: str
    user_id: str | None
    chat_id: str | None
    upload_after_confirm: bool = False


@dataclass(frozen=True)
class PendingPlatformSelection:
    command_name: str
    command_body: str
    user_id: str | None
    chat_id: str | None


@dataclass(frozen=True)
class PendingProposalReplace:
    proposal_id: str
    field_key: str
    field_label: str
    user_id: str | None
    chat_id: str | None


@dataclass(frozen=True)
class PendingPlanFieldReplace:
    record_id: str
    field_name: str
    field_label: str
    user_id: str | None
    chat_id: str | None


_PLAN_DEMO_ACTION_TO_DECISION: dict[str, str] = {"skip_today": "skip"}


class RequestFlowService:
    @staticmethod
    def _extract_daily_plan_record_id_from_reply_markup(reply_markup: object) -> str:
        if not isinstance(reply_markup, dict):
            return ""
        inline_keyboard = reply_markup.get("inline_keyboard")
        if not isinstance(inline_keyboard, list):
            return ""
        for row in inline_keyboard:
            if not isinstance(row, list):
                continue
            for button in row:
                if not isinstance(button, dict):
                    continue
                callback_data = str(button.get("callback_data") or "").strip()
                parts = callback_data.split(":")
                if len(parts) >= 3 and parts[0] == "plan_demo" and parts[2].strip():
                    return parts[2].strip()
        return ""

    def __init__(
        self,
        execution_service: ExecutionService,
        pending_store: "ProactivePendingStore | None" = None,
        stale_rejection_suppression: "SuppressionStore | None" = None,
        posting_recommender: "PostingRecommender | None" = None,
        daily_plan_service: "DailyPlanService | None" = None,
        daily_plan_upload_service: "DailyPlanUploadService | None" = None,
        daily_plan_generation_service: "DailyPlanGenerationService | None" = None,
        plan_reminder_store: "PlanReminderStore | None" = None,
        content_proposal_store: ContentProposalStore | None = None,
        platform_mode_store: PlatformModeStore | None = None,
        daily_plan_message_store: DailyPlanMessageStore | None = None,
        idea_correction_service: IdeaCorrectionService | None = None,
    ) -> None:
        self.execution_service = execution_service
        self._pending_store = pending_store
        self._stale_rejection_suppression = stale_rejection_suppression
        self._recommender = posting_recommender
        self._daily_plan_service = daily_plan_service
        self._daily_plan_upload_service = daily_plan_upload_service
        self._daily_plan_generation_service = daily_plan_generation_service
        self._plan_reminder_store = plan_reminder_store
        self._content_proposal_store = content_proposal_store or ContentProposalStore()
        self._platform_mode_store = platform_mode_store or PlatformModeStore()
        self._daily_plan_message_store = daily_plan_message_store or DailyPlanMessageStore()
        self._idea_correction_service = idea_correction_service
        self._pending_text_inputs: dict[str, PendingTextSelection] = {}
        self._pending_posted_at_inputs: dict[str, PendingPostedAtCapture] = {}
        self._pending_platform_inputs: dict[str, PendingPlatformSelection] = {}
        self._pending_proposal_replace: dict[str, PendingProposalReplace] = {}
        self._pending_plan_field_replace: dict[str, PendingPlanFieldReplace] = {}
        # job_id of a Job parked in waiting_for_approval, keyed by (chat, user),
        # awaiting a /confirm or /reject from the same operator.
        self._pending_confirmations: dict[str, str] = {}

    def register_daily_plan_message(
        self,
        *,
        chat_id: int | None,
        message_id: int | None,
        record_id: str,
    ) -> None:
        if chat_id is None or message_id is None or not record_id:
            return
        self._daily_plan_message_store.put(
            chat_id=chat_id,
            message_id=message_id,
            record_id=record_id,
        )

    def handle_telegram_entry_handoff(self, entry_handoff: TelegramEntryHandoff) -> RequestFlowResult:
        request_context = self._build_request_context(entry_handoff)
        routed_command = entry_handoff.routed_command

        # Pending proposal check: intercept /confirm and /reject bound to a proposal message
        if self._pending_store is not None and routed_command.is_command:
            cmd = (routed_command.command_name or "").strip().lower()
            if cmd in ("confirm", "reject"):
                reply_id = self._parse_reply_id(request_context.reply_to_message_id)
                if reply_id is not None:
                    proposal = self._pending_store.consume(reply_id)
                    if proposal is not None:
                        if cmd == "confirm":
                            return self._handle_proactive_confirm(
                                entry_handoff=entry_handoff,
                                request_context=request_context,
                                proposal=proposal,
                            )
                        else:
                            return self._handle_proactive_reject(
                                entry_handoff=entry_handoff,
                                request_context=request_context,
                                proposal=proposal,
                            )

        # Job-level confirmation gate: a /confirm or /reject from the same operator
        # resolves a Job previously parked in waiting_for_approval by the gate.
        if routed_command.is_command:
            cmd = (routed_command.command_name or "").strip().lower()
            if cmd in ("confirm", "reject"):
                pending_job_id = self._pending_confirmations.get(
                    self._confirmation_key(request_context)
                )
                if pending_job_id is not None:
                    return self._handle_job_confirmation(
                        entry_handoff=entry_handoff,
                        request_context=request_context,
                        action=cmd,
                        job_id=pending_job_id,
                    )

        if not routed_command.is_command:
            _text_key = self._build_text_action_key(request_context)
            posted_at_pending = self._pending_posted_at_inputs.get(_text_key)
            if posted_at_pending is not None:
                return self._build_posted_at_capture_result(
                    entry_handoff=entry_handoff,
                    request_context=request_context,
                    pending=posted_at_pending,
                )
            plan_field_replace_pending = self._pending_plan_field_replace.get(_text_key)
            if plan_field_replace_pending is not None:
                return self._build_plan_field_replace_capture_result(
                    entry_handoff=entry_handoff,
                    request_context=request_context,
                    pending=plan_field_replace_pending,
                )
            proposal_replace_pending = self._pending_proposal_replace.get(_text_key)
            if proposal_replace_pending is not None:
                return self._build_proposal_replace_capture_result(
                    entry_handoff=entry_handoff,
                    request_context=request_context,
                    pending=proposal_replace_pending,
                )
            replied_plan_snapshot = self._resolve_replied_daily_plan_snapshot(
                entry_handoff=entry_handoff,
            )
            if replied_plan_snapshot is not None:
                return self._build_daily_plan_reply_result(
                    entry_handoff=entry_handoff,
                    request_context=request_context,
                    snapshot=replied_plan_snapshot,
                )
            active_proposal = self._content_proposal_store.active_for(
                chat_id=request_context.source_chat_id,
                user_id=request_context.source_user_id,
            )
            if active_proposal is not None:
                return self._build_content_followup_result(
                    entry_handoff=entry_handoff,
                    request_context=request_context,
                    proposal=active_proposal,
                    preserve_scope=True,
                )
            return self._build_free_text_selection_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if routed_command.command_name == "plan_demo":
            return self._build_plan_demo_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if routed_command.command_name == "plan_demo_callback":
            return self._build_plan_demo_callback_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if routed_command.command_name in ("menu", "start"):
            return self._build_menu_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if routed_command.command_name == "menu_callback":
            return self._build_menu_callback_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if routed_command.command_name == "text_action_callback":
            return self._build_text_action_callback_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if routed_command.command_name == "content_ops_callback":
            return self._build_content_ops_callback_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if routed_command.command_name == "modus":
            return self._build_modus_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if routed_command.command_name == "platform_mode_callback":
            return self._build_platform_mode_callback_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if not routed_command.is_known_command:
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="unknown_command",
                message_text=f"Unbekannter Befehl: {routed_command.command_name}",
            )

        platform_resolution = self._resolve_platform_for_command(
            entry_handoff=entry_handoff,
            command_name=routed_command.command_name,
            command_body=routed_command.command_body,
            request_context=request_context,
        )
        if isinstance(platform_resolution, RequestFlowResult):
            return platform_resolution
        resolved_command_body = platform_resolution
        request_context.command_body = resolved_command_body

        return self._build_executed_result(
            entry_handoff=entry_handoff,
            request_context=request_context,
            command_name=routed_command.command_name,
            command_body=resolved_command_body,
            title=f"{routed_command.command_name} request",
        )

    def _build_executed_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        command_name: str,
        command_body: str,
        title: str,
        callback_answer_text: str | None = None,
        edit_message_id: int | None = None,
        edit_message_text: str | None = None,
        edit_reply_markup: dict[str, object] | None = None,
        response_reply_to_message_id: int | None | object = _USE_RESPONSE_SHELL_REPLY_TO,
    ) -> RequestFlowResult:
        execution_result = self.execution_service.execute_request(
            request_context,
            job_type="operator_request",
            title=title,
            priority=0,
        )
        if execution_result.job_status == JobStatus.WAITING_FOR_APPROVAL:
            return self._build_confirmation_required_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                command_name=command_name,
                command_body=command_body,
                execution_result=execution_result,
            )
        output_snapshot = dict(execution_result.output_snapshot or {})
        if output_snapshot.get("lane_name") == "content_ops":
            proposal = self._build_content_proposal(
                proposal_id=execution_result.job_id,
                project_key=entry_handoff.project_context.project_key,
                snapshot=output_snapshot,
                request_context=request_context,
            )
            if proposal is None:
                proposal = self._build_idea_fallback_proposal(
                    proposal_id=execution_result.job_id,
                    project_key=entry_handoff.project_context.project_key,
                    snapshot=output_snapshot,
                    request_context=request_context,
                )
            if proposal is not None:
                self._content_proposal_store.save(proposal)
                output_snapshot["proposal_id"] = proposal.proposal_id

        formatter_payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name=command_name,
            command_body=command_body,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=(
                entry_handoff.response_shell.reply_to_message_id
                if response_reply_to_message_id is _USE_RESPONSE_SHELL_REPLY_TO
                else response_reply_to_message_id
            ),
            decision="executed",
            message_text="Anfrage wurde verarbeitet.",
            execution_summary={
                "job_id": execution_result.job_id,
                "run_id": execution_result.run_id,
                "job_status": execution_result.job_status.value,
                "run_status": execution_result.run_status,
                "event_count": execution_result.event_count,
                "result_summary": execution_result.result_summary,
                "error_summary": execution_result.error_summary,
                "output_snapshot": output_snapshot,
            },
            callback_query_id=entry_handoff.response_shell.callback_query_id,
            callback_answer_text=callback_answer_text,
            edit_message_id=edit_message_id,
            edit_message_text=edit_message_text,
            edit_reply_markup=edit_reply_markup,
        )

        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="executed",
            was_executed=True,
            execution_result=execution_result,
            formatter_payload=formatter_payload,
        )

    def _confirmation_key(self, request_context: RequestContext) -> str:
        return f"{request_context.source_chat_id or ''}:{request_context.source_user_id or ''}"

    def _build_confirmation_required_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        command_name: str,
        command_body: str,
        execution_result,
    ) -> RequestFlowResult:
        """Surface a gated Job and remember it so /confirm or /reject can resolve it."""
        self._pending_confirmations[self._confirmation_key(request_context)] = execution_result.job_id
        formatter_payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name=command_name,
            command_body=command_body,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="confirmation_required",
            message_text="Bestätigung erforderlich. Antworte mit /confirm oder /reject.",
            execution_summary={
                "job_id": execution_result.job_id,
                "job_status": execution_result.job_status.value,
                "approval_state": execution_result.approval_state.value,
                "event_count": execution_result.event_count,
            },
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="confirmation_required",
            was_executed=False,
            execution_result=execution_result,
            formatter_payload=formatter_payload,
        )

    def _handle_job_confirmation(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        action: str,
        job_id: str,
    ) -> RequestFlowResult:
        """Resolve a parked Job: approve+resume execution, or reject (no write)."""
        self._pending_confirmations.pop(self._confirmation_key(request_context), None)
        if action == "confirm":
            execution_result = self.execution_service.resume_confirmed_job(job_id)
            decision = "confirmed"
            message_text = "Bestätigt und ausgeführt."
            was_executed = True
        else:
            execution_result = self.execution_service.reject_job(job_id)
            decision = "rejected"
            message_text = "Abgelehnt. Es wurde nichts ausgeführt."
            was_executed = False
        formatter_payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name=action,
            command_body="",
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision=decision,
            message_text=message_text,
            execution_summary={
                "job_id": execution_result.job_id,
                "run_id": execution_result.run_id,
                "job_status": execution_result.job_status.value,
                "approval_state": execution_result.approval_state.value,
                "event_count": execution_result.event_count,
                "result_summary": execution_result.result_summary,
                "output_snapshot": execution_result.output_snapshot or {},
            },
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision=decision,
            was_executed=was_executed,
            execution_result=execution_result,
            formatter_payload=formatter_payload,
        )

    def _handle_proactive_confirm(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        proposal: "PendingProposal",
    ) -> RequestFlowResult:
        """Route a /confirm reply to the correct action via ExecutionService."""
        mark_stale_ctx = RequestContext(
            request_id=request_context.request_id + ":proactive_confirm",
            project_key=request_context.project_key,
            source_type=request_context.source_type,
            source_user_id=request_context.source_user_id,
            source_chat_id=request_context.source_chat_id,
            source_message_id=request_context.source_message_id,
            command_name=proposal.action_type,
            command_body=proposal.record_id,
            request_text=request_context.request_text,
            reply_to_message_id=request_context.reply_to_message_id,
        )
        execution_result = self.execution_service.execute_request(
            mark_stale_ctx,
            job_type="operator_request",
            title=f"{proposal.action_type} proactive confirm",
            priority=0,
        )
        formatter_payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name=proposal.action_type,
            command_body=proposal.record_id,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="executed",
            message_text="Bestätigung wurde verarbeitet.",
            execution_summary={
                "job_id": execution_result.job_id,
                "run_id": execution_result.run_id,
                "job_status": execution_result.job_status.value,
                "run_status": execution_result.run_status,
                "event_count": execution_result.event_count,
                "result_summary": execution_result.result_summary,
                "error_summary": execution_result.error_summary,
                "output_snapshot": execution_result.output_snapshot or {},
            },
        )
        _log.info(
            "proactive confirm handled | action=%s record_id=%s",
            proposal.action_type,
            proposal.record_id,
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=mark_stale_ctx,
            decision="executed",
            was_executed=True,
            execution_result=execution_result,
            formatter_payload=formatter_payload,
        )

    def _handle_proactive_reject(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        proposal: "PendingProposal",
    ) -> RequestFlowResult:
        """Record rejection suppression and return a non-execution result."""
        if self._stale_rejection_suppression is not None:
            key = f"stale_rejected_{proposal.record_id}"
            self._stale_rejection_suppression.record_sent(key)
        _log.info(
            "proactive reject handled | action=%s record_id=%s",
            proposal.action_type,
            proposal.record_id,
        )
        return self._build_non_execution_result(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="proactive_rejected",
            message_text=f"Okay, Entwurf {proposal.display_text} bleibt unverändert.",
        )

    @staticmethod
    def _parse_reply_id(reply_to_message_id: str | None) -> int | None:
        if reply_to_message_id is None:
            return None
        try:
            return int(reply_to_message_id)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _build_request_context(entry_handoff: TelegramEntryHandoff) -> RequestContext:
        request = entry_handoff.request
        routed_command = entry_handoff.routed_command
        reply_message_id = None
        if request.reply_context is not None and request.reply_context.message_id is not None:
            reply_message_id = str(request.reply_context.message_id)

        request_id = RequestFlowService._build_request_id(entry_handoff)
        request_text = request.normalized_text or request.raw_text

        return RequestContext(
            request_id=request_id,
            project_key=entry_handoff.project_context.project_key,
            source_type="telegram",
            source_user_id=str(request.user_id) if request.user_id is not None else None,
            source_chat_id=str(request.chat_id) if request.chat_id is not None else None,
            source_message_id=str(request.message_id) if request.message_id is not None else None,
            command_name=routed_command.command_name,
            command_body=routed_command.command_body,
            request_text=request_text,
            reply_to_message_id=reply_message_id,
        )

    @staticmethod
    def _build_request_id(entry_handoff: TelegramEntryHandoff) -> str:
        request = entry_handoff.request
        project_key = entry_handoff.project_context.project_key
        chat_id = request.chat_id if request.chat_id is not None else "unknown-chat"
        message_id = request.message_id if request.message_id is not None else "unknown-message"
        update_id = request.update_id if request.update_id is not None else "unknown-update"
        return f"telegram:{project_key}:{chat_id}:{message_id}:{update_id}"

    @staticmethod
    def _build_non_execution_result(
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        decision: str,
        message_text: str,
    ) -> RequestFlowResult:
        routed_command = entry_handoff.routed_command
        formatter_payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name=routed_command.command_name,
            command_body=routed_command.command_body,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision=decision,
            message_text=message_text,
            execution_summary={},
            callback_query_id=entry_handoff.response_shell.callback_query_id,
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision=decision,
            was_executed=False,
            execution_result=None,
            formatter_payload=formatter_payload,
        )

    def _resolve_platform_for_command(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        command_name: str,
        command_body: str,
        request_context: RequestContext,
    ) -> str | RequestFlowResult:
        content_actions = {"idea", "serie", "title", "vollauto", "draft", "hook", "cta", "caption"}
        if command_name not in content_actions:
            return command_body
        content_ops_service = getattr(self.execution_service, "content_ops_service", None)
        if content_ops_service is None:
            return command_body
        platform, normalized_body = content_ops_service.resolve_platform_hint(command_body)
        if platform:
            return f"{platform} {normalized_body}".strip()
        stored_mode = self._platform_mode_store.get_mode(
            chat_id=request_context.source_chat_id,
            user_id=request_context.source_user_id,
        )
        if stored_mode:
            return f"{stored_mode} {normalized_body}".strip()
        return self._build_platform_select_prompt(
            entry_handoff=entry_handoff,
            request_context=request_context,
            command_name=command_name,
            command_body=normalized_body,
        )

    def _build_content_proposal(
        self,
        *,
        proposal_id: str,
        project_key: str,
        snapshot: dict[str, object],
        request_context: RequestContext,
        previous_proposal: ContentProposal | None = None,
    ) -> ContentProposal | None:
        snapshot_action_type = str(snapshot.get("action_type") or "").strip().lower()
        action_type = snapshot_action_type
        if snapshot_action_type == "followup" and previous_proposal is not None:
            action_type = previous_proposal.action_type
        if action_type not in {"idea", "serie", "title", "vollauto", "draft", "hook", "cta", "caption", "followup"}:
            return None
        platform = (
            str(snapshot.get("platform") or "").strip().lower()
            or (previous_proposal.platform if previous_proposal is not None else "")
        )
        items = tuple(str(item).strip() for item in (snapshot.get("items") or []) if str(item).strip())
        fields: dict[str, str] = {}
        key_map = {
            "Serie/Thema": "serie_thema",
            "Title": "title_raw",
            "Hook": "hook",
            "CTA": "cta",
            "Caption": "caption",
            "Format": "format_typ",
            "Bereit": "bereit",
            "Idee": "title_raw",
        }
        for item in items:
            key, _, value = item.partition(":")
            field_name = key_map.get(key.strip())
            if field_name and value.strip():
                fields[field_name] = value.strip()
        if action_type == "idea" and not fields.get("title_raw"):
            visible_idea = RequestFlowService._first_visible_idea_text(items)
            if visible_idea:
                fields["title_raw"] = visible_idea
            else:
                return None
        return ContentProposal(
            proposal_id=proposal_id,
            project_key=project_key,
            action_type=action_type,
            platform=platform,
            fields=fields,
            source_command_body=(
                previous_proposal.source_command_body
                if previous_proposal is not None and snapshot_action_type == "followup"
                else str(snapshot.get("command_body") or request_context.command_body or "").strip()
            ),
            explanation=str(snapshot.get("summary") or "").strip(),
            chat_id=request_context.source_chat_id,
            user_id=request_context.source_user_id,
            commercial_class=str(snapshot.get("commercial_class") or "").strip() or None,
        )

    def _build_idea_fallback_proposal(
        self,
        *,
        proposal_id: str,
        project_key: str,
        snapshot: dict[str, object],
        request_context: RequestContext,
    ) -> ContentProposal | None:
        if not self._is_rejected_same_core_idea_fallback_snapshot(snapshot):
            return None
        return ContentProposal(
            proposal_id=proposal_id,
            project_key=project_key,
            action_type="idea_fallback",
            platform=str(snapshot.get("platform") or "").strip().lower(),
            fields={},
            source_command_body=str(snapshot.get("command_body") or request_context.command_body or "").strip(),
            explanation=str(snapshot.get("summary") or "").strip(),
            chat_id=None,
            user_id=None,
            commercial_class=str(snapshot.get("commercial_class") or "").strip() or None,
        )

    @staticmethod
    def _is_rejected_same_core_idea_fallback_snapshot(snapshot: dict[str, object]) -> bool:
        action_type = str(snapshot.get("action_type") or "").strip().lower()
        items = tuple(str(item).strip() for item in (snapshot.get("items") or []) if str(item).strip())
        summary = str(snapshot.get("summary") or "")
        return action_type == "idea" and not items and _REJECTED_SAME_CORE_FALLBACK_MARKER in summary

    @staticmethod
    def _build_text_action_reply_markup() -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "💡 Idee generieren", "callback_data": "text_action:idea"},
                    {"text": "📝 Voll Auto", "callback_data": "text_action:vollauto"},
                ],
                [
                    {"text": "🧩 Serie/Thema", "callback_data": "text_action:serie"},
                    {"text": "🏷️ Title", "callback_data": "text_action:title"},
                ],
                [
                    {"text": "🎣 Hook erstellen", "callback_data": "text_action:hook"},
                    {"text": "🪝 CTA erstellen", "callback_data": "text_action:cta"},
                ],
                [
                    {"text": "💬 Caption erstellen", "callback_data": "text_action:caption"},
                ],
                [
                    {"text": "✖️ Abbrechen", "callback_data": "text_action:cancel"},
                ],
            ]
        }

    @staticmethod
    def _build_text_selection_message(text: str) -> str:
        return (
            "📝 Eingabe erkannt\n\n"
            f"„{text}“\n\n"
            "Wofür möchtest du das verwenden?"
        )

    @staticmethod
    def _build_text_action_key(request_context: RequestContext) -> str:
        chat_id = request_context.source_chat_id or "unknown-chat"
        user_id = request_context.source_user_id or "unknown-user"
        return f"{chat_id}:{user_id}"

    @staticmethod
    def _build_posted_at_reply_markup(record_id: str, default_posted_at_local: str) -> dict:
        default_time = default_posted_at_local.split(" ", 1)[1] if " " in default_posted_at_local else default_posted_at_local
        return {
            "inline_keyboard": [
                [
                    {
                        "text": f"🕒 Standardzeit übernehmen ({default_time})",
                        "callback_data": f"plan_demo:posted_at_default:{record_id}",
                    }
                ]
            ]
        }

    @staticmethod
    def _build_posted_at_prompt(platform_label: str, default_posted_at_local: str, upload_after_confirm: bool = False) -> str:
        default_time = default_posted_at_local.split(" ", 1)[1] if " " in default_posted_at_local else default_posted_at_local
        if upload_after_confirm:
            return (
                f"🕒 {platform_label} — Upload wird nach Zeitbestätigung ausgeführt.\n\n"
                "Wann hast du gepostet?\n"
                f"Antworte mit `HH:MM` oder übernimm die geplante Zeit `{default_time}`."
            )
        return (
            f"🕒 {platform_label} in Airtable angelegt.\n\n"
            "Wann wurde wirklich gepostet?\n"
            f"Antworte mit `HH:MM` oder übernimm die geplante Zeit `{default_time}`."
        )

    def _build_posted_at_capture_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        pending: PendingPostedAtCapture,
    ) -> RequestFlowResult:
        text = (entry_handoff.request.normalized_text or entry_handoff.request.raw_text).strip()
        parsed = parse_posted_time_input(date=datetime.date.today().isoformat(), text=text)
        if parsed is None:
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="plan_demo_posted_at_pending",
                message_text="Bitte als HH:MM senden, z. B. 19:45.",
            )

        return self._finalize_posted_at_capture(
            entry_handoff=entry_handoff,
            request_context=request_context,
            record_id=pending.record_id,
            posted_at_local=parsed,
        )

    def _finalize_posted_at_capture(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        record_id: str,
        posted_at_local: str,
        callback_answer_text: str | None = None,
        edit_message_id: int | None = None,
    ) -> RequestFlowResult:
        pending_key = self._build_text_action_key(request_context)
        pending_capture = self._pending_posted_at_inputs.pop(pending_key, None)

        snapshot = TodayPlanSnapshot(record_id=record_id, decision="pending")
        if record_id and self._daily_plan_service is not None:
            try:
                snapshot = self._daily_plan_service.get_plan_record(
                    project_key=entry_handoff.project_context.project_key,
                    record_id=record_id,
                )
                if (
                    self._daily_plan_upload_service is not None
                    and pending_capture is not None
                    and pending_capture.upload_after_confirm
                ):
                    upload_result = self._daily_plan_upload_service.upload_plan_snapshot(
                        project_key=entry_handoff.project_context.project_key,
                        snapshot=snapshot,
                        date=datetime.date.today().isoformat(),
                    )
                    snapshot = self._daily_plan_upload_service.set_posted_at_local(
                        project_key=entry_handoff.project_context.project_key,
                        snapshot=upload_result.updated_snapshot,
                        posted_at_local=posted_at_local,
                    )
                    if self._plan_reminder_store is not None and snapshot.platform_record_id:
                        self._schedule_analytics_3d_reminder(
                            snapshot=snapshot,
                            chat_id=int(request_context.source_chat_id or "0"),
                        )
                elif self._daily_plan_upload_service is not None:
                    snapshot = self._daily_plan_upload_service.set_posted_at_local(
                        project_key=entry_handoff.project_context.project_key,
                        snapshot=snapshot,
                        posted_at_local=posted_at_local,
                    )
            except Exception as exc:
                _log.warning("daily_plan posted_at_local persist failed | record_id=%s error=%s", record_id, exc)

        payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name="plan_demo_posted_at",
            command_body=posted_at_local,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="plan_demo_posted_at",
            message_text=f"✅ Posting-Zeit gespeichert: {posted_at_local}",
            execution_summary={},
            callback_query_id=entry_handoff.response_shell.callback_query_id,
            callback_answer_text=callback_answer_text,
            send_response=edit_message_id is None,
            edit_message_id=edit_message_id,
            edit_message_text=(
                _build_platform_plan_text(snapshot) + f"\nPosted at local: {posted_at_local}"
                if edit_message_id is not None
                else None
            ),
            edit_reply_markup=_build_plan_demo_reply_markup(record_id) if edit_message_id is not None else None,
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="plan_demo_posted_at",
            was_executed=False,
            execution_result=None,
            formatter_payload=payload,
        )

    # ── Field replace: plan ──────────────────────────────────────────────────

    @staticmethod
    def _build_plan_replace_field_select_result(
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        record_id: str,
    ) -> RequestFlowResult:
        project_key = entry_handoff.project_context.project_key
        field_rows = []
        field_items = list(_DAILY_PLAN_FIELD_LABELS.items())
        for i in range(0, len(field_items), 2):
            row = [
                {
                    "text": field_items[i][1],
                    "callback_data": f"plan_demo:replace_field:{record_id}:{field_items[i][0]}",
                }
            ]
            if i + 1 < len(field_items):
                row.append(
                    {
                        "text": field_items[i + 1][1],
                        "callback_data": f"plan_demo:replace_field:{record_id}:{field_items[i + 1][0]}",
                    }
                )
            field_rows.append(row)
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="plan_demo_callback",
            was_executed=False,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name="plan_demo_callback",
                command_body=f"plan_demo:replace_field_select:{record_id}",
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="plan_demo_callback",
                message_text="🔄 Welches Feld willst du ersetzen?",
                execution_summary={},
                response_reply_markup={"inline_keyboard": field_rows},
                callback_query_id=entry_handoff.response_shell.callback_query_id,
                callback_answer_text="Ersetzen",
                send_response=True,
            ),
        )

    def _build_plan_field_replace_capture_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        pending: PendingPlanFieldReplace,
    ) -> RequestFlowResult:
        pending_key = self._build_text_action_key(request_context)
        self._pending_plan_field_replace.pop(pending_key, None)
        text = (entry_handoff.request.normalized_text or entry_handoff.request.raw_text or "").strip()
        project_key = entry_handoff.project_context.project_key
        snapshot = TodayPlanSnapshot(record_id=pending.record_id, decision="pending")
        if text and self._daily_plan_service is not None:
            try:
                current = self._daily_plan_service.get_plan_record(
                    project_key=project_key,
                    record_id=pending.record_id,
                )
                snapshot = self._daily_plan_service.patch_fields(
                    project_key=project_key,
                    record_id=pending.record_id,
                    fields={pending.field_name: text},
                    current=current,
                )
            except Exception as exc:
                _log.warning(
                    "plan field replace failed | record_id=%s field=%s error=%s",
                    pending.record_id,
                    pending.field_name,
                    exc,
                )
        plan_text = _build_platform_plan_text(snapshot) + f"\n✅ {pending.field_label} ersetzt."
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="plan_demo_callback",
            was_executed=False,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name="plan_demo_callback",
                command_body=pending.field_name,
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="plan_demo_callback",
                message_text=plan_text,
                execution_summary={},
                response_reply_markup=_build_plan_demo_reply_markup(pending.record_id),
                send_response=True,
            ),
        )

    # ── Field replace: content proposals ─────────────────────────────────────

    _PROPOSAL_PRIMARY_FIELD: dict[str, tuple[str, str]] = {
        "serie": ("serie_thema", "Serie/Thema"),
        "title": ("title_raw", "Title"),
        "hook": ("hook", "Hook"),
        "cta": ("cta", "CTA"),
        "caption": ("caption", "Caption"),
    }

    @staticmethod
    def _get_proposal_replace_field(proposal: ContentProposal) -> tuple[str, str]:
        mapping = RequestFlowService._PROPOSAL_PRIMARY_FIELD
        if proposal.action_type in mapping:
            return mapping[proposal.action_type]
        priority = ("caption", "hook", "cta", "title_raw", "serie_thema")
        for field_name in priority:
            if proposal.fields.get(field_name):
                label = RequestFlowService._display_label_for_field(field_name)
                return (field_name, label)
        if proposal.fields:
            first_key = next(iter(proposal.fields))
            return (first_key, RequestFlowService._display_label_for_field(first_key))
        return ("content", "Inhalt")

    def _build_proposal_replace_prompt(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        proposal: ContentProposal,
    ) -> RequestFlowResult:
        field_key, field_label = self._get_proposal_replace_field(proposal)
        pending_key = self._build_text_action_key(request_context)
        self._pending_proposal_replace[pending_key] = PendingProposalReplace(
            proposal_id=proposal.proposal_id,
            field_key=field_key,
            field_label=field_label,
            user_id=request_context.source_user_id,
            chat_id=request_context.source_chat_id,
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="content_ops_callback",
            was_executed=False,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=entry_handoff.project_context.project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name="content_ops_callback",
                command_body=f"content_ops:regenerate:{proposal.proposal_id}",
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="content_ops_callback",
                message_text=f"✏️ Womit willst du {field_label} ersetzen?\n\nSchreib den neuen Inhalt als Antwort.",
                execution_summary={},
                callback_query_id=entry_handoff.response_shell.callback_query_id,
                callback_answer_text="Ersetzen",
                send_response=True,
            ),
        )

    def _build_proposal_replace_capture_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        pending: PendingProposalReplace,
    ) -> RequestFlowResult:
        pending_key = self._build_text_action_key(request_context)
        self._pending_proposal_replace.pop(pending_key, None)
        proposal = self._content_proposal_store.get(pending.proposal_id)
        if proposal is None:
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="not_a_command",
                message_text="ℹ️ Vorschlag nicht mehr verfügbar.",
            )
        text = (entry_handoff.request.normalized_text or entry_handoff.request.raw_text or "").strip()
        if not text:
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="not_a_command",
                message_text="Bitte einen Inhalt senden.",
            )
        new_fields = {**proposal.fields, pending.field_key: text}
        updated_proposal = replace(proposal, fields=new_fields)
        self._content_proposal_store.replace(proposal.proposal_id, updated_proposal)
        display_items = list(self._proposal_items_for_display(updated_proposal))
        output_snapshot: dict[str, object] = {
            "lane_name": "content_ops",
            "action_type": updated_proposal.action_type,
            "items": display_items,
            "proposal_id": updated_proposal.proposal_id,
            "platform": updated_proposal.platform,
            "proposal_interactive": True,
        }
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="executed",
            was_executed=True,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=entry_handoff.project_context.project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name=updated_proposal.action_type,
                command_body=updated_proposal.source_command_body,
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="executed",
                message_text="Feld ersetzt.",
                execution_summary={
                    "job_id": updated_proposal.proposal_id,
                    "run_id": "",
                    "job_status": "completed",
                    "run_status": "completed",
                    "event_count": 0,
                    "result_summary": f"{pending.field_label} ersetzt.",
                    "error_summary": "",
                    "output_snapshot": output_snapshot,
                },
            ),
        )

    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_replied_daily_plan_snapshot(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
    ) -> TodayPlanSnapshot | None:
        if self._daily_plan_service is None:
            return None
        reply_context = entry_handoff.request.reply_context
        if reply_context is None:
            return None
        record_id = None
        if reply_context.reply_markup is not None:
            record_id = self._extract_daily_plan_record_id_from_reply_markup(
                reply_context.reply_markup,
            )
        if reply_context.message_id is not None and entry_handoff.request.chat_id is not None:
            record_id = record_id or self._daily_plan_message_store.get(
                chat_id=entry_handoff.request.chat_id,
                message_id=reply_context.message_id,
            )
        if record_id:
            try:
                return self._daily_plan_service.get_plan_record(
                    project_key=entry_handoff.project_context.project_key,
                    record_id=record_id,
                )
            except Exception as exc:
                _log.warning(
                    "daily_plan reply resolution via message_id failed | record_id=%s error=%s",
                    record_id,
                    exc,
                )
        if not reply_context.text.strip():
            return None
        first_line = reply_context.text.strip().splitlines()[0].strip()
        if not first_line.startswith("📋 Tagesplan · "):
            return None
        platform_label = first_line.removeprefix("📋 Tagesplan · ").strip().lower()
        platform = _PLATFORM_KEYS_BY_LABEL.get(platform_label)
        if not platform:
            return None
        try:
            snapshots = self._daily_plan_service.list_today_plans(
                project_key=entry_handoff.project_context.project_key,
                date=datetime.date.today().isoformat(),
            )
        except Exception as exc:
            _log.warning(
                "daily_plan reply resolution failed | platform=%s error=%s",
                platform,
                exc,
            )
            return None
        return next((snapshot for snapshot in snapshots if snapshot.platform == platform), None)

    def _build_daily_plan_reply_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        snapshot: TodayPlanSnapshot,
    ) -> RequestFlowResult:
        instruction = (entry_handoff.request.normalized_text or entry_handoff.request.raw_text).strip()
        parsed_edit = self._parse_daily_plan_reply_edit(instruction)
        if parsed_edit is None:
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="not_a_command",
                message_text=(
                    "Bitte als Feldanweisung auf den Tagesplan antworten, z. B. "
                    "„Änder cta zu: clown“ oder „Mach hook direkter“."
                ),
            )

        field_name, mode, value = parsed_edit
        current_snapshot = snapshot
        try:
            if mode == "literal":
                current_snapshot = self._daily_plan_service.patch_fields(
                    project_key=entry_handoff.project_context.project_key,
                    record_id=snapshot.record_id,
                    fields={field_name: value},
                    current=snapshot,
                )
            else:
                revised_value = self._revise_daily_plan_field(
                    entry_handoff=entry_handoff,
                    request_context=request_context,
                    snapshot=snapshot,
                    field_name=field_name,
                    instruction=instruction,
                )
                if not revised_value:
                    return self._build_non_execution_result(
                        entry_handoff=entry_handoff,
                        request_context=request_context,
                        decision="not_a_command",
                        message_text="Die Tagesplan-Änderung konnte gerade nicht erzeugt werden.",
                    )
                current_snapshot = self._daily_plan_service.patch_fields(
                    project_key=entry_handoff.project_context.project_key,
                    record_id=snapshot.record_id,
                    fields={field_name: revised_value},
                    current=snapshot,
                )
        except Exception as exc:
            _log.warning(
                "daily_plan reply edit failed | record_id=%s field=%s mode=%s error=%s",
                snapshot.record_id,
                field_name,
                mode,
                exc,
            )
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="not_a_command",
                message_text="Die Tagesplan-Row konnte gerade nicht aktualisiert werden.",
            )

        payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name="plan_demo_reply",
            command_body=instruction,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="plan_demo_callback",
            message_text=_build_platform_plan_text(current_snapshot),
            execution_summary={},
            response_reply_markup=_build_plan_demo_reply_markup(current_snapshot.record_id),
            callback_query_id=entry_handoff.response_shell.callback_query_id,
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="plan_demo_callback",
            was_executed=False,
            execution_result=None,
            formatter_payload=payload,
        )

    def _revise_daily_plan_field(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        snapshot: TodayPlanSnapshot,
        field_name: str,
        instruction: str,
    ) -> str:
        content_ops_service = getattr(self.execution_service, "content_ops_service", None)
        if content_ops_service is None:
            return ""
        proposal = ContentProposal(
            proposal_id=f"daily-plan-reply:{snapshot.record_id}:{field_name}",
            project_key=entry_handoff.project_context.project_key,
            action_type="vollauto",
            platform=snapshot.platform or "",
            fields=self._daily_plan_snapshot_fields(snapshot),
            source_command_body=self._daily_plan_seed_command(snapshot),
            chat_id=request_context.source_chat_id,
            user_id=request_context.source_user_id,
        )
        label = _DAILY_PLAN_FIELD_LABELS[field_name]
        lane_result = content_ops_service.follow_up(
            project_key=entry_handoff.project_context.project_key,
            proposal=proposal,
            instruction=(
                f"Bearbeite nur das Feld {label} im bestehenden Tagesplan. "
                "Alle anderen Felder sind bindender Kontext und dürfen nicht verändert werden. "
                f"Änderungswunsch: {instruction}. "
                f"Antworte nur mit {label}: ..."
            ),
        )
        return self._extract_field_value_from_items(
            items=tuple(str(item) for item in lane_result.items),
            field_name=field_name,
        )

    @staticmethod
    def _daily_plan_snapshot_fields(snapshot: TodayPlanSnapshot) -> dict[str, str]:
        return {
            "serie_thema": snapshot.serie_thema,
            "title_raw": snapshot.title_raw,
            "hook": snapshot.hook,
            "cta": snapshot.cta,
            "caption": snapshot.caption,
            "format_typ": snapshot.format_typ,
            "bereit": normalize_bereit_value(snapshot.bereit),
        }

    @staticmethod
    def _daily_plan_seed_command(snapshot: TodayPlanSnapshot) -> str:
        body = " ".join(
            part
            for part in (
                snapshot.platform or "",
                snapshot.serie_thema or snapshot.title_raw or snapshot.caption or snapshot.hook,
            )
            if part
        ).strip()
        return body

    def _parse_daily_plan_reply_edit(self, instruction: str) -> tuple[str, str, str] | None:
        stripped = instruction.strip()
        if not stripped:
            return None

        literal_patterns = (
            r"^(?:änder(?:e|n)?|aender(?:e|n)?)(?:\s+(?:den|die|das))?\s+(?P<field>.+?)\s+zu\s*:?\s*(?P<value>.+)$",
            r"^(?:ersetz(?:e|en)?)(?:\s+(?:den|die|das|es))?\s+(?P<field>.+?)\s+(?:mit|durch|in)\s*:?\s*(?P<value>.+)$",
            r"^(?:setz(?:e)?)(?:\s+(?:den|die|das))?\s+(?P<field>.+?)\s+auf\s*:?\s*(?P<value>.+)$",
        )
        for pattern in literal_patterns:
            match = re.match(pattern, stripped, re.IGNORECASE)
            if match:
                field_name = self._resolve_daily_plan_field_name(match.group("field"))
                value = match.group("value").strip().strip("\"' ")
                if field_name and value:
                    return field_name, "literal", value

        soft_patterns = (
            r"^(?:mach|formuliere)(?:\s+(?:den|die|das))?\s+(?P<field>.+?)\s+(?P<value>.+)$",
        )
        for pattern in soft_patterns:
            match = re.match(pattern, stripped, re.IGNORECASE)
            if match:
                field_name = self._resolve_daily_plan_field_name(match.group("field"))
                if field_name and match.group("value").strip():
                    return field_name, "soft", stripped
        return None

    def _resolve_daily_plan_field_name(self, raw_field: str) -> str:
        normalized = " ".join(raw_field.strip().lower().split())
        for field_name, aliases in _DAILY_PLAN_FIELD_ALIASES:
            if normalized in aliases:
                return field_name
        return ""

    @staticmethod
    def _extract_field_value_from_items(*, items: tuple[str, ...], field_name: str) -> str:
        label = _DAILY_PLAN_FIELD_LABELS.get(field_name, field_name)
        prefix = f"{label}:"
        for item in items:
            stripped = item.strip()
            if stripped.startswith(prefix):
                return stripped[len(prefix):].strip()
        return ""

    def _schedule_analytics_3d_reminder(
        self,
        *,
        snapshot: TodayPlanSnapshot,
        chat_id: int,
    ) -> None:
        from operator_core.proactive.plan_reminder_store import PlanReminder

        platform_labels: dict[str, str] = {
            "tiktok": "TikTok",
            "instagram_reel": "Instagram",
            "facebook_reel": "Facebook",
            "youtube_short": "YouTube",
        }
        fire_at = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=3)

        platform = snapshot.platform or ""
        platform_label = platform_labels.get(platform, platform or "Plattform")
        context_text = (
            f"📊 Analytics-Erinnerung · {platform_label}\n\n"
            f"Bitte jetzt die Analytics für den vor 3 Tagen hochgeladenen {platform_label}-Post eintragen.\n"
            f"Serie/Thema: {snapshot.serie_thema or '—'}\n"
            f"Title: {snapshot.title_raw or '—'}\n\n"
            f"Analytics-Werte jetzt in Airtable eintragen.\n"
            f"Record: {snapshot.platform_record_id}"
        )

        reminder = PlanReminder(
            key=f"analytics_3d:{snapshot.record_id}",
            fire_at=fire_at,
            chat_id=chat_id,
            platform=platform,
            record_id=snapshot.record_id,
            reminder_type="analytics_3d",
            context_text=context_text,
            analytics_record_id=snapshot.platform_record_id or "",
            analytics_table_id=snapshot.platform_table_id or "",
        )
        assert self._plan_reminder_store is not None
        self._plan_reminder_store.schedule(reminder)
        _log.info(
            "analytics_3d reminder scheduled | record_id=%s platform=%s fire_at=%s",
            snapshot.record_id,
            platform,
            fire_at.isoformat(),
        )

    def _build_free_text_selection_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
    ) -> RequestFlowResult:
        text = (entry_handoff.request.normalized_text or entry_handoff.request.raw_text).strip()
        if not text:
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="not_a_command",
                message_text="Nachricht wurde nicht als Befehl erkannt.",
            )

        pending_key = self._build_text_action_key(request_context)
        self._pending_text_inputs[pending_key] = PendingTextSelection(
            text=text,
            user_id=request_context.source_user_id,
            chat_id=request_context.source_chat_id,
        )

        payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name="message",
            command_body=text,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="free_text_selection",
            message_text=self._build_text_selection_message(text),
            execution_summary={},
            response_reply_markup=self._build_text_action_reply_markup(),
            callback_query_id=entry_handoff.response_shell.callback_query_id,
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="free_text_selection",
            was_executed=False,
            execution_result=None,
            formatter_payload=payload,
        )

    def _build_text_action_callback_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
    ) -> RequestFlowResult:
        callback_data = entry_handoff.request.callback_data
        action = callback_data.split(":", 1)[1].strip() if ":" in callback_data else ""
        if action == "draft":
            action = "vollauto"
        pending_key = self._build_text_action_key(request_context)
        pending = self._pending_text_inputs.get(pending_key)
        label = _TEXT_ACTION_LABELS.get(action, "Auswahl")

        if action == "cancel":
            self._pending_text_inputs.pop(pending_key, None)
            return RequestFlowResult(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="text_action_callback",
                was_executed=False,
                execution_result=None,
                formatter_payload=FormatterPayload(
                    project_key=entry_handoff.project_context.project_key,
                    project_display_name=entry_handoff.project_context.display_name,
                    command_name="text_action_callback",
                    command_body=callback_data,
                    response_chat_id=entry_handoff.response_shell.chat_id,
                    response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                    decision="text_action_callback",
                    message_text="",
                    execution_summary={},
                    callback_query_id=entry_handoff.response_shell.callback_query_id,
                    callback_answer_text=label,
                    send_response=False,
                    edit_message_id=entry_handoff.request.message_id,
                    edit_message_text="✖️ Eingabe verworfen.",
                    edit_reply_markup=None,
                ),
            )

        if pending is None or action not in {"idea", "serie", "title", "vollauto", "hook", "cta", "caption"}:
            return RequestFlowResult(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="text_action_callback",
                was_executed=False,
                execution_result=None,
                formatter_payload=FormatterPayload(
                    project_key=entry_handoff.project_context.project_key,
                    project_display_name=entry_handoff.project_context.display_name,
                    command_name="text_action_callback",
                    command_body=callback_data,
                    response_chat_id=entry_handoff.response_shell.chat_id,
                    response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                    decision="text_action_callback",
                    message_text="",
                    execution_summary={},
                    callback_query_id=entry_handoff.response_shell.callback_query_id,
                    callback_answer_text="Kein Text mehr vorhanden",
                    send_response=False,
                    edit_message_id=entry_handoff.request.message_id,
                    edit_message_text="ℹ️ Kein freier Text mehr zur Auswahl vorhanden.",
                    edit_reply_markup=None,
                ),
            )

        self._pending_text_inputs.pop(pending_key, None)
        selected_text = pending.text
        action_request_context = RequestContext(
            request_id=request_context.request_id + f":text_action:{action}",
            project_key=request_context.project_key,
            source_type=request_context.source_type,
            source_user_id=request_context.source_user_id,
            source_chat_id=request_context.source_chat_id,
            source_message_id=request_context.source_message_id,
            command_name=action,
            command_body=selected_text,
            request_text=selected_text,
            reply_to_message_id=request_context.reply_to_message_id,
        )
        return self._build_executed_result(
            entry_handoff=entry_handoff,
            request_context=action_request_context,
            command_name=action,
            command_body=selected_text,
            title=f"{action} request",
            callback_answer_text=label,
            edit_message_id=entry_handoff.request.message_id,
            edit_message_text=f"✅ Übernommen: {label} für „{selected_text}“",
            edit_reply_markup=None,
        )

    def _build_content_followup_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        proposal: ContentProposal,
        instruction_override: str | None = None,
        preserve_scope: bool = False,
    ) -> RequestFlowResult:
        instruction = instruction_override or (entry_handoff.request.normalized_text or entry_handoff.request.raw_text).strip()
        content_ops_service = getattr(self.execution_service, "content_ops_service", None)
        if content_ops_service is None:
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="not_a_command",
                message_text="Kein aktiver Vorschlagskontext verfügbar.",
            )
        execution_result = self.execution_service.execute_content_mutation(
            request_context=request_context,
            proposal=proposal,
            instruction=instruction,
            mutation_mode="followup",
            title="Follow-up request",
        )
        output_snapshot = dict(execution_result.output_snapshot or {})
        updated_proposal = self._build_content_proposal(
            proposal_id=proposal.proposal_id,
            project_key=entry_handoff.project_context.project_key,
            snapshot=output_snapshot,
            request_context=request_context,
            previous_proposal=proposal,
        )
        if updated_proposal is not None:
            self._content_proposal_store.replace(proposal.proposal_id, updated_proposal)
            output_snapshot["proposal_id"] = updated_proposal.proposal_id
            output_snapshot["proposal_interactive"] = True
            if preserve_scope:
                output_snapshot["action_type"] = updated_proposal.action_type
                output_snapshot["items"] = list(
                    self._proposal_items_for_display(
                        updated_proposal,
                        raw_items=tuple(str(item) for item in (output_snapshot.get("items") or [])),
                    )
                )
        formatter_payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name="followup",
            command_body=instruction,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="executed",
            message_text="Anfrage wurde verarbeitet.",
            execution_summary={
                "job_id": execution_result.job_id,
                "run_id": execution_result.run_id,
                "job_status": execution_result.job_status.value,
                "run_status": execution_result.run_status,
                "event_count": execution_result.event_count,
                "result_summary": execution_result.result_summary,
                "error_summary": execution_result.error_summary,
                "output_snapshot": output_snapshot,
            },
            callback_query_id=entry_handoff.response_shell.callback_query_id,
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="executed",
            was_executed=True,
            execution_result=None,
            formatter_payload=formatter_payload,
        )

    def _build_content_ops_callback_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
    ) -> RequestFlowResult:
        callback_data = entry_handoff.request.callback_data
        parts = callback_data.split(":")
        action = parts[1].strip() if len(parts) > 1 else ""
        proposal_id = parts[2].strip() if len(parts) > 2 else ""
        proposal = self._content_proposal_store.get(proposal_id)

        if action == "dismiss":
            self._content_proposal_store.discard(proposal_id)
            return self._build_content_ops_callback_payload(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                callback_answer_text="Verworfen",
                edit_text="✖️ Vorschlag verworfen.",
            )

        if proposal is None:
            return self._build_content_ops_callback_payload(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                callback_answer_text="Nicht mehr verfügbar",
                edit_text="ℹ️ Vorschlag nicht mehr verfügbar.",
            )

        if action == "rewrite":
            result = self._build_content_button_variant_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                proposal=proposal,
                action="rewrite",
            )
            return RequestFlowResult(
                entry_handoff=result.entry_handoff,
                request_context=result.request_context,
                decision=result.decision,
                was_executed=result.was_executed,
                execution_result=result.execution_result,
                formatter_payload=replace(
                    result.formatter_payload,
                    callback_query_id=entry_handoff.response_shell.callback_query_id,
                    callback_answer_text="Umformuliert",
                ),
            )

        if action == "regenerate":
            return self._build_proposal_replace_prompt(
                entry_handoff=entry_handoff,
                request_context=request_context,
                proposal=proposal,
            )

        if action == "idea_to_draft":
            return self._build_idea_to_draft_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                proposal=proposal,
            )

        if action in {"idea_fresh", "idea_angle"}:
            return self._build_idea_fallback_rerun_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                proposal=proposal,
                action=action,
            )

        if action == "accept":
            return self._build_idea_correction_accepted(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                proposal=proposal,
            )

        if action == "reject":
            return self._build_idea_rejection_tag_prompt(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                proposal=proposal,
            )

        if action == "rerate":
            return self._build_idea_rerate_prompt(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                proposal=proposal,
            )

        if action in {"reject_tag", "rt"}:
            reason_tag_raw = parts[3].strip() if len(parts) > 3 else ""
            return self._build_idea_correction_rejected(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                proposal=proposal,
                reason_tag_raw=self._decode_reject_reason_callback_value(reason_tag_raw),
            )

        if action != "apply":
            return self._build_content_ops_callback_payload(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                callback_answer_text="Unbekannte Aktion",
                edit_text="ℹ️ Unbekannte Vorschlagsaktion.",
            )

        if self._daily_plan_service is None:
            return self._build_content_ops_callback_payload(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                callback_answer_text="Nicht verfügbar",
                edit_text="ℹ️ Tagesplan-Service ist aktuell nicht verfügbar.",
            )

        if proposal.action_type == "idea":
            return self._build_content_ops_callback_payload(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                callback_answer_text="Erst Entwurf",
                edit_text="ℹ️ Eine Idee muss zuerst in einen Entwurf umgewandelt werden.",
            )

        today = datetime.date.today().isoformat()
        record_id = self._daily_plan_service.upsert_plan(
            project_key=proposal.project_key,
            date=today,
            plan_type="post",
            platform=proposal.platform,
            candidate_record_id=None,
            candidate_count=0,
        )
        current = self._daily_plan_service.get_plan_record(
            project_key=proposal.project_key,
            record_id=record_id,
        )
        overwrite_fields = self._fields_to_apply_for_proposal(proposal)
        patch_fields = {key: value for key, value in overwrite_fields.items() if value}
        if current.decision == "skip":
            patch_fields["decision"] = "pending"
        if not patch_fields:
            return self._build_content_ops_callback_payload(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=callback_data,
                callback_answer_text="Kein Inhalt",
                edit_text="⚠️ Kein Inhalt zum Speichern. Bitte zuerst einen Vorschlag mit /vollauto oder /idee erstellen.",
            )
        current = self._daily_plan_service.patch_fields(
            project_key=proposal.project_key,
            record_id=record_id,
            fields=patch_fields,
            current=current,
        )
        self._content_proposal_store.discard(proposal_id)
        platform_label = _PLATFORM_LABELS.get(proposal.platform, proposal.platform or "Plattform")
        return self._build_content_ops_callback_payload(
            entry_handoff=entry_handoff,
            request_context=request_context,
            callback_data=callback_data,
            callback_answer_text="In Tagesplan gesetzt",
            edit_text=f"✅ In Tagesplan gesetzt · {platform_label}\n\n{_build_platform_plan_text(current)}",
            edit_reply_markup=_build_plan_demo_reply_markup(current.record_id),
        )

    def _build_idea_to_draft_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        proposal: ContentProposal,
    ) -> RequestFlowResult:
        content_ops_service = getattr(self.execution_service, "content_ops_service", None)
        if content_ops_service is None:
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="not_a_command",
                message_text="Kein aktiver Vorschlagskontext verfügbar.",
            )

        idea_text = str(proposal.fields.get("title_raw") or "").strip()
        if not idea_text:
            return self._build_content_ops_callback_payload(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=entry_handoff.request.callback_data,
                callback_answer_text="Kein Inhalt",
                edit_text="⚠️ Idee nicht mehr verfügbar.",
            )

        command_body = f"{proposal.platform} {idea_text}".strip() if proposal.platform else idea_text
        if hasattr(content_ops_service, "supports"):
            action_request_context = replace(
                request_context,
                command_name="vollauto",
                command_body=command_body,
                request_text=command_body,
            )
            execution_result = self.execution_service.execute_request(
                action_request_context,
                job_type="operator_request",
                title="vollauto request",
                priority=0,
            )
            output_snapshot = dict(execution_result.output_snapshot or {})
        else:
            lane_result = content_ops_service.handle(
                project_key=entry_handoff.project_context.project_key,
                action_type="vollauto",
                command_body=command_body,
            )
            execution_result = None
            output_snapshot = lane_result.to_snapshot()
        updated_proposal = self._build_content_proposal(
            proposal_id=proposal.proposal_id,
            project_key=entry_handoff.project_context.project_key,
            snapshot=output_snapshot,
            request_context=request_context,
        )
        if updated_proposal is not None:
            self._content_proposal_store.replace(proposal.proposal_id, updated_proposal)
            output_snapshot["proposal_id"] = updated_proposal.proposal_id
            output_snapshot["action_type"] = updated_proposal.action_type
            output_snapshot["items"] = list(
                self._proposal_items_for_display(
                    updated_proposal,
                    raw_items=tuple(str(item) for item in (output_snapshot.get("items") or [])),
                )
            )

        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="executed",
            was_executed=True,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=entry_handoff.project_context.project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name="vollauto",
                command_body=command_body,
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="executed",
                message_text="Anfrage wurde verarbeitet.",
                execution_summary={
                    "job_id": execution_result.job_id if execution_result is not None else proposal.proposal_id,
                    "run_id": execution_result.run_id if execution_result is not None else "",
                    "job_status": execution_result.job_status.value if execution_result is not None else "completed",
                    "run_status": execution_result.run_status if execution_result is not None else "completed",
                    "event_count": execution_result.event_count if execution_result is not None else 0,
                    "result_summary": execution_result.result_summary if execution_result is not None else lane_result.summary,
                    "error_summary": execution_result.error_summary if execution_result is not None else "",
                    "output_snapshot": output_snapshot,
                },
                callback_query_id=entry_handoff.response_shell.callback_query_id,
                callback_answer_text="Entwurf erstellt",
            ),
        )

    def _build_idea_fallback_rerun_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        proposal: ContentProposal,
        action: str,
    ) -> RequestFlowResult:
        if proposal.action_type != "idea_fallback":
            return self._build_content_ops_callback_payload(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=entry_handoff.request.callback_data,
                callback_answer_text="Nicht verfügbar",
                edit_text="ℹ️ Diese Aktion ist für diesen Vorschlag nicht verfügbar.",
            )

        command_body = self._idea_fallback_rerun_command_body(proposal=proposal, action=action)
        callback_answer_text = "Frischer" if action == "idea_fresh" else "Neuer Winkel"
        action_request_context = replace(
            request_context,
            command_name="idea",
            command_body=command_body,
            request_text=f"/idea {command_body}".strip(),
        )
        result = self._build_executed_result(
            entry_handoff=entry_handoff,
            request_context=action_request_context,
            command_name="idea",
            command_body=command_body,
            title="idea request",
            callback_answer_text=callback_answer_text,
            edit_message_id=entry_handoff.request.message_id,
            edit_message_text=f"↻ {callback_answer_text} wird erstellt.",
            edit_reply_markup=None,
            response_reply_to_message_id=None,
        )
        # If the recovery attempt still produced a no-item fallback (same-core guard blocked
        # again), surface a path-specific message instead of the same generic fallback with
        # Frischer/Neuer-Winkel buttons — otherwise the user sees an infinite loop.
        output_snapshot = dict(
            result.formatter_payload.execution_summary.get("output_snapshot") or {}
        )
        if self._is_rejected_same_core_idea_fallback_snapshot(output_snapshot):
            return self._build_content_ops_callback_payload(
                entry_handoff=entry_handoff,
                request_context=request_context,
                callback_data=entry_handoff.request.callback_data,
                callback_answer_text="Kein Vorschlag",
                edit_text=self._idea_fallback_rerun_exhausted_text(action),
            )
        return result

    @staticmethod
    def _idea_fallback_rerun_exhausted_text(action: str) -> str:
        if action == "idea_fresh":
            return (
                "💡 Ich finde gerade keinen ausreichend frischen Vorschlag.\n"
                "Versuch einen anderen Alltagspunkt."
            )
        return (
            "🔁 Auch mit neuem Winkel landet es noch zu nah am verworfenen Kern.\n"
            "Versuch einen anderen Alltagspunkt."
        )

    @staticmethod
    def _normalize_idea_fallback_source_command_body(source: str) -> str:
        cleaned = str(source or "").strip()
        while True:
            without_command = re.sub(
                r"^/(?:idea|idee)(?:@[A-Za-z0-9_]+)?\b\s*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()
            if without_command == cleaned:
                return cleaned
            cleaned = without_command

    @staticmethod
    def _idea_fallback_rerun_command_body(*, proposal: ContentProposal, action: str) -> str:
        from operator_core.core.content_ops.duplicate_guard import (
            _IDEA_CONCRETE_VERBS,
            _IDEA_SCENE_CONNECTORS,
        )
        platform_prefix = f"{proposal.platform} " if proposal.platform else ""

        if action == "idea_fresh":
            # Strip the original source entirely so the rerun enters IDEATION mode and is not
            # anchored to the blocked scene.  Keeping the original prompt (e.g. "beim Kochen
            # plötzlich sitzen") would re-trigger MIRROR mode and send the LLM back to the
            # same core the guard just rejected.
            return (
                f"{platform_prefix}Neue Idee – frischer Alltagspunkt, "
                "kein Bezug auf den gerade verworfenen Kern."
            ).strip()

        # idea_angle: keep thematic/topical domain words from the source but strip the tokens
        # that trigger MIRROR mode (scene connectors + concrete verbs + first-person pronouns).
        # This forces IDEATION on the same thematic domain without re-locking the scene.
        _mirror_triggers = _IDEA_SCENE_CONNECTORS | _IDEA_CONCRETE_VERBS | {"ich", "mir"}
        source = RequestFlowService._normalize_idea_fallback_source_command_body(
            proposal.source_command_body
        )
        stripped_words = [
            w for w in source.split()
            if w.lower() not in _mirror_triggers and not w.lower().startswith("mein")
        ]
        stripped_source = " ".join(stripped_words).strip()
        instruction = (
            "Gleiche Symptom- oder Alltagsfamilie, aber andere konkrete Szene "
            "und andere Reibung als der gerade verworfene Kern."
        )
        if stripped_source:
            if proposal.platform and not stripped_source.lower().startswith(
                proposal.platform.lower()
            ):
                stripped_source = f"{proposal.platform} {stripped_source}"
            return f"{stripped_source} {instruction}".strip()
        return f"{platform_prefix}{instruction}".strip()

    def _build_content_button_variant_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        proposal: ContentProposal,
        action: str,
    ) -> RequestFlowResult:
        content_ops_service = getattr(self.execution_service, "content_ops_service", None)
        if content_ops_service is None:
            return self._build_non_execution_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
                decision="not_a_command",
                message_text="Kein aktiver Vorschlagskontext verfügbar.",
            )
        if action == "rewrite":
            execution_result = self.execution_service.execute_content_mutation(
                request_context=request_context,
                proposal=proposal,
                instruction=self._build_content_button_instruction(action=action, proposal=proposal),
                mutation_mode="rewrite",
                title="Rewrite request",
            )
            callback_answer_text = "Umformuliert"
        elif action == "regenerate" and hasattr(content_ops_service, "regenerate_proposal"):
            lane_result = content_ops_service.regenerate_proposal(
                project_key=entry_handoff.project_context.project_key,
                proposal=proposal,
            )
            execution_result = None
            callback_answer_text = "Neu generiert"
        else:
            lane_result = content_ops_service.follow_up(
                project_key=entry_handoff.project_context.project_key,
                proposal=proposal,
                instruction=self._build_content_button_instruction(action=action, proposal=proposal),
            )
            execution_result = None
            callback_answer_text = "Umformuliert" if action == "rewrite" else "Neu generiert"
        output_snapshot = dict(execution_result.output_snapshot or {}) if execution_result is not None else lane_result.to_snapshot()
        updated_proposal = self._build_content_proposal(
            proposal_id=proposal.proposal_id,
            project_key=entry_handoff.project_context.project_key,
            snapshot=output_snapshot,
            request_context=request_context,
            previous_proposal=proposal,
        )
        if updated_proposal is not None:
            self._content_proposal_store.replace(proposal.proposal_id, updated_proposal)
            output_snapshot["proposal_id"] = updated_proposal.proposal_id
            output_snapshot["action_type"] = updated_proposal.action_type
            output_snapshot["items"] = list(
                self._proposal_items_for_display(
                    updated_proposal,
                    raw_items=tuple(str(item) for item in (output_snapshot.get("items") or [])),
                )
            )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="executed",
            was_executed=True,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=entry_handoff.project_context.project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name=proposal.action_type,
                command_body=str(output_snapshot.get("command_body") or proposal.source_command_body),
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="executed",
                message_text="Anfrage wurde verarbeitet.",
                execution_summary={
                    "job_id": execution_result.job_id if execution_result is not None else proposal.proposal_id,
                    "run_id": execution_result.run_id if execution_result is not None else "",
                    "job_status": execution_result.job_status.value if execution_result is not None else "completed",
                    "run_status": execution_result.run_status if execution_result is not None else "completed",
                    "event_count": execution_result.event_count if execution_result is not None else 0,
                    "result_summary": execution_result.result_summary if execution_result is not None else lane_result.summary,
                    "error_summary": execution_result.error_summary if execution_result is not None else "",
                    "output_snapshot": output_snapshot,
                },
                callback_query_id=entry_handoff.response_shell.callback_query_id,
                callback_answer_text=callback_answer_text,
            ),
        )

    # ------------------------------------------------------------------
    # Correction capture callbacks
    # ------------------------------------------------------------------

    def _record_idea_correction(
        self,
        *,
        proposal: ContentProposal,
        status: CorrectionStatus,
        reason_tag: CorrectionReasonTag = CorrectionReasonTag.none,
        corrected_output: str | None = None,
    ) -> None:
        if self._idea_correction_service is None:
            _log.warning("idea_correction: no IdeaCorrectionService configured — correction not persisted")
            return
        bot_output = self._visible_idea_text(proposal) or str(proposal.explanation or "").strip()
        self._idea_correction_service.record_correction(
            project_key=proposal.project_key,
            proposal_id=proposal.proposal_id,
            prompt=proposal.source_command_body,
            bot_output=bot_output,
            commercial_class=proposal.commercial_class,
            status=status,
            reason_tag=reason_tag,
            corrected_output=corrected_output,
        )

    def _build_idea_correction_accepted(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        callback_data: str,
        proposal: ContentProposal,
    ) -> RequestFlowResult:
        self._record_idea_correction(proposal=proposal, status=CorrectionStatus.accepted_as_is)
        edit_text = self._idea_correction_edit_text(
            proposal=proposal,
            status_line="✅ Idee als gut bewertet.",
        )
        return self._build_content_ops_callback_payload(
            entry_handoff=entry_handoff,
            request_context=request_context,
            callback_data=callback_data,
            callback_answer_text="Gespeichert ✓",
            edit_text=edit_text,
            edit_reply_markup=self._idea_post_correction_markup(proposal),
        )

    def _build_idea_rejection_tag_prompt(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        callback_data: str,
        proposal: ContentProposal,
    ) -> RequestFlowResult:
        edit_text = self._idea_correction_edit_text(
            proposal=proposal,
            status_line="❌ Warum ist die Idee nicht gut?\nBitte einen Grund auswählen:",
        )
        return self._build_content_ops_callback_payload(
            entry_handoff=entry_handoff,
            request_context=request_context,
            callback_data=callback_data,
            callback_answer_text="Grund auswählen",
            edit_text=edit_text,
            edit_reply_markup=self._idea_rejection_reason_markup(proposal),
        )

    def _build_idea_rerate_prompt(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        callback_data: str,
        proposal: ContentProposal,
    ) -> RequestFlowResult:
        edit_text = self._idea_correction_edit_text(
            proposal=proposal,
            status_line="↩ Bewertung ändern:",
        )
        return self._build_content_ops_callback_payload(
            entry_handoff=entry_handoff,
            request_context=request_context,
            callback_data=callback_data,
            callback_answer_text="Bewertung ändern",
            edit_text=edit_text,
            edit_reply_markup=self._idea_rating_markup(proposal),
        )

    def _build_idea_correction_rejected(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        callback_data: str,
        proposal: ContentProposal,
        reason_tag_raw: str,
    ) -> RequestFlowResult:
        try:
            reason_tag = CorrectionReasonTag(reason_tag_raw)
        except ValueError:
            reason_tag = CorrectionReasonTag.none
        self._record_idea_correction(
            proposal=proposal,
            status=CorrectionStatus.rejected,
            reason_tag=reason_tag,
        )
        label = REASON_TAG_LABELS.get(reason_tag.value, reason_tag.value)
        edit_text = self._idea_correction_edit_text(
            proposal=proposal,
            status_line=f"✖️ Idee abgelehnt — {label}.",
        )
        return self._build_content_ops_callback_payload(
            entry_handoff=entry_handoff,
            request_context=request_context,
            callback_data=callback_data,
            callback_answer_text="Gespeichert ✓",
            edit_text=edit_text,
            edit_reply_markup=self._idea_post_correction_markup(proposal),
        )

    @staticmethod
    def _decode_reject_reason_callback_value(value: str) -> str:
        tag = _REJECT_REASON_CALLBACK_TAGS.get(value)
        return tag.value if tag is not None else value

    @staticmethod
    def _visible_idea_text(proposal: ContentProposal) -> str:
        if proposal.action_type != "idea":
            return ""
        return str(proposal.fields.get("title_raw") or "").strip()

    @staticmethod
    def _idea_correction_edit_text(*, proposal: ContentProposal, status_line: str) -> str:
        visible_text = RequestFlowService._visible_idea_text(proposal)
        if not visible_text:
            return status_line
        return f"{visible_text}\n\n{status_line}"

    @staticmethod
    def _idea_primary_action_markup(proposal: ContentProposal) -> dict | None:
        if proposal.action_type != "idea":
            return None
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "📝 Aus Idee Entwurf erstellen",
                        "callback_data": f"content_ops:idea_to_draft:{proposal.proposal_id}",
                    },
                    {"text": "✖️ Verwerfen", "callback_data": f"content_ops:dismiss:{proposal.proposal_id}"},
                ],
            ]
        }

    @staticmethod
    def _idea_rating_markup(proposal: ContentProposal) -> dict | None:
        primary = RequestFlowService._idea_primary_action_markup(proposal)
        if primary is None:
            return None
        rows = list(primary["inline_keyboard"])
        rows.append([
            {"text": "✅ Gut", "callback_data": f"content_ops:accept:{proposal.proposal_id}"},
            {"text": "❌ Nicht gut", "callback_data": f"content_ops:reject:{proposal.proposal_id}"},
        ])
        return {"inline_keyboard": rows}

    @staticmethod
    def _idea_rejection_reason_markup(proposal: ContentProposal) -> dict | None:
        if proposal.action_type != "idea":
            return None
        pid = proposal.proposal_id
        def reason_callback(tag: CorrectionReasonTag) -> str:
            return f"content_ops:rt:{pid}:{_REJECT_REASON_CALLBACK_CODES[tag]}"

        rows = [
            [
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.too_literal.value],
                 "callback_data": reason_callback(CorrectionReasonTag.too_literal)},
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.too_free.value],
                 "callback_data": reason_callback(CorrectionReasonTag.too_free)},
            ],
            [
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.moment_missed.value],
                 "callback_data": reason_callback(CorrectionReasonTag.moment_missed)},
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.tone_off.value],
                 "callback_data": reason_callback(CorrectionReasonTag.tone_off)},
            ],
            [
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.not_julia.value],
                 "callback_data": reason_callback(CorrectionReasonTag.not_julia)},
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.too_broad.value],
                 "callback_data": reason_callback(CorrectionReasonTag.too_broad)},
            ],
            [
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.too_loud.value],
                 "callback_data": reason_callback(CorrectionReasonTag.too_loud)},
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.too_producty.value],
                 "callback_data": reason_callback(CorrectionReasonTag.too_producty)},
            ],
            [
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.weak_hook.value],
                 "callback_data": reason_callback(CorrectionReasonTag.weak_hook)},
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.weak_clarity.value],
                 "callback_data": reason_callback(CorrectionReasonTag.weak_clarity)},
            ],
            [
                {"text": REASON_TAG_LABELS[CorrectionReasonTag.good_but_wrong_platform.value],
                 "callback_data": reason_callback(CorrectionReasonTag.good_but_wrong_platform)},
            ],
            [
                {"text": "↩ Zurück", "callback_data": f"content_ops:rerate:{pid}"},
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def _idea_post_correction_markup(proposal: ContentProposal) -> dict | None:
        primary = RequestFlowService._idea_primary_action_markup(proposal)
        if primary is None:
            return None
        rows = list(primary["inline_keyboard"])
        rows.append([
            {"text": "↩ Bewertung ändern", "callback_data": f"content_ops:rerate:{proposal.proposal_id}"},
        ])
        return {"inline_keyboard": rows}

    @staticmethod
    def _first_visible_idea_text(items: tuple[str, ...]) -> str:
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            key, separator, value = text.partition(":")
            if separator and key.strip().lower() in {"idee", "antwort"}:
                text = value.strip()
            if text:
                return text
        return ""

    def _build_content_ops_callback_payload(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        callback_data: str,
        callback_answer_text: str,
        edit_text: str,
        edit_reply_markup: dict | None = None,
    ) -> RequestFlowResult:
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="content_ops_callback",
            was_executed=False,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=entry_handoff.project_context.project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name="content_ops_callback",
                command_body=callback_data,
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="content_ops_callback",
                message_text="",
                execution_summary={},
                callback_query_id=entry_handoff.response_shell.callback_query_id,
                callback_answer_text=callback_answer_text,
                send_response=False,
                edit_message_id=entry_handoff.request.message_id,
                edit_message_text=edit_text,
                edit_reply_markup=edit_reply_markup,
            ),
        )

    @staticmethod
    def _fields_to_apply_for_proposal(proposal: ContentProposal) -> dict[str, str]:
        if proposal.action_type in {"vollauto", "draft", "followup"}:
            return dict(proposal.fields)
        field_map = {
            "serie": ("serie_thema",),
            "title": ("title_raw",),
            "hook": ("hook",),
            "cta": ("cta",),
            "caption": ("caption",),
            "idea": ("title_raw",),
        }
        allowed = field_map.get(proposal.action_type, tuple(proposal.fields.keys()))
        return {key: value for key, value in proposal.fields.items() if key in allowed}

    @staticmethod
    def _build_content_button_instruction(*, action: str, proposal: ContentProposal) -> str:
        if action == "rewrite" and proposal.action_type == "vollauto":
            return (
                "Formuliere den aktuellen Vorschlag sichtbar neu. "
                "Behalte Thema, Kernrichtung und Plattform bei, aber ändere Ton, Satzmelodie oder Formulierung spürbar."
            )
        if action == "rewrite":
            label = RequestFlowService._button_field_label(proposal.action_type)
            return (
                f"Formuliere nur das Feld {label} sichtbar neu. "
                f"Behalte Thema, Plattform und Grundrichtung bei. Antworte nur mit {label}."
            )
        return (
            "Erzeuge eine neue passende Variante im selben Kontext. "
            "Nutze weiterhin Plattform, Analytics und Regeln, aber wähle einen frischen Angle, eine neue Formulierung oder eine andere CTA-Richtung."
        )

    @staticmethod
    def _button_field_label(action_type: str) -> str:
        return {
            "serie": "Serie/Thema",
            "title": "Title",
            "hook": "Hook",
            "cta": "CTA",
            "caption": "Caption",
            "idea": "Idee",
        }.get(action_type, "den Vorschlag")

    @staticmethod
    def _proposal_items_for_display(
        proposal: ContentProposal,
        raw_items: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        if proposal.action_type in {"vollauto", "draft"}:
            order = ("serie_thema", "title_raw", "hook", "cta", "caption", "format_typ", "bereit")
            base_items = tuple(
                f"{RequestFlowService._display_label_for_field(field_name)}: {proposal.fields[field_name]}"
                for field_name in order
                if proposal.fields.get(field_name)
            )
            answer_items = tuple(
                item for item in raw_items
                if item.startswith("Antwort:") and item not in base_items
            )
            return base_items + answer_items
        if proposal.action_type == "idea":
            title_value = proposal.fields.get("title_raw", "")
            base_items = (f"Idee: {title_value}",) if title_value else tuple()
            answer_items = tuple(
                item for item in raw_items
                if item.startswith("Antwort:") and item not in base_items
            )
            return base_items + answer_items
        field_map = {
            "serie": "serie_thema",
            "title": "title_raw",
            "hook": "hook",
            "cta": "cta",
            "caption": "caption",
        }
        field_name = field_map.get(proposal.action_type)
        if field_name and proposal.fields.get(field_name):
            base_items = (f"{RequestFlowService._display_label_for_field(field_name)}: {proposal.fields[field_name]}",)
            answer_items = tuple(
                item for item in raw_items
                if item.startswith("Antwort:") and item not in base_items
            )
            return base_items + answer_items
        base_items = tuple(
            f"{RequestFlowService._display_label_for_field(key)}: {value}"
            for key, value in proposal.fields.items()
            if value
        )
        answer_items = tuple(
            item for item in raw_items
            if item.startswith("Antwort:") and item not in base_items
        )
        return base_items + answer_items

    @staticmethod
    def _display_label_for_field(field_name: str) -> str:
        return {
            "serie_thema": "Serie/Thema",
            "title_raw": "Title",
            "hook": "Hook",
            "cta": "CTA",
            "caption": "Caption",
            "format_typ": "Format",
            "bereit": "Bereit",
        }.get(field_name, field_name)

    # ------------------------------------------------------------------
    # Platform mode (modus) handlers
    # ------------------------------------------------------------------

    _PLATFORM_SELECT_BUTTONS = [
        {"text": "TikTok", "platform": "tiktok"},
        {"text": "Instagram", "platform": "instagram_reel"},
        {"text": "Facebook", "platform": "facebook_reel"},
        {"text": "YouTube", "platform": "youtube_short"},
    ]

    def _build_platform_select_markup(self, *, callback_action: str) -> dict:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": btn["text"],
                        "callback_data": f"platform_mode:{callback_action}:{btn['platform']}",
                    }
                    for btn in self._PLATFORM_SELECT_BUTTONS
                ],
                [{"text": "✖️ Modus aufheben", "callback_data": "platform_mode:clear"}],
            ]
        }

    def _build_modus_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
    ) -> RequestFlowResult:
        self._pending_platform_inputs.pop(self._build_text_action_key(request_context), None)
        current_mode = self._platform_mode_store.get_mode(
            chat_id=request_context.source_chat_id,
            user_id=request_context.source_user_id,
        )
        platform_label = _PLATFORM_LABELS.get(current_mode or "", current_mode or "")
        if current_mode and platform_label:
            mode_text = f"Aktiver Modus: {platform_label}"
        else:
            mode_text = "Kein Plattform-Modus aktiv."
        message_text = f"{mode_text}\n\nPlattform wählen:"
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="modus",
            was_executed=False,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=entry_handoff.project_context.project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name="modus",
                command_body="",
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="modus",
                message_text=message_text,
                execution_summary={},
                send_response=True,
                response_reply_markup=self._build_platform_select_markup(callback_action="set"),
            ),
        )

    def _build_platform_mode_callback_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
    ) -> RequestFlowResult:
        callback_data = entry_handoff.request.callback_data or ""
        parts = callback_data.split(":", 2)
        action = parts[1] if len(parts) > 1 else ""
        platform = parts[2] if len(parts) > 2 else ""
        pending_key = self._build_text_action_key(request_context)
        pending = None
        if action == "continue":
            pending = self._pending_platform_inputs.pop(pending_key, None)
        elif action == "set":
            self._pending_platform_inputs.pop(pending_key, None)

        if action == "continue" and platform:
            if pending is None:
                return RequestFlowResult(
                    entry_handoff=entry_handoff,
                    request_context=request_context,
                    decision="platform_mode_callback",
                    was_executed=False,
                    execution_result=None,
                    formatter_payload=FormatterPayload(
                        project_key=entry_handoff.project_context.project_key,
                        project_display_name=entry_handoff.project_context.display_name,
                        command_name="platform_mode_callback",
                        command_body=callback_data,
                        response_chat_id=entry_handoff.response_shell.chat_id,
                        response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                        decision="platform_mode_callback",
                        message_text="",
                        execution_summary={},
                        callback_query_id=entry_handoff.response_shell.callback_query_id,
                        callback_answer_text="Auswahl abgelaufen",
                        send_response=False,
                        edit_message_id=entry_handoff.request.message_id,
                        edit_message_text="ℹ️ Plattformauswahl abgelaufen. Bitte Aktion erneut starten.",
                        edit_reply_markup=None,
                    ),
                )
            self._platform_mode_store.set_mode(
                chat_id=request_context.source_chat_id,
                user_id=request_context.source_user_id,
                platform=platform,
            )
            command_body = f"{platform} {pending.command_body}".strip()
            continuation_context = RequestContext(
                request_id=request_context.request_id + f":platform_continue:{pending.command_name}",
                project_key=request_context.project_key,
                source_type=request_context.source_type,
                source_user_id=request_context.source_user_id,
                source_chat_id=request_context.source_chat_id,
                source_message_id=request_context.source_message_id,
                command_name=pending.command_name,
                command_body=command_body,
                request_text=request_context.request_text,
                reply_to_message_id=request_context.reply_to_message_id,
            )
            return self._build_executed_result(
                entry_handoff=entry_handoff,
                request_context=continuation_context,
                command_name=pending.command_name,
                command_body=command_body,
                title=f"{pending.command_name} request",
                callback_answer_text=_PLATFORM_LABELS.get(platform, platform),
                edit_message_id=entry_handoff.request.message_id,
                edit_message_text=f"✅ Plattform gewählt: {_PLATFORM_LABELS.get(platform, platform)}",
                edit_reply_markup=None,
                response_reply_to_message_id=None,
            )
        if action == "set" and platform:
            self._platform_mode_store.set_mode(
                chat_id=request_context.source_chat_id,
                user_id=request_context.source_user_id,
                platform=platform,
            )
            label = _PLATFORM_LABELS.get(platform, platform)
            answer_text = f"Modus: {label}"
            edit_text = f"✅ Plattform-Modus gesetzt: {label}\n\nAlle Befehle nutzen jetzt {label}."
        elif action == "clear":
            self._pending_platform_inputs.pop(pending_key, None)
            self._platform_mode_store.clear_mode(
                chat_id=request_context.source_chat_id,
                user_id=request_context.source_user_id,
            )
            answer_text = "Modus aufgehoben"
            edit_text = "✖️ Plattform-Modus aufgehoben.\n\nBitte Plattform beim nächsten Befehl angeben."
        else:
            answer_text = "Unbekannte Aktion"
            edit_text = "ℹ️ Unbekannte Modus-Aktion."

        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="platform_mode_callback",
            was_executed=False,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=entry_handoff.project_context.project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name="platform_mode_callback",
                command_body=callback_data,
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="platform_mode_callback",
                message_text="",
                execution_summary={},
                callback_query_id=entry_handoff.response_shell.callback_query_id,
                callback_answer_text=answer_text,
                send_response=False,
                edit_message_id=entry_handoff.request.message_id,
                edit_message_text=edit_text,
                edit_reply_markup=None,
            ),
        )

    def _build_platform_select_prompt(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
        command_name: str,
        command_body: str = "",
    ) -> RequestFlowResult:
        self._pending_platform_inputs[self._build_text_action_key(request_context)] = PendingPlatformSelection(
            command_name=command_name,
            command_body=command_body,
            user_id=request_context.source_user_id,
            chat_id=request_context.source_chat_id,
        )
        message_text = (
            f"Fuer /{command_name} bitte Plattform waehlen oder einmalig z. B. \"TikTok /{command_name}\" schreiben.\n\n"
            "Plattform als Modus setzen:"
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="not_a_command",
            was_executed=False,
            execution_result=None,
            formatter_payload=FormatterPayload(
                project_key=entry_handoff.project_context.project_key,
                project_display_name=entry_handoff.project_context.display_name,
                command_name=command_name,
                command_body="",
                response_chat_id=entry_handoff.response_shell.chat_id,
                response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                decision="not_a_command",
                message_text=message_text,
                execution_summary={},
                send_response=True,
                response_reply_markup=self._build_platform_select_markup(callback_action="continue"),
            ),
        )

    def _resolve_plan_state(
        self, project_key: str
    ) -> tuple[str, str, str | None, str | None, int]:
        """Resolve real daily plan state using PostingRecommender.

        Returns:
            (plan_text, plan_type, candidate_record_id, platform, candidate_count)
            where plan_type is "post", "draft", or "skip".
        """
        if self._recommender is None:
            return _SKIP_PLAN_TEXT, "skip", None, None, 0
        recommendation = self._recommender.recommend(project_key=project_key)
        if recommendation is not None:
            text = _build_post_plan_text(recommendation.candidate, recommendation.candidate_count)
            return (
                text,
                "post",
                recommendation.candidate.record_id,
                recommendation.candidate.platform,
                recommendation.candidate_count,
            )
        draft_count = self._recommender.eligible_draft_count(project_key=project_key)
        if draft_count > 0:
            return _build_draft_plan_text(draft_count), "draft", None, None, draft_count
        return _SKIP_PLAN_TEXT, "skip", None, None, 0

    def _build_platform_plan_snapshots(
        self,
        *,
        project_key: str,
        date: str,
    ) -> tuple[TodayPlanSnapshot, ...]:
        recommendation = self._recommender.recommend(project_key=project_key) if self._recommender else None
        recommended_candidate = recommendation.candidate if recommendation is not None else None
        candidate_count = recommendation.candidate_count if recommendation is not None else 0

        snapshots_by_platform: dict[str, TodayPlanSnapshot] = {}
        if self._daily_plan_service is not None:
            try:
                stored = self._daily_plan_service.list_today_plans(
                    project_key=project_key,
                    date=date,
                )
                snapshots_by_platform = {
                    snapshot.platform or "": snapshot
                    for snapshot in stored
                    if snapshot.platform
                }
            except Exception as exc:
                _log.warning("daily_plan_service.list_today_plans failed | error=%s", exc)

        snapshots: list[TodayPlanSnapshot] = []
        for platform in _PLAN_PLATFORM_DEFAULTS:
            existing = snapshots_by_platform.get(platform)
            if existing is not None:
                snapshots.append(existing)
                continue

            defaults = _PLAN_PLATFORM_DEFAULTS.get(platform, {})
            plan_type = str(defaults.get("plan_type") or "post")
            platform_candidate_record_id: str | None = None
            platform_candidate_count = int(defaults.get("candidate_count") or 0)
            if recommended_candidate is not None and getattr(recommended_candidate, "platform", "") == platform:
                plan_type = "post"
                platform_candidate_record_id = str(getattr(recommended_candidate, "record_id", "") or "").strip() or None
                platform_candidate_count = candidate_count

            record_id = ""
            if self._daily_plan_service is not None:
                try:
                    record_id = self._daily_plan_service.upsert_plan(
                        project_key=project_key,
                        date=date,
                        platform=platform,
                        plan_type=plan_type,
                        candidate_record_id=platform_candidate_record_id,
                        candidate_count=platform_candidate_count,
                    )
                except Exception as exc:
                    _log.warning("daily_plan_service.upsert_plan failed | platform=%s error=%s", platform, exc)

            snapshots.append(
                TodayPlanSnapshot(
                    record_id=record_id,
                    decision="pending",
                    plan_type=plan_type,
                    platform=platform,
                    candidate_count=platform_candidate_count,
                    candidate_record_id=platform_candidate_record_id,
                )
            )

        return tuple(snapshots)

    def _build_plan_demo_payload(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        snapshots: tuple[TodayPlanSnapshot, ...],
    ) -> FormatterPayload:
        primary = snapshots[0]
        additional_messages = tuple(
            AdditionalFormatterMessage(
                text=_build_platform_plan_text(snapshot),
                reply_markup=_build_plan_demo_reply_markup(snapshot.record_id),
            )
            for snapshot in snapshots[1:]
        )
        return FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name="plan_demo",
            command_body="",
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="plan_demo",
            message_text=_build_platform_plan_text(primary),
            execution_summary={},
            response_reply_markup=_build_plan_demo_reply_markup(primary.record_id),
            callback_query_id=entry_handoff.response_shell.callback_query_id,
            additional_messages=additional_messages,
        )

    def _build_plan_demo_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
    ) -> RequestFlowResult:
        project_key = entry_handoff.project_context.project_key
        today = datetime.date.today().isoformat()
        snapshots = self._build_platform_plan_snapshots(
            project_key=project_key,
            date=today,
        )
        payload = self._build_plan_demo_payload(
            entry_handoff=entry_handoff,
            snapshots=snapshots,
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="plan_demo",
            was_executed=False,
            execution_result=None,
            formatter_payload=payload,
        )

    def _build_plan_demo_callback_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
    ) -> RequestFlowResult:
        callback_data = entry_handoff.request.callback_data
        # Format: "plan_demo:<action>[:<record_id>]"
        parts = callback_data.split(":")
        action = parts[1].strip() if len(parts) > 1 else ""
        record_id = parts[2].strip() if len(parts) > 2 else ""

        label = _PLAN_DEMO_LABELS.get(action, action or "Unbekannt")
        _log.info(
            "plan_demo callback received | callback_data=%s user_id=%s chat_id=%s",
            callback_data,
            entry_handoff.request.user_id,
            entry_handoff.request.chat_id,
        )
        snapshot = TodayPlanSnapshot(record_id=record_id, decision="pending")
        if record_id and self._daily_plan_service is not None:
            try:
                project_key = entry_handoff.project_context.project_key
                if action == "upload_airtable":
                    snapshot = self._daily_plan_service.get_plan_record(
                        project_key=project_key,
                        record_id=record_id,
                    )
                    if self._daily_plan_upload_service is not None:
                        upload_after_confirm = snapshot.platform == "youtube_short"
                        default_posted_at_local = self._daily_plan_upload_service.build_default_posted_at_local(
                            project_key=project_key,
                            platform=snapshot.platform or "",
                            date=datetime.date.today().isoformat(),
                        )
                        if not upload_after_confirm:
                            upload_result = self._daily_plan_upload_service.upload_plan_snapshot(
                                project_key=project_key,
                                snapshot=snapshot,
                                date=datetime.date.today().isoformat(),
                            )
                            snapshot = upload_result.updated_snapshot
                            default_posted_at_local = upload_result.default_posted_at_local
                            if self._plan_reminder_store is not None and snapshot.platform_record_id:
                                self._schedule_analytics_3d_reminder(
                                    snapshot=snapshot,
                                    chat_id=int(request_context.source_chat_id or "0"),
                                )
                        pending_key = self._build_text_action_key(request_context)
                        self._pending_posted_at_inputs[pending_key] = PendingPostedAtCapture(
                            record_id=record_id,
                            default_posted_at_local=default_posted_at_local,
                            user_id=request_context.source_user_id,
                            chat_id=request_context.source_chat_id,
                            upload_after_confirm=upload_after_confirm,
                        )
                        platform_label = _PLATFORM_LABELS.get(snapshot.platform or "", snapshot.platform or "Plattform")
                        payload = FormatterPayload(
                            project_key=project_key,
                            project_display_name=entry_handoff.project_context.display_name,
                            command_name="plan_demo_upload",
                            command_body=callback_data,
                            response_chat_id=entry_handoff.response_shell.chat_id,
                            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                            decision="plan_demo_upload",
                            message_text=self._build_posted_at_prompt(
                                platform_label,
                                default_posted_at_local,
                                upload_after_confirm=upload_after_confirm,
                            ),
                            execution_summary={},
                            response_reply_markup=self._build_posted_at_reply_markup(
                                record_id,
                                default_posted_at_local,
                            ),
                            callback_query_id=entry_handoff.response_shell.callback_query_id,
                            callback_answer_text=label,
                            send_response=True,
                            edit_message_id=(
                                entry_handoff.request.message_id if not upload_after_confirm else None
                            ),
                            edit_message_text=(
                                _build_platform_plan_text(snapshot) + "\nAirtable: hochgeladen"
                                if not upload_after_confirm
                                else None
                            ),
                            edit_reply_markup=(
                                _build_plan_demo_reply_markup(record_id) if not upload_after_confirm else None
                            ),
                        )
                        return RequestFlowResult(
                            entry_handoff=entry_handoff,
                            request_context=request_context,
                            decision="plan_demo_upload",
                            was_executed=False,
                            execution_result=None,
                            formatter_payload=payload,
                        )
                elif action == "posted_at_default":
                    pending_key = self._build_text_action_key(request_context)
                    pending = self._pending_posted_at_inputs.get(pending_key)
                    if pending is not None:
                        return self._finalize_posted_at_capture(
                            entry_handoff=entry_handoff,
                            request_context=request_context,
                            record_id=record_id,
                            posted_at_local=pending.default_posted_at_local,
                            callback_answer_text=label,
                            edit_message_id=entry_handoff.request.message_id,
                        )
                elif action == "replace_field_select":
                    return self._build_plan_replace_field_select_result(
                        entry_handoff=entry_handoff,
                        request_context=request_context,
                        record_id=record_id,
                    )
                elif action == "replace_field":
                    # Callback format: plan_demo:replace_field:{record_id}:{field_name}
                    field_name = parts[3].strip() if len(parts) > 3 else ""
                    field_label = _DAILY_PLAN_FIELD_LABELS.get(field_name, field_name)
                    pending_key = self._build_text_action_key(request_context)
                    self._pending_plan_field_replace[pending_key] = PendingPlanFieldReplace(
                        record_id=record_id,
                        field_name=field_name,
                        field_label=field_label,
                        user_id=request_context.source_user_id,
                        chat_id=request_context.source_chat_id,
                    )
                    return RequestFlowResult(
                        entry_handoff=entry_handoff,
                        request_context=request_context,
                        decision="plan_demo_callback",
                        was_executed=False,
                        execution_result=None,
                        formatter_payload=FormatterPayload(
                            project_key=project_key,
                            project_display_name=entry_handoff.project_context.display_name,
                            command_name="plan_demo_callback",
                            command_body=callback_data,
                            response_chat_id=entry_handoff.response_shell.chat_id,
                            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
                            decision="plan_demo_callback",
                            message_text=f"✏️ Womit willst du {field_label} ersetzen?\n\nSchreib den neuen Inhalt als Antwort.",
                            execution_summary={},
                            callback_query_id=entry_handoff.response_shell.callback_query_id,
                            callback_answer_text=f"Ersetzen: {field_label}",
                            send_response=True,
                        ),
                    )
                elif action == "auto_fill":
                    siblings = tuple(
                        snapshot
                        for snapshot in self._daily_plan_service.list_today_plans(
                            project_key=project_key,
                            date=datetime.date.today().isoformat(),
                        )
                        if snapshot.record_id != record_id
                    )
                    excluded_values: dict[str, str] = {}
                    if self._daily_plan_generation_service is not None:
                        excluded_values = self._daily_plan_generation_service.get_non_repetition_exclusions(
                            record_id=record_id,
                        )
                    snapshot = self._daily_plan_service.autofill_selection(
                        project_key=project_key,
                        record_id=record_id,
                        siblings=siblings,
                        excluded_values=excluded_values,
                    )
                    if self._daily_plan_generation_service is not None:
                        snapshot = self._daily_plan_generation_service.fill_missing_fields(
                            project_key=project_key,
                            snapshot=snapshot,
                            siblings=siblings,
                        )
                elif action == "clear_selection":
                    snapshot = self._daily_plan_service.clear_selection(
                        project_key=project_key,
                        record_id=record_id,
                    )
                elif action == "remind_15m":
                    snapshot = self._daily_plan_service.get_plan_record(
                        project_key=project_key,
                        record_id=record_id,
                    )
                    if self._plan_reminder_store is not None:
                        from operator_core.proactive.plan_reminder_store import PlanReminder
                        from operator_core.proactive.plan_reminder_service import _format_snapshot_text
                        fire_at = (
                            datetime.datetime.now(tz=datetime.timezone.utc)
                            + datetime.timedelta(minutes=15)
                        )
                        reminder = PlanReminder(
                            key=f"remind_15m:{record_id}",
                            fire_at=fire_at,
                            chat_id=int(request_context.source_chat_id or "0"),
                            platform=snapshot.platform or "",
                            record_id=record_id,
                            reminder_type="remind_15m",
                            context_text=_format_snapshot_text(snapshot),
                        )
                        self._plan_reminder_store.schedule(reminder)
                        _log.info(
                            "plan remind_15m scheduled | record_id=%s platform=%s fire_at=%s",
                            record_id,
                            snapshot.platform,
                            fire_at.isoformat(),
                        )
                else:
                    decision = _PLAN_DEMO_ACTION_TO_DECISION.get(action)
                    if decision is not None:
                        self._daily_plan_service.update_decision(
                            project_key=project_key,
                            record_id=record_id,
                            decision=decision,
                        )
                    refreshed_rows = self._daily_plan_service.list_today_plans(
                        project_key=project_key,
                        date=datetime.date.today().isoformat(),
                    )
                    snapshot = next(
                        (
                            row_snapshot
                            for row_snapshot in refreshed_rows
                            if row_snapshot.record_id == record_id
                        ),
                        TodayPlanSnapshot(record_id=record_id, decision=decision or "pending"),
                    )
            except Exception as exc:
                _log.warning("daily_plan callback mutation failed | action=%s error=%s", action, exc)

        plan_text = _build_platform_plan_text(snapshot)
        if action == "remind_15m" and self._plan_reminder_store is not None:
            plan_text = plan_text + "\n⏰ Erinnerung in 15 Min. gesetzt."

        payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name="plan_demo_callback",
            command_body=callback_data,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="plan_demo_callback",
            message_text="",
            execution_summary={},
            callback_query_id=entry_handoff.response_shell.callback_query_id,
            callback_answer_text=label,
            send_response=False,
            edit_message_id=entry_handoff.request.message_id,
            edit_message_text=plan_text,
            edit_reply_markup=_build_plan_demo_reply_markup(record_id),
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="plan_demo_callback",
            was_executed=False,
            execution_result=None,
            formatter_payload=payload,
        )

    @staticmethod
    def _build_menu_result(
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
    ) -> RequestFlowResult:
        payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name="menu",
            command_body="",
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="menu",
            message_text="⌨️ Menü wird aktualisiert.",
            execution_summary={},
            response_reply_markup={"remove_keyboard": True},
            callback_query_id=entry_handoff.response_shell.callback_query_id,
            additional_messages=(
                AdditionalFormatterMessage(
                    text="⌨️ Menü aktualisiert.",
                    reply_markup=PERSISTENT_MENU_REPLY_MARKUP,
                ),
                AdditionalFormatterMessage(
                    text=_MENU_TEXT,
                    reply_markup=MENU_OVERLAY_REPLY_MARKUP,
                ),
            ),
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="menu",
            was_executed=False,
            execution_result=None,
            formatter_payload=payload,
        )

    @staticmethod
    def _build_menu_selected_text(action_key: str) -> str:
        label = _MENU_LABELS.get(action_key, "Menü")
        if action_key == "plan":
            return (
                "📋 Tagesplan\n\n"
                "Bereich gewählt.\n"
                "Als Nächstes kannst du den Tagesplan öffnen."
            )
        if action_key == "vollauto":
            return (
                "📝 Voll Auto\n\n"
                "Bereich gewählt.\n"
                "Als Nächstes kannst du einen kompletten Vorschlag anstoßen."
            )
        if action_key == "status":
            return (
                "⏳ Status\n\n"
                "Bereich gewählt.\n"
                "Als Nächstes kannst du den aktuellen Stand prüfen."
            )
        return f"☰ Menü\n\nAusgewählt: {label}"

    def _build_menu_callback_result(
        self,
        *,
        entry_handoff: TelegramEntryHandoff,
        request_context: RequestContext,
    ) -> RequestFlowResult:
        callback_data = entry_handoff.request.callback_data
        callback_key = callback_data.split(":", 1)[1].strip() if ":" in callback_data else callback_data.strip()
        if callback_key == "draft":
            callback_key = "vollauto"
        label = _MENU_LABELS.get(callback_key, "Menü")

        if callback_key == "plan":
            return RequestFlowService._build_plan_demo_result(
                self,
                entry_handoff=entry_handoff,
                request_context=request_context,
            )
        if callback_key == "modus":
            return self._build_modus_result(
                entry_handoff=entry_handoff,
                request_context=request_context,
            )

        if callback_key in _MENU_COMMAND_ACTIONS:
            menu_request_context = RequestContext(
                request_id=request_context.request_id + f":menu:{callback_key}",
                project_key=request_context.project_key,
                source_type=request_context.source_type,
                source_user_id=request_context.source_user_id,
                source_chat_id=request_context.source_chat_id,
                source_message_id=request_context.source_message_id,
                command_name=callback_key,
                command_body="",
                request_text=request_context.request_text,
                reply_to_message_id=request_context.reply_to_message_id,
            )
            return self._build_executed_result(
                entry_handoff=entry_handoff,
                request_context=menu_request_context,
                command_name=callback_key,
                command_body="",
                title=f"{callback_key} request",
                callback_answer_text=label,
            )

        payload = FormatterPayload(
            project_key=entry_handoff.project_context.project_key,
            project_display_name=entry_handoff.project_context.display_name,
            command_name="menu_callback",
            command_body=callback_data,
            response_chat_id=entry_handoff.response_shell.chat_id,
            response_reply_to_message_id=entry_handoff.response_shell.reply_to_message_id,
            decision="menu_callback",
            message_text="",
            execution_summary={},
            callback_query_id=entry_handoff.response_shell.callback_query_id,
            callback_answer_text=label,
            send_response=False,
            edit_message_id=entry_handoff.request.message_id,
            edit_message_text=RequestFlowService._build_menu_selected_text(callback_key),
            edit_reply_markup=None,
        )
        return RequestFlowResult(
            entry_handoff=entry_handoff,
            request_context=request_context,
            decision="menu_callback",
            was_executed=False,
            execution_result=None,
            formatter_payload=payload,
        )
