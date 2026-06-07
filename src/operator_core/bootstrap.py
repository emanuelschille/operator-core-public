from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from operator_core.config import Settings, load_settings
from operator_core.projects.registry import get_project_runtime_path


@dataclass(frozen=True)
class BootstrapContext:
    settings: Settings
    runtime_path: Path
    project_runtime: dict[str, str]


def _parse_simple_yaml(runtime_path: Path) -> dict[str, str]:
    data: dict[str, str] = {}

    for raw_line in runtime_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if ":" not in line:
            raise ValueError(f"Invalid runtime line in {runtime_path}: {raw_line}")

        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()

    return data


def build_bootstrap_context() -> BootstrapContext:
    settings = load_settings()
    runtime_path = get_project_runtime_path(settings.active_project)
    project_runtime = _parse_simple_yaml(runtime_path)

    return BootstrapContext(
        settings=settings,
        runtime_path=runtime_path,
        project_runtime=project_runtime,
    )
