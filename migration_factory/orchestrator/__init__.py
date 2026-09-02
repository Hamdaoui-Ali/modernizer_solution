"""Orchestrator package."""

from migration_factory.orchestrator.artifact_validation import (
    ArtifactValidationResult,
    validate_analysis_artifacts,
    validate_assessment_artifacts,
    validate_planning_artifacts,
)
from migration_factory.orchestrator.approval import (
    approval_node,
    build_approval_payload,
)
from migration_factory.orchestrator.checkpointing import (
    default_checkpointer,
    require_thread_id,
)
from migration_factory.orchestrator.graph import build_graph
from migration_factory.orchestrator.preflight import (
    PreflightError,
    build_langgraph_config,
    validate_preflight,
)
from migration_factory.orchestrator.phase_services import (
    PhaseServices,
    default_phase_services,
    run_analysis_phase,
    run_assessment_phase,
    run_planning_phase,
)
from migration_factory.orchestrator.state import (
    APPROVAL_DECISION_VALUES,
    APPROVAL_STATUS_VALUES,
    PHASE_STATUS_VALUES,
    READ_ONLY_ASSESSMENT_MODE,
    MigrationState,
    build_initial_state,
)
from migration_factory.orchestrator.summary import (
    build_orchestration_summary,
    write_orchestration_summary,
)


def __getattr__(name: str):
    if name in {"main", "parse_args"}:
        from migration_factory.orchestrator import runner

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "APPROVAL_DECISION_VALUES",
    "APPROVAL_STATUS_VALUES",
    "ArtifactValidationResult",
    "PHASE_STATUS_VALUES",
    "PhaseServices",
    "PreflightError",
    "READ_ONLY_ASSESSMENT_MODE",
    "MigrationState",
    "approval_node",
    "build_graph",
    "build_initial_state",
    "build_langgraph_config",
    "build_orchestration_summary",
    "build_approval_payload",
    "default_checkpointer",
    "default_phase_services",
    "main",
    "parse_args",
    "require_thread_id",
    "run_analysis_phase",
    "run_assessment_phase",
    "run_planning_phase",
    "validate_analysis_artifacts",
    "validate_assessment_artifacts",
    "validate_planning_artifacts",
    "validate_preflight",
    "write_orchestration_summary",
]
