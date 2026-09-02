from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi import create_app
from migration_factory.control_tower.domain.states import JobState
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower._helpers import artifact_roots, seed_pipeline_definition, seed_runner_profile_with_roots


class _FakeLauncher:
    pass


class _FakeTerminator:
    pass


def test_health_live_reports_process_availability_only(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/v1/health/live")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "live"
    assert body["service"] == "control-tower-api"
    assert "checks" not in body
    assert "pid" not in str(body).lower()


def test_health_ready_checks_required_dependencies(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection, process_control=True)

    response = client.get("/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    checks = body["checks"]
    assert set(checks) == {
        "singleton_ownership",
        "db_migrations",
        "required_root_access",
        "service_loop",
        "process_control",
    }
    assert checks["db_migrations"]["ready"] is True
    assert checks["required_root_access"]["ready"] is True
    assert checks["service_loop"]["ready"] is True
    assert checks["process_control"]["ready"] is True
    assert checks["singleton_ownership"]["ready"] is True
    assert checks["singleton_ownership"]["status"] != "not_implemented"


def test_recovery_required_job_does_not_make_service_unready(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    _insert_job(connection, status=JobState.RECOVERY_REQUIRED.value, active_slot=1)
    client = _client_from_connection(connection, process_control=True)

    response = client.get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_health_dependencies_reports_only_non_secret_versions_and_config_state(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection, process_control=True)

    response = client.get("/v1/health/dependencies")

    assert response.status_code == 200
    body = response.json()
    assert body["origins"]["api"] == "http://127.0.0.1:8000"
    assert body["origins"]["frontend"] == "http://127.0.0.1:3000"
    assert "python" in body["runtime"]
    assert "fastapi" in body["runtime"]
    assert "sqlite" in body["runtime"]
    snapshot = str(body)
    assert "C:\\" not in snapshot
    assert "/tmp/" not in snapshot
    assert "secret" not in snapshot.lower()
    assert "pid" not in snapshot.lower()
    assert "process_control_id" not in snapshot


def _client(tmp_path: Path) -> TestClient:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    return TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")


def _client_from_connection(connection: sqlite3.Connection, *, process_control: bool) -> TestClient:
    return TestClient(
        create_app(
            lambda: SqliteUnitOfWork(connection),
            worker_launcher=_FakeLauncher() if process_control else None,
            worker_terminator=_FakeTerminator() if process_control else None,
        ),
        base_url="http://127.0.0.1:8000",
    )


def _seeded_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    return connection


def _insert_job(connection: sqlite3.Connection, *, status: str, active_slot: int | None) -> None:
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
            "job-1",
            1,
            status,
            active_slot,
            0,
            "runner-default",
            "2026.06",
            "pipeline-default",
            "2026.06",
            "ANALYZED",
            None,
            "source-root:src",
            "output-root:out",
            "2026-06-11T00:00:00Z",
            "2026-06-11T00:00:00Z",
            None,
            None,
            "tester",
        ),
    )


def _api_test_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        tmp_path / "control_tower.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection
