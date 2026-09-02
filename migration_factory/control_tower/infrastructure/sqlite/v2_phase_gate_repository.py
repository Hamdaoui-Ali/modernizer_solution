"""SQLite repository for F15 phase gates."""

from __future__ import annotations

import sqlite3

from migration_factory.control_tower.domain.entities import PhaseGateRecord


class SqlitePhaseGateRepository:
    """Repository for v2_phase_gates append-only table.

    Open gates may be updated (resolved, superseded). Resolved and
    superseded rows are protected from UPDATE by DB triggers.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, record: PhaseGateRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_phase_gates (
                gate_id, job_id, gate_phase, stage_index, gate_status,
                gate_decision, source_artifact_checksum,
                resolved_artifact_checksum, source_artifact_refs_json,
                created_at, resolved_at, resolved_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.gate_id,
                record.job_id,
                record.gate_phase,
                record.stage_index,
                record.gate_status,
                record.gate_decision,
                record.source_artifact_checksum,
                record.resolved_artifact_checksum,
                record.source_artifact_refs_json,
                record.created_at,
                record.resolved_at,
                record.resolved_by,
            ),
        )

    def get(self, gate_id: str) -> PhaseGateRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_phase_gates WHERE gate_id = ?",
            (gate_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_by_job(self, job_id: str) -> tuple[PhaseGateRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_phase_gates
               WHERE job_id = ?
               ORDER BY stage_index, gate_phase, created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_record(r) for r in rows)

    def list_by_job_and_stage(
        self, job_id: str, stage_index: int
    ) -> tuple[PhaseGateRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_phase_gates
               WHERE job_id = ? AND stage_index = ?
               ORDER BY gate_phase, created_at DESC""",
            (job_id, stage_index),
        ).fetchall()
        return tuple(self._row_to_record(r) for r in rows)

    def find_open(
        self, job_id: str, gate_phase: str, stage_index: int
    ) -> PhaseGateRecord | None:
        row = self._connection.execute(
            """SELECT * FROM v2_phase_gates
               WHERE job_id = ? AND gate_phase = ? AND stage_index = ?
                 AND gate_status = 'open'
               LIMIT 1""",
            (job_id, gate_phase, stage_index),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_open(self, job_id: str) -> tuple[PhaseGateRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_phase_gates
               WHERE job_id = ? AND gate_status = 'open'
               ORDER BY stage_index, gate_phase""",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_record(r) for r in rows)

    def resolve(
        self,
        gate_id: str,
        gate_decision: str,
        resolved_by: str,
        resolved_at: str,
        resolved_artifact_checksum: str | None = None,
    ) -> None:
        """Atomically resolve an open gate. DB trigger blocks if already resolved."""
        self._connection.execute(
            """UPDATE v2_phase_gates
               SET gate_status = 'resolved',
                   gate_decision = ?,
                   resolved_by = ?,
                   resolved_at = ?,
                   resolved_artifact_checksum = ?
               WHERE gate_id = ? AND gate_status = 'open'""",
            (gate_decision, resolved_by, resolved_at,
             resolved_artifact_checksum, gate_id),
        )

    def supersede(self, gate_id: str) -> None:
        """Mark an open gate as superseded. DB trigger blocks if not open."""
        self._connection.execute(
            """UPDATE v2_phase_gates
               SET gate_status = 'superseded'
               WHERE gate_id = ? AND gate_status = 'open'""",
            (gate_id,),
        )

    def _row_to_record(self, row: sqlite3.Row) -> PhaseGateRecord:
        return PhaseGateRecord(
            gate_id=str(row["gate_id"]),
            job_id=str(row["job_id"]),
            gate_phase=str(row["gate_phase"]),
            stage_index=int(row["stage_index"]),
            gate_status=str(row["gate_status"]),
            gate_decision=str(row["gate_decision"]),
            source_artifact_checksum=str(row["source_artifact_checksum"]),
            resolved_artifact_checksum=(
                str(row["resolved_artifact_checksum"])
                if row["resolved_artifact_checksum"] is not None
                else None
            ),
            source_artifact_refs_json=str(row["source_artifact_refs_json"]),
            created_at=str(row["created_at"]),
            resolved_at=(
                str(row["resolved_at"])
                if row["resolved_at"] is not None
                else None
            ),
            resolved_by=(
                str(row["resolved_by"])
                if row["resolved_by"] is not None
                else None
            ),
        )
