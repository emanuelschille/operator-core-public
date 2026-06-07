from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from operator_core.projects.registry import PROJECT_ROOT


ProjectDocType = Literal[
    "project_state",
    "content_rules",
    "monetization_rules",
    "operational_semantics",
]

_DOC_FILENAMES: dict[str, str] = {
    "project_state": "project-state.md",
    "content_rules": "content-rules.md",
    "monetization_rules": "monetization-rules.md",
    "operational_semantics": "operational-semantics.md",
}

ALL_DOC_TYPES: tuple[ProjectDocType, ...] = (
    "project_state",
    "content_rules",
    "monetization_rules",
    "operational_semantics",
)


class ProjectDocNotFoundError(FileNotFoundError):
    """Raised when a project doc file cannot be found on disk."""


class UnknownProjectDocTypeError(ValueError):
    """Raised when an unrecognised doc_type is requested."""


@dataclass(frozen=True)
class ProjectDoc:
    project_key: str
    doc_type: ProjectDocType
    content: str
    path: Path

    @property
    def is_empty(self) -> bool:
        return not self.content.strip()

    def excerpt(self, max_chars: int = 200) -> str:
        text = self.content.strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "…"


class ProjectDocsLoader:
    """Loads everydayengel (and future project) markdown docs from the projects/ directory."""

    def load(self, project_key: str, doc_type: ProjectDocType) -> ProjectDoc:
        if doc_type not in _DOC_FILENAMES:
            raise UnknownProjectDocTypeError(
                f"Unknown doc type '{doc_type}'. "
                f"Allowed: {', '.join(_DOC_FILENAMES)}"
            )

        filename = _DOC_FILENAMES[doc_type]
        path = PROJECT_ROOT / "projects" / project_key / filename

        if not path.exists():
            raise ProjectDocNotFoundError(
                f"Project doc not found: {path} "
                f"(project='{project_key}', doc_type='{doc_type}')"
            )

        content = path.read_text(encoding="utf-8")
        return ProjectDoc(
            project_key=project_key,
            doc_type=doc_type,
            content=content,
            path=path,
        )

    def load_all(self, project_key: str) -> dict[ProjectDocType, ProjectDoc]:
        return {
            doc_type: self.load(project_key, doc_type)
            for doc_type in ALL_DOC_TYPES
        }

    def available_doc_types(self, project_key: str) -> list[ProjectDocType]:
        result: list[ProjectDocType] = []
        for doc_type, filename in _DOC_FILENAMES.items():
            path = PROJECT_ROOT / "projects" / project_key / filename
            if path.exists():
                result.append(doc_type)  # type: ignore[arg-type]
        return result
