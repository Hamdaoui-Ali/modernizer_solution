import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from migration_factory.agents.planning_agent.paths import (
    get_optional_analysis_artifact_paths,
    get_required_analysis_artifact_paths,
)

_REQUIRED_JSON_ARTIFACTS = {
    "analysis_report.json",
    "dependency_graph.json",
    "test_inventory.json",
}

_OPTIONAL_JSON_ARTIFACTS = {
    "config_inventory.json",
    "rewrite_preview.json",
    "rewrite_plugin_plan.json",
    "rewrite_impact_summary.json",
    "copilot_assist.json",
}

_JSON_OBJECT_ARTIFACTS = {
    "rewrite_plugin_plan.json",
    "rewrite_impact_summary.json",
}


@dataclass(frozen=True)
class LoadedAnalysisArtifacts:
    required: dict[str, Any] = field(default_factory=dict)
    optional: dict[str, Any] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ok: bool = True


def load_analysis_artifacts(
    modernized_app_path: str | Path, run_id: str
) -> LoadedAnalysisArtifacts:
    required: dict[str, Any] = {}
    optional: dict[str, Any] = {}
    missing_required: list[str] = []
    errors: list[str] = []

    required_paths = get_required_analysis_artifact_paths(modernized_app_path, run_id)
    optional_paths = get_optional_analysis_artifact_paths(modernized_app_path, run_id)

    for artifact_name, artifact_path in required_paths.items():
        if not artifact_path.exists():
            missing_required.append(artifact_name)
            continue

        loaded, error = _load_artifact(artifact_name, artifact_path, is_required=True)
        if error:
            errors.append(error)
            continue
        required[artifact_name] = loaded

    for artifact_name, artifact_path in optional_paths.items():
        if not artifact_path.exists():
            continue

        loaded, error = _load_artifact(artifact_name, artifact_path, is_required=False)
        if error:
            errors.append(error)
            continue
        optional[artifact_name] = loaded

    ok = not missing_required and not any(
        error.startswith("Required artifact") for error in errors
    )

    return LoadedAnalysisArtifacts(
        required=required,
        optional=optional,
        missing_required=missing_required,
        errors=errors,
        ok=ok,
    )


def _load_artifact(
    artifact_name: str, artifact_path: Path, is_required: bool
) -> tuple[Any | None, str | None]:
    try:
        if artifact_name in _REQUIRED_JSON_ARTIFACTS or artifact_name in _OPTIONAL_JSON_ARTIFACTS:
            with artifact_path.open("r", encoding="utf-8") as file_obj:
                loaded = json.load(file_obj)
            if artifact_name in _JSON_OBJECT_ARTIFACTS and not isinstance(loaded, dict):
                prefix = "Required" if is_required else "Optional"
                return None, f"{prefix} artifact {artifact_name} must be JSON object."
            return loaded, None

        with artifact_path.open("r", encoding="utf-8") as file_obj:
            return file_obj.read(), None
    except (OSError, json.JSONDecodeError) as exc:
        prefix = "Required" if is_required else "Optional"
        return None, f"{prefix} artifact {artifact_name} failed to load: {exc}"
