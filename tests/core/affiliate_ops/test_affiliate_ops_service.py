import pytest

from operator_core.core.affiliate_ops.service import (
    AffiliateOpsService,
    UnsupportedAffiliateActionError,
)


def test_offer_match_action_returns_structured_stub() -> None:
    service = AffiliateOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="offer_match",
        command_body="stillkissen partnerprogramm",
    )

    assert result.lane_name == "affiliate_ops"
    assert result.project_key == "everydayengel"
    assert result.action_type == "offer_match"
    assert result.summary == "Offer-Match-Stub vorbereitet."
    assert len(result.items) == 3
    assert result.to_snapshot()["lane_name"] == "affiliate_ops"


def test_product_fit_action_returns_structured_stub() -> None:
    service = AffiliateOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="product_fit",
        command_body="baby trage alltag",
    )

    assert result.action_type == "product_fit"
    assert result.summary == "Product-Fit-Stub vorbereitet."
    assert "baby trage alltag" in result.items[1]


def test_unsupported_action_raises() -> None:
    service = AffiliateOpsService()

    with pytest.raises(UnsupportedAffiliateActionError):
        service.handle(
            project_key="everydayengel",
            action_type="rules",
            command_body="bitte prüfen",
        )
