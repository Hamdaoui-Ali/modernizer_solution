"""Focused tests for F15 jobs 014-017 — repositories and UnitOfWork wiring."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.domain.entities import (
    ArtifactRevisionRecord,
    GateDecisionRecord,
    PhaseGateRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteControlTowerUnitOfWork,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
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


# ── PhaseGate repository ─────────────────────────────────────────────


def test_phase_gate_repo_save_and_get(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "pg.sqlite3")
    repo = SqlitePhaseGateRepository(conn)

    record = PhaseGateRecord(
        gate_id="gate-001", job_id="job-abc", gate_phase="analysis_review",
        stage_index=1, gate_status="open", gate_decision="pending",
        source_artifact_checksum="sha256:abc",
        resolved_artifact_checksum=None,
        source_artifact_refs_json='["a1","a2"]',
        created_at="2026-06-17T12:00:00Z",
    )
    repo.save(record)

    fetched = repo.get("gate-001")
    assert fetched is not None
    assert fetched.gate_id == "gate-001"
    assert fetched.gate_status == "open"


def test_phase_gate_repo_list_by_job(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "pg.sqlite3")
    repo = SqlitePhaseGateRepository(conn)

    repo.save(PhaseGateRecord(
        gate_id="g1", job_id="j1", gate_phase="analysis_review",
        stage_index=1, gate_status="open", gate_decision="pending",
        source_artifact_checksum="", resolved_artifact_checksum=None,
        source_artifact_refs_json="[]", created_at="2026-06-17T12:00:00Z",
    ))
    repo.save(PhaseGateRecord(
        gate_id="g2", job_id="j1", gate_phase="planning_review",
        stage_index=1, gate_status="open", gate_decision="pending",
        source_artifact_checksum="", resolved_artifact_checksum=None,
        source_artifact_refs_json="[]", created_at="2026-06-17T12:00:00Z",
    ))

    gates = repo.list_by_job("j1")
    assert len(gates) == 2


def test_phase_gate_repo_find_open(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "pg.sqlite3")
    repo = SqlitePhaseGateRepository(conn)

    repo.save(PhaseGateRecord(
        gate_id="g1", job_id="j1", gate_phase="analysis_review",
        stage_index=1, gate_status="open", gate_decision="pending",
        source_artifact_checksum="", resolved_artifact_checksum=None,
        source_artifact_refs_json="[]", created_at="2026-06-17T12:00:00Z",
    ))

    found = repo.find_open("j1", "analysis_review", 1)
    assert found is not None
    assert found.gate_id == "g1"

    not_found = repo.find_open("j1", "planning_review", 1)
    assert not_found is None


def test_phase_gate_repo_resolve(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "pg.sqlite3")
    repo = SqlitePhaseGateRepository(conn)

    repo.save(PhaseGateRecord(
        gate_id="g1", job_id="j1", gate_phase="analysis_review",
        stage_index=1, gate_status="open", gate_decision="pending",
        source_artifact_checksum="", resolved_artifact_checksum=None,
        source_artifact_refs_json="[]", created_at="2026-06-17T12:00:00Z",
    ))
    repo.resolve("g1", "continue", "user-1", "2026-06-17T13:00:00Z")

    resolved = repo.get("g1")
    assert resolved is not None
    assert resolved.gate_status == "resolved"
    assert resolved.gate_decision == "continue"


def test_phase_gate_repo_supersede(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "pg.sqlite3")
    repo = SqlitePhaseGateRepository(conn)

    repo.save(PhaseGateRecord(
        gate_id="g1", job_id="j1", gate_phase="analysis_review",
        stage_index=1, gate_status="open", gate_decision="pending",
        source_artifact_checksum="", resolved_artifact_checksum=None,
        source_artifact_refs_json="[]", created_at="2026-06-17T12:00:00Z",
    ))
    repo.supersede("g1")

    superseded = repo.get("g1")
    assert superseded is not None
    assert superseded.gate_status == "superseded"


# ── GateDecision repository ──────────────────────────────────────────


def test_decision_repo_save_and_get(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "gd.sqlite3")
    repo = SqliteGateDecisionRepository(conn)

    record = GateDecisionRecord(
        decision_id="dec-001", gate_id="gate-abc", job_id="job-xyz",
        action="continue", expected_gate_checksum="sha256:abc",
        idempotency_key="idem-001", request_checksum="sha256:req",
        decided_by="user-1", decided_at="2026-06-17T14:00:00Z",
    )
    repo.save(record)

    fetched = repo.get("dec-001")
    assert fetched is not None
    assert fetched.action == "continue"


def test_decision_repo_idempotency_key_lookup(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "gd.sqlite3")
    repo = SqliteGateDecisionRepository(conn)

    repo.save(GateDecisionRecord(
        decision_id="dec-001", gate_id="gate-abc", job_id="job-xyz",
        action="approve", expected_gate_checksum="sha256:abc",
        idempotency_key="idem-abc", request_checksum="sha256:req",
        decided_by="user-1", decided_at="2026-06-17T14:00:00Z",
    ))

    found = repo.find_by_idempotency_key("idem-abc")
    assert found is not None
    assert found.decision_id == "dec-001"

    not_found = repo.find_by_idempotency_key("nonexistent")
    assert not_found is None


def test_decision_repo_list_by_gate(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "gd.sqlite3")
    repo = SqliteGateDecisionRepository(conn)

    repo.save(GateDecisionRecord(
        decision_id="d1", gate_id="gate-a", job_id="j1", action="continue",
        expected_gate_checksum="chk", idempotency_key="ik1",
        request_checksum="req1", decided_by="u1",
        decided_at="2026-06-17T14:00:00Z",
    ))
    repo.save(GateDecisionRecord(
        decision_id="d2", gate_id="gate-a", job_id="j1", action="reject",
        expected_gate_checksum="chk", idempotency_key="ik2",
        request_checksum="req2", decided_by="u1",
        decided_at="2026-06-17T15:00:00Z",
    ))

    decisions = repo.list_by_gate("gate-a")
    assert len(decisions) == 2


# ── ArtifactRevision repository ──────────────────────────────────────


def test_revision_repo_save_and_get(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "ar.sqlite3")
    repo = SqliteArtifactRevisionRepository(conn)

    record = ArtifactRevisionRecord(
        revision_id="rev-001", job_id="job-abc", stage_index=1,
        revision_kind="analysis", revision_status="draft",
        revision_order=0, evidence_checksum="sha256:abc",
        prior_revision_checksum=None, artifact_refs_json='["a1"]',
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id=None, created_at="2026-06-17T12:00:00Z",
        created_by="system",
    )
    repo.save(record)

    fetched = repo.get("rev-001")
    assert fetched is not None
    assert fetched.revision_kind == "analysis"


def test_revision_repo_find_accepted(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "ar.sqlite3")
    repo = SqliteArtifactRevisionRepository(conn)

    repo.save(ArtifactRevisionRecord(
        revision_id="rev-a", job_id="j1", stage_index=1,
        revision_kind="analysis", revision_status="accepted",
        revision_order=0, evidence_checksum="sha256:abc",
        prior_revision_checksum=None, artifact_refs_json="[]",
        prior_revision_id=None, superseded_by_revision_id=None,
        accepted_at_gate_id="gate-1", created_at="2026-06-17T12:00:00Z",
        created_by="system", accepted_at="2026-06-17T13:00:00Z",
        accepted_by="user-1",
    ))

    found = repo.find_accepted("j1", 1, "analysis")
    assert found is not None
    assert found.revision_id == "rev-a"
    assert found.accepted_at_gate_id == "gate-1"


def test_revision_repo_list_by_prior(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "ar.sqlite3")
    repo = SqliteArtifactRevisionRepository(conn)

    repo.save(ArtifactRevisionRecord(
        revision_id="rev-v1", job_id="j1", stage_index=1,
        revision_kind="analysis", revision_status="superseded",
        revision_order=0, evidence_checksum="sha256:v1",
        prior_revision_checksum=None, artifact_refs_json="[]",
        prior_revision_id=None, superseded_by_revision_id="rev-v2",
        accepted_at_gate_id=None, created_at="2026-06-17T12:00:00Z",
        created_by="system",
    ))
    repo.save(ArtifactRevisionRecord(
        revision_id="rev-v2", job_id="j1", stage_index=1,
        revision_kind="analysis", revision_status="accepted",
        revision_order=1, evidence_checksum="sha256:v2",
        prior_revision_checksum="sha256:v1", artifact_refs_json="[]",
        prior_revision_id="rev-v1", superseded_by_revision_id=None,
        accepted_at_gate_id="gate-1", created_at="2026-06-17T13:00:00Z",
        created_by="system", accepted_at="2026-06-17T14:00:00Z",
        accepted_by="user-1",
    ))

    successors = repo.list_by_prior("rev-v1")
    assert len(successors) == 1
    assert successors[0].revision_id == "rev-v2"


# ── UnitOfWork wiring ────────────────────────────────────────────────


def test_uow_exposes_f15_repositories(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "uow.sqlite3")
    uow = SqliteControlTowerUnitOfWork(conn)

    assert uow.phase_gates is not None
    assert isinstance(uow.phase_gates, SqlitePhaseGateRepository)
    assert uow.gate_decisions is not None
    assert isinstance(uow.gate_decisions, SqliteGateDecisionRepository)
    assert uow.artifact_revisions is not None
    assert isinstance(uow.artifact_revisions, SqliteArtifactRevisionRepository)


def test_uow_transaction_commits_gate_save(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "uow.sqlite3")
    with SqliteControlTowerUnitOfWork(conn) as uow:
        uow.phase_gates.save(PhaseGateRecord(
            gate_id="g-txn", job_id="j-txn", gate_phase="analysis_review",
            stage_index=1, gate_status="open", gate_decision="pending",
            source_artifact_checksum="", resolved_artifact_checksum=None,
            source_artifact_refs_json="[]", created_at="2026-06-17T12:00:00Z",
        ))

    # After commit, gate should be visible
    fetched = SqlitePhaseGateRepository(conn).get("g-txn")
    assert fetched is not None
