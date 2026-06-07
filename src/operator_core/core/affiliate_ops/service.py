from __future__ import annotations

from typing import TYPE_CHECKING

from operator_core.core.knowledge_ops.doc_reader import (
    extract_section,
    first_sentences,
    list_items,
    trim,
)
from operator_core.core.affiliate_ops.models import (
    AffiliateOpResult,
    SUPPORTED_AFFILIATE_ACTIONS,
)

if TYPE_CHECKING:
    from operator_core.projects.docs import ProjectDocsLoader


class UnsupportedAffiliateActionError(ValueError):
    """Raised when an affiliate action is not supported by affiliate_ops."""


class AffiliateOpsService:
    lane_name = "affiliate_ops"

    def __init__(self, *, docs_loader: "ProjectDocsLoader | None" = None) -> None:
        self.docs_loader = docs_loader

    def supports(self, action_type: str) -> bool:
        return action_type.strip().lower() in SUPPORTED_AFFILIATE_ACTIONS

    def handle(
        self,
        *,
        project_key: str,
        action_type: str,
        command_body: str,
    ) -> AffiliateOpResult:
        normalized_action = action_type.strip().lower()
        normalized_body = self._normalize(command_body)

        if not self.supports(normalized_action):
            raise UnsupportedAffiliateActionError(
                f"unsupported affiliate action: {action_type}"
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
    ) -> AffiliateOpResult:
        if action_type == "offer_match":
            return self._read_offer_match(project_key, command_body)
        if action_type == "product_fit":
            return self._read_product_fit(project_key, command_body)
        if action_type == "cta_direction":
            return self._read_cta_direction(project_key, command_body)
        if action_type == "monetization_fit":
            return self._read_monetization_fit(project_key, command_body)
        return self._read_recommendation_ready(project_key, command_body)

    def _read_offer_match(self, project_key: str, command_body: str) -> AffiliateOpResult:
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]
        content = mono_doc.content

        fit_text = extract_section(content, "Offer Fit Rule") or ""
        fit_summary = "Fit-Regel: " + trim(first_sentences(fit_text, 2)) if fit_text else "Fit-Regel: keine Doc-Quelle"

        foundations_text = extract_section(content, "Allowed Monetization Foundations") or ""
        foundations = list_items(foundations_text, max_items=3)
        foundations_summary = "Erlaubte Grundlagen: " + " | ".join(foundations) if foundations else "Erlaubte Grundlagen: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="offer_match",
            command_body=command_body,
            title="Offer match",
            summary="Monetarisierungsregeln geladen.",
            items=(
                fit_summary,
                foundations_summary,
                f"Kontext: {self._display_body(command_body)}",
            ),
        )

    def _read_product_fit(self, project_key: str, command_body: str) -> AffiliateOpResult:
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]
        content = mono_doc.content

        category_text = extract_section(content, "Offer Category Rule") or ""
        category_items = list_items(category_text, max_items=3)
        category_summary = "Offer-Kategorien: " + " | ".join(category_items) if category_items else trim(first_sentences(category_text, 1)) if category_text else "Offer-Kategorien: keine Doc-Quelle"

        trust_text = extract_section(content, "Trust-First Principle") or ""
        trust_summary = "Trust-First: " + trim(first_sentences(trust_text, 1)) if trust_text else "Trust-First: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="product_fit",
            command_body=command_body,
            title="Product fit",
            summary="Produkt-Fit-Kontext geladen.",
            items=(
                category_summary,
                trust_summary,
                f"Kontext: {self._display_body(command_body)}",
            ),
        )

    def _read_cta_direction(self, project_key: str, command_body: str) -> AffiliateOpResult:
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]
        content = mono_doc.content

        cta_text = extract_section(content, "CTA Strength Rule") or ""
        cta_summary = "CTA-Stärke-Regel: " + trim(first_sentences(cta_text, 1)) if cta_text else "CTA-Stärke-Regel: keine Doc-Quelle"

        maturity_text = extract_section(content, "Monetization Maturity Model") or ""
        maturity_stages = list_items(maturity_text, max_items=5)
        maturity_summary = "Reife-Stufen: " + " → ".join(maturity_stages) if maturity_stages else "Reife-Stufen: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="cta_direction",
            command_body=command_body,
            title="CTA direction",
            summary="CTA-Richtung geladen.",
            items=(
                cta_summary,
                maturity_summary,
                f"Kontext: {self._display_body(command_body)}",
            ),
        )

    def _read_monetization_fit(self, project_key: str, command_body: str) -> AffiliateOpResult:
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]

        maturity_text = extract_section(mono_doc.content, "Monetization Maturity Model") or ""
        maturity_stages = list_items(maturity_text, max_items=5)
        maturity_summary = "Reife-Modell: " + " → ".join(maturity_stages) if maturity_stages else "Reife-Modell: keine Doc-Quelle"

        direction_text = extract_section(state_doc.content, "Active Monetization Direction") or ""
        direction_items = list_items(direction_text, max_items=2)
        direction_summary = "Aktive Richtung: " + ", ".join(direction_items) if direction_items else trim(first_sentences(direction_text, 1)) if direction_text else "Aktive Richtung: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="monetization_fit",
            command_body=command_body,
            title="Monetization fit",
            summary="Monetarisierungs-Fit-Kontext geladen.",
            items=(
                maturity_summary,
                direction_summary,
                f"Kontext: {self._display_body(command_body)}",
            ),
        )

    def _read_recommendation_ready(self, project_key: str, command_body: str) -> AffiliateOpResult:
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]
        content = mono_doc.content

        stage_text = extract_section(content, "Monetization Stage Rule") or ""
        stage_summary = "Stufen-Regel: " + trim(first_sentences(stage_text, 1)) if stage_text else "Stufen-Regel: keine Doc-Quelle"

        downgrade_text = extract_section(content, "Downgrade Rule") or ""
        downgrade_summary = "Herabstufung: " + trim(first_sentences(downgrade_text, 1)) if downgrade_text else "Herabstufung: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="recommendation_ready",
            command_body=command_body,
            title="Recommendation readiness",
            summary="Empfehlungsbereitschaft geladen.",
            items=(
                stage_summary,
                downgrade_summary,
                f"Kontext: {self._display_body(command_body)}",
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
    ) -> AffiliateOpResult:
        if action_type == "offer_match":
            return self._build_result(
                project_key=project_key,
                action_type="offer_match",
                command_body=command_body,
                title="Offer match",
                summary="Offer-Match-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Kontext: {self._display_body(command_body)}",
                    "Leitlinie: trust first, soft CTA first.",
                ),
            )

        if action_type == "product_fit":
            return self._build_result(
                project_key=project_key,
                action_type="product_fit",
                command_body=command_body,
                title="Product fit",
                summary="Product-Fit-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Kontext: {self._display_body(command_body)}",
                    "Leitlinie: nur passende Produktempfehlung vorbereiten.",
                ),
            )

        if action_type == "cta_direction":
            return self._build_result(
                project_key=project_key,
                action_type="cta_direction",
                command_body=command_body,
                title="CTA direction",
                summary="CTA-Direction-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Kontext: {self._display_body(command_body)}",
                    "Leitlinie: softer CTA vor hartem Verkauf.",
                ),
            )

        if action_type == "monetization_fit":
            return self._build_result(
                project_key=project_key,
                action_type="monetization_fit",
                command_body=command_body,
                title="Monetization fit",
                summary="Monetization-Fit-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Kontext: {self._display_body(command_body)}",
                    "Leitlinie: Monetisierung früh vorbereiten, Druck niedrig halten.",
                ),
            )

        return self._build_result(
            project_key=project_key,
            action_type="recommendation_ready",
            command_body=command_body,
            title="Recommendation readiness",
            summary="Recommendation-Readiness-Stub vorbereitet.",
            items=(
                f"Projekt: {project_key}",
                f"Kontext: {self._display_body(command_body)}",
                "Leitlinie: Empfehlung erst bei ausreichendem Fit ausspielen.",
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
    ) -> AffiliateOpResult:
        return AffiliateOpResult(
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
