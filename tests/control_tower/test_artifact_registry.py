from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from migration_factory.control_tower.application.commands import (
    CreateMigrationJobCommand,
    RegisterArtifactCommand,
)
from migration_factory.control_tower.application.dto import ArtifactDto
from migration_factory.control_tower.application.services import (
    ArtifactRegistryService,
    CreateMigrationJobService,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.errors import (
    ArtifactPathError,
    NotFoundError,
    RegistrationConflictError,
)
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.artifact_paths import (
    hash_registered_artifact,
)
from migration_factory.control_tower.infrastructure.sqlite.connection import (
    connect_control_tower,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteControlTowerUnitOfWork,
)
from migration_factory.control_tower.schemas.run_configuration import RunPolicy

from ._helpers import (
    artifact_roots,
    make_migrated_connection,
    seed_pipeline_definition,
    seed_runner_profile_with_roots,
)


def test_register_artifact_stores_metadata_without_absolute_path(tmp_path: Path) -> None:
    db_path, roots, job_id, _stage_ids = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/artifact.txt", b"artifact contents")

    registered = _artifact_service_for(db_path).register_artifact(
        _artifact_command(job_id, artifact)
    )

    assert isinstance(registered, ArtifactDto)
    assert registered.job_id == job_id
    assert registered.registered_root_id == "source-root"
    assert registered.relative_path == "reports/artifact.txt"
    assert registered.normalized_relative_path == "reports/artifact.txt"
    assert registered.size_bytes == len(b"artifact contents")
    assert registered.checksum_algorithm == "sha256"
    assert registered.content_type == "text/plain"
    assert registered.created_by == "tester"
    assert not Path(registered.relative_path).is_absolute()
    assert str(Path(roots[0].path)) not in registered.relative_path
    assert str(Path(roots[0].path)) not in registered.normalized_relative_path

    with connect_control_tower(db_path) as connection:
        row = connection.execute("SELECT * FROM artifacts WHERE job_id = ?", (job_id,)).fetchone()
        assert row["artifact_id"] == registered.artifact_id
        assert row["registered_root_id"] == "source-root"
        assert row["relative_path"] == "reports/artifact.txt"
        assert row["normalized_relative_path"] == "reports/artifact.txt"
        assert row["size_bytes"] == len(b"artifact contents")
        assert row["checksum"] == artifact.checksum


def test_missing_job_rolls_back_artifact_registration(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    roots = artifact_roots(tmp_path)
    connection = make_migrated_connection(tmp_path)
    connection.close()
    artifact = _write_and_hash(roots, "reports/missing-job.txt", b"orphan")

    with pytest.raises(NotFoundError):
        _artifact_service_for(db_path).register_artifact(
            _artifact_command("missing-job", artifact)
        )

    with connect_control_tower(db_path) as connection:
        assert _count(connection, "artifacts") == 0
        assert _count(connection, "run_events") == 0
        assert _count(connection, "audit_records") == 0


def test_optional_stage_must_belong_to_same_job(tmp_path: Path) -> None:
    db_path, roots, job_id, _stage_ids = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/stage-mismatch.txt", b"stage")
    with connect_control_tower(db_path) as connection:
        _seed_other_job_and_stage(connection)

    with pytest.raises(NotFoundError):
        _artifact_service_for(db_path).register_artifact(
            _artifact_command(job_id, artifact, stage_run_id="stage-other-0001")
        )

    with connect_control_tower(db_path) as connection:
        assert _count(connection, "artifacts") == 0
        assert _artifact_event_count(connection, job_id) == 0
        assert _job_sequence(connection, job_id) == 1


def test_same_artifact_registration_is_idempotent(tmp_path: Path) -> None:
    db_path, roots, job_id, stage_ids = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/idempotent.txt", b"same")
    service = _artifact_service_for(db_path)
    command = _artifact_command(job_id, artifact, stage_run_id=stage_ids[0])

    first = service.register_artifact(command)
    second = service.register_artifact(command)

    assert second == first
    with connect_control_tower(db_path) as connection:
        assert _count(connection, "artifacts") == 1
        assert _artifact_event_count(connection, job_id) == 1
        assert _artifact_audit_count(connection, job_id) == 1
        assert _job_sequence(connection, job_id) == 2


def test_same_normalized_path_different_checksum_conflicts(tmp_path: Path) -> None:
    db_path, roots, job_id, _stage_ids = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/conflict.txt", b"first")
    service = _artifact_service_for(db_path)
    service.register_artifact(_artifact_command(job_id, artifact))

    changed = _write_and_hash(roots, "reports/conflict.txt", b"second")

    with pytest.raises(RegistrationConflictError):
        service.register_artifact(_artifact_command(job_id, changed))

    with connect_control_tower(db_path) as connection:
        assert _count(connection, "artifacts") == 1
        assert _artifact_event_count(connection, job_id) == 1
        assert _artifact_audit_count(connection, job_id) == 1
        assert _job_sequence(connection, job_id) == 2


def test_only_trusted_artifact_metadata_is_accepted(tmp_path: Path) -> None:
    db_path, _roots, job_id, _stage_ids = _job_with_artifact_roots(tmp_path)
    command = RegisterArtifactCommand(
        job_id=job_id,
        artifact=object(),  # type: ignore[arg-type]
        artifact_type="report",
        actor_type="user",
        actor_id="tester",
    )

    with pytest.raises(ArtifactPathError, match="trusted validated artifact metadata"):
        _artifact_service_for(db_path).register_artifact(command)


def test_event_failure_rolls_back_artifact_and_sequence(tmp_path: Path) -> None:
    db_path, roots, job_id, _stage_ids = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/event-failure.txt", b"event")
    command = _artifact_command(job_id, artifact)

    failing_service = ArtifactRegistryService(
        lambda: _FailingRunEventUnitOfWork(
            connect_control_tower(db_path),
            close_connection=True,
        )
    )

    with pytest.raises(RuntimeError, match="event failed"):
        failing_service.register_artifact(command)

    with connect_control_tower(db_path) as connection:
        assert _count(connection, "artifacts") == 0
        assert _artifact_event_count(connection, job_id) == 0
        assert _job_sequence(connection, job_id) == 1


def test_retry_after_database_failure_succeeds(tmp_path: Path) -> None:
    db_path, roots, job_id, _stage_ids = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/retry.txt", b"retry")
    command = _artifact_command(job_id, artifact)
    failing_service = ArtifactRegistryService(
        lambda: _FailingRunEventUnitOfWork(
            connect_control_tower(db_path),
            close_connection=True,
        )
    )

    with pytest.raises(RuntimeError, match="event failed"):
        failing_service.register_artifact(command)

    registered = _artifact_service_for(db_path).register_artifact(command)

    assert registered.checksum == artifact.checksum
    with connect_control_tower(db_path) as connection:
        assert _count(connection, "artifacts") == 1
        assert _artifact_event_count(connection, job_id) == 1
        assert _job_sequence(connection, job_id) == 2


def test_artifact_metadata_survives_database_reopen(tmp_path: Path) -> None:
    db_path, roots, job_id, _stage_ids = _job_with_artifact_roots(tmp_path)
    artifact = _write_and_hash(roots, "reports/persistent.txt", b"persist")

    registered = _artifact_service_for(db_path).register_artifact(
        _artifact_command(job_id, artifact)
    )

    with connect_control_tower(db_path) as reopened:
        with SqliteControlTowerUnitOfWork(reopened) as uow:
            artifacts = uow.artifacts.list_for_job(job_id)

    assert len(artifacts) == 1
    assert artifacts[0] == registered


class _FailingRunEventRepository:
    def insert(self, event) -> None:
        raise RuntimeError("event failed")


class _FailingRunEventUnitOfWork(SqliteControlTowerUnitOfWork):
    def __enter__(self) -> "_FailingRunEventUnitOfWork":
        super().__enter__()
        self.run_events = _FailingRunEventRepository()
        return self


def _job_with_artifact_roots(tmp_path: Path):
    db_path = tmp_path / "control_tower.sqlite3"
    roots = artifact_roots(tmp_path)
    connection = make_migrated_connection(tmp_path)
    seed_runner_profile_with_roots(connection, roots)
    seed_pipeline_definition(connection)
    connection.close()
    job = _create_job_service_for(db_path).execute(_create_command())
    return db_path, roots, job.job_id, job.stage_run_ids


def _create_job_service_for(db_path: Path) -> CreateMigrationJobService:
    return CreateMigrationJobService(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path),
            close_connection=True,
        )
    )


def _artifact_service_for(db_path: Path) -> ArtifactRegistryService:
    return ArtifactRegistryService(
        lambda: SqliteControlTowerUnitOfWork(
            connect_control_tower(db_path),
            close_connection=True,
        )
    )


def _create_command() -> CreateMigrationJobCommand:
    return CreateMigrationJobCommand(
        actor="tester",
        legacy_source_ref="C:/legacy/source",
        output_root_ref="C:/workspace/output",
        runner_profile_id="runner-default",
        runner_profile_version="2026.06",
        pipeline_id="pipeline-default",
        pipeline_version="2026.06",
        target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
        enabled_gates=("build", "test"),
        policy=RunPolicy(),
        correlation_id="corr-job",
    )


def _artifact_command(
    job_id: str,
    artifact,
    *,
    stage_run_id: str | None = None,
) -> RegisterArtifactCommand:
    return RegisterArtifactCommand(
        job_id=job_id,
        artifact=artifact,
        artifact_type="report",
        actor_type="user",
        actor_id="tester",
        stage_run_id=stage_run_id,
        content_type="text/plain",
        correlation_id="corr-artifact",
    )


def _write_and_hash(roots, relative_path: str, contents: bytes):
    path = Path(roots[0].path) / Path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return hash_registered_artifact(roots, "source-root", relative_path)


def _seed_other_job_and_stage(connection: sqlite3.Connection) -> None:
    now = utc_now_text()
    connection.execute(
        """
        INSERT INTO migration_jobs (
            job_id, version, status, active_slot, last_event_sequence,
            runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
            target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
            created_at, updated_at, started_at, finished_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job-other",
            1,
            "COMPLETED",
            None,
            0,
            "runner-default",
            "2026.06",
            "pipeline-default",
            "2026.06",
            "BUILD_TEST_VERIFIED",
            None,
            "C:/legacy/other",
            "C:/workspace/other",
            now,
            now,
            None,
            now,
            "tester",
        ),
    )
    connection.execute(
        """
        INSERT INTO stage_runs (
            stage_run_id, job_id, stage_index, stage_id, status,
            input_source_json, created_at, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "stage-other-0001",
            "job-other",
            1,
            "analyze",
            "PASSED",
            '{"kind":"legacy_source","previous_stage_index":null}',
            now,
            now,
            now,
        ),
    )


def _count(connection: sqlite3.Connection, table_name: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _artifact_event_count(connection: sqlite3.Connection, job_id: str) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM run_events WHERE job_id = ? AND event_type = ?",
            (job_id, "artifact_registered"),
        ).fetchone()[0]
    )


def _artifact_audit_count(connection: sqlite3.Connection, job_id: str) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM audit_records WHERE job_id = ? AND action = ?",
            (job_id, "artifact_registered"),
        ).fetchone()[0]
    )


def _job_sequence(connection: sqlite3.Connection, job_id: str) -> int:
    return int(
        connection.execute(
            "SELECT last_event_sequence FROM migration_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
    )
