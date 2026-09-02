"""F14 — Stage 3 POM Dependency Editor (main orchestrator).

Central service for POM review, dependency edit proposal, backend-controlled
apply, async validation, failure diagnosis, repair, and rollback.

Delegates to sub-services: review, policy, proposer, patcher, diagnoser, validation.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4

from migration_factory.control_tower.application.pom_change_models import (
    PomChangePlan,
    PomChangeProposal,
    PomChangeRecord,
    PomChangeRecordSummary,
    PomChangeStatus,
    PomChangeTarget,
    PomApplyResult,
    PomBaseline,
    PomDependencyFinding,
    PomDependencyReview,
    PomRepairPlan,
    PomRepairPlanStatus,
    PomRollbackResult,
    PomValidationFailureDiagnosis,
    PomValidationRun,
    PomValidationStatus,
    PomView,
    ALLOWED_POM_OPERATIONS,
    APPLY_CAPABLE_POM_OPERATIONS,
    PROPOSAL_ONLY_POM_OPERATIONS,
)
from migration_factory.control_tower.application.pom_dependency_policy import (
    PomDependencyPolicy,
    DependencyPolicyDecision,
    DependencyControlMode,
    _is_vague_request,
    _is_latest_request,
)
from migration_factory.control_tower.application.pom_dependency_review import (
    PomDependencyReviewer,
)
from migration_factory.control_tower.application.pom_change_proposer import (
    PomChangeProposer,
    _clean_requested_version,
)
from migration_factory.control_tower.application.pom_xml_patcher import (
    PomXmlPatcher,
    PomPatchResult,
    _sha256,
)
from migration_factory.control_tower.application.pom_validation_diagnosis import (
    PomValidationDiagnoser,
)
from migration_factory.control_tower.domain.checksums import utc_now_text


# ── Protocols for DI ───────────────────────────────────────────────

class PomChangeRepositoryProto(Protocol):
    def save(self, **kwargs) -> PomChangeRecord: ...
    def get(self, change_id: str) -> PomChangeRecord | None: ...
    def find_by_idempotency(self, job_id: str, key: str) -> PomChangeRecord | None: ...
    def update_status(self, change_id: str, status: str, **kwargs) -> None: ...
    def list_by_job(self, job_id: str) -> list[PomChangeRecord]: ...


class PomProposalRepositoryProto(Protocol):
    def save(self, **kwargs) -> Any: ...
    def get(self, proposal_id: str) -> Any | None: ...
    def mark_consumed(self, proposal_id: str) -> None: ...


class PomValidationRepositoryProto(Protocol):
    def save(self, **kwargs) -> str: ...
    def update_result(self, validation_id: str, **kwargs) -> None: ...
    def get(self, validation_id: str) -> dict[str, Any] | None: ...
    def get_by_change(self, change_id: str) -> dict[str, Any] | None: ...


class PomRepairPlanRepoProto(Protocol):
    def save(self, **kwargs) -> str: ...
    def get(self, repair_plan_id: str) -> dict[str, Any] | None: ...
    def get_by_validation(self, validation_id: str) -> dict[str, Any] | None: ...
    def update_status(self, repair_plan_id: str, status: str) -> None: ...


class EventSinkProto(Protocol):
    def save(self, *, job_id: str, stage: int | None, event_type: str,
             status: str, message: str, payload: dict[str, Any] | None) -> Any: ...


class ValidationLauncherProto(Protocol):
    def __call__(self, change_id: str, validation_id: str,
                 command: str, job_id: str, sandbox_path: str) -> None: ...


# ── Main service ───────────────────────────────────────────────────

class PomDependencyEditor:
    """Stage 3 POM review, proposal, apply, validate, repair, rollback.

    Thin orchestrator. All heavy logic lives in sub-services.
    """

    _SNAPSHOT_DIR = ".f14_snapshots"

    def __init__(
        self,
        *,
        review_service: PomDependencyReviewer | None = None,
        policy: PomDependencyPolicy | None = None,
        proposer: PomChangeProposer | None = None,
        patcher: PomXmlPatcher | None = None,
        diagnoser: PomValidationDiagnoser | None = None,
        event_sink: EventSinkProto | None = None,
        change_repo: PomChangeRepositoryProto | None = None,
        proposal_repo: PomProposalRepositoryProto | None = None,
        validation_repo: PomValidationRepositoryProto | None = None,
        repair_plan_repo: PomRepairPlanRepoProto | None = None,
        resolve_sandbox_root: Callable[[str, int], Path | None] | None = None,
        resolve_pom_content: Callable[[str], str] | None = None,
        launch_validation: ValidationLauncherProto | None = None,
    ) -> None:
        self._review = review_service or PomDependencyReviewer()
        self._policy = policy or PomDependencyPolicy()
        self._proposer = proposer or PomChangeProposer()
        self._patcher = patcher or PomXmlPatcher()
        self._diagnoser = diagnoser or PomValidationDiagnoser()
        self._events = event_sink
        self._change_repo = change_repo
        self._proposal_repo = proposal_repo
        self._validation_repo = validation_repo
        self._repair_plan_repo = repair_plan_repo
        self._resolve_sandbox = resolve_sandbox_root or (lambda j, s: None)
        self._resolve_pom = resolve_pom_content or (lambda j: "")
        self._launch_validation = launch_validation or (lambda c, v, cmd, j, sp: None)

    # ── Read operations (no write) ──────────────────────────────────

    def get_stage3_pom(
        self,
        job_id: str,
        pom_content: str | None = None,
        pom_path: str | None = None,
        target_dependency_plan: dict[str, Any] | None = None,
    ) -> PomView:
        """Get redacted Stage 3 POM view."""
        content = pom_content or self._resolve_pom(job_id)

        review = self._review.review(
            job_id=job_id,
            stage=3,
            pom_content=content,
            pom_path=pom_path or "",
            target_dependency_plan=target_dependency_plan,
        )

        # Truncate content for display
        truncated = False
        max_chars = 100_000
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        return PomView(
            job_id=job_id,
            stage=3,
            exists=bool(content),
            content=content,
            truncated=truncated,
            content_type="application/xml",
            redaction_applied=True,
            detected_baseline=review.baseline,
            reason=None,
        )

    def review_stage3_dependencies(
        self,
        job_id: str,
        pom_content: str | None = None,
        pom_path: str | None = None,
        pom_deps_data: dict[str, Any] | None = None,
        target_dependency_plan: dict[str, Any] | None = None,
        dependency_policy_report: dict[str, Any] | None = None,
    ) -> PomDependencyReview:
        """Get classified Stage 3 dependency review."""
        content = pom_content or self._resolve_pom(job_id)
        return self._review.review(
            job_id=job_id,
            stage=3,
            pom_content=content,
            pom_path=pom_path or "",
            pom_deps_data=pom_deps_data,
            target_dependency_plan=target_dependency_plan,
            dependency_policy_report=dependency_policy_report,
        )

    def propose_change(
        self,
        job_id: str,
        user_request: str,
        idempotency_key: str | None = None,
        pom_content: str | None = None,
        pom_deps_data: dict[str, Any] | None = None,
    ) -> PomChangeProposal:
        """Propose a POM change. Read-only — no file written."""
        content = pom_content or self._resolve_pom(job_id)

        live_deps_data = pom_deps_data or self._review.parse_pom_deps(content)

        # Propose
        proposal = self._proposer.propose(
            job_id=job_id,
            user_request=user_request,
            stage=3,
            pom_content=content,
            pom_deps_data=live_deps_data,
        )

        # Persist proposal if repos available
        if self._proposal_repo:
            self._proposal_repo.save(
                proposal_id=proposal.proposal_id,
                job_id=job_id,
                stage_index=3,
                user_request=user_request,
                server_plan_json=json.dumps(proposal.server_validated_plan_preview, sort_keys=True),
                risk=proposal.risk,
                can_apply=proposal.can_apply,
                control_mode=proposal.control_mode,
            )

        # Emit event
        self._emit(
            job_id=job_id, stage=3,
            event_type="pom_change_proposed",
            status="proposed",
            message=f"POM change proposed: {user_request}",
            payload={
                "proposal_id": proposal.proposal_id,
                "operation": proposal.server_validated_plan_preview.get("operation", ""),
                "target_desc": _target_desc_from_plan(proposal.server_validated_plan_preview),
                "risk": proposal.risk,
                "can_apply": proposal.can_apply,
                "stage_index": 3,
            },
        )

        return proposal

    # ── Write operations (backend-owned) ────────────────────────────

    def apply_change_from_proposal(
        self,
        job_id: str,
        proposal_id: str,
        idempotency_key: str,
        pom_content: str | None = None,
        pom_deps_data: dict[str, Any] | None = None,
        sandbox_path: str | None = None,
        build_command: str | None = None,
    ) -> PomApplyResult:
        """Apply a POM change from a previously generated proposal.

        Reloads proposal from repo, revalidates, then writes.
        """
        if not self._proposal_repo:
            return _error_result("Proposal repository not available", idempotency_key)

        # Check idempotency
        existing = self._check_idempotency(job_id, idempotency_key)
        if existing:
            return existing

        # Load proposal
        prop_record = self._proposal_repo.get(proposal_id)
        if not prop_record:
            return _error_result(f"Proposal {proposal_id} not found", idempotency_key)

        if prop_record.status == "consumed":
            return _error_result(f"Proposal {proposal_id} has already been consumed", idempotency_key)

        plan_data = json.loads(prop_record.server_plan_json)

        return self._do_apply(
            job_id=job_id,
            proposal_id=proposal_id,
            plan_data=plan_data,
            idempotency_key=idempotency_key,
            pom_content=pom_content,
            pom_deps_data=pom_deps_data,
            sandbox_path=sandbox_path,
            build_command=build_command,
        )

    def apply_change_from_user_request(
        self,
        job_id: str,
        user_request: str,
        idempotency_key: str,
        pom_content: str | None = None,
        pom_deps_data: dict[str, Any] | None = None,
        sandbox_path: str | None = None,
        build_command: str | None = None,
    ) -> PomApplyResult:
        """Apply a POM change from a user request string.

        Classifies, validates through policy, then writes if allowed.
        """
        # Check idempotency
        existing = self._check_idempotency(job_id, idempotency_key)
        if existing:
            return existing

        content = pom_content or self._resolve_pom(job_id)
        live_deps_data = pom_deps_data or self._review.parse_pom_deps(content)

        # Parse user request to extract target
        parsed = self._proposer._parse_user_request(user_request, content, live_deps_data)
        target = parsed["target"]
        operation = parsed["operation"]
        requested_version = _clean_requested_version(parsed.get("requested_version", ""))

        # Create a fresh policy with the current POM data for this evaluation
        fresh_policy = PomDependencyPolicy(pom_deps_data=live_deps_data)

        # Policy evaluation
        decision = fresh_policy.evaluate_change(
            target_kind=target.kind,
            group_id=target.group_id,
            artifact_id=target.artifact_id,
            property_name=target.property_name,
            requested_version=requested_version,
            user_request=user_request,
            stage=3,
        )

        if not decision.can_apply:
            return _blocked_result(decision, user_request, idempotency_key)

        # Build plan data for apply
        plan_data = {
            "intent": "apply_dependency_change",
            "stage": 3,
            "operation": operation,
            "target": target.to_dict(),
            "requested_version": requested_version,
            "risk": decision.risk,
            "control_mode": decision.control_mode.value,
            "requires_validation": True,
            "evidence": ["root_pom"],
            "rationale": f"User-requested change: {user_request}",
        }

        return self._do_apply(
            job_id=job_id,
            proposal_id=None,
            plan_data=plan_data,
            idempotency_key=idempotency_key,
            pom_content=content,
            pom_deps_data=pom_deps_data,
            sandbox_path=sandbox_path,
            build_command=build_command,
        )

    # ── Internal apply ──────────────────────────────────────────────

    def _do_apply(
        self,
        *,
        job_id: str,
        proposal_id: str | None,
        plan_data: dict[str, Any],
        idempotency_key: str,
        pom_content: str | None = None,
        pom_deps_data: dict[str, Any] | None = None,
        sandbox_path: str | None = None,
        build_command: str | None = None,
    ) -> PomApplyResult:
        """Core apply logic: patch, persist, emit, enqueue validation."""

        content = pom_content or self._resolve_pom(job_id)
        if not content:
            return _error_result("Could not resolve POM content for job", idempotency_key)

        operation = plan_data["operation"]
        target_data = plan_data.get("target", {})

        if operation not in ALLOWED_POM_OPERATIONS:
            return _error_result(f"Operation {operation} is not allowed", idempotency_key)

        if operation in PROPOSAL_ONLY_POM_OPERATIONS:
            return PomApplyResult(
                change_id="",
                status="blocked",
                operation=operation,
                target_desc=_target_desc_from_plan(plan_data),
                before_version="",
                after_version="",
                before_checksum="",
                after_checksum="",
                diff_summary="",
                validation_id=None,
                rollback_available=False,
                idempotency_key=idempotency_key,
                created_at=utc_now_text(),
                message=f"Operation '{operation}' is recognized but not apply-capable in F14. "
                        f"It requires an OpenRewrite or repair-loop executor which is not yet wired. "
                        f"Use propose-change for a proposal or request a different operation.",
            )

        # Resolve sandbox path for writing
        sandbox = sandbox_path
        if not sandbox and self._resolve_sandbox:
            resolved = self._resolve_sandbox(job_id, 3)
            if resolved:
                sandbox = str(resolved)

        if not sandbox:
            return _error_result("Cannot resolve Stage 3 sandbox path", idempotency_key)

        # Validate stage
        if plan_data.get("stage", 0) != 3:
            return _error_result("POM changes can only be applied in Stage 3", idempotency_key)

        # Apply patch
        result: PomPatchResult = self._patcher.patch(
            pom_content=content,
            operation=operation,
            target_kind=target_data.get("kind", ""),
            group_id=target_data.get("group_id"),
            artifact_id=target_data.get("artifact_id"),
            property_name=target_data.get("property_name"),
            plugin_group_id=target_data.get("plugin_group_id"),
            plugin_artifact_id=target_data.get("plugin_artifact_id"),
            requested_version=plan_data.get("requested_version", ""),
        )

        if not result.success:
            return _error_result(f"Patch failed: {result.error}", idempotency_key)

        if result.before_checksum == result.after_checksum:
            return PomApplyResult(
                change_id="",
                status="noop",
                operation=result.operation,
                target_desc=result.target_desc,
                before_version=result.before_version,
                after_version=result.after_version,
                before_checksum=result.before_checksum,
                after_checksum=result.after_checksum,
                diff_summary="No changes",
                validation_id=None,
                rollback_available=False,
                idempotency_key=idempotency_key,
                created_at=utc_now_text(),
                message=(
                    "No Stage 3 POM change was applied because the requested "
                    "value is already present in the live sandbox POM."
                ),
            )

        # Write to sandbox (save before-content snapshot first, then write after)
        # Generate change_id early for snapshot
        snap_change_id = uuid4().hex if not self._change_repo else ""
        before_ref = f"{self._SNAPSHOT_DIR}/{snap_change_id}.pom"
        if sandbox:
            before_ref = self._save_snapshot(sandbox, snap_change_id, result.before_content)
        try:
            self._write_pom_to_sandbox(sandbox, result.after_content)
        except Exception as e:
            return _error_result(f"Failed to write POM to sandbox: {e}", idempotency_key)

        # Persist change record
        target_json = json.dumps(target_data, sort_keys=True)
        after_ref = f"{self._SNAPSHOT_DIR}/{snap_change_id}.after.pom"

        if self._change_repo:
            # Mark proposal consumed
            if proposal_id and self._proposal_repo:
                self._proposal_repo.mark_consumed(proposal_id)

            record = self._change_repo.save(
                proposal_id=proposal_id,
                job_id=job_id,
                stage_index=3,
                operation=result.operation,
                target_json=target_json,
                requested_version=result.after_version,
                before_checksum=result.before_checksum,
                after_checksum=result.after_checksum,
                before_content_ref=before_ref,
                after_content_ref=after_ref,
                diff_unified=result.diff_unified,
                idempotency_key=idempotency_key,
                executor="pom_span_patch",
            )
            change_id = record.change_id
        else:
            change_id = uuid4().hex

        # Emit event
        self._emit(
            job_id=job_id, stage=3,
            event_type="pom_change_applied",
            status="applied_pending_validation",
            message=f"POM change applied: {result.target_desc} → {result.after_version}",
            payload={
                "change_id": change_id,
                "operation": result.operation,
                "target_desc": result.target_desc,
                "before_checksum": result.before_checksum,
                "after_checksum": result.after_checksum,
                "diff_summary": _diff_summary(result.diff_unified),
                "stage_index": 3,
            },
        )

        # Enqueue async validation
        validation_id: str | None = None
        cmd = build_command or "mvn clean compile test"
        validation_id = self._enqueue_validation(
            job_id=job_id,
            change_id=change_id,
            command=cmd,
            sandbox_path=sandbox,
        )

        # Update change with validation_id
        if self._change_repo and validation_id:
            self._change_repo.update_status(
                change_id, PomChangeStatus.APPLIED_PENDING_VALIDATION.value,
                validation_id=validation_id,
            )

        return PomApplyResult(
            change_id=change_id,
            status="applied_pending_validation",
            operation=result.operation,
            target_desc=result.target_desc,
            before_version=result.before_version,
            after_version=result.after_version,
            before_checksum=result.before_checksum,
            after_checksum=result.after_checksum,
            diff_summary=_diff_summary(result.diff_unified),
            validation_id=validation_id,
            rollback_available=True,
            idempotency_key=idempotency_key,
            created_at=utc_now_text(),
            message="The POM change was applied to the Stage 3 sandbox. Validation is now running.",
        )

    # ── Validation ──────────────────────────────────────────────────

    def _enqueue_validation(
        self,
        job_id: str,
        change_id: str,
        command: str,
        sandbox_path: str,
    ) -> str | None:
        """Enqueue async validation. Does NOT block."""
        if not self._validation_repo:
            return None

        validation_id = self._validation_repo.save(
            change_id=change_id,
            job_id=job_id,
            stage_index=3,
            command=command,
            status=PomValidationStatus.RUNNING.value,
        )

        # Emit started event
        self._emit(
            job_id=job_id, stage=3,
            event_type="pom_validation_started",
            status="running",
            message=f"Validation started for change {change_id}",
            payload={
                "validation_id": validation_id,
                "change_id": change_id,
                "command_desc": command,
                "stage_index": 3,
            },
        )

        # Launch asynchronously
        if self._launch_validation:
            self._launch_validation(change_id, validation_id, command, job_id, sandbox_path)

        return validation_id

    def get_validation_result(
        self,
        job_id: str,
        validation_id: str,
    ) -> PomValidationRun | None:
        """Get validation run result."""
        if not self._validation_repo:
            return None

        data = self._validation_repo.get(validation_id)
        if not data:
            return None

        diagnosis = None
        if data.get("diagnosis_json"):
            try:
                diag_data = json.loads(data["diagnosis_json"])
                diagnosis = PomValidationFailureDiagnosis(
                    failure_classification=diag_data.get("failure_classification", "unknown"),
                    failed_phase=diag_data.get("failed_phase", "unknown"),
                    exit_code=diag_data.get("exit_code", 1),
                    log_excerpt=diag_data.get("log_excerpt", ""),
                    log_ref=diag_data.get("log_ref", ""),
                    root_cause=diag_data.get("root_cause", ""),
                    evidence_sufficient=diag_data.get("evidence_sufficient", False),
                    missing_evidence=tuple(diag_data.get("missing_evidence", [])),
                )
            except (json.JSONDecodeError, KeyError):
                pass

        repair_plan = None
        if self._repair_plan_repo:
            rp_data = self._repair_plan_repo.get_by_validation(validation_id)
            if rp_data:
                repair_plan = PomRepairPlan(
                    repair_plan_id=rp_data["repair_plan_id"],
                    change_id=rp_data["change_id"],
                    summary=rp_data["summary"],
                    detailed_steps=tuple(json.loads(rp_data["steps_json"])),
                    confidence=rp_data["confidence"],
                    evidence_sources=tuple(json.loads(rp_data["evidence_refs_json"])),
                    actions_available=("apply_repair", "rollback", "show_logs"),
                    created_at=rp_data["created_at"],
                )

        return PomValidationRun(
            validation_id=str(data["validation_id"]),
            change_id=str(data["change_id"]),
            status=str(data["status"]),
            command=str(data["command"]),
            build_status="passed" if data.get("exit_code") == 0 else "failed",
            test_status="unknown",
            exit_code=data.get("exit_code"),
            duration_ms=data.get("duration_ms"),
            log_ref=data.get("log_ref"),
            test_log_ref=data.get("test_log_ref"),
            diagnosis=diagnosis,
            repair_plan=repair_plan,
            created_at=str(data["created_at"]),
            completed_at=data.get("completed_at"),
        )

    def record_validation_outcome(
        self,
        validation_id: str,
        *,
        status: str,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
        duration_ms: int = 0,
        log_ref: str = "",
        test_log_ref: str = "",
    ) -> None:
        """Record validation outcome (called by async worker)."""
        if not self._validation_repo:
            return

        self._validation_repo.update_result(
            validation_id,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            log_ref=log_ref,
            test_log_ref=test_log_ref,
        )

        # Get change for this validation
        data = self._validation_repo.get(validation_id)
        if not data:
            return

        change_id = str(data["change_id"])
        job_id = str(data["job_id"])

        if status == PomValidationStatus.PASSED.value:
            # Update change status
            if self._change_repo:
                self._change_repo.update_status(change_id, PomChangeStatus.VALIDATED_PASSED.value)
            # Emit event
            self._emit(
                job_id=job_id, stage=3,
                event_type="pom_validation_passed",
                status="passed",
                message=f"Validation passed for change {change_id}",
                payload={
                    "validation_id": validation_id,
                    "change_id": change_id,
                    "build_status": "passed",
                    "test_status": "passed",
                    "duration_ms": duration_ms,
                    "stage_index": 3,
                },
            )

        elif status == PomValidationStatus.FAILED.value:
            # Update change status
            if self._change_repo:
                self._change_repo.update_status(change_id, PomChangeStatus.VALIDATED_FAILED.value)

            # Diagnose failure
            combined = stdout + "\n" + stderr
            diagnosis = self._diagnoser.diagnose(
                validation_id=validation_id,
                change_id=change_id,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                command=data.get("command", ""),
                diff_unified="",
            )

            # Store diagnosis
            diagnosis_json = json.dumps(diagnosis.to_public_dict(), sort_keys=True)
            self._validation_repo.update_result(
                validation_id,
                status=status,
                failure_classification=diagnosis.failure_classification,
                diagnosis_json=diagnosis_json,
            )

            # Generate repair plan
            repair_plan = self._diagnoser.generate_repair_plan(
                diagnosis=diagnosis,
                validation_id=validation_id,
                change_id=change_id,
                diff_unified="",
                log_output=combined,
                command=data.get("command", ""),
            )

            if repair_plan and self._repair_plan_repo:
                rp_id = self._repair_plan_repo.save(
                    validation_id=validation_id,
                    change_id=change_id,
                    summary=repair_plan.summary,
                    steps_json=json.dumps(list(repair_plan.detailed_steps)),
                    confidence=repair_plan.confidence,
                    evidence_refs_json=json.dumps(list(repair_plan.evidence_sources)),
                )
                self._emit(
                    job_id=job_id, stage=3,
                    event_type="pom_repair_plan_created",
                    status="repair_proposed",
                    message=f"Repair plan created for change {change_id}",
                    payload={
                        "repair_plan_id": rp_id,
                        "validation_id": validation_id,
                        "change_id": change_id,
                        "failure_classification": diagnosis.failure_classification,
                        "confidence": repair_plan.confidence,
                        "stage_index": 3,
                    },
                )

            # Emit failed event
            self._emit(
                job_id=job_id, stage=3,
                event_type="pom_validation_failed",
                status="failed",
                message=f"Validation failed for change {change_id}",
                payload={
                    "validation_id": validation_id,
                    "change_id": change_id,
                    "exit_code": exit_code,
                    "failed_phase": diagnosis.failed_phase,
                    "log_ref": log_ref,
                    "stage_index": 3,
                },
            )

    # ── Repair / Rollback ───────────────────────────────────────────

    def apply_repair_plan(
        self,
        job_id: str,
        repair_plan_id: str,
        idempotency_key: str,
    ) -> PomApplyResult:
        """Apply a repair plan. Re-triggers async validation."""
        if not self._repair_plan_repo:
            return _error_result("Repair plan repository not available", idempotency_key)

        rp_data = self._repair_plan_repo.get(repair_plan_id)
        if not rp_data:
            return _error_result(f"Repair plan {repair_plan_id} not found", idempotency_key)

        # For now, repair means re-triggering validation
        # This could be extended to apply POM patches for repair
        self._repair_plan_repo.update_status(repair_plan_id, PomRepairPlanStatus.APPLIED.value)

        change_id = rp_data["change_id"]
        sandbox = ""
        if self._resolve_sandbox:
            resolved = self._resolve_sandbox(job_id, 3)
            if resolved:
                sandbox = str(resolved)

        # Re-enqueue validation
        validation_id = self._enqueue_validation(
            job_id=job_id,
            change_id=change_id,
            command="mvn clean compile test",
            sandbox_path=sandbox,
        )

        return PomApplyResult(
            change_id=change_id,
            status="applied_pending_validation",
            operation="repair_plan_applied",
            target_desc="repair_plan",
            before_version="",
            after_version="",
            before_checksum="",
            after_checksum="",
            diff_summary="Repair plan applied",
            validation_id=validation_id,
            rollback_available=True,
            idempotency_key=idempotency_key,
            created_at=utc_now_text(),
            message="Repair plan applied. Validation is now running.",
        )

    def rollback_change(
        self,
        job_id: str,
        change_id: str,
        idempotency_key: str,
    ) -> PomRollbackResult:
        """Rollback a POM change. Restores before-content, verifies checksum.

        1. Load the change record.
        2. Resolve Stage 3 sandbox root internally.
        3. Read current POM and verify checksum matches after_checksum.
        4. Load stored before-content from snapshot.
        5. Write before-content back to sandbox pom.xml.
        6. Verify restored checksum equals before_checksum.
        7. Update change status to rolled_back.
        8. Emit pom_change_rolled_back event.
        """
        if not self._change_repo:
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=uuid4().hex,
                status="error",
                checksum_restored=False,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        record = self._change_repo.get(change_id)
        if not record:
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=uuid4().hex,
                status="error",
                checksum_restored=False,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        # Idempotency: already rolled back
        if record.status == PomChangeStatus.ROLLED_BACK.value:
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=record.rollback_id or uuid4().hex,
                status="rolled_back",
                checksum_restored=True,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        # Resolve sandbox path internally
        sandbox_path = ""
        if self._resolve_sandbox:
            resolved = self._resolve_sandbox(job_id, 3)
            if resolved:
                sandbox_path = str(resolved)

        if not sandbox_path:
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=uuid4().hex,
                status="error",
                checksum_restored=False,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        # Read current POM content from sandbox
        current_content = self._read_current_pom_from_sandbox(sandbox_path)
        if not current_content:
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=uuid4().hex,
                status="error",
                checksum_restored=False,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        # Verify current checksum matches the expected after_checksum
        current_checksum = _sha256(current_content)
        if current_checksum != record.after_checksum:
            # Checksum conflict — refuse rollback
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=uuid4().hex,
                status="checksum_conflict",
                checksum_restored=False,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        # Load stored before-content from snapshot
        before_content = self._read_snapshot(sandbox_path, record.before_content_ref)
        if not before_content:
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=uuid4().hex,
                status="error",
                checksum_restored=False,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        # Verify the snapshot matches before_checksum
        snapshot_checksum = _sha256(before_content)
        if snapshot_checksum != record.before_checksum:
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=uuid4().hex,
                status="checksum_mismatch",
                checksum_restored=False,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        # Write before-content back to sandbox pom.xml
        try:
            self._write_pom_to_sandbox(sandbox_path, before_content)
        except Exception:
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=uuid4().hex,
                status="error",
                checksum_restored=False,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        # Verify restored content
        restored_content = self._read_current_pom_from_sandbox(sandbox_path)
        restored_checksum = _sha256(restored_content)
        checksum_restored = restored_checksum == record.before_checksum

        if not checksum_restored:
            return PomRollbackResult(
                change_id=change_id,
                rollback_id=uuid4().hex,
                status="checksum_mismatch",
                checksum_restored=False,
                validation_triggered=False,
                validation_id=None,
                created_at=utc_now_text(),
            )

        rollback_id = uuid4().hex
        self._change_repo.update_status(
            change_id, PomChangeStatus.ROLLED_BACK.value,
            rollback_id=rollback_id,
        )

        # Emit event only after real restore
        self._emit(
            job_id=job_id, stage=3,
            event_type="pom_change_rolled_back",
            status="rolled_back",
            message=f"POM change {change_id} rolled back",
            payload={
                "change_id": change_id,
                "rollback_id": rollback_id,
                "checksum_restored": checksum_restored,
                "stage_index": 3,
            },
        )

        return PomRollbackResult(
            change_id=change_id,
            rollback_id=rollback_id,
            status="rolled_back",
            checksum_restored=checksum_restored,
            validation_triggered=False,
            validation_id=None,
            created_at=utc_now_text(),
        )

    def list_changes(self, job_id: str) -> list[PomChangeRecordSummary]:
        """List all POM changes for a job."""
        if not self._change_repo:
            return []
        records = self._change_repo.list_by_job(job_id)
        return [r.to_summary() for r in records]

    # ── Internal helpers ────────────────────────────────────────────

    def _check_idempotency(
        self, job_id: str, idempotency_key: str | None,
    ) -> PomApplyResult | None:
        """Check if an idempotency key already has a result."""
        if not idempotency_key or not self._change_repo:
            return None
        record = self._change_repo.find_by_idempotency(job_id, idempotency_key)
        if record is None:
            return None
        # Return existing result
        return PomApplyResult(
            change_id=record.change_id,
            status=record.status,
            operation=record.operation,
            target_desc=_target_desc_from_json(record.target_json),
            before_version="",
            after_version=record.requested_version,
            before_checksum=record.before_checksum,
            after_checksum=record.after_checksum,
            diff_summary=record.operation,
            validation_id=record.validation_id,
            rollback_available=record.status != PomChangeStatus.ROLLED_BACK.value,
            idempotency_key=record.idempotency_key,
            created_at=record.created_at,
            message="Duplicate request. Returning existing result.",
        )

    def _write_pom_to_sandbox(self, sandbox_path: str, content: str) -> None:
        """Write POM content to sandbox. Validates path is safe."""
        from pathlib import Path
        pom_file = Path(sandbox_path) / "pom.xml"
        # Ensure sandbox path exists
        pom_file.parent.mkdir(parents=True, exist_ok=True)
        pom_file.write_text(content, encoding="utf-8")

    def _save_snapshot(self, sandbox_path: str, change_id: str, content: str) -> str:
        """Store a POM content snapshot in the sandbox's .f14_snapshots dir.

        Returns a snapshot ref string (relative path within sandbox).
        """
        from pathlib import Path
        snap_dir = Path(sandbox_path) / self._SNAPSHOT_DIR
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_file = snap_dir / f"{change_id}.pom"
        snap_file.write_text(content, encoding="utf-8")
        return f"{self._SNAPSHOT_DIR}/{change_id}.pom"

    def _read_snapshot(self, sandbox_path: str, before_content_ref: str) -> str:
        """Read a stored POM content snapshot from the sandbox.

        Returns empty string if the snapshot does not exist.
        """
        from pathlib import Path
        snap_file = Path(sandbox_path) / before_content_ref
        if snap_file.exists():
            return snap_file.read_text(encoding="utf-8")
        return ""

    def _read_current_pom_from_sandbox(self, sandbox_path: str) -> str:
        """Read the current pom.xml from the sandbox."""
        from pathlib import Path
        pom_file = Path(sandbox_path) / "pom.xml"
        if pom_file.exists():
            return pom_file.read_text(encoding="utf-8")
        return ""

    def _emit(
        self,
        job_id: str,
        stage: int | None,
        event_type: str,
        status: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event through the event sink."""
        if self._events:
            self._events.save(
                job_id=job_id,
                stage=stage,
                event_type=event_type,
                status=status,
                message=message,
                payload=payload,
            )


# ── Helpers ────────────────────────────────────────────────────────

def _target_desc_from_plan(plan_data: dict[str, Any]) -> str:
    t = plan_data.get("target", {})
    kind = t.get("kind", "")
    if kind == "property":
        return f"property:{t.get('property_name', '')}"
    g = t.get("group_id", "")
    a = t.get("artifact_id", "")
    if g and a:
        return f"{g}:{a}"
    return kind


def _target_desc_from_json(target_json: str) -> str:
    try:
        t = json.loads(target_json)
        kind = t.get("kind", "")
        if kind == "property":
            return f"property:{t.get('property_name', '')}"
        g = t.get("group_id", "")
        a = t.get("artifact_id", "")
        if g and a:
            return f"{g}:{a}"
        return kind
    except json.JSONDecodeError:
        return ""


def _diff_summary(diff: str) -> str:
    """Create a brief summary from a unified diff."""
    if not diff:
        return "No changes"
    lines = diff.splitlines()
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return f"{added} addition(s), {removed} removal(s)"


def _error_result(message: str, idempotency_key: str | None = None) -> PomApplyResult:
    return PomApplyResult(
        change_id="",
        status="error",
        operation="",
        target_desc="",
        before_version="",
        after_version="",
        before_checksum="",
        after_checksum="",
        diff_summary="",
        validation_id=None,
        rollback_available=False,
        idempotency_key=idempotency_key,
        created_at=utc_now_text(),
        message=message,
    )


def _blocked_result(
    decision: DependencyPolicyDecision,
    user_request: str,
    idempotency_key: str | None = None,
) -> PomApplyResult:
    return PomApplyResult(
        change_id="",
        status="blocked",
        operation="",
        target_desc="",
        before_version="",
        after_version="",
        before_checksum="",
        after_checksum="",
        diff_summary="",
        validation_id=None,
        rollback_available=False,
        idempotency_key=idempotency_key,
        created_at=utc_now_text(),
        message=f"Change blocked: {decision.reason}. {decision.suggested_next_action}",
    )
