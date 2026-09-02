from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from migration_factory.final_report import (
    generate_final_migration_report,
    write_report_context,
)
from migration_factory.orchestrator.artifact_validation import (
    validate_successful_full_sandbox_orchestration,
)
from migration_factory.orchestrator.state import FULL_SANDBOX_MIGRATION_MODE
from migration_factory.orchestrator.state import MigrationState
from migration_factory.orchestrator.timing import record_phase_duration, write_timing_artifacts


EXECUTION_CLAIMS = {
    "transformation_executed": False,
    "openrewrite_apply_executed": False,
    "migrated_build_executed": False,
    "migrated_tests_executed": False,
    "final_migration_executed": False,
}


def build_orchestration_summary(state: MigrationState) -> dict:
    normalized = _normalize_output_paths(dict(state))
    execution_claims = _execution_claims(normalized)  # type: ignore[arg-type]
    return {
        "run_id": normalized.get("run_id", ""),
        "final_status": _final_status(normalized),  # type: ignore[arg-type]
        "current_phase": normalized.get("current_phase", normalized.get("current_unit", "")),
        "analysis_status": normalized.get("analysis_status", ""),
        "planning_status": normalized.get("planning_status", ""),
        "assessment_status": normalized.get("assessment_status", ""),
        "orchestration_status": normalized.get("orchestration_status", ""),
        "approval_status": normalized.get("approval_status", ""),
        "approval_decision": normalized.get("approval_decision"),
        "approved_by": normalized.get("approved_by", ""),
        "transform_status": normalized.get("transform_status", ""),
        "build_status": normalized.get("build_status", ""),
        "test_status": normalized.get("test_status", ""),
        "test_totals": dict(normalized.get("test_totals", {}) or {}),
        "test_report_path": normalized.get("test_report_path", ""),
        "test_summary_path": normalized.get("test_summary_path", ""),
        "test_log_path": normalized.get("test_log_path", ""),
        "test_phase": normalized.get("test_phase", ""),
        "copilot_availability_status": normalized.get("copilot_availability_status", "SKIPPED"),
        "copilot_invocation_status": normalized.get("copilot_invocation_status", "SKIPPED"),
        "repair_mode": normalized.get("repair_mode", "proposal_only"),
        "repair_loop_status": normalized.get("repair_loop_status", "NOT_IMPLEMENTED"),
        "repair_attempts_count": int(normalized.get("repair_attempts_count") or 0),
        "repair_fallback_generated": bool(normalized.get("repair_fallback_generated", False)),
        "repair_safe_patch_applied": bool(normalized.get("repair_safe_patch_applied", False)),
        "failure_classification_status": normalized.get("failure_classification_status", "PENDING"),
        "openrewrite_diff_risk_status": normalized.get("openrewrite_diff_risk_status", "UNKNOWN"),
        "h2_startup_required": bool(normalized.get("h2_startup_required", False)),
        "h2_startup_status": normalized.get("h2_startup_status", "H2_STARTUP_SKIPPED"),
        "runtime_security_warnings": list(normalized.get("runtime_security_warnings", []) or []),
        "final_proof_level": normalized.get("final_proof_level", "not_verified"),
        "sandbox_path": normalized.get("sandbox_path", ""),
        "modernized_app_path": normalized.get("modernized_app_path", ""),
        "log_path": normalized.get("transform_log_path", ""),
        "stop_reason": normalized.get("stop_reason"),
        "blockers": list(normalized.get("blockers", []) or []),
        "warnings": list(normalized.get("warnings", []) or []),
        "errors": list(normalized.get("errors", []) or []),
        "artifact_refs": dict(normalized.get("artifact_refs", {}) or {}),
        "copilot_phase_statuses": dict(normalized.get("copilot_phase_statuses", {}) or {}),
        "copilot_artifact_refs": dict(normalized.get("copilot_artifact_refs", {}) or {}),
        "copilot_warnings": list(normalized.get("copilot_warnings", []) or []),
        "copilot_errors": list(normalized.get("copilot_errors", []) or []),
        "copilot_fallback_used": bool(normalized.get("copilot_fallback_used", False)),
        "timing": dict(normalized.get("timing", {}) or {}),
        **execution_claims,
    }


def write_orchestration_summary(state: MigrationState) -> Path:
    normalized = _normalize_output_paths(dict(state))
    summary_path = Path(normalized["orchestration_dir"]) / "orchestration_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            _to_json_safe(build_orchestration_summary(normalized)),  # type: ignore[arg-type]
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return summary_path


def finalize_orchestration_state(
    state: MigrationState,
    *,
    summary_writer=write_orchestration_summary,
) -> MigrationState:
    result = _normalize_output_paths(dict(state))
    summary_path = Path(str(result.get("orchestration_dir", ""))) / "orchestration_summary.json"

    artifact_refs = dict(result.get("artifact_refs", {}) or {})
    artifact_refs["orchestration_summary"] = str(summary_path)
    result["artifact_refs"] = artifact_refs

    timing_refs = write_timing_artifacts(result)
    result["artifact_refs"] = {**dict(result.get("artifact_refs", {}) or {}), **timing_refs}
    result = _normalize_output_paths(result)

    if not _is_successful_full_sandbox_migration(result):  # type: ignore[arg-type]
        result["orchestration_artifacts_valid"] = False
        summary_writer(result)  # type: ignore[arg-type]
        return result  # type: ignore[return-value]

    summary_writer(result)  # type: ignore[arg-type]

    final_report_started = time.monotonic()
    final_report = generate_final_migration_report(result)
    record_phase_duration(result, phase="final_report", duration_seconds=time.monotonic() - final_report_started)

    timing_refs = write_timing_artifacts(result)
    result["artifact_refs"] = {**dict(result.get("artifact_refs", {}) or {}), **timing_refs}

    if final_report.blockers:
        result["blockers"] = [
            *list(result.get("blockers", []) or []),
            *final_report.blockers,
        ]
        result["orchestration_status"] = "FAIL"
        result["final_status"] = "FAILED"
        result["orchestration_artifacts_valid"] = False
        summary_writer(result)  # type: ignore[arg-type]
        return result  # type: ignore[return-value]

    if final_report.warnings:
        result["warnings"] = [
            *list(result.get("warnings", []) or []),
            *final_report.warnings,
        ]

    artifact_refs = {
        **dict(result.get("artifact_refs", {}) or {}),
        **final_report.artifact_refs,
    }

    if "final_report" in artifact_refs:
        artifact_refs.setdefault("stage_report", artifact_refs["final_report"])
    if "final_report_md" in artifact_refs:
        artifact_refs.setdefault("stage_report_md", artifact_refs["final_report_md"])

    result["artifact_refs"] = artifact_refs
    result = _normalize_output_paths(result)

    report_context_path = write_report_context(result["run_dir"])
    result["artifact_refs"] = {
        **dict(result.get("artifact_refs", {}) or {}),
        "copilot_report_context": str(report_context_path),
    }

    _maybe_generate_copilot_final_report(result)
    _generate_copilot_docs(result)

    timing_refs = write_timing_artifacts(result)
    result["artifact_refs"] = {**dict(result.get("artifact_refs", {}) or {}), **timing_refs}
    result = _normalize_output_paths(result)

    summary_writer(result)  # type: ignore[arg-type]

    validation = validate_successful_full_sandbox_orchestration(result)  # type: ignore[arg-type]
    result["orchestration_artifacts_valid"] = validation.valid

    artifact_refs = dict(result.get("artifact_refs", {}) or {})
    result["artifact_refs"] = {
        **artifact_refs,
        **validation.artifact_refs,
    }

    if validation.blockers:
        result["blockers"] = [
            *list(result.get("blockers", []) or []),
            *validation.blockers,
        ]
    if validation.warnings:
        result["warnings"] = [
            *list(result.get("warnings", []) or []),
            *validation.warnings,
        ]

    result = _normalize_output_paths(result)
    summary_writer(result)  # type: ignore[arg-type]
    return result  # type: ignore[return-value]


def _maybe_generate_copilot_final_report(state: dict[str, Any]) -> None:
    return


def _generate_copilot_docs(state: dict[str, Any]) -> None:
    return


def _final_status(state: MigrationState) -> str:
    if state.get("final_status"):
        return str(state.get("final_status"))
    if state.get("approval_status") == "FAILED":
        return "FAILED"
    if state.get("errors") or state.get("blockers"):
        return "FAILED"
    if any(
        state.get(status_key) == "FAIL"
        for status_key in ("analysis_status", "planning_status", "assessment_status")
    ):
        return "FAILED"
    if state.get("approval_status") == "INTERRUPTED":
        return "INTERRUPTED"
    if state.get("approval_status") == "COMPLETED":
        return "COMPLETED"
    return "COMPLETED"


def _is_successful_full_sandbox_migration(state: MigrationState) -> bool:
    return (
        state.get("mode") == FULL_SANDBOX_MIGRATION_MODE
        and state.get("approval_status") == "COMPLETED"
        and state.get("approval_decision") == "approved"
        and state.get("orchestration_status") == "PASS"
        and state.get("transform_status") == "TRANSFORM_APPLIED_IN_SANDBOX"
        and state.get("build_status") == "BUILD_PASSED_IN_SANDBOX"
        and state.get("test_status") == "TEST_PASSED"
        and _final_status(state) == "TRANSFORM_APPLIED_IN_SANDBOX"
    )


def _execution_claims(state: MigrationState) -> dict[str, bool]:
    claims = dict(EXECUTION_CLAIMS)
    if state.get("transform_status") == "TRANSFORM_APPLIED_IN_SANDBOX":
        claims["transformation_executed"] = True
        claims["openrewrite_apply_executed"] = True
    if state.get("build_status") == "BUILD_PASSED_IN_SANDBOX":
        claims["migrated_build_executed"] = True
    if state.get("test_status") == "TEST_PASSED":
        claims["migrated_tests_executed"] = True
    if _is_successful_full_sandbox_migration(state):
        claims["final_migration_executed"] = True
    return claims


def _normalize_output_paths(state: dict[str, Any]) -> dict[str, Any]:
    updated = dict(state)
    artifact_refs = dict(updated.get("artifact_refs", {}) or {})

    sandbox_path = _first_text(
        updated.get("sandbox_path"),
        artifact_refs.get("sandbox"),
        artifact_refs.get("sandbox_path"),
        artifact_refs.get("modernized_app"),
        artifact_refs.get("modernized_app_path"),
    )

    if sandbox_path:
        updated["sandbox_path"] = sandbox_path

    return updated


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_to_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
