from __future__ import annotations

from dataclasses import dataclass

from operator_core.bootstrap import BootstrapContext


@dataclass(frozen=True)
class ResolvedProjectContext:
    project_key: str
    display_name: str
    status: str
    primary_interface: str
    human_in_the_loop: bool


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_active_project_context(context: BootstrapContext) -> ResolvedProjectContext:
    runtime = context.project_runtime

    runtime_project_key = runtime.get("project_key", "").strip()
    if runtime_project_key != context.settings.active_project:
        raise ValueError(
            "Active project setting does not match runtime project_key: "
            f"{context.settings.active_project} != {runtime_project_key}"
        )

    display_name = runtime.get("display_name", runtime_project_key).strip()
    status = runtime.get("status", "inactive").strip()
    primary_interface = runtime.get("primary_interface", "telegram").strip()
    human_in_the_loop = _parse_bool(runtime.get("human_in_the_loop", "true"))

    return ResolvedProjectContext(
        project_key=runtime_project_key,
        display_name=display_name,
        status=status,
        primary_interface=primary_interface,
        human_in_the_loop=human_in_the_loop,
    )
