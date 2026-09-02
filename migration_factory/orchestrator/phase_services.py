from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from migration_factory.orchestrator.state import MigrationState
from migration_factory.orchestrator.events import emit_control_tower_event
from migration_factory.orchestrator.timing import record_phase_duration
from migration_factory.contracts.migration import LedgerError, load_ledger


PhaseCallable = Callable[[MigrationState], MigrationState]


@dataclass(frozen=True)
class PhaseServices:
    run_analysis_phase: PhaseCallable
    run_planning_phase: PhaseCallable
    run_assessment_phase: PhaseCallable


def default_phase_services() -> PhaseServices:
    return PhaseServices(
        run_analysis_phase=run_analysis_phase,
        run_planning_phase=run_planning_phase,
        run_assessment_phase=run_assessment_phase,
    )


def run_analysis_phase(state: MigrationState) -> MigrationState:
    return _run_phase(
        state,
        phase="analysis",
        status_key="analysis_status",
        service=_run_analysis_service,
    )


def run_planning_phase(state: MigrationState) -> MigrationState:
    return _run_phase(
        state,
        phase="planning",
        status_key="planning_status",
        service=_run_planning_service,
    )


def run_assessment_phase(state: MigrationState) -> MigrationState:
    return _run_phase(
        state,
        phase="assessment",
        status_key="assessment_status",
        service=_run_assessment_service,
    )


def record_approval_decision_phase(state: MigrationState) -> MigrationState:
    from migration_factory.approval.approve_run import record_approval_decision_for_run

    decision = state.get("approval_decision")
    approved_by = state.get("approved_by") or "human"
    run_id = state.get("run_id", "")
    run_dir = Path(state.get("run_dir", ""))
    if decision not in {"approved", "rejected", "replan_required"}:
        message = f"Cannot record approval decision: {decision!r}"
        emit_control_tower_event(phase="approval", status="failed", message=message)
        return _with_phase_failure(
            state,
            phase="approval",
            status_key="orchestration_status",
            message=message,
        )

    emit_control_tower_event(phase="approval", status="running", message="Recording approval decision.")
    started = time.monotonic()
    try:
        result = record_approval_decision_for_run(
            run_dir=run_dir,
            run_id=run_id,
            decided_by=approved_by,
            decision=decision,
            comments=state.get("approval_comments", ""),
            source="orchestrator_resume",
        )
    except Exception as exc:
        emit_control_tower_event(phase="approval", status="failed", message=f"approval recording failed: {exc}")
        return _with_phase_failure(
            state,
            phase="approval",
            status_key="orchestration_status",
            message=f"approval recording failed: {exc}",
        )
    record_phase_duration(state, phase="approval_resume", duration_seconds=time.monotonic() - started)

    artifact_refs = dict(state.get("artifact_refs", {}) or {})
    artifact_refs["approval_decision"] = str(result.approval_decision)
    if result.approved_plan_lock is not None:
        artifact_refs["approved_plan_lock"] = str(result.approved_plan_lock)

    stop_reason = state.get("stop_reason")
    final_status = state.get("final_status", "")
    if decision != "approved":
        stop_reason = f"Approval decision '{decision}' recorded; stopping."
        final_status = decision.upper()

    emit_control_tower_event(phase="approval", status="completed", message="Approval decision recorded.")
    return {
        "approval_status": "COMPLETED",
        "approval_decision": decision,
        "current_phase": "approval",
        "orchestration_status": "PASS",
        "artifact_refs": artifact_refs,
        "stop_reason": stop_reason,
        "final_status": final_status,
    }


def run_sandbox_transform_phase(state: MigrationState) -> MigrationState:
    from migration_factory.transform_v1_after_approval import (
        STATUS_APPLIED,
        apply_approved_sandbox_transform,
    )
    from migration_factory.control_tower.application.v2_profile_runtime import (
        resolve_runtime_profile_for_state,
    )

    emit_control_tower_event(
        phase="sandbox_transform",
        status="running",
        message="Sandbox transform started.",
    )
    started = time.monotonic()
    try:
        runtime_profile = resolve_runtime_profile_for_state(state)
        result = apply_approved_sandbox_transform(
            run_dir=Path(state.get("run_dir", "")),
            legacy_app=Path(state.get("legacy_app_path", "")),
            modernized_app=Path(state.get("modernized_app_path", "")),
            ai_hub=state.get("ai_hub_path", ""),
            profile=runtime_profile,
            approved_by=state.get("approved_by") or "human",
            quiet=True,
            status_writer=None,
            error_writer=None,
        )
    except Exception as exc:
        emit_control_tower_event(
            phase="sandbox_transform",
            status="failed",
            message=f"sandbox transform failed: {exc}",
        )
        return _with_phase_failure(
            state,
            phase="sandbox_transform",
            status_key="orchestration_status",
            message=f"sandbox transform failed: {exc}",
        )
    record_phase_duration(state, phase="sandbox_transform", duration_seconds=time.monotonic() - started)

    artifact_refs = dict(state.get("artifact_refs", {}) or {})
    if result.generated_plan is not None:
        artifact_refs["transformation_execution_plan"] = str(result.generated_plan)
    if result.plugin_xml is not None:
        artifact_refs["openrewrite_plugin_xml"] = str(result.plugin_xml)
    if result.ledger_file is not None:
        artifact_refs["migration_ledger"] = str(result.ledger_file)
    artifact_refs["phase2_log"] = str(result.log_file)
    if result.test_report_path is not None:
        artifact_refs["post_transform_test_report"] = str(result.test_report_path)
    if result.test_summary_path is not None:
        artifact_refs["post_transform_test_summary"] = str(result.test_summary_path)
    if result.test_log_path is not None:
        artifact_refs["post_transform_test_log"] = str(result.test_log_path)
    for key, value in dict(result.dependency_policy_artifact_refs or {}).items():
        artifact_refs[key] = value
    if result.dependency_policy_report_path is not None:
        artifact_refs["dependency_policy_report"] = str(result.dependency_policy_report_path)
    if result.dependency_policy_summary_path is not None:
        artifact_refs["dependency_policy_summary"] = str(result.dependency_policy_summary_path)
    validation_context = dict(result.validation_execution_context or {})
    build_validation: dict[str, Any] = {}
    if result.ledger_file is not None:
        try:
            ledger = load_ledger(result.ledger_file)
            candidate = ledger.get("build_validation")
            if isinstance(candidate, dict):
                build_validation = dict(candidate)
        except (LedgerError, OSError, json.JSONDecodeError):
            build_validation = {}
    if not validation_context.get("validation_command") and build_validation.get("command"):
        validation_context["validation_command"] = list(build_validation["command"])
    if not validation_context.get("working_directory") and build_validation.get("cwd"):
        validation_context["working_directory"] = str(build_validation["cwd"])
    if not validation_context.get("validation_unit_id") and build_validation.get("unit_id"):
        validation_context["validation_unit_id"] = str(build_validation["unit_id"])
    for key in ("source_profile", "target_profile"):
        if not validation_context.get(key) and state.get(key):
            validation_context[key] = state[key]
    if not validation_context.get("runtime_profile"):
        validation_context["runtime_profile"] = runtime_profile

    if result.exit_code != 0 or result.status != STATUS_APPLIED or result.sandbox_path is None:
        message = result.message or f"sandbox transform failed with status {result.status}"
        emit_control_tower_event(
            phase="sandbox_transform",
            status="failed",
            message=message,
            build_status=result.build_status or "",
            test_status=result.test_status or "",
        )
        failed = _with_phase_failure(
            state,
            phase="sandbox_transform",
            status_key="orchestration_status",
            message=message,
        )
        failed.update(
            {
                "transform_status": result.status,
                "build_status": result.build_status or "",
                "test_status": result.test_status or "",
                "test_totals": dict(result.test_totals or {}),
                "test_report_path": str(result.test_report_path or ""),
                "test_summary_path": str(result.test_summary_path or ""),
                "test_log_path": str(result.test_log_path or ""),
                "test_phase": result.test_phase or "",
                "dependency_policy_status": result.dependency_policy_status,
                "dependency_policy_risks_count": result.dependency_policy_risks_count,
                "dependency_policy_blockers_count": result.dependency_policy_blockers_count,
                "copilot_dependency_advisory_status": result.copilot_dependency_advisory_status,
                "policy_patch_applied": result.policy_patch_applied,
                "sandbox_path": str(result.sandbox_path or ""),
                "validation_execution_context": validation_context,
                "build_validation": build_validation,
                "transform_log_path": str(result.log_file),
                "artifact_refs": artifact_refs,
                "profile_id": runtime_profile,
                "final_status": result.status,
                "stop_reason": message,
                "timing": _merged_timing_state(Path(state.get("run_dir", "")), state),
            }
        )
        return _merge_repair_updates(failed)

    h2_updates: dict[str, Any] = {
        "h2_startup_status": "H2_STARTUP_SKIPPED",
        "final_proof_level": "unit_tests_passed" if result.test_status == "TEST_PASSED" else "compiled",
    }
    if state.get("h2_startup_required") is True:
        from migration_factory.agents.h2_runtime_startup_agent import (
            build_h2_startup_report,
            write_h2_startup_report,
        )

        h2_report = build_h2_startup_report(
            run_id=state.get("run_id", ""),
            run_dir=Path(state.get("run_dir", "")),
            sandbox_path=result.sandbox_path,
            required=True,
        )
        h2_path = write_h2_startup_report(run_dir=Path(state.get("run_dir", "")), report=h2_report)
        artifact_refs["h2_startup_report"] = str(h2_path)
        h2_updates.update(
            {
                "h2_startup_status": h2_report.get("h2_status", "H2_STARTUP_FAILED"),
                "final_proof_level": h2_report.get("proof_level", "not_verified"),
                "runtime_security_warnings": list(h2_report.get("security_env_warnings", []) or []),
            }
        )
        if h2_report.get("h2_status") == "H2_STARTUP_FAILED":
            failed = _with_phase_failure(
                state,
                phase="h2_runtime_startup",
                status_key="orchestration_status",
                message="required H2 runtime startup failed",
            )
            failed.update(
                {
                    "transform_status": result.status,
                    "build_status": result.build_status or "",
                    "test_status": result.test_status or "",
                    "test_totals": dict(result.test_totals or {}),
                    "sandbox_path": str(result.sandbox_path),
                    "validation_execution_context": validation_context,
                    "build_validation": build_validation,
                    "transform_log_path": str(result.log_file),
                    "artifact_refs": artifact_refs,
                    "final_status": "H2_STARTUP_FAILED",
                    "stop_reason": "required H2 runtime startup failed",
                    **h2_updates,
                }
            )
            return _merge_repair_updates(failed, h2_startup_report=h2_report)

    emit_control_tower_event(
        phase="sandbox_transform",
        status="completed",
        message="Sandbox transform completed.",
        sandbox_path=str(result.sandbox_path),
        build_status=result.build_status or "",
        test_status=result.test_status or "",
    )
    return {
        "current_phase": "sandbox_transform",
        "orchestration_status": "PASS",
        "transform_status": result.status,
        "build_status": result.build_status or "",
        "test_status": result.test_status or "",
        "test_totals": dict(result.test_totals or {}),
        "test_report_path": str(result.test_report_path or ""),
        "test_summary_path": str(result.test_summary_path or ""),
        "test_log_path": str(result.test_log_path or ""),
        "test_phase": result.test_phase or "",
        "dependency_policy_status": result.dependency_policy_status,
        "dependency_policy_risks_count": result.dependency_policy_risks_count,
        "dependency_policy_blockers_count": result.dependency_policy_blockers_count,
        "copilot_dependency_advisory_status": result.copilot_dependency_advisory_status,
        "policy_patch_applied": result.policy_patch_applied,
        "sandbox_path": str(result.sandbox_path),
        "validation_execution_context": validation_context,
        "build_validation": build_validation,
        "transform_log_path": str(result.log_file),
        "artifact_refs": artifact_refs,
        "profile_id": runtime_profile,
        "final_status": STATUS_APPLIED,
        "stop_reason": result.message,
        "timing": _merged_timing_state(Path(state.get("run_dir", "")), state),
        **h2_updates,
    }


def _merge_repair_updates(
    failed_state: MigrationState,
    *,
    h2_startup_report: dict[str, Any] | None = None,
) -> MigrationState:
    result: MigrationState = dict(failed_state)
    artifact_refs = dict(result.get("artifact_refs", {}) or {})
    result["artifact_refs"] = artifact_refs
    result["repair_loop_status"] = "REPAIR_REVIEW_REQUIRED"
    result["repair_blocker"] = "f5_reviewed_repair_required"
    result["final_status"] = "REPAIR_REVIEW_REQUIRED"
    result["stop_reason"] = (
        "Build/test failure requires F5 reviewed Azure repair chain and "
        "checksum-bound human approval."
    )
    if h2_startup_report is not None:
        result["h2_startup_report_available"] = True
    return result


def _merged_timing_state(run_dir: Path, state: MigrationState) -> dict[str, Any]:
    timing = dict(state.get("timing", {}) or {})
    path = run_dir / "performance" / "timing_state.json"
    if not path.is_file():
        return timing
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return timing
    incoming = payload.get("timing") if isinstance(payload, dict) else None
    if not isinstance(incoming, dict):
        return timing
    merged_phase = dict(timing.get("phase_durations_seconds", {}) or {})
    merged_phase.update(dict(incoming.get("phase_durations_seconds", {}) or {}))
    merged_commands = list(timing.get("commands", []) or []) + list(incoming.get("commands", []) or [])
    return {
        **timing,
        "phase_durations_seconds": merged_phase,
        "commands": merged_commands,
    }


def _run_phase(
    state: MigrationState,
    *,
    phase: str,
    status_key: str,
    service: PhaseCallable,
) -> MigrationState:
    emit_control_tower_event(phase=phase, status="running", message=f"{phase} phase started.")
    running_state = _with_phase_status(state, phase=phase, status_key=status_key, status="RUNNING")
    started = time.monotonic()
    try:
        service_result = service(running_state)
    except Exception as exc:
        message = f"{phase} phase failed: {exc}"
        emit_control_tower_event(phase=phase, status="failed", message=message)
        return _with_phase_failure(
            running_state,
            phase=phase,
            status_key=status_key,
            message=message,
        )

    result = _merge_state(running_state, service_result)
    record_phase_duration(result, phase=phase, duration_seconds=time.monotonic() - started)
    if result.get(status_key) == "FAIL":
        message = f"{phase} phase failed"
        emit_control_tower_event(phase=phase, status="failed", message=message)
        return _ensure_failure_details(
            result,
            phase=phase,
            status_key=status_key,
            message=message,
        )

    result[status_key] = "PASS"  # type: ignore[literal-required]
    result["current_phase"] = phase  # type: ignore[typeddict-unknown-key]
    emit_control_tower_event(phase=phase, status="completed", message=f"{phase} phase completed.")
    return result


def _run_analysis_service(state: MigrationState) -> MigrationState:
    analysis_root = Path(__file__).resolve().parents[1] / "agents" / "analysis_agent" / "analysis_agent"
    sys.path.insert(0, str(analysis_root))
    try:
        context_module = importlib.import_module("context_manager")
        main_module = importlib.import_module("main")
        context = context_module.MigrationContext(
            state.get("run_id", ""),
            state.get("legacy_app_path", ""),
            state.get("modernized_app_path", ""),
            state.get("ai_hub_path") or None,
            state.get("profile_id") or None,
        )
        result = main_module.run_analysis_agent(context)
    finally:
        try:
            sys.path.remove(str(analysis_root))
        except ValueError:
            pass

    errors = list(getattr(result, "errors", []) or [])
    warnings = list(getattr(result, "warnings", []) or [])
    status = "PASS" if getattr(result, "status", "") == "COMPLETED" and not errors else "FAIL"
    artifact_refs = dict(getattr(result, "artifact_paths", {}) or {})
    review_updates: dict[str, Any] = {}
    if status == "PASS" and state.get("job_id"):
        from migration_factory.orchestrator.review_chain import (
            ReviewChainProductionError,
            produce_phase_review_chain,
        )

        try:
            review_updates = produce_phase_review_chain(
                state,
                phase="analysis",
                stage_index=1,
                artifact_refs=artifact_refs,
                deterministic_facts={
                    "assist_status": getattr(result, "assist_status", ""),
                    "rewrite_status": getattr(result, "rewrite_status", ""),
                    "artifact_kinds": sorted(artifact_refs),
                    "warning_count": len(warnings),
                },
                warnings=warnings,
            )
        except ReviewChainProductionError as exc:
            errors.append(str(exc))
            status = "FAIL"
    return {
        "analysis_status": status,
        "current_unit": "analysis",
        "errors": errors,
        "blockers": list(errors),
        "warnings": warnings,
        "artifact_refs": {
            **artifact_refs,
            **dict(review_updates.get("artifact_refs", {}) or {}),
        },
        **({"review_chain": review_updates["review_chain"]} if review_updates.get("review_chain") else {}),
    }


def _run_planning_service(state: MigrationState) -> MigrationState:
    from migration_factory.agents.planning_agent.node import planning_node

    return planning_node(state)


def _run_assessment_service(state: MigrationState) -> MigrationState:
    from migration_factory.assessment import write_assessment_artifacts

    result = write_assessment_artifacts(
        Path(state.get("modernized_app_path", "")),
        state.get("run_id", ""),
    )
    artifact_refs = dict(state.get("artifact_refs", {}) or {})
    artifact_refs.update(
        {
            "assessment_report": str(result.report_path),
            "assessment_summary": str(result.summary_path),
        }
    )
    return {
        "assessment_status": "PASS",
        "current_unit": "assessment",
        "artifact_refs": artifact_refs,
    }


def _with_phase_status(
    state: MigrationState,
    *,
    phase: str,
    status_key: str,
    status: str,
) -> MigrationState:
    result = dict(state)
    result[status_key] = status
    result["current_phase"] = phase
    return result  # type: ignore[return-value]


def _with_phase_failure(
    state: MigrationState,
    *,
    phase: str,
    status_key: str,
    message: str,
) -> MigrationState:
    result = _with_phase_status(state, phase=phase, status_key=status_key, status="FAIL")
    result["errors"] = [*list(state.get("errors", []) or []), message]
    result["blockers"] = [*list(state.get("blockers", []) or []), message]
    result["current_unit"] = phase
    return result


def _ensure_failure_details(
    state: MigrationState,
    *,
    phase: str,
    status_key: str,
    message: str,
) -> MigrationState:
    result = _with_phase_status(state, phase=phase, status_key=status_key, status="FAIL")
    errors = list(result.get("errors", []) or [])
    blockers = list(result.get("blockers", []) or [])
    if not errors:
        errors.append(message)
    if not blockers:
        blockers.append(message)
    result["errors"] = errors
    result["blockers"] = blockers
    result["current_unit"] = phase
    return result


def _merge_state(state: MigrationState, service_result: MigrationState | dict[str, Any]) -> MigrationState:
    result = dict(state)
    result.update(service_result)
    return result  # type: ignore[return-value]
