"""SQLite repository for V2 reviewer critiques (F07)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class V2ReviewerCritiqueRecord:
    critique_id: str
    proposal_id: str
    proposal_type: str
    proposal_checksum: str
    context_pack_checksum: str
    decision: str  # accept, revise, reject
    reasoning: str
    missing_evidence_json: str
    unsafe_assumptions_json: str
    model_invocation_id: str | None
    created_at: str


class SqliteV2ReviewerRepository:
    """Repository for V2 reviewer critiques.

    All operations are append-only. No update, no delete.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_critique(self, record: V2ReviewerCritiqueRecord) -> None:
        """Persist a new reviewer critique."""
        self._connection.execute(
            """INSERT INTO v2_reviewer_critiques (
                critique_id, proposal_id, proposal_type,
                proposal_checksum, context_pack_checksum,
                decision, reasoning, missing_evidence_json,
                unsafe_assumptions_json, model_invocation_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.critique_id,
                record.proposal_id,
                record.proposal_type,
                record.proposal_checksum,
                record.context_pack_checksum,
                record.decision,
                record.reasoning,
                record.missing_evidence_json,
                record.unsafe_assumptions_json,
                record.model_invocation_id,
                record.created_at,
            ),
        )

    def get_critique(self, critique_id: str) -> V2ReviewerCritiqueRecord | None:
        """Retrieve a single critique by ID."""
        row = self._connection.execute(
            "SELECT * FROM v2_reviewer_critiques WHERE critique_id = ?",
            (critique_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_critiques_by_proposal(self, proposal_id: str) -> tuple[V2ReviewerCritiqueRecord, ...]:
        """List all critiques for a proposal, newest first."""
        rows = self._connection.execute(
            """SELECT * FROM v2_reviewer_critiques
               WHERE proposal_id = ?
               ORDER BY created_at DESC""",
            (proposal_id,),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def get_latest_accepted(
        self,
        proposal_id: str,
        proposal_checksum: str,
        context_pack_checksum: str,
    ) -> V2ReviewerCritiqueRecord | None:
        """Get the latest accepted critique matching exact proposal and context checksums.

        Returns None if no matching accepted critique exists — this is the
        fail-closed reviewer gate.
        """
        row = self._connection.execute(
            """SELECT * FROM v2_reviewer_critiques
               WHERE proposal_id = ?
                 AND proposal_checksum = ?
                 AND context_pack_checksum = ?
                 AND decision = 'accept'
               ORDER BY created_at DESC
               LIMIT 1""",
            (proposal_id, proposal_checksum, context_pack_checksum),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def _row_to_record(self, row: sqlite3.Row) -> V2ReviewerCritiqueRecord:
        return V2ReviewerCritiqueRecord(
            critique_id=str(row["critique_id"]),
            proposal_id=str(row["proposal_id"]),
            proposal_type=str(row["proposal_type"]),
            proposal_checksum=str(row["proposal_checksum"]),
            context_pack_checksum=str(row["context_pack_checksum"]),
            decision=str(row["decision"]),
            reasoning=str(row["reasoning"]),
            missing_evidence_json=str(row["missing_evidence_json"]),
            unsafe_assumptions_json=str(row["unsafe_assumptions_json"]),
            model_invocation_id=str(row["model_invocation_id"]) if row["model_invocation_id"] else None,
            created_at=str(row["created_at"]),
        )
