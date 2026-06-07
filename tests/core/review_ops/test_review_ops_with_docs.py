import pytest

from operator_core.core.review_ops.service import ReviewOpsService
from operator_core.projects.docs import ProjectDocsLoader


@pytest.fixture()
def service() -> ReviewOpsService:
    return ReviewOpsService(docs_loader=ProjectDocsLoader())


def test_performance_review_with_docs_loads_priorities_and_outcomes(service: ReviewOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="performance_review",
        command_body="wochencheck",
    )

    assert result.lane_name == "review_ops"
    assert result.action_type == "performance_review"
    assert result.summary == "Review-Kontext geladen."
    assert result.summary != "Performance-Review-Stub vorbereitet."
    assert any("Prioritäten" in item or "Outcomes" in item for item in result.items)
    assert len(result.items) == 3


def test_learning_extract_with_docs_loads_trigger_and_lifecycle(service: ReviewOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="learning_extract",
        command_body="",
    )

    assert result.action_type == "learning_extract"
    assert result.summary == "Learning-Kontext geladen."
    assert any("Trigger" in item or "Lifecycle" in item or "learned" in item.lower() for item in result.items)


def test_hypothesis_with_docs_loads_phase_and_direction(service: ReviewOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="hypothesis",
        command_body="",
    )

    assert result.action_type == "hypothesis"
    assert result.summary == "Hypothesen-Kontext geladen."
    assert any("Phase" in item or "Richtung" in item for item in result.items)


def test_next_step_with_docs_loads_priorities(service: ReviewOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="next_step",
        command_body="",
    )

    assert result.action_type == "next_step"
    assert result.summary == "Nächster-Schritt-Kontext geladen."
    assert any("Prioritäten" in item or "Workflow" in item for item in result.items)


def test_pattern_check_with_docs_loads_repeatability_and_reusability(service: ReviewOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="pattern_check",
        command_body="",
    )

    assert result.action_type == "pattern_check"
    assert result.summary == "Pattern-Check-Kontext geladen."
    assert any("Wiederholbarkeit" in item or "Wiederverwendbarkeit" in item for item in result.items)


def test_with_docs_passes_command_body_through(service: ReviewOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="performance_review",
        command_body="mein fokusthema",
    )

    assert any("mein fokusthema" in item for item in result.items)
