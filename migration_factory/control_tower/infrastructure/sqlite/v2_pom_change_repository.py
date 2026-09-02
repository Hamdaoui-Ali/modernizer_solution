"""SQLite repositories for F14 POM change proposals, changes,
validations, and repair plans.

Follows append-only V2 repository patterns.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.application.pom_change_models import (
    PomChangeRecord,
    PomChangeStatus,
    PomRepairPlan,
    PomRepairPlanStatus,
    PomValidationRun,
    PomValidationStatus,
)
from migration_factory.control_tower.domain.checksums import utc_now_text


# ── Proposal record ────────────────────────────────────────────────

@dataclass(frozen=True)
class PomChangeProposalRecord:
    proposal_id: str
    job_id: str
    stage_index: int
    user_request: str
    server_plan_json: str
    risk: str
    can_apply: bool
    control_mode: str
    expected_checksum: str | None
    expires_at: str | None
    status: str
    created_at: str


# ── PomChangeProposal Repository ───────────────────────────────────

class SqlitePomChangeProposalRepository:

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def save(
        self,
        *,
        proposal_id: str | None = None,
        job_id: str,
        stage_index: int,
        user_request: str,
        server_plan_json: str,
        risk: str,
        can_apply: bool,
        control_mode: str,
        expected_checksum: str | None = None,
    ) -> PomChangeProposalRecord:
        proposal_id = proposal_id or uuid4().hex
        now = utc_now_text()
        record = PomChangeProposalRecord(
            proposal_id=proposal_id,
            job_id=job_id,
            stage_index=stage_index,
            user_request=user_request,
            server_plan_json=server_plan_json,
            risk=risk,
            can_apply=can_apply,
            control_mode=control_mode,
            expected_checksum=expected_checksum,
            expires_at=None,
            status="active",
            created_at=now,
        )
        self._conn.execute(
            """INSERT INTO v2_pom_change_proposals (
                proposal_id, job_id, stage_index, user_request,
                server_plan_json, risk, can_apply, control_mode,
                expected_checksum, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.proposal_id, record.job_id, record.stage_index,
                record.user_request, record.server_plan_json, record.risk,
                1 if record.can_apply else 0, record.control_mode,
                record.expected_checksum, record.status, record.created_at,
            ),
        )
        return record

    def get(self, proposal_id: str) -> PomChangeProposalRecord | None:
        row = self._conn.execute(
            "SELECT * FROM v2_pom_change_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def mark_consumed(self, proposal_id: str) -> None:
        self._conn.execute(
            "UPDATE v2_pom_change_proposals SET status = 'consumed' WHERE proposal_id = ?",
            (proposal_id,),
        )

    def list_by_job(self, job_id: str) -> list[PomChangeProposalRecord]:
        rows = self._conn.execute(
            "SELECT * FROM v2_pom_change_proposals WHERE job_id = ? ORDER BY created_at DESC",
            (job_id,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def _row_to_record(self, row: sqlite3.Row) -> PomChangeProposalRecord:
        return PomChangeProposalRecord(
            proposal_id=str(row["proposal_id"]),
            job_id=str(row["job_id"]),
            stage_index=int(row["stage_index"]),
            user_request=str(row["user_request"]),
            server_plan_json=str(row["server_plan_json"]),
            risk=str(row["risk"]),
            can_apply=bool(row["can_apply"]),
            control_mode=str(row["control_mode"]),
            expected_checksum=str(row["expected_checksum"]) if row["expected_checksum"] else None,
            expires_at=str(row["expires_at"]) if row["expires_at"] else None,
            status=str(row["status"]),
            created_at=str(row["created_at"]),
        )


# ── PomChange Repository ───────────────────────────────────────────

class SqlitePomChangeRepository:

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def save(
        self,
        *,
        proposal_id: str | None,
        job_id: str,
        stage_index: int,
        operation: str,
        target_json: str,
        requested_version: str,
        before_checksum: str,
        after_checksum: str,
        before_content_ref: str,
        after_content_ref: str,
        diff_unified: str,
        idempotency_key: str | None = None,
        executor: str = "pom_span_patch",
        status: str = PomChangeStatus.APPLIED_PENDING_VALIDATION.value,
        logical_stage_index: int | None = None,
        execution_stage_index: int | None = None,
        route_step_index: int | None = None,
        expected_checksum: str | None = None,
        request_checksum: str | None = None,
        command_id: str | None = None,
        validation_context_ref: str | None = None,
        validation_context_checksum: str | None = None,
        repair_linkage_json: str | None = None,
        repair_proposal_id: str | None = None,
        pom_path_ref: str | None = None,
    ) -> PomChangeRecord:
        now = utc_now_text()
        change_id = uuid4().hex
        record = PomChangeRecord(
            change_id=change_id,
            proposal_id=proposal_id,
            job_id=job_id,
            stage_index=stage_index,
            operation=operation,
            target_json=target_json,
            requested_version=requested_version,
            before_content_ref=before_content_ref,
            after_content_ref=after_content_ref,
            before_checksum=before_checksum,
            after_checksum=after_checksum,
            diff_unified=diff_unified,
            status=status,
            validation_id=None,
            rollback_id=None,
            idempotency_key=idempotency_key,
            executor=executor,
            created_at=now,
            updated_at=now,
        )
        self._conn.execute(
            """INSERT INTO v2_pom_changes (
                change_id, proposal_id, job_id, stage_index, operation,
                target_json, requested_version, before_checksum, after_checksum,
                before_content_ref, after_content_ref, diff_unified, status,
                validation_id, rollback_id, idempotency_key, executor,
                created_at, updated_at, logical_stage_index, execution_stage_index,
                route_step_index, expected_checksum, request_checksum, command_id,
                validation_context_ref, validation_context_checksum, repair_linkage_json,
                repair_proposal_id, pom_path_ref
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )""",
            (
                record.change_id, record.proposal_id, record.job_id,
                record.stage_index, record.operation, record.target_json,
                record.requested_version, record.before_checksum,
                record.after_checksum, record.before_content_ref,
                record.after_content_ref, record.diff_unified, record.status,
                record.validation_id, record.rollback_id,
                record.idempotency_key, record.executor,
                record.created_at, record.updated_at,
                logical_stage_index, execution_stage_index, route_step_index,
                expected_checksum, request_checksum, command_id,
                validation_context_ref, validation_context_checksum, repair_linkage_json,
                repair_proposal_id, pom_path_ref,
            ),
        )
        return record

    def find_by_idempotency(self, job_id: str, idempotency_key: str) -> PomChangeRecord | None:
        if not idempotency_key:
            return None
        row = self._conn.execute(
            "SELECT * FROM v2_pom_changes WHERE job_id = ? AND idempotency_key = ?",
            (job_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get(self, change_id: str) -> PomChangeRecord | None:
        row = self._conn.execute(
            "SELECT * FROM v2_pom_changes WHERE change_id = ?",
            (change_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_lineage(self, change_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM v2_pom_changes WHERE change_id = ?",
            (change_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def find_by_request_checksum(self, job_id: str, request_checksum: str) -> PomChangeRecord | None:
        row = self._conn.execute(
            "SELECT * FROM v2_pom_changes WHERE job_id = ? AND request_checksum = ?",
            (job_id, request_checksum),
        ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def update_lineage(self, change_id: str, **fields: Any) -> None:
        allowed = {
            "validation_id", "status", "command_id", "validation_context_ref",
            "validation_context_checksum", "repair_linkage_json",
            "repair_proposal_id", "pom_path_ref",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = utc_now_text()
        assignments = ", ".join(f"{key} = ?" for key in values)
        self._conn.execute(
            f"UPDATE v2_pom_changes SET {assignments} WHERE change_id = ?",
            (*values.values(), change_id),
        )

    def update_status(
        self, change_id: str, status: str, *, validation_id: str | None = None,
        rollback_id: str | None = None,
    ) -> None:
        now = utc_now_text()
        self._conn.execute(
            """UPDATE v2_pom_changes
               SET status = ?, validation_id = COALESCE(?, validation_id),
                   rollback_id = COALESCE(?, rollback_id), updated_at = ?
               WHERE change_id = ?""",
            (status, validation_id, rollback_id, now, change_id),
        )

    def list_by_job(self, job_id: str) -> list[PomChangeRecord]:
        rows = self._conn.execute(
            "SELECT * FROM v2_pom_changes WHERE job_id = ? ORDER BY created_at DESC",
            (job_id,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def _row_to_record(self, row: sqlite3.Row) -> PomChangeRecord:
        return PomChangeRecord(
            change_id=str(row["change_id"]),
            proposal_id=str(row["proposal_id"]) if row["proposal_id"] else None,
            job_id=str(row["job_id"]),
            stage_index=int(row["stage_index"]),
            operation=str(row["operation"]),
            target_json=str(row["target_json"]),
            requested_version=str(row["requested_version"]),
            before_content_ref=str(row["before_content_ref"]),
            after_content_ref=str(row["after_content_ref"]),
            before_checksum=str(row["before_checksum"]),
            after_checksum=str(row["after_checksum"]),
            diff_unified=str(row["diff_unified"]),
            status=str(row["status"]),
            validation_id=str(row["validation_id"]) if row["validation_id"] else None,
            rollback_id=str(row["rollback_id"]) if row["rollback_id"] else None,
            idempotency_key=str(row["idempotency_key"]) if row["idempotency_key"] else None,
            executor=str(row["executor"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


# ── PomValidation Repository ───────────────────────────────────────

class SqlitePomValidationRepository:

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def save(
        self,
        *,
        change_id: str,
        job_id: str,
        stage_index: int,
        command: str,
        status: str = PomValidationStatus.RUNNING.value,
        logical_stage_index: int | None = None,
        execution_stage_index: int | None = None,
        route_step_index: int | None = None,
        command_id: str | None = None,
        validation_context_ref: str | None = None,
        validation_context_checksum: str | None = None,
    ) -> str:
        validation_id = uuid4().hex
        now = utc_now_text()
        self._conn.execute(
            """INSERT INTO v2_pom_validations (
                validation_id, change_id, job_id, stage_index, command,
                status, created_at, logical_stage_index, execution_stage_index,
                route_step_index, command_id, validation_context_ref,
                validation_context_checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (validation_id, change_id, job_id, stage_index, command, status, now,
             logical_stage_index, execution_stage_index, route_step_index, command_id,
             validation_context_ref, validation_context_checksum),
        )
        return validation_id

    def update_result(
        self,
        validation_id: str,
        *,
        status: str,
        exit_code: int | None = None,
        duration_ms: int | None = None,
        log_ref: str | None = None,
        test_log_ref: str | None = None,
        failure_classification: str | None = None,
        diagnosis_json: str | None = None,
        build_status: str | None = None,
        test_status: str | None = None,
        repair_linkage_json: str | None = None,
    ) -> None:
        now = utc_now_text()
        self._conn.execute(
            """UPDATE v2_pom_validations
               SET status = ?, exit_code = ?, duration_ms = ?,
                   log_ref = COALESCE(?, log_ref),
                   test_log_ref = COALESCE(?, test_log_ref),
                   failure_classification = COALESCE(?, failure_classification),
                   diagnosis_json = COALESCE(?, diagnosis_json),
                   build_status = COALESCE(?, build_status),
                   test_status = COALESCE(?, test_status),
                   repair_linkage_json = COALESCE(?, repair_linkage_json),
                   completed_at = CASE WHEN ? IN ('running', 'validation_queued', 'apply_intent_persisted') THEN NULL ELSE ? END
               WHERE validation_id = ?""",
            (status, exit_code, duration_ms, log_ref, test_log_ref,
            failure_classification, diagnosis_json, build_status, test_status,
            repair_linkage_json, status, now, validation_id),
        )

    def get(self, validation_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM v2_pom_validations WHERE validation_id = ?",
            (validation_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_by_change(self, change_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM v2_pom_validations WHERE change_id = ? ORDER BY created_at DESC LIMIT 1",
            (change_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)


# ── PomRepairPlan Repository ───────────────────────────────────────

class SqlitePomRepairPlanRepository:

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def save(
        self,
        *,
        validation_id: str,
        change_id: str,
        summary: str,
        steps_json: str,
        confidence: str,
        evidence_refs_json: str,
        status: str = PomRepairPlanStatus.PROPOSED.value,
    ) -> str:
        repair_plan_id = uuid4().hex
        now = utc_now_text()
        self._conn.execute(
            """INSERT INTO v2_pom_repair_plans (
                repair_plan_id, validation_id, change_id, summary,
                steps_json, confidence, evidence_refs_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (repair_plan_id, validation_id, change_id, summary,
             steps_json, confidence, evidence_refs_json, status, now),
        )
        return repair_plan_id

    def get(self, repair_plan_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM v2_pom_repair_plans WHERE repair_plan_id = ?",
            (repair_plan_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_by_validation(self, validation_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM v2_pom_repair_plans WHERE validation_id = ? ORDER BY created_at DESC LIMIT 1",
            (validation_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def update_status(self, repair_plan_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE v2_pom_repair_plans SET status = ? WHERE repair_plan_id = ?",
            (status, repair_plan_id),
        )
