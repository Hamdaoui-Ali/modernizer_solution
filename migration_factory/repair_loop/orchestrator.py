from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from migration_factory.repair_loop.evidence_collector import collect_failure_evidence
from migration_factory.repair_loop.fallback_planner import generate_deterministic_fallback
from migration_factory.repair_loop.ledger import (
    append_attempt,
    base_attempt,
    new_ledger,
    write_ledger,
    write_patch_attempt_result,
)
from migration_factory.repair_loop.patch_apply import apply_patch_to_sandbox, rollback_patch
from migration_factory.repair_loop.patch_gate import evaluate_patch_proposal
from migration_factory.repair_loop.validation_runner import ValidationResult, run_validation_after_patch


CopilotInvoker = Callable[..., dict[str, Any]]
ValidationRunner = Callable[..., ValidationResult]


def run_post_failure_repair_loop(
    state: dict[str, Any],
    *,
    h2_startup_report: dict[str, Any] | None = None,
    copilot_invoker: CopilotInvoker | None = None,
    validation_runner: ValidationRunner = run_validation_after_patch,
) -> dict[str, Any]:
    if not bool(state.get("copilot_failure_agent_enabled") or state.get("repair_loop_enabled")):
        return {
            "repair_loop_status": "DISABLED",
            "repair_loop_enabled": False,
        }
    return {
        "repair_loop_status": "BLOCKED",
        "repair_loop_enabled": False,
        "repair_loop_quarantined": True,
        "repair_blocker": "copilot_removed_from_v2_f5",
        "repair_message": (
            "Legacy Copilot repair loop is quarantined. F5 repair proposals must be "
            "created through the Azure proposer/reviewer reviewed-diff chain."
        ),
    }

    run_id = str(state.get("run_id") or "")
    run_dir = Path(str(state.get("run_dir") or ""))
    artifact_refs = dict(state.get("artifact_refs", {}) or {})
    max_attempts = int(state.get("repair_max_attempts") or 3)
    auto_apply = bool(state.get("auto_apply_safe_repairs", False))
    h2_status = str((h2_startup_report or {}).get("h2_status") or state.get("h2_startup_status") or "")

    def state_updates(**kwargs: Any) -> dict[str, Any]:
        updates = _state_updates(**kwargs)
        if h2_status:
            updates["h2_startup_status"] = h2_status
        return updates

    ledger = new_ledger(
        run_id=run_id,
        enabled=True,
        auto_apply_enabled=auto_apply,
        max_attempts=max_attempts,
        artifact_refs=artifact_refs,
    )
    ledger_ref = write_ledger(run_dir, ledger)
    artifact_refs["repair_ledger"] = str(ledger_ref)

    availability = _load_or_probe_copilot_availability(state, run_dir, artifact_refs)
    classification, classification_path, request = collect_failure_evidence(
        run_id=run_id,
        run_dir=run_dir,
        sandbox_path=str(state.get("sandbox_path") or "") or None,
        artifact_refs=artifact_refs,
        transform_log_path=str(state.get("transform_log_path") or ""),
        build_status=str(state.get("build_status") or ""),
        test_status=str(state.get("test_status") or ""),
        h2_startup_report=h2_startup_report,
    )
    request_path = run_dir / "failures" / "copilot_repair_request.json"
    artifact_refs["failure_classification"] = str(classification_path)
    artifact_refs["copilot_repair_request"] = str(request_path)

    if availability.get("status") != "AVAILABLE":
        reason = str(availability.get("reason") or "Copilot unavailable")
        rejected_ref = _write_rejected_copilot_response(
            run_dir,
            status="UNAVAILABLE",
            reason=reason,
            raw=availability,
        )
        artifact_refs["copilot_repair_response"] = str(rejected_ref)
        attempt = base_attempt(
            attempt=1,
            failure_type=str(classification.get("failure_type") or "UNKNOWN_MIGRATION_FAILURE"),
            classification_ref=str(classification_path),
            copilot_request_ref=str(request_path),
            copilot_response_ref=str(rejected_ref),
        )
        attempt["status"] = "FALLBACK_WRITTEN"
        fallback = _write_fallback(
            run_dir=run_dir,
            run_id=run_id,
            ledger=ledger,
            attempt=attempt,
            artifact_refs=artifact_refs,
            classification=classification,
            classification_path=classification_path,
            h2_startup_report=h2_startup_report,
            raw_copilot_output=availability,
            fallback_reason="COPILOT_UNAVAILABLE",
            availability=availability,
            h2_status=h2_status,
        )
        ledger.setdefault("warnings", []).append(reason)
        ledger.setdefault("errors", []).extend(str(error) for error in list(availability.get("errors", []) or []))
        write_ledger(run_dir, ledger)
        return fallback

    sandbox_path = str(state.get("sandbox_path") or "")
    legacy_path = str(state.get("legacy_app_path") or "")
    repeated_hashes: set[str] = set()
    failed_file_sets: dict[tuple[str, ...], int] = {}

    for attempt_number in range(1, max_attempts + 1):
        attempt = base_attempt(
            attempt=attempt_number,
            failure_type=str(classification.get("failure_type") or "UNKNOWN_MIGRATION_FAILURE"),
            classification_ref=str(classification_path),
            copilot_request_ref=str(request_path),
        )

        invocation = copilot_invoker(
            repo_root=Path(__file__).resolve().parents[2],
            run_dir=run_dir,
            run_id=run_id,
            request_payload=request,
            availability=availability,
            model=str(state.get("copilot_model") or ""),
            timeout_seconds=int(state.get("copilot_timeout_seconds") or 300),
            strict_containment=bool(state.get("copilot_repair_strict_containment", True)),
        )
        invocation_refs = dict(invocation.get("artifact_refs", {}) or {})
        invocation_status = _normalized_invocation_status(str(invocation.get("status") or "COMPLETED"))
        artifact_refs.update(invocation_refs)
        attempt["copilot_response_ref"] = invocation_refs.get("copilot_repair_response", "")
        attempt["repair_plan_ref"] = invocation_refs.get("repair_plan", "")

        response = _read_json(attempt["copilot_response_ref"])
        if invocation_status == "READ_TOOL_UNAVAILABLE":
            attempt["status"] = "FALLBACK_WRITTEN"
            ledger.setdefault("warnings", []).append("Copilot repair proposal skipped because safe evidence read mode is unavailable.")
            return _write_fallback(
                run_dir=run_dir,
                run_id=run_id,
                ledger=ledger,
                attempt=attempt,
                artifact_refs=artifact_refs,
                classification=classification,
                classification_path=classification_path,
                h2_startup_report=h2_startup_report,
                raw_copilot_output=response,
                fallback_reason="COPILOT_READ_TOOL_UNAVAILABLE",
                availability=availability,
                h2_status=h2_status,
            )

        if not response or response.get("status") == "FAILED":
            attempt["status"] = "FALLBACK_WRITTEN"
            ledger.setdefault("errors", []).append("Copilot response was invalid or rejected by schema validation")
            return _write_fallback(
                run_dir=run_dir,
                run_id=run_id,
                ledger=ledger,
                attempt=attempt,
                artifact_refs=artifact_refs,
                classification=classification,
                classification_path=classification_path,
                h2_startup_report=h2_startup_report,
                raw_copilot_output=response,
                fallback_reason="COPILOT_INVALID_RESPONSE",
                availability=availability,
                h2_status=h2_status,
            )

        proposals = [proposal for proposal in response.get("patch_proposals", []) if isinstance(proposal, dict)]
        if bool(response.get("security_review_required", False)):
            attempt["patch_gate_status"] = "HUMAN_REVIEW_REQUIRED"
            attempt["status"] = "BLOCKED"
            append_attempt(ledger, attempt)
            ledger["artifact_refs"] = artifact_refs
            ledger["final_status"] = "REPAIR_BLOCKED_HUMAN_REVIEW"
            write_ledger(run_dir, ledger)
            return state_updates(
                artifact_refs=artifact_refs,
                ledger=ledger,
                final_status="REPAIR_BLOCKED_HUMAN_REVIEW",
                copilot_invocation_status=invocation_status,
                safe_patch_applied=False,
                human_review=True,
                failure_classified=True,
                availability=availability,
            )

        if not proposals or not auto_apply:
            attempt["status"] = "PROPOSAL_WRITTEN"
            append_attempt(ledger, attempt)
            ledger["artifact_refs"] = artifact_refs
            ledger["final_status"] = "PROPOSAL_ONLY"
            write_ledger(run_dir, ledger)
            return state_updates(
                artifact_refs=artifact_refs,
                ledger=ledger,
                final_status="PROPOSAL_ONLY",
                copilot_invocation_status=invocation_status,
                safe_patch_applied=False,
                human_review=False,
                failure_classified=True,
                availability=availability,
            )

        selected, gate = _select_allowed_proposal(
            proposals=proposals,
            sandbox_path=sandbox_path,
            run_dir=run_dir,
            legacy_path=legacy_path,
            failure_classification=classification,
            h2_required=bool(state.get("h2_startup_required", False)),
        )
        attempt["patch_gate_status"] = gate.status
        attempt["deterministic_rule_id"] = gate.rule_id
        if gate.human_review_required:
            attempt["status"] = "BLOCKED"
            append_attempt(ledger, attempt)
            ledger["artifact_refs"] = artifact_refs
            ledger["final_status"] = "REPAIR_BLOCKED_HUMAN_REVIEW"
            ledger.setdefault("warnings", []).append(gate.reason)
            write_ledger(run_dir, ledger)
            return state_updates(
                artifact_refs=artifact_refs,
                ledger=ledger,
                final_status="REPAIR_BLOCKED_HUMAN_REVIEW",
                copilot_invocation_status=invocation_status,
                safe_patch_applied=False,
                human_review=True,
                failure_classified=True,
                availability=availability,
            )
        if selected is None:
            attempt["status"] = "BLOCKED"
            append_attempt(ledger, attempt)
            ledger["artifact_refs"] = artifact_refs
            ledger["final_status"] = "PROPOSAL_ONLY"
            ledger.setdefault("warnings", []).append(gate.reason)
            write_ledger(run_dir, ledger)
            return state_updates(
                artifact_refs=artifact_refs,
                ledger=ledger,
                final_status="PROPOSAL_ONLY",
                copilot_invocation_status=invocation_status,
                safe_patch_applied=False,
                human_review=False,
                failure_classified=True,
                availability=availability,
            )

        diff = str(selected.get("unified_diff") or "")
        patch_hash = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        if patch_hash in repeated_hashes:
            attempt["status"] = "BLOCKED"
            append_attempt(ledger, attempt)
            ledger["final_status"] = "REPAIR_FAILED"
            ledger.setdefault("warnings", []).append("repeated patch proposal detected")
            write_ledger(run_dir, ledger)
            return state_updates(
                artifact_refs=artifact_refs,
                ledger=ledger,
                final_status="REPAIR_FAILED",
                copilot_invocation_status=invocation_status,
                safe_patch_applied=False,
                human_review=False,
                failure_classified=True,
                availability=availability,
            )
        repeated_hashes.add(patch_hash)

        apply_result = apply_patch_to_sandbox(
            run_dir=run_dir,
            sandbox_path=sandbox_path,
            attempt=attempt_number,
            unified_diff=diff,
            touched_paths=list(gate.touched_paths),
        )
        attempt["patch_ref"] = str(apply_result.patch_path)
        if apply_result.status != "APPLIED":
            result_path = write_patch_attempt_result(
                run_dir=run_dir,
                run_id=run_id,
                attempt=attempt_number,
                status=apply_result.status,
                reason=apply_result.reason,
                rule_id=gate.rule_id,
                risk=gate.risk,
                paths=apply_result.touched_paths,
                before_hashes=apply_result.before_hashes,
                errors=apply_result.errors,
            )
            attempt["patch_result_ref"] = str(result_path)
            attempt["status"] = "FAILED"
            append_attempt(ledger, attempt)
            ledger["final_status"] = "REPAIR_FAILED"
            ledger["artifact_refs"] = artifact_refs
            write_ledger(run_dir, ledger)
            return state_updates(
                artifact_refs=artifact_refs,
                ledger=ledger,
                final_status="REPAIR_FAILED",
                copilot_invocation_status=invocation_status,
                safe_patch_applied=False,
                human_review=False,
                failure_classified=True,
                availability=availability,
            )

        validation = validation_runner(
            run_id=run_id,
            run_dir=run_dir,
            sandbox_path=sandbox_path,
            attempt=attempt_number,
            h2_required=bool(state.get("h2_startup_required", False)),
            h2_enabled=bool(state.get("h2_startup_required", False)),
        )
        attempt["validation"] = {
            "build_status": validation.build_status,
            "test_status": validation.test_status,
            "h2_status": validation.h2_status,
        }
        artifact_refs.update(validation.artifact_refs)

        if validation.passed:
            result_path = write_patch_attempt_result(
                run_dir=run_dir,
                run_id=run_id,
                attempt=attempt_number,
                status="APPLIED",
                reason="patch applied and validation passed",
                rule_id=gate.rule_id,
                risk=gate.risk,
                paths=apply_result.touched_paths,
                before_hashes=apply_result.before_hashes,
                after_hashes=apply_result.after_hashes,
                validation_commands=validation.validation_commands,
                warnings=validation.warnings,
            )
            attempt["patch_result_ref"] = str(result_path)
            attempt["status"] = "VALIDATED"
            append_attempt(ledger, attempt)
            ledger["final_status"] = "REPAIR_VALIDATED"
            ledger["artifact_refs"] = artifact_refs
            write_ledger(run_dir, ledger)
            return {
                    **state_updates(
                    artifact_refs=artifact_refs,
                    ledger=ledger,
                    final_status="REPAIR_VALIDATED",
                    copilot_invocation_status=invocation_status,
                    safe_patch_applied=True,
                    human_review=False,
                    failure_classified=True,
                    availability=availability,
                ),
                "build_status": validation.build_status,
                "test_status": validation.test_status,
                "h2_startup_status": validation.h2_status,
                "orchestration_status": "PASS",
            }

        rolled_back, rollback_reason = rollback_patch(
            sandbox_path=sandbox_path,
            snapshot_dir=apply_result.snapshot_dir,
            touched_paths=apply_result.touched_paths,
            created_paths=apply_result.created_paths,
        )
        attempt["rollback"] = {
            "performed": True,
            "reason": "; ".join(validation.errors) or "validation failed",
            "status": "ROLLED_BACK" if rolled_back else "ROLLBACK_FAILED",
        }
        result_path = write_patch_attempt_result(
            run_dir=run_dir,
            run_id=run_id,
            attempt=attempt_number,
            status="ROLLED_BACK" if rolled_back else "FAILED",
            reason=rollback_reason,
            rule_id=gate.rule_id,
            risk=gate.risk,
            paths=apply_result.touched_paths,
            before_hashes=apply_result.before_hashes,
            after_hashes=apply_result.after_hashes,
            validation_commands=validation.validation_commands,
            warnings=validation.warnings,
            errors=validation.errors,
        )
        attempt["patch_result_ref"] = str(result_path)
        attempt["status"] = "ROLLED_BACK" if rolled_back else "FAILED"
        append_attempt(ledger, attempt)
        if not rolled_back:
            ledger["final_status"] = "REPAIR_FAILED"
            ledger.setdefault("errors", []).append("rollback failed after repair validation failure")
            ledger["artifact_refs"] = artifact_refs
            write_ledger(run_dir, ledger)
            return state_updates(
                artifact_refs=artifact_refs,
                ledger=ledger,
                final_status="REPAIR_FAILED",
                copilot_invocation_status=invocation_status,
                safe_patch_applied=True,
                human_review=False,
                failure_classified=True,
                availability=availability,
            )

        file_set = tuple(sorted(apply_result.touched_paths))
        failed_file_sets[file_set] = failed_file_sets.get(file_set, 0) + 1
        if failed_file_sets[file_set] >= 2:
            ledger["final_status"] = "REPAIR_FAILED"
            ledger.setdefault("warnings", []).append("same file set failed validation twice")
            ledger["artifact_refs"] = artifact_refs
            write_ledger(run_dir, ledger)
            return state_updates(
                artifact_refs=artifact_refs,
                ledger=ledger,
                final_status="REPAIR_FAILED",
                copilot_invocation_status=invocation_status,
                safe_patch_applied=True,
                human_review=False,
                failure_classified=True,
                availability=availability,
            )

        ledger["artifact_refs"] = artifact_refs
        write_ledger(run_dir, ledger)

    ledger["final_status"] = "MAX_ATTEMPTS_REACHED"
    ledger["artifact_refs"] = artifact_refs
    write_ledger(run_dir, ledger)
    return state_updates(
        artifact_refs=artifact_refs,
        ledger=ledger,
        final_status="MAX_ATTEMPTS_REACHED",
        copilot_invocation_status="COMPLETED",
        safe_patch_applied=False,
        human_review=False,
        failure_classified=True,
        availability=availability,
    )


def _select_allowed_proposal(
    *,
    proposals: list[dict[str, Any]],
    sandbox_path: str,
    run_dir: Path,
    legacy_path: str,
    failure_classification: dict[str, Any],
    h2_required: bool,
):
    last_gate = None
    for proposal in proposals:
        gate = evaluate_patch_proposal(
            proposal=proposal,
            sandbox_path=sandbox_path,
            run_dir=run_dir,
            legacy_path=legacy_path,
            failure_classification=failure_classification,
            h2_required=h2_required,
        )
        last_gate = gate
        if gate.status == "ALLOWED":
            return proposal, gate
        if gate.human_review_required:
            return None, gate
    return None, last_gate or evaluate_patch_proposal(
        proposal={},
        sandbox_path=sandbox_path,
        run_dir=run_dir,
        legacy_path=legacy_path,
        failure_classification=failure_classification,
        h2_required=h2_required,
    )


def _state_updates(
    *,
    artifact_refs: dict[str, str],
    ledger: dict[str, Any],
    final_status: str,
    copilot_invocation_status: str,
    safe_patch_applied: bool,
    human_review: bool,
    failure_classified: bool,
    availability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    availability = availability or {}
    return {
        "repair_loop_enabled": True,
        "repair_loop_status": final_status,
        "repair_attempts_count": len(list(ledger.get("attempts", []) or [])),
        "repair_safe_patch_applied": safe_patch_applied,
        "repair_human_review_required": human_review,
        "copilot_invocation_status": copilot_invocation_status,
        "copilot_availability_status": str(availability.get("status") or "SKIPPED"),
        "copilot_feature_probe": availability,
        "failure_classification_status": "COMPLETED" if failure_classified else "PENDING",
        "artifact_refs": artifact_refs,
        "repair_fallback_generated": bool(ledger.get("fallback_generated", False)),
        "final_status": final_status,
        "final_proof_level": "not_verified" if final_status not in {"REPAIR_VALIDATED"} else "runtime_smoke_passed",
    }


def _normalized_invocation_status(status: str) -> str:
    if status in {"CALLED", "USED"}:
        return "COMPLETED"
    return status


def _read_json(path_value: Any) -> dict[str, Any]:
    if not path_value:
        return {}
    try:
        payload = json.loads(Path(str(path_value)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_fallback(
    *,
    run_dir: Path,
    run_id: str,
    ledger: dict[str, Any],
    attempt: dict[str, Any],
    artifact_refs: dict[str, str],
    classification: dict[str, Any],
    classification_path: Path,
    h2_startup_report: dict[str, Any] | None,
    raw_copilot_output: Any,
    fallback_reason: str,
    availability: dict[str, Any],
    h2_status: str,
) -> dict[str, Any]:
    fallback = generate_deterministic_fallback(
        run_dir=run_dir,
        run_id=run_id,
        failure_classification=classification,
        failure_classification_path=classification_path,
        h2_startup_report=h2_startup_report,
        artifact_refs=artifact_refs,
        raw_copilot_output=raw_copilot_output,
        fallback_reason=fallback_reason,
        auto_apply=False,
    )
    artifact_refs.update(dict(fallback.get("artifact_refs", {}) or {}))
    attempt["deterministic_fallback_response_ref"] = str(fallback.get("deterministic_fallback_response_ref") or "")
    attempt["deterministic_fallback_plan_ref"] = str(fallback.get("deterministic_fallback_plan_ref") or "")
    append_attempt(ledger, attempt)
    ledger["fallback_generated"] = True
    ledger["fallback_reason"] = fallback_reason
    ledger["final_status"] = "FALLBACK_REPAIR_PLAN"
    ledger["deterministic_fallback_response_ref"] = str(fallback.get("deterministic_fallback_response_ref") or "")
    ledger["deterministic_fallback_plan_ref"] = str(fallback.get("deterministic_fallback_plan_ref") or "")
    ledger["copilot_response_ref"] = str(fallback.get("copilot_response_ref") or artifact_refs.get("copilot_repair_response", ""))
    ledger["artifact_refs"] = artifact_refs
    write_ledger(run_dir, ledger)
    updates = _state_updates(
        artifact_refs=artifact_refs,
        ledger=ledger,
        final_status="FALLBACK_REPAIR_PLAN",
        copilot_invocation_status="INVALID_RESPONSE",
        safe_patch_applied=False,
        human_review=False,
        failure_classified=True,
        availability=availability,
    )
    updates["repair_fallback_generated"] = True
    updates["final_status"] = "FALLBACK_REPAIR_PLAN"
    updates["final_proof_level"] = "not_verified"
    if h2_status:
        updates["h2_startup_status"] = h2_status
    return updates


def _write_rejected_copilot_response(
    run_dir: Path,
    *,
    status: str,
    reason: str,
    raw: Any,
) -> Path:
    path = run_dir / "failures" / "copilot_repair_response.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0.0",
        "status": "FAILED",
        "copilot_status": status,
        "reason": reason,
        "raw": raw,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _load_or_probe_copilot_availability(
    state: dict[str, Any],
    run_dir: Path,
    artifact_refs: dict[str, str],
) -> dict[str, Any]:
    availability = dict(state.get("copilot_feature_probe", {}) or {})
    state_status = str(state.get("copilot_availability_status") or "")
    if availability.get("status") and availability.get("status") != "SKIPPED" and state_status != "SKIPPED":
        return availability

    path = run_dir / "preflight" / "copilot_availability.json"
    artifact_refs["copilot_availability"] = str(path)
    from_file = _read_json(path)
    if from_file.get("status") and from_file.get("status") != "SKIPPED":
        return from_file

    return {
        "status": "UNAVAILABLE",
        "reason": "Legacy Copilot repair loop is quarantined for V2/F5.",
        "errors": ["copilot_removed_from_v2_f5"],
    }
