from __future__ import annotations

import threading
from pathlib import Path

import pytest

from migration_factory.control_tower.application.commands import CreateMigrationJobCommand
from migration_factory.control_tower.application.services import CreateMigrationJobService
from migration_factory.control_tower.domain.errors import ConcurrencyConflictError
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteControlTowerUnitOfWork
from migration_factory.control_tower.schemas.run_configuration import RunPolicy

from ._helpers import make_migrated_connection, seed_runner_and_pipeline


def test_second_nonterminal_job_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_and_pipeline(connection)
    connection.close()

    service = _service_for(db_path)
    command = _create_command()

    first = service.execute(command)

    with pytest.raises(ConcurrencyConflictError):
        service.execute(command)

    assert first.version == 1

    with connect_control_tower(db_path) as verification_connection:
        active_jobs = verification_connection.execute(
            "SELECT COUNT(*) FROM migration_jobs WHERE active_slot = 1"
        ).fetchone()[0]
        assert active_jobs == 1


def test_concurrent_job_creation_allows_exactly_one(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    connection = make_migrated_connection(tmp_path)
    seed_runner_and_pipeline(connection)
    connection.close()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def worker() -> None:
        service = _service_for(db_path)
        barrier.wait()
        try:
            service.execute(_create_command())
            with outcomes_lock:
                outcomes.append("ok")
        except ConcurrencyConflictError:
            with outcomes_lock:
                outcomes.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    with connect_control_tower(db_path) as verification_connection:
        total_jobs = verification_connection.execute(
            "SELECT COUNT(*) FROM migration_jobs"
        ).fetchone()[0]
        assert total_jobs == 1


def _service_for(db_path: Path) -> CreateMigrationJobService:
    def factory() -> SqliteControlTowerUnitOfWork:
        return SqliteControlTowerUnitOfWork(connect_control_tower(db_path), close_connection=True)

    return CreateMigrationJobService(factory)


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
        correlation_id="corr-1",
    )
