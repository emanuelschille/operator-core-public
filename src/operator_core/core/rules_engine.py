"""Confirmation policy for high-impact operator actions.

This is a small, pure policy layer: given a classified command it decides
**whether human confirmation is required** before the action may execute. It
*decides only* — it never writes Jobs/Runs/Events, never persists anything, and
never resolves project context. Persistence and gating live in
``execution_service``/``job_service`` (which preserve the single-writer rules).

The high-impact set is an explicit, configurable allow-list rather than inferred
heuristics: the policy is exactly as broad as what is declared here.
"""

from __future__ import annotations

# Commands that mutate or publish in a way that warrants a human stop before
# execution. Kept deliberately small and explicit; callers may override.
DEFAULT_HIGH_IMPACT_COMMANDS: frozenset[str] = frozenset(
    {
        "vollauto",  # full-auto content generation: creates a draft end-to-end
    }
)


def _normalise(command_name: str | None) -> str:
    if not command_name:
        return ""
    name = command_name.strip().lower()
    if name.startswith("/"):
        name = name[1:].split("@", 1)[0].strip()
    return name


def requires_confirmation(
    command_name: str | None,
    *,
    high_impact: frozenset[str] = DEFAULT_HIGH_IMPACT_COMMANDS,
) -> bool:
    """Return ``True`` if ``command_name`` is a high-impact action needing confirmation."""
    return _normalise(command_name) in high_impact
