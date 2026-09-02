"""Regression tests for durable repair attempt counting."""

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
from migration_factory.control_tower.application.v2_repair_gate_service import (
    V2RepairGateService,
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


def _build_services(tmp_path: Path) -> tuple[sqlite3.Connection, SqlitePhaseGateRepository, SqliteGateDecisionRepository, V2PhaseGateService, V2GateActionService, V2RepairGateService]:
    conn = _connection(tmp_path, "repair_attempts.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)
    action_service = V2GateActionService(gate_repo, decision_repo, gate_service)
    repair_gate_service = V2RepairGateService(
        gate_service=gate_service,
        gate_action_service=action_service,
        max_repair_attempts=3,
    )
    return conn, gate_repo, decision_repo, gate_service, action_service, repair_gate_service


def _create_repair_gate(gate_service: V2PhaseGateService, gate_repo: SqlitePhaseGateRepository, *, job_id: str, checksum: str) -> str:
    result = gate_service.create_gate(
        CreateGateRequest(
            job_id=job_id,
            gate_phase="repair_review",
            stage_index=1,
            source_artifact_checksum=checksum,
            source_artifact_refs=("diagnosis:1",),
        )
    )
    assert result.status == "created"
    return result.gate_id


def test_attempt_limit_survives_service_recreation(tmp_path: Path) -> None:
    conn, gate_repo, decision_repo, gate_service, action_service, repair_gate_service = _build_services(tmp_path)
    first_gate_id = _create_repair_gate(gate_service, gate_repo, job_id="job-abc", checksum="sha256:first")

    first_resolution = repair_gate_service.reject_repair(
        gate_id=first_gate_id,
        job_id="job-abc",
        decided_by="human-1",
        reason="try again",
    )
    assert first_resolution.status == "executed"

    second_gate_id = _create_repair_gate(gate_service, gate_repo, job_id="job-abc", checksum="sha256:second")
    assert second_gate_id

    fresh_gate_repo = SqlitePhaseGateRepository(conn)
    fresh_decision_repo = SqliteGateDecisionRepository(conn)
    fresh_gate_service = V2PhaseGateService(fresh_gate_repo)
    fresh_action_service = V2GateActionService(fresh_gate_repo, fresh_decision_repo, fresh_gate_service)
    fresh_repair_gate_service = V2RepairGateService(
        gate_service=fresh_gate_service,
        gate_action_service=fresh_action_service,
        max_repair_attempts=3,
    )
    assert fresh_repair_gate_service.get_remaining_attempts("job-abc", 1) == 2
