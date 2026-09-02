"""V2 repair/proposal flow — failed stage evidence to bounded repair."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
    V2RepairProposalRecord,
    V2SandboxActionRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
)
from migration_factory.control_tower.application.v2_reviewer_service import (
    V2ReviewerService,
)
from migration_factory.repair_loop.ledger import (
    append_attempt,
    base_attempt,
    new_ledger,
    write_patch_draft,
    write_ledger,
    write_patch_attempt_result,
)
from migration_factory.repair_loop.patch_apply import apply_patch_to_sandbox, rollback_patch
from migration_factory.repair_loop.patch_gate import evaluate_patch_proposal
from migration_factory.repair_loop.validation_runner import (
    ValidationResult,
    run_validation_after_patch,
)


ValidationRunner = Callable[..., ValidationResult]
RepairEventRecorder = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class RepairProposal:
    proposal_id: str
    command_id: str
    failure_summary: str
    hypothesis: str
    patch_summary: str
    affected_paths: tuple[str, ...]
    status: str  # draft, proposed, approved, rejected, applied
    approval_checksum: str | None
    created_at: str
    proposal_checksum: str = ""
    # F05: revision metadata (None for non-revision proposals)
    source_proposal_id: str | None = None
    revision_of: str | None = None
    revision_number: int | None = None
    context_pack_checksum: str | None = None
    allowed_scope: str | None = None


@dataclass(frozen=True)
class SandboxAction:
    action_id: str
    proposal_id: str
    target_path: str
    patch_content: str
    status: str  # pending, applied, failed, rolled_back
    result_summary: str
    created_at: str


class V2RepairFlowService:
    """Convert failed command evidence into repair proposals and actions.

    - Proposal created from failure context
    - Approval with checksum required before patch application
    - Reviewer critique gate: approval requires latest accepted critique
      matching current proposal_checksum and context_pack_checksum (F07)
    - Actions are sandbox-only (no legacy source mutation)
    - Rollback on failure
    """

    def __init__(
        self,
        repair_repo: SqliteV2RepairRepository | None = None,
        reviewer_service: V2ReviewerService | None = None,
        job_repo: SqliteV2JobRepository | None = None,
        setup_repo: SqliteV2SetupRepository | None = None,
        command_repo: SqliteV2CommandRepository | None = None,
    ) -> None:
        self._proposals: dict[str, RepairProposal] = {}
        self._actions: dict[str, SandboxAction] = {}
        self._repo = repair_repo
        self._reviewer = reviewer_service or V2ReviewerService()
        self._job_repo = job_repo
        self._setup_repo = setup_repo
        self._command_repo = command_repo

    def create_proposal(
        self,
        command_id: str,
        failure_summary: str,
        hypothesis: str,
        patch_summary: str,
        affected_paths: tuple[str, ...],
    ) -> RepairProposal:
        proposal_checksum = self._proposal_checksum(
            command_id=command_id,
            failure_summary=failure_summary,
            hypothesis=hypothesis,
            patch_summary=patch_summary,
            affected_paths=affected_paths,
        )
        proposal = RepairProposal(
            proposal_id=uuid4().hex,
            command_id=command_id,
            failure_summary=failure_summary,
            hypothesis=hypothesis,
            patch_summary=patch_summary,
            affected_paths=affected_paths,
            status="draft",
            approval_checksum=None,
            created_at=utc_now_text(),
            proposal_checksum=proposal_checksum,
        )
        self._proposals[proposal.proposal_id] = proposal
        # Persist if repo available
        if self._repo is not None:
            record = V2RepairProposalRecord(
                proposal_id=proposal.proposal_id,
                command_id=proposal.command_id,
                failure_summary=proposal.failure_summary,
                hypothesis=proposal.hypothesis,
                patch_summary=proposal.patch_summary,
                affected_paths_json=json.dumps(list(proposal.affected_paths), separators=(",", ":")),
                status=proposal.status,
                approval_checksum=proposal.approval_checksum,
                created_at=proposal.created_at,
                proposal_checksum=proposal.proposal_checksum,
                source_proposal_id=proposal.source_proposal_id,
                revision_of=proposal.revision_of,
                revision_number=proposal.revision_number,
                context_pack_checksum=proposal.context_pack_checksum,
                allowed_scope=proposal.allowed_scope,
            )
            self._repo.save_proposal(record)
        return proposal

    def create_revision_proposal(
        self,
        *,
        command_id: str,
        source_proposal_id: str,
        failure_summary: str,
        hypothesis: str,
        patch_summary: str,
        affected_paths: tuple[str, ...],
        revision_instruction: str = "",
        context_pack_checksum: str = "",
        allowed_scope: str = "any",
        revision_number: int = 1,
    ) -> RepairProposal:
        """Create a revised proposal draft from a source proposal.

        F05: Never mutates the source proposal. The new proposal is a
        separate draft with revision metadata linking back to the source.
        The caller must first validate binding via V2AssistantActionResolver.
        """
        proposal_checksum = self._proposal_checksum(
            command_id=command_id,
            failure_summary=failure_summary,
            hypothesis=hypothesis,
            patch_summary=patch_summary,
            affected_paths=affected_paths,
            source_proposal_id=source_proposal_id,
            revision_of=source_proposal_id,
            revision_number=revision_number,
            context_pack_checksum=context_pack_checksum,
            allowed_scope=allowed_scope,
        )
        proposal = RepairProposal(
            proposal_id=uuid4().hex,
            command_id=command_id,
            failure_summary=failure_summary,
            hypothesis=hypothesis,
            patch_summary=patch_summary,
            affected_paths=affected_paths,
            status="draft",
            approval_checksum=None,
            created_at=utc_now_text(),
            proposal_checksum=proposal_checksum,
            source_proposal_id=source_proposal_id,
            revision_of=source_proposal_id,
            revision_number=revision_number,
            context_pack_checksum=context_pack_checksum,
            allowed_scope=allowed_scope,
        )
        self._proposals[proposal.proposal_id] = proposal
        if self._repo is not None:
            record = V2RepairProposalRecord(
                proposal_id=proposal.proposal_id,
                command_id=proposal.command_id,
                failure_summary=proposal.failure_summary,
                hypothesis=proposal.hypothesis,
                patch_summary=proposal.patch_summary,
                affected_paths_json=json.dumps(list(proposal.affected_paths), separators=(",", ":")),
                status=proposal.status,
                approval_checksum=proposal.approval_checksum,
                created_at=proposal.created_at,
                proposal_checksum=proposal.proposal_checksum,
                source_proposal_id=proposal.source_proposal_id,
                revision_of=proposal.revision_of,
                revision_number=proposal.revision_number,
                context_pack_checksum=proposal.context_pack_checksum,
                allowed_scope=proposal.allowed_scope,
            )
            self._repo.save_proposal(record)
        return proposal

    def approve_proposal(
        self,
        proposal_id: str,
        approval_checksum: str,
        *,
        proposal_checksum: str,
        context_pack_checksum: str,
        reviewer_critique_id: str | None = None,
    ) -> RepairProposal:
        """Approve a repair proposal.

        F07: Requires reviewer gate — a latest accepted critique must match
        the given proposal_checksum and context_pack_checksum. No bypass.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None and self._repo is not None:
            record = self._repo.get_proposal(proposal_id)
            if record is not None:
                proposal = RepairProposal(
                    proposal_id=record.proposal_id,
                    command_id=record.command_id,
                    failure_summary=record.failure_summary,
                    hypothesis=record.hypothesis,
                    patch_summary=record.patch_summary,
                    affected_paths=tuple(json.loads(record.affected_paths_json)),
                    status=record.status,
                    approval_checksum=record.approval_checksum,
                    created_at=record.created_at,
                    proposal_checksum=record.proposal_checksum
                    or self._proposal_checksum(
                        command_id=record.command_id,
                        failure_summary=record.failure_summary,
                        hypothesis=record.hypothesis,
                        patch_summary=record.patch_summary,
                        affected_paths=tuple(json.loads(record.affected_paths_json)),
                        source_proposal_id=record.source_proposal_id,
                        revision_of=record.revision_of,
                        revision_number=record.revision_number,
                        context_pack_checksum=record.context_pack_checksum,
                        allowed_scope=record.allowed_scope,
                    ),
                    source_proposal_id=record.source_proposal_id,
                    revision_of=record.revision_of,
                    revision_number=record.revision_number,
                    context_pack_checksum=record.context_pack_checksum,
                    allowed_scope=record.allowed_scope,
                )
                self._proposals[proposal_id] = proposal
        if proposal is None:
            raise ValueError(f"Proposal {proposal_id!r} not found")
        if proposal.status != "draft":
            raise ValueError(f"Proposal {proposal_id!r} is already {proposal.status}")

        # F07: Reviewer gate — mandatory. Requires latest accepted critique
        # matching the current proposal_checksum and context_pack_checksum.
        accepted = self._reviewer.check_reviewer_gate(
            proposal_id=proposal_id,
            proposal_checksum=proposal_checksum,
            context_pack_checksum=context_pack_checksum,
        )
        if accepted is None:
            raise ValueError(
                f"Proposal {proposal_id!r} blocked by reviewer gate: "
                f"no accepted critique matches current proposal_checksum "
                f"{proposal_checksum!r} and context_pack_checksum "
                f"{context_pack_checksum!r}"
            )
        reviewer_critique_id = accepted.critique_id

        updated = RepairProposal(
            proposal_id=proposal.proposal_id,
            command_id=proposal.command_id,
            failure_summary=proposal.failure_summary,
            hypothesis=proposal.hypothesis,
            patch_summary=proposal.patch_summary,
            affected_paths=proposal.affected_paths,
            status="approved",
            approval_checksum=approval_checksum,
            created_at=proposal.created_at,
            proposal_checksum=proposal.proposal_checksum,
        )
        self._proposals[proposal_id] = updated
        # Persist if repo available
        if self._repo is not None:
            self._repo.update_proposal_status(proposal_id, "approved", approval_checksum)
        return updated

    def apply_patch(
        self,
        proposal_id: str,
        target_path: str,
        patch_content: str,
        *,
        run_dir: str | Path,
        sandbox_path: str | Path,
        legacy_path: str | Path,
        deterministic_rule_id: str,
        risk: str = "LOW",
        requires_human_review: bool = False,
        expected_validation: tuple[str, ...] = (),
        limitations: tuple[str, ...] = (),
        failure_classification: dict[str, Any] | None = None,
        h2_required: bool = False,
        run_id: str = "",
        binding_checksum: str | None = None,
        validation_runner: ValidationRunner = run_validation_after_patch,
        event_recorder: RepairEventRecorder | None = None,
    ) -> SandboxAction:
        proposal = self._proposals.get(proposal_id)
        if proposal is None and self._repo is not None:
            record = self._repo.get_proposal(proposal_id)
            if record is not None:
                proposal = RepairProposal(
                    proposal_id=record.proposal_id,
                    command_id=record.command_id,
                    failure_summary=record.failure_summary,
                    hypothesis=record.hypothesis,
                    patch_summary=record.patch_summary,
                    affected_paths=tuple(json.loads(record.affected_paths_json)),
                    status=record.status,
                    approval_checksum=record.approval_checksum,
                    created_at=record.created_at,
                    proposal_checksum=record.proposal_checksum
                    or self._proposal_checksum(
                        command_id=record.command_id,
                        failure_summary=record.failure_summary,
                        hypothesis=record.hypothesis,
                        patch_summary=record.patch_summary,
                        affected_paths=tuple(json.loads(record.affected_paths_json)),
                        source_proposal_id=record.source_proposal_id,
                        revision_of=record.revision_of,
                        revision_number=record.revision_number,
                        context_pack_checksum=record.context_pack_checksum,
                        allowed_scope=record.allowed_scope,
                    ),
                    source_proposal_id=record.source_proposal_id,
                    revision_of=record.revision_of,
                    revision_number=record.revision_number,
                    context_pack_checksum=record.context_pack_checksum,
                    allowed_scope=record.allowed_scope,
                )
                self._proposals[proposal_id] = proposal
        if proposal is None:
            raise ValueError(f"Proposal {proposal_id!r} not found")
        if proposal.status != "approved":
            raise ValueError(f"Proposal {proposal_id!r} must be approved first")

        run_path = Path(run_dir)
        resolved_run_id = run_id or proposal.command_id
        classification = dict(failure_classification or {})
        failure_type = str(classification.get("failure_type") or proposal.failure_summary or "V2_REPAIR_PROPOSAL")
        artifact_refs: dict[str, str] = {}
        ledger = new_ledger(
            run_id=resolved_run_id,
            enabled=True,
            auto_apply_enabled=False,
            max_attempts=1,
            artifact_refs=artifact_refs,
        )
        ledger_ref = write_ledger(run_path, ledger)
        artifact_refs["repair_ledger"] = str(ledger_ref)

        attempt = base_attempt(
            attempt=1,
            failure_type=failure_type,
            classification_ref="",
            repair_plan_ref=proposal.proposal_id,
        )
        if binding_checksum:
            attempt["binding_checksum"] = binding_checksum
        attempt["proposal_id"] = proposal.proposal_id

        repair_loop_proposal = {
            "proposal_id": proposal.proposal_id,
            "deterministic_rule_id": deterministic_rule_id,
            "risk": risk,
            "requires_human_review": requires_human_review,
            "description": proposal.hypothesis,
            "unified_diff": patch_content,
            "expected_validation": list(expected_validation),
            "limitations": list(limitations),
        }
        repair_proposal_checksum = sha256_canonical_json(repair_loop_proposal)
        draft_path = write_patch_draft(
            run_dir=run_path,
            attempt=1,
            payload={
                "schema_version": "1.0",
                "proposal_id": proposal.proposal_id,
                "repair_proposal_checksum": repair_proposal_checksum,
                "target_path": target_path,
                "deterministic_rule_id": deterministic_rule_id,
                "risk": risk,
                "requires_human_review": requires_human_review,
                "binding_checksum": binding_checksum,
                "h2_required": h2_required,
                **repair_loop_proposal,
            },
        )
        artifact_refs["repair_patch_draft"] = str(draft_path)
        gate = evaluate_patch_proposal(
            proposal=repair_loop_proposal,
            sandbox_path=sandbox_path,
            run_dir=run_path,
            legacy_path=legacy_path,
            failure_classification=classification,
            h2_required=h2_required,
        )
        attempt["patch_gate_status"] = gate.status
        attempt["deterministic_rule_id"] = gate.rule_id
        attempt["touched_paths"] = list(gate.touched_paths)
        attempt["repair_proposal_checksum"] = repair_proposal_checksum
        attempt["repair_patch_draft_ref"] = str(draft_path)
        resolved_target_path = ",".join(gate.touched_paths) or "<unresolved>"
        self._emit_repair_event(
            event_recorder,
            "repair_patch_gate_completed",
            {
                "proposal_id": proposal.proposal_id,
                "repair_proposal_checksum": repair_proposal_checksum,
                "repair_patch_draft_ref": str(draft_path),
                "binding_checksum": binding_checksum,
                "patch_gate_status": gate.status,
                "deterministic_rule_id": gate.rule_id,
                "touched_paths": list(gate.touched_paths),
                "ledger_ref": artifact_refs["repair_ledger"],
            },
        )

        if gate.status != "ALLOWED":
            attempt["status"] = "BLOCKED"
            append_attempt(ledger, attempt)
            ledger["artifact_refs"] = artifact_refs
            ledger["final_status"] = "REPAIR_BLOCKED_HUMAN_REVIEW" if gate.human_review_required else "REPAIR_BLOCKED"
            ledger.setdefault("warnings", []).append(gate.reason)
            write_ledger(run_path, ledger)
            return self._record_action(
                proposal_id=proposal_id,
                target_path=resolved_target_path,
                patch_content=patch_content,
                status="failed",
                result_summary=f"Patch gate blocked repair proposal: {gate.reason}",
            )

        apply_result = apply_patch_to_sandbox(
            run_dir=run_path,
            sandbox_path=sandbox_path,
            attempt=1,
            unified_diff=patch_content,
            touched_paths=list(gate.touched_paths),
        )
        attempt["patch_ref"] = str(apply_result.patch_path)
        apply_artifact_ref, apply_artifact_checksum = self._write_repair_json_artifact(
            run_path,
            "repair_apply_result.json",
            {
                "proposal_id": proposal.proposal_id,
                "attempt": 1,
                "status": apply_result.status,
                "reason": apply_result.reason,
                "touched_paths": list(apply_result.touched_paths),
                "created_paths": list(apply_result.created_paths),
                "patch_ref": str(apply_result.patch_path),
                "binding_checksum": binding_checksum or "",
            },
        )
        artifact_refs["repair_apply_result"] = str(apply_artifact_ref)
        attempt["repair_apply_result_ref"] = str(apply_artifact_ref)
        attempt["repair_apply_result_checksum"] = apply_artifact_checksum
        if apply_result.status != "APPLIED":
            result_path = write_patch_attempt_result(
                run_dir=run_path,
                run_id=resolved_run_id,
                attempt=1,
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
            terminal_ref, terminal_checksum = self._write_repair_json_artifact(
                run_path,
                "repair_terminal_failure.json",
                {
                    "proposal_id": proposal.proposal_id,
                    "attempt": 1,
                    "status": "REPAIR_FAILED",
                    "reason": apply_result.reason,
                    "max_attempts_exhausted": True,
                    "binding_checksum": binding_checksum or "",
                },
            )
            artifact_refs["repair_terminal_failure"] = str(terminal_ref)
            attempt["repair_terminal_failure_ref"] = str(terminal_ref)
            attempt["repair_terminal_failure_checksum"] = terminal_checksum
            append_attempt(ledger, attempt)
            ledger["artifact_refs"] = artifact_refs
            ledger["final_status"] = "REPAIR_FAILED"
            write_ledger(run_path, ledger)
            return self._record_action(
                proposal_id=proposal_id,
                target_path=resolved_target_path,
                patch_content=patch_content,
                status="failed",
                result_summary=f"Repair patch was rejected in sandbox: {apply_result.reason}",
            )
        self._emit_repair_event(
            event_recorder,
            "repair_patch_applied",
            {
                "proposal_id": proposal.proposal_id,
                "patch_ref": str(apply_result.patch_path),
                "patch_status": apply_result.status,
                "touched_paths": list(apply_result.touched_paths),
                "ledger_ref": artifact_refs["repair_ledger"],
            },
        )

        validation: ValidationResult = validation_runner(
            run_id=resolved_run_id,
            run_dir=run_path,
            sandbox_path=sandbox_path,
            attempt=1,
            h2_required=h2_required,
            h2_enabled=h2_required,
        )
        attempt["validation"] = {
            "build_status": validation.build_status,
            "test_status": validation.test_status,
            "h2_status": validation.h2_status,
        }
        rerun_ref, rerun_checksum = self._write_repair_json_artifact(
            run_path,
            "repair_rerun_result.json",
            {
                "proposal_id": proposal.proposal_id,
                "attempt": 1,
                "passed": validation.passed,
                "build_status": validation.build_status,
                "test_status": validation.test_status,
                "h2_status": validation.h2_status,
                "validation_commands": list(validation.validation_commands),
                "warnings": list(validation.warnings),
                "errors": list(validation.errors),
                "artifact_refs": dict(validation.artifact_refs),
                "binding_checksum": binding_checksum or "",
            },
        )
        artifact_refs["repair_rerun_result"] = str(rerun_ref)
        attempt["repair_rerun_result_ref"] = str(rerun_ref)
        attempt["repair_rerun_result_checksum"] = rerun_checksum
        artifact_refs.update(validation.artifact_refs)
        self._emit_repair_event(
            event_recorder,
            "repair_validation_completed",
            {
                "proposal_id": proposal.proposal_id,
                "passed": validation.passed,
                "build_status": validation.build_status,
                "test_status": validation.test_status,
                "h2_status": validation.h2_status,
                "artifact_refs": dict(validation.artifact_refs),
                "ledger_ref": artifact_refs["repair_ledger"],
            },
        )

        if validation.passed:
            result_path = write_patch_attempt_result(
                run_dir=run_path,
                run_id=resolved_run_id,
                attempt=1,
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
            proof_ref, proof_checksum = self._write_repair_json_artifact(
                run_path,
                "repair_proof.json",
                {
                    "proposal_id": proposal.proposal_id,
                    "attempt": 1,
                    "status": "REPAIR_VALIDATED",
                    "patch_result_ref": str(result_path),
                    "repair_apply_result_ref": str(apply_artifact_ref),
                    "repair_rerun_result_ref": str(rerun_ref),
                    "binding_checksum": binding_checksum or "",
                    "repair_apply_result_checksum": apply_artifact_checksum,
                    "repair_rerun_result_checksum": rerun_checksum,
                },
            )
            artifact_refs["repair_proof"] = str(proof_ref)
            attempt["repair_proof_ref"] = str(proof_ref)
            attempt["repair_proof_checksum"] = proof_checksum
            append_attempt(ledger, attempt)
            ledger["artifact_refs"] = artifact_refs
            ledger["final_status"] = "REPAIR_VALIDATED"
            write_ledger(run_path, ledger)
            action = self._record_action(
                proposal_id=proposal_id,
                target_path=resolved_target_path,
                patch_content=patch_content,
                status="applied",
                result_summary=f"Patch applied to {resolved_target_path} and validation passed",
            )
            self._mark_proposal_applied(proposal)
            return action

        rolled_back, rollback_reason = rollback_patch(
            sandbox_path=sandbox_path,
            snapshot_dir=apply_result.snapshot_dir,
            touched_paths=apply_result.touched_paths,
            created_paths=apply_result.created_paths,
        )
        self._emit_repair_event(
            event_recorder,
            "repair_rollback_completed",
            {
                "proposal_id": proposal.proposal_id,
                "rollback_status": "ROLLED_BACK" if rolled_back else "ROLLBACK_FAILED",
                "reason": rollback_reason,
            },
        )
        attempt["rollback"] = {
            "performed": True,
            "reason": "; ".join(validation.errors) or "validation failed",
            "status": "ROLLED_BACK" if rolled_back else "ROLLBACK_FAILED",
        }
        rollback_ref, rollback_checksum = self._write_repair_json_artifact(
            run_path,
            "repair_rollback_result.json",
            {
                "proposal_id": proposal.proposal_id,
                "attempt": 1,
                "performed": True,
                "status": "ROLLED_BACK" if rolled_back else "ROLLBACK_FAILED",
                "reason": rollback_reason,
                "validation_errors": list(validation.errors),
                "binding_checksum": binding_checksum or "",
            },
        )
        artifact_refs["repair_rollback_result"] = str(rollback_ref)
        attempt["repair_rollback_result_ref"] = str(rollback_ref)
        attempt["repair_rollback_result_checksum"] = rollback_checksum
        result_path = write_patch_attempt_result(
            run_dir=run_path,
            run_id=resolved_run_id,
            attempt=1,
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
        terminal_ref, terminal_checksum = self._write_repair_json_artifact(
            run_path,
            "repair_terminal_failure.json",
            {
                "proposal_id": proposal.proposal_id,
                "attempt": 1,
                "status": "REPAIR_FAILED",
                "reason": rollback_reason,
                "validation_errors": list(validation.errors),
                "rollback_status": attempt["rollback"]["status"],
                "max_attempts_exhausted": True,
                "binding_checksum": binding_checksum or "",
            },
        )
        artifact_refs["repair_terminal_failure"] = str(terminal_ref)
        attempt["repair_terminal_failure_ref"] = str(terminal_ref)
        attempt["repair_terminal_failure_checksum"] = terminal_checksum
        append_attempt(ledger, attempt)
        ledger["artifact_refs"] = artifact_refs
        ledger["final_status"] = "REPAIR_FAILED"
        if not rolled_back:
            ledger.setdefault("errors", []).append("rollback failed after repair validation failure")
        write_ledger(run_path, ledger)
        return self._record_action(
            proposal_id=proposal_id,
            target_path=resolved_target_path,
            patch_content=patch_content,
            status="rolled_back" if rolled_back else "failed",
            result_summary=rollback_reason,
        )

    def _emit_repair_event(
        self,
        recorder: RepairEventRecorder | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if recorder is not None:
            recorder(event_type, payload)

    @staticmethod
    def _write_repair_json_artifact(
        run_path: Path,
        filename: str,
        payload: dict[str, Any],
    ) -> tuple[Path, str]:
        repairs_dir = run_path / "repairs"
        repairs_dir.mkdir(parents=True, exist_ok=True)
        checksum = sha256_canonical_json(payload)
        artifact_payload = {
            "schema_version": "1.0",
            "artifact_checksum": checksum,
            **payload,
        }
        artifact_ref = repairs_dir / filename
        artifact_ref.write_text(
            json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return artifact_ref, checksum

    def _record_action(
        self,
        *,
        proposal_id: str,
        target_path: str,
        patch_content: str,
        status: str,
        result_summary: str,
    ) -> SandboxAction:
        action = SandboxAction(
            action_id=uuid4().hex,
            proposal_id=proposal_id,
            target_path=target_path,
            patch_content=patch_content,
            status=status,
            result_summary=result_summary,
            created_at=utc_now_text(),
        )
        self._actions[action.action_id] = action
        if self._repo is not None:
            action_record = V2SandboxActionRecord(
                action_id=action.action_id,
                proposal_id=action.proposal_id,
                target_path=action.target_path,
                patch_content=action.patch_content,
                status=action.status,
                result_summary=action.result_summary,
                created_at=action.created_at,
            )
            self._repo.save_action(action_record)
        return action

    def apply_approved_proposal(
        self,
        *,
        proposal_id: str,
        command_id: str,
        validation_runner: ValidationRunner | None = None,
        event_recorder: RepairEventRecorder | None = None,
    ) -> SandboxAction:
        """Fail closed: legacy draft patches are not authoritative for F5."""
        raise ValueError(
            "Legacy repair proposal apply is disabled. "
            "Use apply_reviewed_repair_diff with checksum-bound reviewed artifacts."
        )
        proposal = self._proposals.get(proposal_id)
        if proposal is None and self._repo is not None:
            record = self._repo.get_proposal(proposal_id)
            if record is not None:
                proposal = self._record_to_proposal(record)
                self._proposals[proposal_id] = proposal
        if proposal is None:
            raise ValueError(f"Proposal {proposal_id!r} not found")
        if proposal.status != "approved":
            raise ValueError(f"Proposal {proposal_id!r} must be approved first")
        if proposal.command_id != command_id:
            raise ValueError(
                f"Proposal {proposal_id!r} is bound to command {proposal.command_id!r}, "
                f"not {command_id!r}"
            )
        if self._job_repo is None or self._setup_repo is None or self._command_repo is None:
            raise ValueError("Repair approval apply requires job, setup, and command repositories")
        if validation_runner is None:
            validation_runner = run_validation_after_patch

        command = self._command_repo.get(command_id)
        if command is None:
            raise ValueError(f"Command {command_id!r} not found")
        job = self._job_repo.get(command.job_id)
        if job is None:
            raise ValueError(f"Job {command.job_id!r} not found for command {command_id!r}")
        setup = self._setup_repo.get(job.setup_id)
        if setup is None:
            raise ValueError(f"Setup {job.setup_id!r} not found for job {job.job_id!r}")

        result_json = command.result_json or ""
        result_data: dict[str, Any] = {}
        if result_json:
            try:
                parsed = json.loads(result_json)
                if isinstance(parsed, dict):
                    result_data = parsed
            except (json.JSONDecodeError, TypeError):
                result_data = {}

        run_id = str(result_data.get("run_id") or command.command_id)
        output_root = str(
            result_data.get("modernized_app_path")
            or result_data.get("output_root_dir")
            or setup.output_parent_path
        )
        run_dir = Path(output_root) / ".migration" / "runs" / run_id
        sandbox_path = str(
            result_data.get("sandbox_path")
            or result_data.get("modernized_app_path")
            or result_data.get("output_app_path")
            or ""
        )
        if not sandbox_path:
            raise ValueError(f"Sandbox path could not be resolved for command {command_id!r}")

        draft_path = run_dir / "repairs" / "patch_draft_1.json"
        if not draft_path.is_file():
            raise ValueError(f"Repair patch draft not found at {draft_path}")

        try:
            draft_payload = json.loads(draft_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            raise ValueError(f"Repair patch draft could not be read: {draft_path}") from exc
        if not isinstance(draft_payload, dict):
            raise ValueError(f"Repair patch draft is invalid: {draft_path}")
        if draft_payload.get("proposal_id") != proposal_id:
            raise ValueError(f"Repair patch draft proposal mismatch at {draft_path}")
        if draft_payload.get("repair_proposal_checksum") != proposal.proposal_checksum:
            raise ValueError(
                "Repair patch draft checksum does not match the approved proposal"
            )

        affected_paths = list(proposal.affected_paths)
        target_path = affected_paths[0] if affected_paths else ""
        if not target_path:
            raise ValueError(f"Proposal {proposal_id!r} does not declare a target path")

        expected_validation = tuple(
            str(item) for item in draft_payload.get("expected_validation", [])
            if isinstance(item, str)
        )
        limitations = tuple(
            str(item) for item in draft_payload.get("limitations", [])
            if isinstance(item, str)
        )

        return self.apply_patch(
            proposal_id=proposal_id,
            target_path=target_path,
            patch_content=str(draft_payload.get("unified_diff", "")),
            run_id=run_id,
            run_dir=run_dir,
            sandbox_path=sandbox_path,
            legacy_path=setup.legacy_app_path,
            deterministic_rule_id=str(draft_payload.get("deterministic_rule_id", "")),
            risk=str(draft_payload.get("risk", "LOW")),
            requires_human_review=bool(draft_payload.get("requires_human_review", False)),
            expected_validation=expected_validation,
            limitations=limitations,
            failure_classification=dict(draft_payload.get("failure_classification") or {}),
            h2_required=bool(draft_payload.get("h2_required", False)),
            binding_checksum=str(draft_payload.get("binding_checksum") or "") or None,
            validation_runner=validation_runner,
            event_recorder=event_recorder,
        )

    def apply_reviewed_repair_diff(
        self,
        *,
        proposal_id: str,
        final_diff_ref: str | Path,
        final_diff_checksum: str,
        reviewer_output_checksum: str,
        expected_reviewer_output_checksum: str,
        policy_validation_checksum: str,
        expected_policy_validation_checksum: str,
        policy_status: str,
        expected_base_repo_state_checksum: str,
        current_base_repo_state_checksum: str,
        target_path: str,
        run_dir: str | Path,
        sandbox_path: str | Path,
        legacy_path: str | Path,
        deterministic_rule_id: str,
        run_id: str = "",
        risk: str = "LOW",
        expected_validation: tuple[str, ...] = (),
        h2_required: bool = False,
        validation_runner: ValidationRunner = run_validation_after_patch,
        event_recorder: RepairEventRecorder | None = None,
    ) -> SandboxAction:
        """Apply only an exact reviewed diff loaded from backend artifact storage.

        F5 callers pass artifact refs and checksums, not diff content. The
        service loads the diff, verifies reviewer/policy/base-state bindings,
        and then reuses the existing sandbox patch gate/apply/rerun machinery.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None and self._repo is not None:
            record = self._repo.get_proposal(proposal_id)
            if record is not None:
                proposal = self._record_to_proposal(record)
                self._proposals[proposal_id] = proposal
        if proposal is None:
            raise ValueError(f"Proposal {proposal_id!r} not found")
        if proposal.status != "approved":
            raise ValueError(f"Proposal {proposal_id!r} must be approved first")
        if not expected_reviewer_output_checksum or reviewer_output_checksum != expected_reviewer_output_checksum:
            raise ValueError("Reviewed diff cannot be applied: reviewer checksum mismatch")
        if not expected_policy_validation_checksum or policy_validation_checksum != expected_policy_validation_checksum:
            raise ValueError("Reviewed diff cannot be applied: policy validation checksum mismatch")
        if str(policy_status).lower() not in {"allowed", "allow"}:
            raise ValueError("Reviewed diff cannot be applied: policy validation is not allowed")
        if expected_base_repo_state_checksum != current_base_repo_state_checksum:
            raise ValueError("Reviewed diff cannot be applied: base repository state is stale")

        diff_path = Path(final_diff_ref)
        if not diff_path.is_file():
            raise ValueError(f"Reviewed repair diff artifact not found: {diff_path}")
        diff_content = diff_path.read_text(encoding="utf-8")
        actual_diff_checksum = sha256_canonical_json({"unified_diff": diff_content})
        if actual_diff_checksum != final_diff_checksum:
            raise ValueError("Reviewed repair diff artifact checksum mismatch")

        binding_checksum = sha256_canonical_json(
            {
                "proposal_id": proposal_id,
                "final_diff_checksum": final_diff_checksum,
                "reviewer_output_checksum": reviewer_output_checksum,
                "policy_validation_checksum": policy_validation_checksum,
                "base_repo_state_checksum": expected_base_repo_state_checksum,
            }
        )
        return self.apply_patch(
            proposal_id=proposal_id,
            target_path=target_path,
            patch_content=diff_content,
            run_id=run_id,
            run_dir=run_dir,
            sandbox_path=sandbox_path,
            legacy_path=legacy_path,
            deterministic_rule_id=deterministic_rule_id,
            risk=risk,
            requires_human_review=False,
            expected_validation=expected_validation,
            h2_required=h2_required,
            binding_checksum=binding_checksum,
            validation_runner=validation_runner,
            event_recorder=event_recorder,
        )

    def _mark_proposal_applied(self, proposal: RepairProposal) -> None:
        updated = RepairProposal(
            proposal_id=proposal.proposal_id,
            command_id=proposal.command_id,
            failure_summary=proposal.failure_summary,
            hypothesis=proposal.hypothesis,
            patch_summary=proposal.patch_summary,
            affected_paths=proposal.affected_paths,
            status="applied",
            approval_checksum=proposal.approval_checksum,
            created_at=proposal.created_at,
            proposal_checksum=proposal.proposal_checksum,
        )
        if self._repo is not None:
            self._repo.update_proposal_status(proposal.proposal_id, "applied")
        self._proposals[proposal.proposal_id] = updated

    def _record_to_proposal(self, record: V2RepairProposalRecord) -> RepairProposal:
        affected_paths = tuple(json.loads(record.affected_paths_json))
        return RepairProposal(
            proposal_id=record.proposal_id,
            command_id=record.command_id,
            failure_summary=record.failure_summary,
            hypothesis=record.hypothesis,
            patch_summary=record.patch_summary,
            affected_paths=affected_paths,
            status=record.status,
            approval_checksum=record.approval_checksum,
            created_at=record.created_at,
            proposal_checksum=record.proposal_checksum
            or self._proposal_checksum(
                command_id=record.command_id,
                failure_summary=record.failure_summary,
                hypothesis=record.hypothesis,
                patch_summary=record.patch_summary,
                affected_paths=affected_paths,
                source_proposal_id=record.source_proposal_id,
                revision_of=record.revision_of,
                revision_number=record.revision_number,
                context_pack_checksum=record.context_pack_checksum,
                allowed_scope=record.allowed_scope,
            ),
            source_proposal_id=record.source_proposal_id,
            revision_of=record.revision_of,
            revision_number=record.revision_number,
            context_pack_checksum=record.context_pack_checksum,
            allowed_scope=record.allowed_scope,
        )

    def proposal_to_dict(self, proposal: RepairProposal, *, reviewer_critique_id: str | None = None, reviewer_decision: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "proposal_id": proposal.proposal_id,
            "command_id": proposal.command_id,
            "failure_summary": proposal.failure_summary,
            "hypothesis": proposal.hypothesis,
            "patch_summary": proposal.patch_summary,
            "affected_paths": list(proposal.affected_paths),
            "status": proposal.status,
            "approval_checksum": proposal.approval_checksum,
            "created_at": proposal.created_at,
            "proposal_checksum": proposal.proposal_checksum,
        }
        # F07: Include reviewer metadata when available
        if reviewer_critique_id is not None:
            result["reviewer_critique_id"] = reviewer_critique_id
        if reviewer_decision is not None:
            result["reviewer_decision"] = reviewer_decision
        # F05: Include revision metadata when present
        if proposal.source_proposal_id is not None:
            result["source_proposal_id"] = proposal.source_proposal_id
            result["revision_of"] = proposal.revision_of
            result["revision_number"] = proposal.revision_number
        if proposal.allowed_scope is not None:
            result["allowed_scope"] = proposal.allowed_scope
        return result

    def action_to_dict(self, action: SandboxAction) -> dict[str, Any]:
        return {
            "action_id": action.action_id,
            "proposal_id": action.proposal_id,
            "target_path": action.target_path,
            "patch_content": action.patch_content[:100] if action.patch_content else "",
            "status": action.status,
            "result_summary": action.result_summary,
            "created_at": action.created_at,
        }

    @staticmethod
    def _proposal_checksum(
        *,
        command_id: str,
        failure_summary: str,
        hypothesis: str,
        patch_summary: str,
        affected_paths: tuple[str, ...],
        source_proposal_id: str | None = None,
        revision_of: str | None = None,
        revision_number: int | None = None,
        context_pack_checksum: str | None = None,
        allowed_scope: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "command_id": command_id,
            "failure_summary": failure_summary,
            "hypothesis": hypothesis,
            "patch_summary": patch_summary,
            "affected_paths": list(affected_paths),
        }
        if source_proposal_id is not None:
            payload["source_proposal_id"] = source_proposal_id
        if revision_of is not None:
            payload["revision_of"] = revision_of
        if revision_number is not None:
            payload["revision_number"] = revision_number
        if context_pack_checksum is not None:
            payload["context_pack_checksum"] = context_pack_checksum
        if allowed_scope is not None:
            payload["allowed_scope"] = allowed_scope
        return sha256_canonical_json(payload)
