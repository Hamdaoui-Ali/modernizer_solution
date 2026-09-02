from pathlib import Path

from migration_factory.contracts.planning_artifacts import (
    OPTIONAL_ANALYSIS_INPUT_ARTIFACTS,
    PLANNING_OUTPUT_ARTIFACTS,
    REQUIRED_ANALYSIS_INPUT_ARTIFACTS,
)


def get_run_analysis_dir(modernized_app_path: str | Path, run_id: str) -> Path:
    return Path(modernized_app_path) / ".migration" / "runs" / run_id / "analysis"


def get_run_planning_dir(modernized_app_path: str | Path, run_id: str) -> Path:
    return Path(modernized_app_path) / ".migration" / "runs" / run_id / "planning"


def get_required_analysis_artifact_path(
    modernized_app_path: str | Path, run_id: str, artifact_name: str
) -> Path:
    return get_run_analysis_dir(modernized_app_path, run_id) / artifact_name


def get_optional_analysis_artifact_path(
    modernized_app_path: str | Path, run_id: str, artifact_name: str
) -> Path:
    return get_run_analysis_dir(modernized_app_path, run_id) / artifact_name


def get_planning_output_artifact_path(
    modernized_app_path: str | Path, run_id: str, artifact_name: str
) -> Path:
    return get_run_planning_dir(modernized_app_path, run_id) / artifact_name


def get_required_analysis_artifact_paths(
    modernized_app_path: str | Path, run_id: str
) -> dict[str, Path]:
    return {
        name: get_required_analysis_artifact_path(modernized_app_path, run_id, name)
        for name in REQUIRED_ANALYSIS_INPUT_ARTIFACTS
    }


def get_optional_analysis_artifact_paths(
    modernized_app_path: str | Path, run_id: str
) -> dict[str, Path]:
    return {
        name: get_optional_analysis_artifact_path(modernized_app_path, run_id, name)
        for name in OPTIONAL_ANALYSIS_INPUT_ARTIFACTS
    }


def get_planning_output_artifact_paths(
    modernized_app_path: str | Path, run_id: str
) -> dict[str, Path]:
    return {
        name: get_planning_output_artifact_path(modernized_app_path, run_id, name)
        for name in PLANNING_OUTPUT_ARTIFACTS
    }


def get_ai_hub_profile_path(ai_hub_path: str | Path, profile_id: str) -> Path:
    return Path(ai_hub_path) / "profiles" / f"{profile_id}.yaml"
