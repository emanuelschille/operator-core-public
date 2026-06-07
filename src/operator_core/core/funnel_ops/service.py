from __future__ import annotations

from typing import TYPE_CHECKING

from operator_core.core.knowledge_ops.doc_reader import (
    extract_section,
    first_sentences,
    list_items,
    trim,
)
from operator_core.core.funnel_ops.models import (
    FunnelOpResult,
    SUPPORTED_FUNNEL_ACTIONS,
)

if TYPE_CHECKING:
    from operator_core.projects.docs import ProjectDocsLoader


class UnsupportedFunnelActionError(ValueError):
    """Raised when a funnel action is not supported by funnel_ops."""


class FunnelOpsService:
    lane_name = "funnel_ops"

    def __init__(self, *, docs_loader: "ProjectDocsLoader | None" = None) -> None:
        self.docs_loader = docs_loader

    def supports(self, action_type: str) -> bool:
        return action_type.strip().lower() in SUPPORTED_FUNNEL_ACTIONS

    def handle(
        self,
        *,
        project_key: str,
        action_type: str,
        command_body: str,
    ) -> FunnelOpResult:
        normalized_action = action_type.strip().lower()
        normalized_body = self._normalize(command_body)

        if not self.supports(normalized_action):
            raise UnsupportedFunnelActionError(
                f"unsupported funnel action: {action_type}"
            )

        if self.docs_loader is not None:
            return self._handle_with_docs(
                project_key=project_key,
                action_type=normalized_action,
                command_body=normalized_body,
            )

        return self._handle_stub(
            project_key=project_key,
            action_type=normalized_action,
            command_body=normalized_body,
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
    ) -> FunnelOpResult:
        if action_type == "page_brief":
            return self._read_page_brief(project_key, command_body)
        if action_type == "funnel_direction":
            return self._read_funnel_direction(project_key, command_body)
        if action_type == "routing_hint":
            return self._read_routing_hint(project_key, command_body)
        if action_type == "page_structure":
            return self._read_page_structure(project_key, command_body)
        return self._read_offer_path(project_key, command_body)

    def _read_page_brief(self, project_key: str, command_body: str) -> FunnelOpResult:
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]

        funnel_text = extract_section(mono_doc.content, "Funnel Readiness Rule") or ""
        funnel_summary = "Funnel-Bereitschaft: " + trim(first_sentences(funnel_text, 2)) if funnel_text else "Funnel-Bereitschaft: keine Doc-Quelle"

        phase_text = extract_section(state_doc.content, "Current Phase") or ""
        phase_summary = "Phase: " + trim(first_sentences(phase_text, 1)) if phase_text else "Phase: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="page_brief",
            command_body=command_body,
            title="Page brief",
            summary="Page-Brief-Kontext geladen.",
            items=(
                funnel_summary,
                phase_summary,
                f"Fokus: {self._display_body(command_body)}",
            ),
        )

    def _read_funnel_direction(self, project_key: str, command_body: str) -> FunnelOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]

        pillars_text = extract_section(content_doc.content, "Active Content Pillars") or ""
        pillars = list_items(pillars_text, max_items=3)
        pillars_summary = "Content-Säulen: " + " | ".join(pillars) if pillars else "Content-Säulen: keine Doc-Quelle"

        cta_text = extract_section(mono_doc.content, "CTA Strength Rule") or ""
        cta_summary = "CTA-Regel: " + trim(first_sentences(cta_text, 1)) if cta_text else "CTA-Regel: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="funnel_direction",
            command_body=command_body,
            title="Funnel direction",
            summary="Funnel-Richtung geladen.",
            items=(
                pillars_summary,
                cta_summary,
                f"Fokus: {self._display_body(command_body)}",
            ),
        )

    def _read_routing_hint(self, project_key: str, command_body: str) -> FunnelOpResult:
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]
        content = mono_doc.content

        maturity_text = extract_section(content, "Monetization Maturity Model") or ""
        maturity_stages = list_items(maturity_text, max_items=5)
        maturity_summary = "Reife-Modell: " + " → ".join(maturity_stages) if maturity_stages else "Reife-Modell: keine Doc-Quelle"

        cta_text = extract_section(content, "CTA Strength Rule") or ""
        cta_summary = "CTA-Stärke: " + trim(first_sentences(cta_text, 1)) if cta_text else "CTA-Stärke: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="routing_hint",
            command_body=command_body,
            title="Routing hint",
            summary="Routing-Kontext geladen.",
            items=(
                maturity_summary,
                cta_summary,
                f"Fokus: {self._display_body(command_body)}",
            ),
        )

    def _read_page_structure(self, project_key: str, command_body: str) -> FunnelOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]

        posting_text = extract_section(content_doc.content, "Posting Readiness Rule") or ""
        posting_items = list_items(posting_text, max_items=3)
        posting_summary = "Posting-Bereitschaft: " + " | ".join(posting_items) if posting_items else trim(first_sentences(posting_text, 1)) if posting_text else "Posting-Bereitschaft: keine Doc-Quelle"

        mapping_text = extract_section(mono_doc.content, "Offer Mapping Quality Rule") or ""
        mapping_summary = "Mapping-Qualität: " + trim(first_sentences(mapping_text, 1)) if mapping_text else "Mapping-Qualität: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="page_structure",
            command_body=command_body,
            title="Page structure",
            summary="Page-Struktur-Kontext geladen.",
            items=(
                posting_summary,
                mapping_summary,
                f"Fokus: {self._display_body(command_body)}",
            ),
        )

    def _read_offer_path(self, project_key: str, command_body: str) -> FunnelOpResult:
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]
        content = mono_doc.content

        funnel_text = extract_section(content, "Funnel Readiness Rule") or ""
        funnel_summary = "Funnel-Bereitschaft: " + trim(first_sentences(funnel_text, 2)) if funnel_text else "Funnel-Bereitschaft: keine Doc-Quelle"

        avoid_text = extract_section(content, "What Monetization Should Avoid") or ""
        avoid_items = list_items(avoid_text, max_items=2)
        avoid_summary = "Vermeiden: " + " | ".join(avoid_items) if avoid_items else trim(first_sentences(avoid_text, 1)) if avoid_text else "Vermeiden: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="offer_path",
            command_body=command_body,
            title="Offer path",
            summary="Angebotspfad-Kontext geladen.",
            items=(
                funnel_summary,
                avoid_summary,
                f"Fokus: {self._display_body(command_body)}",
            ),
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
    ) -> FunnelOpResult:
        if action_type == "page_brief":
            return self._build_result(
                project_key=project_key,
                action_type="page_brief",
                command_body=command_body,
                title="Page brief",
                summary="Page-Brief-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Fokus: {self._display_body(command_body)}",
                    "Nächster Schritt: Ziel, Zielgruppe und CTA sauber bündeln.",
                ),
            )

        if action_type == "funnel_direction":
            return self._build_result(
                project_key=project_key,
                action_type="funnel_direction",
                command_body=command_body,
                title="Funnel direction",
                summary="Funnel-Direction-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Fokus: {self._display_body(command_body)}",
                    "Nächster Schritt: primäre Nutzerführung festlegen.",
                ),
            )

        if action_type == "routing_hint":
            return self._build_result(
                project_key=project_key,
                action_type="routing_hint",
                command_body=command_body,
                title="Routing hint",
                summary="Routing-Hint-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Fokus: {self._display_body(command_body)}",
                    "Nächster Schritt: weichen CTA-Pfad priorisieren.",
                ),
            )

        if action_type == "page_structure":
            return self._build_result(
                project_key=project_key,
                action_type="page_structure",
                command_body=command_body,
                title="Page structure",
                summary="Page-Structure-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Fokus: {self._display_body(command_body)}",
                    "Nächster Schritt: Hero, Proof, CTA-Reihenfolge skizzieren.",
                ),
            )

        return self._build_result(
            project_key=project_key,
            action_type="offer_path",
            command_body=command_body,
            title="Offer path",
            summary="Offer-Path-Stub vorbereitet.",
            items=(
                f"Projekt: {project_key}",
                f"Fokus: {self._display_body(command_body)}",
                "Nächster Schritt: Angebotsweg von Einstieg bis CTA klären.",
            ),
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
    ) -> FunnelOpResult:
        return FunnelOpResult(
            lane_name=self.lane_name,
            project_key=project_key,
            action_type=action_type,
            command_body=command_body,
            title=title,
            summary=summary,
            items=items,
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.strip().split())

    @staticmethod
    def _display_body(value: str) -> str:
        if value:
            return value
        return "kein Zusatzkontext"
