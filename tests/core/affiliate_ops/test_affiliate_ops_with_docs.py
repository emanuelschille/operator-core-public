import pytest

from operator_core.core.affiliate_ops.service import AffiliateOpsService
from operator_core.projects.docs import ProjectDocsLoader


@pytest.fixture()
def service() -> AffiliateOpsService:
    return AffiliateOpsService(docs_loader=ProjectDocsLoader())


def test_offer_match_with_docs_loads_fit_rule(service: AffiliateOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="offer_match",
        command_body="stillkissen partnerprogramm",
    )

    assert result.lane_name == "affiliate_ops"
    assert result.action_type == "offer_match"
    assert result.summary == "Monetarisierungsregeln geladen."
    assert result.summary != "Offer-Match-Stub vorbereitet."
    assert any("Fit-Regel" in item or "Grundlagen" in item for item in result.items)
    assert len(result.items) == 3


def test_product_fit_with_docs_loads_category_and_trust(service: AffiliateOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="product_fit",
        command_body="baby trage alltag",
    )

    assert result.action_type == "product_fit"
    assert result.summary == "Produkt-Fit-Kontext geladen."
    assert any("Kategori" in item or "Trust" in item for item in result.items)
    assert result.summary != "Product-Fit-Stub vorbereitet."


def test_cta_direction_with_docs_loads_strength_rule(service: AffiliateOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="cta_direction",
        command_body="",
    )

    assert result.action_type == "cta_direction"
    assert result.summary == "CTA-Richtung geladen."
    assert any("CTA" in item or "Reife" in item for item in result.items)


def test_monetization_fit_with_docs_loads_maturity_model(service: AffiliateOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="monetization_fit",
        command_body="",
    )

    assert result.action_type == "monetization_fit"
    assert result.summary == "Monetarisierungs-Fit-Kontext geladen."
    assert any("Reife" in item or "Richtung" in item for item in result.items)


def test_recommendation_ready_with_docs_loads_stage_and_downgrade(service: AffiliateOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="recommendation_ready",
        command_body="produkt xyz",
    )

    assert result.action_type == "recommendation_ready"
    assert result.summary == "Empfehlungsbereitschaft geladen."
    assert any("Stufen" in item or "Herabstufung" in item for item in result.items)


def test_with_docs_passes_command_body_through(service: AffiliateOpsService) -> None:
    result = service.handle(
        project_key="everydayengel",
        action_type="offer_match",
        command_body="mein testprodukt",
    )

    assert any("mein testprodukt" in item for item in result.items)
