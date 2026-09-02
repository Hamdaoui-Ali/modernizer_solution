import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

READ_ONLY_ASSESSMENT_MODE = "read_only_assessment"
FULL_SANDBOX_MIGRATION_MODE = "full_sandbox_migration"
ORCHESTRATION_MODES = {
    READ_ONLY_ASSESSMENT_MODE,
    FULL_SANDBOX_MIGRATION_MODE,
}

PHASE_STATUS_VALUES = {"PENDING", "RUNNING", "PASS", "FAIL", "SKIPPED"}
APPROVAL_STATUS_VALUES = {"PENDING", "INTERRUPTED", "COMPLETED", "FAILED"}
APPROVAL_DECISION_VALUES = {"approved", "rejected", "replan_required"}
COPILOT_ASSIST_MODE_VALUES = {"off", "failures", "warnings", "always"}
COPILOT_PROVIDER_VALUES = {"cli", "sdk", "deterministic", "copilot_cli"}

DEFAULT_COPILOT_ASSIST_MODE = "off"
DEFAULT_COPILOT_REPORT_ENABLED = False
DEFAULT_COPILOT_PROVIDER = "copilot_cli"
DEFAULT_COPILOT_MODEL = "gpt-5-mini"
DEFAULT_COPILOT_TIMEOUT_SECONDS = 300
DEFAULT_COPILOT_REPAIR_MAX_ATTEMPTS = 3

_COPILOT_ASSIST_ENV = "AI_MIGRATION_COPILOT_ASSIST"
_COPILOT_REPORT_ENV = "AI_MIGRATION_ENABLE_COPILOT_REPORT"
_COPILOT_PROVIDER_ENV = "AI_MIGRATION_COPILOT_PROVIDER"
_COPILOT_MODEL_ENV = "AI_MIGRATION_COPILOT_MODEL"
_COPILOT_TIMEOUT_ENV = "AI_MIGRATION_COPILOT_TIMEOUT_SECONDS"
_COPILOT_REQUIRED_ENV = "AI_MIGRATION_COPILOT_REQUIRED"
_COPILOT_FAILURE_AGENT_ENABLED_ENV = "AI_MIGRATION_COPILOT_FAILURE_AGENT_ENABLED"
_COPILOT_REPAIR_MAX_ATTEMPTS_ENV = "AI_MIGRATION_COPILOT_REPAIR_MAX_ATTEMPTS"
_AUTO_APPLY_SAFE_REPAIRS_ENV = "AI_MIGRATION_AUTO_APPLY_SAFE_REPAIRS"
_H2_STARTUP_REQUIRED_ENV = "AI_MIGRATION_H2_STARTUP_REQUIRED"
_COPILOT_REPAIR_STRICT_CONTAINMENT_ENV = "AI_MIGRATION_COPILOT_REPAIR_STRICT_CONTAINMENT"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

PhaseStatus = Literal["PENDING", "RUNNING", "PASS", "FAIL", "SKIPPED"]
ApprovalStatus = Literal["PENDING", "INTERRUPTED", "COMPLETED", "FAILED"]
ApprovalDecision = Literal["approved", "rejected", "replan_required"]
CopilotAssistMode = Literal["off", "failures", "warnings", "always"]
CopilotProvider = Literal["cli", "sdk", "deterministic"]


class CopilotConfigError(ValueError):
    """Raised when Copilot configuration is invalid."""


class MigrationState(TypedDict, total=False):
    run_id: str
    mode: str
    phase: str
    legacy_app_path: str
    modernized_app_path: str
    ai_hub_path: str
    profile_id: str
    thread_id: str
    current_unit: str

    analysis_status: PhaseStatus
    planning_status: PhaseStatus
    assessment_status: PhaseStatus
    orchestration_status: PhaseStatus

    approval_status: ApprovalStatus
    approval_decision: ApprovalDecision | None
    approved_by: str
    approval_comments: str

    final_status: str
    transform_status: str
    build_status: str
    test_status: str
    test_totals: dict[str, int]
    test_report_path: str
    test_summary_path: str
    test_log_path: str
    test_phase: str
    sandbox_path: str
    transform_log_path: str
    validation_execution_context: dict[str, Any]
    build_validation: dict[str, Any]
    stop_reason: str | None
    blockers: list[str]
    warnings: list[str]
    errors: list[str]

    artifact_refs: dict[str, str]
    timing: dict[str, object]
    analysis_artifacts_valid: bool
    planning_artifacts_valid: bool
    assessment_artifacts_valid: bool
    orchestration_artifacts_valid: bool

    run_dir: str
    analysis_dir: str
    planning_dir: str
    assessment_dir: str
    orchestration_dir: str

    copilot_enabled: bool
    copilot_assist_mode: CopilotAssistMode
    copilot_report_enabled: bool
    copilot_provider: CopilotProvider
    copilot_model: str
    copilot_timeout_seconds: int
    copilot_phase_statuses: dict[str, str]
    copilot_artifact_refs: dict[str, str]
    copilot_warnings: list[str]
    copilot_errors: list[str]
    copilot_fallback_used: bool
    copilot_assist_phase: str
    copilot_route_after_assist: str
    copilot_validation_had_warnings: bool
    copilot_required: bool
    copilot_failure_agent_enabled: bool
    copilot_availability_status: str
    copilot_feature_probe: dict[str, object]
    copilot_invocation_status: str
    repair_mode: str
    repair_loop_status: str
    repair_loop_enabled: bool
    repair_max_attempts: int
    auto_apply_safe_repairs: bool
    repair_attempts_count: int
    repair_fallback_generated: bool
    repair_safe_patch_applied: bool
    repair_human_review_required: bool
    failure_classification_status: str
    openrewrite_diff_risk_status: str
    h2_startup_required: bool
    h2_startup_status: str
    runtime_security_warnings: list[str]
    final_proof_level: str
    copilot_repair_strict_containment: bool
    dependency_policy_status: str
    dependency_policy_risks_count: int
    dependency_policy_blockers_count: int
    copilot_dependency_advisory_status: str
    policy_patch_applied: bool


def build_initial_state(
    *,
    run_id: str,
    legacy_app_path: str,
    modernized_app_path: str,
    ai_hub_path: str = "",
    profile_id: str = "",
    thread_id: str = "",
    mode: str = READ_ONLY_ASSESSMENT_MODE,
) -> MigrationState:
    run_dir = Path(modernized_app_path) / ".migration" / "runs" / run_id

    state: MigrationState = {
        "run_id": run_id,
        "mode": mode,
        "legacy_app_path": str(legacy_app_path),
        "modernized_app_path": str(modernized_app_path),
        "ai_hub_path": str(ai_hub_path),
        "profile_id": profile_id,
        "thread_id": thread_id,
        "current_unit": "",
        "analysis_status": "PENDING",
        "planning_status": "PENDING",
        "assessment_status": "PENDING",
        "orchestration_status": "PENDING",
        "approval_status": "PENDING",
        "approval_decision": None,
        "approved_by": "",
        "approval_comments": "",
        "final_status": "",
        "transform_status": "",
        "build_status": "",
        "test_status": "",
        "test_totals": {},
        "test_report_path": "",
        "test_summary_path": "",
        "test_log_path": "",
        "test_phase": "",
        "sandbox_path": "",
        "transform_log_path": "",
        "stop_reason": None,
        "blockers": [],
        "warnings": [],
        "errors": [],
        "artifact_refs": {},
        "timing": {},
        "analysis_artifacts_valid": False,
        "planning_artifacts_valid": False,
        "assessment_artifacts_valid": False,
        "orchestration_artifacts_valid": False,
        "run_dir": str(run_dir),
        "analysis_dir": str(run_dir / "analysis"),
        "planning_dir": str(run_dir / "planning"),
        "assessment_dir": str(run_dir / "assessment"),
        "orchestration_dir": str(run_dir / "orchestration"),
    }
    state.update(build_copilot_state_defaults())
    return state


def build_copilot_state_defaults() -> MigrationState:
    return {
        "copilot_enabled": DEFAULT_COPILOT_ASSIST_MODE != "off",
        "copilot_assist_mode": DEFAULT_COPILOT_ASSIST_MODE,
        "copilot_report_enabled": DEFAULT_COPILOT_REPORT_ENABLED,
        "copilot_provider": DEFAULT_COPILOT_PROVIDER,
        "copilot_model": DEFAULT_COPILOT_MODEL,
        "copilot_timeout_seconds": DEFAULT_COPILOT_TIMEOUT_SECONDS,
        "copilot_phase_statuses": {},
        "copilot_artifact_refs": {},
        "copilot_warnings": [],
        "copilot_errors": [],
        "copilot_fallback_used": False,
        "copilot_assist_phase": "",
        "copilot_route_after_assist": "",
        "copilot_validation_had_warnings": False,
        "copilot_required": False,
        "copilot_failure_agent_enabled": False,
        "copilot_availability_status": "SKIPPED",
        "copilot_feature_probe": {},
        "copilot_invocation_status": "SKIPPED",
        "repair_mode": "proposal_only",
        "repair_loop_status": "NOT_IMPLEMENTED",
        "repair_loop_enabled": False,
        "repair_max_attempts": DEFAULT_COPILOT_REPAIR_MAX_ATTEMPTS,
        "auto_apply_safe_repairs": False,
        "repair_attempts_count": 0,
        "repair_fallback_generated": False,
        "repair_safe_patch_applied": False,
        "repair_human_review_required": False,
        "failure_classification_status": "PENDING",
        "openrewrite_diff_risk_status": "UNKNOWN",
        "h2_startup_required": False,
        "h2_startup_status": "H2_STARTUP_SKIPPED",
        "runtime_security_warnings": [],
        "final_proof_level": "not_verified",
        "copilot_repair_strict_containment": True,
        "dependency_policy_status": "NOT_RUN",
        "dependency_policy_risks_count": 0,
        "dependency_policy_blockers_count": 0,
        "copilot_dependency_advisory_status": "SKIPPED",
        "policy_patch_applied": False,
    }


def parse_copilot_config_from_env(env: Mapping[str, str] | None = None) -> MigrationState:
    source = env or os.environ
    assist_mode = _normalized_env_value(source, _COPILOT_ASSIST_ENV, DEFAULT_COPILOT_ASSIST_MODE)
    if assist_mode not in COPILOT_ASSIST_MODE_VALUES:
        raise CopilotConfigError(
            f"{_COPILOT_ASSIST_ENV} must be one of: {', '.join(sorted(COPILOT_ASSIST_MODE_VALUES))}"
        )

    provider = _normalized_env_value(source, _COPILOT_PROVIDER_ENV, DEFAULT_COPILOT_PROVIDER)
    if provider not in COPILOT_PROVIDER_VALUES:
        raise CopilotConfigError(
            f"{_COPILOT_PROVIDER_ENV} must be one of: {', '.join(sorted(COPILOT_PROVIDER_VALUES))}"
        )

    timeout_seconds = _positive_int_env_value(
        source,
        _COPILOT_TIMEOUT_ENV,
        DEFAULT_COPILOT_TIMEOUT_SECONDS,
    )
    report_enabled = _bool_env_value(
        source,
        _COPILOT_REPORT_ENV,
        DEFAULT_COPILOT_REPORT_ENABLED,
    )
    model = str(source.get(_COPILOT_MODEL_ENV, "")).strip() or DEFAULT_COPILOT_MODEL

    config: MigrationState = build_copilot_state_defaults()
    config.update(
        {
            "copilot_enabled": assist_mode != "off",
            "copilot_assist_mode": assist_mode,
            "copilot_report_enabled": report_enabled,
            "copilot_provider": provider,
            "copilot_model": model,
            "copilot_timeout_seconds": timeout_seconds,
            "copilot_required": _bool_env_value(source, _COPILOT_REQUIRED_ENV, False),
            "copilot_failure_agent_enabled": _bool_env_value(
                source,
                _COPILOT_FAILURE_AGENT_ENABLED_ENV,
                False,
            ),
            "repair_loop_enabled": _bool_env_value(
                source,
                _COPILOT_FAILURE_AGENT_ENABLED_ENV,
                False,
            ),
            "repair_max_attempts": _positive_int_env_value(
                source,
                _COPILOT_REPAIR_MAX_ATTEMPTS_ENV,
                DEFAULT_COPILOT_REPAIR_MAX_ATTEMPTS,
            ),
            "auto_apply_safe_repairs": _bool_env_value(
                source,
                _AUTO_APPLY_SAFE_REPAIRS_ENV,
                False,
            ),
            "h2_startup_required": _bool_env_value(source, _H2_STARTUP_REQUIRED_ENV, False),
            "copilot_repair_strict_containment": _bool_env_value(
                source,
                _COPILOT_REPAIR_STRICT_CONTAINMENT_ENV,
                True,
            ),
        }
    )
    return config


def apply_copilot_config(
    state: Mapping[str, object],
    env: Mapping[str, str] | None = None,
) -> MigrationState:
    updated: MigrationState = dict(state)
    preserve_runtime = {
        key: updated[key]
        for key in (
            "copilot_availability_status",
            "copilot_feature_probe",
            "copilot_invocation_status",
            "repair_loop_status",
            "repair_attempts_count",
            "repair_fallback_generated",
            "repair_safe_patch_applied",
            "repair_human_review_required",
            "failure_classification_status",
            "openrewrite_diff_risk_status",
            "h2_startup_status",
            "runtime_security_warnings",
            "final_proof_level",
        )
        if key in updated
    }
    updated.update(parse_copilot_config_from_env(env))
    updated.update(preserve_runtime)
    return updated


def _normalized_env_value(source: Mapping[str, str], name: str, default: str) -> str:
    return str(source.get(name, "")).strip().lower() or default


def _bool_env_value(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(source.get(name, "")).strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return False


def _positive_int_env_value(source: Mapping[str, str], name: str, default: int) -> int:
    raw = str(source.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise CopilotConfigError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise CopilotConfigError(f"{name} must be a positive integer")
    return value
