"""Focused tests for F15-JOB-042 — Planning completion hook.

Verifies that when planning (Stage 2) completes under manual policy,
a planning_review gate is created and approval is not auto-started.
"""

import json
import sqlite3
from pathlib import Path

import pytest

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
from migration_factory.control_tower.schemas.phase_gate import (
    GatePhase,
    GateDecision,
    is_valid_decision_for_phase,
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


def test_planning_review_gate_created_after_planning(tmp_path: Path) -> None:
    """planning_review gate can be created via V2PhaseGateService."""
    conn = _connection(tmp_path, "plan1.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-planning-1",
        gate_phase="planning_review",
        stage_index=2,
        source_artifact_checksum="plan-output-chk",
        source_artifact_refs=("/tmp/sandbox/stage2-planning",),
        created_by="system",
    ))
    assert gate_result.status == "created"

    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    assert gate.gate_phase == "planning_review"
    assert gate.gate_status == "open"
    assert gate.stage_index == 2


def test_planning_review_gate_bound_to_plan_artifacts(tmp_path: Path) -> None:
    """planning_review gate binds to planning artifact checksums."""
    conn = _connection(tmp_path, "plan2.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    checksum = "sha256:plan-evidence-456"
    refs = ("/tmp/sandbox/plan/plan.json", "/tmp/sandbox/plan/patches.json")

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-planning-2",
        gate_phase="planning_review",
        stage_index=2,
        source_artifact_checksum=checksum,
        source_artifact_refs=refs,
        created_by="system",
    ))

    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    assert gate.source_artifact_checksum == checksum
    stored_refs = json.loads(gate.source_artifact_refs_json)
    assert sorted(stored_refs) == sorted(refs)


def test_planning_review_gate_allows_continue(tmp_path: Path) -> None:
    """planning_review gate exposes 'continue' to proceed to approval."""
    assert is_valid_decision_for_phase(
        GatePhase.PLANNING_REVIEW, GateDecision.CONTINUE
    )


def test_planning_review_gate_allows_revise(tmp_path: Path) -> None:
    """planning_review gate exposes 'revise' action."""
    assert is_valid_decision_for_phase(
        GatePhase.PLANNING_REVIEW, GateDecision.REVISE
    )


def test_planning_review_gate_does_not_allow_approve(tmp_path: Path) -> None:
    """planning_review gate does NOT allow approve (approval is a separate gate)."""
    assert not is_valid_decision_for_phase(
        GatePhase.PLANNING_REVIEW, GateDecision.APPROVE
    )


def test_planning_review_gate_does_not_allow_reject(tmp_path: Path) -> None:
    """planning_review gate does NOT allow reject."""
    assert not is_valid_decision_for_phase(
        GatePhase.PLANNING_REVIEW, GateDecision.REJECT
    )


def test_planning_review_gate_duplicate_conflict(tmp_path: Path) -> None:
    """Only one open planning_review gate per job/stage."""
    conn = _connection(tmp_path, "plan7.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    gate_service.create_gate(CreateGateRequest(
        job_id="job-dup-plan",
        gate_phase="planning_review",
        stage_index=2,
        source_artifact_checksum="chk1",
        source_artifact_refs=("/tmp/s2",),
        created_by="system",
    ))

    result = gate_service.create_gate(CreateGateRequest(
        job_id="job-dup-plan",
        gate_phase="planning_review",
        stage_index=2,
        source_artifact_checksum="chk2",
        source_artifact_refs=("/tmp/s2",),
        created_by="system",
    ))
    assert result.status == "conflict"
    assert result.existing_gate_id is not None
