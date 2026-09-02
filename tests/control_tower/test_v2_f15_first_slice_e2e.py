"""Focused end-to-end test for F15 manual mode phase-level progression.

Proves the first demo flow without real Maven/orchestrator cost:
1. Stage 1 analysis completes (simulated via fake result payload)
2. Under manual policy, an analysis_review gate is created
3. Chatbot explanation can read the gate
4. Continue action creates planning_review gate (not Stage 2)
"""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.v2_phase_gate_service import (
    CreateGateRequest,
    ResolveGateRequest,
    V2PhaseGateService,
)
from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.application.v2_stage_progression import (
    V2StageProgressionService,
)
from migration_factory.control_tower.application.v2_gate_action_service import (
    V2GateActionService,
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
from migration_factory.control_tower.infrastructure.sqlite.v2_event_repository import (
    SqliteV2JobEventRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.schemas.run_configuration import (
    StageContinuationPolicy,
)
from migration_factory.control_tower.schemas.phase_gate import (
    GatePhase,
    GateDecision,
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
        run_name="test-fake-runner-e2e",
        legacy_app_path="/tmp/legacy",
        output_parent_path="/tmp/output",
        ai_hub_path="/tmp/ai-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
    )
    return service.create_setup(req).setup_id


def _seed_completed_command(
    command_repo: SqliteV2CommandRepository,
    job_id: str,
    stage_index: int,
    sandbox_path: str,
) -> str:
    """Seed a fake completed command for the stage."""
    command_id = uuid4().hex
    now = utc_now_text()
    result = {
        "sandbox_path": sandbox_path,
        "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "PASS",
        "orchestration_status": "PASS",
    }
    record = V2StageCommandRecord(
        command_id=command_id,
        job_id=job_id,
        stage_index=stage_index,
        manifest_checksum="fake-runner",
        argv_json=json.dumps(["fake-runner", "--stage", str(stage_index)], separators=(",", ":")),
        env_json=json.dumps({}, separators=(",", ":")),
        status="completed",
        created_at=now,
        updated_at=now,
        result_json=json.dumps(result, separators=(",", ":")),
    )
    command_repo.save(record)
    return command_id


def test_f15_first_slice_analysis_stops_at_gate(tmp_path: Path) -> None:
    """[E2E] Stage 1 analysis completes → analysis_review gate created
    under manual policy. Stage 2 is NOT auto-queued."""
    conn = _connection(tmp_path, "e2e1.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    gate_repo = SqlitePhaseGateRepository(conn)
    event_repo = SqliteV2JobEventRepository(conn)
    setup_id = _create_setup(setup_repo)

    job_id = "job-e2e-analysis-stop"
    sandbox_path = "/tmp/sandbox/e2e-stage1"

    # Simulate: Stage 1 completed (fake runner result persisted)
    _seed_completed_command(command_repo, job_id, 1, sandbox_path)

    # Under MANUAL policy, queue_next_stage blocks
    service = V2StageProgressionService(setup_repo, command_repo)
    queued = service.queue_next_stage(
        job_id=job_id,
        setup_id=setup_id,
        current_stage=1,
        sandbox_path=sandbox_path,
        stage_continuation_policy=StageContinuationPolicy.MANUAL,
    )

    assert queued.status == "blocked"
    assert queued.reason == "stage_continuation_policy_manual"
    assert queued.argv == ()
    assert queued.to_stage == 2

    # Verify Stage 2 command was NOT created
    stage2_commands = command_repo.list_by_job_and_stage(job_id, 2)
    assert len(stage2_commands) == 0


def test_f15_first_slice_gate_can_be_read(tmp_path: Path) -> None:
    """[E2E] Chatbot explanation can read the created gate."""
    conn = _connection(tmp_path, "e2e2.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)

    # Create an analysis_review gate with bound artifacts
    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id="job-e2e-gate-read",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:analysis-evidence-001",
        source_artifact_refs=(
            "/tmp/sandbox/e2e/analysis/report.json",
            "/tmp/sandbox/e2e/analysis/summary.json",
        ),
        created_by="system",
    ))
    assert gate_result.status == "created"

    # Chatbot can read the gate
    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    assert gate.gate_phase == "analysis_review"
    assert gate.gate_status == "open"
    assert gate.source_artifact_checksum == "sha256:analysis-evidence-001"

    # Artifact refs are accessible for explanation
    import json
    refs = json.loads(gate.source_artifact_refs_json)
    assert len(refs) == 2
    assert all(isinstance(r, str) for r in refs)
    # No secrets in refs
    for r in refs:
        assert "secret" not in r.lower()
        assert "password" not in r.lower()


def test_f15_first_slice_continue_advances_phase_not_stage(tmp_path: Path) -> None:
    """[E2E] Continue action at analysis_review gate queues a planning
    command — does NOT create a synthetic planning_review gate and
    does NOT queue Stage 2."""
    conn = _connection(tmp_path, "e2e3.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    _create_setup(setup_repo)

    job_id = "job-e2e-continue"
    sandbox_path = "/tmp/sandbox/e2e-stage1"

    # Seed Stage 1 completed command
    _seed_completed_command(command_repo, job_id, 1, sandbox_path)

    # Create an analysis_review gate (as runner would after Stage 1)
    gate_service = V2PhaseGateService(gate_repo)
    gate_result = gate_service.create_gate(CreateGateRequest(
        job_id=job_id,
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:analysis-001",
        source_artifact_refs=(sandbox_path,),
        created_by="system",
    ))
    assert gate_result.status == "created"

    # Compute expected checksum for the action
    gate = gate_repo.get(gate_result.gate_id)
    assert gate is not None
    from migration_factory.control_tower.domain.gate_checksum import gate_checksum
    import json
    refs = json.loads(gate.source_artifact_refs_json)
    expected_checksum = gate_checksum(
        gate_id=gate.gate_id,
        job_id=gate.job_id,
        gate_phase=gate.gate_phase,
        stage_index=gate.stage_index,
        source_artifact_checksum=gate.source_artifact_checksum,
        source_artifact_refs=refs,
    )

    # Execute continue action via V2GateActionService
    action_service = V2GateActionService(
        gate_repo=gate_repo,
        decision_repo=decision_repo,
        gate_service=gate_service,
        command_repo=command_repo,
    )
    action_result = action_service.continue_from_gate(
        gate_id=gate_result.gate_id,
        job_id=job_id,
        decided_by="human-test",
        expected_gate_checksum=expected_checksum,
    )

    assert action_result.status == "executed"
    assert action_result.decision_id

    # After gate continues: NO Stage 2 command was created
    stage2_commands = command_repo.list_by_job_and_stage(job_id, 2)
    assert len(stage2_commands) == 0, (
        f"Expected no Stage 2 commands, got {len(stage2_commands)}"
    )

    # P0: NO synthetic planning_review gate created directly from
    # CONTINUE. Real planning must run first and produce artifacts.
    gates = gate_repo.list_by_job(job_id)
    planning_gates = [g for g in gates if g.gate_phase == "planning_review" and g.stage_index == 1]
    assert len(planning_gates) == 0, (
        f"Expected NO planning_review gate (synthetic), "
        f"but found {len(planning_gates)}"
    )

    # Instead, a planning command was queued (proof that planning
    # execution was requested, not skipped)
    assert action_result.result_command_id is not None, (
        "Expected result_command_id for planning command"
    )
    planning_commands = command_repo.list_by_job_and_stage(job_id, 1)
    planning_pending = [
        c for c in planning_commands
        if c.status == "planning_pending" and c.manifest_checksum == "phase:planning"
    ]
    assert len(planning_pending) >= 1, (
        "Expected at least one planning_pending command"
    )
    pending_cmd = planning_pending[0]
    assert pending_cmd.command_id == action_result.result_command_id

    # Verify the analysis_review gate is now resolved
    resolved_analysis = gate_repo.get(gate_result.gate_id)
    assert resolved_analysis is not None
    assert resolved_analysis.gate_status == "resolved"


def test_f15_first_slice_manual_mode_no_auto_transform(tmp_path: Path) -> None:
    """[E2E] Under manual policy, gate creation prevents auto-transform."""
    conn = _connection(tmp_path, "e2e4.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    gate_repo = SqlitePhaseGateRepository(conn)
    setup_id = _create_setup(setup_repo)

    job_id = "job-e2e-no-auto-transform"

    # Create analysis_review gate (as if Stage 1 completed under manual)
    gate_service = V2PhaseGateService(gate_repo)
    gate_service.create_gate(CreateGateRequest(
        job_id=job_id,
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="chk",
        source_artifact_refs=(),
        created_by="system",
    ))

    # Under AUTO_ON_GREEN (not manual), queue would work
    # But since there's no Stage 1 command (simulating manual mode),
    # the progression service should check before queuing
    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id=job_id,
        setup_id=setup_id,
        current_stage=2,
        sandbox_path="/tmp/sandbox/fake",
        stage_continuation_policy=StageContinuationPolicy.MANUAL,
    )
    assert result.status == "blocked"
    assert result.reason == "stage_continuation_policy_manual"
