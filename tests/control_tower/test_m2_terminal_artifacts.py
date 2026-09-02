"""Tests for M2-06 terminal command artifact finalization."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.commands import (
    CreateMigrationJobCommand,
    FinalizeCommandCommand,
    PrepareCommandWorkspaceCommand,
    RegisterArtifactCommand,
)
from migration_factory.control_tower.application.services import (
    ArtifactRegistryService,
    CommandFinalizationService,
    CommandWorkspaceService,
    CreateMigrationJobService,
)
from migration_factory.control_tower.domain.artifacts import ArtifactHashResult
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.entities import CommandExecutionRecord
from migration_factory.control_tower.domain.errors import (
    InvalidJobStateTransitionError,
    NotFoundError,
)
from migration_factory.control_tower.domain.manifests import CommandManifest, compute_manifest_checksum
from migration_factory.control_tower.domain.states import JobState, TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqliteCommandExecutionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteControlTowerUnitOfWork,
)
from migration_factory.control_tower.schemas.run_configuration import RunPolicy
from tests.control_tower._helpers import (
    canonical_json,
    make_migrated_connection,
    seed_pipeline_definition,
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


def _seed_job_with_terminal_command(
    tmp_path: Path,
    status: CommandState = CommandState.SUCCEEDED,
) -> tuple[Path, str, str, Path]:
    """Seed a job and command workspace and set command to terminal state.

    Returns (db_path, job_id, command_id, working_dir).
    """
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile_with_workspace_root(connection, tmp_path)
    _set_runner_python_executable(connection, sys.executable)
    seed_pipeline_definition(connection)
    connection.close()

    job_service = _service_for(db_path, CreateMigrationJobService)
    now = __import__("migration_factory.control_tower.domain.checksums", fromlist=["utc_now_text"]).utc_now_text()
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
            cmd = CommandExecutionRecord(
                command_id=f"command-{uuid4().hex}",
                job_id=job.job_id,
                operation="foundation_diagnostic",
                status=status,
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

    working_dir = tmp_path / "workspace" / job.job_id
    return db_path, job.job_id, command_id, working_dir


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


def _create_log_files(working_dir: Path, with_spool: bool = True) -> None:
    """Create mock stdout, stderr, result, and spool files."""
    log_dir = working_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "stdout.log").write_text("test stdout output\nline2\n")
    (log_dir / "stderr.log").write_text("test stderr output\n")

    result_path = working_dir / "result.json"
    result_path.write_text(json.dumps({"status": "completed", "exit_code": 0}))

    if with_spool:
        spool_dir = working_dir / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)
        (spool_dir / "events.jsonl").write_text(
            json.dumps({"event": "started", "ts": "2026-01-01T00:00:00Z"}) + "\n"
            + json.dumps({"event": "completed", "ts": "2026-01-01T00:00:01Z"}) + "\n"
        )


# ── Finalization success tests ───────────────────────────────────


class TestCommandFinalization:
    def test_finalize_successful_command(self, tmp_path: Path) -> None:
        db_path, job_id, command_id, working_dir = _seed_job_with_terminal_command(
            tmp_path, CommandState.SUCCEEDED
        )
        _create_log_files(working_dir)

        service = _service_for(db_path, CommandFinalizationService)
        service.execute(
            FinalizeCommandCommand(
                command_id=command_id,
                job_id=job_id,
                outcome="completed",
                actor_type="system",
                actor_id="finalizer",
            )
        )

        with connect_control_tower(db_path) as conn:
            row = conn.execute(
                "SELECT * FROM command_executions WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        assert row is not None
        assert row["finalization_status"] in ("COMPLETE_VERIFIED", "COMPLETE_FORENSIC")
        assert row["stdout_artifact_id"] is not None
        assert row["stderr_artifact_id"] is not None
        assert row["result_artifact_id"] is not None
        assert row["finalized_at"] is not None

    def test_finalize_creates_artifacts(self, tmp_path: Path) -> None:
        db_path, job_id, command_id, working_dir = _seed_job_with_terminal_command(
            tmp_path, CommandState.SUCCEEDED
        )
        _create_log_files(working_dir)

        service = _service_for(db_path, CommandFinalizationService)
        service.execute(
            FinalizeCommandCommand(
                command_id=command_id,
                job_id=job_id,
                outcome="completed",
                actor_type="system",
                actor_id="finalizer",
            )
        )

        with connect_control_tower(db_path) as conn:
            artifacts = conn.execute(
                "SELECT * FROM artifacts WHERE job_id = ?",
                (job_id,),
            ).fetchall()
        # Should have run_config, command_manifest, stdout, stderr, result
        artifact_types = [str(a["artifact_type"]) for a in artifacts]
        assert "command_stdout" in artifact_types
        assert "command_stderr" in artifact_types
        assert "command_result" in artifact_types

        for artifact in artifacts:
            relative_path = str(artifact["relative_path"])
            normalized_relative_path = str(artifact["normalized_relative_path"])
            assert not Path(relative_path).is_absolute()
            assert not Path(normalized_relative_path).is_absolute()
            assert not relative_path.startswith(("/", "\\"))
            assert not normalized_relative_path.startswith(("/", "\\"))

    def test_finalize_retries_after_database_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        db_path, job_id, command_id, working_dir = _seed_job_with_terminal_command(
            tmp_path, CommandState.SUCCEEDED
        )
        _create_log_files(working_dir)

        original_finalize = SqliteCommandExecutionRepository.finalize_terminal_artifacts
        calls = {"count": 0}

        def flaky_finalize(self, *args, **kwargs):
            if calls["count"] == 0:
                calls["count"] += 1
                raise RuntimeError("event failed")
            return original_finalize(self, *args, **kwargs)

        monkeypatch.setattr(
            SqliteCommandExecutionRepository,
            "finalize_terminal_artifacts",
            flaky_finalize,
        )

        service = _service_for(db_path, CommandFinalizationService)

        with pytest.raises(RuntimeError, match="event failed"):
            service.execute(
                FinalizeCommandCommand(
                    command_id=command_id,
                    job_id=job_id,
                    outcome="completed",
                    actor_type="system",
                    actor_id="finalizer",
                )
            )

        service.execute(
            FinalizeCommandCommand(
                command_id=command_id,
                job_id=job_id,
                outcome="completed",
                actor_type="system",
                actor_id="finalizer",
            )
        )

        with connect_control_tower(db_path) as conn:
            row = conn.execute(
                "SELECT finalization_status, stdout_artifact_id, stderr_artifact_id, result_artifact_id, spool_artifact_id FROM command_executions WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        assert row is not None
        assert row["finalization_status"] in ("COMPLETE_VERIFIED", "COMPLETE_FORENSIC")
        assert row["stdout_artifact_id"] is not None
        assert row["stderr_artifact_id"] is not None
        assert row["result_artifact_id"] is not None

    def test_finalize_creates_finalization_event(self, tmp_path: Path) -> None:
        db_path, job_id, command_id, working_dir = _seed_job_with_terminal_command(
            tmp_path, CommandState.SUCCEEDED
        )
        _create_log_files(working_dir)

        service = _service_for(db_path, CommandFinalizationService)
        service.execute(
            FinalizeCommandCommand(
                command_id=command_id,
                job_id=job_id,
                outcome="completed",
                actor_type="system",
                actor_id="finalizer",
            )
        )

        with connect_control_tower(db_path) as conn:
            events = conn.execute(
                "SELECT event_type, payload_json FROM run_events WHERE job_id = ? ORDER BY sequence",
                (job_id,),
            ).fetchall()
        event_types = [str(e["event_type"]) for e in events]
        assert "command_finalized" in event_types
        fin_event = [e for e in events if str(e["event_type"]) == "command_finalized"][0]
        payload = json.loads(str(fin_event["payload_json"]))
        assert payload["command_id"] == command_id
        assert payload["job_id"] == job_id
        assert "stdout_artifact_id" in payload

    def test_finalize_idempotent_on_retry(self, tmp_path: Path) -> None:
        db_path, job_id, command_id, working_dir = _seed_job_with_terminal_command(
            tmp_path, CommandState.SUCCEEDED
        )
        _create_log_files(working_dir)

        service = _service_for(db_path, CommandFinalizationService)

        # First finalization
        service.execute(
            FinalizeCommandCommand(
                command_id=command_id,
                job_id=job_id,
                outcome="completed",
                actor_type="system",
                actor_id="finalizer",
            )
        )

        # Second finalization should be idempotent
        result = service.execute(
            FinalizeCommandCommand(
                command_id=command_id,
                job_id=job_id,
                outcome="completed",
                actor_type="system",
                actor_id="finalizer",
            )
        )
        assert result is None  # Returns None for already finalized

    def test_finalize_fails_for_non_terminal_state(self, tmp_path: Path) -> None:
        db_path, job_id, working_dir = _make_nonterminal_command(tmp_path)
        command_id = _direct_insert_nonterminal(db_path, job_id)

        service = _service_for(db_path, CommandFinalizationService)
        with pytest.raises(InvalidJobStateTransitionError):
            service.execute(
                FinalizeCommandCommand(
                    command_id=command_id,
                    job_id=job_id,
                    outcome="completed",
                    actor_type="system",
                    actor_id="finalizer",
                )
            )

    def test_finalize_fails_for_nonexistent_command(self, tmp_path: Path) -> None:
        db_path = tmp_path / "control_tower.sqlite3"
        connection = make_migrated_connection(tmp_path)
        connection.close()

        service = _service_for(db_path, CommandFinalizationService)
        with pytest.raises(NotFoundError, match="command execution"):
            service.execute(
                FinalizeCommandCommand(
                    command_id="nonexistent",
                    job_id="job-none",
                    outcome="completed",
                    actor_type="system",
                    actor_id="finalizer",
                )
            )

    def test_finalize_without_log_files(self, tmp_path: Path) -> None:
        db_path, job_id, command_id, working_dir = _seed_job_with_terminal_command(
            tmp_path, CommandState.SUCCEEDED
        )
        # No log files created

        service = _service_for(db_path, CommandFinalizationService)
        service.execute(
            FinalizeCommandCommand(
                command_id=command_id,
                job_id=job_id,
                outcome="completed",
                actor_type="system",
                actor_id="finalizer",
            )
        )

        with connect_control_tower(db_path) as conn:
            row = conn.execute(
                "SELECT finalization_status FROM command_executions WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        assert row is not None
        assert row["finalization_status"] == "EMPTY"


# ── Forensic spool tests ─────────────────────────────────────────


class TestForensicSpool:
    def test_corrupt_spool_is_forensic(self, tmp_path: Path) -> None:
        db_path, job_id, command_id, working_dir = _seed_job_with_terminal_command(
            tmp_path, CommandState.FAILED
        )
        _create_log_files(working_dir, with_spool=True)
        spool_dir = working_dir / "spool"
        (spool_dir / "events.jsonl").write_text("not valid json\n")

        service = _service_for(db_path, CommandFinalizationService)
        service.execute(
            FinalizeCommandCommand(
                command_id=command_id,
                job_id=job_id,
                outcome="failed",
                actor_type="system",
                actor_id="finalizer",
            )
        )

        with connect_control_tower(db_path) as conn:
            row = conn.execute(
                "SELECT finalization_status FROM command_executions WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        assert row is not None
        # Without verified spool, status is COMPLETE_FORENSIC (log artifacts exist)
        assert row["finalization_status"] in ("COMPLETE_FORENSIC", "COMPLETE_VERIFIED")

        with connect_control_tower(db_path) as conn:
            event_row = conn.execute(
                """
                SELECT payload_json
                FROM run_events
                WHERE job_id = ? AND event_type = 'command_finalized'
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        assert event_row is not None
        payload = json.loads(str(event_row["payload_json"]))
        assert payload["ingestion_verified"] is False
        assert payload["spool_verified"] is False


# ── Artifact registry integration tests ──────────────────────────


class TestArtifactHashing:
    def test_streamed_hashing_no_memory_blowup(self, tmp_path: Path) -> None:
        from migration_factory.control_tower.domain.checksums import stream_sha256

        test_file = tmp_path / "large_output.bin"
        test_file.write_bytes(b"x" * 100_000)

        checksum, size = stream_sha256(test_file)
        assert size == 100_000
        assert len(checksum) == 64  # SHA-256 hex

    def test_artifact_hash_result_structure(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.log"
        test_file.write_text("test content")
        from migration_factory.control_tower.domain.checksums import stream_sha256

        checksum, size = stream_sha256(test_file)
        stat = test_file.stat(follow_symlinks=False)
        result = ArtifactHashResult(
            registered_root_id="working-root",
            root_kind="output",
            relative_path="logs/stdout.log",
            normalized_relative_path="logs/stdout.log",
            checksum_algorithm="sha256",
            checksum=checksum,
            size_bytes=size,
            mtime_ns=int(stat.st_mtime_ns),
            file_identity=(None, None),
        )
        assert result.checksum == checksum
        assert result.size_bytes == len("test content")
        assert result.registered_root_id == "working-root"


# ── Helpers ──────────────────────────────────────────────────────


def _make_nonterminal_command(tmp_path: Path) -> tuple[Path, str, Path]:
    """Create a job without seeding a command, for manual command insertion."""
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

    working_dir = tmp_path / "workspace" / job.job_id
    return db_path, job.job_id, working_dir


def _direct_insert_nonterminal(db_path: Path, job_id: str) -> str:
    """Insert a non-terminal command directly for testing rejection."""
    now = __import__("migration_factory.control_tower.domain.checksums", fromlist=["utc_now_text"]).utc_now_text()
    command_id = f"command-{uuid4().hex}"
    with connect_control_tower(db_path) as conn:
        conn.execute(
            """INSERT INTO command_executions (
                command_id, job_id, operation, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (command_id, job_id, "foundation_diagnostic", CommandState.RUNNING.value, now, now),
        )
    return command_id
