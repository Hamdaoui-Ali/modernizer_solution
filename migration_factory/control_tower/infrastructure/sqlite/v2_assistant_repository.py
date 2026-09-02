"""SQLite repository for V2 assistant messages and pending action drafts."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class V2AssistantMessageRecord:
    message_id: str
    job_id: str
    role: str
    content: str
    correlation_id: str | None
    created_at: str


@dataclass(frozen=True)
class V2PendingActionDraftRecord:
    action_id: str
    job_id: str
    action_type: str
    reason: str
    stage_index: int
    payload_checksum: str
    status: str
    created_at: str


class SqliteV2AssistantRepository:
    """Repository for V2 assistant messages and pending action drafts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_message(self, record: V2AssistantMessageRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_assistant_messages (
                message_id, job_id, role, content,
                correlation_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                record.message_id,
                record.job_id,
                record.role,
                record.content,
                record.correlation_id,
                record.created_at,
            ),
        )

    def get_message(self, message_id: str) -> V2AssistantMessageRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_assistant_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    def list_messages(self, job_id: str) -> tuple[V2AssistantMessageRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_assistant_messages WHERE job_id = ? ORDER BY created_at",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_message(row) for row in rows)

    def save_draft(self, record: V2PendingActionDraftRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_pending_action_drafts (
                action_id, job_id, action_type, reason,
                stage_index, payload_checksum, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.action_id,
                record.job_id,
                record.action_type,
                record.reason,
                record.stage_index,
                record.payload_checksum,
                record.status,
                record.created_at,
            ),
        )

    def update_draft_status(self, action_id: str, new_status: str) -> None:
        """Update the status of a pending action draft."""
        self._connection.execute(
            "UPDATE v2_pending_action_drafts SET status = ? WHERE action_id = ?",
            (new_status, action_id),
        )

    def get_draft(self, action_id: str) -> V2PendingActionDraftRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_pending_action_drafts WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_draft(row)

    def list_drafts(self, job_id: str) -> tuple[V2PendingActionDraftRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_pending_action_drafts WHERE job_id = ? ORDER BY created_at DESC",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_draft(row) for row in rows)

    def _row_to_message(self, row: sqlite3.Row) -> V2AssistantMessageRecord:
        return V2AssistantMessageRecord(
            message_id=str(row["message_id"]),
            job_id=str(row["job_id"]),
            role=str(row["role"]),
            content=str(row["content"]),
            correlation_id=str(row["correlation_id"]) if row["correlation_id"] else None,
            created_at=str(row["created_at"]),
        )

    def _row_to_draft(self, row: sqlite3.Row) -> V2PendingActionDraftRecord:
        return V2PendingActionDraftRecord(
            action_id=str(row["action_id"]),
            job_id=str(row["job_id"]),
            action_type=str(row["action_type"]),
            reason=str(row["reason"]),
            stage_index=int(row["stage_index"]),
            payload_checksum=str(row["payload_checksum"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
        )
