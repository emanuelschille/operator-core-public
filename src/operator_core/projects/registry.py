from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_project_runtime_path(project_key: str) -> Path:
    runtime_path = PROJECT_ROOT / "projects" / project_key / "runtime.yaml"

    if not runtime_path.exists():
        raise FileNotFoundError(
            f"Missing runtime config for project '{project_key}': {runtime_path}"
        )

    return runtime_path
