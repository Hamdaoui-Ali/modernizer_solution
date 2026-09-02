"""SQLite repository for V2 migration jobs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class V2MigrationJobRecord:
    job_id: str
    setup_id: str
    setup_checksum: str
    pipeline_id: str
    stage_chain_json: str
    status: str
    created_at: str
    updated_at: str
    correlation_id: str | None


class SqliteV2JobRepository:
    """Repository for V2 migration jobs.

    Jobs track setup-bound parent migration jobs across their lifecycle.
    All records are append-only: no UPDATE or DELETE is permitted.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, record: V2MigrationJobRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_migration_jobs (
                job_id, setup_id, setup_checksum, pipeline_id,
                stage_chain_json, status, created_at, updated_at,
                correlation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.job_id,
                record.setup_id,
                record.setup_checksum,
                record.pipeline_id,
                record.stage_chain_json,
                record.status,
                record.created_at,
                record.updated_at,
                record.correlation_id,
            ),
        )

    def get(self, job_id: str) -> V2MigrationJobRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_migration_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list(self) -> tuple[V2MigrationJobRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_migration_jobs ORDER BY created_at DESC"
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def list_by_setup(self, setup_id: str) -> tuple[V2MigrationJobRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_migration_jobs WHERE setup_id = ? ORDER BY created_at DESC",
            (setup_id,),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def list_by_status(self, status: str) -> tuple[V2MigrationJobRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_migration_jobs WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def get_auto_approval_enabled(self, job_id: str) -> bool:
        row = self._connection.execute(
            "SELECT auto_approval_enabled FROM v2_job_approval_settings WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return False
        return bool(int(row["auto_approval_enabled"]))

    def set_auto_approval_enabled(
        self,
        job_id: str,
        enabled: bool,
        *,
        updated_at: str,
        updated_by: str,
    ) -> bool:
        self._connection.execute(
            """INSERT INTO v2_job_approval_settings (
                job_id, auto_approval_enabled, updated_at, updated_by
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                auto_approval_enabled = excluded.auto_approval_enabled,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by""",
            (job_id, 1 if enabled else 0, updated_at, updated_by),
        )
        return enabled

    def _row_to_record(self, row: sqlite3.Row) -> V2MigrationJobRecord:
        return V2MigrationJobRecord(
            job_id=str(row["job_id"]),
            setup_id=str(row["setup_id"]),
            setup_checksum=str(row["setup_checksum"]),
            pipeline_id=str(row["pipeline_id"]),
            stage_chain_json=str(row["stage_chain_json"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            correlation_id=str(row["correlation_id"]) if row["correlation_id"] else None,
        )
