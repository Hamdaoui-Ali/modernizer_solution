"""SQLite repository for V2 approval decisions and resume commands."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class V2ApprovalDecisionRecord:
    card_id: str
    interrupt_id: str
    request_checksum: str
    stage_index: int
    summary: str
    status: str
    created_at: str
    job_id: str = ""


@dataclass(frozen=True)
class V2ResumeCommandRecord:
    resume_id: str
    card_id: str
    decision: str
    job_id: str
    stage_index: int
    command_json: str
    created_at: str


class SqliteV2ApprovalRepository:
    """Repository for V2 approval decision cards."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_card(self, record: V2ApprovalDecisionRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_approval_decisions (
                card_id, interrupt_id, request_checksum, stage_index,
                summary, status, created_at, job_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.card_id,
                record.interrupt_id,
                record.request_checksum,
                record.stage_index,
                record.summary,
                record.status,
                record.created_at,
                record.job_id,
            ),
        )

    def update_card_status(self, card_id: str, new_status: str) -> None:
        """Update the status of a decision card (e.g. pending -> approved)."""
        self._connection.execute(
            "UPDATE v2_approval_decisions SET status = ? WHERE card_id = ?",
            (new_status, card_id),
        )

    def get_card(self, card_id: str) -> V2ApprovalDecisionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_approval_decisions WHERE card_id = ?",
            (card_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_card(row)

    def list_cards_by_job(self, job_id: str) -> tuple[V2ApprovalDecisionRecord, ...]:
        """List decision cards directly by job, including pending cards."""
        rows = self._connection.execute(
            """SELECT * FROM v2_approval_decisions
               WHERE job_id = ?
               ORDER BY created_at DESC""",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_card(row) for row in rows)

    def list_cards_by_status(self, status: str) -> tuple[V2ApprovalDecisionRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_approval_decisions WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
        return tuple(self._row_to_card(row) for row in rows)

    def save_resume(self, record: V2ResumeCommandRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_resume_commands (
                resume_id, card_id, decision, job_id,
                stage_index, command_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                record.resume_id,
                record.card_id,
                record.decision,
                record.job_id,
                record.stage_index,
                record.command_json,
                record.created_at,
            ),
        )

    def get_resume(self, resume_id: str) -> V2ResumeCommandRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_resume_commands WHERE resume_id = ?",
            (resume_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_resume(row)

    def list_resumes_by_job(self, job_id: str) -> tuple[V2ResumeCommandRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_resume_commands WHERE job_id = ? ORDER BY created_at DESC",
            (job_id,),
        ).fetchall()
        return tuple(self._row_to_resume(row) for row in rows)

    def _row_to_card(self, row: sqlite3.Row) -> V2ApprovalDecisionRecord:
        return V2ApprovalDecisionRecord(
            card_id=str(row["card_id"]),
            interrupt_id=str(row["interrupt_id"]),
            request_checksum=str(row["request_checksum"]),
            stage_index=int(row["stage_index"]),
            summary=str(row["summary"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            job_id=str(row["job_id"]) if "job_id" in row.keys() else "",
        )

    def _row_to_resume(self, row: sqlite3.Row) -> V2ResumeCommandRecord:
        return V2ResumeCommandRecord(
            resume_id=str(row["resume_id"]),
            card_id=str(row["card_id"]),
            decision=str(row["decision"]),
            job_id=str(row["job_id"]),
            stage_index=int(row["stage_index"]),
            command_json=str(row["command_json"]),
            created_at=str(row["created_at"]),
        )
