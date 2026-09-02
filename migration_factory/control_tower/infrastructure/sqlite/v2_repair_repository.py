"""SQLite repository for V2 repair proposals and sandbox actions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class V2RepairProposalRecord:
    proposal_id: str
    command_id: str
    failure_summary: str
    hypothesis: str
    patch_summary: str
    affected_paths_json: str
    status: str
    approval_checksum: str | None
    created_at: str
    proposal_checksum: str | None = None
    source_proposal_id: str | None = None
    revision_of: str | None = None
    revision_number: int | None = None
    context_pack_checksum: str | None = None
    allowed_scope: str | None = None
    # PR-B: reviewed-diff and job-scoped fields (all nullable)
    job_id: str | None = None
    route_step_index: int | None = None
    attempt_number: int | None = None
    failure_evidence_ref: str | None = None
    repair_context_ref: str | None = None
    diagnosis_ref: str | None = None
    repair_plan_ref: str | None = None
    diff_ref: str | None = None
    diff_checksum: str | None = None
    safe_diff_preview_ref: str | None = None
    reviewer_verdict_id: str | None = None
    reviewer_verdict_ref: str | None = None
    reviewer_output_checksum: str | None = None
    policy_validation_checksum: str | None = None
    gate_id: str | None = None
    status_reason: str | None = None
    # PR-F: retry/attempt history fields (all nullable)
    apply_status: str | None = None
    rerun_status: str | None = None
    rollback_status: str | None = None
    validation_result_ref: str | None = None
    next_gate_id: str | None = None
    next_gate_status: str | None = None
    remaining_attempts: int | None = None
    completed_at: str | None = None
    reviewer_decision: str | None = None
    deterministic_rule_id: str | None = None
    risk: str | None = None
    lineage_manifest_ref: str | None = None
    lineage_manifest_checksum: str | None = None
    validation_context_ref: str | None = None
    validation_context_checksum: str | None = None
    apply_idempotency_key: str | None = None
    apply_claim_status: str | None = None
    apply_claim_version: int | None = None
    continuation_command_id: str | None = None
    validation_proof_status: str | None = None
    final_diff_source: str | None = None
    source_profile: str | None = None
    target_profile: str | None = None


@dataclass(frozen=True)
class V2SandboxActionRecord:
    action_id: str
    proposal_id: str
    target_path: str
    patch_content: str
    status: str
    result_summary: str
    created_at: str


class SqliteV2RepairRepository:
    """Repository for V2 repair proposals and sandbox actions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_proposal(self, record: V2RepairProposalRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_repair_proposals (
                proposal_id, command_id, failure_summary, hypothesis,
                patch_summary, affected_paths_json, status,
                approval_checksum, created_at, proposal_checksum, source_proposal_id,
                revision_of, revision_number, context_pack_checksum,
                allowed_scope,
                job_id, route_step_index, attempt_number,
                failure_evidence_ref, repair_context_ref, diagnosis_ref,
                repair_plan_ref, diff_ref, diff_checksum,
                safe_diff_preview_ref, reviewer_verdict_id,
                reviewer_verdict_ref, reviewer_output_checksum,
                policy_validation_checksum, gate_id, status_reason,
                apply_status, rerun_status, rollback_status,
                validation_result_ref, next_gate_id, next_gate_status,
                remaining_attempts, completed_at, reviewer_decision,
                deterministic_rule_id, risk
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.proposal_id,
                record.command_id,
                record.failure_summary,
                record.hypothesis,
                record.patch_summary,
                record.affected_paths_json,
                record.status,
                record.approval_checksum,
                record.created_at,
                record.proposal_checksum,
                record.source_proposal_id,
                record.revision_of,
                record.revision_number,
                record.context_pack_checksum,
                record.allowed_scope,
                record.job_id,
                record.route_step_index,
                record.attempt_number,
                record.failure_evidence_ref,
                record.repair_context_ref,
                record.diagnosis_ref,
                record.repair_plan_ref,
                record.diff_ref,
                record.diff_checksum,
                record.safe_diff_preview_ref,
                record.reviewer_verdict_id,
                record.reviewer_verdict_ref,
                record.reviewer_output_checksum,
                record.policy_validation_checksum,
                record.gate_id,
                record.status_reason,
                record.apply_status,
                record.rerun_status,
                record.rollback_status,
                record.validation_result_ref,
                record.next_gate_id,
                record.next_gate_status,
                record.remaining_attempts,
                record.completed_at,
                record.reviewer_decision,
                record.deterministic_rule_id,
                record.risk,
            ),
        )
        self._connection.execute(
            """UPDATE v2_repair_proposals SET
               lineage_manifest_ref = ?, lineage_manifest_checksum = ?,
               validation_context_ref = ?, validation_context_checksum = ?,
               apply_idempotency_key = ?, apply_claim_status = ?,
               apply_claim_version = ?, continuation_command_id = ?,
               validation_proof_status = ?, final_diff_source = ?,
               source_profile = ?, target_profile = ? WHERE proposal_id = ?""",
            (record.lineage_manifest_ref, record.lineage_manifest_checksum,
             record.validation_context_ref, record.validation_context_checksum,
             record.apply_idempotency_key, record.apply_claim_status,
             record.apply_claim_version, record.continuation_command_id,
             record.validation_proof_status, record.final_diff_source,
             record.source_profile, record.target_profile, record.proposal_id),
        )

    def update_proposal_status(self, proposal_id: str, status: str, approval_checksum: str | None = None) -> None:
        """Update proposal status and optional approval checksum."""
        if approval_checksum is not None:
            self._connection.execute(
                "UPDATE v2_repair_proposals SET status = ?, approval_checksum = ? WHERE proposal_id = ?",
                (status, approval_checksum, proposal_id),
            )
        else:
            self._connection.execute(
                "UPDATE v2_repair_proposals SET status = ? WHERE proposal_id = ?",
                (status, proposal_id),
            )

    def update_proposal_status_with_reason(self, proposal_id: str, status: str, status_reason: str) -> None:
        """Update proposal status and reason."""
        self._connection.execute(
            "UPDATE v2_repair_proposals SET status = ?, status_reason = ? WHERE proposal_id = ?",
            (status, status_reason, proposal_id),
        )

    def update_proposal_prf_fields(self, proposal_id: str, **fields) -> None:
        """Update PR-F retry/attempt history fields for a proposal.

        Accepts keyword args matching column names:
        apply_status, rerun_status, rollback_status, validation_result_ref,
        next_gate_id, next_gate_status, remaining_attempts, completed_at,
        reviewer_decision, status, status_reason
        """
        allowed = frozenset({
            "apply_status", "rerun_status", "rollback_status",
            "validation_result_ref", "next_gate_id", "next_gate_status",
            "remaining_attempts", "completed_at", "reviewer_decision",
            "status", "status_reason",
            "lineage_manifest_ref", "lineage_manifest_checksum",
            "validation_context_ref", "validation_context_checksum",
            "apply_idempotency_key", "apply_claim_status", "apply_claim_version",
            "continuation_command_id", "validation_proof_status",
            "final_diff_source",
        })
        bad = [k for k in fields if k not in allowed]
        if bad:
            raise ValueError(f"Unknown PR-F fields: {bad}")
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = tuple(fields[k] for k in fields) + (proposal_id,)
        self._connection.execute(
            f"UPDATE v2_repair_proposals SET {set_clause} WHERE proposal_id = ?",
            values,
        )

    def get_proposal(self, proposal_id: str) -> V2RepairProposalRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_repair_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_proposal(row)

    def claim_apply(self, job_id: str, proposal_id: str, idempotency_key: str, *, expected_checksum: str) -> str:
        """Claim one Apply before filesystem/build work is started.

        The caller commits this short transaction before doing external work.
        A repeated key is replayable only for the same proposal/checksum.
        """
        existing = self._connection.execute(
            "SELECT job_id, proposal_id, diff_checksum, apply_claim_status FROM v2_repair_proposals "
            "WHERE apply_idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            if str(existing["job_id"]) != job_id or str(existing["proposal_id"]) != proposal_id or str(existing["diff_checksum"] or "") != expected_checksum:
                return "idempotency_conflict"
            existing_status = str(existing["apply_claim_status"] or "in_flight")
            return "in_flight" if existing_status == "claimed" else existing_status
        cursor = self._connection.execute(
            """UPDATE v2_repair_proposals
               SET apply_idempotency_key = ?, apply_claim_status = 'claimed',
                   apply_claim_version = COALESCE(apply_claim_version, 0) + 1
               WHERE job_id = ? AND proposal_id = ? AND diff_checksum = ?
                 AND (apply_claim_status IS NULL OR apply_claim_status IN ('failed', 'replayable'))""",
            (idempotency_key, job_id, proposal_id, expected_checksum),
        )
        if cursor.rowcount == 1:
            return "newly_claimed"
        current = self._connection.execute(
            "SELECT apply_claim_status FROM v2_repair_proposals WHERE job_id = ? AND proposal_id = ?",
            (job_id, proposal_id),
        ).fetchone()
        current_status = str(current["apply_claim_status"] or "") if current is not None else ""
        if current_status == "claimed":
            return "in_flight"
        if current_status in {"completed", "failed"}:
            return current_status
        return "idempotency_conflict"

    def list_proposals_by_command(self, command_id: str) -> tuple[V2RepairProposalRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_repair_proposals WHERE command_id = ? ORDER BY created_at DESC",
            (command_id,),
        ).fetchall()
        return tuple(self._row_to_proposal(row) for row in rows)

    def list_proposals_by_job(self, job_id: str) -> tuple[V2RepairProposalRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_repair_proposals
               WHERE job_id = ?
               ORDER BY created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_proposal(row) for row in rows)

    def get_proposal_for_job(self, job_id: str, proposal_id: str) -> V2RepairProposalRecord | None:
        row = self._connection.execute(
            """SELECT * FROM v2_repair_proposals
               WHERE proposal_id = ? AND job_id = ?""",
            (proposal_id, job_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_proposal(row)

    def get_current_proposal_for_job(self, job_id: str) -> V2RepairProposalRecord | None:
        """Return the authoritative newest leaf proposal for the job.

        A leaf is a proposal that has no same-job child where
        child.revision_of = p.proposal_id OR child.source_proposal_id = p.proposal_id.
        """
        row = self._connection.execute(
            """SELECT p.* FROM v2_repair_proposals p
               WHERE p.job_id = ?
                  AND NOT EXISTS (
                   SELECT 1 FROM v2_repair_proposals child
                   WHERE child.job_id = ?
                     AND (child.revision_of = p.proposal_id OR child.source_proposal_id = p.proposal_id)
                 )
               ORDER BY p.created_at DESC, COALESCE(p.revision_number, 0) DESC, p.proposal_id DESC
               LIMIT 1""",
            (job_id, job_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_proposal(row)

    def get_superseding_leaf_for_proposal(self, job_id: str, proposal_id: str) -> V2RepairProposalRecord | None:
        """If the given proposal has a same-job descendant leaf, return the
        authoritative newest leaf. Returns None if the proposal is itself a leaf
        (no descendants).

        Handles arbitrary-depth chains, multiple independent chains
        deterministically, and is cycle-safe (depth limit 100).
        """
        row = self._connection.execute(
            """WITH RECURSIVE descendants(proposal_id, depth) AS (
                   SELECT ?, 0
                   UNION ALL
                   SELECT child.proposal_id, d.depth + 1
                   FROM v2_repair_proposals child
                   JOIN descendants d ON (child.revision_of = d.proposal_id OR child.source_proposal_id = d.proposal_id)
                   WHERE child.job_id = ? AND d.depth < 100
               )
               SELECT p.* FROM v2_repair_proposals p
               WHERE p.proposal_id IN (SELECT proposal_id FROM descendants WHERE proposal_id != ?)
                 AND p.job_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM v2_repair_proposals child
                     WHERE child.job_id = ?
                       AND (child.revision_of = p.proposal_id OR child.source_proposal_id = p.proposal_id)
                 )
               ORDER BY p.created_at DESC, COALESCE(p.revision_number, 0) DESC, p.proposal_id DESC
               LIMIT 1""",
            (proposal_id, job_id, proposal_id, job_id, job_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_proposal(row)

    def list_attempts_by_job(self, job_id: str) -> tuple[V2RepairProposalRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_repair_proposals
               WHERE job_id = ?
                 AND attempt_number IS NOT NULL
               ORDER BY attempt_number DESC, created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_proposal(row) for row in rows)

    def save_action(self, record: V2SandboxActionRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_sandbox_actions (
                action_id, proposal_id, target_path, patch_content,
                status, result_summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                record.action_id,
                record.proposal_id,
                record.target_path,
                record.patch_content,
                record.status,
                record.result_summary,
                record.created_at,
            ),
        )

    def get_action(self, action_id: str) -> V2SandboxActionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_sandbox_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_action(row)

    def list_actions_by_proposal(self, proposal_id: str) -> tuple[V2SandboxActionRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_sandbox_actions WHERE proposal_id = ? ORDER BY created_at DESC",
            (proposal_id,),
        ).fetchall()
        return tuple(self._row_to_action(row) for row in rows)

    def _row_to_proposal(self, row: sqlite3.Row) -> V2RepairProposalRecord:
        keys = row.keys()
        return V2RepairProposalRecord(
            proposal_id=str(row["proposal_id"]),
            command_id=str(row["command_id"]),
            failure_summary=str(row["failure_summary"]),
            hypothesis=str(row["hypothesis"]),
            patch_summary=str(row["patch_summary"]),
            affected_paths_json=str(row["affected_paths_json"]),
            status=str(row["status"]),
            approval_checksum=str(row["approval_checksum"]) if row["approval_checksum"] else None,
            created_at=str(row["created_at"]),
            proposal_checksum=str(row["proposal_checksum"]) if "proposal_checksum" in keys and row["proposal_checksum"] else None,
            source_proposal_id=str(row["source_proposal_id"]) if "source_proposal_id" in keys and row["source_proposal_id"] else None,
            revision_of=str(row["revision_of"]) if "revision_of" in keys and row["revision_of"] else None,
            revision_number=int(row["revision_number"]) if "revision_number" in keys and row["revision_number"] is not None else None,
            context_pack_checksum=str(row["context_pack_checksum"]) if "context_pack_checksum" in keys and row["context_pack_checksum"] else None,
            allowed_scope=str(row["allowed_scope"]) if "allowed_scope" in keys and row["allowed_scope"] else None,
            job_id=str(row["job_id"]) if "job_id" in keys and row["job_id"] else None,
            route_step_index=int(row["route_step_index"]) if "route_step_index" in keys and row["route_step_index"] is not None else None,
            attempt_number=int(row["attempt_number"]) if "attempt_number" in keys and row["attempt_number"] is not None else None,
            failure_evidence_ref=str(row["failure_evidence_ref"]) if "failure_evidence_ref" in keys and row["failure_evidence_ref"] else None,
            repair_context_ref=str(row["repair_context_ref"]) if "repair_context_ref" in keys and row["repair_context_ref"] else None,
            diagnosis_ref=str(row["diagnosis_ref"]) if "diagnosis_ref" in keys and row["diagnosis_ref"] else None,
            repair_plan_ref=str(row["repair_plan_ref"]) if "repair_plan_ref" in keys and row["repair_plan_ref"] else None,
            diff_ref=str(row["diff_ref"]) if "diff_ref" in keys and row["diff_ref"] else None,
            diff_checksum=str(row["diff_checksum"]) if "diff_checksum" in keys and row["diff_checksum"] else None,
            safe_diff_preview_ref=str(row["safe_diff_preview_ref"]) if "safe_diff_preview_ref" in keys and row["safe_diff_preview_ref"] else None,
            reviewer_verdict_id=str(row["reviewer_verdict_id"]) if "reviewer_verdict_id" in keys and row["reviewer_verdict_id"] else None,
            reviewer_verdict_ref=str(row["reviewer_verdict_ref"]) if "reviewer_verdict_ref" in keys and row["reviewer_verdict_ref"] else None,
            reviewer_output_checksum=str(row["reviewer_output_checksum"]) if "reviewer_output_checksum" in keys and row["reviewer_output_checksum"] else None,
            policy_validation_checksum=str(row["policy_validation_checksum"]) if "policy_validation_checksum" in keys and row["policy_validation_checksum"] else None,
            gate_id=str(row["gate_id"]) if "gate_id" in keys and row["gate_id"] else None,
            status_reason=str(row["status_reason"]) if "status_reason" in keys and row["status_reason"] else None,
            apply_status=str(row["apply_status"]) if "apply_status" in keys and row["apply_status"] else None,
            rerun_status=str(row["rerun_status"]) if "rerun_status" in keys and row["rerun_status"] else None,
            rollback_status=str(row["rollback_status"]) if "rollback_status" in keys and row["rollback_status"] else None,
            validation_result_ref=str(row["validation_result_ref"]) if "validation_result_ref" in keys and row["validation_result_ref"] else None,
            next_gate_id=str(row["next_gate_id"]) if "next_gate_id" in keys and row["next_gate_id"] else None,
            next_gate_status=str(row["next_gate_status"]) if "next_gate_status" in keys and row["next_gate_status"] else None,
            remaining_attempts=int(row["remaining_attempts"]) if "remaining_attempts" in keys and row["remaining_attempts"] is not None else None,
            completed_at=str(row["completed_at"]) if "completed_at" in keys and row["completed_at"] else None,
            reviewer_decision=str(row["reviewer_decision"]) if "reviewer_decision" in keys and row["reviewer_decision"] else None,
            deterministic_rule_id=str(row["deterministic_rule_id"]) if "deterministic_rule_id" in keys and row["deterministic_rule_id"] else None,
            risk=str(row["risk"]) if "risk" in keys and row["risk"] else None,
            lineage_manifest_ref=str(row["lineage_manifest_ref"]) if "lineage_manifest_ref" in keys and row["lineage_manifest_ref"] else None,
            lineage_manifest_checksum=str(row["lineage_manifest_checksum"]) if "lineage_manifest_checksum" in keys and row["lineage_manifest_checksum"] else None,
            validation_context_ref=str(row["validation_context_ref"]) if "validation_context_ref" in keys and row["validation_context_ref"] else None,
            validation_context_checksum=str(row["validation_context_checksum"]) if "validation_context_checksum" in keys and row["validation_context_checksum"] else None,
            apply_idempotency_key=str(row["apply_idempotency_key"]) if "apply_idempotency_key" in keys and row["apply_idempotency_key"] else None,
            apply_claim_status=str(row["apply_claim_status"]) if "apply_claim_status" in keys and row["apply_claim_status"] else None,
            apply_claim_version=int(row["apply_claim_version"]) if "apply_claim_version" in keys and row["apply_claim_version"] is not None else None,
            continuation_command_id=str(row["continuation_command_id"]) if "continuation_command_id" in keys and row["continuation_command_id"] else None,
            validation_proof_status=str(row["validation_proof_status"]) if "validation_proof_status" in keys and row["validation_proof_status"] else None,
            final_diff_source=str(row["final_diff_source"]) if "final_diff_source" in keys and row["final_diff_source"] else None,
            source_profile=str(row["source_profile"]) if "source_profile" in keys and row["source_profile"] else None,
            target_profile=str(row["target_profile"]) if "target_profile" in keys and row["target_profile"] else None,
        )

    def _row_to_action(self, row: sqlite3.Row) -> V2SandboxActionRecord:
        return V2SandboxActionRecord(
            action_id=str(row["action_id"]),
            proposal_id=str(row["proposal_id"]),
            target_path=str(row["target_path"]),
            patch_content=str(row["patch_content"]),
            status=str(row["status"]),
            result_summary=str(row["result_summary"]),
            created_at=str(row["created_at"]),
        )
