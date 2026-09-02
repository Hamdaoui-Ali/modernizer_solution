from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from migration_factory.orchestrator.approval import approval_node
from migration_factory.orchestrator.artifact_validation import (
    ArtifactValidationResult,
    validate_analysis_artifacts,
    validate_assessment_artifacts,
    validate_planning_artifacts,
)
from migration_factory.orchestrator.copilot_assist import (
    copilot_final_report,
    copilot_phase_assist,
)
from migration_factory.orchestrator.phase_services import (
    PhaseServices,
    default_phase_services,
    record_approval_decision_phase,
    run_sandbox_transform_phase,
)
from migration_factory.orchestrator.state import MigrationState
from migration_factory.orchestrator.state import FULL_SANDBOX_MIGRATION_MODE
from migration_factory.orchestrator.events import emit_control_tower_event
from migration_factory.orchestrator.summary import finalize_orchestration_state


ValidationCallable = Callable[[MigrationState], ArtifactValidationResult]


def build_graph(
    checkpointer=None,
    phase_services: PhaseServices | None = None,
    approval_record_service=None,
    sandbox_transform_service=None,
):
    services = phase_services or default_phase_services()

    graph = StateGraph(MigrationState)
    graph.add_node(
        "analysis",
        _phase_node(
            services.run_analysis_phase,
            validate_analysis_artifacts,
            phase="analysis",
            status_key="analysis_status",
            artifacts_valid_key="analysis_artifacts_valid",
            next_on_pass="planning",
        ),
    )
    graph.add_node(
        "planning",
        _phase_node(
            services.run_planning_phase,
            validate_planning_artifacts,
            phase="planning",
            status_key="planning_status",
            artifacts_valid_key="planning_artifacts_valid",
            next_on_pass="assessment",
        ),
    )
    graph.add_node(
        "assessment",
        _phase_node(
            services.run_assessment_phase,
            validate_assessment_artifacts,
            phase="assessment",
            status_key="assessment_status",
            artifacts_valid_key="assessment_artifacts_valid",
            next_on_pass="approval",
        ),
    )
    graph.add_node("copilot_phase_assist", copilot_phase_assist)
    graph.add_node("approval", approval_node)
    graph.add_node(
        "approval_record",
        approval_record_service or record_approval_decision_phase,
    )
    graph.add_node(
        "sandbox_transform",
        sandbox_transform_service or run_sandbox_transform_phase,
    )
    graph.add_node("final_report", _deterministic_final_report_node)
    graph.add_node("copilot_final_report", copilot_final_report)

    graph.add_edge(START, "analysis")
    graph.add_conditional_edges(
        "analysis",
        _route_analysis,
        {"planning": "planning", "copilot_phase_assist": "copilot_phase_assist", END: END},
    )
    graph.add_conditional_edges(
        "planning",
        _route_planning,
        {"assessment": "assessment", "copilot_phase_assist": "copilot_phase_assist", END: END},
    )
    graph.add_conditional_edges(
        "assessment",
        _route_assessment,
        {"approval": "approval", "copilot_phase_assist": "copilot_phase_assist", END: END},
    )
    graph.add_conditional_edges(
        "copilot_phase_assist",
        _route_after_copilot_phase_assist,
        {"planning": "planning", "assessment": "assessment", "approval": "approval", END: END},
    )
    graph.add_conditional_edges(
        "approval",
        _route_after_approval,
        {"approval_record": "approval_record", END: END},
    )
    graph.add_conditional_edges(
        "approval_record",
        _route_after_approval_record,
        {"sandbox_transform": "sandbox_transform", END: END},
    )
    graph.add_edge("sandbox_transform", "final_report")
    graph.add_conditional_edges(
        "final_report",
        _route_after_final_report,
        {"copilot_final_report": "copilot_final_report", END: END},
    )
    graph.add_edge("copilot_final_report", END)

    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


def _phase_node(
    run_phase: Callable[[MigrationState], MigrationState],
    validate_artifacts: ValidationCallable,
    *,
    phase: str,
    status_key: str,
    artifacts_valid_key: str,
    next_on_pass: str,
):
    def node(state: MigrationState) -> MigrationState:
        requested_phase = state.get("phase")
        if requested_phase and requested_phase != phase:
            return dict(state)  # type: ignore[return-value]

        result = dict(state)
        result.update(run_phase(state))

        validation = validate_artifacts(result)  # type: ignore[arg-type]
        result[artifacts_valid_key] = validation.valid
        result["artifact_refs"] = {
            **dict(result.get("artifact_refs", {}) or {}),
            **validation.artifact_refs,
        }
        result["blockers"] = [
            *list(result.get("blockers", []) or []),
            *validation.blockers,
        ]
        result["warnings"] = [
            *list(result.get("warnings", []) or []),
            *validation.warnings,
        ]
        validation_passed = result.get(status_key) == "PASS" and validation.valid
        result["copilot_assist_phase"] = phase
        result["copilot_route_after_assist"] = next_on_pass if validation_passed else END
        result["copilot_validation_had_warnings"] = bool(validation.warnings)
        return result  # type: ignore[return-value]

    return node


def _route_analysis(state: MigrationState) -> str:
    requested = state.get("phase")
    if requested:
        if requested == "planning":
            return "planning"
        if requested == "assessment":
            return "assessment"
    return _route_after_validation(
        state,
        status_key="analysis_status",
        artifacts_valid_key="analysis_artifacts_valid",
        next_on_pass="planning",
    )


def _route_planning(state: MigrationState) -> str:
    requested = state.get("phase")
    if requested:
        if requested == "assessment":
            return "assessment"
        if requested == "analysis":
            return END
    return _route_after_validation(
        state,
        status_key="planning_status",
        artifacts_valid_key="planning_artifacts_valid",
        next_on_pass="assessment",
    )


def _route_assessment(state: MigrationState) -> str:
    requested = state.get("phase")
    if requested:
        if requested in ("analysis", "planning"):
            return END
    return _route_after_validation(
        state,
        status_key="assessment_status",
        artifacts_valid_key="assessment_artifacts_valid",
        next_on_pass="approval",
    )


def _route_after_validation(
    state: MigrationState,
    *,
    status_key: str,
    artifacts_valid_key: str,
    next_on_pass: str,
) -> str:
    validation_passed = state.get(status_key) == "PASS" and state.get(artifacts_valid_key) is True
    deterministic_route = next_on_pass if validation_passed else END
    if _should_route_to_copilot_assist(state, validation_passed=validation_passed):
        return "copilot_phase_assist"
    return deterministic_route


def _should_route_to_copilot_assist(state: MigrationState, *, validation_passed: bool) -> bool:
    return False


def _route_after_copilot_phase_assist(state: MigrationState) -> str:
    route = state.get("copilot_route_after_assist")
    return str(route) if route in {"planning", "assessment", "approval"} else END


def _route_after_approval(state: MigrationState) -> str:
    if state.get("mode") == FULL_SANDBOX_MIGRATION_MODE and state.get("approval_status") == "COMPLETED":
        return "approval_record"
    return END


def _route_after_approval_record(state: MigrationState) -> str:
    if (
        state.get("mode") == FULL_SANDBOX_MIGRATION_MODE
        and state.get("approval_status") == "COMPLETED"
        and state.get("approval_decision") == "approved"
        and not state.get("errors")
        and state.get("orchestration_status") != "FAIL"
    ):
        return "sandbox_transform"
    return END


def _deterministic_final_report_node(state: MigrationState) -> MigrationState:
    emit_control_tower_event(phase="final_report", status="running", message="Final report generation started.")
    result = finalize_orchestration_state(state)
    emit_control_tower_event(phase="final_report", status="completed", message="Final report written.")
    return result


def _route_after_final_report(state: MigrationState) -> str:
    return END
