from __future__ import annotations

from operator_core.core.command_router import KNOWN_COMMANDS
from operator_core.core.menu_layouts import PERSISTENT_MENU_REPLY_MARKUP
from operator_core.core.request_flow.models import FormatterPayload, RequestFlowResult
from operator_core.core.response_formatter.models import (
    AdditionalFormattedResponse,
    FormattedResponse,
)


class ResponseFormatterService:
    _STATUS_LABELS: dict[str, str] = {
        "completed": "abgeschlossen",
        "failed": "fehlgeschlagen",
        "waiting_for_input": "wartet auf Rückmeldung",
        "unknown": "unbekannt",
    }

    def format_request_flow_result(self, result: RequestFlowResult) -> FormattedResponse:
        payload = result.formatter_payload

        if result.was_executed:
            text = self._format_executed(payload)
        elif payload.decision in {
            "menu",
            "menu_callback",
            "modus",
            "platform_mode_callback",
            "plan_demo",
            "plan_demo_callback",
            "plan_demo_upload",
            "plan_demo_posted_at",
            "plan_demo_posted_at_pending",
            "free_text_selection",
            "text_action_callback",
            "content_ops_callback",
        }:
            text = payload.message_text
        elif payload.decision == "unknown_command":
            text = self._format_unknown_command(payload)
        elif payload.decision == "not_a_command":
            text = self._format_non_command(payload)
        else:
            text = self._format_not_executed(payload)

        reply_markup = payload.response_reply_markup
        if reply_markup is None and result.was_executed:
            output_snapshot = payload.execution_summary.get("output_snapshot") or {}
            if output_snapshot.get("lane_name") == "content_ops":
                reply_markup = self.build_content_ops_reply_markup(output_snapshot)
        if reply_markup is None and payload.send_response and text:
            reply_markup = PERSISTENT_MENU_REPLY_MARKUP

        additional_responses = tuple(
            AdditionalFormattedResponse(
                text=message.text,
                reply_to_message_id=message.reply_to_message_id,
                reply_markup=message.reply_markup,
            )
            for message in payload.additional_messages
            if message.text
        )

        return FormattedResponse(
            decision=payload.decision,
            text=text,
            chat_id=payload.response_chat_id,
            reply_to_message_id=payload.response_reply_to_message_id,
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            callback_query_id=payload.callback_query_id,
            callback_answer_text=payload.callback_answer_text,
            send_response=payload.send_response,
            edit_message_id=payload.edit_message_id,
            edit_message_text=payload.edit_message_text,
            edit_reply_markup=payload.edit_reply_markup,
            additional_responses=additional_responses,
        )

    _CONTENT_OPS_HEADERS: dict[str, str] = {
        "idea": "💡 Idee",
        "serie": "🧩 Serie/Thema",
        "title": "🏷️ Title",
        "draft": "📝 Entwurf",
        "vollauto": "📝 Voll Auto",
        "hook": "🎣 Hook",
        "cta": "🪝 CTA",
        "caption": "💬 Caption",
        "followup": "🛠️ Follow-up",
        "variant": "🔄 Variante",
    }

    def _format_executed(self, payload: FormatterPayload) -> str:
        summary = payload.execution_summary
        job_status = str(summary.get("job_status") or "unknown")
        run_status = str(summary.get("run_status") or "unknown")
        job_status_label = self._STATUS_LABELS.get(job_status, job_status)
        run_status_label = self._STATUS_LABELS.get(run_status, run_status)
        result_summary = self._clean(summary.get("result_summary"))
        error_summary = self._clean(summary.get("error_summary"))
        job_id = self._clean(summary.get("job_id"))
        run_id = self._clean(summary.get("run_id"))
        output_snapshot = summary.get("output_snapshot") or {}
        lane_name = self._clean(output_snapshot.get("lane_name"))

        if error_summary or job_status == "failed" or run_status == "failed":
            lines = [
                "❌ Anfrage fehlgeschlagen",
                f"Projekt: {payload.project_display_name}",
                f"Befehl: {payload.command_name}",
                f"Status: {job_status_label}",
            ]
            if error_summary:
                lines.append(f"Fehler: {error_summary}")
            if job_id:
                lines.append(f"Job: {job_id}")
            if run_id:
                lines.append(f"Run: {run_id}")
            return "\n".join(lines)

        if job_status == "waiting_for_input":
            lines = [
                "🟡 Anfrage erfasst",
                f"Projekt: {payload.project_display_name}",
                f"Befehl: {payload.command_name}",
                f"Status: {job_status_label}",
            ]
            if result_summary:
                lines.append(f"Hinweis: {result_summary}")
            if job_id:
                lines.append(f"Job: {job_id}")
            return "\n".join(lines)

        if payload.command_name == "status" and output_snapshot.get("status_type") == "commercial_mix":
            return self._format_commercial_mix_status(payload, output_snapshot)

        if lane_name == "content_ops":
            return self._format_content_ops(output_snapshot, job_id)

        title = self._clean(output_snapshot.get("title"))
        items = output_snapshot.get("items") or []
        lines = [
            "✅ Anfrage verarbeitet",
            f"Projekt: {payload.project_display_name}",
            f"Befehl: {payload.command_name}",
            f"Status: {job_status_label}",
        ]
        if lane_name:
            lines.append(f"Bereich: {lane_name}")
        if title:
            lines.append(f"Typ: {title}")
        if result_summary:
            lines.append(f"Ergebnis: {result_summary}")
        for item in items[:3]:
            cleaned = self._clean(item)
            if cleaned:
                lines.append(f"• {cleaned}")
        if job_id:
            lines.append(f"Job: {job_id}")
        if run_id:
            lines.append(f"Lauf: {run_id}")
        return "\n".join(lines)

    def _format_commercial_mix_status(self, payload: FormatterPayload, snapshot: dict) -> str:
        mix = snapshot.get("commercial_mix") or {}
        window_days = int(snapshot.get("window_days") or 7)
        total = int(snapshot.get("total") or 0)
        drift_hint = self._clean(snapshot.get("drift_hint"))
        lines = [
            "📊 Status",
            "",
            f"Letzte {window_days} Tage: {total} Inhalte eingeordnet",
            "",
            f"• Vertrauensaufbau: {int(mix.get('trust_building') or 0)}",
            f"• Produktnah: {int(mix.get('product_near') or 0)}",
            f"• Empfehlungsbereit: {int(mix.get('recommendation_ready') or 0)}",
            f"• Direktes Angebot: {int(mix.get('direct_offer') or 0)}",
            f"• Nicht passend: {int(mix.get('off_thesis_or_monetization_waste') or 0)}",
        ]
        if drift_hint:
            lines.extend(("", "Hinweis:", drift_hint))
        return "\n".join(lines)

    @staticmethod
    def build_content_ops_reply_markup(snapshot: dict) -> dict | None:
        action_type = str(snapshot.get("action_type") or "").strip().lower()
        if action_type not in {"idea", "serie", "title", "vollauto", "hook", "cta", "caption", "followup"}:
            return None
        proposal_id = str(snapshot.get("proposal_id") or "").strip()
        if not proposal_id:
            return None
        if not (snapshot.get("openai_used") or snapshot.get("proposal_interactive")):
            return None
        if action_type == "idea":
            items = snapshot.get("items") or []
            if not any(str(item or "").strip() for item in items):
                if ResponseFormatterService._is_rejected_same_core_idea_fallback(snapshot):
                    return {
                        "inline_keyboard": [
                            [
                                {"text": "💡 Frischer", "callback_data": f"content_ops:idea_fresh:{proposal_id}"},
                                {"text": "🔁 Neuer Winkel", "callback_data": f"content_ops:idea_angle:{proposal_id}"},
                            ],
                            [
                                {"text": "🗑 Verwerfen", "callback_data": f"content_ops:dismiss:{proposal_id}"},
                            ],
                        ]
                    }
                return None
            return {
                "inline_keyboard": [
                    [
                        {"text": "📝 Aus Idee Entwurf erstellen", "callback_data": f"content_ops:idea_to_draft:{proposal_id}"},
                        {"text": "✖️ Verwerfen", "callback_data": f"content_ops:dismiss:{proposal_id}"},
                    ],
                    [
                        {"text": "✅ Gut", "callback_data": f"content_ops:accept:{proposal_id}"},
                        {"text": "❌ Nicht gut", "callback_data": f"content_ops:reject:{proposal_id}"},
                    ],
                ]
            }
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ In Tagesplan setzen", "callback_data": f"content_ops:apply:{proposal_id}"},
                    {"text": "✖️ Verwerfen", "callback_data": f"content_ops:dismiss:{proposal_id}"},
                ],
                [
                    {"text": "✍️ Umformulieren", "callback_data": f"content_ops:rewrite:{proposal_id}"},
                    {"text": "🔄 Ersetzen", "callback_data": f"content_ops:regenerate:{proposal_id}"},
                ]
            ]
        }

    @staticmethod
    def _is_rejected_same_core_idea_fallback(snapshot: dict) -> bool:
        summary = str(snapshot.get("summary") or "")
        return "gerade in fast diesem Kern verworfen" in summary

    # Display-only key translation: keeps internal item strings intact for Airtable parsing
    _ITEM_KEY_DE: dict[str, str] = {
        "Pillar": "Säule",
        "Angle": "Blickwinkel",
        "Body": "Text",
        "CTA-Richtung": "Aufruf",
        "Stärke-Check": "Einschätzung",
        "Bereit-Check": "Bereit",
    }

    def _format_content_ops(self, snapshot: dict, job_id: str) -> str:
        action_type = self._clean(snapshot.get("action_type"))
        header = self._CONTENT_OPS_HEADERS.get(action_type, f"✅ {action_type}" if action_type else "✅")
        items: list[str] = snapshot.get("items") or []
        summary = self._clean(snapshot.get("summary"))
        airtable_record_id = self._clean(snapshot.get("airtable_record_id"))
        model_name = self._clean(snapshot.get("model_name"))

        lines = [header, ""]
        for item in items:
            cleaned = self._clean(item)
            if cleaned:
                lines.append(f"• {self._de_label(cleaned)}")
        if len(lines) == 2:
            if summary:
                lines.append(f"• {summary}")
            else:
                lines.append("• Kein Vorschlag verfügbar.")

        trace_parts: list[str] = []
        if airtable_record_id:
            trace_parts.append(airtable_record_id)
        if job_id:
            trace_parts.append(f"…{job_id[-8:]}")
        if model_name:
            trace_parts.append(model_name)

        if trace_parts:
            lines.append("")
            lines.append(f"🗂 {' · '.join(trace_parts)}")

        return "\n".join(lines)

    def _de_label(self, item: str) -> str:
        """Translate English key prefix to German for display. Value is never modified."""
        if ": " not in item:
            return item
        key, _, value = item.partition(": ")
        return f"{self._ITEM_KEY_DE.get(key, key)}: {value}"

    def _format_unknown_command(self, payload: FormatterPayload) -> str:
        return "\n".join(
            [
                "⚠️ Unbekannter Befehl",
                f"Projekt: {payload.project_display_name}",
                f"Befehl: {payload.command_name}",
                "Nutze /menu oder den Button ☰ Menü.",
            ]
        )

    def _format_non_command(self, payload: FormatterPayload) -> str:
        if payload.message_text:
            return payload.message_text
        return "\n".join(
            [
                "ℹ️ Keine Aktion",
                f"Projekt: {payload.project_display_name}",
                "Nachricht wurde nicht als Befehl erkannt.",
                "Nutze /menu oder den Button ☰ Menü.",
            ]
        )

    def _format_not_executed(self, payload: FormatterPayload) -> str:
        lines = [
            "⏸️ Keine Änderung vorgenommen",
            f"Projekt: {payload.project_display_name}",
        ]
        if payload.command_name:
            lines.append(f"Befehl: {payload.command_name}")
        if payload.message_text:
            lines.append(f"Hinweis: {payload.message_text}")
        return "\n".join(lines)

    @staticmethod
    def _clean(value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()
