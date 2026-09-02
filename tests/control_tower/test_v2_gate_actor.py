"""Focused tests for F15 job034 — gate actor model."""

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
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.schemas.phase_gate import (
    GateActorType,
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


def _setup(tmp_path: Path) -> tuple:
    conn = _connection(tmp_path, "actor.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc)
    return gate_repo, decision_repo, gate_svc, action_svc, conn


def _create_open_gate(gate_svc, phase="analysis_review", stage=1, job="job-abc") -> str:
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:chk",
        source_artifact_refs=("ref",),
    ))
    assert result.status == "created"
    return result.gate_id


# ── assistant cannot approve alone ────────────────────────────────────


def test_assistant_cannot_approve(tmp_path: Path) -> None:
    """Assistant actor_type cannot perform approve action."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc, phase="approval_review", stage=2)

    result = action_svc._execute_action(
        gate_id=gate_id,
        job_id="job-abc",
        action=mocker_decision("approve"),
        decided_by="assistant-1",
        actor_type=GateActorType.ASSISTANT.value,
    )

    assert result.status == "actor_not_authoritative"
    assert "human actor" in result.reason

    # Gate should remain OPEN — no side effects
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


def test_assistant_cannot_reject(tmp_path: Path) -> None:
    """Assistant actor_type cannot perform reject action."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc, phase="approval_review", stage=2)

    result = action_svc._execute_action(
        gate_id=gate_id,
        job_id="job-abc",
        action=mocker_decision("reject"),
        decided_by="assistant-1",
        actor_type=GateActorType.ASSISTANT.value,
    )

    assert result.status == "actor_not_authoritative"
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


# ── assistant can perform non-authoritative actions ───────────────────


def test_assistant_can_continue(tmp_path: Path) -> None:
    """Assistant actor can perform non-authoritative actions like continue."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc._execute_action(
        gate_id=gate_id,
        job_id="job-abc",
        action=mocker_decision("continue"),
        decided_by="assistant-1",
        actor_type=GateActorType.ASSISTANT.value,
    )

    assert result.status == "executed"


# ── user approval is distinguishable ─────────────────────────────────


def test_user_approval_stores_human_actor(tmp_path: Path) -> None:
    """User (human) approval is stored with actor_type='human'."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc, phase="approval_review", stage=2)

    # Normal approve_from_gate uses human (default)
    result = action_svc.approve_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "executed"

    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.actor_type == GateActorType.HUMAN.value


def test_system_actor_type_persisted(tmp_path: Path) -> None:
    """System-initiated action stores actor_type='system'."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc._execute_action(
        gate_id=gate_id,
        job_id="job-abc",
        action=mocker_decision("continue"),
        decided_by="system-1",
        actor_type=GateActorType.SYSTEM.value,
    )
    assert result.status == "executed"

    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.actor_type == GateActorType.SYSTEM.value
    assert decision.decided_by == "system-1"


# ── audit shows actor ────────────────────────────────────────────────


def test_audit_shows_actor(tmp_path: Path) -> None:
    """Decision record shows who performed the action."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc, phase="approval_review", stage=2)

    result = action_svc.approve_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-42",
    )
    assert result.status == "executed"

    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.actor_type == "human"
    assert decision.actor_id == "user-42"
    assert decision.decided_by == "user-42"


# ── enum values ──────────────────────────────────────────────────────


def test_actor_enum_values() -> None:
    """GateActorType has expected values."""
    assert GateActorType.HUMAN.value == "human"
    assert GateActorType.ASSISTANT.value == "assistant"
    assert GateActorType.API.value == "api"
    assert GateActorType.SYSTEM.value == "system"


# ── authoritative actions set ────────────────────────────────────────


def test_authoritative_actions_include_approve_and_reject() -> None:
    """HUMAN_AUTHORITATIVE_ACTIONS includes approve and reject."""
    from migration_factory.control_tower.schemas.phase_gate import (
        HUMAN_AUTHORITATIVE_ACTIONS,
    )
    assert "approve" in HUMAN_AUTHORITATIVE_ACTIONS
    assert "reject" in HUMAN_AUTHORITATIVE_ACTIONS


# ── no source writes ─────────────────────────────────────────────────


def test_actor_model_no_source_writes(tmp_path: Path) -> None:
    """Verify that no sandbox_path, argv, or command fields are involved."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc._execute_action(
        gate_id=gate_id,
        job_id="job-abc",
        action=mocker_decision("continue"),
        decided_by="system-1",
        actor_type=GateActorType.SYSTEM.value,
    )

    assert result.status == "executed"
    assert not hasattr(result, "sandbox_path")
    assert not hasattr(result, "argv")
    assert not hasattr(result, "command")


# ── helper ───────────────────────────────────────────────────────────


def mocker_decision(value: str):
    """Create a GateDecision from string without importing the full enum.

    Allows test to work with the action value without the enum import.
    """
    from migration_factory.control_tower.schemas.phase_gate import GateDecision
    return GateDecision(value)
