"""Focused tests for F15-JOB-037 — Resolve next stage from persisted output.

Verifies that V2StageProgressionService can resolve prior-stage output
from persisted command records instead of requiring frontend-supplied
sandbox_path.
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


def _create_setup(
    repo: SqliteV2SetupRepository,
    *,
    java11_home: str = "/usr/lib/jvm/java-11",
    java17_home: str = "/usr/lib/jvm/java-17",
    java21_home: str = "/usr/lib/jvm/java-21",
) -> str:
    service = V2SetupService(repo)
    req = CreateSetupRequest(
        run_name="test-output-resolver",
        legacy_app_path="/tmp/legacy",
        output_parent_path="/tmp/output",
        ai_hub_path="/tmp/ai-hub",
        java11_home=java11_home,
        java17_home=java17_home,
        java21_home=java21_home,
        maven_cmd="/usr/bin/mvn",
    )
    return service.create_setup(req).setup_id


def _seed_command(
    command_repo: SqliteV2CommandRepository,
    job_id: str,
    stage_index: int,
    sandbox_path: str,
) -> str:
    """Seed a completed command record with result_json containing sandbox_path."""
    command_id = uuid4().hex
    now = utc_now_text()
    result = {
        "sandbox_path": sandbox_path,
        "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "PASS",
        "orchestration_status": "PASS",
        "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
    }
    record = V2StageCommandRecord(
        command_id=command_id,
        job_id=job_id,
        stage_index=stage_index,
        manifest_checksum="test",
        argv_json=json.dumps(["python", "-m", "test"], separators=(",", ":")),
        env_json=json.dumps({"JAVA_HOME": "/usr/lib/jvm/java-17"}, separators=(",", ":")),
        status="completed",
        created_at=now,
        updated_at=now,
        result_json=json.dumps(result, separators=(",", ":")),
    )
    command_repo.save(record)
    return command_id


def test_resolve_prior_stage_output_finds_sandbox_path(tmp_path: Path) -> None:
    """resolve_prior_stage_output returns sandbox_path from command result_json."""
    conn = _connection(tmp_path, "resolve1.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    # Seed a completed Stage 1 command
    _seed_command(command_repo, "job-resolve-1", 1, "/tmp/sandbox/stage1-output")

    service = V2StageProgressionService(setup_repo, command_repo)
    result_path = service.resolve_prior_stage_output("job-resolve-1", 1)

    assert result_path == "/tmp/sandbox/stage1-output"


def test_resolve_prior_stage_output_returns_none_when_no_commands(tmp_path: Path) -> None:
    """resolve_prior_stage_output returns None when no commands exist for the stage."""
    conn = _connection(tmp_path, "resolve2.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result_path = service.resolve_prior_stage_output("job-no-commands", 1)

    assert result_path is None


def test_resolve_prior_stage_output_returns_none_when_no_result_json(tmp_path: Path) -> None:
    """resolve_prior_stage_output returns None when command has no result_json."""
    conn = _connection(tmp_path, "resolve3.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    now = utc_now_text()
    record = V2StageCommandRecord(
        command_id=uuid4().hex,
        job_id="job-no-result",
        stage_index=1,
        manifest_checksum="test",
        argv_json=json.dumps(["python", "-m", "test"], separators=(",", ":")),
        env_json=json.dumps({}, separators=(",", ":")),
        status="manifest_ready",
        created_at=now,
        updated_at=now,
        result_json=None,
    )
    command_repo.save(record)

    service = V2StageProgressionService(setup_repo, command_repo)
    result_path = service.resolve_prior_stage_output("job-no-result", 1)
    assert result_path is None


def test_resolve_prior_stage_output_finds_from_artifact_refs(tmp_path: Path) -> None:
    """resolve_prior_stage_output finds sandbox_path from artifact_refs sub-dict."""
    conn = _connection(tmp_path, "resolve4.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    command_id = uuid4().hex
    now = utc_now_text()
    result = {
        "artifact_refs": {
            "sandbox": "/tmp/sandbox/stage1-from-artifact-refs",
        },
        "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "PASS",
        "orchestration_status": "PASS",
    }
    record = V2StageCommandRecord(
        command_id=command_id,
        job_id="job-artifact-refs",
        stage_index=1,
        manifest_checksum="test",
        argv_json=json.dumps(["python", "-m", "test"], separators=(",", ":")),
        env_json=json.dumps({}, separators=(",", ":")),
        status="completed",
        created_at=now,
        updated_at=now,
        result_json=json.dumps(result, separators=(",", ":")),
    )
    command_repo.save(record)

    service = V2StageProgressionService(setup_repo, command_repo)
    result_path = service.resolve_prior_stage_output("job-artifact-refs", 1)
    assert result_path == "/tmp/sandbox/stage1-from-artifact-refs"


def test_queue_next_stage_from_persisted_works(tmp_path: Path) -> None:
    """queue_next_stage_from_persisted resolves output and queues next stage."""
    conn = _connection(tmp_path, "resolve5.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    _seed_command(command_repo, "job-persisted-1", 1, "/tmp/sandbox/stage1-out")

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage_from_persisted(
        job_id="job-persisted-1",
        setup_id=setup_id,
        current_stage=1,
    )

    assert result.status == "queued"
    assert result.to_stage == 2
    assert result.sandbox_path == "/tmp/sandbox/stage1-out"


def test_queue_next_stage_from_persisted_blocks_when_no_output(tmp_path: Path) -> None:
    """queue_next_stage_from_persisted returns blocked when output unresolvable."""
    conn = _connection(tmp_path, "resolve6.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage_from_persisted(
        job_id="job-persisted-2",
        setup_id=setup_id,
        current_stage=1,
    )

    assert result.status == "blocked"
    assert result.reason == "prior_stage_output_not_resolved"


def test_queue_next_stage_from_persisted_respects_manual_policy(tmp_path: Path) -> None:
    """queue_next_stage_from_persisted respects MANUAL policy."""
    conn = _connection(tmp_path, "resolve7.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    _seed_command(command_repo, "job-manual-persist", 1, "/tmp/sandbox/stage1")

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage_from_persisted(
        job_id="job-manual-persist",
        setup_id=setup_id,
        current_stage=1,
        stage_continuation_policy=StageContinuationPolicy.MANUAL,
    )

    assert result.status == "blocked"
    assert result.reason == "stage_continuation_policy_manual"
    assert result.sandbox_path == "/tmp/sandbox/stage1"
    assert result.argv == ()


def test_queue_next_stage_persists_java17_env_for_stage2(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "resolve8.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    _seed_command(command_repo, "job-stage2", 1, "/tmp/sandbox/stage1")

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage_from_persisted(
        job_id="job-stage2",
        setup_id=setup_id,
        current_stage=1,
    )

    assert result.status == "queued"
    assert result.to_stage == 2
    record = command_repo.get(result.command_id or "")
    assert record is not None
    env = json.loads(record.env_json)
    assert env.get("JAVA_HOME") == "/usr/lib/jvm/java-17"
    assert env.get("PATH_PREPEND") == str(Path(env["JAVA17_HOME"]) / "bin")
    assert env.get("JAVA11_HOME") == "/usr/lib/jvm/java-11"
    assert env.get("JAVA21_HOME") == "/usr/lib/jvm/java-21"


def test_queue_next_stage_fails_closed_when_stage2_jdk_missing(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "resolve9.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo, java17_home="")

    _seed_command(command_repo, "job-stage2-missing-jdk", 1, "/tmp/sandbox/stage1")

    service = V2StageProgressionService(setup_repo, command_repo)
    with pytest.raises(ValueError, match="JAVA17_HOME"):
        service.queue_next_stage_from_persisted(
            job_id="job-stage2-missing-jdk",
            setup_id=setup_id,
            current_stage=1,
        )
