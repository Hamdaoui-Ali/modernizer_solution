"""Focused tests for F15 jobs 021-022 — V2PhaseGateService create/resolve."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_phase_gate_service import (
    CreateGateRequest,
    CreateGateResult,
    ResolveGateRequest,
    ResolveGateResult,
    V2PhaseGateService,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.domain.gate_checksum import gate_checksum


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


# ── create gate ──────────────────────────────────────────────────────


def test_create_analysis_review_gate(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    result = service.create_gate(CreateGateRequest(
        job_id="job-abc",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=("artifact-1", "artifact-2"),
    ))

    assert result.status == "created"
    assert result.gate_id
    assert len(result.gate_checksum) == 64

    gate = repo.get(result.gate_id)
    assert gate is not None
    assert gate.gate_phase == "analysis_review"
    assert gate.stage_index == 1
    assert gate.gate_status == "open"


def test_create_gate_rejects_duplicate_open(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    r1 = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:abc", source_artifact_refs=("a1",),
    ))
    assert r1.status == "created"

    r2 = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:xyz", source_artifact_refs=("a2",),
    ))
    assert r2.status == "conflict"
    assert r2.existing_gate_id == r1.gate_id


def test_create_different_phase_allowed(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    r1 = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:abc", source_artifact_refs=(),
    ))
    assert r1.status == "created"

    r2 = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="planning_review", stage_index=1,
        source_artifact_checksum="sha256:def", source_artifact_refs=(),
    ))
    assert r2.status == "created"


def test_create_gate_checksum_binds_artifacts(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    result = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:deadbeef",
        source_artifact_refs=("a1", "a2"),
    ))

    expected = gate_checksum(
        gate_id=result.gate_id,
        job_id="job-abc",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:deadbeef",
        source_artifact_refs=["a1", "a2"],
    )
    assert result.gate_checksum == expected


# ── resolve gate ────────────────────────────────────────────────────


def test_resolve_gate_success(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    create_result = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:abc", source_artifact_refs=("a1",),
    ))

    resolve_result = service.resolve_gate(ResolveGateRequest(
        gate_id=create_result.gate_id,
        job_id="job-abc",
        gate_decision="continue",
        expected_gate_checksum=create_result.gate_checksum,
        resolved_by="user-1",
    ))

    assert resolve_result.status == "resolved"

    gate = repo.get(create_result.gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"
    assert gate.gate_decision == "continue"


def test_resolve_gate_stale_checksum(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    create_result = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:abc", source_artifact_refs=("a1",),
    ))

    result = service.resolve_gate(ResolveGateRequest(
        gate_id=create_result.gate_id,
        job_id="job-abc",
        gate_decision="continue",
        expected_gate_checksum="sha256:wrongchecksum000000000000000000",
        resolved_by="user-1",
    ))

    assert result.status == "stale_checksum"


def test_resolve_gate_not_found(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    result = service.resolve_gate(ResolveGateRequest(
        gate_id="nonexistent",
        job_id="job-abc",
        gate_decision="continue",
        expected_gate_checksum="sha256:anything",
        resolved_by="user-1",
    ))
    assert result.status == "not_found"


def test_resolve_gate_already_resolved(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    create_result = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:abc", source_artifact_refs=("a1",),
    ))

    # First resolve
    service.resolve_gate(ResolveGateRequest(
        gate_id=create_result.gate_id,
        job_id="job-abc",
        gate_decision="continue",
        expected_gate_checksum=create_result.gate_checksum,
        resolved_by="user-1",
    ))

    # Second resolve
    result2 = service.resolve_gate(ResolveGateRequest(
        gate_id=create_result.gate_id,
        job_id="job-abc",
        gate_decision="reject",
        expected_gate_checksum=create_result.gate_checksum,
        resolved_by="user-2",
    ))
    assert result2.status == "already_resolved"


def test_resolve_then_create_new_gate_allowed(tmp_path: Path) -> None:
    """After resolving, a new open gate for the same key is allowed."""
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    r1 = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:abc", source_artifact_refs=("a1",),
    ))
    service.resolve_gate(ResolveGateRequest(
        gate_id=r1.gate_id, job_id="job-abc",
        gate_decision="reanalyze",
        expected_gate_checksum=r1.gate_checksum,
        resolved_by="user-1",
    ))

    r2 = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:xyz", source_artifact_refs=("a2",),
    ))
    assert r2.status == "created"
    assert r2.gate_id != r1.gate_id


# ── supersede gate ──────────────────────────────────────────────────


def test_supersede_open_gate(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    result = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:abc", source_artifact_refs=(),
    ))

    ok = service.supersede_gate(result.gate_id)
    assert ok

    gate = repo.get(result.gate_id)
    assert gate is not None
    assert gate.gate_status == "superseded"


def test_supersede_resolved_gate_fails(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "svc.sqlite3")
    repo = SqlitePhaseGateRepository(conn)
    service = V2PhaseGateService(repo)

    result = service.create_gate(CreateGateRequest(
        job_id="job-abc", gate_phase="analysis_review", stage_index=1,
        source_artifact_checksum="sha256:abc", source_artifact_refs=(),
    ))
    service.resolve_gate(ResolveGateRequest(
        gate_id=result.gate_id, job_id="job-abc",
        gate_decision="continue",
        expected_gate_checksum=result.gate_checksum,
        resolved_by="user-1",
    ))

    ok = service.supersede_gate(result.gate_id)
    assert not ok
