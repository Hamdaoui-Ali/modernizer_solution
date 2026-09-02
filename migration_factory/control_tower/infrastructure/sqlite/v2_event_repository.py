"""SQLite repository for V2 cockpit job events."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text


@dataclass(frozen=True)
class V2JobEventRecord:
    event_id: str
    job_id: str
    stage: int | None
    type: str
    status: str
    message: str
    payload_json: str
    created_at: str
    sequence: int


class SqliteV2JobEventRepository:
    """Append-only V2 event repository."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(
        self,
        *,
        job_id: str,
        stage: int | None,
        event_type: str,
        status: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> V2JobEventRecord:
        sequence = self._next_sequence()
        record = V2JobEventRecord(
            event_id=uuid4().hex,
            job_id=job_id,
            stage=stage,
            type=event_type,
            status=status,
            message=message,
            payload_json=json.dumps(payload or {}, separators=(",", ":"), sort_keys=True),
            created_at=utc_now_text(),
            sequence=sequence,
        )
        self._connection.execute(
            """INSERT INTO v2_job_events (
                event_id, job_id, stage, type, status, message,
                payload_json, created_at, sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.event_id,
                record.job_id,
                record.stage,
                record.type,
                record.status,
                record.message,
                record.payload_json,
                record.created_at,
                record.sequence,
            ),
        )
        return record

    def list_after_sequence(self, job_id: str, sequence: int) -> tuple[V2JobEventRecord, ...]:
        rows = self._connection.execute(
            """SELECT * FROM v2_job_events
               WHERE job_id = ? AND sequence > ?
               ORDER BY sequence ASC""",
            (job_id, sequence),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def list_by_job(self, job_id: str) -> tuple[V2JobEventRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_job_events WHERE job_id = ? ORDER BY sequence ASC",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def _next_sequence(self) -> int:
        row = self._connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM v2_job_events").fetchone()
        return int(row[0])

    def _row_to_record(self, row: sqlite3.Row) -> V2JobEventRecord:
        return V2JobEventRecord(
            event_id=str(row["event_id"]),
            job_id=str(row["job_id"]),
            stage=int(row["stage"]) if row["stage"] is not None else None,
            type=str(row["type"]),
            status=str(row["status"]),
            message=str(row["message"]),
            payload_json=str(row["payload_json"]),
            created_at=str(row["created_at"]),
            sequence=int(row["sequence"]),
        )
