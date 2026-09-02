"""SQLite repository for V2 Azure model health checks."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class V2ModelHealthCheckRecord:
    health_id: str
    profile_id: str
    profile_checksum: str
    overall_status: str
    role_checks_json: str
    structured_output_checks_json: str
    latency_ms_json: str
    error_classification: str
    artifact_id: str | None
    created_at: str
    created_by: str


class SqliteV2AzureHealthRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, record: V2ModelHealthCheckRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_model_health_checks (
                health_id, profile_id, profile_checksum, overall_status,
                role_checks_json, structured_output_checks_json,
                latency_ms_json, error_classification, artifact_id,
                created_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.health_id,
                record.profile_id,
                record.profile_checksum,
                record.overall_status,
                record.role_checks_json,
                record.structured_output_checks_json,
                record.latency_ms_json,
                record.error_classification,
                record.artifact_id,
                record.created_at,
                record.created_by,
            ),
        )

    def get_latest(self, profile_id: str) -> V2ModelHealthCheckRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_model_health_checks WHERE profile_id = ? ORDER BY created_at DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_for_profile(self, profile_id: str, limit: int = 10) -> tuple[V2ModelHealthCheckRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_model_health_checks WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def _row_to_record(self, row: sqlite3.Row) -> V2ModelHealthCheckRecord:
        return V2ModelHealthCheckRecord(
            health_id=str(row["health_id"]),
            profile_id=str(row["profile_id"]),
            profile_checksum=str(row["profile_checksum"]),
            overall_status=str(row["overall_status"]),
            role_checks_json=str(row["role_checks_json"]),
            structured_output_checks_json=str(row["structured_output_checks_json"]),
            latency_ms_json=str(row["latency_ms_json"]),
            error_classification=str(row["error_classification"]),
            artifact_id=str(row["artifact_id"]) if row["artifact_id"] else None,
            created_at=str(row["created_at"]),
            created_by=str(row["created_by"]),
        )
