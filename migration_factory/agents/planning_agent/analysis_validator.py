from dataclasses import dataclass, field
from typing import Any

from migration_factory.agents.planning_agent.artifact_reader import LoadedAnalysisArtifacts

_REQUIRED_JSON_ARTIFACTS = (
    "analysis_report.json",
    "dependency_graph.json",
    "test_inventory.json",
)


@dataclass(frozen=True)
class AnalysisValidationResult:
    ok: bool
    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    non_executable_reason: str | None = None


def validate_analysis_completeness(
    loaded_artifacts: LoadedAnalysisArtifacts,
) -> AnalysisValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    non_executable_reason: str | None = None

    if loaded_artifacts.missing_required:
        errors.append(
            "Missing required analysis artifacts: "
            + ", ".join(sorted(loaded_artifacts.missing_required))
        )

    for error in loaded_artifacts.errors:
        if error.startswith("Required artifact"):
            errors.append(error)
        else:
            warnings.append(error)

    required = loaded_artifacts.required

    for artifact_name in _REQUIRED_JSON_ARTIFACTS:
        value = required.get(artifact_name)
        if value is None:
            continue
        if not isinstance(value, dict):
            errors.append(f"Required artifact {artifact_name} must be JSON object.")

    summary = required.get("analysis_summary.md")
    if summary is None:
        pass
    elif not isinstance(summary, str) or not summary.strip():
        errors.append("Required artifact analysis_summary.md must be non-empty text.")

    analysis_report = required.get("analysis_report.json")
    if isinstance(analysis_report, dict) and analysis_report.get("status") == "FAIL":
        non_executable_reason = "Analysis status FAIL in analysis_report.json"

    if errors:
        return AnalysisValidationResult(
            ok=False,
            status="FAIL",
            errors=errors,
            warnings=warnings,
            non_executable_reason=non_executable_reason,
        )

    if non_executable_reason:
        return AnalysisValidationResult(
            ok=False,
            status="PASS",
            errors=[],
            warnings=warnings,
            non_executable_reason=non_executable_reason,
        )

    return AnalysisValidationResult(
        ok=True,
        status="PASS",
        errors=[],
        warnings=warnings,
        non_executable_reason=None,
    )
