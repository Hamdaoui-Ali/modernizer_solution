from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from migration_factory.control_tower.adapters.fastapi import create_app
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower._helpers import (
    artifact_roots,
    seed_pipeline_definition,
    seed_runner_profile_with_roots,
)


class _FakeLauncher:
    pass


class _FakeTerminator:
    pass


def test_create_get_and_start_diagnostic_job_over_http(tmp_path: Path) -> None:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    client = TestClient(
        create_app(
            lambda: SqliteUnitOfWork(connection),
            worker_launcher=_FakeLauncher(),
            worker_terminator=_FakeTerminator(),
        ),
        base_url="http://127.0.0.1:8000",
    )

    assert client.get("/v1/health/live").json()["status"] == "live"
    assert client.get("/v1/health/ready").json()["status"] == "ready"
    assert client.get("/v1/runner-profiles").json()["runner_profiles"][0]["runner_profile_id"] == "runner-default"
    assert client.get("/v1/pipelines").json()["pipelines"][0]["pipeline_id"] == "pipeline-default"
    root_payload = client.get("/v1/filesystem/roots").json()
    assert root_payload["filesystem_roots"][0]["root_id"] == "source-root"
    assert "path" not in root_payload["filesystem_roots"][0]

    create_response = client.post("/v1/jobs", json=_job_payload(), headers=_mutation_headers(idempotency_key="create-1"))
    assert create_response.status_code == 201
    assert create_response.json()["job"]["state"] == "CREATED"
    etag = create_response.headers["etag"]
    job_id = create_response.json()["job"]["job_id"]

    get_response = client.get(f"/v1/jobs/{job_id}")
    assert get_response.status_code == 200
    assert get_response.headers["etag"] == etag
    listed_jobs = client.get("/v1/jobs")
    assert listed_jobs.status_code == 200
    assert listed_jobs.json()["jobs"][0]["job_id"] == job_id

    missing_precondition = client.post(
        f"/v1/jobs/{job_id}/start",
        json={},
        headers=_mutation_headers(idempotency_key="start-1"),
    )
    assert missing_precondition.status_code == 428

    queued = client.post(
        f"/v1/jobs/{job_id}/start",
        json={},
        headers=_mutation_headers(idempotency_key="start-1", if_match=etag),
    )
    assert queued.status_code == 200
    assert queued.json()["job"]["state"] == "QUEUED"
    assert queued.json()["active_command"]["status"] == "QUEUED"
    assert queued.headers["etag"] != etag
    command_id = queued.json()["active_command"]["command_id"]

    commands = client.get(f"/v1/jobs/{job_id}/commands")
    assert commands.status_code == 200
    assert commands.json()["commands"][0]["command_id"] == command_id

    stdout_log = client.get(f"/v1/jobs/{job_id}/commands/{command_id}/logs/stdout")
    stderr_log = client.get(f"/v1/jobs/{job_id}/commands/{command_id}/logs/stderr")
    assert stdout_log.status_code == 200
    assert stdout_log.json()["stream"] == "stdout"
    assert stderr_log.status_code == 200
    assert stderr_log.json()["stream"] == "stderr"

    artifacts = client.get(f"/v1/jobs/{job_id}/artifacts")
    assert artifacts.status_code == 200
    assert artifacts.json()["artifacts"] == []


def test_create_requires_idempotency_key(tmp_path: Path) -> None:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/jobs",
        json=_job_payload(),
        headers={
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:3000",
            "X-Control-Tower-Client": "control-tower-frontend",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def _job_payload() -> dict:
    return {
        "runner_profile_id": "runner-default",
        "runner_profile_version": "2026.06",
        "pipeline_id": "pipeline-default",
        "pipeline_version": "2026.06",
        "legacy_source_root_id": "source-root",
        "legacy_source_relative_path": "src",
        "output_root_id": "output-root",
        "output_relative_path": "out",
        "target_proof_level": "ANALYZED",
        "enabled_gates": [],
        "policy": {
            "continue_after_warning": False,
            "enable_runtime_gate": False,
            "enable_endpoint_gate": False,
        },
    }


def _mutation_headers(*, idempotency_key: str, if_match: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": "control-tower-frontend",
        "Idempotency-Key": idempotency_key,
    }
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


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
