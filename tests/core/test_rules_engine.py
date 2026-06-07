"""T2 — confirmation policy (rules_engine). Pure, decides only."""

from operator_core.core.rules_engine import (
    DEFAULT_HIGH_IMPACT_COMMANDS,
    requires_confirmation,
)


def test_high_impact_command_requires_confirmation() -> None:
    assert "vollauto" in DEFAULT_HIGH_IMPACT_COMMANDS
    assert requires_confirmation("vollauto") is True


def test_ordinary_command_does_not_require_confirmation() -> None:
    assert requires_confirmation("idea") is False
    assert requires_confirmation("status") is False


def test_policy_is_configurable() -> None:
    assert requires_confirmation("publish_now", high_impact=frozenset({"publish_now"})) is True
    assert requires_confirmation("vollauto", high_impact=frozenset()) is False


def test_command_name_is_normalised() -> None:
    # leading slash / casing should not change the decision
    assert requires_confirmation("/vollauto") is True
    assert requires_confirmation("VOLLAUTO") is True
    assert requires_confirmation("") is False
    assert requires_confirmation(None) is False
