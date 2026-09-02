"""Focused tests for F15 job018 — gate decision immutability triggers.

The migrations 0039, 0040, 0041 already include UPDATE/DELETE triggers.
This test verifies they prevent mutation at the database level.
"""

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


# ── v2_phase_gates immutability ──────────────────────────────────────


def test_phase_gates_resolved_update_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "imm_pg.sqlite3")
    conn.execute(
        """INSERT INTO v2_phase_gates
           (gate_id, job_id, gate_phase, stage_index, gate_status, gate_decision,
            source_artifact_checksum, source_artifact_refs_json, created_at,
            resolved_at, resolved_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("g1", "j1", "analysis_review", 1, "resolved", "continue",
         "chk", "[]", "2026-06-17T12:00:00Z", "2026-06-17T13:00:00Z", "u1"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE v2_phase_gates SET gate_decision = 'reject' WHERE gate_id = 'g1'")


def test_phase_gates_delete_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "imm_pg.sqlite3")
    conn.execute(
        """INSERT INTO v2_phase_gates
           (gate_id, job_id, gate_phase, stage_index, gate_status, gate_decision,
            source_artifact_checksum, source_artifact_refs_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("g1", "j1", "analysis_review", 1, "open", "pending", "", "[]", "2026-06-17T12:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="DELETE forbidden"):
        conn.execute("DELETE FROM v2_phase_gates WHERE gate_id = 'g1'")


def test_phase_gates_superseded_update_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "imm_pg.sqlite3")
    conn.execute(
        """INSERT INTO v2_phase_gates
           (gate_id, job_id, gate_phase, stage_index, gate_status, gate_decision,
            source_artifact_checksum, source_artifact_refs_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("g1", "j1", "analysis_review", 1, "superseded", "pending", "", "[]", "2026-06-17T12:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE v2_phase_gates SET gate_decision = 'continue' WHERE gate_id = 'g1'")


# ── v2_gate_decisions immutability ───────────────────────────────────


def test_gate_decisions_update_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "imm_gd.sqlite3")
    conn.execute(
        """INSERT INTO v2_gate_decisions
           (decision_id, gate_id, job_id, action, expected_gate_checksum,
            idempotency_key, request_checksum, decided_by, decided_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("d1", "g1", "j1", "continue", "chk", "ik1", "req1", "u1", "2026-06-17T14:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="UPDATE forbidden"):
        conn.execute("UPDATE v2_gate_decisions SET action = 'reject' WHERE decision_id = 'd1'")


def test_gate_decisions_delete_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "imm_gd.sqlite3")
    conn.execute(
        """INSERT INTO v2_gate_decisions
           (decision_id, gate_id, job_id, action, expected_gate_checksum,
            idempotency_key, request_checksum, decided_by, decided_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("d1", "g1", "j1", "continue", "chk", "ik1", "req1", "u1", "2026-06-17T14:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="DELETE forbidden"):
        conn.execute("DELETE FROM v2_gate_decisions WHERE decision_id = 'd1'")


# ── v2_artifact_revisions immutability ───────────────────────────────


def test_artifact_revisions_update_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "imm_ar.sqlite3")
    conn.execute(
        """INSERT INTO v2_artifact_revisions
           (revision_id, job_id, stage_index, revision_kind, revision_status,
            revision_order, evidence_checksum, artifact_refs_json, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("r1", "j1", 1, "analysis", "draft", 0, "chk", "[]", "2026-06-17T12:00:00Z", "sys"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="UPDATE forbidden"):
        conn.execute("UPDATE v2_artifact_revisions SET revision_status = 'accepted' WHERE revision_id = 'r1'")


def test_artifact_revisions_delete_blocked(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "imm_ar.sqlite3")
    conn.execute(
        """INSERT INTO v2_artifact_revisions
           (revision_id, job_id, stage_index, revision_kind, revision_status,
            revision_order, evidence_checksum, artifact_refs_json, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("r1", "j1", 1, "analysis", "draft", 0, "chk", "[]", "2026-06-17T12:00:00Z", "sys"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="DELETE forbidden"):
        conn.execute("DELETE FROM v2_artifact_revisions WHERE revision_id = 'r1'")
