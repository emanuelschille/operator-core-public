import pytest

from operator_core.core.review_ops.service import (
    ReviewOpsService,
    UnsupportedReviewActionError,
)


def test_performance_review_action_returns_structured_stub() -> None:
    service = ReviewOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="performance_review",
        command_body="letzte 7 tage",
    )

    assert result.lane_name == "review_ops"
    assert result.project_key == "everydayengel"
    assert result.action_type == "performance_review"
    assert result.summary == "Performance-Review-Stub vorbereitet."
    assert len(result.items) == 3
    assert result.to_snapshot()["lane_name"] == "review_ops"


def test_learning_extract_action_returns_structured_stub() -> None:
    service = ReviewOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="learning_extract",
        command_body="caption performance",
    )

    assert result.action_type == "learning_extract"
    assert result.summary == "Learning-Extract-Stub vorbereitet."
    assert "caption performance" in result.items[1]


def test_unsupported_action_raises() -> None:
    service = ReviewOpsService()

    with pytest.raises(UnsupportedReviewActionError):
        service.handle(
            project_key="everydayengel",
            action_type="rules",
            command_body="bitte prüfen",
        )
