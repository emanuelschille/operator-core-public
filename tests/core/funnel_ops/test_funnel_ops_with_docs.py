import pytest

from operator_core.core.funnel_ops.service import FunnelOpsService
from operator_core.projects.docs import ProjectDocsLoader


@pytest.fixture()
def service() -> FunnelOpsService:
    return FunnelOpsService(docs_loader=ProjectDocsLoader())


def test_page_brief_with_docs_loads_funnel_readiness(service: FunnelOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="page_brief",
        command_body="affiliate landingpage",
    )

    assert result.lane_name == "funnel_ops"
    assert result.action_type == "page_brief"
    assert result.summary == "Page-Brief-Kontext geladen."
    assert result.summary != "Page-Brief-Stub vorbereitet."
    assert any("Funnel" in item or "Phase" in item for item in result.items)
    assert len(result.items) == 3


def test_funnel_direction_with_docs_loads_pillars_and_cta(service: FunnelOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="funnel_direction",
        command_body="",
    )

    assert result.action_type == "funnel_direction"
    assert result.summary == "Funnel-Richtung geladen."
    assert any("Säulen" in item or "CTA" in item for item in result.items)


def test_routing_hint_with_docs_loads_maturity_model(service: FunnelOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="routing_hint",
        command_body="",
    )

    assert result.action_type == "routing_hint"
    assert result.summary == "Routing-Kontext geladen."
    assert any("Reife" in item or "CTA" in item for item in result.items)


def test_page_structure_with_docs_loads_posting_readiness(service: FunnelOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="page_structure",
        command_body="",
    )

    assert result.action_type == "page_structure"
    assert result.summary == "Page-Struktur-Kontext geladen."
    assert any("Posting" in item or "Mapping" in item for item in result.items)


def test_offer_path_with_docs_loads_funnel_and_avoid(service: FunnelOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="offer_path",
        command_body="",
    )

    assert result.action_type == "offer_path"
    assert result.summary == "Angebotspfad-Kontext geladen."
    assert any("Funnel" in item or "Vermeiden" in item for item in result.items)


def test_with_docs_passes_command_body_through(service: FunnelOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="page_brief",
        command_body="mein seitenkontext",
    )

    assert any("mein seitenkontext" in item for item in result.items)
