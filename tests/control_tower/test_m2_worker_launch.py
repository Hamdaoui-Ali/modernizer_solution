"""Tests for M2-04 controlled diagnostic worker launch."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.commands import (
    CreateMigrationJobCommand,
    LaunchWorkerCommand,
    PrepareCommandWorkspaceCommand,
)
from migration_factory.control_tower.application.dto import WorkerLaunchResult
from migration_factory.control_tower.application.services import (
    CommandWorkspaceService,
    CreateMigrationJobService,
    DiagnosticJobService,
    WorkerLaunchService,
)
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.entities import CommandExecutionRecord
from migration_factory.control_tower.domain.errors import (
    InvalidJobStateTransitionError,
    NotFoundError,
    UnsupportedPlatformError,
    WorkspaceConflictError,
)
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.domain.manifests import CommandManifest, verify_manifest_checksum
from migration_factory.control_tower.infrastructure.worker_launcher import (
    UnsupportedPlatformWorkerLauncher,
)
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteControlTowerUnitOfWork,
)
from migration_factory.control_tower.schemas.run_configuration import RunPolicy
from tests.control_tower._helpers import (
    artifact_roots,
    canonical_json,
    make_migrated_connection,
    pipeline_definition_payload,
    runner_profile_payload,
    seed_pipeline_definition,
    seed_runner_profile_with_roots,
    seed_runner_profile_with_workspace_root,
    sha256_json,
)


def _service_for(db_path: Path, service_cls):
    return service_cls(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path),
            close_connection=True,
        )
    )


def _seed_job_and_command(tmp_path: Path) -> tuple[Path, str, str]:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile_with_workspace_root(connection, tmp_path)
    _set_runner_python_executable(connection, sys.executable)
    seed_pipeline_definition(connection)
    connection.close()

    job_service = _service_for(db_path, CreateMigrationJobService)
    job = job_service.execute(
        CreateMigrationJobCommand(
            actor="tester",
            legacy_source_ref="source-root:source",
            output_root_ref="output-root:output",
            runner_profile_id="runner-default",
            runner_profile_version="2026.06",
            pipeline_id="pipeline-default",
            pipeline_version="2026.06",
            target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
            enabled_gates=("build", "test"),
            policy=RunPolicy(),
            correlation_id="corr-job",
        )
    )

    with connect_control_tower(db_path) as conn:
        with SqliteControlTowerUnitOfWork(conn) as uow:
            now = __import__("migration_factory.control_tower.domain.checksums", fromlist=["utc_now_text"]).utc_now_text()
            cmd = CommandExecutionRecord(
                command_id=f"command-{uuid4().hex}",
                job_id=job.job_id,
                operation="foundation_diagnostic",
                status=CommandState.QUEUED,
                created_at=now,
                updated_at=now,
                correlation_id="corr-cmd",
                causation_id=None,
            )
            uow.command_executions.insert_queued(cmd)
            command_id = cmd.command_id

    workspace_service = _service_for(db_path, CommandWorkspaceService)
    workspace_service.prepare_workspace(
        PrepareCommandWorkspaceCommand(
            command_id=command_id,
            job_id=job.job_id,
            working_directory_root_id="working-root",
            working_directory_relative_path=job.job_id,
            worker_id="worker-1",
            launch_attempt=1,
            actor_type="system",
            actor_id="worker",
            correlation_id="corr-ws",
            causation_id=None,
        )
    )

    return db_path, job.job_id, command_id


def _set_runner_python_executable(connection, python_executable: str) -> None:
    row = connection.execute(
        "SELECT payload_json FROM runner_profiles WHERE runner_profile_id = ?",
        ("runner-default",),
    ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    payload["python_executable"] = python_executable
    connection.execute(
        """
        UPDATE runner_profiles
        SET payload_json = ?, payload_checksum = ?
        WHERE runner_profile_id = ?
        """,
        (
            canonical_json(payload),
            sha256_json(payload),
            "runner-default",
        ),
    )


# ── Portable unsupported-platform fail-closed tests ──────────────


def test_unsupported_platform_launcher_raises():
    launcher = UnsupportedPlatformWorkerLauncher()
    manifest = CommandManifest(
        schema_version="1.0.0",
        job_id="job-1",
        command_id="cmd-1",
        worker_id="worker-1",
        operation="foundation_diagnostic",
        run_configuration_artifact_id="art-1",
        run_configuration_checksum="abc",
        working_directory_root_id="working-root",
        working_directory_relative_path="workspace",
        stdout_relative_path="logs/stdout.log",
        stderr_relative_path="logs/stderr.log",
        result_relative_path="result.json",
        spool_relative_path="spool",
        timeout_seconds=3600,
        max_stdout_bytes=1000,
        max_stderr_bytes=1000,
        event_schema_version="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        manifest_checksum="",
    )
    manifest_checksum = __import__(
        "migration_factory.control_tower.domain.manifests",
        fromlist=["compute_manifest_checksum"],
    ).compute_manifest_checksum(manifest)
    manifest = manifest.model_copy(update={"manifest_checksum": manifest_checksum})
    manifest_bytes = __import__(
        "migration_factory.control_tower.domain.checksums",
        fromlist=["canonical_json_bytes"],
    ).canonical_json_bytes(manifest.model_dump(mode="json"))

    with pytest.raises(UnsupportedPlatformError):
        launcher.launch(
            working_dir=Path("/tmp"),
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            python_executable="python",
        )


def test_worker_launch_service_fails_closed_on_unsupported_platform(tmp_path: Path):
    db_path, job_id, command_id = _seed_job_and_command(tmp_path)
    launcher = UnsupportedPlatformWorkerLauncher()
    service = WorkerLaunchService(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path), close_connection=True
        ),
        launcher,
    )

    with pytest.raises(UnsupportedPlatformError):
        service.execute(
            LaunchWorkerCommand(
                command_id=command_id,
                job_id=job_id,
                actor_type="system",
                actor_id="controller",
            )
        )


# ── Pre-launch validation tests (portable) ───────────────────────


def test_launch_rejects_missing_command(tmp_path: Path):
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    connection.close()
    launcher = UnsupportedPlatformWorkerLauncher()
    service = WorkerLaunchService(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path), close_connection=True
        ),
        launcher,
    )

    with pytest.raises(NotFoundError, match="command execution"):
        service.execute(
            LaunchWorkerCommand(
                command_id="nonexistent",
                job_id="job-none",
                actor_type="system",
                actor_id="controller",
            )
        )


def test_launch_rejects_non_queued_command(tmp_path: Path):
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    connection.close()

    diag_service = DiagnosticJobService(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path), close_connection=True
        )
    )
    from migration_factory.control_tower.application.commands import CreateDiagnosticJobCommand
    created = diag_service.create_diagnostic_job(
        CreateDiagnosticJobCommand(
            idempotency_key="test-launch-reject",
            runner_profile_id="runner-default",
            runner_profile_version="2026.06",
            pipeline_id="pipeline-default",
            pipeline_version="2026.06",
            legacy_source_root_id="source-root",
            legacy_source_relative_path="src",
            output_root_id="output-root",
            output_relative_path="out",
            target_proof_level=TargetProofLevel.ANALYZED,
            enabled_gates=(),
            policy=RunPolicy(),
        )
    )

    launcher = UnsupportedPlatformWorkerLauncher()
    service = WorkerLaunchService(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path), close_connection=True
        ),
        launcher,
    )

    with pytest.raises(NotFoundError, match="command execution"):
        service.execute(
            LaunchWorkerCommand(
                command_id=f"nonexistent-{uuid4().hex}",
                job_id=created.job.job_id,
                actor_type="system",
                actor_id="controller",
            )
        )


def test_launch_rejects_unprepared_workspace(tmp_path: Path):
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    connection.close()

    diag_service = DiagnosticJobService(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path), close_connection=True
        )
    )
    from migration_factory.control_tower.application.commands import CreateDiagnosticJobCommand
    created = diag_service.create_diagnostic_job(
        CreateDiagnosticJobCommand(
            idempotency_key="test-unprep-ws",
            runner_profile_id="runner-default",
            runner_profile_version="2026.06",
            pipeline_id="pipeline-default",
            pipeline_version="2026.06",
            legacy_source_root_id="source-root",
            legacy_source_relative_path="src",
            output_root_id="output-root",
            output_relative_path="out",
            target_proof_level=TargetProofLevel.ANALYZED,
            enabled_gates=(),
            policy=RunPolicy(),
        )
    )

    from migration_factory.control_tower.application.commands import StartMigrationJobCommand
    diag_service.start_migration_job(
        StartMigrationJobCommand(
            job_id=created.job.job_id,
            expected_version=1,
            idempotency_key="start-unprep-ws",
            actor_type="user",
            actor_id="tester",
        )
    )

    with connect_control_tower(db_path) as conn:
        cmd_rows = conn.execute(
            "SELECT command_id FROM command_executions WHERE job_id = ?",
            (created.job.job_id,),
        ).fetchall()
    assert len(cmd_rows) == 1
    command_id = str(cmd_rows[0]["command_id"])

    launcher = UnsupportedPlatformWorkerLauncher()
    service = WorkerLaunchService(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path), close_connection=True
        ),
        launcher,
    )

    with pytest.raises(WorkspaceConflictError, match="not prepared"):
        service.execute(
            LaunchWorkerCommand(
                command_id=command_id,
                job_id=created.job.job_id,
                actor_type="system",
                actor_id="controller",
            )
        )


# ── DTO safety tests (portable) ──────────────────────────────────


def test_worker_launch_result_contains_no_raw_handles():
    result = WorkerLaunchResult(
        command_id="cmd-1",
        job_id="job-1",
        process_control_id="uuid-123",
        worker_pid=12345,
        process_started_at="2026-01-01T00:00:00Z",
        worker_id="worker-1",
        launch_attempt=1,
    )
    dump = repr(result)
    assert "handle" not in dump.lower()


def test_projection_payload_does_not_leak_process_details(tmp_path: Path):
    db_path, job_id, command_id = _seed_job_and_command(tmp_path)

    with connect_control_tower(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM command_executions WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    assert row is not None
    assert row["command_manifest_artifact_id"] is not None


# ── Windows-only Job Object integration tests ────────────────────


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Windows-only Job Object integration; skipped on non-Windows.",
)
def test_windows_worker_launcher_creates_process(tmp_path: Path):
    from migration_factory.control_tower.infrastructure.worker_launcher import (
        WindowsWorkerLauncher,
    )

    working_dir = tmp_path / "workspace"
    working_dir.mkdir(parents=True)
    control_dir = working_dir / "control" / "commands" / "cmd-win-test"
    control_dir.mkdir(parents=True)

    manifest_path = control_dir / "command_manifest.json"
    manifest = CommandManifest(
        schema_version="1.0.0",
        job_id="job-win-test",
        command_id="cmd-win-test",
        worker_id="worker-1",
        operation="foundation_diagnostic",
        run_configuration_artifact_id="art-1",
        run_configuration_checksum="abc",
        working_directory_root_id="working-root",
        working_directory_relative_path="workspace",
        stdout_relative_path="logs/stdout.log",
        stderr_relative_path="logs/stderr.log",
        result_relative_path="result.json",
        spool_relative_path="spool",
        timeout_seconds=3600,
        max_stdout_bytes=1000,
        max_stderr_bytes=1000,
        event_schema_version="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        manifest_checksum="",
    )
    from migration_factory.control_tower.domain.manifests import compute_manifest_checksum
    manifest_checksum = compute_manifest_checksum(manifest)
    manifest = manifest.model_copy(update={"manifest_checksum": manifest_checksum})

    from migration_factory.control_tower.domain.checksums import canonical_json_bytes
    manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
    manifest_path.write_bytes(manifest_bytes)

    launcher = WindowsWorkerLauncher()
    result = launcher.launch(
        working_dir=working_dir,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        python_executable=sys.executable,
    )

    assert result.command_id == "cmd-win-test"
    assert result.job_id == "job-win-test"
    assert result.process_control_id is not None
    assert result.worker_pid > 0
    assert result.process_started_at is not None


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Windows-only Job Object integration; skipped on non-Windows.",
)
def test_windows_worker_launcher_assigns_to_job_object(tmp_path: Path):
    import ctypes
    from ctypes import wintypes

    from migration_factory.control_tower.infrastructure.worker_launcher import (
        WindowsWorkerLauncher,
    )

    working_dir = tmp_path / "workspace"
    working_dir.mkdir(parents=True)
    control_dir = working_dir / "control" / "commands" / "cmd-jo-test"
    control_dir.mkdir(parents=True)

    manifest = CommandManifest(
        schema_version="1.0.0",
        job_id="job-jo-test",
        command_id="cmd-jo-test",
        worker_id="worker-1",
        operation="foundation_diagnostic",
        run_configuration_artifact_id="art-1",
        run_configuration_checksum="abc",
        working_directory_root_id="working-root",
        working_directory_relative_path="workspace",
        stdout_relative_path="logs/stdout.log",
        stderr_relative_path="logs/stderr.log",
        result_relative_path="result.json",
        spool_relative_path="spool",
        timeout_seconds=3600,
        max_stdout_bytes=1000,
        max_stderr_bytes=1000,
        event_schema_version="1.0.0",
        created_at="2026-01-01T00:00:00Z",
        manifest_checksum="",
    )
    from migration_factory.control_tower.domain.manifests import compute_manifest_checksum
    manifest_checksum = compute_manifest_checksum(manifest)
    manifest = manifest.model_copy(update={"manifest_checksum": manifest_checksum})

    from migration_factory.control_tower.domain.checksums import canonical_json_bytes
    manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
    manifest_path = control_dir / "command_manifest.json"
    manifest_path.write_bytes(manifest_bytes)

    launcher = WindowsWorkerLauncher()
    result = launcher.launch(
        working_dir=working_dir,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        python_executable=sys.executable,
    )

    kernel32 = ctypes.windll.kernel32

    is_in_job = ctypes.c_bool(False)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    process_handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        result.worker_pid,
    )
    assert process_handle
    assert kernel32.IsProcessInJob(
        process_handle,
        None,
        ctypes.byref(is_in_job),
    )
    kernel32.CloseHandle(process_handle)
    assert is_in_job.value, "Worker process was not assigned to a Job Object"


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Windows-only Job Object integration; skipped on non-Windows.",
)
def test_launch_service_persists_process_state(tmp_path: Path):
    from migration_factory.control_tower.infrastructure.worker_launcher import (
        WindowsWorkerLauncher,
    )

    db_path, job_id, command_id = _seed_job_and_command(tmp_path)

    launcher = WindowsWorkerLauncher()
    service = WorkerLaunchService(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path), close_connection=True
        ),
        launcher,
    )

    result = service.execute(
        LaunchWorkerCommand(
            command_id=command_id,
            job_id=job_id,
            actor_type="system",
            actor_id="controller",
        )
    )

    assert result.command_id == command_id
    assert result.job_id == job_id
    assert result.process_control_id is not None
    assert result.worker_pid > 0

    with connect_control_tower(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM command_executions WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == CommandState.RUNNING.value
    assert row["process_control_id"] == result.process_control_id
    assert row["worker_pid"] == result.worker_pid
    assert row["process_started_at"] is not None


# ── Event and audit trail verification ───────────────────────────


def test_launch_service_creates_starting_and_running_events(tmp_path: Path):
    db_path, job_id, command_id = _seed_job_and_command(tmp_path)

    launcher = UnsupportedPlatformWorkerLauncher()
    with pytest.raises(UnsupportedPlatformError):
        service = WorkerLaunchService(
            lambda: SqliteControlTowerUnitOfWork(
                connect_control_tower(db_path), close_connection=True
            ),
            launcher,
        )
        service.execute(
            LaunchWorkerCommand(
                command_id=command_id,
                job_id=job_id,
                actor_type="system",
                actor_id="controller",
            )
        )

    with connect_control_tower(db_path) as conn:
        events = conn.execute(
            "SELECT event_type, payload_json FROM run_events WHERE job_id = ? ORDER BY sequence",
            (job_id,),
        ).fetchall()
        event_types = [str(e["event_type"]) for e in events]
        assert "command_starting" in event_types

        for event in events:
            payload = __import__("json").loads(str(event["payload_json"]))
            assert "process_control_id" not in payload
            assert "worker_pid" not in payload
