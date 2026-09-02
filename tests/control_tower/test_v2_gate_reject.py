"""Focused tests for F15 job030 — reject_gate action with reason persistence."""

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
    conn = _connection(tmp_path, "reject.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc)
    return gate_repo, decision_repo, gate_svc, action_svc, conn


def _create_open_gate(gate_svc, phase="approval_review", stage=2, job="job-abc") -> str:
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:reject-chk",
        source_artifact_refs=("ref1",),
    ))
    assert result.status == "created"
    return result.gate_id


# ── reject_gate success ─────────────────────────────────────────────


def test_reject_gate_persists_reason(tmp_path: Path) -> None:
    """Reject gate persists the rejection reason in the decision record."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.reject_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        reason="The analysis does not meet our quality standards",
    )

    assert result.status == "executed"
    assert result.decision_id
    # No command should be queued
    assert result.result_command_id is None
    assert result.result_revision_id is None

    # Gate should be resolved with REJECT
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"
    assert gate.gate_decision == "reject"

    # Decision should be persisted with reason
    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.action == "reject"
    assert decision.reason == "The analysis does not meet our quality standards"


def test_reject_gate_without_reason(tmp_path: Path) -> None:
    """Reject gate works with empty reason (default)."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.reject_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
    )

    assert result.status == "executed"

    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.reason == ""


# ── validation: rejected gate cannot continue ────────────────────────


def test_rejected_gate_cannot_continue(tmp_path: Path) -> None:
    """Once rejected, the gate cannot be used for continue."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    # Reject the gate
    result = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="Not ready",
    )
    assert result.status == "executed"

    # Try to continue — should fail
    continue_result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-2",
    )
    assert continue_result.status == "gate_not_open"


def test_rejected_gate_cannot_approve(tmp_path: Path) -> None:
    """Once rejected, the gate cannot be used for approve."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="Need more work",
    )
    assert result.status == "executed"

    # Try to approve — should fail
    approve_result = action_svc.approve_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-2",
    )
    assert approve_result.status == "gate_not_open"


# ── idempotency ──────────────────────────────────────────────────────


def test_reject_gate_idempotent(tmp_path: Path) -> None:
    """Reject gate with same idempotency key returns same result."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    r1 = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="First rejection",
        idempotency_key="idem-reject-1",
    )
    assert r1.status == "executed"

    r2 = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="First rejection",
        idempotency_key="idem-reject-1",
    )
    assert r2.status == "gate_not_open"


# ── decision is auditable ────────────────────────────────────────────


def test_reject_gate_decision_is_auditable(tmp_path: Path) -> None:
    """The decision record provides a full audit trail."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="Security concern",
    )

    assert result.status == "executed"

    # Read decision back — full audit record
    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.decision_id == result.decision_id
    assert decision.gate_id == gate_id
    assert decision.job_id == "job-abc"
    assert decision.action == "reject"
    assert decision.decided_by == "user-1"
    assert decision.reason == "Security concern"
    assert decision.decided_at  # timestamp present
    assert decision.expected_gate_checksum  # bound to gate snapshot
    # No command or revision references
    assert decision.result_command_id is None
    assert decision.result_revision_id is None


# ── validation: wrong phase ──────────────────────────────────────────


def test_reject_gate_on_analysis_gate_fails(tmp_path: Path) -> None:
    """Reject is only valid for approval_review gates."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc, phase="analysis_review")

    result = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="Not applicable",
    )

    assert result.status == "invalid_decision"


def test_reject_gate_on_planning_gate_fails(tmp_path: Path) -> None:
    """Reject is only valid for approval_review gates."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc, phase="planning_review")

    result = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="Not applicable",
    )

    assert result.status == "invalid_decision"


def test_reject_gate_on_repair_gate_succeeds(tmp_path: Path) -> None:
    """Reject is now valid for repair_review gates (F15-JOB-106)."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc, phase="repair_review")

    result = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="The repair proposal is too risky",
    )

    assert result.status == "executed"
    assert result.decision_id

    # Gate should be resolved with REJECT
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"
    assert gate.gate_decision == "reject"


# ── no command is queued ─────────────────────────────────────────────


def test_reject_gate_no_command_queued(tmp_path: Path) -> None:
    """Reject gate must not queue any command."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="No command expected",
    )

    assert result.status == "executed"
    assert result.result_command_id is None

    decision = decision_repo.get(result.decision_id)
    assert decision is not None
    assert decision.result_command_id is None


# ── no source writes ─────────────────────────────────────────────────


def test_reject_gate_no_source_writes(tmp_path: Path) -> None:
    """Verify that no sandbox_path, argv, or command fields are involved."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.reject_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        reason="Test rejection",
    )

    assert result.status == "executed"
    assert not hasattr(result, "sandbox_path")
    assert not hasattr(result, "argv")
    assert not hasattr(result, "command")
