import json
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from migration_factory.orchestrator.events import emit_control_tower_event
from migration_factory.orchestrator.state import (
    APPROVAL_DECISION_VALUES,
    FULL_SANDBOX_MIGRATION_MODE,
    MigrationState,
)

DECISION_OPTIONS = ["approved", "rejected", "replan_required"]


def build_approval_payload(state: MigrationState) -> dict[str, Any]:
    summary = {
        key: state[key]
        for key in (
            "analysis_status",
            "planning_status",
            "assessment_status",
            "orchestration_status",
        )
        if key in state
    }

    return {
        "type": "human_approval_required",
        "run_id": state.get("run_id", ""),
        "summary": summary,
        "artifact_refs": dict(state.get("artifact_refs", {})),
        "blockers": list(state.get("blockers", [])),
        "warnings": list(state.get("warnings", [])),
        "decision_options": DECISION_OPTIONS,
    }


def approval_node(state: MigrationState) -> MigrationState:
    _write_interrupt_checkpoint_snapshot(state)
    emit_control_tower_event(
        phase="approval",
        status="blocked",
        message="Human approval required.",
        run_id=state.get("run_id", ""),
    )
    resume_payload = interrupt(build_approval_payload(state))
    decision = (
        resume_payload.get("decision")
        if isinstance(resume_payload, dict)
        else None
    )

    if decision in APPROVAL_DECISION_VALUES:
        stop_reason = f"Approval decision '{decision}' received; stopping."
        if state.get("mode") == FULL_SANDBOX_MIGRATION_MODE and decision == "approved":
            stop_reason = "Approval decision 'approved' received; continuing to sandbox transform."
        result = {
            "approval_status": "COMPLETED",
            "approval_decision": decision,
            "current_phase": "approval",
            "stop_reason": stop_reason,
        }
        if state.get("mode") == FULL_SANDBOX_MIGRATION_MODE or (
            isinstance(resume_payload, dict)
            and ("approved_by" in resume_payload or "comments" in resume_payload)
        ):
            result["approved_by"] = (
                str(resume_payload.get("approved_by") or state.get("approved_by") or "human")
                if isinstance(resume_payload, dict)
                else str(state.get("approved_by") or "human")
            )
            result["approval_comments"] = (
                str(resume_payload.get("comments") or state.get("approval_comments") or "")
                if isinstance(resume_payload, dict)
                else str(state.get("approval_comments") or "")
            )
        return result

    message = f"Invalid approval decision: {decision!r}"
    return {
        "approval_status": "FAILED",
        "approval_decision": None,
        "current_phase": "approval",
        "stop_reason": message,
        "blockers": [*state.get("blockers", []), message],
        "errors": [*state.get("errors", []), message],
    }


def _write_interrupt_checkpoint_snapshot(state: MigrationState) -> None:
    run_dir = state.get("run_dir")
    if not run_dir:
        return
    path = Path(run_dir) / "orchestration" / "approval_interrupt_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_json_safe(dict(state)), indent=2, sort_keys=True), encoding="utf-8")


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
