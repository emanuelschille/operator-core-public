from __future__ import annotations

from typing import TYPE_CHECKING

from operator_core.core.knowledge_ops.doc_reader import (
    extract_section,
    first_sentences,
    list_items,
    trim,
)
from operator_core.core.knowledge_ops.models import (
    KnowledgeOpResult,
    SUPPORTED_KNOWLEDGE_ACTIONS,
)

if TYPE_CHECKING:
    from operator_core.projects.docs import ProjectDocsLoader


class UnsupportedKnowledgeActionError(ValueError):
    """Raised when a knowledge action is not supported by knowledge_ops."""


class KnowledgeOpsService:
    lane_name = "knowledge_ops"

    def __init__(self, *, docs_loader: "ProjectDocsLoader | None" = None) -> None:
        self.docs_loader = docs_loader

    def supports(self, action_type: str) -> bool:
        return action_type.strip().lower() in SUPPORTED_KNOWLEDGE_ACTIONS

    def handle(
        self,
        *,
        project_key: str,
        action_type: str,
        command_body: str,
    ) -> KnowledgeOpResult:
        normalized_action = action_type.strip().lower()
        normalized_body = self._normalize(command_body)

        if not self.supports(normalized_action):
            raise UnsupportedKnowledgeActionError(
                f"unsupported knowledge action: {action_type}"
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
    ) -> KnowledgeOpResult:
        if action_type == "state":
            return self._read_state(project_key, command_body)
        if action_type == "rules":
            return self._read_rules(project_key, command_body)
        if action_type == "context":
            return self._read_context(project_key, command_body)
        if action_type == "assumptions":
            return self._read_assumptions(project_key, command_body)
        # decisions
        return self._read_decisions(project_key, command_body)

    def _read_state(self, project_key: str, command_body: str) -> KnowledgeOpResult:
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        content = state_doc.content

        phase_text = extract_section(content, "Current Phase") or ""
        audience_text = extract_section(content, "Active Audience Assumption") or ""
        priorities_text = extract_section(content, "Current Operational Priorities") or ""

        phase_summary = trim(first_sentences(phase_text, 2)) if phase_text else f"Projekt: {project_key}"
        audience_summary = trim(first_sentences(audience_text, 2)) if audience_text else ""
        priorities = list_items(priorities_text, max_items=3)
        priorities_summary = "Prioritäten: " + ", ".join(priorities) if priorities else ""

        items = tuple(
            item for item in (phase_summary, audience_summary, priorities_summary) if item
        ) or (f"Projekt: {project_key}",)

        return self._build_result(
            project_key=project_key,
            action_type="state",
            command_body=command_body,
            title="Project state",
            summary="Projektstand geladen.",
            items=items,
        )

    def _read_rules(self, project_key: str, command_body: str) -> KnowledgeOpResult:
        content_doc = self.docs_loader.load(project_key, "content_rules")  # type: ignore[union-attr]
        mono_doc = self.docs_loader.load(project_key, "monetization_rules")  # type: ignore[union-attr]

        pillars_text = extract_section(content_doc.content, "Active Content Pillars") or ""
        pillars = list_items(pillars_text, max_items=5)
        pillars_summary = "Content-Säulen: " + " | ".join(pillars) if pillars else ""

        tone_text = extract_section(content_doc.content, "Content Tone") or ""
        tone_summary = trim(first_sentences(tone_text, 1)) if tone_text else ""

        mono_text = extract_section(mono_doc.content, "Trust-First Principle") or ""
        mono_summary = trim(first_sentences(mono_text, 1)) if mono_text else ""

        items = tuple(
            item for item in (pillars_summary, tone_summary, mono_summary) if item
        ) or (f"Projekt: {project_key}",)

        return self._build_result(
            project_key=project_key,
            action_type="rules",
            command_body=command_body,
            title="Project rules",
            summary="Projektregeln geladen.",
            items=items,
        )

    def _read_context(self, project_key: str, command_body: str) -> KnowledgeOpResult:
        sem_doc = self.docs_loader.load(project_key, "operational_semantics")  # type: ignore[union-attr]
        content = sem_doc.content

        objects_text = extract_section(content, "Object Catalog") or ""
        # Sub-objects listed under ### headings
        object_names: list[str] = []
        for line in objects_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                name = stripped.lstrip("# ").strip()
                if name:
                    object_names.append(name)
        objects_summary = "Objekte: " + ", ".join(object_names) if object_names else ""

        lifecycle_text = extract_section(content, "Lifecycle states") or ""
        stages = list_items(lifecycle_text, max_items=10)
        stages_summary = "Lifecycle: " + " → ".join(stages[:5]) if stages else ""

        role_text = extract_section(content, "Julia role") or ""
        role_summary = trim(first_sentences(role_text, 1)) if role_text else ""

        items = tuple(
            item for item in (objects_summary, stages_summary, role_summary) if item
        ) or (f"Projekt: {project_key}",)

        return self._build_result(
            project_key=project_key,
            action_type="context",
            command_body=command_body,
            title="Working context",
            summary="Projektkontext geladen.",
            items=items,
        )

    def _read_assumptions(self, project_key: str, command_body: str) -> KnowledgeOpResult:
        # Assumptions extracted from project_state audience + direction sections
        state_doc = self.docs_loader.load(project_key, "project_state")  # type: ignore[union-attr]
        content = state_doc.content

        audience_text = extract_section(content, "Active Audience Assumption") or ""
        audience_summary = trim(first_sentences(audience_text, 2)) if audience_text else ""

        direction_text = extract_section(content, "Active Content Direction") or ""
        direction_items = list_items(direction_text, max_items=3)
        direction_summary = "Content-Richtung: " + ", ".join(direction_items) if direction_items else ""

        mono_text = extract_section(content, "Active Monetization Direction") or ""
        mono_items = list_items(mono_text, max_items=3)
        mono_summary = "Monetarisierung: " + ", ".join(mono_items) if mono_items else ""

        items = tuple(
            item for item in (audience_summary, direction_summary, mono_summary) if item
        ) or (f"Projekt: {project_key} — Annahmen aus project-state geladen.",)

        return self._build_result(
            project_key=project_key,
            action_type="assumptions",
            command_body=command_body,
            title="Active assumptions",
            summary="Aktive Annahmen aus project-state geladen.",
            items=items,
        )

    def _read_decisions(self, project_key: str, command_body: str) -> KnowledgeOpResult:
        # No dedicated decisions source yet — explicit controlled fallback
        return self._build_result(
            project_key=project_key,
            action_type="decisions",
            command_body=command_body,
            title="Decision log",
            summary="Keine dedizierte Entscheidungsquelle verknüpft.",
            items=(
                f"Projekt: {project_key}",
                "Entscheidungslog hat noch keine eigene Quelle im project_docs-Layer.",
                "Bestätigte Entscheidungen: docs/08-decisions-log.md (global), nicht projektspezifisch.",
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
    ) -> KnowledgeOpResult:
        display = self._display_body(command_body)

        stubs: dict[str, tuple[str, str, tuple[str, ...]]] = {
            "state": (
                "Project state",
                "Project-State-Stub vorbereitet.",
                (
                    f"Projekt: {project_key}",
                    f"Fokus: {display}",
                    "Nächster Schritt: echte State-Quelle andocken.",
                ),
            ),
            "rules": (
                "Project rules",
                "Rules-Stub vorbereitet.",
                (
                    f"Projekt: {project_key}",
                    f"Filter: {display}",
                    "Nächster Schritt: persistente Regelquelle anschließen.",
                ),
            ),
            "assumptions": (
                "Active assumptions",
                "Assumptions-Stub vorbereitet.",
                (
                    f"Projekt: {project_key}",
                    f"Fokus: {display}",
                    "Nächster Schritt: aktive Arbeitsannahmen strukturieren.",
                ),
            ),
            "decisions": (
                "Decision log",
                "Decisions-Stub vorbereitet.",
                (
                    f"Projekt: {project_key}",
                    f"Fokus: {display}",
                    "Nächster Schritt: bestätigte Entscheidungen anbinden.",
                ),
            ),
            "context": (
                "Working context",
                "Context-Stub vorbereitet.",
                (
                    f"Projekt: {project_key}",
                    f"Fokus: {display}",
                    "Nächster Schritt: aktiven Kontext konsolidieren.",
                ),
            ),
        }

        title, summary, items = stubs.get(
            action_type,
            ("Knowledge action", "Aktion vorbereitet.", (f"Projekt: {project_key}",)),
        )
        return self._build_result(
            project_key=project_key,
            action_type=action_type,
            command_body=command_body,
            title=title,
            summary=summary,
            items=items,
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
    ) -> KnowledgeOpResult:
        return KnowledgeOpResult(
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
        return value if value else "kein Zusatzkontext"
