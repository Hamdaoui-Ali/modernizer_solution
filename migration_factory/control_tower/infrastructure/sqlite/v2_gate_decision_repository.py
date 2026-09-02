"""SQLite repository for F15 gate decisions."""

from __future__ import annotations

import sqlite3

from migration_factory.control_tower.domain.entities import GateDecisionRecord


class SqliteGateDecisionRepository:
    """Repository for v2_gate_decisions append-only table.

    Idempotency: duplicate (idempotency_key, request_checksum) is
    blocked by the UNIQUE index at the DB level. Conflicts (same
    key, different checksum) are detected at the service layer
    by checking before INSERT.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, record: GateDecisionRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_gate_decisions (
                decision_id, gate_id, job_id, action,
                expected_gate_checksum, idempotency_key, request_checksum,
                result_gate_id, result_command_id, result_revision_id,
                decided_by, decided_at, actor_type, actor_id,
                reason, correlation_id, causation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.decision_id,
                record.gate_id,
                record.job_id,
                record.action,
                record.expected_gate_checksum,
                record.idempotency_key,
                record.request_checksum,
                record.result_gate_id,
                record.result_command_id,
                record.result_revision_id,
                record.decided_by,
                record.decided_at,
                record.actor_type,
                record.actor_id,
                record.reason,
                record.correlation_id,
                record.causation_id,
            ),
        )

    def get(self, decision_id: str) -> GateDecisionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_gate_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def find_by_idempotency_key(
        self, idempotency_key: str
    ) -> GateDecisionRecord | None:
        """Find the most recent decision for an idempotency key."""
        row = self._connection.execute(
            """SELECT * FROM v2_gate_decisions
               WHERE idempotency_key = ?
               ORDER BY decided_at DESC
               LIMIT 1""",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def find_by_idempotency_key_and_checksum(
        self,
        idempotency_key: str,
        request_checksum: str,
    ) -> GateDecisionRecord | None:
        """Find the most recent exact idempotent match for a request."""
        row = self._connection.execute(
            """SELECT * FROM v2_gate_decisions
               WHERE idempotency_key = ?
                 AND request_checksum = ?
               ORDER BY decided_at DESC
               LIMIT 1""",
            (idempotency_key, request_checksum),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_by_gate(self, gate_id: str) -> tuple[GateDecisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_gate_decisions
               WHERE gate_id = ?
               ORDER BY decided_at DESC""",
            (gate_id,),
        ).fetchall()
        return tuple(self._row_to_record(r) for r in rows)

    def list_by_job(self, job_id: str) -> tuple[GateDecisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_gate_decisions
               WHERE job_id = ?
               ORDER BY decided_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_record(r) for r in rows)

    def _row_to_record(self, row: sqlite3.Row) -> GateDecisionRecord:
        return GateDecisionRecord(
            decision_id=str(row["decision_id"]),
            gate_id=str(row["gate_id"]),
            job_id=str(row["job_id"]),
            action=str(row["action"]),
            expected_gate_checksum=str(row["expected_gate_checksum"]),
            idempotency_key=str(row["idempotency_key"]),
            request_checksum=str(row["request_checksum"]),
            result_gate_id=(
                str(row["result_gate_id"])
                if row["result_gate_id"] is not None
                else None
            ),
            result_command_id=(
                str(row["result_command_id"])
                if row["result_command_id"] is not None
                else None
            ),
            result_revision_id=(
                str(row["result_revision_id"])
                if row["result_revision_id"] is not None
                else None
            ),
            decided_by=str(row["decided_by"]),
            decided_at=str(row["decided_at"]),
            actor_type=str(row["actor_type"]),
            actor_id=str(row["actor_id"]),
            reason=str(row["reason"]) if "reason" in row.keys() else "",
            correlation_id=(
                str(row["correlation_id"])
                if row["correlation_id"] is not None
                else None
            ),
            causation_id=(
                str(row["causation_id"])
                if row["causation_id"] is not None
                else None
            ),
        )
