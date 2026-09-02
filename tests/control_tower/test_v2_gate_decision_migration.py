"""Focused tests for F15 job012 — v2_gate_decisions SQLite migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)


def _connection(tmp_path: Path, name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(tmp_path / name),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _insert_decision(conn: sqlite3.Connection, **overrides) -> None:
    defaults = {
        "decision_id": "dec-001",
        "gate_id": "gate-abc",
        "job_id": "job-xyz",
        "action": "continue",
        "expected_gate_checksum": "sha256:abc123",
        "idempotency_key": "idem-001",
        "request_checksum": "sha256:req456",
        "decided_by": "user-1",
        "decided_at": "2026-06-17T14:00:00Z",
    }
    defaults.update(overrides)
    conn.execute(
        """
        INSERT INTO v2_gate_decisions
            (decision_id, gate_id, job_id, action, expected_gate_checksum,
             idempotency_key, request_checksum, decided_by, decided_at)
        VALUES (:decision_id, :gate_id, :job_id, :action, :expected_gate_checksum,
                :idempotency_key, :request_checksum, :decided_by, :decided_at)
        """,
        defaults,
    )


def test_migration_creates_table(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_dec.sqlite3")
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_gate_decisions'"
    ).fetchone()
    assert row is not None


def test_insert_decision(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_dec.sqlite3")
    _insert_decision(conn)
    row = conn.execute("SELECT * FROM v2_gate_decisions WHERE decision_id = 'dec-001'").fetchone()
    assert row["action"] == "continue"
    assert row["idempotency_key"] == "idem-001"


def test_duplicate_idempotency_matches(tmp_path: Path) -> None:
    """Duplicate (idempotency_key, request_checksum) fails on UNIQUE."""
    conn = _connection(tmp_path, "test_dec.sqlite3")
    _insert_decision(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_decision(conn)


def test_different_checksum_same_key_allowed(tmp_path: Path) -> None:
    """Different request_checksum with same key is allowed at DB level.
    (Service layer must check idempotency_key uniqueness for conflicts.)"""
    conn = _connection(tmp_path, "test_dec.sqlite3")
    _insert_decision(conn,
                     decision_id="dec-001",
                     idempotency_key="idem-001",
                     request_checksum="sha256:req-aaa")
    # Different checksum = different row in unique index, so allowed
    _insert_decision(conn,
                     decision_id="dec-002",
                     idempotency_key="idem-001",
                     request_checksum="sha256:req-bbb")
    rows = conn.execute(
        "SELECT decision_id FROM v2_gate_decisions WHERE idempotency_key = 'idem-001'"
    ).fetchall()
    assert len(rows) == 2


def test_result_columns_nullable(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_dec.sqlite3")
    conn.execute(
        """
        INSERT INTO v2_gate_decisions
            (decision_id, gate_id, job_id, action, expected_gate_checksum,
             idempotency_key, request_checksum, result_gate_id, result_command_id,
             result_revision_id, decided_by, decided_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("dec-r1", "gate-1", "job-1", "approve", "sha256:abc",
         "idem-r1", "sha256:req-r1", "gate-new", "cmd-new", "rev-new",
         "user-1", "2026-06-17T14:00:00Z"),
    )
    row = conn.execute("SELECT * FROM v2_gate_decisions WHERE decision_id = 'dec-r1'").fetchone()
    assert row["result_gate_id"] == "gate-new"
    assert row["result_command_id"] == "cmd-new"
    assert row["result_revision_id"] == "rev-new"


def test_update_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_dec.sqlite3")
    _insert_decision(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE v2_gate_decisions SET action = 'reject' WHERE decision_id = 'dec-001'"
        )


def test_delete_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_dec.sqlite3")
    _insert_decision(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM v2_gate_decisions WHERE decision_id = 'dec-001'")


def test_actor_defaults(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "test_dec.sqlite3")
    _insert_decision(conn)
    row = conn.execute("SELECT actor_type, actor_id FROM v2_gate_decisions WHERE decision_id = 'dec-001'").fetchone()
    assert row["actor_type"] == "human"
    assert row["actor_id"] == ""
