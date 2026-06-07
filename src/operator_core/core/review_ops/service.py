from __future__ import annotations

from typing import TYPE_CHECKING

from operator_core.core.knowledge_ops.doc_reader import (
    extract_section,
    first_sentences,
    list_items,
    trim,
)
from operator_core.core.review_ops.models import (
    ReviewOpResult,
    SUPPORTED_REVIEW_ACTIONS,
)

if TYPE_CHECKING:
    from operator_core.projects.docs import ProjectDocsLoader


class UnsupportedReviewActionError(ValueError):
    """Raised when a review action is not supported by review_ops."""


class ReviewOpsService:
    lane_name = "review_ops"

    def __init__(self, *, docs_loader: "ProjectDocsLoader | None" = None) -> None:
        self.docs_loader = docs_loader

    def supports(self, action_type: str) -> bool:
        return action_type.strip().lower() in SUPPORTED_REVIEW_ACTIONS

    def handle(
        self,
        *,
        project_key: str,
        action_type: str,
        command_body: str,
    ) -> ReviewOpResult:
        normalized_action = action_type.strip().lower()
        normalized_body = self._normalize(command_body)

        if not self.supports(normalized_action):
            raise UnsupportedReviewActionError(
                f"unsupported review action: {action_type}"
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
    ) -> ReviewOpResult:
        if action_type == "performance_review":
            return self._read_performance_review(project_key, command_body)
        if action_type == "learning_extract":
            return self._read_learning_extract(project_key, command_body)
        if action_type == "hypothesis":
            return self._read_hypothesis(project_key, command_body)
        if action_type == "next_step":
            return self._read_next_step(project_key, command_body)
        return self._read_pattern_check(project_key, command_body)

    def _read_performance_review(self, project_key: str, command_body: str) -> ReviewOpResult:
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        sem_doc = self.docs_loader.load(project_key, "operational_semantics")  # type: ignore[union-attr]

        priorities_text = extract_section(state_doc.content, "Current Operational Priorities") or ""
        priorities = list_items(priorities_text, max_items=3)
        priorities_summary = "Prioritäten: " + ", ".join(priorities) if priorities else "Prioritäten: keine Doc-Quelle"

        outcomes_text = extract_section(sem_doc.content, "Review Outcome Model") or ""
        outcome_items = list_items(outcomes_text, max_items=6)
        outcomes_summary = "Review-Outcomes: " + " | ".join(outcome_items) if outcome_items else "Review-Outcomes: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="performance_review",
            command_body=command_body,
            title="Performance review",
            summary="Review-Kontext geladen.",
            items=(
                priorities_summary,
                outcomes_summary,
                f"Fokus: {self._display_body(command_body)}",
            ),
        )

    def _read_learning_extract(self, project_key: str, command_body: str) -> ReviewOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        sem_doc = self.docs_loader.load(project_key, "operational_semantics")  # type: ignore[union-attr]

        trigger_text = extract_section(content_doc.content, "Review Trigger Rule") or ""
        trigger_summary = "Review-Trigger: " + trim(first_sentences(trigger_text, 1)) if trigger_text else "Review-Trigger: keine Doc-Quelle"

        lifecycle_text = extract_section(sem_doc.content, "Content Lifecycle") or ""
        lifecycle_stages = list_items(lifecycle_text, max_items=10)
        learned_present = any("learned" in s for s in lifecycle_stages)
        lifecycle_note = "Lifecycle-Stage 'learned': Erkenntnisse aus Review strukturiert festhalten" if learned_present else "Lifecycle: Content Lifecycle geladen"

        return self._build_result(
            project_key=project_key,
            action_type="learning_extract",
            command_body=command_body,
            title="Learning extract",
            summary="Learning-Kontext geladen.",
            items=(
                trigger_summary,
                lifecycle_note,
                f"Fokus: {self._display_body(command_body)}",
            ),
        )

    def _read_hypothesis(self, project_key: str, command_body: str) -> ReviewOpResult:
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        content = state_doc.content

        phase_text = extract_section(content, "Current Phase") or ""
        phase_summary = "Phase: " + trim(first_sentences(phase_text, 1)) if phase_text else "Phase: keine Doc-Quelle"

        direction_text = extract_section(content, "Active Content Direction") or ""
        direction_items = list_items(direction_text, max_items=2)
        direction_summary = "Richtung: " + ", ".join(direction_items) if direction_items else trim(first_sentences(direction_text, 1)) if direction_text else "Richtung: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="hypothesis",
            command_body=command_body,
            title="Working hypothesis",
            summary="Hypothesen-Kontext geladen.",
            items=(
                phase_summary,
                direction_summary,
                f"Fokus: {self._display_body(command_body)}",
            ),
        )

    def _read_next_step(self, project_key: str, command_body: str) -> ReviewOpResult:
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        content = state_doc.content

        priorities_text = extract_section(content, "Current Operational Priorities") or ""
        priorities = list_items(priorities_text, max_items=3)
        priorities_summary = "Prioritäten: " + ", ".join(priorities) if priorities else "Prioritäten: keine Doc-Quelle"

        workflows_text = extract_section(content, "Current High-Priority Workflows") or ""
        workflow_items = list_items(workflows_text, max_items=2)
        workflows_summary = "Workflows: " + " | ".join(workflow_items) if workflow_items else trim(first_sentences(workflows_text, 1)) if workflows_text else "Workflows: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="next_step",
            command_body=command_body,
            title="Next step",
            summary="Nächster-Schritt-Kontext geladen.",
            items=(
                priorities_summary,
                workflows_summary,
                f"Fokus: {self._display_body(command_body)}",
            ),
        )

    def _read_pattern_check(self, project_key: str, command_body: str) -> ReviewOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        content = content_doc.content

        repeat_text = extract_section(content, "Repeatability Rule") or ""
        repeat_summary = "Wiederholbarkeit: " + trim(first_sentences(repeat_text, 1)) if repeat_text else "Wiederholbarkeit: keine Doc-Quelle"

        reuse_text = extract_section(content, "Reusability Rule") or ""
        reuse_summary = "Wiederverwendbarkeit: " + trim(first_sentences(reuse_text, 1)) if reuse_text else "Wiederverwendbarkeit: nicht geladen"

        return self._build_result(
            project_key=project_key,
            action_type="pattern_check",
            command_body=command_body,
            title="Pattern check",
            summary="Pattern-Check-Kontext geladen.",
            items=(
                repeat_summary,
                reuse_summary,
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
    ) -> ReviewOpResult:
        if action_type == "performance_review":
            return self._build_result(
                project_key=project_key,
                action_type="performance_review",
                command_body=command_body,
                title="Performance review",
                summary="Performance-Review-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Fokus: {self._display_body(command_body)}",
                    "Nächster Schritt: wichtigste Beobachtung zusammenfassen.",
                ),
            )

        if action_type == "learning_extract":
            return self._build_result(
                project_key=project_key,
                action_type="learning_extract",
                command_body=command_body,
                title="Learning extract",
                summary="Learning-Extract-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Fokus: {self._display_body(command_body)}",
                    "Nächster Schritt: umsetzbares Learning festhalten.",
                ),
            )

        if action_type == "hypothesis":
            return self._build_result(
                project_key=project_key,
                action_type="hypothesis",
                command_body=command_body,
                title="Working hypothesis",
                summary="Hypothesis-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Fokus: {self._display_body(command_body)}",
                    "Nächster Schritt: prüfbare Hypothese formulieren.",
                ),
            )

        if action_type == "next_step":
            return self._build_result(
                project_key=project_key,
                action_type="next_step",
                command_body=command_body,
                title="Next step",
                summary="Next-Step-Stub vorbereitet.",
                items=(
                    f"Projekt: {project_key}",
                    f"Fokus: {self._display_body(command_body)}",
                    "Nächster Schritt: konkrete Folgeaktion priorisieren.",
                ),
            )

        return self._build_result(
            project_key=project_key,
            action_type="pattern_check",
            command_body=command_body,
            title="Pattern check",
            summary="Pattern-Check-Stub vorbereitet.",
            items=(
                f"Projekt: {project_key}",
                f"Fokus: {self._display_body(command_body)}",
                "Nächster Schritt: wiederkehrendes Muster kurz prüfen.",
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
    ) -> ReviewOpResult:
        return ReviewOpResult(
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
