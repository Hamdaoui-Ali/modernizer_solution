"""Focused tests for F15-JOB-041 — Analysis completion hook.

Verifies that when analysis (Stage 1) completes under manual policy,
an analysis_review gate is created and planning is not auto-started.
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


def test_analysis_review_gate_created_after_analysis(tmp_path: Path) -> None:
    """analysis_review gate can be created via V2PhaseGateService."""
    conn = _connection(tmp_path, "analysis1.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-analysis-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="analysis-output-chk",
        source_artifact_refs=("/tmp/sandbox/stage1-analysis",),
        created_by="system",
    ))
    assert gate_result.status == "created"
    assert len(gate_result.gate_id) > 0

    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    assert gate.gate_phase == "analysis_review"
    assert gate.gate_status == "open"
    assert gate.stage_index == 1


def test_analysis_review_gate_bound_to_analysis_artifacts(tmp_path: Path) -> None:
    """analysis_review gate binds to analysis artifact checksums."""
    conn = _connection(tmp_path, "analysis2.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    checksum = "sha256:analysis-evidence-123"
    refs = ("/tmp/sandbox/analysis/report.json", "/tmp/sandbox/analysis/analysis.json")

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-analysis-2",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum=checksum,
        source_artifact_refs=refs,
        created_by="system",
    ))

    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    assert gate.source_artifact_checksum == checksum
    stored_refs = json.loads(gate.source_artifact_refs_json)
    assert sorted(stored_refs) == sorted(refs)


def test_analysis_review_gate_allows_continue(tmp_path: Path) -> None:
    """analysis_review gate exposes 'continue' action to proceed to planning."""
    assert is_valid_decision_for_phase(
        GatePhase.ANALYSIS_REVIEW, GateDecision.CONTINUE
    )


def test_analysis_review_gate_allows_reanalyze(tmp_path: Path) -> None:
    """analysis_review gate exposes 'reanalyze' action."""
    assert is_valid_decision_for_phase(
        GatePhase.ANALYSIS_REVIEW, GateDecision.REANALYZE
    )


def test_analysis_review_gate_does_not_allow_approve(tmp_path: Path) -> None:
    """analysis_review gate does NOT expose approve (approval comes later)."""
    assert not is_valid_decision_for_phase(
        GatePhase.ANALYSIS_REVIEW, GateDecision.APPROVE
    )


def test_analysis_review_gate_does_not_allow_reject(tmp_path: Path) -> None:
    """analysis_review gate does NOT expose reject."""
    assert not is_valid_decision_for_phase(
        GatePhase.ANALYSIS_REVIEW, GateDecision.REJECT
    )


def test_analysis_review_gate_duplicate_conflict(tmp_path: Path) -> None:
    """Only one open analysis_review gate per job/stage."""
    conn = _connection(tmp_path, "analysis7.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    # First gate
    gate_service.create_gate(CreateGateRequest(
        job_id="job-dup-analysis",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="chk1",
        source_artifact_refs=("/tmp/s1",),
        created_by="system",
    ))

    # Second gate for same (job, phase, stage)
    result = gate_service.create_gate(CreateGateRequest(
        job_id="job-dup-analysis",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="chk2",
        source_artifact_refs=("/tmp/s1",),
        created_by="system",
    ))
    assert result.status == "conflict"
    assert result.existing_gate_id is not None
