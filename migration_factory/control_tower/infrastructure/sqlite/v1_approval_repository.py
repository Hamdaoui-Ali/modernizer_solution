"""V1 approval repository implementation for SQLite."""

from __future__ import annotations

import sqlite3

from migration_factory.control_tower.domain.entities import ApprovalRecord, ApprovalResumeRecord


class SqliteV1ApprovalRepository:
    """SQLite repository for v1_approvals table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, approval: ApprovalRecord) -> None:
        self._connection.execute(
            """INSERT INTO v1_approvals (
                approval_id, job_id, interrupt_id, request_checksum,
                decision, approved_by, approval_comments,
                actor_type, actor_id, payload_json, payload_checksum,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                approval.approval_id,
                approval.job_id,
                approval.interrupt_id,
                approval.request_checksum,
                approval.decision,
                approval.approved_by,
                approval.approval_comments,
                approval.actor_type,
                approval.actor_id,
                approval.payload_json,
                approval.payload_checksum,
                approval.created_at,
            ),
        )

    def get(self, approval_id: str) -> ApprovalRecord | None:
        row = self._connection.execute(
            """SELECT approval_id, job_id, interrupt_id, request_checksum,
                      decision, approved_by, approval_comments,
                      actor_type, actor_id, payload_json, payload_checksum,
                      created_at
               FROM v1_approvals WHERE approval_id = ?""",
            (approval_id,),
        ).fetchone()
        if row is None:
            return None
        return ApprovalRecord(*row)

    def get_by_interrupt(
        self, interrupt_id: str, request_checksum: str
    ) -> ApprovalRecord | None:
        row = self._connection.execute(
            """SELECT approval_id, job_id, interrupt_id, request_checksum,
                      decision, approved_by, approval_comments,
                      actor_type, actor_id, payload_json, payload_checksum,
                      created_at
               FROM v1_approvals
               WHERE interrupt_id = ? AND request_checksum = ?""",
            (interrupt_id, request_checksum),
        ).fetchone()
        if row is None:
            return None
        return ApprovalRecord(*row)

    def list_for_job(self, job_id: str) -> tuple[ApprovalRecord, ...]:
        rows = self._connection.execute(
            """SELECT approval_id, job_id, interrupt_id, request_checksum,
                      decision, approved_by, approval_comments,
                      actor_type, actor_id, payload_json, payload_checksum,
                      created_at
               FROM v1_approvals
               WHERE job_id = ?
               ORDER BY created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(ApprovalRecord(*row) for row in rows)


class SqliteV1ApprovalResumeRepository:
    """SQLite repository for v1_approval_resume_queue table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, resume: ApprovalResumeRecord) -> None:
        self._connection.execute(
            """INSERT INTO v1_approval_resume_queue (
                resume_id, approval_id, job_id, command_type,
                command_payload_json, status, created_at,
                executed_at, failure_reason, correlation_id, causation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                resume.resume_id,
                resume.approval_id,
                resume.job_id,
                resume.command_type,
                resume.command_payload_json,
                resume.status,
                resume.created_at,
                resume.executed_at,
                resume.failure_reason,
                resume.correlation_id,
                resume.causation_id,
            ),
        )

    def list_pending(self) -> tuple[ApprovalResumeRecord, ...]:
        rows = self._connection.execute(
            """SELECT resume_id, approval_id, job_id, command_type,
                      command_payload_json, status, created_at,
                      executed_at, failure_reason, correlation_id, causation_id
               FROM v1_approval_resume_queue
               WHERE status = 'pending'
               ORDER BY created_at ASC""",
        ).fetchall()
        return tuple(ApprovalResumeRecord(*row) for row in rows)

    def list_for_approval(
        self, approval_id: str
    ) -> tuple[ApprovalResumeRecord, ...]:
        rows = self._connection.execute(
            """SELECT resume_id, approval_id, job_id, command_type,
                      command_payload_json, status, created_at,
                      executed_at, failure_reason, correlation_id, causation_id
               FROM v1_approval_resume_queue
               WHERE approval_id = ?
               ORDER BY created_at ASC""",
            (approval_id,),
        ).fetchall()
        return tuple(ApprovalResumeRecord(*row) for row in rows)

    def update_status(
        self,
        resume_id: str,
        status: str,
        executed_at: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        self._connection.execute(
            """UPDATE v1_approval_resume_queue
               SET status = ?,
                   executed_at = COALESCE(?, executed_at),
                   failure_reason = COALESCE(?, failure_reason)
               WHERE resume_id = ?""",
            (status, executed_at, failure_reason, resume_id),
        )
