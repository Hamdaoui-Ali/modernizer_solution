"""Focused tests for F15 job031 — gate available-actions resolver."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_phase_gate_service import (
    AvailableAction,
    CreateGateRequest,
    V2PhaseGateService,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
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
    conn = _connection(tmp_path, "available_actions.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    return gate_repo, gate_svc, conn


def _create_gate(gate_svc, phase, stage=1, job="job-abc") -> str:
    result = gate_svc.create_gate(CreateGateRequest(
        job_id=job, gate_phase=phase, stage_index=stage,
        source_artifact_checksum="sha256:chk",
        source_artifact_refs=("ref",),
    ))
    assert result.status == "created"
    return result.gate_id


# ── analysis_review gate ─────────────────────────────────────────────


def test_analysis_gate_exposes_accept_and_reanalysis(tmp_path: Path) -> None:
    """Analysis_review gate exposes accept (continue), reanalysis, and
    source profile override."""
    gate_repo, gate_svc, conn = _setup(tmp_path)

    gate_id = _create_gate(gate_svc, "analysis_review")

    actions = gate_svc.get_available_actions(gate_id)

    assert len(actions) == 3

    actions_by_label = {a.label: a for a in actions}

    assert "Accept" in actions_by_label
    assert actions_by_label["Accept"].action == "continue"
    assert not actions_by_label["Accept"].blocked

    assert "Request Reanalysis" in actions_by_label
    assert actions_by_label["Request Reanalysis"].action == "reanalyze"

    assert "Override Source Profile" in actions_by_label
    assert actions_by_label["Override Source Profile"].action == "override_source_profile"


# ── planning_review gate ─────────────────────────────────────────────


def test_planning_gate_exposes_accept_and_revision(tmp_path: Path) -> None:
    """Planning_review gate exposes accept (continue) and revision."""
    gate_repo, gate_svc, conn = _setup(tmp_path)

    gate_id = _create_gate(gate_svc, "planning_review")

    actions = gate_svc.get_available_actions(gate_id)

    assert len(actions) == 2

    actions_by_label = {a.label: a for a in actions}

    assert "Accept" in actions_by_label
    assert actions_by_label["Accept"].action == "continue"

    assert "Request Revision" in actions_by_label
    assert actions_by_label["Request Revision"].action == "revise"


# ── approval_review gate ─────────────────────────────────────────────


def test_approval_gate_exposes_approve_and_reject(tmp_path: Path) -> None:
    """Approval_review gate exposes approve and reject."""
    gate_repo, gate_svc, conn = _setup(tmp_path)

    gate_id = _create_gate(gate_svc, "approval_review")

    actions = gate_svc.get_available_actions(gate_id)

    assert len(actions) == 2

    actions_by_label = {a.label: a for a in actions}

    assert "Approve" in actions_by_label
    assert actions_by_label["Approve"].action == "approve"

    assert "Reject" in actions_by_label
    assert actions_by_label["Reject"].action == "reject"


# ── repair_review gate ────────────────────────────────────────────────


def test_repair_gate_exposes_accept_reanalyze_revise_reject(tmp_path: Path) -> None:
    """Repair_review gate exposes accept, reanalyze, revise, and reject."""
    gate_repo, gate_svc, conn = _setup(tmp_path)

    gate_id = _create_gate(gate_svc, "repair_review")

    actions = gate_svc.get_available_actions(gate_id)

    assert len(actions) == 4

    actions_by_action = {a.action: a for a in actions}

    assert "continue" in actions_by_action
    assert actions_by_action["continue"].label == "Accept"

    assert "reanalyze" in actions_by_action
    assert actions_by_action["reanalyze"].label == "Request Reanalysis"

    assert "revise" in actions_by_action
    assert actions_by_action["revise"].label == "Request Revision"

    assert "reject" in actions_by_action


# ── stage_completion_review gate ──────────────────────────────────────


def test_stage_completion_gate_exposes_continue(tmp_path: Path) -> None:
    """Stage_completion_review gate exposes only continue."""
    gate_repo, gate_svc, conn = _setup(tmp_path)

    gate_id = _create_gate(gate_svc, "stage_completion_review")

    actions = gate_svc.get_available_actions(gate_id)

    assert len(actions) == 1
    assert actions[0].action == "continue"
    assert actions[0].label == "Accept"


# ── resolved gate returns empty ───────────────────────────────────────


def test_resolved_gate_returns_empty_actions(tmp_path: Path) -> None:
    """A resolved gate has no available actions."""
    gate_repo, gate_svc, conn = _setup(tmp_path)

    gate_id = _create_gate(gate_svc, "analysis_review")

    # Resolve the gate using V2PhaseGateService
    from migration_factory.control_tower.application.v2_phase_gate_service import (
        ResolveGateRequest,
    )
    import json
    gate = gate_repo.get(gate_id)
    assert gate is not None
    refs = json.loads(gate.source_artifact_refs_json)
    from migration_factory.control_tower.domain.gate_checksum import gate_checksum
    chk = gate_checksum(
        gate_id=gate.gate_id, job_id=gate.job_id,
        gate_phase=gate.gate_phase, stage_index=gate.stage_index,
        source_artifact_checksum=gate.source_artifact_checksum,
        source_artifact_refs=refs,
    )
    gate_svc.resolve_gate(ResolveGateRequest(
        gate_id=gate_id, job_id="job-abc",
        gate_decision="continue",
        expected_gate_checksum=chk,
        resolved_by="user-1",
    ))

    actions = gate_svc.get_available_actions(gate_id)
    assert actions == []


# ── nonexistent gate returns empty ────────────────────────────────────


def test_nonexistent_gate_returns_empty_actions(tmp_path: Path) -> None:
    """A nonexistent gate has no available actions."""
    gate_repo, gate_svc, conn = _setup(tmp_path)

    actions = gate_svc.get_available_actions("nonexistent")
    assert actions == []


# ── blocked actions ───────────────────────────────────────────────────


def test_blocked_action_marked_as_blocked(tmp_path: Path) -> None:
    """Actions can be marked as blocked via blocked_actions set."""
    gate_repo, gate_svc, conn = _setup(tmp_path)

    gate_id = _create_gate(gate_svc, "analysis_review")

    actions = gate_svc.get_available_actions(
        gate_id,
        blocked_actions={"continue"},
    )

    assert len(actions) == 3
    continue_action = [a for a in actions if a.action == "continue"][0]
    assert continue_action.blocked is True
    assert "blocked by an open or running command" in continue_action.block_reason

    reanalyze_action = [a for a in actions if a.action == "reanalyze"][0]
    assert reanalyze_action.blocked is False


# ── AvailableAction dataclass ─────────────────────────────────────────


def test_available_action_dataclass(tmp_path: Path) -> None:
    """AvailableAction provides structured action metadata."""
    action = AvailableAction(
        action="continue",
        label="Accept",
        description="Accept the current state",
    )
    assert action.action == "continue"
    assert action.label == "Accept"
    assert action.description == "Accept the current state"
    assert action.blocked is False
    assert action.block_reason == ""
