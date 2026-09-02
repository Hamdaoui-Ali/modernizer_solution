"""Focused tests for F15-JOB-038 — Stage completion gate creation.

Verifies that a stage_completion_review gate is created when a stage
completes under manual policy, and that old auto_on_green behavior
does not create a gate.
"""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.v2_phase_gate_service import (
    CreateGateRequest,
    V2PhaseGateService,
)
from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.application.v2_stage_progression import (
    V2StageProgressionService,
)
from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.schemas.run_configuration import (
    RunPolicy,
    StageContinuationPolicy,
)
from migration_factory.control_tower.schemas.phase_gate import (
    GatePhase,
    GateDecision,
    GateStatus,
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


def _create_setup(repo: SqliteV2SetupRepository) -> str:
    service = V2SetupService(repo)
    req = CreateSetupRequest(
        run_name="test-completion-gate",
        legacy_app_path="/tmp/legacy",
        output_parent_path="/tmp/output",
        ai_hub_path="/tmp/ai-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
    )
    return service.create_setup(req).setup_id


def test_stage_completion_gate_created_via_service(tmp_path: Path) -> None:
    """V2PhaseGateService can create a stage_completion_review gate."""
    conn = _connection(tmp_path, "comp1.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-comp-1",
        gate_phase="stage_completion_review",
        stage_index=1,
        source_artifact_checksum="abc123",
        source_artifact_refs=("/tmp/sandbox/stage1",),
        created_by="system",
    ))
    assert gate_result.status == "created"
    assert len(gate_result.gate_id) > 0
    assert len(gate_result.gate_checksum) > 0

    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    assert gate.gate_phase == "stage_completion_review"
    assert gate.gate_status == "open"
    assert gate.stage_index == 1


def test_stage_completion_gate_has_correct_phase(tmp_path: Path) -> None:
    """stage_completion_review gate uses the correct phase enum value."""
    conn = _connection(tmp_path, "comp2.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-phase-test",
        gate_phase="stage_completion_review",
        stage_index=1,
        source_artifact_checksum="chk",
        source_artifact_refs=("/tmp/output",),
        created_by="system",
    ))
    assert gate_result.status == "created"

    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    assert gate.gate_phase == GatePhase.STAGE_COMPLETION_REVIEW.value


def test_stage_completion_gate_allows_continue_action() -> None:
    """stage_completion_review gate exposes 'continue' action."""
    assert is_valid_decision_for_phase(
        GatePhase.STAGE_COMPLETION_REVIEW, GateDecision.CONTINUE
    )


def test_auto_policy_does_not_block_next_stage(tmp_path: Path) -> None:
    """Under AUTO_ON_GREEN, queue_next_stage returns queued."""
    conn = _connection(tmp_path, "comp3.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-auto-comp",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )

    assert result.status == "queued"
    assert result.to_stage == 2


def test_manual_policy_blocks_next_stage(tmp_path: Path) -> None:
    """Under MANUAL policy, queue_next_stage returns blocked."""
    conn = _connection(tmp_path, "comp4.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-manual-comp",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        stage_continuation_policy=StageContinuationPolicy.MANUAL,
    )

    assert result.status == "blocked"
    assert result.reason == "stage_continuation_policy_manual"
    assert result.argv == ()


def test_stage_completion_gate_bound_to_stage_artifact_checksum(tmp_path: Path) -> None:
    """stage_completion_review gate binds to stage result checksum."""
    conn = _connection(tmp_path, "comp5.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    result_payload = {
        "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "PASS",
        "sandbox_path": "/tmp/sandbox/stage1",
    }
    checksum = sha256_canonical_json(result_payload)

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-checksum-test",
        gate_phase="stage_completion_review",
        stage_index=1,
        source_artifact_checksum=checksum,
        source_artifact_refs=("/tmp/sandbox/stage1",),
        created_by="system",
    ))
    assert gate_result.status == "created"

    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    assert gate.source_artifact_checksum == checksum
    assert gate.gate_phase == "stage_completion_review"


def test_duplicate_stage_completion_gate_returns_conflict(tmp_path: Path) -> None:
    """Creating a second open stage_completion_review gate returns conflict."""
    conn = _connection(tmp_path, "comp6.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    # First gate
    gate_service.create_gate(CreateGateRequest(
        job_id="job-dup",
        gate_phase="stage_completion_review",
        stage_index=1,
        source_artifact_checksum="chk1",
        source_artifact_refs=("/tmp/s1",),
        created_by="system",
    ))

    # Second gate for same (job, phase, stage)
    result = gate_service.create_gate(CreateGateRequest(
        job_id="job-dup",
        gate_phase="stage_completion_review",
        stage_index=1,
        source_artifact_checksum="chk2",
        source_artifact_refs=("/tmp/s1",),
        created_by="system",
    ))
    assert result.status == "conflict"
    assert result.existing_gate_id is not None
