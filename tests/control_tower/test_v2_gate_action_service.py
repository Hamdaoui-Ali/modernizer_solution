"""Focused tests for F15 jobs 024-025 — V2GateActionService and continue validation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_gate_action_service import (
    V2GateActionService,
)
from migration_factory.control_tower.application.v2_phase_gate_service import (
    CreateGateRequest,
    V2PhaseGateService,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
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


def _setup(tmp_path: Path) -> tuple[V2GateActionService, SqlitePhaseGateRepository, SqliteGateDecisionRepository]:
    conn = _connection(tmp_path, "action.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)
    action_service = V2GateActionService(gate_repo, decision_repo, gate_service)
    return action_service, gate_repo, decision_repo


def _create_open_gate(service: V2PhaseGateService, job_id: str = "job-abc",
                      phase: str = "analysis_review", stage: int = 1) -> str:
    result = service.create_gate(CreateGateRequest(
        job_id=job_id, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=("a1",),
    ))
    assert result.status == "created"
    return result.gate_id


# ── continue_from_gate ───────────────────────────────────────────────


def test_continue_from_analysis_review_gate(tmp_path: Path) -> None:
    action_svc, gate_repo, decision_repo = _setup(tmp_path)
    gate_svc = V2PhaseGateService(gate_repo)

    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    result = action_svc.continue_from_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "executed"
    assert result.decision_id

    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"
    assert gate.gate_decision == "continue"

    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.action == "continue"


def test_continue_on_already_resolved_gate(tmp_path: Path) -> None:
    action_svc, gate_repo, decision_repo = _setup(tmp_path)
    gate_svc = V2PhaseGateService(gate_repo)

    gate_id = _create_open_gate(gate_svc, phase="stage_completion_review")
    action_svc.continue_from_gate(gate_id=gate_id, job_id="job-abc", decided_by="user-1")

    result2 = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-2",
    )
    assert result2.status == "gate_not_open"


def test_continue_idempotent(tmp_path: Path) -> None:
    action_svc, gate_repo, decision_repo = _setup(tmp_path)
    gate_svc = V2PhaseGateService(gate_repo)

    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    r1 = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        idempotency_key="idem-continue-1",
    )
    assert r1.status == "executed"

    # Same key on resolved gate returns gate_not_open
    # (gate status checked before idempotency per F1 contract)
    r2 = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        idempotency_key="idem-continue-1",
    )
    assert r2.status == "gate_not_open"


# ── invalid decision checks ──────────────────────────────────────────


def test_reject_at_analysis_review_not_allowed(tmp_path: Path) -> None:
    action_svc, gate_repo, decision_repo = _setup(tmp_path)
    gate_svc = V2PhaseGateService(gate_repo)

    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    result = action_svc.reject_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "invalid_decision"


def test_continue_at_approval_review_not_allowed(tmp_path: Path) -> None:
    action_svc, gate_repo, decision_repo = _setup(tmp_path)
    gate_svc = V2PhaseGateService(gate_repo)

    gate_id = _create_open_gate(gate_svc, phase="approval_review")

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "invalid_decision"


# ── approve / reject at approval gate ────────────────────────────────


def test_approve_at_approval_review_gate(tmp_path: Path) -> None:
    action_svc, gate_repo, decision_repo = _setup(tmp_path)
    gate_svc = V2PhaseGateService(gate_repo)

    gate_id = _create_open_gate(gate_svc, phase="approval_review", stage=2)

    result = action_svc.approve_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "executed"


def test_reject_at_approval_review_gate(tmp_path: Path) -> None:
    action_svc, gate_repo, decision_repo = _setup(tmp_path)
    gate_svc = V2PhaseGateService(gate_repo)

    gate_id = _create_open_gate(gate_svc, phase="approval_review", stage=2)

    result = action_svc.reject_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "executed"


# ── reanalyze creates new gate ──────────────────────────────────────


def test_reanalyze_creates_new_open_gate(tmp_path: Path) -> None:
    action_svc, gate_repo, decision_repo = _setup(tmp_path)
    gate_svc = V2PhaseGateService(gate_repo)

    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    result = action_svc.reanalyze_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "executed"
    assert result.result_gate_id is not None
    assert result.result_gate_id != gate_id

    # Old gate resolved
    old = gate_repo.get(gate_id)
    assert old is not None
    assert old.gate_status == "resolved"
    assert old.gate_decision == "reanalyze"

    # New gate open
    new = gate_repo.get(result.result_gate_id)
    assert new is not None
    assert new.gate_status == "open"


# ── gate_not_found ───────────────────────────────────────────────────


def test_action_on_nonexistent_gate(tmp_path: Path) -> None:
    action_svc, gate_repo, decision_repo = _setup(tmp_path)

    result = action_svc.continue_from_gate(
        gate_id="nonexistent", job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "gate_not_found"
