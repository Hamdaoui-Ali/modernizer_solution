"""SQLite repository for repair assistant messages."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

_REPAIR_ASSISTANT_LEASE_SECONDS = int(os.environ.get("REPAIR_ASSISTANT_PROCESSING_LEASE_SECONDS", "120"))
_REPAIR_ASSISTANT_HEARTBEAT_SECONDS = int(os.environ.get("REPAIR_ASSISTANT_HEARTBEAT_SECONDS", "45"))


def _parse_utc(ts: str | None) -> datetime:
    if not ts:
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _lease_active(lease_expires_at: str | None, now: str) -> bool:
    return _parse_utc(lease_expires_at) > _parse_utc(now)


class LeaseState:
    OWNED = "owned"
    LOST = "lost"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


class ClaimOutcome:
    CLAIMED = "claimed"
    ALREADY_PROCESSING = "already_processing"
    COMPLETED = "completed"
    EXPIRED_TAKEOVER = "expired_takeover"


@dataclass(frozen=True)
class RepairAssistantMessageRecord:
    message_id: str
    job_id: str
    proposal_id: str
    attempt_number: int | None
    role: str
    message_text: str
    action: str | None
    revision_intent_json: str | None
    base_diff_checksum: str
    generated_proposal_id: str | None
    status: str
    created_at: str
    idempotency_key: str | None
    processing_owner: str | None = None
    processing_started_at: str | None = None
    lease_expires_at: str | None = None
    response_message_id: str | None = None
    failure_stage: str | None = None
    failure_code: str | None = None
    safe_failure_message: str | None = None
    correlation_id: str | None = None


class SqliteRepairAssistantRepository:
    """Repository for repair assistant messages."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_message(self, record: RepairAssistantMessageRecord) -> None:
        self._connection.execute(
            """INSERT INTO repair_assistant_messages (
                message_id, job_id, proposal_id, attempt_number,
                role, message_text, action, revision_intent_json,
                base_diff_checksum, generated_proposal_id, status,
                created_at, idempotency_key,
                processing_owner, processing_started_at, lease_expires_at,
                response_message_id,
                failure_stage, failure_code, safe_failure_message, correlation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?,
                      ?, ?, ?, ?)""",
            (
                record.message_id,
                record.job_id,
                record.proposal_id,
                record.attempt_number,
                record.role,
                record.message_text,
                record.action,
                record.revision_intent_json,
                record.base_diff_checksum,
                record.generated_proposal_id,
                record.status,
                record.created_at,
                record.idempotency_key,
                record.processing_owner,
                record.processing_started_at,
                record.lease_expires_at,
                record.response_message_id,
                record.failure_stage,
                record.failure_code,
                record.safe_failure_message,
                record.correlation_id,
            ),
        )

    def claim_idempotency_lease(
        self,
        *,
        job_id: str,
        proposal_id: str,
        idempotency_key: str,
        owner: str,
        now: str,
        lease_expiry: str,
        message_payload: tuple,
    ) -> tuple[str, RepairAssistantMessageRecord | None]:
        """Atomically claim an idempotency key within the current write transaction.

        Scoped by (job_id, proposal_id, idempotency_key).

        Returns (ClaimOutcome, record_or_None).
        """
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        existing = self.get_message_by_scoped_idempotency_key(
            job_id=job_id, proposal_id=proposal_id, idempotency_key=idempotency_key,
        )
        if existing is None:
            self._connection.execute(
                """INSERT INTO repair_assistant_messages (
                    message_id, job_id, proposal_id, attempt_number,
                    role, message_text, action, revision_intent_json,
                    base_diff_checksum, generated_proposal_id, status,
                    created_at, idempotency_key,
                    processing_owner, processing_started_at, lease_expires_at,
                    response_message_id,
                    failure_stage, failure_code, safe_failure_message, correlation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?,
                          ?, ?, ?, ?)""",
                (*message_payload, owner, now, lease_expiry, None, None, None, None, None),
            )
            return (ClaimOutcome.CLAIMED, None)

        if existing.status != "processing":
            return (ClaimOutcome.COMPLETED, existing)

        if _lease_active(existing.lease_expires_at, now):
            return (ClaimOutcome.ALREADY_PROCESSING, existing)

        cursor = self._connection.execute(
            """UPDATE repair_assistant_messages
               SET processing_owner = ?,
                   processing_started_at = ?,
                   lease_expires_at = ?,
                   created_at = ?
               WHERE job_id = ?
                 AND proposal_id = ?
                 AND idempotency_key = ?
                 AND processing_owner = ?
                 AND lease_expires_at = ?""",
            (owner, now, lease_expiry, now, job_id, proposal_id,
             idempotency_key, existing.processing_owner, existing.lease_expires_at),
        )
        if cursor.rowcount == 1:
            updated = self.get_message_by_scoped_idempotency_key(
                job_id=job_id, proposal_id=proposal_id, idempotency_key=idempotency_key,
            )
            return (ClaimOutcome.EXPIRED_TAKEOVER, updated)
        return (ClaimOutcome.ALREADY_PROCESSING, existing)

    def renew_lease(
        self,
        *,
        message_id: str,
        job_id: str,
        proposal_id: str,
        idempotency_key: str,
        processing_owner: str,
        new_lease_expires_at: str,
    ) -> bool:
        """Owner-bound lease renewal. Only the current processing_owner may
        extend the lease on an active processing record."""
        cursor = self._connection.execute(
            """UPDATE repair_assistant_messages
               SET lease_expires_at = ?
               WHERE message_id = ?
                 AND job_id = ?
                 AND proposal_id = ?
                 AND idempotency_key = ?
                 AND status = 'processing'
                 AND processing_owner = ?""",
            (new_lease_expires_at, message_id, job_id, proposal_id,
             idempotency_key, processing_owner),
        )
        return cursor.rowcount == 1

    def verify_ownership(
        self,
        *,
        message_id: str,
        job_id: str,
        proposal_id: str,
        idempotency_key: str,
        processing_owner: str,
    ) -> bool:
        """Check that the given owner still holds the lease on a processing record."""
        row = self._connection.execute(
            """SELECT processing_owner, lease_expires_at, status
               FROM repair_assistant_messages
               WHERE message_id = ?
                 AND job_id = ?
                 AND proposal_id = ?
                 AND idempotency_key = ?
                 AND status = 'processing'
                 AND processing_owner = ?""",
            (message_id, job_id, proposal_id, idempotency_key, processing_owner),
        ).fetchone()
        if row is None:
            return False
        now = datetime.now(timezone.utc).isoformat()
        return _lease_active(str(row["lease_expires_at"]), now)

    def get_message_by_scoped_idempotency_key(
        self, *, job_id: str, proposal_id: str, idempotency_key: str,
    ) -> RepairAssistantMessageRecord | None:
        row = self._connection.execute(
            """SELECT * FROM repair_assistant_messages
               WHERE job_id = ? AND proposal_id = ? AND idempotency_key = ?""",
            (job_id, proposal_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    def finalize_lease(
        self,
        *,
        message_id: str,
        owner: str,
        status: str,
        generated_proposal_id: str | None = None,
        response_message_id: str | None = None,
    ) -> str:
        """Owner-bound finalization with atomic CAS.

        WHERE message_id = ? AND processing_owner = ? AND status IN ('processing', 'revision_generating')

        Returns:
            LeaseState.OWNED — rowcount=1, clean finalization
            LeaseState.OWNED — already finalized with same data (idempotent replay)
            LeaseState.LOST — different owner or unexpected state
        """
        if generated_proposal_id is not None:
            cursor = self._connection.execute(
                """UPDATE repair_assistant_messages
                   SET status = ?, generated_proposal_id = ?,
                       response_message_id = COALESCE(?,
                           (SELECT response_message_id FROM repair_assistant_messages WHERE message_id = ?)),
                       processing_owner = NULL,
                       processing_started_at = NULL,
                       lease_expires_at = NULL
                   WHERE message_id = ?
                     AND processing_owner = ?
                     AND status IN ('processing', 'revision_generating')""",
                (status, generated_proposal_id, response_message_id, message_id,
                 message_id, owner),
            )
        else:
            cursor = self._connection.execute(
                """UPDATE repair_assistant_messages
                   SET status = ?,
                       response_message_id = COALESCE(?,
                           (SELECT response_message_id FROM repair_assistant_messages WHERE message_id = ?)),
                       processing_owner = NULL,
                       processing_started_at = NULL,
                       lease_expires_at = NULL
                   WHERE message_id = ?
                     AND processing_owner = ?
                     AND status IN ('processing', 'revision_generating')""",
                (status, response_message_id, message_id, message_id, owner),
            )
        if cursor.rowcount == 1:
            return LeaseState.OWNED
        check = self._connection.execute(
            "SELECT processing_owner, status FROM repair_assistant_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if check is not None:
            existing_owner = str(check["processing_owner"]) if check["processing_owner"] else None
            existing_status = str(check["status"])
            if existing_owner is None and existing_status == status:
                return LeaseState.OWNED
            if existing_owner != owner:
                return LeaseState.LOST
        return LeaseState.LOST

    def finalize_lease_with_failure(
        self,
        *,
        message_id: str,
        owner: str,
        status: str,
        failure_stage: str,
        failure_code: str,
        safe_failure_message: str,
        correlation_id: str,
        generated_proposal_id: str | None = None,
        response_message_id: str | None = None,
    ) -> str:
        cursor = self._connection.execute(
            """UPDATE repair_assistant_messages
               SET status = ?,
                   failure_stage = ?,
                   failure_code = ?,
                   safe_failure_message = ?,
                   correlation_id = ?,
                   generated_proposal_id = COALESCE(?, generated_proposal_id),
                   response_message_id = COALESCE(?,
                       (SELECT response_message_id FROM repair_assistant_messages WHERE message_id = ?)),
                   processing_owner = NULL,
                   processing_started_at = NULL,
                   lease_expires_at = NULL
               WHERE message_id = ?
                 AND processing_owner = ?
                 AND status IN ('processing', 'revision_generating')""",
            (status, failure_stage, failure_code, safe_failure_message, correlation_id,
             generated_proposal_id, response_message_id, message_id,
             message_id, owner),
        )
        if cursor.rowcount == 1:
            return LeaseState.OWNED
        check = self._connection.execute(
            "SELECT processing_owner, status FROM repair_assistant_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if check is not None:
            existing_owner = str(check["processing_owner"]) if check["processing_owner"] else None
            existing_status = str(check["status"])
            if existing_owner is None and existing_status == status:
                return LeaseState.OWNED
            if existing_owner != owner:
                return LeaseState.LOST
        return LeaseState.LOST

    def check_lease_state(self, *, message_id: str, owner: str) -> str:
        """Check the current lease state for a message. Returns one of LeaseState.*"""
        row = self._connection.execute(
            "SELECT processing_owner, status, lease_expires_at FROM repair_assistant_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return LeaseState.LOST
        if str(row["processing_owner"] or "") == owner and row["status"] in ("processing", "revision_generating"):
            now = datetime.now(timezone.utc).isoformat()
            if _lease_active(str(row["lease_expires_at"]), now):
                return LeaseState.OWNED
            return LeaseState.TEMPORARILY_UNAVAILABLE
        return LeaseState.LOST

    def get_message_by_response_id(self, response_message_id: str) -> RepairAssistantMessageRecord | None:
        row = self._connection.execute(
            "SELECT * FROM repair_assistant_messages WHERE message_id = ?",
            (response_message_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    def get_message(self, message_id: str) -> RepairAssistantMessageRecord | None:
        row = self._connection.execute(
            "SELECT * FROM repair_assistant_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    def get_message_by_idempotency_key(self, idempotency_key: str) -> RepairAssistantMessageRecord | None:
        row = self._connection.execute(
            "SELECT * FROM repair_assistant_messages WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    def list_messages(self, job_id: str, proposal_id: str) -> tuple[RepairAssistantMessageRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM repair_assistant_messages WHERE job_id = ? AND proposal_id = ? ORDER BY created_at",
            (job_id, proposal_id),
        ).fetchall()
        return tuple(self._row_to_message(row) for row in rows)

    def update_message_status(
        self,
        message_id: str,
        status: str,
        generated_proposal_id: str | None = None,
    ) -> None:
        if generated_proposal_id is not None:
            self._connection.execute(
                "UPDATE repair_assistant_messages SET status = ?, generated_proposal_id = ? WHERE message_id = ?",
                (status, generated_proposal_id, message_id),
            )
        else:
            self._connection.execute(
                "UPDATE repair_assistant_messages SET status = ? WHERE message_id = ?",
                (status, message_id),
            )

    def update_message_outcome(
        self, message_id: str, *, status: str, message_text: str,
    ) -> None:
        self._connection.execute(
            "UPDATE repair_assistant_messages SET status = ?, message_text = ? WHERE message_id = ?",
            (status, message_text, message_id),
        )

    def _row_to_message(self, row: sqlite3.Row) -> RepairAssistantMessageRecord:
        keys = row.keys()
        return RepairAssistantMessageRecord(
            message_id=str(row["message_id"]),
            job_id=str(row["job_id"]),
            proposal_id=str(row["proposal_id"]),
            attempt_number=int(row["attempt_number"]) if row["attempt_number"] is not None else None,
            role=str(row["role"]),
            message_text=str(row["message_text"]),
            action=str(row["action"]) if row["action"] else None,
            revision_intent_json=str(row["revision_intent_json"]) if row["revision_intent_json"] else None,
            base_diff_checksum=str(row["base_diff_checksum"]),
            generated_proposal_id=str(row["generated_proposal_id"]) if row["generated_proposal_id"] else None,
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            idempotency_key=str(row["idempotency_key"]) if row["idempotency_key"] else None,
            processing_owner=str(row["processing_owner"]) if "processing_owner" in keys and row["processing_owner"] else None,
            processing_started_at=str(row["processing_started_at"]) if "processing_started_at" in keys and row["processing_started_at"] else None,
            lease_expires_at=str(row["lease_expires_at"]) if "lease_expires_at" in keys and row["lease_expires_at"] else None,
            response_message_id=str(row["response_message_id"]) if "response_message_id" in keys and row["response_message_id"] else None,
            failure_stage=str(row["failure_stage"]) if "failure_stage" in keys and row["failure_stage"] else None,
            failure_code=str(row["failure_code"]) if "failure_code" in keys and row["failure_code"] else None,
            safe_failure_message=str(row["safe_failure_message"]) if "safe_failure_message" in keys and row["safe_failure_message"] else None,
            correlation_id=str(row["correlation_id"]) if "correlation_id" in keys and row["correlation_id"] else None,
        )
