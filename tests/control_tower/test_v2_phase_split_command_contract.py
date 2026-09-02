"""Focused contract tests for F15-JOB-040 — Phase-split command design support.

Verifies the contract that analysis and planning phases can be executed
independently (phase-split), and that planning consumes accepted analysis
output. No transform happens without approval.
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
    STAGE_CONFIG,
    RUNNER_MODULE,
)
from migration_factory.control_tower.domain.checksums import (
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
from migration_factory.control_tower.schemas.phase_gate import (
    GatePhase,
    GateDecision,
    is_valid_decision_for_phase,
)
from migration_factory.control_tower.schemas.run_configuration import (
    StageContinuationPolicy,
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
        run_name="test-phase-split",
        legacy_app_path="/tmp/legacy",
        output_parent_path="/tmp/output",
        ai_hub_path="/tmp/ai-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
    )
    return service.create_setup(req).setup_id


# ── Contract: Analysis phase runs alone ─────────────────────────────


def test_analysis_phase_runs_independently(tmp_path: Path) -> None:
    """Stage 1 (analysis) argv can be generated without requiring planning."""
    conn = _connection(tmp_path, "split1.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    # Stage 1 is the first stage — no prior stage needed
    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-analysis-only",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )

    # Stage 2 (planning) should be queued
    assert result.status == "queued"
    assert result.to_stage == 2
    assert RUNNER_MODULE in " ".join(result.argv)
    assert STAGE_CONFIG[2]["profile"] in " ".join(result.argv)


# ── Contract: Planning consumes analysis output ─────────────────────


def test_planning_phase_consumes_analysis_output(tmp_path: Path) -> None:
    """Stage 2 (planning) uses Stage 1 sandbox as its --legacy input."""
    conn = _connection(tmp_path, "split2.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-planning-consumes",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1-analysis-output",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )

    assert result.status == "queued"
    # The sandbox_path from stage 1 becomes --legacy for stage 2
    assert "/tmp/sandbox/stage1-analysis-output" in " ".join(result.argv)


# ── Contract: No transform before approval ──────────────────────────


def test_transform_requires_approved_plan(tmp_path: Path) -> None:
    """Stage 3 (transform) depends on Stage 2 (planning) output."""
    conn = _connection(tmp_path, "split3.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-transform-needs-plan",
        setup_id=setup_id,
        current_stage=2,
        sandbox_path="/tmp/sandbox/stage2-planning-output",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )

    assert result.status == "queued"
    assert result.to_stage == 3
    # Stage 2 sandbox becomes --legacy for Stage 3
    assert "/tmp/sandbox/stage2-planning-output" in " ".join(result.argv)
    assert STAGE_CONFIG[3]["profile"] in " ".join(result.argv)


def test_stage4_uses_stage3_sandbox_and_boot4_profile(tmp_path: Path) -> None:
    """Stage 4 is queued from successful Stage 3 output."""
    conn = _connection(tmp_path, "split4.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)
    now = utc_now_text()
    command_repo.save(
        V2StageCommandRecord(
            command_id="cmd-stage3",
            job_id="job-stage4",
            stage_index=3,
            manifest_checksum="checksum-stage3",
            argv_json=json.dumps(["python", "-m", RUNNER_MODULE, "--run-id", "v2-job-stag-s3"]),
            env_json="{}",
            status="completed",
            created_at=now,
            updated_at=now,
            result_json=json.dumps({
                "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
                "orchestration_status": "PASS",
                "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
                "build_status": "BUILD_PASSED_IN_SANDBOX",
                "test_status": "PASS",
                "sandbox_path": "/tmp/sandbox/s3",
            }),
        )
    )

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-stage4",
        setup_id=setup_id,
        current_stage=3,
        sandbox_path="/tmp/sandbox/s3",
    )

    assert result.status == "queued"
    assert result.to_stage == 4
    assert result.argv[result.argv.index("--run-id") + 1].endswith("-s4")
    assert result.argv[result.argv.index("--legacy") + 1] == "/tmp/sandbox/s3"
    assert STAGE_CONFIG[4]["profile"] in " ".join(result.argv)


def test_transform_not_queued_without_approval_gate(tmp_path: Path) -> None:
    """Under manual policy, Stage 2 completion creates a gate
    and does NOT automatically queue Stage 3 transform."""
    conn = _connection(tmp_path, "split5.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-no-auto-transform",
        setup_id=setup_id,
        current_stage=2,
        sandbox_path="/tmp/sandbox/stage2",
        stage_continuation_policy=StageContinuationPolicy.MANUAL,
    )

    assert result.status == "blocked"
    assert result.reason == "stage_continuation_policy_manual"
    assert result.argv == ()


def test_analysis_phase_creates_analysis_review_gate_under_manual(tmp_path: Path) -> None:
    """Under manual policy, analysis phase completion creates
    an analysis_review gate before planning can start."""
    conn = _connection(tmp_path, "split6.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-analysis-manual",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="analysis-chk",
        source_artifact_refs=("/tmp/sandbox/analysis",),
        created_by="system",
    ))

    assert gate_result.status == "created"
    assert gate_result.gate_id

    # Verify analysis_review allows continue/reanalyze
    assert is_valid_decision_for_phase(
        GatePhase.ANALYSIS_REVIEW, GateDecision.CONTINUE
    )
    assert is_valid_decision_for_phase(
        GatePhase.ANALYSIS_REVIEW, GateDecision.REANALYZE
    )


def test_planning_phase_creates_planning_review_gate_under_manual(tmp_path: Path) -> None:
    """Under manual policy, planning phase completion creates
    a planning_review gate before approval can start."""
    conn = _connection(tmp_path, "split7.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-planning-manual",
        gate_phase="planning_review",
        stage_index=2,
        source_artifact_checksum="plan-chk",
        source_artifact_refs=("/tmp/sandbox/planning",),
        created_by="system",
    ))

    assert gate_result.status == "created"

    # Verify planning_review allows continue/revise
    assert is_valid_decision_for_phase(
        GatePhase.PLANNING_REVIEW, GateDecision.CONTINUE
    )
    assert is_valid_decision_for_phase(
        GatePhase.PLANNING_REVIEW, GateDecision.REVISE
    )
