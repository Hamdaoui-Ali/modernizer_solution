"""SQLite repository for V2 stage command manifests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class V2StageCommandRecord:
    command_id: str
    job_id: str
    stage_index: int
    manifest_checksum: str
    argv_json: str
    env_json: str
    status: str
    created_at: str
    updated_at: str
    result_json: str | None
    gate_id: str | None = None
    decision_id: str | None = None


class SqliteV2CommandRepository:
    """Repository for V2 stage command manifests.

    Commands are backend-owned execution manifests. Browser payloads
    cannot supply argv or env values. All records are append-only.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, record: V2StageCommandRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_stage_commands (
                command_id, job_id, stage_index, manifest_checksum,
                argv_json, env_json, status, created_at, updated_at,
                result_json, gate_id, decision_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.command_id,
                record.job_id,
                record.stage_index,
                record.manifest_checksum,
                record.argv_json,
                record.env_json,
                record.status,
                record.created_at,
                record.updated_at,
                record.result_json,
                record.gate_id,
                record.decision_id,
            ),
        )

    def get(self, command_id: str) -> V2StageCommandRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_stage_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_by_job(self, job_id: str) -> tuple[V2StageCommandRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_stage_commands WHERE job_id = ? ORDER BY stage_index, created_at",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def list_by_job_and_stage(self, job_id: str, stage_index: int) -> tuple[V2StageCommandRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_stage_commands WHERE job_id = ? AND stage_index = ? ORDER BY created_at DESC",
            (job_id, stage_index),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def _row_to_record(self, row: sqlite3.Row) -> V2StageCommandRecord:
        return V2StageCommandRecord(
            command_id=str(row["command_id"]),
            job_id=str(row["job_id"]),
            stage_index=int(row["stage_index"]),
            manifest_checksum=str(row["manifest_checksum"]),
            argv_json=str(row["argv_json"]),
            env_json=str(row["env_json"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            result_json=str(row["result_json"]) if row["result_json"] else None,
            gate_id=str(row["gate_id"]) if row["gate_id"] else None,
            decision_id=str(row["decision_id"]) if row["decision_id"] else None,
        )
