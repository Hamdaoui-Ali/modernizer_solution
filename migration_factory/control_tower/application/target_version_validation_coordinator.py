"""Asynchronous post-target-version validation coordinator.

This module coordinates durable state and existing validation/repair services.
It intentionally contains no build, test, Maven, patch, environment-selection,
or model logic.
"""

from __future__ import annotations

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from migration_factory.control_tower.application.execution_environment import (
    decode_environment_manifest,
    materialize_execution_environment,
)
from migration_factory.control_tower.domain.checksums import sha256_canonical_json
from migration_factory.repair_loop.validation_runner import (
    ValidationExecutionContext,
    run_validation_after_patch,
)


class TargetVersionValidationCoordinator:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], Any],
        event_sink: Callable[..., None],
        repair_handler: Callable[..., dict[str, Any] | None] | None = None,
    ) -> None:
        self._uow_factory = unit_of_work_factory
        self._event_sink = event_sink
        self._repair_handler = repair_handler
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="target-version-validation")

    def enqueue(self, change_id: str, validation_id: str) -> None:
        self._executor.submit(self.run, change_id, validation_id)

    def recover(self) -> None:
        with self._uow_factory() as uow:
            uow.connection.execute(
                "UPDATE v2_pom_validations SET status = 'validation_queued', completed_at = NULL "
                "WHERE status = 'running' AND change_id IN "
                "(SELECT change_id FROM v2_pom_changes WHERE operation = 'target_version_batch')"
            )
            uow.connection.execute(
                "UPDATE v2_pom_changes SET status = 'validation_queued', updated_at = CURRENT_TIMESTAMP "
                "WHERE operation = 'target_version_batch' AND status = 'validation_running'"
            )
            rows = uow.connection.execute(
                "SELECT change_id, validation_id FROM v2_pom_changes "
                "WHERE operation = 'target_version_batch' "
                "AND status IN ('applied_pending_validation', 'apply_intent_persisted', 'validation_queued') "
                "AND validation_id IS NOT NULL"
            ).fetchall()
        for row in rows:
            self.enqueue(str(row["change_id"]), str(row["validation_id"]))

    def run(self, change_id: str, validation_id: str) -> None:
        with self._uow_factory() as uow:
            change = uow.v2_pom_changes.get_lineage(change_id)
            validation = uow.v2_pom_validations.get(validation_id)
        if not change or not validation:
            return
        if str(change.get("status")) == "apply_intent_persisted":
            self._reconcile_apply_intent(change)
            with self._uow_factory() as uow:
                change = uow.v2_pom_changes.get_lineage(change_id)
            if not change or str(change.get("status")) != "validation_queued":
                return
        if str(validation.get("status")) in {"passed", "failed", "repair_review_required"}:
            return

        self._emit(
            change,
            "target_version_validation_started",
            "running",
            "Target-version validation started.",
            {"change_id": change_id, "validation_id": validation_id},
        )
        self._update_validation(validation_id, status="running")

        try:
            context, execution_env, run_dir, sandbox = self._load_execution_contract(change)
            self._update_lineage(
                change_id,
                validation_context_ref=str(change.get("validation_context_ref") or ""),
                validation_context_checksum=str(change.get("validation_context_checksum") or ""),
            )

            def observe(phase: str, payload: dict[str, Any]) -> None:
                event_type = f"target_version_{phase}"
                persisted = {"diagnosis_json": json.dumps(payload, sort_keys=True)}
                if phase.startswith("build_"):
                    persisted["build_status"] = str(payload.get("build_status") or phase)
                if phase.startswith("test_"):
                    persisted["test_status"] = str(payload.get("test_status") or phase)
                self._update_validation(validation_id, status="running", **persisted)
                event_status = (
                    "failed" if phase.endswith("_failed")
                    else "blocked" if phase.endswith("_blocked")
                    else "passed" if phase.endswith("_passed")
                    else "running"
                )
                self._emit(change, event_type, event_status, f"Target-version {phase.replace('_', ' ')}.", {
                    "change_id": change_id,
                    "validation_id": validation_id,
                    **payload,
                })

            result = run_validation_after_patch(
                run_id=validation_id,
                run_dir=run_dir,
                sandbox_path=sandbox,
                attempt=0,
                h2_required=context.h2_required,
                h2_enabled=context.h2_enabled,
                build_timeout_seconds=context.build_timeout_seconds,
                validation_context=context,
                execution_env=execution_env,
                observer=observe,
            )
            result_payload = {
                "build_status": result.build_status,
                "test_status": result.test_status,
                "h2_status": result.h2_status,
                "artifact_refs": result.artifact_refs,
                "warnings": result.warnings,
                "errors": result.errors,
                "validation_commands": result.validation_commands,
            }
            if result.passed:
                self._update_validation(
                    validation_id,
                    status="passed",
                    exit_code=0,
                    build_status=result.build_status,
                    test_status=result.test_status,
                    diagnosis_json=json.dumps(result_payload, sort_keys=True),
                )
                self._update_lineage(change_id, status="validated")
                self._emit(change, "target_version_validation_passed", "passed", "Target-version validation passed.", {
                    "change_id": change_id, "validation_id": validation_id, **result_payload,
                })
                self._emit(change, "target_version_update_validated", "validated", "Target-version POM update validated.", {
                    "change_id": change_id, "validation_id": validation_id, "lifecycle_action": "complete_target_version_update",
                })
                return

            self._update_validation(
                validation_id,
                status="failed",
                exit_code=1,
                build_status=result.build_status,
                test_status=result.test_status,
                failure_classification="VALIDATION",
                diagnosis_json=json.dumps(result_payload, sort_keys=True),
            )
            # Persist the target-version failure before invoking AMF-252. The
            # handoff opens its own write UoWs and must not run in this UoW.
            self._update_lineage(change_id, status="failed")
            repair = self._handoff_to_repair(change, validation, context, run_dir, sandbox, result)
            self._update_validation(
                validation_id,
                status="repair_review_required" if repair and repair.get("status") == "created" else "failed",
                repair_linkage_json=json.dumps(repair or {}, sort_keys=True),
            )
            final_lineage_status = "repair_review_required" if repair and repair.get("status") == "created" else "failed"
            self._update_lineage(change_id, status=final_lineage_status, repair_linkage_json=json.dumps(repair or {}, sort_keys=True))
            if repair and repair.get("proposal_id"):
                self._update_lineage(change_id, repair_proposal_id=str(repair["proposal_id"]))
            self._emit(change, "target_version_validation_failed", "failed", "Target-version validation failed; modified sandbox preserved.", {
                "change_id": change_id, "validation_id": validation_id, **result_payload,
            })
            if repair and repair.get("status") == "created":
                self._emit(change, "target_version_repair_required", "blocked", "AMF-252 repair review is required.", {
                    "change_id": change_id, "validation_id": validation_id, **repair,
                })
        except Exception as exc:
            self._update_validation(validation_id, status="failed", failure_classification="VALIDATION", diagnosis_json=json.dumps({"error": str(exc)[:1000]}))
            self._update_lineage(change_id, status="failed")
            self._emit(change, "target_version_validation_failed", "failed", "Target-version validation could not run.", {
                "change_id": change_id, "validation_id": validation_id, "reason": str(exc)[:1000],
            })

    def _load_execution_contract(self, change: dict[str, Any]) -> tuple[ValidationExecutionContext, dict[str, str], str, str]:
        command_id = str(change.get("command_id") or "")
        context_ref = str(change.get("validation_context_ref") or "")
        context_checksum = str(change.get("validation_context_checksum") or "")
        if not command_id:
            raise ValueError("authoritative validation command is missing")
        if not context_ref or not Path(context_ref).is_file():
            raise ValueError("authoritative ValidationExecutionContext is missing")
        context_data = json.loads(Path(context_ref).read_text(encoding="utf-8"))
        if not isinstance(context_data, dict):
            raise ValueError("authoritative ValidationExecutionContext is missing")
        actual_checksum = sha256_canonical_json(context_data)
        if not context_checksum or actual_checksum != context_checksum:
            raise ValueError("authoritative ValidationExecutionContext checksum mismatch")
        context = ValidationExecutionContext.from_mapping(context_data)
        with self._uow_factory() as uow:
            command = uow.v2_commands.get(command_id)
        if command is None:
            raise ValueError("authoritative validation command is missing")
        manifest = decode_environment_manifest(command.env_json)
        execution_env = materialize_execution_environment(manifest)
        run_dir = str(context_data.get("run_dir") or "")
        sandbox = str(context.sandbox_path or context_data.get("sandbox_path") or "")
        if not run_dir or not sandbox:
            raise ValueError("authoritative run directory or sandbox is missing")
        return context, execution_env, run_dir, sandbox

    def _handoff_to_repair(self, change: dict[str, Any], validation: dict[str, Any], context: ValidationExecutionContext, run_dir: str, sandbox: str, result: Any) -> dict[str, Any] | None:
        if self._repair_handler is None:
            return None
        return self._repair_handler(change=change, validation=validation, context=context, run_dir=run_dir, sandbox=sandbox, result=result)

    def _update_validation(self, validation_id: str, **fields: Any) -> None:
        with self._uow_factory() as uow:
            uow.v2_pom_validations.update_result(validation_id, **fields)

    def _update_lineage(self, change_id: str, **fields: Any) -> None:
        with self._uow_factory() as uow:
            uow.v2_pom_changes.update_lineage(change_id, **fields)

    def _emit(self, change: dict[str, Any], event_type: str, status: str, message: str, payload: dict[str, Any]) -> None:
        self._event_sink(job_id=str(change["job_id"]), stage=int(change.get("execution_stage_index") or change.get("stage_index") or 0), event_type=event_type, status=status, message=message, payload=payload)

    def _reconcile_apply_intent(self, change: dict[str, Any]) -> None:
        pom_path = Path(str(change.get("pom_path_ref") or ""))
        if not pom_path.is_file():
            return
        actual = hashlib.sha256(pom_path.read_bytes()).hexdigest()
        if actual == str(change.get("after_checksum") or ""):
            with self._uow_factory() as uow:
                uow.v2_pom_changes.update_status(str(change["change_id"]), "validation_queued", validation_id=str(change.get("validation_id") or ""))
        elif actual == str(change.get("before_checksum") or ""):
            with self._uow_factory() as uow:
                uow.v2_pom_changes.update_status(str(change["change_id"]), "apply_failed", validation_id=str(change.get("validation_id") or ""))
                if change.get("validation_id"):
                    uow.v2_pom_validations.update_result(str(change["validation_id"]), status="apply_failed", failure_classification="POM_APPLY")
