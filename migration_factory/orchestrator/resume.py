from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from langgraph.types import Command

from migration_factory.orchestrator import graph as graph_module
from migration_factory.orchestrator.checkpointing import default_checkpointer
from migration_factory.orchestrator.phase_services import record_approval_decision_phase
from migration_factory.orchestrator.preflight import build_langgraph_config
from migration_factory.orchestrator.state import (
    APPROVAL_DECISION_VALUES,
    FULL_SANDBOX_MIGRATION_MODE,
)
from migration_factory.orchestrator.summary import finalize_orchestration_state
from migration_factory.orchestrator.timing import start_total_run_timing


class ResumeCliError(ValueError):
    """Raised when an orchestrator run cannot be resumed."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m migration_factory.orchestrator.resume",
        description="Resume a paused migration orchestration after human approval.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--decision", required=True, choices=sorted(APPROVAL_DECISION_VALUES))
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--comments", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        result = resume_orchestration(
            run_id=args.run_id,
            run_dir=Path(args.run_dir),
            decision=args.decision,
            approved_by=args.approved_by,
            comments=args.comments,
        )
    except ResumeCliError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    final_json = json.dumps(_to_json_safe(result), sort_keys=True, separators=(",", ":"))
    sys.stdout.write("CONTROL_TOWER_FINAL_JSON " + final_json + "\n")
    sys.stdout.flush()
    return 0


def resume_orchestration(
    *,
    run_id: str,
    run_dir: Path,
    decision: str,
    approved_by: str,
    comments: str = "",
) -> dict[str, Any]:
    if decision not in APPROVAL_DECISION_VALUES:
        raise ResumeCliError(f"Unsupported approval decision: {decision}")
    if not approved_by:
        raise ResumeCliError("--approved-by is required")

    resolved_run_dir = Path(run_dir).expanduser().resolve()
    explicit_recorded = _record_explicit_approval_decision(
        run_id=run_id,
        run_dir=resolved_run_dir,
        decision=decision,
        approved_by=approved_by,
        comments=comments,
    )
    snapshot_mode = _load_interrupt_snapshot_mode(resolved_run_dir)

    # The approval interrupt snapshot is the authoritative source for resume mode.
    # If the run was paused in read-only mode, do not resume through the checkpointed
    # LangGraph state because it may still reflect an older full-sandbox mode.
    if snapshot_mode and snapshot_mode != FULL_SANDBOX_MIGRATION_MODE:
        snapshot_result = _normalize_resume_result(
            _resume_from_interrupt_snapshot(
                run_id=run_id,
                run_dir=resolved_run_dir,
                decision=decision,
                approved_by=approved_by,
                comments=comments,
            ),
            decision=decision,
            explicit_recorded=explicit_recorded,
        )
        return finalize_orchestration_state(
            _ensure_resume_output_paths(snapshot_result, resolved_run_dir)
        )

    config = build_langgraph_config(run_id)
    graph = graph_module.build_graph(checkpointer=default_checkpointer(resolved_run_dir))

    result = graph.invoke(
        Command(
            resume={
                "decision": decision,
                "approved_by": approved_by,
                "comments": comments,
            }
        ),
        config=config,
    )

    result = _normalize_resume_result(
        _with_explicit_run_paths(dict(result), resolved_run_dir),
        decision=decision,
        explicit_recorded=explicit_recorded,
    )
    result = _ensure_resume_output_paths(result, resolved_run_dir)
    start_total_run_timing(result)

    if decision != "approved" and not (resolved_run_dir / "approval" / "approval_decision.json").is_file():
        snapshot_result = _normalize_resume_result(
            _resume_from_interrupt_snapshot(
                run_id=run_id,
                run_dir=resolved_run_dir,
                decision=decision,
                approved_by=approved_by,
                comments=comments,
            ),
            decision=decision,
            explicit_recorded=explicit_recorded,
        )
        return finalize_orchestration_state(
            _ensure_resume_output_paths(snapshot_result, resolved_run_dir)
        )

    if _resume_completed(result, resolved_run_dir):
        return finalize_orchestration_state(_ensure_resume_output_paths(result, resolved_run_dir))

    snapshot_result = _normalize_resume_result(
        _resume_from_interrupt_snapshot(
            run_id=run_id,
            run_dir=resolved_run_dir,
            decision=decision,
            approved_by=approved_by,
            comments=comments,
        ),
        decision=decision,
        explicit_recorded=explicit_recorded,
    )
    return finalize_orchestration_state(
        _ensure_resume_output_paths(snapshot_result, resolved_run_dir)
    )


def _load_interrupt_snapshot_mode(run_dir: Path) -> str:
    snapshot_path = run_dir / "orchestration" / "approval_interrupt_state.json"
    if not snapshot_path.is_file():
        return ""
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("mode") or "").strip()


def _resume_completed(result: dict[str, Any], run_dir: Path) -> bool:
    if result.get("approval_decision") not in APPROVAL_DECISION_VALUES:
        return False
    if not (run_dir / "approval" / "approval_decision.json").is_file():
        return False
    if result.get("approval_decision") == "approved":
        if result.get("mode") != FULL_SANDBOX_MIGRATION_MODE:
            return True
        return bool(result.get("transform_status"))
    return True


def _resume_from_interrupt_snapshot(
    *,
    run_id: str,
    run_dir: Path,
    decision: str,
    approved_by: str,
    comments: str,
) -> dict[str, Any]:
    snapshot_path = run_dir / "orchestration" / "approval_interrupt_state.json"
    if not snapshot_path.is_file():
        raise ResumeCliError(f"approval interrupt checkpoint not found: {snapshot_path}")

    state = json.loads(snapshot_path.read_text(encoding="utf-8"))
    start_total_run_timing(state)

    if state.get("run_id") != run_id:
        raise ResumeCliError("approval interrupt checkpoint run_id mismatch")

    state["run_dir"] = str(run_dir)
    state = _with_explicit_run_paths(state, run_dir)

    state.update(
        {
            "approval_status": "COMPLETED",
            "approval_decision": decision,
            "approved_by": approved_by,
            "approval_comments": comments,
            "current_phase": "approval",
            "stop_reason": f"Approval decision '{decision}' received; stopping.",
        }
    )

    if decision == "approved" and state.get("mode") == FULL_SANDBOX_MIGRATION_MODE:
        state["stop_reason"] = "Approval decision 'approved' received; continuing to sandbox transform."

    recorded = dict(state)
    recorded.update(record_approval_decision_phase(recorded))
    recorded = _ensure_resume_output_paths(recorded, run_dir)

    if decision != "approved" or recorded.get("errors"):
        return recorded

    if recorded.get("mode") != FULL_SANDBOX_MIGRATION_MODE:
        recorded["stop_reason"] = "Approval decision 'approved' recorded; stopping."
        return recorded

    transformed = dict(recorded)
    transformed.update(graph_module.run_sandbox_transform_phase(transformed))
    return _ensure_resume_output_paths(transformed, run_dir)


def _record_explicit_approval_decision(
    *,
    run_id: str,
    run_dir: Path,
    decision: str,
    approved_by: str,
    comments: str,
) -> dict[str, Any]:
    state = _with_explicit_run_paths(
        {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "approval_decision": decision,
            "approved_by": approved_by,
            "approval_comments": comments,
            "artifact_refs": {},
        },
        run_dir,
    )
    recorded = record_approval_decision_phase(state)
    if recorded.get("errors"):
        raise ResumeCliError("; ".join(str(error) for error in recorded.get("errors", [])))
    return _ensure_resume_output_paths(recorded, run_dir)


def _with_explicit_run_paths(state: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    updated = dict(state)
    updated["run_dir"] = str(run_dir)
    updated["analysis_dir"] = str(run_dir / "analysis")
    updated["planning_dir"] = str(run_dir / "planning")
    updated["assessment_dir"] = str(run_dir / "assessment")
    updated["orchestration_dir"] = str(run_dir / "orchestration")
    return load_copilot_config(updated)


def _normalize_resume_result(
    state: dict[str, Any],
    *,
    decision: str,
    explicit_recorded: dict[str, Any],
) -> dict[str, Any]:
    result = dict(state)
    artifact_refs = dict(result.get("artifact_refs", {}) or {})
    artifact_refs.update(explicit_recorded.get("artifact_refs", {}) or {})
    result["artifact_refs"] = artifact_refs

    if decision != "approved":
        result["stop_reason"] = f"Approval decision '{decision}' recorded; stopping."
        result["final_status"] = decision.upper()
    elif result.get("mode") != FULL_SANDBOX_MIGRATION_MODE:
        result["stop_reason"] = "Approval decision 'approved' recorded; stopping."

    run_dir_text = str(result.get("run_dir") or "")
    if run_dir_text:
        result = _ensure_resume_output_paths(result, Path(run_dir_text))
    return result


def _ensure_resume_output_paths(state: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Keep sandbox_path available after resume/finalize.

    V2 Stage 2/3 progression needs the previous stage's sandbox path. Some
    resume paths preserve it as modernized_app_path or artifact_refs instead of
    top-level sandbox_path, so normalize it here before final JSON is printed.
    """
    updated = dict(state)
    artifact_refs = dict(updated.get("artifact_refs", {}) or {})

    sandbox_path = _first_text(
        updated.get("sandbox_path"),
        updated.get("modernized_app_path"),
        updated.get("output_app_path"),
        artifact_refs.get("sandbox"),
        artifact_refs.get("sandbox_path"),
        artifact_refs.get("modernized_app"),
        artifact_refs.get("modernized_app_path"),
    )

    if not sandbox_path:
        sandbox_path = _existing_candidate(
            run_dir / "sandbox",
            run_dir / "modernized",
            run_dir / "output",
            run_dir / "stage_1_sandbox",
        )

    if sandbox_path:
        updated["sandbox_path"] = sandbox_path
        updated.setdefault("modernized_app_path", sandbox_path)
        artifact_refs.setdefault("sandbox", sandbox_path)
        updated["artifact_refs"] = artifact_refs

    return updated


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _existing_candidate(*paths: Path) -> str:
    for path in paths:
        try:
            if path.exists():
                return str(path)
        except OSError:
            continue
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


def load_copilot_config(state: dict[str, Any]) -> dict[str, Any]:
    return dict(state)


def parse_copilot_config() -> dict[str, Any]:
    return {}


if __name__ == "__main__":
    raise SystemExit(main())
