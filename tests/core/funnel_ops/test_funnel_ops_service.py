import pytest

from operator_core.core.funnel_ops.service import (
    FunnelOpsService,
    UnsupportedFunnelActionError,
)


def test_page_brief_action_returns_structured_stub() -> None:
    service = FunnelOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="page_brief",
        command_body="stillkissen landingpage",
    )

    assert result.lane_name == "funnel_ops"
    assert result.project_key == "everydayengel"
    assert result.action_type == "page_brief"
    assert result.summary == "Page-Brief-Stub vorbereitet."
    assert len(result.items) == 3
    assert result.to_snapshot()["lane_name"] == "funnel_ops"


def test_page_structure_action_returns_structured_stub() -> None:
    service = FunnelOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="page_structure",
        command_body="hero proof cta",
    )

    assert result.action_type == "page_structure"
    assert result.summary == "Page-Structure-Stub vorbereitet."
    assert "hero proof cta" in result.items[1]


def test_unsupported_action_raises() -> None:
    service = FunnelOpsService()

    with pytest.raises(UnsupportedFunnelActionError):
        service.handle(
            project_key="everydayengel",
            action_type="rules",
            command_body="bitte prüfen",
        )
