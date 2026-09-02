"""Focused tests for F15 stage continuation policy in V2 progression."""

import sqlite3
from pathlib import Path

from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.application.v2_stage_progression import (
    V2StageProgressionService,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.schemas.run_configuration import (
    StageContinuationPolicy,
)


def test_auto_on_green_policy_queues_next_stage_command(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "auto.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-auto",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        stage_continuation_policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )

    assert result.status == "queued"
    assert command_repo.list_by_job_and_stage("job-auto", 2)


def test_manual_policy_blocks_next_stage_command(tmp_path: Path) -> None:
    conn = _connection(tmp_path, "manual.sqlite3")
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    service = V2StageProgressionService(setup_repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-manual",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        stage_continuation_policy=StageContinuationPolicy.MANUAL,
    )

    assert result.status == "blocked"
    assert result.reason == "stage_continuation_policy_manual"
    assert result.argv == ()
    assert command_repo.list_by_job_and_stage("job-manual", 2) == ()


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
        run_name="test-progression-policy",
        legacy_app_path="/tmp/legacy",
        output_parent_path="/tmp/output",
        ai_hub_path="/tmp/ai-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
    )
    return service.create_setup(req).setup_id
