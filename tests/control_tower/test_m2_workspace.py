from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.commands import (
    CreateMigrationJobCommand,
    PrepareCommandWorkspaceCommand,
    RegisterArtifactCommand,
)
from migration_factory.control_tower.application.services import (
    ArtifactRegistryService,
    CommandWorkspaceService,
    CreateMigrationJobService,
)
from migration_factory.control_tower.domain.artifacts import ArtifactHashResult
from migration_factory.control_tower.domain.checksums import (
    canonical_json_bytes,
    sha256_canonical_json,
    sha256_hex,
    stream_sha256,
    utc_now_text,
)
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.entities import (
    CommandExecutionRecord,
    RunConfigurationRecord,
)
from migration_factory.control_tower.domain.errors import (
    ArtifactHashError,
    ArtifactPathError,
    ManifestIntegrityError,
    WorkspaceConflictError,
)
from migration_factory.control_tower.domain.manifests import (
    CommandManifest,
    compute_manifest_checksum,
    verify_manifest_checksum,
)
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.artifact_paths import (
    normalize_registered_relative_path,
)
from migration_factory.control_tower.infrastructure.sqlite.connection import (
    connect_control_tower,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteControlTowerUnitOfWork,
)
from migration_factory.control_tower.infrastructure.workspace import (
    atomic_publish,
    cleanup_stale_temp_files,
    materialize_command_manifest,
    materialize_run_config,
    prepare_safe_workspace,
)
from migration_factory.control_tower.schemas.run_configuration import RunPolicy

from ._helpers import (
    make_migrated_connection,
    pipeline_definition_payload,
    runner_profile_payload,
    seed_pipeline_definition,
    seed_runner_profile_with_workspace_root,
)


def _symlink_to_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"Windows symlink creation privilege unavailable: {exc}")
        raise


def _service_for(db_path: Path, service_cls):
    return service_cls(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path),
            close_connection=True,
        )
    )


class TestAtomicPublish:
    def test_atomic_publish_creates_file(self, tmp_path: Path):
        path = tmp_path / "test.txt"
        content = b"hello world"
        atomic_publish(path, content)
        assert path.read_bytes() == content

    def test_atomic_publish_overwrites_with_allow_flag(self, tmp_path: Path):
        path = tmp_path / "test.txt"
        path.write_bytes(b"original")
        content = b"updated"
        atomic_publish(path, content, allow_overwrite=True)
        assert path.read_bytes() == content

    def test_atomic_publish_idempotent_same_content(self, tmp_path: Path):
        path = tmp_path / "test.txt"
        content = b"hello world"
        path.write_bytes(content)
        mtime_before = path.stat().st_mtime_ns
        atomic_publish(path, content)
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime_before

    def test_atomic_publish_conflict_different_content(self, tmp_path: Path):
        path = tmp_path / "test.txt"
        path.write_bytes(b"original")
        with pytest.raises(WorkspaceConflictError):
            atomic_publish(path, b"different")

    def test_atomic_publish_crash_leaves_only_temp(self, tmp_path: Path):
        path = tmp_path / "target.txt"
        content = b"data"

        import os as _os

        parent = path.parent
        temp_path = parent / f".{path.name}.{uuid4().hex[:12]}.tmp"

        fd = _os.open(str(temp_path), _os.O_CREAT | _os.O_WRONLY | _os.O_EXCL)
        try:
            _os.write(fd, b"partial")
        finally:
            _os.close(fd)

        assert temp_path.exists()
        assert not path.exists()

        temp_path.unlink()


class TestManifestChecksum:
    def test_command_manifest_checksum_excludes_self(self):
        now = utc_now_text()
        m1 = CommandManifest(
            schema_version="1.0.0",
            job_id="job-1",
            command_id="cmd-1",
            worker_id="worker-1",
            operation="test",
            run_configuration_artifact_id="art-1",
            run_configuration_checksum="abc",
            working_directory_root_id="root-1",
            working_directory_relative_path="workspace",
            stdout_relative_path="stdout.log",
            stderr_relative_path="stderr.log",
            result_relative_path="result.json",
            spool_relative_path="spool",
            timeout_seconds=3600,
            max_stdout_bytes=1000,
            max_stderr_bytes=1000,
            event_schema_version="1.0.0",
            created_at=now,
            manifest_checksum="",
        )
        m2 = CommandManifest(
            schema_version="1.0.0",
            job_id="job-1",
            command_id="cmd-1",
            worker_id="worker-1",
            operation="test",
            run_configuration_artifact_id="art-1",
            run_configuration_checksum="abc",
            working_directory_root_id="root-1",
            working_directory_relative_path="workspace",
            stdout_relative_path="stdout.log",
            stderr_relative_path="stderr.log",
            result_relative_path="result.json",
            spool_relative_path="spool",
            timeout_seconds=3600,
            max_stdout_bytes=1000,
            max_stderr_bytes=1000,
            event_schema_version="1.0.0",
            created_at=now,
            manifest_checksum="different",
        )

        assert compute_manifest_checksum(m1) == compute_manifest_checksum(m2)

    def test_command_manifest_verify_checksum_roundtrip(self):
        now = utc_now_text()
        manifest = CommandManifest(
            schema_version="1.0.0",
            job_id="job-1",
            command_id="cmd-1",
            worker_id="worker-1",
            operation="test",
            run_configuration_artifact_id="art-1",
            run_configuration_checksum="abc",
            working_directory_root_id="root-1",
            working_directory_relative_path="workspace",
            stdout_relative_path="stdout.log",
            stderr_relative_path="stderr.log",
            result_relative_path="result.json",
            spool_relative_path="spool",
            timeout_seconds=3600,
            max_stdout_bytes=1000,
            max_stderr_bytes=1000,
            event_schema_version="1.0.0",
            created_at=now,
            manifest_checksum="",
        )

        checksum = compute_manifest_checksum(manifest)
        manifest = manifest.model_copy(update={"manifest_checksum": checksum})
        verify_manifest_checksum(manifest)

    def test_command_manifest_bad_checksum_raises(self):
        now = utc_now_text()
        manifest = CommandManifest(
            schema_version="1.0.0",
            job_id="job-1",
            command_id="cmd-1",
            worker_id="worker-1",
            operation="test",
            run_configuration_artifact_id="art-1",
            run_configuration_checksum="abc",
            working_directory_root_id="root-1",
            working_directory_relative_path="workspace",
            stdout_relative_path="stdout.log",
            stderr_relative_path="stderr.log",
            result_relative_path="result.json",
            spool_relative_path="spool",
            timeout_seconds=3600,
            max_stdout_bytes=1000,
            max_stderr_bytes=1000,
            event_schema_version="1.0.0",
            created_at=now,
            manifest_checksum="bad",
        )

        with pytest.raises(ManifestIntegrityError, match="checksum mismatch"):
            verify_manifest_checksum(manifest)


class TestMaterializeRunConfig:
    def test_materialize_run_config_canonical_matches_checksum(self, tmp_path: Path):
        working_dir = tmp_path / "workspace"
        working_dir.mkdir()

        payload = {"key": "value", "nested": {"a": 1}}
        payload_json = canonical_json_bytes(payload).decode("utf-8")
        payload_checksum = sha256_hex(canonical_json_bytes(payload))

        run_config = RunConfigurationRecord(
            run_configuration_id="rc-1",
            job_id="job-1",
            schema_version="1.0.0",
            runner_profile_id="rp-1",
            runner_profile_version="v1",
            pipeline_id="p-1",
            pipeline_version="v1",
            target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
            enabled_gates_json="[]",
            policy_json="{}",
            payload_json=payload_json,
            payload_checksum=payload_checksum,
            created_at=utc_now_text(),
        )

        result = materialize_run_config(run_config, working_dir, "output-root")
        assert result.checksum_algorithm == "sha256"
        assert result.checksum == payload_checksum
        assert result.size_bytes > 0

        final_path = working_dir / "control" / "run_configuration.json"
        assert final_path.exists()

    def test_materialize_run_config_checksum_mismatch_raises(self, tmp_path: Path):
        working_dir = tmp_path / "workspace"
        working_dir.mkdir()

        run_config = RunConfigurationRecord(
            run_configuration_id="rc-1",
            job_id="job-1",
            schema_version="1.0.0",
            runner_profile_id="rp-1",
            runner_profile_version="v1",
            pipeline_id="p-1",
            pipeline_version="v1",
            target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
            enabled_gates_json="[]",
            policy_json="{}",
            payload_json="{}",
            payload_checksum="deadbeef",
            created_at=utc_now_text(),
        )

        with pytest.raises(ArtifactHashError, match="checksum mismatch"):
            materialize_run_config(run_config, working_dir, "output-root")


class TestMaterializeCommandManifest:
    def test_materialize_command_manifest_creates_file(self, tmp_path: Path):
        working_dir = tmp_path / "workspace"
        working_dir.mkdir()

        now = utc_now_text()
        manifest = CommandManifest(
            schema_version="1.0.0",
            job_id="job-1",
            command_id="cmd-1",
            worker_id="worker-1",
            operation="test",
            run_configuration_artifact_id="art-1",
            run_configuration_checksum="abc",
            working_directory_root_id="root-1",
            working_directory_relative_path="workspace",
            stdout_relative_path="stdout.log",
            stderr_relative_path="stderr.log",
            result_relative_path="result.json",
            spool_relative_path="spool",
            timeout_seconds=3600,
            max_stdout_bytes=1000,
            max_stderr_bytes=1000,
            event_schema_version="1.0.0",
            created_at=now,
            manifest_checksum="",
        )

        artifact_id = "art-run-config-1"
        result, _bytes = materialize_command_manifest(manifest, working_dir, artifact_id, "output-root")

        expected_path = working_dir / "control" / "commands" / "cmd-1" / "command_manifest.json"
        assert expected_path.exists()
        assert result.checksum_algorithm == "sha256"
        assert result.size_bytes > 0


class TestSafeWorkspace:
    def test_prepare_safe_workspace_creates_directory(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        result = prepare_safe_workspace(root, "job-workspace")
        assert result.is_dir()
        assert result == root / "job-workspace"

    def test_prepare_safe_workspace_rejects_parent_traversal(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        with pytest.raises(ArtifactPathError):
            prepare_safe_workspace(root, "../escape")

    def test_prepare_safe_workspace_rejects_absolute(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        with pytest.raises(ArtifactPathError):
            prepare_safe_workspace(root, "/etc/passwd")

    def test_prepare_safe_workspace_creates_nested_dirs(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        result = prepare_safe_workspace(root, "a/b/c")
        assert result.is_dir()
        assert result == root / "a" / "b" / "c"

    def test_prepare_safe_workspace_rejects_unc_path(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        with pytest.raises(ArtifactPathError):
            prepare_safe_workspace(root, r"\\server\share\path")

    def test_prepare_safe_workspace_rejects_drive_qualified(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        with pytest.raises(ArtifactPathError):
            prepare_safe_workspace(root, "D:relative")

    def test_prepare_safe_workspace_rejects_symlink_root(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        link = tmp_path / "link"
        _symlink_to_or_skip(link, root, target_is_directory=True)
        with pytest.raises(ArtifactPathError):
            prepare_safe_workspace(link, "test-dir")


class TestCleanupStaleTempFiles:
    def test_removes_tmp_files(self, tmp_path: Path):
        (tmp_path / "stale.tmp").write_bytes(b"x")
        (tmp_path / "not_temp.txt").write_bytes(b"y")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "also.tmp").write_bytes(b"z")

        cleanup_stale_temp_files(tmp_path)

        assert not (tmp_path / "stale.tmp").exists()
        assert (tmp_path / "not_temp.txt").exists()
        assert not (tmp_path / "subdir" / "also.tmp").exists()

    def test_handles_missing_directory(self, tmp_path: Path):
        cleanup_stale_temp_files(tmp_path / "nonexistent")


class TestImmutableTriggers:
    def test_artifact_triggers_block_update(self, tmp_path: Path):
        connection = make_migrated_connection(tmp_path)
        try:
            _seed_job_for_triggers(connection)
            connection.execute(
                """INSERT INTO artifacts (
                    artifact_id, job_id, stage_run_id, artifact_type,
                    registered_root_id, relative_path, normalized_relative_path,
                    content_type, size_bytes, checksum_algorithm, checksum,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("art-1", "job-1", None, "test", "root-1", "test.txt", "test.txt",
                 None, 100, "sha256", "abc", "2026-01-01T00:00:00Z", "tester"),
            )
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute("UPDATE artifacts SET size_bytes = 200 WHERE artifact_id = ?", ("art-1",))
        finally:
            connection.close()

    def test_artifact_triggers_block_delete(self, tmp_path: Path):
        connection = make_migrated_connection(tmp_path)
        try:
            _seed_job_for_triggers(connection)
            connection.execute(
                """INSERT INTO artifacts (
                    artifact_id, job_id, stage_run_id, artifact_type,
                    registered_root_id, relative_path, normalized_relative_path,
                    content_type, size_bytes, checksum_algorithm, checksum,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("art-1", "job-1", None, "test", "root-1", "test.txt", "test.txt",
                 None, 100, "sha256", "abc", "2026-01-01T00:00:00Z", "tester"),
            )
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute("DELETE FROM artifacts WHERE artifact_id = ?", ("art-1",))
        finally:
            connection.close()

    def test_run_configuration_triggers_block_update(self, tmp_path: Path):
        connection = make_migrated_connection(tmp_path)
        try:
            _seed_job_for_triggers(connection)
            connection.execute(
                """INSERT INTO run_configurations (
                    run_configuration_id, job_id, schema_version,
                    runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                    target_proof_level, enabled_gates_json, policy_json,
                    payload_json, payload_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("rc-1", "job-1", "1.0.0", "rp-1", "v1", "p-1", "v1",
                 "BUILD_TEST_VERIFIED", "[]", "{}", "{}", "abc", "2026-01-01T00:00:00Z"),
            )
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(
                    "UPDATE run_configurations SET payload_json = '{}' WHERE run_configuration_id = ?",
                    ("rc-1",),
                )
        finally:
            connection.close()

    def test_run_configuration_triggers_block_delete(self, tmp_path: Path):
        connection = make_migrated_connection(tmp_path)
        try:
            _seed_job_for_triggers(connection)
            connection.execute(
                """INSERT INTO run_configurations (
                    run_configuration_id, job_id, schema_version,
                    runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                    target_proof_level, enabled_gates_json, policy_json,
                    payload_json, payload_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("rc-1", "job-1", "1.0.0", "rp-1", "v1", "p-1", "v1",
                 "BUILD_TEST_VERIFIED", "[]", "{}", "{}", "abc", "2026-01-01T00:00:00Z"),
            )
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute("DELETE FROM run_configurations WHERE run_configuration_id = ?", ("rc-1",))
        finally:
            connection.close()

    def test_run_events_triggers_block_update(self, tmp_path: Path):
        connection = make_migrated_connection(tmp_path)
        try:
            _seed_job_for_triggers(connection)
            connection.execute(
                """INSERT INTO run_events (
                    event_id, job_id, sequence, event_type, actor_type, actor_id,
                    correlation_id, causation_id, payload_json, payload_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("ev-1", "job-1", 1, "job_created", "user", "tester",
                 None, None, "{}", "abc", "2026-01-01T00:00:00Z"),
            )
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(
                    "UPDATE run_events SET payload_json = '{}' WHERE event_id = ?",
                    ("ev-1",),
                )
        finally:
            connection.close()

    def test_run_events_triggers_block_delete(self, tmp_path: Path):
        connection = make_migrated_connection(tmp_path)
        try:
            _seed_job_for_triggers(connection)
            connection.execute(
                """INSERT INTO run_events (
                    event_id, job_id, sequence, event_type, actor_type, actor_id,
                    correlation_id, causation_id, payload_json, payload_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("ev-1", "job-1", 1, "job_created", "user", "tester",
                 None, None, "{}", "abc", "2026-01-01T00:00:00Z"),
            )
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute("DELETE FROM run_events WHERE event_id = ?", ("ev-1",))
        finally:
            connection.close()


class TestPrepareWorkspaceEndToEnd:
    def test_prepare_workspace_end_to_end(self, tmp_path: Path):
        db_path = tmp_path / "control_tower.sqlite3"
        connection = make_migrated_connection(tmp_path)
        seed_runner_profile_with_workspace_root(connection, tmp_path)
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
                now = utc_now_text()
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
        run_config_result, manifest_result = workspace_service.prepare_workspace(
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

        assert isinstance(run_config_result, ArtifactHashResult)
        assert isinstance(manifest_result, ArtifactHashResult)

        workspace_root = tmp_path / "workspace"
        working_dir = workspace_root / job.job_id
        assert working_dir.is_dir()
        assert (working_dir / "control" / "run_configuration.json").exists()
        assert (working_dir / "control" / "commands" / command_id / "command_manifest.json").exists()

        with connect_control_tower(db_path) as conn:
            cmd_row = conn.execute(
                "SELECT * FROM command_executions WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            assert cmd_row is not None
            assert cmd_row["command_manifest_artifact_id"] is not None
            assert cmd_row["working_directory_root_id"] == "working-root"
            assert cmd_row["working_directory_relative_path"] == job.job_id
            assert cmd_row["worker_id"] == "worker-1"
            assert cmd_row["launch_attempt"] == 1

            artifacts = conn.execute(
                "SELECT artifact_type FROM artifacts WHERE job_id = ?",
                (job.job_id,),
            ).fetchall()
            artifact_types = {row["artifact_type"] for row in artifacts}
            assert "run_configuration" in artifact_types
            assert "command_manifest" in artifact_types

    def test_prepare_workspace_rejects_retry(self, tmp_path: Path):
        db_path = tmp_path / "control_tower.sqlite3"
        connection = make_migrated_connection(tmp_path)
        seed_runner_profile_with_workspace_root(connection, tmp_path)
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

        command_id = f"command-{uuid4().hex}"
        with connect_control_tower(db_path) as conn:
            with SqliteControlTowerUnitOfWork(conn) as uow:
                now = utc_now_text()
                cmd = CommandExecutionRecord(
                    command_id=command_id,
                    job_id=job.job_id,
                    operation="foundation_diagnostic",
                    status=CommandState.QUEUED,
                    created_at=now,
                    updated_at=now,
                    correlation_id="corr-cmd",
                    causation_id=None,
                )
                uow.command_executions.insert_queued(cmd)

        workspace_service = _service_for(db_path, CommandWorkspaceService)
        cmd = PrepareCommandWorkspaceCommand(
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
        workspace_service.prepare_workspace(cmd)

        cmd2 = PrepareCommandWorkspaceCommand(
            command_id=command_id,
            job_id=job.job_id,
            working_directory_root_id="working-root",
            working_directory_relative_path=job.job_id,
            worker_id="worker-2",
            launch_attempt=2,
            actor_type="system",
            actor_id="worker",
            correlation_id="corr-ws2",
            causation_id=None,
        )
        with pytest.raises(WorkspaceConflictError, match="already prepared"):
            workspace_service.prepare_workspace(cmd2)


def _seed_job_for_triggers(connection: sqlite3.Connection) -> None:
    now = utc_now_text()
    _ensure_refs(connection)
    connection.execute(
        """INSERT INTO migration_jobs (
            job_id, version, status, active_slot, last_event_sequence,
            runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
            target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
            created_at, updated_at, started_at, finished_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "job-1", 1, "CREATED", 1, 1,
            "rp-1", "v1", "p-1", "v1",
            "ANALYZED", None, "src", "out",
            now, now, None, None, "tester",
        ),
    )


def _ensure_refs(connection: sqlite3.Connection) -> None:
    existing = connection.execute(
        "SELECT 1 FROM runner_profiles WHERE runner_profile_id = 'rp-1'"
    ).fetchone()
    if existing is None:
        connection.execute(
            """INSERT INTO runner_profiles (
                runner_profile_id, runner_profile_version, display_name, schema_version,
                payload_json, payload_checksum, created_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("rp-1", "v1", "Runner", "1.0", "{}", "abc", "2026-01-01T00:00:00Z", "tester"),
        )

    existing = connection.execute(
        "SELECT 1 FROM pipeline_definitions WHERE pipeline_id = 'p-1'"
    ).fetchone()
    if existing is None:
        connection.execute(
            """INSERT INTO pipeline_definitions (
                pipeline_id, pipeline_version, display_name, schema_version,
                graph_version, graph_state_schema_version, payload_json, payload_checksum,
                created_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("p-1", "v1", "Pipeline", "1.0", "1.0", "1.0", "{}", "abc", "2026-01-01T00:00:00Z", "tester"),
        )
