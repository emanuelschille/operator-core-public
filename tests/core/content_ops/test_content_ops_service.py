import pytest

from operator_core.core.content_ops.service import (
    ContentOpsService,
    UnsupportedContentActionError,
)


def test_idea_action_returns_structured_stub() -> None:
    service = ContentOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="idea",
        command_body="morgenroutine video",
    )

    assert result.lane_name == "content_ops"
    assert result.project_key == "everydayengel"
    assert result.action_type == "idea"
    assert result.summary == "Idea-Stub vorbereitet."
    assert result.command_body == "morgenroutine video"
    assert len(result.items) == 3
    assert result.to_snapshot()["lane_name"] == "content_ops"


def test_caption_action_returns_structured_stub() -> None:
    service = ContentOpsService()

    result = service.handle(
        project_key="everydayengel",
        action_type="caption",
        command_body="baby trage alltag",
    )

    assert result.action_type == "caption"
    assert result.summary == "Caption-Stub vorbereitet."
    assert "baby trage alltag" in result.items[1]


def test_unsupported_action_raises() -> None:
    service = ContentOpsService()

    with pytest.raises(UnsupportedContentActionError):
        service.handle(
            project_key="everydayengel",
            action_type="review",
            command_body="bitte prüfen",
        )
