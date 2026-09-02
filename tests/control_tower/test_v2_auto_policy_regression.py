"""Focused regression tests for F15-JOB-049 — Legacy auto behavior.

Protects that old auto_on_green behavior remains unchanged while F15
manual policy is opt-in. These tests assert that:
- Stage 1 auto-queues Stage 2 under auto_on_green
- Stage 2 auto-queues Stage 3 under auto_on_green
- Manual mode blocks independently (no cross-contamination)
"""

import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.application.v2_stage_progression import (
    V2StageProgressionService,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
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
        run_name="test-auto-regression",
        legacy_app_path="/tmp/legacy",
        output_parent_path="/tmp/output",
        ai_hub_path="/tmp/ai-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
    )
    return service.create_setup(req).setup_id


def _save_successful_stage3_command(command_repo: SqliteV2CommandRepository, *, job_id: str) -> None:
    now = utc_now_text()
    command_repo.save(
        V2StageCommandRecord(
            command_id="cmd-stage3",
            job_id=job_id,
            stage_index=3,
            manifest_checksum="checksum-stage3",
            argv_json=json.dumps(["python", "-m", "migration_factory.orchestrator.runner", "--run-id", f"v2-{job_id[:8]}-s3"]),
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


def test_stage1_auto_queues_stage2(tmp_path: Path) -> None:
    """Stage 1 auto-queues Stage 2 under AUTO_ON_GREEN (default)."""
    conn = _connection(tmp_path, "reg1.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-s1-to-s2",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )

    assert result.status == "queued"
    assert result.to_stage == 2
    assert len(result.argv) > 0
    assert result.continuation_id

    # Verify command was persisted
    commands = command_repo.list_by_job_and_stage("job-s1-to-s2", 2)
    assert len(commands) >= 1


def test_stage2_auto_queues_stage3(tmp_path: Path) -> None:
    """Stage 2 auto-queues Stage 3 under AUTO_ON_GREEN (default)."""
    conn = _connection(tmp_path, "reg2.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-s2-to-s3",
        setup_id=setup_id,
        current_stage=2,
        sandbox_path="/tmp/sandbox/stage2",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )

    assert result.status == "queued"
    assert result.to_stage == 3
    assert len(result.argv) > 0

    commands = command_repo.list_by_job_and_stage("job-s2-to-s3", 3)
    assert len(commands) >= 1


def test_auto_on_green_is_default_policy(tmp_path: Path) -> None:
    """queue_next_stage defaults to AUTO_ON_GREEN (backward compatible)."""
    conn = _connection(tmp_path, "reg3.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-default-policy",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        # No stage_continuation_policy specified — defaults to AUTO_ON_GREEN
    )

    assert result.status == "queued"
    assert result.to_stage == 2


def test_auto_and_manual_jobs_are_independent(tmp_path: Path) -> None:
    """Auto policy for one job doesn't affect manual policy for another."""
    conn = _connection(tmp_path, "reg4.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)

    # Auto job
    auto_result = service.queue_next_stage(
        job_id="job-reg-auto",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/s1",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )

    # Manual job (different job_id)
    manual_result = service.queue_next_stage(
        job_id="job-reg-manual",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/s1",
        stage_continuation_policy=StageContinuationPolicy.MANUAL,
    )

    assert auto_result.status == "queued"
    assert manual_result.status == "blocked"

    # Auto job has persisted command, manual does not
    auto_commands = command_repo.list_by_job_and_stage("job-reg-auto", 2)
    manual_commands = command_repo.list_by_job_and_stage("job-reg-manual", 2)
    assert len(auto_commands) >= 1
    assert len(manual_commands) == 0


def test_auto_stage2_uses_stage1_sandbox(tmp_path: Path) -> None:
    """Stage 2 command uses Stage 1 sandbox_path as --legacy input."""
    conn = _connection(tmp_path, "reg5.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-sandbox-chain",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/regression-stage1-output",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )

    assert "/tmp/sandbox/regression-stage1-output" in " ".join(result.argv)


def test_auto_pipeline_completes_through_stage4(tmp_path: Path) -> None:
    """Full auto pipeline stages 1->2->3->4 without manual intervention."""
    conn = _connection(tmp_path, "reg6.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)

    # Stage 1 -> Stage 2
    r1 = service.queue_next_stage(
        job_id="job-full-pipe",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/s1",
    )
    assert r1.status == "queued"
    assert r1.to_stage == 2

    # Stage 2 -> Stage 3
    r2 = service.queue_next_stage(
        job_id="job-full-pipe",
        setup_id=setup_id,
        current_stage=2,
        sandbox_path="/tmp/sandbox/s2",
    )
    assert r2.status == "queued"
    assert r2.to_stage == 3

    _save_successful_stage3_command(command_repo, job_id="job-full-pipe")
    r3 = service.queue_next_stage(
        job_id="job-full-pipe",
        setup_id=setup_id,
        current_stage=3,
        sandbox_path="/tmp/sandbox/s3",
    )
    assert r3.status == "queued"
    assert r3.to_stage == 4
    assert r3.argv[r3.argv.index("--run-id") + 1].endswith("-s4")
    assert r3.argv[r3.argv.index("--legacy") + 1] == "/tmp/sandbox/s3"
