"""Focused tests for F15-JOB-045 — Stage skip blocker.

Verifies that stage chain validation prevents:
- Stage skipping: must progress one stage at a time
- Stage 2 cannot start without Stage 1 completed output
- Stage 3 cannot start without Stage 1 and 2 completed output
"""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

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
        run_name="test-skip-blocker",
        legacy_app_path="/tmp/legacy",
        output_parent_path="/tmp/output",
        ai_hub_path="/tmp/ai-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
    )
    return service.create_setup(req).setup_id


def _seed_command(
    command_repo: SqliteV2CommandRepository,
    job_id: str,
    stage_index: int,
    sandbox_path: str,
) -> str:
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
        manifest_checksum="test",
        argv_json=json.dumps(["python", "-m", "test"], separators=(",", ":")),
        env_json=json.dumps({}, separators=(",", ":")),
        status="completed",
        created_at=now,
        updated_at=now,
        result_json=json.dumps(result, separators=(",", ":")),
    )
    command_repo.save(record)
    return command_id


def test_skip_from_stage1_to_stage3_blocked(tmp_path: Path) -> None:
    """Jumping from Stage 1 to Stage 3 (skipping Stage 2) is blocked."""
    conn = _connection(tmp_path, "skip1.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    _create_setup(setup_repo)

    _seed_command(command_repo, "job-skip-s1s3", 1, "/tmp/sandbox/s1")

    service = V2StageProgressionService(setup_repo, command_repo)
    is_valid, reason = service.validate_stage_chain("job-skip-s1s3", 1, 3)
    assert not is_valid
    assert "skip" in reason.lower()


def test_stage3_cannot_start_without_stage1_and_stage2(tmp_path: Path) -> None:
    """Stage 3 cannot start if Stage 1 hasn't completed."""
    conn = _connection(tmp_path, "skip2.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    _create_setup(setup_repo)

    # Only Stage 2 has output, not Stage 1
    _seed_command(command_repo, "job-missing-s1", 2, "/tmp/sandbox/s2")

    service = V2StageProgressionService(setup_repo, command_repo)
    is_valid, reason = service.validate_stage_chain("job-missing-s1", 2, 3)
    assert not is_valid
    assert "Stage 1 has no completed output" in reason


def test_first_time_stage1_to_stage2_does_not_require_persisted_output(tmp_path: Path) -> None:
    """First-time Stage 1 -> Stage 2 is valid even without persisted output
    because Stage 1 output is currently being generated (not yet persisted).
    The skip blocker only checks stages BEFORE the current stage."""
    conn = _connection(tmp_path, "skip2b.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    is_valid, reason = service.validate_stage_chain("job-fresh-s1", 1, 2)

    # Stage 1 (current) is not checked for persisted output on first pass
    assert is_valid


def test_stage1_to_stage2_valid_when_stage1_complete(tmp_path: Path) -> None:
    """Stage 1 -> Stage 2 is valid when Stage 1 has completed output."""
    conn = _connection(tmp_path, "skip4.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    _create_setup(setup_repo)

    _seed_command(command_repo, "job-valid-s1", 1, "/tmp/sandbox/s1")

    service = V2StageProgressionService(setup_repo, command_repo)
    is_valid, reason = service.validate_stage_chain("job-valid-s1", 1, 2)
    assert is_valid
    assert reason == ""


def test_stage2_to_stage3_valid_when_both_stages_complete(tmp_path: Path) -> None:
    """Stage 2 -> Stage 3 is valid when Stage 1 has completed output."""
    conn = _connection(tmp_path, "skip5.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    _create_setup(setup_repo)

    _seed_command(command_repo, "job-full", 1, "/tmp/sandbox/s1")
    _seed_command(command_repo, "job-full", 2, "/tmp/sandbox/s2")

    service = V2StageProgressionService(setup_repo, command_repo)
    is_valid, reason = service.validate_stage_chain("job-full", 2, 3)
    assert is_valid
    assert reason == ""


def test_target_stage_must_be_exactly_next(tmp_path: Path) -> None:
    """Target must be exactly current + 1 (no skipping numbers)."""
    conn = _connection(tmp_path, "skip6.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    is_valid, reason = service.validate_stage_chain("job-backwards", 2, 1)
    assert not is_valid
    assert "skip" in reason.lower()


def test_current_stage_out_of_range_rejected(tmp_path: Path) -> None:
    """Current stage outside 1-3 is rejected."""
    conn = _connection(tmp_path, "skip7.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    is_valid, reason = service.validate_stage_chain("job-bad", 0, 1)
    assert not is_valid
    assert "out of range" in reason


def test_no_frontend_path_override() -> None:
    """validate_stage_chain does not accept sandbox_path, argv, or env."""
    import inspect
    sig = inspect.signature(
        V2StageProgressionService.validate_stage_chain
    )
    params = list(sig.parameters.keys())
    assert "job_id" in params
    assert "current_stage" in params
    assert "target_stage" in params
    assert "sandbox_path" not in params
    assert "argv" not in params
    assert "env" not in params
