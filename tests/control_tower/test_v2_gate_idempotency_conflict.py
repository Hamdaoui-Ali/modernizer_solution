"""Regression tests for gate decision idempotency conflicts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
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


def _setup(tmp_path: Path) -> tuple[V2PhaseGateService, SqlitePhaseGateRepository, SqliteGateDecisionRepository, V2GateActionService]:
    conn = _connection(tmp_path, "idempotency_conflict.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)
    action_service = V2GateActionService(gate_repo, decision_repo, gate_service)
    return gate_service, gate_repo, decision_repo, action_service


def _open_gate(gate_service: V2PhaseGateService) -> str:
    result = gate_service.create_gate(
        CreateGateRequest(
            job_id="job-abc",
            gate_phase="approval_review",
            stage_index=2,
            source_artifact_checksum="sha256:gate",
            source_artifact_refs=("analysis:1", "plan:1"),
        )
    )
    assert result.status == "created"
    return result.gate_id


def test_same_key_same_checksum_is_idempotent(tmp_path: Path) -> None:
    gate_service, gate_repo, decision_repo, action_service = _setup(tmp_path)
    gate_id = _open_gate(gate_service)

    r1 = action_service.reject_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="human-1",
        reason="needs more work",
        idempotency_key="idem-1",
    )
    assert r1.status == "executed"

    r2 = action_service.reject_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="human-1",
        reason="needs more work",
        idempotency_key="idem-1",
    )
    assert r2.status == "gate_not_open"
    assert len(decision_repo.list_by_gate(gate_id)) == 1


def test_same_key_different_checksum_is_conflict(tmp_path: Path) -> None:
    gate_service, gate_repo, decision_repo, action_service = _setup(tmp_path)
    gate_id = _open_gate(gate_service)

    r1 = action_service.reject_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="human-1",
        reason="first reason",
        idempotency_key="idem-2",
    )
    assert r1.status == "executed"

    r2 = action_service.reject_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="human-1",
        reason="different reason",
        idempotency_key="idem-2",
    )
    assert r2.status == "gate_not_open"
    assert len(decision_repo.list_by_gate(gate_id)) == 1
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"
