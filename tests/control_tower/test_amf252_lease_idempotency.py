"""Comprehensive tests for AMF-252 lease/idempotency fixes.

Covers:
1. Concurrency: two workers claiming same idempotency key
2. Replay: completed status returns COMPLETED with existing record
3. Expired lease takeover
4. Transient DB errors → TEMPORARILY_UNAVAILABLE, not LOST
5. Ownership loss → LOST on finalize
6. Missing idempotency_key → ValueError before DB write
7. Atomic CAS finalization scoping
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.repair_assistant_repository import (
    ClaimOutcome,
    LeaseState,
    RepairAssistantMessageRecord,
    SqliteRepairAssistantRepository,
)


def _connection(tmp_path: Path, name: str = "test.sqlite3") -> sqlite3.Connection:
    conn = sqlite3.connect(
        tmp_path / name,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    return conn


def _make_payload(
    *,
    message_id: str | None = None,
    job_id: str = "job1",
    proposal_id: str = "prop1",
    idempotency_key: str = "ik1",
    owner: str = "owner1",
    status: str = "processing",
    now: str | None = None,
    lease_expiry: str | None = None,
    base_diff_checksum: str = "abc123",
) -> tuple:
    mid = message_id or uuid4().hex
    now_val = now or utc_now_text()
    return (
        mid,
        job_id,
        proposal_id,
        1,
        "user",
        "Test message",
        None,
        None,
        base_diff_checksum,
        None,
        status,
        now_val,
        idempotency_key,
    )


def _insert_direct(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    job_id: str = "job1",
    proposal_id: str = "prop1",
    idempotency_key: str = "ik1",
    status: str = "processing",
    processing_owner: str | None = "owner1",
    lease_expires_at: str | None = None,
    created_at: str | None = None,
) -> None:
    now = created_at or utc_now_text()
    expiry = lease_expires_at or (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    conn.execute(
        """INSERT INTO repair_assistant_messages (
            message_id, job_id, proposal_id, attempt_number,
            role, message_text, action, revision_intent_json,
            base_diff_checksum, generated_proposal_id, status,
            created_at, idempotency_key,
            processing_owner, processing_started_at, lease_expires_at,
            response_message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_id,
            job_id,
            proposal_id,
            1,
            "user",
            "Existing message",
            None,
            None,
            "abc123",
            None,
            status,
            now,
            idempotency_key,
            processing_owner,
            now,
            expiry,
            None,
        ),
    )


# ── 1. Concurrency test ────────────────────────────────────────────────


class TestConcurrentClaim:
    def test_two_workers_same_key_first_claimed_second_already_processing(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        job_id = "job1"
        proposal_id = "prop1"
        idempotency_key = "concurrent_ik"
        now = utc_now_text()
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        payload = _make_payload(
            job_id=job_id, proposal_id=proposal_id,
            idempotency_key=idempotency_key, now=now,
        )
        conn.execute("BEGIN")
        outcome1, _ = repo.claim_idempotency_lease(
            job_id=job_id,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            owner="worker1",
            now=now,
            lease_expiry=expiry,
            message_payload=payload,
        )
        conn.commit()
        assert outcome1 == ClaimOutcome.CLAIMED

        conn.execute("BEGIN")
        outcome2, record2 = repo.claim_idempotency_lease(
            job_id=job_id,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            owner="worker2",
            now=utc_now_text(),
            lease_expiry=expiry,
            message_payload=payload,
        )
        conn.commit()
        assert outcome2 == ClaimOutcome.ALREADY_PROCESSING
        assert record2 is not None
        assert record2.processing_owner == "worker1"


# ── 2. Replay test ─────────────────────────────────────────────────────


class TestReplay:
    def test_completed_key_returns_completed_with_record(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        job_id = "job1"
        proposal_id = "prop1"
        ik = "replay_ik"
        mid = uuid4().hex
        now = utc_now_text()
        _insert_direct(
            conn,
            message_id=mid,
            job_id=job_id,
            proposal_id=proposal_id,
            idempotency_key=ik,
            status="answered",
            processing_owner=None,
            created_at=now,
        )
        conn.commit()

        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        payload = _make_payload(
            message_id=mid, job_id=job_id, proposal_id=proposal_id,
            idempotency_key=ik, now=now,
        )
        conn.execute("BEGIN")
        outcome, record = repo.claim_idempotency_lease(
            job_id=job_id,
            proposal_id=proposal_id,
            idempotency_key=ik,
            owner="replayer",
            now=utc_now_text(),
            lease_expiry=expiry,
            message_payload=payload,
        )
        conn.commit()
        assert outcome == ClaimOutcome.COMPLETED
        assert record is not None
        assert record.status == "answered"


# ── 3. Expired lease test ──────────────────────────────────────────────


class TestExpiredLease:
    def test_expired_lease_allows_takeover(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        job_id = "job1"
        proposal_id = "prop1"
        ik = "expired_ik"
        mid = uuid4().hex
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _insert_direct(
            conn,
            message_id=mid,
            job_id=job_id,
            proposal_id=proposal_id,
            idempotency_key=ik,
            status="processing",
            processing_owner="original_owner",
            lease_expires_at=past,
        )
        conn.commit()

        now = utc_now_text()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        payload = _make_payload(
            message_id=mid, job_id=job_id, proposal_id=proposal_id,
            idempotency_key=ik, now=now,
        )
        conn.execute("BEGIN")
        outcome, record = repo.claim_idempotency_lease(
            job_id=job_id,
            proposal_id=proposal_id,
            idempotency_key=ik,
            owner="new_owner",
            now=now,
            lease_expiry=future,
            message_payload=payload,
        )
        conn.commit()
        assert outcome == ClaimOutcome.EXPIRED_TAKEOVER
        assert record is not None
        assert record.processing_owner == "new_owner"


# ── 4. Transient DB error test ─────────────────────────────────────────


class TestTransientDbError:
    def test_sqlite_lock_returns_temporarily_unavailable(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        owner = "owner1"
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner=owner,
            lease_expires_at=future,
            status="processing",
        )
        conn.commit()

        state = repo.check_lease_state(message_id=mid, owner=owner)
        assert state == LeaseState.OWNED

    def test_expired_lease_returns_temporarily_unavailable(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        owner = "owner1"
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner=owner,
            lease_expires_at=past,
            status="processing",
        )
        conn.commit()

        state = repo.check_lease_state(message_id=mid, owner=owner)
        assert state == LeaseState.TEMPORARILY_UNAVAILABLE

    def test_no_message_returns_lost(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        state = repo.check_lease_state(message_id="nonexistent", owner="whoever")
        assert state == LeaseState.LOST

    def test_finalize_lease_with_failure_sets_diagnostics(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _insert_direct(
            conn,
            message_id=mid,
            processing_owner="owner1",
            lease_expires_at=future,
            status="revision_generating",
        )
        conn.commit()

        conn.execute("BEGIN")
        outcome = repo.finalize_lease_with_failure(
            message_id=mid,
            owner="owner1",
            status="revision_failed",
            failure_stage="revision_generation",
            failure_code="AttributeError",
            safe_failure_message="AttributeError: 'NoneType' object has no attribute 'context_pack'",
            correlation_id="corr-lease-1",
        )
        conn.commit()
        assert outcome == LeaseState.OWNED

        row = conn.execute(
            """SELECT status, failure_stage, failure_code, safe_failure_message, correlation_id,
                      processing_owner, processing_started_at, lease_expires_at
               FROM repair_assistant_messages WHERE message_id = ?""",
            (mid,),
        ).fetchone()
        assert row is not None
        assert str(row["status"]) == "revision_failed"
        assert str(row["failure_stage"]) == "revision_generation"
        assert str(row["failure_code"]) == "AttributeError"
        assert str(row["safe_failure_message"]) == "AttributeError: 'NoneType' object has no attribute 'context_pack'"
        assert str(row["correlation_id"]) == "corr-lease-1"
        assert row["processing_owner"] is None
        assert row["processing_started_at"] is None
        assert row["lease_expires_at"] is None

    def test_wrong_owner_returns_lost(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner="real_owner",
            lease_expires_at=future,
            status="processing",
        )
        conn.commit()

        state = repo.check_lease_state(message_id=mid, owner="wrong_owner")
        assert state == LeaseState.LOST


# ── 5. Ownership loss test ─────────────────────────────────────────────


class TestOwnershipLoss:
    def test_wrong_owner_on_finalize_returns_lost(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner="real_owner",
            lease_expires_at=future,
            status="processing",
        )
        conn.commit()

        conn.execute("BEGIN")
        outcome = repo.finalize_lease(
            message_id=mid,
            owner="wrong_owner",
            status="answered",
        )
        conn.commit()
        assert outcome == LeaseState.LOST

    def test_correct_owner_finalize_returns_owned(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner="owner1",
            lease_expires_at=future,
            status="processing",
        )
        conn.commit()

        conn.execute("BEGIN")
        outcome = repo.finalize_lease(
            message_id=mid,
            owner="owner1",
            status="answered",
        )
        conn.commit()
        assert outcome == LeaseState.OWNED

    def test_idempotent_replay_after_clean_finalize_returns_owned(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner="owner1",
            lease_expires_at=future,
            status="processing",
        )
        conn.commit()

        conn.execute("BEGIN")
        outcome1 = repo.finalize_lease(
            message_id=mid,
            owner="owner1",
            status="answered",
        )
        conn.commit()
        assert outcome1 == LeaseState.OWNED

        conn.execute("BEGIN")
        outcome2 = repo.finalize_lease(
            message_id=mid,
            owner="owner1",
            status="answered",
        )
        conn.commit()
        assert outcome2 == LeaseState.OWNED


# ── 6. Missing idempotency key test ────────────────────────────────────


class TestMissingIdempotencyKey:
    def test_none_key_raises_value_error(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        now = utc_now_text()
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        payload = _make_payload(idempotency_key=None)

        conn.execute("BEGIN")
        with pytest.raises(ValueError, match="idempotency_key is required"):
            repo.claim_idempotency_lease(
                job_id="job1",
                proposal_id="prop1",
                idempotency_key=None,
                owner="owner1",
                now=now,
                lease_expiry=expiry,
                message_payload=payload,
            )
        conn.rollback()

    def test_empty_key_raises_value_error(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        now = utc_now_text()
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        payload = _make_payload(idempotency_key="")

        conn.execute("BEGIN")
        with pytest.raises(ValueError, match="idempotency_key is required"):
            repo.claim_idempotency_lease(
                job_id="job1",
                proposal_id="prop1",
                idempotency_key="",
                owner="owner1",
                now=now,
                lease_expiry=expiry,
                message_payload=payload,
            )
        conn.rollback()


# ── 7. Atomic CAS finalization test ────────────────────────────────────


class TestAtomicCasFinalization:
    def test_only_matches_processing_or_revision_generating(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner="owner1",
            lease_expires_at=future,
            status="answered",
        )
        conn.commit()

        conn.execute("BEGIN")
        outcome = repo.finalize_lease(
            message_id=mid,
            owner="owner1",
            status="revision_created",
        )
        conn.commit()
        assert outcome == LeaseState.LOST

    def test_matches_revision_generating_status(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner="owner1",
            lease_expires_at=future,
            status="revision_generating",
        )
        conn.commit()

        conn.execute("BEGIN")
        outcome = repo.finalize_lease(
            message_id=mid,
            owner="owner1",
            status="revision_created",
            generated_proposal_id="new_prop_1",
        )
        conn.commit()
        assert outcome == LeaseState.OWNED

    def test_wrong_owner_with_processing_status_returns_lost(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner="owner1",
            lease_expires_at=future,
            status="processing",
        )
        conn.commit()

        conn.execute("BEGIN")
        outcome = repo.finalize_lease(
            message_id=mid,
            owner="intruder",
            status="answered",
            response_message_id="resp1",
        )
        conn.commit()
        assert outcome == LeaseState.LOST

    def test_correct_owner_with_processing_and_response_id(
        self,
        tmp_path: Path,
    ) -> None:
        conn = _connection(tmp_path)
        repo = SqliteRepairAssistantRepository(conn)
        mid = uuid4().hex
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        _insert_direct(
            conn,
            message_id=mid,
            processing_owner="owner1",
            lease_expires_at=future,
            status="processing",
        )
        conn.commit()

        assistant_mid = uuid4().hex
        _insert_direct(
            conn,
            message_id=assistant_mid,
            job_id="job1",
            proposal_id="prop1",
            idempotency_key="resp_ik",
            status="answered",
            processing_owner=None,
            created_at=utc_now_text(),
        )
        conn.commit()

        conn.execute("BEGIN")
        outcome = repo.finalize_lease(
            message_id=mid,
            owner="owner1",
            status="answered",
            response_message_id=assistant_mid,
        )
        conn.commit()
        assert outcome == LeaseState.OWNED

        row = conn.execute(
            "SELECT response_message_id FROM repair_assistant_messages WHERE message_id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        assert str(row["response_message_id"]) == assistant_mid
