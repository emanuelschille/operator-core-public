import pytest

from operator_core.core.content_ops.service import ContentOpsService
from operator_core.projects.docs import ProjectDocsLoader


@pytest.fixture()
def service() -> ContentOpsService:
    return ContentOpsService(docs_loader=ProjectDocsLoader())


def test_idea_with_docs_loads_pillars(service: ContentOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="idea",
        command_body="morgenroutine",
    )

    assert result.lane_name == "content_ops"
    assert result.action_type == "idea"
    assert result.summary == "Content-Regeln geladen."
    assert result.summary != "Idea-Stub vorbereitet."
    assert any("Säulen" in item or "Fit" in item for item in result.items)
    assert len(result.items) == 3


def test_hook_with_docs_loads_hook_rules(service: ContentOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="hook",
        command_body="video start",
    )

    assert result.action_type == "hook"
    assert result.summary == "Hook-Regeln geladen."
    assert any("Hook" in item or "Ton" in item for item in result.items)


def test_caption_with_docs_loads_caption_rules(service: ContentOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="caption",
        command_body="baby trage alltag",
    )

    assert result.action_type == "caption"
    assert result.summary == "Caption-Regeln geladen."
    assert any("Caption" in item or "CTA" in item for item in result.items)
    # stub summary must not appear
    assert result.summary != "Caption-Stub vorbereitet."


def test_draft_with_docs_loads_readiness_and_direction(service: ContentOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="draft",
        command_body="rohentwurf test",
    )

    assert result.action_type == "draft"
    assert result.summary == "Draft-Kontext geladen."
    assert any("Produktionsreife" in item or "Richtung" in item for item in result.items)


def test_variant_with_docs_loads_reusability(service: ContentOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="variant",
        command_body="",
    )

    assert result.action_type == "variant"
    assert result.summary == "Varianten-Kontext geladen."
    assert any("Wiederverwendbarkeit" in item or "Formate" in item for item in result.items)


def test_with_docs_passes_command_body_through(service: ContentOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="idea",
        command_body="mein testkontext",
    )

    assert any("mein testkontext" in item for item in result.items)
