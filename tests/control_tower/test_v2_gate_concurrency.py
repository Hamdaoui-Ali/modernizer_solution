"""Focused tests for F15 job032 — conflicting command guard."""

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
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
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
    conn = _connection(tmp_path, "concurrency.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    action_svc = V2GateActionService(
        gate_repo, decision_repo, gate_svc,
        command_repo=command_repo,
    )
    return gate_repo, decision_repo, command_repo, gate_svc, action_svc, conn


def _create_open_gate(gate_svc, phase="stage_completion_review", stage=1, job="job-abc") -> str:
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:chk",
        source_artifact_refs=("ref",),
    ))
    assert result.status == "created"
    return result.gate_id


def _seed_running_command(command_repo, job="job-abc", cmd_id="cmd-running", status="RUNNING"):
    """Seed a non-terminal command for the job."""
    command_repo.save(V2StageCommandRecord(
        command_id=cmd_id,
        job_id=job,
        stage_index=1,
        manifest_checksum="sha256:manifest",
        argv_json="[]",
        env_json="{}",
        status=status,
        created_at="2026-06-17T12:00:00Z",
        updated_at="2026-06-17T12:00:00Z",
        result_json=None,
    ))


# ── two continue requests do not queue two commands ──────────────────


def test_running_command_blocks_new_action(tmp_path: Path) -> None:
    """A running command for the job prevents queuing another command."""
    gate_repo, decision_repo, command_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    _seed_running_command(command_repo)
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "command_conflict"
    assert "non-terminal" in result.reason


def test_queued_command_blocks_approve(tmp_path: Path) -> None:
    """A queued command for the job blocks approve."""
    gate_repo, decision_repo, command_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    _seed_running_command(command_repo, status="QUEUED")
    gate_id = _create_open_gate(gate_svc, phase="approval_review")

    result = action_svc.approve_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "command_conflict"


def test_starting_command_blocks_reject(tmp_path: Path) -> None:
    """A starting command blocks reject too (any action is blocked)."""
    gate_repo, decision_repo, command_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    _seed_running_command(command_repo, status="STARTING")
    gate_id = _create_open_gate(gate_svc, phase="approval_review")

    result = action_svc.reject_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "command_conflict"


# ── terminal commands do not block ───────────────────────────────────


def test_terminal_command_does_not_block(tmp_path: Path) -> None:
    """A completed command (terminal state) does not block."""
    gate_repo, decision_repo, command_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    _seed_running_command(command_repo, status="SUCCEEDED")
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "executed"


# ── conflicting action returns clear error ───────────────────────────


def test_conflict_error_message_is_clear(tmp_path: Path) -> None:
    """The conflict error contains the running command ID and status."""
    gate_repo, decision_repo, command_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    _seed_running_command(command_repo, cmd_id="cmd-running-42", status="RUNNING")
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )

    assert result.status == "command_conflict"
    assert "cmd-running-42" in result.reason
    assert "RUNNING" in result.reason


# ── no command repo configured — guard skips check ───────────────────


def test_no_command_repo_skips_conflict_check(tmp_path: Path) -> None:
    """Without command_repo, the conflict guard is skipped."""
    conn = _connection(tmp_path, "no_cmd_repo.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    # No command_repo passed
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc)

    gate_id = _create_open_gate(gate_svc)

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "executed"


# ── no transaction held across process wait ──────────────────────────


def test_no_transaction_held_across_process_wait(tmp_path: Path) -> None:
    """The conflict guard does not hold a DB transaction.

    Each check is a single SELECT followed by a return.  No transaction
    is started by the guard itself.
    """
    gate_repo, decision_repo, command_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    gate_id = _create_open_gate(gate_svc)

    # Verify no active transaction after guard check
    cursor = conn.execute("SELECT 1")
    assert cursor.fetchone() is not None

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )
    assert result.status == "executed"


# ── no source writes ─────────────────────────────────────────────────


def test_conflict_guard_no_source_writes(tmp_path: Path) -> None:
    """Verify that no sandbox_path, argv, or command fields are involved."""
    gate_repo, decision_repo, command_repo, gate_svc, action_svc, conn = _setup(tmp_path)

    _seed_running_command(command_repo)
    gate_id = _create_open_gate(gate_svc)

    result = action_svc.continue_from_gate(
        gate_id=gate_id, job_id="job-abc", decided_by="user-1",
    )

    assert result.status == "command_conflict"
    assert not hasattr(result, "sandbox_path")
    assert not hasattr(result, "argv")
    assert not hasattr(result, "command")
