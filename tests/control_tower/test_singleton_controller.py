from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi import create_app
from migration_factory.control_tower.infrastructure.singleton import (
    FakeControllerOwnership,
    create_controller_ownership,
)
from migration_factory.control_tower.domain.errors import ControllerOwnershipConflictError
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork


def test_first_controller_acquisition_succeeds(tmp_path: Path) -> None:
    ownership = create_controller_ownership(tmp_path / "control_tower.sqlite3")

    try:
        ownership.acquire()
        snapshot = ownership.snapshot()
        assert snapshot.ready is True
        assert snapshot.status == "owned"
    finally:
        ownership.release()


def test_second_controller_acquisition_is_rejected(tmp_path: Path) -> None:
    resource = tmp_path / "control_tower.sqlite3"
    first = create_controller_ownership(resource)
    second = create_controller_ownership(resource)

    try:
        first.acquire()
        with pytest.raises(ControllerOwnershipConflictError):
            second.acquire()
        assert second.snapshot().status == "conflict"
    finally:
        first.release()
        second.release()


def test_release_allows_reacquisition(tmp_path: Path) -> None:
    resource = tmp_path / "control_tower.sqlite3"
    first = create_controller_ownership(resource)
    second = create_controller_ownership(resource)

    try:
        first.acquire()
        first.release()
        second.acquire()
        assert second.snapshot().ready is True
        assert second.snapshot().status == "owned"
    finally:
        first.release()
        second.release()


def test_readiness_reports_singleton_healthy_when_acquired(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    ownership = FakeControllerOwnership()
    app = create_app(
        lambda: SqliteUnitOfWork(connection),
        controller_ownership=ownership,
    )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["singleton_ownership"] == {"ready": True, "status": "owned"}
    assert "not_implemented" not in str(body)


def test_second_controller_reports_not_ready_and_rejects_requests(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    ownership = FakeControllerOwnership(raise_conflict=True)
    app = create_app(
        lambda: SqliteUnitOfWork(connection),
        controller_ownership=ownership,
    )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        ready = client.get("/v1/health/ready")
        response = client.get("/v1/runner-profiles")

    assert ready.status_code == 200
    assert ready.json()["status"] == "not_ready"
    assert ready.json()["checks"]["singleton_ownership"] == {"ready": False, "status": "conflict"}
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "SERVICE_INSTANCE_CONFLICT"
    assert error["correlation_id"]
    assert "C:\\" not in error["message"]
    assert "Local\\" not in error["message"]


def test_public_readiness_payload_is_redacted(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    app = create_app(
        lambda: SqliteUnitOfWork(connection),
        controller_ownership=FakeControllerOwnership(),
    )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/v1/health/ready")

    assert response.status_code == 200
    snapshot = str(response.json())
    assert "C:\\" not in snapshot
    assert "/tmp/" not in snapshot
    assert "pid" not in snapshot.lower()
    assert "handle" not in snapshot.lower()
    assert "secret" not in snapshot.lower()


def test_tests_can_inject_fake_singleton_provider_and_shutdown_releases(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    ownership = FakeControllerOwnership()
    app = create_app(
        lambda: SqliteUnitOfWork(connection),
        controller_ownership=ownership,
    )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/v1/health/ready")
        assert response.status_code == 200

    assert ownership.acquire_calls >= 1
    assert ownership.release_calls == 1


def _connection(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        tmp_path / "control_tower.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    apply_pending_migrations(connection)
    return connection
