import pytest

from operator_core.core.knowledge_ops.service import (
    KnowledgeOpsService,
    UnsupportedKnowledgeActionError,
)
from operator_core.projects.docs import ProjectDocsLoader


# ------------------------------------------------------------------
# Stub path (no docs_loader)
# ------------------------------------------------------------------

def test_state_action_returns_structured_stub() -> None:
    service = KnowledgeOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="state",
        command_body="aktueller stand",
    )

    assert result.lane_name == "knowledge_ops"
    assert result.project_key == "everydayengel"
    assert result.action_type == "state"
    assert result.summary == "Project-State-Stub vorbereitet."
    assert len(result.items) == 3
    assert result.to_snapshot()["lane_name"] == "knowledge_ops"


def test_rules_action_returns_structured_stub() -> None:
    service = KnowledgeOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="rules",
        command_body="nur harte grenzen",
    )

    assert result.action_type == "rules"
    assert result.summary == "Rules-Stub vorbereitet."
    assert "nur harte grenzen" in result.items[1]


def test_unsupported_action_raises() -> None:
    service = KnowledgeOpsService()

    with pytest.raises(UnsupportedKnowledgeActionError):
        service.handle(
            project_key="everydayengel",
            action_type="review",
            command_body="bitte prüfen",
        )


# ------------------------------------------------------------------
# With-docs path (real ProjectDocsLoader)
# ------------------------------------------------------------------

@pytest.fixture()
def docs_service() -> KnowledgeOpsService:
    return KnowledgeOpsService(docs_loader=ProjectDocsLoader())


def test_state_with_docs_uses_real_content(docs_service: KnowledgeOpsService) -> None:
    result = docs_service.handle(
        project_key="everydayengel",
        action_type="state",
        command_body="",
    )

    assert result.lane_name == "knowledge_ops"
    assert result.project_key == "everydayengel"
    assert result.action_type == "state"
    assert result.summary == "Projektstand geladen."
    assert len(result.items) >= 1
    assert result.summary != "Project-State-Stub vorbereitet."


def test_rules_with_docs_loads_content_pillars(docs_service: KnowledgeOpsService) -> None:
    result = docs_service.handle(
        project_key="everydayengel",
        action_type="rules",
        command_body="",
    )

    assert result.action_type == "rules"
    assert result.summary == "Projektregeln geladen."
    assert any("Säulen" in item or "Ton" in item or "Trust" in item for item in result.items)


def test_context_with_docs_returns_semantic_info(docs_service: KnowledgeOpsService) -> None:
    result = docs_service.handle(
        project_key="everydayengel",
        action_type="context",
        command_body="",
    )

    assert result.action_type == "context"
    assert result.summary == "Projektkontext geladen."
    assert len(result.items) >= 1


def test_assumptions_with_docs_reads_audience_and_direction(docs_service: KnowledgeOpsService) -> None:
    result = docs_service.handle(
        project_key="everydayengel",
        action_type="assumptions",
        command_body="",
    )

    assert result.action_type == "assumptions"
    assert result.summary == "Aktive Annahmen aus project-state geladen."
    assert len(result.items) >= 1


def test_decisions_with_docs_returns_controlled_fallback(docs_service: KnowledgeOpsService) -> None:
    result = docs_service.handle(
        project_key="everydayengel",
        action_type="decisions",
        command_body="",
    )

    assert result.action_type == "decisions"
    assert "Entscheidungsquelle" in result.summary or "Entscheidungslog" in " ".join(result.items)
