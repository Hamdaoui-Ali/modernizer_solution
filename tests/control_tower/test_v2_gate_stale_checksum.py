"""Focused tests for F15 job033 — gate checksum stale protection."""

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
    conn = _connection(tmp_path, "stale.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc)
    return gate_repo, decision_repo, gate_svc, action_svc, conn


def _create_gate_and_get_checksum(
    gate_svc, gate_repo, phase="analysis_review", stage=1, job="job-abc",
) -> tuple[str, str]:
    """Create a gate and return (gate_id, current_checksum)."""
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=("ref",),
    ))
    assert result.status == "created"
    # Return the gate's checksum at creation time
    return result.gate_id, result.gate_checksum


# ── stale checksum cannot approve (or any action) ────────────────────


def test_stale_checksum_rejected_at_continue(tmp_path: Path) -> None:
    """A stale expected checksum blocks continue action."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id, _ = _create_gate_and_get_checksum(gate_svc, gate_repo)

    # Use clearly stale checksum
    result = action_svc.continue_from_gate(
        gate_id=gate_id,
        job_id="job-abc",
        decided_by="user-1",
        expected_gate_checksum="sha256:stale-checksum-that-will-never-match",
    )

    assert result.status == "stale_checksum"
    # Gate should remain OPEN — no side effects
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


def test_stale_checksum_rejected_at_approve(tmp_path: Path) -> None:
    """A stale expected checksum blocks approve at approval_review gate."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id, _ = _create_gate_and_get_checksum(
        gate_svc, gate_repo, phase="approval_review", stage=2,
    )

    result = action_svc.approve_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        expected_gate_checksum="sha256:stale",
    )

    assert result.status == "stale_checksum"
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


def test_stale_checksum_rejected_at_reject(tmp_path: Path) -> None:
    """A stale expected checksum blocks reject action."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id, _ = _create_gate_and_get_checksum(
        gate_svc, gate_repo, phase="approval_review", stage=2,
    )

    result = action_svc.reject_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        expected_gate_checksum="sha256:stale",
    )

    assert result.status == "stale_checksum"
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"


# ── matching checksum succeeds ───────────────────────────────────────


def test_matching_checksum_succeeds(tmp_path: Path) -> None:
    """The correct current checksum allows the action to proceed."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id, current_chk = _create_gate_and_get_checksum(gate_svc, gate_repo)

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        expected_gate_checksum=current_chk,
    )

    assert result.status == "executed"
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"


# ── response explains refresh needed ────────────────────────────────


def test_stale_checksum_response_explains_refresh(tmp_path: Path) -> None:
    """Stale checksum response contains a clear message to refresh."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id, _ = _create_gate_and_get_checksum(gate_svc, gate_repo)

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        expected_gate_checksum="sha256:old",
    )

    assert result.status == "stale_checksum"
    assert "checksum mismatch" in result.reason.lower()
    assert "refresh" in result.reason.lower()


# ── no side effects on stale checksum ────────────────────────────────


def test_stale_checksum_no_side_effects(tmp_path: Path) -> None:
    """Stale checksum does not leave any decision or gate state change."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id, _ = _create_gate_and_get_checksum(gate_svc, gate_repo)

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        expected_gate_checksum="sha256:stale",
    )

    assert result.status == "stale_checksum"
    assert result.decision_id == ""  # No decision was created

    # No decisions should exist for this gate
    decisions = decision_repo.list_by_gate(gate_id)
    assert len(decisions) == 0

    # Gate state unchanged
    gate = gate_repo.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "open"
    assert gate.resolved_at is None
    assert gate.resolved_by is None


# ── omitted expected_checksum (backward compat) ──────────────────────


def test_omitted_checksum_backward_compatible(tmp_path: Path) -> None:
    """Omitting expected_gate_checksum uses current checksum (backward compat)."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id, _ = _create_gate_and_get_checksum(gate_svc, gate_repo)

    # No expected_gate_checksum — uses internal computation
    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )

    assert result.status == "executed"


# ── no source writes ─────────────────────────────────────────────────


def test_stale_checksum_no_source_writes(tmp_path: Path) -> None:
    """Stale checksum test — no sandbox_path, argv, or command fields."""
    gate_repo, decision_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id, _ = _create_gate_and_get_checksum(gate_svc, gate_repo)

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
        expected_gate_checksum="sha256:stale",
    )

    assert result.status == "stale_checksum"
    assert not hasattr(result, "sandbox_path")
    assert not hasattr(result, "argv")
    assert not hasattr(result, "command")
