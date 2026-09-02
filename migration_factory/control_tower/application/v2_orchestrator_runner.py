"""Run V2 backend-owned orchestrator manifests and persist live events."""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from migration_factory.control_tower.application.redaction import (
    redact_model_summary,
    redact_public_value,
)
from migration_factory.control_tower.application.execution_environment import (
    MANIFEST_ENV_KEYS,
    SAFE_ENV_KEYS,
    decode_environment_manifest,
    materialize_execution_environment,
)
from migration_factory.control_tower.application.v2_approval_mapping import V2ApprovalMappingService
from migration_factory.control_tower.application.v2_gate_action_service import V2GateActionService
from migration_factory.control_tower.application.v2_phase_gate_service import V2PhaseGateService
from migration_factory.control_tower.application.v2_stage_progression import (
    TERMINAL_STAGE_INDEX,
    route_to_dict,
)
from migration_factory.control_tower.application.v2_profile_runtime import (
    RouteRuntimeProfileUnavailableError,
    ensure_runtime_profile_available,
    public_runtime_profile_error_message,
    resolve_runtime_profile_for_run_configuration,
)
from migration_factory.control_tower.domain.checksums import sha256_canonical_json
from migration_factory.control_tower.domain.gate_checksum import gate_checksum
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    V2StageCommandRecord,
)
from migration_factory.repair_loop.failure_evidence import (
    FailureSource,
    NormalizedCompilerError,
    build_failure_evidence,
    failure_evidence_to_dict,
)
from migration_factory.repair_loop.repair_context import (
    build_repair_context_pack,
    context_pack_to_dict,
)


UnitOfWorkFactory = Callable[[], Any]


def validation_context_sidecar_path(repo_root: Path, command_id: str) -> Path:
    return Path(repo_root) / ".control_tower" / "contexts" / f"{command_id}.json"

_EVENT_PREFIX = "CONTROL_TOWER_EVENT "
_FINAL_JSON_PREFIX = "CONTROL_TOWER_FINAL_JSON "
_MAX_TEXT = 4096

_SAFE_ENV_KEYS = SAFE_ENV_KEYS
_MANIFEST_ENV_KEYS = MANIFEST_ENV_KEYS

_SECRET_ENV_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "AUTHORIZATION")

_APPROVAL_ACCEPTED_STATUSES = {"approved", "auto_approved"}

_TERMINAL_FAILURES = {
    "BUILD_FAILED_IN_SANDBOX",
    "TEST_FAILED",
    "TEST_FAILED_IN_SANDBOX",
    "FALLBACK_REPAIR_PLAN",
    "TRANSFORM_FAILED",
    "FAILED",
    "FAIL",
}

_SUCCESS_ORCHESTRATION_STATUS = "PASS"
_SUCCESS_FINAL_STATUS = "TRANSFORM_APPLIED_IN_SANDBOX"
_SUCCESS_TRANSFORM_STATUS = "TRANSFORM_APPLIED_IN_SANDBOX"
_SUCCESS_BUILD_STATUS = "BUILD_PASSED_IN_SANDBOX"
_SUCCESS_TEST_STATUSES = {"PASS", "TEST_PASSED", "PASS_WITH_WARNINGS"}

_NON_ACTIVE_REPAIR_STATUSES = {
    "",
    "SKIPPED",
    "PENDING",
    "NOT_IMPLEMENTED",
    "NONE",
    "NO_REPAIR",
}


@dataclass(frozen=True)
class V2OrchestratorStart:
    command_id: str
    job_id: str
    stage_index: int
    pid: int | None
    status: str
    message: str = ""


@dataclass(frozen=True)
class _ResumeValidationResult:
    ok: bool
    reason: str = ""
    stage_index: int = -1


class V2OrchestratorRunner:
    """Launches the persisted runner manifest in a background subprocess."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        notifier: Any | None = None,
        popen_factory: Any = subprocess.Popen,
        cwd: Path | None = None,
        diagnosis_callback: Callable[[str, int, str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._notifier = notifier
        self._popen_factory = popen_factory
        self._cwd = cwd or Path(__file__).resolve().parents[3]
        self._event_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._active_processes: dict[str, Any] = {}
        self._last_stdout_lines: list[str] = []
        self._diagnosis_callback = diagnosis_callback

    def cancel_job(self, job_id: str, *, grace_period_seconds: float = 5.0) -> dict[str, Any]:
        """Terminate the active orchestrator subprocess for a V2 job, if any."""
        with self._process_lock:
            processes = list(self._active_processes.get(job_id, {}).values())

        terminated = False
        for process in processes:
            if process is None or process.poll() is not None:
                continue
            try:
                process.terminate()
                process.wait(timeout=grace_period_seconds)
                terminated = True
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=grace_period_seconds)
                    terminated = True
                except Exception:
                    pass
            except Exception:
                try:
                    if os.name != "nt":
                        os.kill(int(process.pid), signal.SIGTERM)
                        terminated = True
                except Exception:
                    pass

        return {
            "process_found": bool(processes),
            "terminated": terminated,
            "process_count": len(processes),
        }

    def start(self, *, job_id: str, command_id: str) -> V2OrchestratorStart:
        with self._unit_of_work_factory() as uow:
            command = uow.v2_commands.get(command_id)
            if command is None:
                raise ValueError(f"V2 command {command_id!r} not found")
            argv = _load_json_list(command.argv_json)
            env_manifest = _load_json_dict(command.env_json)
            stage_index = command.stage_index
            manifest_checksum = command.manifest_checksum

        # Phase commands (e.g., manifest_checksum="phase:planning")
        # have empty stored argv. Build real argv here.
        command_phase = _resolve_phase_from_checksum(manifest_checksum)
        if command_phase and not argv:
            try:
                argv = _build_phase_argv(
                    self, job_id, command_id, command, stage_index, command_phase,
                )
            except RouteRuntimeProfileUnavailableError as exc:
                message = public_runtime_profile_error_message(exc)
                self._event(
                    job_id=job_id,
                    stage=stage_index,
                    event_type="stage_failed",
                    status="blocked",
                    message=message,
                    payload={
                        "command_id": command_id,
                        "command_phase": command_phase,
                        "error_code": exc.code,
                    },
                )
                raise

        thread = threading.Thread(
            target=self._run_process,
            kwargs={
                "job_id": job_id,
                "command_id": command_id,
                "stage_index": stage_index,
                "argv": argv,
                "env_manifest": env_manifest,
                "command_phase": command_phase,
            },
            name=f"v2-orchestrator-{command_id[:8]}",
            daemon=True,
        )
        thread.start()

        return V2OrchestratorStart(
            command_id=command_id,
            job_id=job_id,
            stage_index=stage_index,
            pid=None,
            status="started",
        )

    def start_resume(self, *, job_id: str, resume_id: str) -> V2OrchestratorStart:
        rejected: _ResumeValidationResult | None = None
        try:
            with self._unit_of_work_factory() as uow:
                resume = uow.v2_approvals.get_resume(resume_id)
                if resume is None:
                    raise ValueError(f"V2 resume command {resume_id!r} not found")
                if resume.job_id != job_id:
                    raise ValueError(f"V2 resume command {resume_id!r} does not belong to job {job_id!r}")

                validation = _validate_resume_checkpoint(uow, job_id=job_id, resume=resume)
                if not validation.ok:
                    rejected = validation
                    stage_index = validation.stage_index
                    argv = []
                    env_manifest = {}
                else:
                    argv = _load_json_list(resume.command_json)
                    stage_index = resume.stage_index
                    env_manifest = _load_env_manifest_for_stage(uow, job_id, stage_index)
                    authoritative_command = _resolve_original_stage_command_for_resume(
                        uow,
                        job_id=job_id,
                        stage_index=stage_index,
                    )
                    if authoritative_command is None:
                        rejected = _ResumeValidationResult(
                            False,
                            "missing_original_stage_command_metadata",
                            stage_index,
                        )
                    else:
                        authoritative_command_id = authoritative_command.command_id
        except sqlite3.OperationalError as exc:
            if _is_sqlite_locked_error(exc):
                return V2OrchestratorStart(
                    command_id=resume_id,
                    job_id=job_id,
                    stage_index=-1,
                    pid=None,
                    status="retrying",
                    message=str(exc),
                )
            raise

        if rejected is not None:
            self._event(
                job_id=job_id,
                stage=rejected.stage_index if rejected.stage_index > 0 else None,
                event_type="resume_rejected",
                status="blocked",
                message="Resume checkpoint validation rejected the request.",
                payload={"resume_id": resume_id, "reason": rejected.reason},
            )
            return V2OrchestratorStart(
                command_id=resume_id,
                job_id=job_id,
                stage_index=rejected.stage_index,
                pid=None,
                status="rejected",
                message=rejected.reason,
            )

        thread = threading.Thread(
            target=self._run_process,
            kwargs={
                "job_id": job_id,
                "command_id": authoritative_command_id,
                "stage_index": stage_index,
                "argv": argv,
                "env_manifest": env_manifest,
                "resume": True,
                "resume_id": resume_id,
            },
            name=f"v2-orchestrator-resume-{resume_id[:8]}",
            daemon=True,
        )
        thread.start()

        return V2OrchestratorStart(
            command_id=resume_id,
            job_id=job_id,
            stage_index=stage_index,
            pid=None,
            status="started",
            message="",
        )

    def _run_process(
        self,
        *,
        job_id: str,
        command_id: str,
        stage_index: int,
        argv: list[str],
        env_manifest: dict[str, Any],
        resume: bool = False,
        resume_id: str | None = None,
        command_phase: str | None = None,
    ) -> None:
        if resume:
            resume_payload = {"command_id": command_id}
            if resume_id:
                resume_payload["resume_id"] = resume_id
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="approval_started",
                status="running",
                message="Approval accepted; orchestrator resume process starting.",
                payload=resume_payload,
            )

        start_payload = {"command_id": command_id}
        if resume_id:
            start_payload["resume_id"] = resume_id
        self._event(
            job_id=job_id,
            stage=stage_index,
            event_type="resume_started" if resume else "stage_started",
            status="running",
            message=f"Stage {stage_index} real orchestrator {'resume ' if resume else ''}started.",
            payload=start_payload,
        )
        command_payload = {"command_id": command_id, "shell": False, "cwd": str(self._cwd)}
        if resume_id:
            command_payload["resume_id"] = resume_id
        self._event(
            job_id=job_id,
            stage=stage_index,
            event_type="command_started",
            status="running",
            message=(
                "Backend-owned approval resume command launched."
                if resume
                else "Backend-owned orchestrator manifest launched."
            ),
            payload=command_payload,
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        try:
            if self._is_job_cancelled(job_id):
                return

            process_env = _build_env(env_manifest)

            process = self._popen_factory(
                _normalized_argv(argv),
                cwd=str(self._cwd),
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )

            with self._process_lock:
                self._active_processes.setdefault(job_id, {})[command_id] = process

            if self._is_job_cancelled(job_id):
                self.cancel_job(job_id)
                return

            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="process_started",
                status="running",
                message="Orchestrator subprocess is running.",
                payload={"command_id": command_id, "pid": getattr(process, "pid", None)},
            )

            out_thread = threading.Thread(
                target=self._read_stream,
                args=(process.stdout, stdout_lines),
                kwargs={
                    "job_id": job_id,
                    "stage_index": stage_index,
                    "command_id": command_id,
                    "stream": "stdout",
                },
                daemon=True,
            )
            err_thread = threading.Thread(
                target=self._read_stream,
                args=(process.stderr, stderr_lines),
                kwargs={
                    "job_id": job_id,
                    "stage_index": stage_index,
                    "command_id": command_id,
                    "stream": "stderr",
                },
                daemon=True,
            )

            out_thread.start()
            err_thread.start()

            exit_code = process.wait()

            out_thread.join(timeout=5)
            err_thread.join(timeout=5)

            self._last_stdout_lines = list(stdout_lines)
            final_json = _extract_final_json("\n".join(stdout_lines))

            self._handle_exit(
                job_id=job_id,
                stage_index=stage_index,
                command_id=command_id,
                exit_code=exit_code,
                result=final_json,
                stderr="\n".join(stderr_lines),
                resume=resume,
                command_phase=command_phase,
            )
        except Exception as exc:
            if not self._is_job_cancelled(job_id):
                self._event(
                    job_id=job_id,
                    stage=stage_index,
                    event_type="stage_failed",
                    status="failed",
                    message=f"Orchestrator launch failed: {exc}",
                    payload={"command_id": command_id},
                )
        finally:
            with self._process_lock:
                processes = self._active_processes.get(job_id)
                if processes is not None:
                    processes.pop(command_id, None)
                    if not processes:
                        self._active_processes.pop(job_id, None)

    def _read_stream(
        self,
        stream_handle: Any,
        captured: list[str],
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        stream: str,
    ) -> None:
        if stream_handle is None:
            return

        for raw_line in stream_handle:
            line = raw_line.rstrip("\r\n")
            captured.append(line)

            if not line:
                continue

            if stream == "stdout" and line.startswith(_EVENT_PREFIX):
                self._event_from_orchestrator(
                    job_id=job_id,
                    stage_index=stage_index,
                    command_id=command_id,
                    line=line[len(_EVENT_PREFIX):],
                )
                continue

            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type=stream,
                status="running",
                message=line,
                payload={"command_id": command_id},
            )

    def _event_from_orchestrator(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        line: str,
    ) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return

        phase = str(payload.get("phase") or "orchestrator")
        status = str(payload.get("status") or "running").lower()
        suffix = _status_suffix(status)

        if phase.startswith("model") or phase == "assistant_model":
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type=f"model_invocation_{suffix}",
                status=status,
                message=str(payload.get("message") or f"{phase} {status}"),
                payload={"command_id": command_id, "source_phase": phase},
            )
            return

        canonical_type = _canonical_event_type(phase, suffix, stage_index=stage_index)

        if phase in ("sandbox_transform", "transform") and suffix == "started":
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="approval_completed",
                status="completed",
                message="Human approval phase complete; sandbox transform has started.",
                payload={"command_id": command_id},
            )

        self._event(
            job_id=job_id,
            stage=stage_index,
            event_type=canonical_type,
            status=status,
            message=str(payload.get("message") or f"{phase} {status}"),
            payload={"command_id": command_id, **payload},
        )

    def _handle_exit(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        exit_code: int,
        result: dict[str, Any] | None,
        stderr: str,
        resume: bool = False,
        command_phase: str | None = None,
    ) -> None:
        if self._is_job_cancelled(job_id):
            return

        raw_stdout = "\n".join(self._last_stdout_lines) if hasattr(self, "_last_stdout_lines") and self._last_stdout_lines else ""
        raw_stderr = stderr

        stdout_tail = _bounded(raw_stdout)
        stderr_tail = _bounded(stderr)
        parse_strategy = "sentinel" if result is not None and "CONTROL_TOWER_FINAL_JSON" in ("".join(getattr(self, "_last_stdout_lines", []))) else "generic_scan"

        compiler_errors = _normalize_compiler_errors(
            stdout_tail=raw_stdout,
            stderr_tail=raw_stderr,
        )

        if exit_code != 0:
            if result is not None:
                self._emit_diagnostic_failure_events(
                    job_id=job_id,
                    stage_index=stage_index,
                    command_id=command_id,
                    result=result,
                )
            payload: dict[str, Any] = {
                "command_id": command_id,
                "exit_code": exit_code,
                "stderr": stderr_tail,
                "stdout_tail": stdout_tail,
                "final_json_found": result is not None,
                "parse_strategy": parse_strategy,
            }
            if result is None:
                payload["result_parse_status"] = "missing_final_json"
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="stage_failed",
                status="failed",
                message=f"Orchestrator exited with code {exit_code}.",
                payload=payload,
            )
            return

        if result is None:
            payload: dict[str, Any] = {
                "command_id": command_id,
                "exit_code": exit_code,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
                "final_json_found": False,
                "parse_strategy": parse_strategy,
            }
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="result_contract_failed",
                status="failed",
                message="Orchestrator result contract could not be parsed.",
                payload=payload,
            )
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="stage_failed",
                status="failed",
                message="Orchestrator completed without a parseable final JSON result. Inspect orchestrator stdout/stderr and orchestration_summary.json. The subprocess exited but Control Tower could not parse its final result contract.",
                payload={
                    "command_id": command_id,
                    "exit_code": exit_code,
                    "stderr": stderr_tail,
                    "stdout_tail": stdout_tail,
                    "final_json_found": False,
                    "parse_strategy": parse_strategy,
                    "result_contract_failed": True,
                },
            )
            return

        result = dict(result)
        sandbox_path = _result_sandbox_path(result)

        if sandbox_path:
            result["sandbox_path"] = sandbox_path

        # Persist the authoritative validation execution context for CSV
        # target-version validation.  The context is written to a sidecar file
        # so that app.py can read it later without mutating append-only
        # v2_stage_commands.
        validation_context = dict(result.get("validation_execution_context") or {})
        if validation_context:
            sandbox = result.get("sandbox_path") or result.get("sandbox_root") or ""
            run_dir = result.get("run_dir") or ""
            validation_context.update({
                "job_id": job_id,
                "command_id": command_id,
                "run_dir": str(run_dir),
                "sandbox_path": str(sandbox or sandbox_path),
                "stage_index": stage_index,
                "route_step_index": result.get("route_step_index", stage_index),
            })
            context_path = validation_context_sidecar_path(self._cwd, command_id)
            contexts_dir = context_path.parent
            contexts_dir.mkdir(parents=True, exist_ok=True)
            context_path.write_text(
                json.dumps(validation_context, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result["_validation_context_ref"] = str(context_path)
            result["_validation_context_checksum"] = sha256_canonical_json(validation_context)

        self._maybe_write_repair_failure_context(
            job_id=job_id,
            stage_index=stage_index,
            command_id=command_id,
            result=result,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            compiler_errors=compiler_errors,
        )

        # ── Phase-specific handling: planning bypasses full-stage proof ──
        if command_phase == "analysis":
            self._emit_artifacts(
                job_id=job_id,
                stage_index=stage_index,
                command_id=command_id,
                result=result,
            )
            orchestration_status = str(result.get("orchestration_status", ""))
            if orchestration_status == "PASS":
                self._handle_reviewed_phase_completed(
                    job_id=job_id,
                    stage_index=stage_index,
                    command_id=command_id,
                    result=result,
                    phase="analysis",
                    gate_phase="analysis_review",
                    required_event_type="analysis_review_required",
                )
            else:
                self._event(
                    job_id=job_id,
                    stage=stage_index,
                    event_type="stage_failed",
                    status="failed",
                    message=f"Analysis phase did not produce valid proof: orchestration_status={orchestration_status}",
                    payload={"command_id": command_id, "orchestration_status": orchestration_status},
                )
            return

        if command_phase == "planning":
            self._emit_artifacts(
                job_id=job_id,
                stage_index=stage_index,
                command_id=command_id,
                result=result,
            )
            orchestration_status = str(result.get("orchestration_status", ""))
            # Planning is a reviewed checkpoint phase, not a transform phase.
            # It must produce accepted reviewed artifacts, but it does not
            # need to emit a sandbox output path.
            if orchestration_status == "PASS":
                self._handle_reviewed_phase_completed(
                    job_id=job_id,
                    stage_index=stage_index,
                    command_id=command_id,
                    result=result,
                    phase="planning",
                    gate_phase="planning_review",
                    required_event_type="planning_review_required",
                )
            else:
                self._emit_diagnostic_failure_events(
                    job_id=job_id,
                    stage_index=stage_index,
                    command_id=command_id,
                    result=result,
                )
                self._event(
                    job_id=job_id,
                    stage=stage_index,
                    event_type="stage_failed",
                    status="failed",
                    message=(
                        f"Planning phase did not produce valid proof: "
                        f"orchestration_status={orchestration_status}"
                    ),
                    payload={
                        "command_id": command_id,
                        "orchestration_status": orchestration_status,
                    },
                )
            return

        self._emit_artifacts(
            job_id=job_id,
            stage_index=stage_index,
            command_id=command_id,
            result=result,
        )
        self._emit_phase_outcome_events(
            job_id=job_id,
            stage_index=stage_index,
            command_id=command_id,
            result=result,
        )
        self._emit_failure_repair_events(
            job_id=job_id,
            stage_index=stage_index,
            command_id=command_id,
            result=result,
        )

        if result.get("status") == "human_approval_required":
            checksum = sha256_canonical_json(result)
            with self._unit_of_work_factory() as uow:
                approval_gate, created_new_gate = _open_or_refresh_approval_review_gate(
                    uow=uow,
                    job_id=job_id,
                    stage_index=stage_index,
                    command_id=command_id,
                    result=result,
                )
                import json as _json
                try:
                    approval_refs = _json.loads(approval_gate.source_artifact_refs_json)
                except (TypeError, _json.JSONDecodeError):
                    approval_refs = []
                approval_gate_checksum = gate_checksum(
                    gate_id=approval_gate.gate_id,
                    job_id=approval_gate.job_id,
                    gate_phase=approval_gate.gate_phase,
                    stage_index=approval_gate.stage_index,
                    source_artifact_checksum=approval_gate.source_artifact_checksum,
                    source_artifact_refs=approval_refs,
                )
                approval_service = V2ApprovalMappingService(uow.v2_approvals)
                pending_cards = [
                    existing_card
                    for existing_card in uow.v2_approvals.list_cards_by_job(job_id)
                    if existing_card.stage_index == stage_index
                    and existing_card.status == "pending"
                    and existing_card.request_checksum == approval_gate_checksum
                ]
                if pending_cards:
                    card = pending_cards[0]
                else:
                    card = approval_service.create_decision_card(
                        job_id=job_id,
                        interrupt_id=str(result.get("run_id") or command_id),
                        request_checksum=approval_gate_checksum,
                        stage_index=stage_index,
                        summary="Pre-transform review required before sandbox transform.",
                    )

                auto_approval_enabled = uow.v2_jobs.get_auto_approval_enabled(job_id)
                auto_resume_id = ""
                auto_decision_id = ""
                auto_approval_error = ""
                print("[approval-mode-read-at-gate-creation]", {
                    "job_id": job_id,
                    "auto_approval_enabled": auto_approval_enabled,
                    "gate_id": approval_gate.gate_id,
                    "stage_id": stage_index,
                })
                print("[approval-gate-created]", {
                    "job_id": job_id,
                    "gate_id": approval_gate.gate_id,
                    "gate_type": approval_gate.gate_phase,
                    "gate_status": approval_gate.gate_status,
                    "checksum_present": bool(approval_gate.source_artifact_checksum),
                })
                if auto_approval_enabled:
                    profile_metadata: dict[str, Any] | None = None
                    route = _current_route_for_job(uow, job_id)
                    if route is not None and route.valid:
                        profile_metadata = route_to_dict(route)
                        profile_metadata["stage_index"] = stage_index
                    if profile_metadata is None:
                        auto_approval_error = "checkpoint_profile_metadata_missing"
                        print("[auto-approval-skipped]", {
                            "job_id": job_id,
                            "gate_id": approval_gate.gate_id,
                            "reason": auto_approval_error,
                        })
                    else:
                        action_service = V2GateActionService(
                            uow.phase_gates,
                            uow.gate_decisions,
                            V2PhaseGateService(uow.phase_gates),
                            revision_repo=uow.artifact_revisions,
                            command_repo=uow.v2_commands,
                        )
                        print("[auto-approval-check]", {
                            "job_id": job_id,
                            "gate_id": approval_gate.gate_id,
                            "auto_approval_enabled": True,
                            "safe_to_approve": True,
                        })
                        print("[auto-approval-calling-manual-path]", {
                            "job_id": job_id,
                            "gate_id": approval_gate.gate_id,
                            "checksum": approval_gate_checksum,
                            "decision_source": "auto_approval",
                        })
                        # Use approve_from_gate — the EXACT same method used by:
                        #   - POST /approvals/{card_id}/approve (Approve button)
                        #   - assistant "confirm checksum <checksum>" command
                        # approve_transformation was previously used but it
                        # requires accepted analysis/plan revision records
                        # that may not exist in real jobs, causing silent
                        # failures (no_accepted_analysis / no_accepted_plan).
                        gate_result = action_service.approve_from_gate(
                            gate_id=approval_gate.gate_id,
                            job_id=job_id,
                            decided_by="system:auto-approval",
                            idempotency_key=f"auto-approval:{approval_gate.gate_id}:{approval_gate_checksum}",
                            expected_gate_checksum=approval_gate_checksum,
                            actor_type="system",
                            profile_metadata=profile_metadata,
                        )
                        if gate_result.status in {"executed", "idempotent"}:
                            run_dir = _result_run_dir(result, cwd=self._cwd)
                            resume = approval_service.approve(
                                card.card_id,
                                approval_gate_checksum,
                                job_id,
                                run_dir=str(run_dir) if run_dir is not None else "",
                            )
                            uow.v2_approvals.update_card_status(card.card_id, "auto_approved")
                            auto_resume_id = resume.resume_id
                            auto_decision_id = gate_result.decision_id
                            print("[auto-approval-applied]", {
                                "job_id": job_id,
                                "gate_id": approval_gate.gate_id,
                                "stage_id": stage_index,
                                "decision_source": "auto_approval",
                            })
                        else:
                            auto_approval_error = gate_result.reason or gate_result.status
                            print("[auto-approval-skipped]", {
                                "job_id": job_id,
                                "gate_id": approval_gate.gate_id,
                                "reason": auto_approval_error,
                            })
            if auto_resume_id:
                self._event(
                    job_id=job_id,
                    stage=stage_index,
                    event_type="approval_auto_approved",
                    status="completed",
                    message="Approval gate auto-approved because Auto Approval is enabled.",
                    payload={
                        "command_id": command_id,
                        "card_id": card.card_id,
                        "gate_id": approval_gate.gate_id,
                        "gate_checksum": approval_gate_checksum,
                        "decision_id": auto_decision_id,
                        "resume_id": auto_resume_id,
                        "approval_mode": "auto",
                        "decision_source": "auto_approval",
                        "reason": "Auto approval enabled for this migration job",
                    },
                )
                print("[workflow-resumed-after-auto-approval]", {
                    "job_id": job_id,
                    "next_phase": "transform",
                    "resume_id": auto_resume_id,
                })
                self.start_resume(job_id=job_id, resume_id=auto_resume_id)
                return

            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="approval_required",
                status="blocked",
                message="Orchestrator paused for human approval review.",
                payload={
                    "command_id": command_id,
                    "card_id": card.card_id,
                    "request_checksum": approval_gate_checksum,
                    "gate_id": approval_gate.gate_id,
                    "gate_checksum": approval_gate_checksum,
                    "gate_created": created_new_gate,
                },
            )
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="stage_blocked_for_approval",
                status="blocked",
                message="Stage is blocked until exact checksum approval-review confirmation.",
                payload={
                    "command_id": command_id,
                    "card_id": card.card_id,
                    "gate_id": approval_gate.gate_id,
                    "gate_checksum": approval_gate_checksum,
                },
            )
            return

        final_status = str(result.get("final_status", ""))
        build_status = str(result.get("build_status", ""))
        test_status = str(result.get("test_status", ""))
        transform_status = str(result.get("transform_status", ""))
        repair_status = str(result.get("repair_loop_status", ""))
        orchestration_status = str(result.get("orchestration_status", ""))

        if _is_terminal_failure_result(result):
            self._emit_diagnostic_failure_events(
                job_id=job_id,
                stage_index=stage_index,
                command_id=command_id,
                result=result,
            )
            terminal_value = (
                final_status
                or build_status
                or test_status
                or transform_status
                or repair_status
                or orchestration_status
                or "FAILED"
            )
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="stage_failed",
                status="failed",
                message=f"Stage {stage_index} real orchestrator completed with terminal failure: {terminal_value}.",
                payload={
                    "command_id": command_id,
                    "final_status": final_status,
                    "build_status": build_status,
                    "test_status": test_status,
                    "transform_status": transform_status,
                    "repair_loop_status": repair_status,
                    "orchestration_status": orchestration_status,
                },
            )
            return

        success_proof = _has_success_proof(result)
        if not success_proof[0]:
            failure = success_proof[1]
            expected_text = (
                failure["expected"]
                if failure["detected"] != "missing" and failure["expected"] not in {"present", "empty"}
                else f"{failure['field']}={failure['expected']}"
            )
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="stage_failed",
                status="failed",
                message=(
                    f"Stage {stage_index} did not produce strict success proof: "
                    f"expected {expected_text}, detected={failure['detected']}."
                ),
                payload={
                    "command_id": command_id,
                    "missing_success_proof": True,
                    "reason": failure["reason"],
                    "proof_failure_field": failure["field"],
                    "proof_expected": failure["expected"],
                    "proof_detected": failure["detected"],
                    "proof_expected_values": failure["expected_values"],
                    "proof_detected_values": failure["detected_values"],
                    "orchestration_status": orchestration_status,
                    "transform_status": transform_status,
                    "build_status": build_status,
                    "test_status": test_status,
                    "final_status": final_status,
                    "sandbox_path": sandbox_path,
                    "errors": result.get("errors", {}),
                    "blockers": result.get("blockers", {}),
                },
            )
            return

        if stage_index in (1, 2):
            with self._unit_of_work_factory() as uow:
                cards = uow.v2_approvals.list_cards_by_job(job_id)

            unapproved = [
                c
                for c in cards
                if c.stage_index == stage_index and c.status not in _APPROVAL_ACCEPTED_STATUSES
            ]

            if unapproved:
                card = unapproved[0]
                self._event(
                    job_id=job_id,
                    stage=stage_index,
                    event_type="stage_blocked_for_approval",
                    status="blocked",
                    message=(
                        f"Stage {stage_index} cannot progress: "
                        f"approval card {card.card_id!r} has status {card.status!r}."
                    ),
                    payload={"command_id": command_id, "card_id": card.card_id},
                )
                return

        self._event(
            job_id=job_id,
            stage=stage_index,
            event_type="proof_updated",
            status="completed",
            message="Orchestrator result parsed into deterministic evidence.",
            payload={"command_id": command_id, "final_status": final_status},
        )
        self._event(
            job_id=job_id,
            stage=stage_index,
            event_type="stage_completed",
            status="completed",
            message=f"Stage {stage_index} real orchestrator completed.",
            payload={
                "command_id": command_id,
                "sandbox_path": sandbox_path,
                "exit_code": exit_code,
            },
        )

        # Terminal Stage 4: emit stage_completed only.
        # migration_completed is deferred to backend governance
        # after terminal gate/artifact acceptance.
        if stage_index == 4:
            return

        if stage_index == 3:
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="stage_completed",
                status="completed",
                message=f"Stage {stage_index} completed.",
                payload={
                    "command_id": command_id,
                    "final_status": final_status,
                },
            )
        else:
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="stage_report_started",
                status="running",
                message=f"Stage {stage_index} report started.",
                payload={"command_id": command_id},
            )
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="stage_report_completed",
                status="completed",
                message=f"Stage {stage_index} report completed.",
                payload={"command_id": command_id, "final_status": final_status},
            )

        if command_phase == "planning":
            self._handle_planning_phase_completed(
                job_id=job_id,
                stage_index=stage_index,
                command_id=command_id,
                result=result,
            )
        else:
            self._auto_queue_next_stage(
                job_id=job_id,
                stage_index=stage_index,
                sandbox_path=sandbox_path,
                command_id=command_id,
                result=result,
            )

    def _emit_phase_outcome_events(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        result: dict[str, Any],
    ) -> None:
        transform_status = str(result.get("transform_status", ""))
        build_status = str(result.get("build_status", ""))
        test_status = str(result.get("test_status", ""))

        if transform_status == "TRANSFORM_APPLIED_IN_SANDBOX":
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="sandbox_transform_completed",
                status="completed",
                message="Sandbox transform completed.",
                payload={"command_id": command_id, "transform_status": transform_status},
            )
        elif _is_failure_status(transform_status):
            transform_payload = {"command_id": command_id, "transform_status": transform_status}
            _add_repair_refs_to_payload(result, transform_payload)
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="sandbox_transform_failed",
                status="failed",
                message=f"Sandbox transform failed: {transform_status}",
                payload=transform_payload,
            )

        if build_status == "BUILD_PASSED_IN_SANDBOX":
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="build_completed",
                status="completed",
                message="Sandbox build completed.",
                payload={"command_id": command_id, "build_status": build_status},
            )
        elif _is_failure_status(build_status):
            build_payload = {"command_id": command_id, "build_status": build_status}
            _add_repair_refs_to_payload(result, build_payload)
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="build_failed",
                status="failed",
                message=f"Sandbox build failed: {build_status}",
                payload=build_payload,
            )

        if test_status in _SUCCESS_TEST_STATUSES:
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="test_completed",
                status="completed",
                message=(
                    "Sandbox tests passed."
                    if test_status in {"PASS", "TEST_PASSED"}
                    else f"Sandbox tests accepted with status: {test_status}."
                ),
                payload={"command_id": command_id, "test_status": test_status},
            )
        elif _is_failure_status(test_status):
            test_payload = {"command_id": command_id, "test_status": test_status}
            _add_repair_refs_to_payload(result, test_payload)
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="test_failed",
                status="failed",
                message=f"Sandbox test validation failed: {test_status}",
                payload=test_payload,
            )

    def _emit_failure_repair_events(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        result: dict[str, Any],
    ) -> None:
        repair_status = str(result.get("repair_loop_status", ""))
        fallback = result.get("repair_fallback_generated")

        if repair_status.upper() not in _NON_ACTIVE_REPAIR_STATUSES:
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="repair_started",
                status="running" if not _is_failure_status(repair_status) else "failed",
                message=f"Repair loop status: {repair_status}",
                payload={"command_id": command_id, "repair_loop_status": repair_status},
            )

        if fallback in (True, "true", "True", 1, "yes"):
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="repair_fallback_generated",
                status="completed",
                message="Fallback repair plan generated.",
                payload={"command_id": command_id},
            )

    def _maybe_write_repair_failure_context(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        result: dict[str, Any],
        stdout_tail: str,
        stderr_tail: str,
        compiler_errors: tuple[NormalizedCompilerError, ...] = (),
    ) -> None:
        build_status = str(result.get("build_status", ""))
        test_status = str(result.get("test_status", ""))
        if _is_failure_status(build_status):
            failure_source = FailureSource.BUILD
        elif _is_failure_status(test_status):
            failure_source = FailureSource.TEST
        else:
            return

        run_dir = _result_run_dir(result, cwd=self._cwd)
        if run_dir is None:
            return

        artifact_refs = result.get("artifact_refs") if isinstance(result.get("artifact_refs"), dict) else {}
        changed_files = tuple(str(path) for path in result.get("changed_files", ()) if str(path).strip()) if isinstance(result.get("changed_files"), (list, tuple)) else ()
        accepted_checksums = tuple(
            str(value)
            for value in (
                result.get("accepted_artifact_checksums")
                if isinstance(result.get("accepted_artifact_checksums"), (list, tuple))
                else ()
            )
            if str(value).strip()
        )
        failure_summary = _first_text(
            result.get("failure_summary"),
            result.get("message"),
            build_status if failure_source == FailureSource.BUILD else test_status,
            result.get("final_status"),
            "Build/test failure",
        )
        evidence = build_failure_evidence(
            failure_source=failure_source,
            stage_index=stage_index,
            job_id=job_id,
            command_id=command_id,
            failure_summary=failure_summary,
            compiler_errors=compiler_errors,
            changed_files=changed_files,
            source_profile=str(result.get("source_profile") or ""),
            target_profile=str(result.get("target_profile") or ""),
            accepted_artifact_checksums=accepted_checksums,
            artifact_refs={str(k): str(v) for k, v in artifact_refs.items() if v},
            diagnostic_metadata={
                str(key): str(value)
                for key, value in (result.get("diagnostic_metadata") or {}).items()
                if value
            } if isinstance(result.get("diagnostic_metadata"), dict) else {},
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            safe_log_preview=_first_text(result.get("safe_log_preview"), stderr_tail, stdout_tail),
        )
        compiler_error_locations: list[tuple[str, int]] = []
        for err in evidence.compiler_errors:
            if err.file_path and err.line > 0:
                compiler_error_locations.append((err.file_path, err.line))

        sandbox_root = str(run_dir / "workspaces" / "sandbox")
        from migration_factory.repair_loop.repair_context import (
            build_bounded_source_context,
            find_relevant_build_context_files,
        )
        validation_context = result.get("validation_execution_context")
        validation_context = validation_context if isinstance(validation_context, dict) else {}
        build_tool = str(validation_context.get("tool") or result.get("build_tool") or "").lower()
        maven_evidence_text = "\n".join((failure_summary, stdout_tail, stderr_tail)).lower()
        maven_evidence = any(marker in maven_evidence_text for marker in ("could not find artifact", "could not resolve artifact"))
        build_context_files = find_relevant_build_context_files(
            sandbox_root=sandbox_root,
            working_directory=str(validation_context.get("working_directory") or result.get("working_directory") or ""),
            module=str(validation_context.get("module") or result.get("module") or ""),
            tool=build_tool or "maven",
        ) if sandbox_root and ("maven" in build_tool or build_tool in {"mvn", "mvnw"} or maven_evidence) else ()
        source_contexts = build_bounded_source_context(
            sandbox_root=sandbox_root,
            compiler_errors=compiler_error_locations or None,
            changed_files=changed_files,
            build_context_files=build_context_files,
            include_full_build_descriptors=bool(build_context_files),
        ) if sandbox_root else ()

        context_pack = build_repair_context_pack(
            failure_evidence=evidence,
            job_id=job_id,
            stage_index=stage_index,
            command_id=command_id,
            source_profile=str(result.get("source_profile") or ""),
            target_profile=str(result.get("target_profile") or ""),
            changed_files=changed_files,
            accepted_artifact_checksums=accepted_checksums,
            source_contexts=source_contexts,
        )

        repair_dir = run_dir / "repairs"
        repair_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = repair_dir / "repair_failure_evidence.json"
        context_path = repair_dir / "repair_context_pack.json"
        validation_context = dict(result.get("validation_execution_context") or {})
        build_validation = result.get("build_validation")
        build_validation = build_validation if isinstance(build_validation, dict) else {}
        build_command = build_validation.get("command") or build_validation.get("resolved_command")
        validation_context.update({
            "job_id": job_id,
            "command_id": command_id,
            "run_dir": str(run_dir),
            "stage_index": stage_index,
            "route_step_index": result.get("route_step_index", stage_index),
            "sandbox_path": str(result.get("sandbox_path") or result.get("sandbox_root") or sandbox_root),
            "validation_command": (
                validation_context.get("validation_command")
                or result.get("validation_command")
                or result.get("command")
                or build_command
                or ()
            ),
            "validation_unit_id": (
                validation_context.get("validation_unit_id")
                or result.get("validation_unit_id")
                or build_validation.get("unit_id")
                or result.get("unit_id")
                or ""
            ),
            "module": validation_context.get("module") or build_validation.get("module") or result.get("module"),
            "main_class": validation_context.get("main_class") or build_validation.get("main_class") or result.get("main_class"),
            "tool": validation_context.get("tool") or build_validation.get("build_tool") or result.get("build_tool") or "",
            "wrapper": validation_context.get("wrapper") or build_validation.get("wrapper") or result.get("wrapper") or "",
            "source_profile": str(result.get("source_profile") or validation_context.get("source_profile") or ""),
            "target_profile": str(result.get("target_profile") or validation_context.get("target_profile") or ""),
            "runtime_profile": str(result.get("runtime_profile") or validation_context.get("runtime_profile") or ""),
            "working_directory": str(result.get("working_directory") or validation_context.get("working_directory") or result.get("sandbox_path") or sandbox_root),
            "source_jdk_home_env": (
                validation_context.get("source_jdk_home_env")
                or result.get("source_jdk_home_env")
                or build_validation.get("source_jdk_home_env")
            ),
            "target_jdk_home_env": (
                validation_context.get("target_jdk_home_env")
                or result.get("target_jdk_home_env")
                or build_validation.get("target_jdk_home_env")
            ),
            "build_timeout_seconds": (
                validation_context.get("build_timeout_seconds")
                if validation_context.get("build_timeout_seconds") is not None
                else result.get("build_timeout_seconds")
            ),
        })
        validation_context_path = repair_dir / "validation_execution_context.json"
        validation_context_path.write_text(
            json.dumps(validation_context, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        evidence_path.write_text(
            json.dumps(failure_evidence_to_dict(evidence), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        context_path.write_text(
            json.dumps(context_pack_to_dict(context_pack), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # Inject internal refs into result for downstream callback
        result["_repair_failure_evidence_ref"] = str(evidence_path)
        result["_repair_context_pack_ref"] = str(context_path)
        result["_repair_run_dir"] = str(run_dir)
        result["_repair_failure_evidence_checksum"] = evidence.content_checksum
        result["_repair_context_pack_checksum"] = context_pack.context_pack_checksum
        result["_repair_validation_context_ref"] = str(validation_context_path)
        result["_repair_validation_context_checksum"] = sha256_canonical_json(validation_context)
        result["_repair_base_repo_state_checksum"] = context_pack.base_repo_state_checksum
        sandbox = result.get("sandbox_path") or result.get("sandbox_root") or ""
        if sandbox:
            result["_repair_sandbox_path"] = str(sandbox)
        result["_repair_h2_required"] = bool(result.get("h2_required") or result.get("h2_startup_required"))

        self._event(
            job_id=job_id,
            stage=stage_index,
            event_type="repair_failure_evidence_written",
            status="completed",
            message="Repair failure evidence artifact written.",
            payload={
                "command_id": command_id,
                "failure_source": evidence.failure_source.value,
                "failure_evidence_ref": _safe_artifact_ref(evidence_path),
                "failure_evidence_checksum": evidence.content_checksum,
                "failure_evidence_artifact_checksum": evidence.artifact_checksum,
            },
        )
        self._event(
            job_id=job_id,
            stage=stage_index,
            event_type="repair_context_pack_written",
            status="completed",
            message="Repair context pack artifact written.",
            payload={
                "command_id": command_id,
                "context_pack_ref": _safe_artifact_ref(context_path),
                "context_pack_checksum": context_pack.context_pack_checksum,
                "failure_evidence_checksum": evidence.content_checksum,
                "base_repo_state_checksum": context_pack.base_repo_state_checksum,
            },
        )

    def _emit_diagnostic_failure_events(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        result: dict[str, Any],
    ) -> None:
        build_status = str(result.get("build_status", ""))
        test_status = str(result.get("test_status", ""))
        final_status = str(result.get("final_status", ""))
        final_proof = str(result.get("final_proof_level", ""))
        transform_status = str(result.get("transform_status", ""))
        fallback = result.get("repair_fallback_generated")

        build_validation = result.get("build_validation") or {}
        build_contract = {
            "matched_line": _str_or_none(build_validation.get("matched_line") or result.get("matched_line")),
            "command": _list_or_none(build_validation.get("command") or result.get("command")),
            "requested_command": _list_or_none(build_validation.get("requested_command") or result.get("requested_command")),
            "resolved_command": _list_or_none(build_validation.get("resolved_command") or result.get("resolved_command")),
            "build_tool": _str_or_none(build_validation.get("build_tool") or result.get("build_tool")),
            "module": _str_or_none(build_validation.get("module") or result.get("module")),
            "main_class": _str_or_none(build_validation.get("main_class") or result.get("main_class")),
            "unit_id": _str_or_none(build_validation.get("unit_id") or result.get("unit_id")),
            "result_kind": _str_or_none(build_validation.get("result_kind") or result.get("result_kind")),
            "message": _str_or_none(build_validation.get("message") or result.get("message")),
            "java_home": _str_or_none(build_validation.get("java_home") or result.get("java_home")),
            "detected_version": _str_or_none(build_validation.get("detected_version") or result.get("detected_version")),
            "required_minimum": _str_or_none(build_validation.get("required_minimum") or result.get("required_minimum")),
        }
        public_contract = {k: v for k, v in build_contract.items() if v is not None}

        if _is_failure_status(build_status):
            build_payload = {
                "command_id": command_id,
                "build_status": build_status,
                "test_status": test_status,
                **public_contract,
            }
            _add_repair_refs_to_payload(result, build_payload)
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="build_failed",
                status="failed",
                message=f"Build result: {build_status}",
                payload=build_payload,
            )
            self._maybe_diagnose(
                job_id=job_id,
                stage_index=stage_index,
                command_id=command_id,
                event_type="build_failed",
                payload=build_payload,
            )

        if _is_failure_status(test_status):
            test_payload = {
                "command_id": command_id,
                "test_status": test_status,
                **public_contract,
            }
            _add_repair_refs_to_payload(result, test_payload)
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="test_failed",
                status="failed",
                message=f"Test result: {test_status}",
                payload=test_payload,
            )
            self._maybe_diagnose(
                job_id=job_id,
                stage_index=stage_index,
                command_id=command_id,
                event_type="test_failed",
                payload=test_payload,
            )

        if _is_failure_status(final_status) or _is_failure_status(transform_status):
            transform_payload = {
                "command_id": command_id,
                "final_status": final_status,
                "transform_status": transform_status,
                "final_proof_level": final_proof,
                "build_status": build_status,
                "repair_fallback_generated": bool(fallback),
                **public_contract,
            }
            _add_repair_refs_to_payload(result, transform_payload)
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="transform_failed",
                status="failed",
                message=f"Transform/build failed: {final_status or transform_status}",
                payload=transform_payload,
            )
            self._maybe_diagnose(
                job_id=job_id,
                stage_index=stage_index,
                command_id=command_id,
                event_type="transform_failed",
                payload=transform_payload,
            )

    def _maybe_diagnose(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Route failure events to the optional diagnosis callback (F02).

        Only called when a diagnosis_callback was injected at construction.
        The callback is expected to have signature:
            (job_id, stage_index, command_id, event_type, payload) -> None
        """
        if self._diagnosis_callback is not None:
            try:
                self._diagnosis_callback(
                    job_id, stage_index, command_id, event_type, payload
                )
            except Exception:
                # Diagnosis is advisory — never let a diagnosis failure
                # block the orchestrator event loop.
                pass

    def _auto_queue_next_stage(
        self,
        *,
        job_id: str,
        stage_index: int,
        sandbox_path: str,
        command_id: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        from migration_factory.control_tower.application.v2_stage_progression import (
            V2StageProgressionService,
            route_to_dict,
        )
        from migration_factory.control_tower.application.v2_phase_gate_service import (
            CreateGateRequest,
            V2PhaseGateService,
        )
        from migration_factory.control_tower.schemas.run_configuration import (
            RunPolicy,
            StageContinuationPolicy,
        )

        next_stage = stage_index + 1
        queued_target_stage = next_stage
        next_command_id: str | None = None

        try:
            with self._unit_of_work_factory() as uow:
                job = uow.v2_jobs.get(job_id)
                if job is None:
                    return
                command = uow.v2_commands.get(command_id)
                command_from_resume = False
                if command is None:
                    resume = uow.v2_approvals.get_resume(command_id)
                    if resume is None:
                        self._save_stage_progression_blocked(
                            uow,
                            job_id=job_id,
                            stage_index=stage_index,
                            next_stage=next_stage,
                            reason="missing_command_metadata",
                            command_id=command_id,
                        )
                        return
                    command_from_resume = True
                    if resume.job_id != job_id or int(resume.stage_index) != int(stage_index):
                        self._save_stage_progression_blocked(
                            uow,
                            job_id=job_id,
                            stage_index=stage_index,
                            next_stage=next_stage,
                            reason="resume_command_mismatch",
                            command_id=command_id,
                        )
                        return
                    command = _resolve_original_stage_command_for_resume(
                        uow,
                        job_id=job_id,
                        stage_index=stage_index,
                    )
                if command is None:
                    self._save_stage_progression_blocked(
                        uow,
                        job_id=job_id,
                        stage_index=stage_index,
                        next_stage=next_stage,
                        reason="missing_original_stage_command_metadata",
                        command_id=command_id,
                    )
                    return

                # Load stage continuation policy from run configuration
                raw_policy = StageContinuationPolicy.AUTO_ON_GREEN
                run_config = uow.run_configurations.get_for_job(job_id)
                source_profile = "springboot-2.7-java11"
                target_profile = "springboot-4.0-java21"
                if run_config is not None:
                    if run_config.policy_json:
                        try:
                            import json
                            policy_dict = json.loads(run_config.policy_json)
                            policy = RunPolicy(**policy_dict)
                            raw_policy = policy.stage_continuation_policy
                        except (json.JSONDecodeError, Exception):
                            pass
                    if run_config.payload_json:
                        try:
                            import json
                            payload = json.loads(run_config.payload_json)
                            if payload.get("source_profile"):
                                source_profile = str(payload["source_profile"])
                            if payload.get("target_profile"):
                                target_profile = str(payload["target_profile"])
                        except (json.JSONDecodeError, Exception):
                            pass

                # Resolve effective policy:
                # For MANUAL_ON_WARNING_OR_FAILURE, check if the result has
                # warnings. Only block on warnings/failures; clean green
                # results auto-progress like AUTO_ON_GREEN.
                effective_policy = raw_policy
                if raw_policy == StageContinuationPolicy.MANUAL_ON_WARNING_OR_FAILURE:
                    has_warnings = _result_has_warnings(result) if result else False
                    if not has_warnings:
                        effective_policy = StageContinuationPolicy.AUTO_ON_GREEN
                    else:
                        effective_policy = StageContinuationPolicy.MANUAL

                service = V2StageProgressionService(
                    setup_repo=uow.v2_setups,
                    command_repo=uow.v2_commands,
                    artifact_revision_repo=uow.artifact_revisions,
                    run_config_repo=uow.run_configurations,
                )
                route = service.compute_route_for_job(job_id, run_config)
                source_profile = route.source_profile
                target_profile = route.target_profile
                current_route_step_index = _resolve_route_step_index_for_command(
                    command=command,
                    result=result,
                    route=route,
                )
                if command_from_resume and route.route_steps and current_route_step_index is None:
                    self._save_stage_progression_blocked(
                        uow,
                        job_id=job_id,
                        stage_index=stage_index,
                        next_stage=next_stage,
                        reason="missing_route_step_index",
                        command_id=command_id,
                    )
                    return
                queued = service.queue_next_stage(
                    job_id=job_id,
                    setup_id=job.setup_id,
                    current_stage=stage_index,
                    sandbox_path=sandbox_path,
                    stage_continuation_policy=effective_policy,
                    current_stage_result=result,
                    profile_route=route,
                    current_route_step_index=current_route_step_index,
                )
                queued_target_stage = queued.to_stage

                if queued.status == "completed":
                    route_payload = route_to_dict(route) if route.valid else {}
                    uow.v2_events.save(
                        job_id=job_id,
                        stage=stage_index,
                        event_type="migration_completed",
                        status="completed",
                        message=(
                            f"Selected target profile '{target_profile}' reached. "
                            "Migration completed."
                        ),
                        payload={
                            "from_stage": stage_index,
                            "to_stage": queued_target_stage,
                            "reason": queued.reason,
                            "route": route_payload,
                            "command_id": command_id,
                        },
                    )
                    return

                # Handle blocked policy or target_reached
                if queued.status == "blocked":
                    import json
                    from migration_factory.control_tower.domain.gate_checksum import (
                        gate_checksum,
                    )

                    # Compute source artifact checksum from result if available
                    source_checksum = ""
                    artifact_refs: tuple[str, ...] = ()
                    if result is not None:
                        from migration_factory.control_tower.domain.checksums import (
                            sha256_canonical_json,
                        )
                        source_checksum = sha256_canonical_json(result)
                        refs_dict = result.get("artifact_refs") if isinstance(result.get("artifact_refs"), dict) else {}
                        ref_values = [str(v) for v in refs_dict.values() if v and isinstance(v, str)]
                        if sandbox_path:
                            ref_values.append(sandbox_path)
                        artifact_refs = tuple(sorted(ref_values))

                    # Determine event type and gate phase based on reason + stage
                    reason = queued.reason
                    if reason == "target_reached":
                        gate_phase = "stage_completion_review"
                        event_type = "target_reached"
                        phase_label = "target"
                    elif reason == "profile_incompatible":
                        gate_phase = "stage_completion_review"
                        event_type = "profile_incompatible"
                        phase_label = "profile"
                    elif stage_index == 1:
                        gate_phase = "analysis_review"
                        event_type = "analysis_review_required"
                        phase_label = "analysis"
                    elif stage_index == 2:
                        gate_phase = "planning_review"
                        event_type = "planning_review_required"
                        phase_label = "planning"
                    else:
                        gate_phase = "stage_completion_review"
                        event_type = "stage_completion_review_required"
                        phase_label = "stage"

                    gate_service = V2PhaseGateService(
                        gate_repo=uow.phase_gates,
                    )
                    gate_result = gate_service.create_gate(CreateGateRequest(
                        job_id=job_id,
                        gate_phase=gate_phase,
                        stage_index=stage_index,
                        source_artifact_checksum=source_checksum,
                        source_artifact_refs=artifact_refs,
                        created_by="system",
                    ))

                    uow.v2_events.save(
                        job_id=job_id,
                        stage=stage_index,
                        event_type="f15_gate_opened",
                        status="open",
                        message=(
                            f"{gate_phase} gate opened for stage {stage_index}"
                        ),
                        payload={
                            "gate_id": gate_result.gate_id,
                            "gate_checksum": gate_result.gate_checksum,
                            "gate_phase": gate_phase,
                            "stage_index": stage_index,
                        },
                    )

                    route_payload = route_to_dict(route) if route.valid else {}
                    if reason == "target_reached":
                        target_msg = (
                            f"Target profile '{target_profile}' reached at stage {stage_index}. "
                            f"No further stages will execute."
                        )
                    elif reason == "profile_incompatible":
                        target_msg = (
                            f"Source profile '{source_profile}' and target profile "
                            f"'{target_profile}' form an incompatible pair. "
                            f"Stage progression is blocked: {route.reason}"
                        )
                    else:
                        target_msg = (
                            f"Stage {stage_index} ({phase_label}) completed under manual policy. "
                            f"{gate_phase} gate review required before "
                            f"stage {queued_target_stage} can start."
                        )
                    uow.v2_events.save(
                        job_id=job_id,
                        stage=stage_index,
                        event_type=event_type,
                        status="blocked",
                        message=target_msg,
                        payload={
                            "from_stage": stage_index,
                            "to_stage": queued_target_stage,
                            "gate_id": gate_result.gate_id,
                            "gate_checksum": gate_result.gate_checksum,
                            "gate_status": gate_result.status,
                            "reason": reason,
                            "route": route_payload,
                        },
                    )
                    return

                uow.v2_events.save(
                    job_id=job_id,
                    stage=queued_target_stage,
                    event_type="next_stage_queued",
                    status="queued",
                    message=(
                        f"Stage {queued_target_stage} route step command manifest queued "
                        "for real orchestrator execution."
                    ),
                    payload={
                        "from_stage": stage_index,
                        "to_stage": queued_target_stage,
                        "sandbox_path": sandbox_path,
                        "route": route_to_dict(route) if route.valid else {},
                    },
                )

                next_command_id = _queued_command_id(queued)

                if not next_command_id:
                    commands = uow.v2_commands.list_by_job(job_id)
                    stage_commands = [
                        cmd
                        for cmd in commands
                        if int(getattr(cmd, "stage_index", 0)) == queued_target_stage
                    ]
                    if stage_commands:
                        next_command_id = str(getattr(stage_commands[-1], "command_id", ""))

        except ValueError as exc:
            try:
                with self._unit_of_work_factory() as uow:
                    uow.v2_events.save(
                        job_id=job_id,
                        stage=next_stage,
                        event_type="stage_progression_blocked",
                        status="blocked",
                        message=f"Stage {next_stage} was not queued: {exc}",
                        payload={
                            "from_stage": stage_index,
                            "to_stage": next_stage,
                            "reason": str(exc),
                        },
                    )
            except Exception:
                pass
            return

        if next_command_id and not self._stage_has_started(job_id=job_id, stage_index=queued_target_stage):
            self.start(job_id=job_id, command_id=next_command_id)

    def _save_stage_progression_blocked(
        self,
        uow: Any,
        *,
        job_id: str,
        stage_index: int,
        next_stage: int,
        reason: str,
        command_id: str,
    ) -> None:
        uow.v2_events.save(
            job_id=job_id,
            stage=stage_index,
            event_type="stage_progression_blocked",
            status="blocked",
            message=f"Stage {next_stage} was not queued: {reason}",
            payload={
                "from_stage": stage_index,
                "to_stage": next_stage,
                "reason": reason,
                "command_id": command_id,
            },
        )

    def _stage_has_started(self, *, job_id: str, stage_index: int) -> bool:
        with self._unit_of_work_factory() as uow:
            events = uow.v2_events.list_by_job(job_id)
        started_event_types = {"stage_started", "process_started", "resume_started"}
        return any(
            event.stage == stage_index and event.type in started_event_types
            for event in events
        )

    def _emit_artifacts(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        result: dict[str, Any],
    ) -> None:
        refs = result.get("artifact_refs") if isinstance(result.get("artifact_refs"), dict) else {}
        for kind, path in refs.items():
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="artifact_written",
                status="completed",
                message=f"Artifact written: {kind}",
                payload={
                    "command_id": command_id,
                    "artifact_kind": str(kind),
                    "relative_path": _safe_artifact_ref(path),
                },
            )

        sandbox_path = _result_sandbox_path(result)
        if sandbox_path:
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="artifact_written",
                status="completed",
                message="Stage sandbox output registered.",
                payload={
                    "command_id": command_id,
                    "artifact_kind": "sandbox",
                    "relative_path": _safe_artifact_ref(sandbox_path),
                },
            )

    def _event(
        self,
        *,
        job_id: str,
        stage: int | None,
        event_type: str,
        status: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._event_lock:
            if (
                event_type not in {"migration_cancelling", "stage_cancelled", "migration_cancelled"}
                and self._is_job_cancelled(job_id)
            ):
                return

            with self._unit_of_work_factory() as uow:
                redacted_payload = redact_public_value(payload or {})
                uow.v2_events.save(
                    job_id=job_id,
                    stage=stage,
                    event_type=event_type,
                    status=status,
                    message=_bounded(str(redact_public_value(message))),
                    payload=redacted_payload if isinstance(redacted_payload, dict) else {},
                )

        if self._notifier is not None:
            asyncio.run(self._notifier.notify())

    def _is_job_cancelled(self, job_id: str) -> bool:
        try:
            with self._unit_of_work_factory() as uow:
                events = uow.v2_events.list_by_job(job_id)
        except sqlite3.Error:
            return False
        return any(event.type == "migration_cancelled" for event in events)

    # ── planning phase completion ──────────────────────────────────

    def _handle_planning_phase_completed(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        result: dict[str, Any] | None,
    ) -> None:
        """Handle completion of a planning-phase subprocess.

        Collects planning artifact refs/checksums and creates a
        planning_review gate bound to those real artifacts.
        Does NOT queue Stage 2.

        The command record is append-only and cannot be updated.
        Completion is tracked via events and the planning_review gate.
        """
        if result is None:
            return

        self._handle_reviewed_phase_completed(
            job_id=job_id,
            stage_index=stage_index,
            command_id=command_id,
            result=result,
            phase="planning",
            gate_phase="planning_review",
            required_event_type="planning_review_required",
        )
        return

    def _handle_reviewed_phase_completed(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        result: dict[str, Any],
        phase: str,
        gate_phase: str,
        required_event_type: str,
    ) -> None:
        from migration_factory.control_tower.application.v2_review_chain_contracts import (
            validate_runtime_review_chain_result,
        )

        failures = validate_runtime_review_chain_result(
            result,
            phase=phase,
            stage_index=stage_index,
            expected_job_id=job_id,
        )
        if failures:
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="reviewer_failed",
                status="failed",
                message=f"{phase.title()} review chain failed closed.",
                payload={"command_id": command_id, "failures": failures},
            )
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="stage_failed",
                status="failed",
                message=f"{phase.title()} phase missing accepted checksum-bound reviewer output.",
                payload={"command_id": command_id, "review_chain_failed": True},
            )
            return

        self._event(
            job_id=job_id,
            stage=stage_index,
            event_type=f"{phase}_started",
            status="running",
            message=f"{phase.title()} phase started.",
            payload={"command_id": command_id},
        )
        self._event(
            job_id=job_id,
            stage=stage_index,
            event_type=f"{phase}_completed",
            status="completed",
            message=f"{phase.title()} phase completed.",
            payload={"command_id": command_id},
        )

        # Collect reviewed phase artifact refs from orchestrator result.
        phase_artifacts: dict[str, str] = {}
        artifact_refs = result.get("artifact_refs") if isinstance(result.get("artifact_refs"), dict) else {}
        if artifact_refs:
            phase_artifacts = {str(k): str(v) for k, v in artifact_refs.items() if v}

        # Compute source checksum from result (reviewed output evidence)
        from migration_factory.control_tower.domain.checksums import sha256_canonical_json
        source_checksum = sha256_canonical_json(result)

        # Build artifact refs tuple
        ref_values = list(phase_artifacts.values())
        sandbox_path = _result_sandbox_path(result)
        if sandbox_path and sandbox_path not in ref_values:
            ref_values.append(sandbox_path)
        artifact_refs_tuple = tuple(sorted(ref_values))

        # Emit artifact written events
        for kind, path in phase_artifacts.items():
            self._event(
                job_id=job_id,
                stage=stage_index,
                event_type="artifact_written",
                status="completed",
                message=f"{phase.title()} artifact: {kind}",
                payload={"command_id": command_id, "artifact_kind": kind, "relative_path": _safe_artifact_ref(path)},
            )

        # Create the review gate only after the supplied phase result has
        # passed the reviewed-output contract. Production Analysis/Planning
        # producer integration for creating this chain remains separate.
        with self._unit_of_work_factory() as uow:
            from migration_factory.control_tower.application.v2_phase_gate_service import (
                CreateGateRequest,
                V2PhaseGateService,
            )
            gate_service = V2PhaseGateService(gate_repo=uow.phase_gates)

            gate_result = gate_service.create_gate(CreateGateRequest(
                job_id=job_id,
                gate_phase=gate_phase,
                stage_index=stage_index,
                source_artifact_checksum=source_checksum,
                source_artifact_refs=artifact_refs_tuple,
                created_by="system",
            ))

            if gate_result.status == "created":
                uow.v2_events.save(
                    job_id=job_id,
                    stage=stage_index,
                    event_type="f15_gate_opened",
                    status="open",
                    message=f"{gate_phase} gate opened for stage {stage_index}",
                    payload={
                        "gate_id": gate_result.gate_id,
                        "gate_checksum": gate_result.gate_checksum,
                        "gate_phase": gate_phase,
                        "stage_index": stage_index,
                    },
                )
                uow.v2_events.save(
                    job_id=job_id,
                    stage=stage_index,
                    event_type=required_event_type,
                    status="blocked",
                    message=(
                        f"Stage {stage_index} {phase} completed with reviewed Markdown. "
                        f"{gate_phase} gate review required before proceeding."
                    ),
                    payload={
                        "from_stage": stage_index,
                        "to_stage": stage_index,
                        "gate_id": gate_result.gate_id,
                        "gate_checksum": gate_result.gate_checksum,
                        "gate_status": gate_result.status,
                    },
                )


# ── phase command helpers ──────────────────────────────────────────


def _resolve_phase_from_checksum(manifest_checksum: str) -> str | None:
    """Extract phase name from manifest_checksum like 'phase:planning'."""
    if manifest_checksum and manifest_checksum.startswith("phase:"):
        return manifest_checksum.split(":", 1)[1]
    return None


def _build_phase_argv(
    runner: V2OrchestratorRunner,
    job_id: str,
    command_id: str,
    command: Any,
    stage_index: int,
    command_phase: str,
) -> list[str]:
    """Build backend-owned argv for a phase-only orchestrator execution.

    Loads the job/setup from the DB and constructs proper argv with
    --phase <phase> flag.
    """
    with runner._unit_of_work_factory() as uow:
        job = uow.v2_jobs.get(job_id)
        if job is None:
            raise ValueError(f"Job {job_id!r} not found for phase command {command_id!r}")

        setup = uow.v2_setups.get(job.setup_id)
        if setup is None:
            raise ValueError(f"Setup {job.setup_id!r} not found for phase command {command_id!r}")

        run_configuration = uow.run_configurations.get_for_job(job_id)
        if run_configuration is None:
            raise ValueError(
                "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: persisted run configuration not found for job "
                f"{job_id!r}"
            )
        route = _current_route_for_job(uow, job_id)
        runtime_profile = ""
        if route is not None and getattr(route, "route_steps", None):
            for route_step in route.route_steps:
                if int(getattr(route_step, "stage_index", -1)) == stage_index:
                    runtime_profile = str(getattr(route_step, "runtime_profile", "") or "").strip()
                    if runtime_profile:
                        break
        if not runtime_profile:
            runtime_profile = resolve_runtime_profile_for_run_configuration(run_configuration)
        ensure_runtime_profile_available(setup.ai_hub_path, runtime_profile)

        effective_run_id = f"v2-{job_id[:8]}-s{stage_index}-{command_phase}"
        argv = [
            sys.executable,
            "-m",
            "migration_factory.orchestrator.runner",
            "--run-id", effective_run_id,
            "--job-id", job_id,
            "--legacy", setup.legacy_app_path,
            "--modernized", setup.output_parent_path,
            "--ai-hub", setup.ai_hub_path,
            "--profile", runtime_profile,
            "--mode", "full_sandbox_migration",
            "--phase", command_phase,
        ]
        return argv


def _normalized_argv(argv: list[str]) -> list[str]:
    if argv and argv[0] == "python":
        return [sys.executable, *argv[1:]]
    return argv


def _build_env(manifest: dict[str, Any]) -> dict[str, str]:
    return materialize_execution_environment(manifest)


def _is_secret_env_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in _SECRET_ENV_MARKERS)


def _load_json_list(text: str) -> list[str]:
    value = json.loads(text)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Persisted argv_json must be a string array")
    return value


def _load_json_dict(text: str) -> dict[str, Any]:
    return decode_environment_manifest(text)


def _load_env_manifest_for_stage(uow: Any, job_id: str, stage_index: int) -> dict[str, Any]:
    commands = uow.v2_commands.list_by_job(job_id)
    for cmd in commands:
        if int(getattr(cmd, "stage_index", -1)) == stage_index:
            try:
                return _load_json_dict(getattr(cmd, "env_json", "{}"))
            except json.JSONDecodeError:
                return {}
    return {}


def _resolve_original_stage_command_for_resume(
    uow: Any,
    *,
    job_id: str,
    stage_index: int,
) -> Any | None:
    commands = uow.v2_commands.list_by_job_and_stage(job_id, stage_index)
    if not commands:
        return None

    def _has_route_metadata(command: Any) -> bool:
        try:
            env_manifest = _load_json_dict(getattr(command, "env_json", "{}"))
        except (json.JSONDecodeError, TypeError, ValueError):
            env_manifest = {}
        return bool(
            env_manifest.get("ROUTE_STEP_INDEX")
            or env_manifest.get("ROUTE_STEP_RUNTIME_PROFILE")
            or _argv_option_value(
                _safe_argv_for_command(command),
                "--profile",
            )
        )

    for command in commands:
        if _has_route_metadata(command):
            return command
    return commands[0]


def _safe_argv_for_command(command: Any) -> list[str]:
    try:
        return _load_json_list(getattr(command, "argv_json", "[]"))
    except (json.JSONDecodeError, ValueError, TypeError):
        return []


def _resolve_route_step_index_for_command(
    *,
    command: Any,
    result: dict[str, Any] | None,
    route: Any,
) -> int | None:
    env_manifest = _load_json_dict(getattr(command, "env_json", "{}"))
    argv = _safe_argv_for_command(command)

    candidate_values: list[Any] = [
        env_manifest.get("ROUTE_STEP_INDEX"),
        env_manifest.get("ROUTE_STEP_RUNTIME_PROFILE"),
        _argv_option_value(argv, "--profile"),
    ]
    if isinstance(result, dict):
        candidate_values.extend([
            result.get("route_step_index"),
            result.get("profile_id"),
            result.get("runtime_profile"),
            result.get("route_step_runtime_profile"),
        ])

    for candidate in candidate_values:
        try:
            route_step_index = int(str(candidate).strip())
        except (TypeError, ValueError):
            route_step_index = None
        if route_step_index is not None and route_step_index >= 1:
            return route_step_index
        runtime_profile = str(candidate or "").strip()
        if runtime_profile:
            resolved = _route_step_index_for_runtime_profile(route, runtime_profile)
            if resolved is not None:
                return resolved

    return None


def _route_step_index_for_runtime_profile(route: Any, runtime_profile: str) -> int | None:
    for index, step in enumerate(getattr(route, "route_steps", ()), start=1):
        if str(getattr(step, "runtime_profile", "")).strip() == runtime_profile:
            return index
    return None


def _argv_option_value(argv: list[str], option_name: str) -> str:
    for index, value in enumerate(argv):
        if value == option_name and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith(f"{option_name}="):
            return value.split("=", 1)[1]
    return ""


def _validate_resume_checkpoint(
    uow: Any,
    *,
    job_id: str,
    resume: Any,
) -> _ResumeValidationResult:
    """Validate a queued resume against backend-owned checkpoint evidence."""
    stage_index = int(getattr(resume, "stage_index", -1))
    card = uow.v2_approvals.get_card(str(getattr(resume, "card_id", "")))
    if card is None:
        return _ResumeValidationResult(False, "checkpoint_not_found", stage_index)
    if card.job_id != job_id or getattr(resume, "job_id", "") != job_id:
        return _ResumeValidationResult(False, "foreign_job", stage_index)
    if card.stage_index != stage_index:
        return _ResumeValidationResult(False, "stage_mismatch", stage_index)
    if card.status not in _APPROVAL_ACCEPTED_STATUSES:
        return _ResumeValidationResult(False, "checkpoint_not_accepted", stage_index)

    gate = _find_gate_for_resume_checksum(
        uow,
        job_id=job_id,
        stage_index=stage_index,
        checkpoint_checksum=card.request_checksum,
    )
    if gate is None:
        return _ResumeValidationResult(False, "checkpoint_checksum_mismatch", stage_index)
    if gate.gate_status == "superseded":
        return _ResumeValidationResult(False, "checkpoint_stale", stage_index)

    accepted = _find_accepted_revision_for_gate(
        uow,
        job_id=job_id,
        stage_index=stage_index,
        evidence_checksum=gate.source_artifact_checksum,
    )
    if accepted is None:
        return _ResumeValidationResult(False, "accepted_artifact_not_found", stage_index)
    if accepted.superseded_by_revision_id is not None:
        return _ResumeValidationResult(False, "accepted_artifact_superseded", stage_index)

    checkpoint_route = _extract_checkpoint_route(
        gate.source_artifact_refs_json,
        accepted.artifact_refs_json,
    )
    if checkpoint_route is None:
        return _ResumeValidationResult(False, "checkpoint_profile_metadata_missing", stage_index)

    current_route = _current_route_for_job(uow, job_id)
    if current_route is None or not current_route.valid:
        return _ResumeValidationResult(False, "profile_incompatible", stage_index)

    current_route_dict = route_to_dict(current_route)
    for key in (
        "source_profile",
        "target_profile",
        "included_stages",
        "excluded_stages",
        "skipped_stages",
    ):
        if checkpoint_route.get(key) != current_route_dict.get(key):
            return _ResumeValidationResult(False, "checkpoint_route_changed", stage_index)

    if stage_index not in current_route.included_stages and stage_index not in (1,):
        return _ResumeValidationResult(False, "checkpoint_stage_not_in_route", stage_index)

    return _ResumeValidationResult(True, stage_index=stage_index)


def _find_gate_for_resume_checksum(
    uow: Any,
    *,
    job_id: str,
    stage_index: int,
    checkpoint_checksum: str,
) -> Any | None:
    for gate in uow.phase_gates.list_by_job_and_stage(job_id, stage_index):
        refs = _json_loads(gate.source_artifact_refs_json, default=[])
        current_checksum = gate_checksum(
            gate_id=gate.gate_id,
            job_id=gate.job_id,
            gate_phase=gate.gate_phase,
            stage_index=gate.stage_index,
            source_artifact_checksum=gate.source_artifact_checksum,
            source_artifact_refs=refs if isinstance(refs, list) else [],
        )
        if current_checksum == checkpoint_checksum:
            return gate
    return None


def _find_accepted_revision_for_gate(
    uow: Any,
    *,
    job_id: str,
    stage_index: int,
    evidence_checksum: str,
) -> Any | None:
    for revision in uow.artifact_revisions.list_by_job_and_stage(job_id, stage_index):
        if revision.revision_status != "accepted":
            continue
        if revision.evidence_checksum == evidence_checksum:
            return revision
    return None


def _current_route_for_job(uow: Any, job_id: str) -> Any | None:
    from migration_factory.control_tower.application.v2_stage_progression import (
        V2StageProgressionService,
    )

    service = V2StageProgressionService(
        setup_repo=uow.v2_setups,
        command_repo=uow.v2_commands,
        artifact_revision_repo=uow.artifact_revisions,
        run_config_repo=uow.run_configurations,
    )
    return service.compute_route_for_job(job_id)


def _extract_checkpoint_route(*raw_values: str) -> dict[str, Any] | None:
    for raw in raw_values:
        value = _json_loads(raw, default=None)
        found = _find_route_metadata(value)
        if found is not None:
            return found
    return None


def _find_route_metadata(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        candidate = value.get("profile_metadata") if isinstance(value.get("profile_metadata"), dict) else value
        if _looks_like_route_metadata(candidate):
            return _normalize_route_metadata(candidate)
        for nested in value.values():
            found = _find_route_metadata(nested)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_route_metadata(item)
            if found is not None:
                return found
    return None


def _looks_like_route_metadata(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and "source_profile" in value
        and "target_profile" in value
        and "included_stages" in value
    )


def _normalize_route_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_profile": str(value.get("source_profile") or ""),
        "target_profile": str(value.get("target_profile") or ""),
        "included_stages": _int_list(value.get("included_stages")),
        "excluded_stages": _int_list(value.get("excluded_stages")),
        "skipped_stages": _int_list(value.get("skipped_stages")),
    }


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            return []
    return result


def _json_loads(raw: Any, *, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def _extract_final_json(stdout: str) -> dict[str, Any] | None:
    """Extract the orchestrator result JSON from stdout.

    Strategy (in order of priority):
    1. Look for a line starting with CONTROL_TOWER_FINAL_JSON prefix.
    2. Fall back to scanning for the last JSON object that looks like an
       orchestrator result (backward-compatible).
    """
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if line.startswith(_FINAL_JSON_PREFIX):
            payload = line[len(_FINAL_JSON_PREFIX):]
            try:
                value = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and _looks_like_orchestrator_result(value):
                return value

    # Fallback: generic scan over non-event lines
    lines = [line for line in stdout.splitlines() if not line.startswith(_EVENT_PREFIX)]
    text = "\n".join(lines).strip()
    if not text:
        return None

    decoder = json.JSONDecoder()
    index = 0
    last_result: dict[str, Any] | None = None

    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break

        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue

        if isinstance(value, dict) and _looks_like_orchestrator_result(value):
            last_result = value

        index = start + max(consumed, 1)

    return last_result


def _looks_like_orchestrator_result(value: dict[str, Any]) -> bool:
    return any(
        key in value
        for key in (
            "run_id",
            "final_status",
            "approval_status",
            "transform_status",
            "build_status",
            "test_status",
            "artifact_refs",
            "sandbox_path",
            "modernized_app_path",
        )
    )


def _result_sandbox_path(result: dict[str, Any]) -> str:
    artifact_refs = result.get("artifact_refs") if isinstance(result.get("artifact_refs"), dict) else {}

    direct = _first_text(
        result.get("sandbox_path"),
        artifact_refs.get("sandbox"),
        artifact_refs.get("sandbox_path"),
        artifact_refs.get("modernized_app"),
        artifact_refs.get("modernized_app_path"),
    )
    if direct:
        return direct

    summary_ref = _first_text(
        artifact_refs.get("orchestration_summary"),
        result.get("orchestration_summary"),
    )
    if summary_ref:
        try:
            summary_path = Path(summary_ref)
            if summary_path.is_file():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                return _first_text(
                    summary.get("sandbox_path"),
                    summary.get("modernized_app_path"),
                    (summary.get("artifact_refs") or {}).get("sandbox")
                    if isinstance(summary.get("artifact_refs"), dict)
                    else "",
                )
        except Exception:
            return ""

    return ""


_RE_JAVAC_ERROR = re.compile(
    r'\[ERROR\]\s+(.+?\.[Jj][Aa][Vv][Aa])\s*:\s*\[?(\d+)(?:,\s*(\d+))?\]?\s+(.+)',
)
"""Match Maven/javac compiler diagnostic lines.

Supports the standard Maven-compiler-plugin format:

  [ERROR] /path/to/Foo.java:[42,17] cannot find symbol

and the plain-colon format:

  [ERROR] /path/to/Foo.java:42: error: cannot find symbol

Group 1: file path (case-insensitive .java)
Group 2: line number
Group 3: column number (optional)
Group 4: error message
"""


def _normalize_compiler_source_path(value: str) -> str:
    text = str(value or "").strip()
    if re.match(r"^/[A-Za-z]:[\\/]", text):
        return text[1:]
    return text


def _normalize_compiler_errors(
    *,
    stdout_tail: str = "",
    stderr_tail: str = "",
) -> tuple[NormalizedCompilerError, ...]:
    """Extract NormalizedCompilerError tuples from Maven/javac build output.

    Parses stdout and stderr for javac compiler diagnostic lines matching
    the standard Maven-compiler-plugin format. Deduplicates exact duplicates
    and preserves stable ordering. Malformed lines are silently skipped.
    """
    combined = f"{stdout_tail}\n{stderr_tail}"
    seen: set[tuple[str, int, int]] = set()
    results: list[NormalizedCompilerError] = []

    for line in combined.splitlines():
        m = _RE_JAVAC_ERROR.match(line.strip())
        if not m:
            continue
        file_path = _normalize_compiler_source_path(m.group(1))
        try:
            line_num = int(m.group(2))
        except (ValueError, TypeError):
            continue
        column_str = m.group(3)
        try:
            column = int(column_str) if column_str else 0
        except (ValueError, TypeError):
            column = 0
        message = m.group(4).strip()
        if line_num <= 0:
            continue
        key = (file_path, line_num, column)
        if key in seen:
            continue
        seen.add(key)
        results.append(NormalizedCompilerError(
            message=message,
            file_path=file_path,
            line=line_num,
            column=column,
            severity="error",
        ))

    # Sort by (file_path, line, column) for stable deterministic ordering
    results.sort(key=lambda e: (e.file_path, e.line, e.column))
    return tuple(results)


def _result_run_dir(result: dict[str, Any], *, cwd: Path) -> Path | None:
    direct = _first_text(result.get("run_dir"))
    if direct:
        return Path(direct)

    run_id = str(result.get("run_id") or "").strip()
    if run_id:
        artifact_refs = result.get("artifact_refs") if isinstance(result.get("artifact_refs"), dict) else {}
        for candidate in (result.get("sandbox_path"), result.get("modernized_app_path"), *artifact_refs.values()):
            text = str(candidate or "")
            marker = f".migration{os.sep}runs{os.sep}{run_id}"
            alt_marker = f".migration/runs/{run_id}"
            normalized = text.replace("\\", "/")
            if alt_marker in normalized:
                prefix = normalized[: normalized.index(alt_marker) + len(alt_marker)]
                return Path(prefix)
            if marker in text:
                prefix = text[: text.index(marker) + len(marker)]
                return Path(prefix)
        return cwd / ".migration" / "runs" / run_id

    return None


def _safe_artifact_ref(value: Any) -> str:
    text = str(value)
    marker = ".migration"
    if marker in text:
        return text[text.index(marker):]
    return _bounded(str(redact_public_value(text)))


_APPROVAL_REVIEW_ARTIFACT_KEYS: tuple[str, ...] = (
    "analysis_report",
    "analysis_report.json",
    "analysis_summary",
    "analysis_summary.md",
    "dependency_graph",
    "dependency_graph.json",
    "config_inventory",
    "config_inventory.json",
    "test_inventory",
    "test_inventory.json",
    "migration_plan.yaml",
    "migration_units.yaml",
    "plan_summary.md",
    "plan_validation_report.json",
    "target_dependency_plan",
    "rewrite_preview",
    "rewrite_preview.json",
    "rewrite_dry_run.patch",
    "rewrite_impact_summary",
    "rewrite_impact_summary.json",
    "assessment_report",
    "assessment_report.json",
    "assessment_summary",
    "assessment_summary.md",
    "approval_request.json",
    "approval_request",
)


def _approval_review_artifact_refs(result: dict[str, Any]) -> tuple[str, ...]:
    """Collect evidence refs to bind to an approval_review gate."""
    artifact_refs = result.get("artifact_refs") if isinstance(result.get("artifact_refs"), dict) else {}
    selected: list[str] = []

    for key in _APPROVAL_REVIEW_ARTIFACT_KEYS:
        value = artifact_refs.get(key)
        if isinstance(value, str) and value.strip():
            selected.append(value.strip())

    if not selected:
        for value in artifact_refs.values():
            if isinstance(value, str) and value.strip():
                selected.append(value.strip())

    # Preserve deterministic ordering for gate checksum binding.
    return tuple(sorted(dict.fromkeys(selected)))


def _approval_review_source_checksum(
    *,
    job_id: str,
    stage_index: int,
    command_id: str,
    result: dict[str, Any],
    artifact_refs: tuple[str, ...],
) -> str:
    """Compute the approval evidence checksum bound to the gate."""
    approval_request_checksum = _first_text(
        result.get("approval_request_checksum"),
        result.get("request_checksum"),
        result.get("card_checksum"),
        sha256_canonical_json(result),
    )
    return sha256_canonical_json({
        "job_id": job_id,
        "stage_index": stage_index,
        "command_id": command_id,
        "approval_request_checksum": approval_request_checksum,
        "artifact_refs": list(artifact_refs),
    })


def _open_or_refresh_approval_review_gate(
    *,
    uow: Any,
    job_id: str,
    stage_index: int,
    command_id: str,
    result: dict[str, Any],
) -> tuple[Any, bool]:
    """Create or reuse the approval_review gate for a blocked transform.

    Returns (gate_record, created_new_gate).
    """
    from migration_factory.control_tower.application.v2_phase_gate_service import (
        CreateGateRequest,
        V2PhaseGateService,
    )

    gate_service = V2PhaseGateService(gate_repo=uow.phase_gates)
    artifact_refs = _approval_review_artifact_refs(result)
    source_checksum = _approval_review_source_checksum(
        job_id=job_id,
        stage_index=stage_index,
        command_id=command_id,
        result=result,
        artifact_refs=artifact_refs,
    )
    refs_json = json.dumps(list(artifact_refs), separators=(",", ":"))
    existing = uow.phase_gates.find_open(job_id, "approval_review", stage_index)
    if existing is not None:
        same_refs = existing.source_artifact_refs_json == refs_json
        same_checksum = existing.source_artifact_checksum == source_checksum
        if same_refs and same_checksum:
            return existing, False
        gate_service.supersede_gate(existing.gate_id)

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id=job_id,
        gate_phase="approval_review",
        stage_index=stage_index,
        source_artifact_checksum=source_checksum,
        source_artifact_refs=artifact_refs,
        created_by="backend_orchestrator",
    ))
    if gate_result.status == "created":
        gate = uow.phase_gates.get(gate_result.gate_id)
        if gate is None:
            raise ValueError("approval_review gate was created but could not be loaded")
        return gate, True

    if gate_result.existing_gate_id:
        gate = uow.phase_gates.get(gate_result.existing_gate_id)
        if gate is None:
            raise ValueError("approval_review gate conflict could not be resolved")
        return gate, False

    raise ValueError("approval_review gate could not be created")


def _canonical_event_type(phase: str, suffix: str, *, stage_index: int) -> str:
    mapping = {
        "transform": f"sandbox_transform_{suffix}",
        "sandbox_transform": f"sandbox_transform_{suffix}",
        "build": f"build_{suffix}",
        "test": f"test_{suffix}",
        "repair": f"repair_{suffix}",
    }

    if phase == "final_report":
        # Only Stage 3 gets final_report events; earlier stages get stage_report events
        if stage_index == 3:
            return f"final_report_{suffix}"
        return f"stage_report_{suffix}"

    if phase in mapping:
        return mapping[phase]

    return f"{phase}_{suffix}"


def _status_suffix(status: str) -> str:
    normalized = str(status or "").lower()
    if normalized == "running":
        return "started"
    if normalized == "completed":
        return "completed"
    return normalized or "running"


def _is_terminal_failure_result(result: dict[str, Any]) -> bool:
    statuses = [
        str(result.get("final_status", "")),
        str(result.get("build_status", "")),
        str(result.get("test_status", "")),
        str(result.get("transform_status", "")),
        str(result.get("repair_loop_status", "")),
    ]
    if any(_is_failure_status(value) for value in statuses):
        return True
    if result.get("errors") or result.get("blockers"):
        return True
    return False


def _has_success_proof(result: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    expected_values = {
        "orchestration_status": _SUCCESS_ORCHESTRATION_STATUS,
        "final_status": _SUCCESS_FINAL_STATUS,
        "transform_status": _SUCCESS_TRANSFORM_STATUS,
        "build_status": _SUCCESS_BUILD_STATUS,
        "test_status": sorted(_SUCCESS_TEST_STATUSES),
    }
    detected_values = {
        "orchestration_status": _normalize_detected(result.get("orchestration_status")),
        "final_status": _normalize_detected(result.get("final_status")),
        "transform_status": _normalize_detected(result.get("transform_status")),
        "build_status": _normalize_detected(result.get("build_status")),
        "test_status": _normalize_detected(result.get("test_status")),
        "sandbox_path": _result_sandbox_path(result),
    }

    if not detected_values["sandbox_path"]:
        return False, _proof_failure_details(
            field="sandbox_path",
            expected="present",
            detected="missing",
            reason="missing sandbox_path",
            expected_values=expected_values,
            detected_values=detected_values,
        )
    if result.get("errors"):
        return False, _proof_failure_details(
            field="errors",
            expected="empty",
            detected=_normalize_detected(result.get("errors")),
            reason="errors present",
            expected_values=expected_values,
            detected_values=detected_values,
        )
    if result.get("blockers"):
        return False, _proof_failure_details(
            field="blockers",
            expected="empty",
            detected=_normalize_detected(result.get("blockers")),
            reason="blockers present",
            expected_values=expected_values,
            detected_values=detected_values,
        )

    checks = (
        ("orchestration_status", {_SUCCESS_ORCHESTRATION_STATUS}, "PASS"),
        ("final_status", {_SUCCESS_FINAL_STATUS}, _SUCCESS_FINAL_STATUS),
        ("transform_status", {_SUCCESS_TRANSFORM_STATUS}, _SUCCESS_TRANSFORM_STATUS),
        ("build_status", {_SUCCESS_BUILD_STATUS}, _SUCCESS_BUILD_STATUS),
        ("test_status", _SUCCESS_TEST_STATUSES, "{" + ", ".join(sorted(_SUCCESS_TEST_STATUSES)) + "}"),
    )
    for field, accepted, expected_text in checks:
        detected = detected_values[field]
        if not detected:
            return False, _proof_failure_details(
                field=field,
                expected=expected_text,
                detected="missing",
                reason=f"missing {field}",
                expected_values=expected_values,
                detected_values=detected_values,
            )
        if detected not in accepted:
            return False, _proof_failure_details(
                field=field,
                expected=expected_text,
                detected=detected,
                reason=f"expected {field}={expected_text}, detected={detected}",
                expected_values=expected_values,
                detected_values=detected_values,
            )
    return True, {
        "field": "",
        "expected": "",
        "detected": "",
        "reason": "",
        "expected_values": expected_values,
        "detected_values": detected_values,
    }


def _is_failure_status(value: Any) -> bool:
    text = str(value or "").strip().upper()
    if not text:
        return False
    if text in _TERMINAL_FAILURES:
        return True
    if "FAILED" in text or text.endswith("_FAIL") or text == "FAIL":
        return True
    if "FALLBACK_REPAIR_PLAN" in text:
        return True
    return False


def _normalize_detected(value: Any) -> str:
    return str(value or "").strip()


def _proof_failure_details(
    *,
    field: str,
    expected: str,
    detected: Any,
    reason: str,
    expected_values: dict[str, Any],
    detected_values: dict[str, Any],
) -> dict[str, Any]:
    return {
        "field": field,
        "expected": expected,
        "detected": detected,
        "reason": reason,
        "expected_values": expected_values,
        "detected_values": detected_values,
    }


def _queued_command_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("command_id") or "")
    return str(getattr(value, "command_id", "") or "")


def _bounded(value: str) -> str:
    redacted = redact_model_summary(value)
    if len(redacted) <= _MAX_TEXT:
        return redacted
    return redacted[:_MAX_TEXT] + "...[truncated]"


def _is_sqlite_locked_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    lowered = str(exc).lower()
    return "database is locked" in lowered or "database table is locked" in lowered or "locked" in lowered


def _result_has_warnings(result: dict[str, Any] | None) -> bool:
    """Check if an orchestrator result contains warnings.

    Returns True if any status field contains a warning indicator
    (e.g. PASS_WITH_WARNINGS), or if the result has an explicit
    'warnings' key with content.
    """
    if result is None:
        return False

    # Check known warning-bearing status fields
    test_status = str(result.get("test_status", "")).strip()
    if test_status == "PASS_WITH_WARNINGS":
        return True

    build_status = str(result.get("build_status", "")).strip()
    if build_status == "PASS_WITH_WARNINGS":
        return True

    orchestration_status = str(result.get("orchestration_status", "")).strip()
    if orchestration_status == "PASS_WITH_WARNINGS":
        return True

    # Check for explicit warnings key
    explicit_warnings = result.get("warnings")
    if explicit_warnings:
        if isinstance(explicit_warnings, (list, tuple)) and len(explicit_warnings) > 0:
            return True
        if isinstance(explicit_warnings, str) and explicit_warnings.strip():
            return True

    return False


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _list_or_none(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
        return items if items else None
    return None


def _add_repair_refs_to_payload(result: dict[str, Any], payload: dict[str, Any]) -> None:
    for key in ("_repair_failure_evidence_ref", "_repair_context_pack_ref", "_repair_run_dir",
                "_repair_sandbox_path", "_repair_failure_evidence_checksum",
                "_repair_context_pack_checksum", "_repair_base_repo_state_checksum",
                "_repair_validation_context_ref", "_repair_validation_context_checksum",
                "_repair_h2_required", "source_profile", "target_profile", "changed_files"):
        if key in result:
            payload[key] = result[key]
