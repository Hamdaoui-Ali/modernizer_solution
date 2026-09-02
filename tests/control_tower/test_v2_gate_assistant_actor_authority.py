"""Regression tests for assistant-vs-human gate authority."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from migration_factory.control_tower.application.v2_gate_action_service import (
    V2GateActionService,
)
from migration_factory.control_tower.application.v2_gate_assistant import (
    GateActionExecutor,
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
from migration_factory.control_tower.schemas.phase_gate import GateActorType


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


def _setup(tmp_path: Path) -> tuple[V2PhaseGateService, SqlitePhaseGateRepository, SqliteGateDecisionRepository, V2GateActionService, GateActionExecutor]:
    conn = _connection(tmp_path, "assistant_authority.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)
    action_service = V2GateActionService(gate_repo, decision_repo, gate_service)
    executor = GateActionExecutor(action_service)
    return gate_service, gate_repo, decision_repo, action_service, executor


def _open_gate(gate_service: V2PhaseGateService, *, phase: str = "approval_review") -> str:
    result = gate_service.create_gate(
        CreateGateRequest(
            job_id="job-abc",
            gate_phase=phase,
            stage_index=2,
            source_artifact_checksum="sha256:gate",
            source_artifact_refs=("analysis:1", "plan:1"),
        )
    )
    assert result.status == "created"
    return result.gate_id


def test_assistant_approve_cannot_persist_human_decision(tmp_path: Path) -> None:
    gate_service, gate_repo, decision_repo, action_service, executor = _setup(tmp_path)
    gate_id = _open_gate(gate_service)
    gate = gate_repo.get(gate_id)

    result = executor.execute_approve(gate_id, "sha256:gate", job_id=gate.job_id)

    assert result.status == "actor_not_authoritative"
    assert decision_repo.list_by_gate(gate_id) == ()
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


def test_assistant_reject_cannot_persist_human_decision(tmp_path: Path) -> None:
    gate_service, gate_repo, decision_repo, action_service, executor = _setup(tmp_path)
    gate_id = _open_gate(gate_service)
    gate = gate_repo.get(gate_id)

    result = executor.execute_reject(gate_id, "sha256:gate", job_id=gate.job_id, reason="needs more work")

    assert result.status == "actor_not_authoritative"
    assert decision_repo.list_by_gate(gate_id) == ()
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


def test_human_approve_still_works_and_preserves_actor_type(tmp_path: Path) -> None:
    gate_service, gate_repo, decision_repo, action_service, executor = _setup(tmp_path)
    gate_id = _open_gate(gate_service)

    result = action_service.approve_from_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="human-1",
        actor_type=GateActorType.HUMAN.value,
    )

    assert result.status == "executed"
    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.actor_type == GateActorType.HUMAN.value
    assert decision.decided_by == "human-1"


def test_human_reject_still_works_and_preserves_actor_type(tmp_path: Path) -> None:
    gate_service, gate_repo, decision_repo, action_service, executor = _setup(tmp_path)
    gate_id = _open_gate(gate_service)

    result = action_service.reject_from_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="human-2",
        reason="not ready",
        actor_type=GateActorType.HUMAN.value,
    )

    assert result.status == "executed"
    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.actor_type == GateActorType.HUMAN.value
    assert decision.decided_by == "human-2"
