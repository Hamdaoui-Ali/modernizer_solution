"""Tests for V2 Azure health check service."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.application.v2_azure_health_service import (
    V2AzureHealthService,
)
from migration_factory.control_tower.application.v2_settings import (
    ControlTowerSettings,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_azure_health_repository import (
    SqliteV2AzureHealthRepository,
)


def _mutation_headers():
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _api_client(tmp_path, app=None):
    from migration_factory.control_tower.adapters.fastapi import create_app
    conn = sqlite3.connect(
        tmp_path / "health_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    app = app or create_app(lambda: SqliteUnitOfWork(conn))
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return client, conn


def test_health_check_creates_record(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    repo = SqliteV2AzureHealthRepository(conn)
    settings = ControlTowerSettings()
    service = V2AzureHealthService(repo, settings)

    result = service.run_health_check()

    assert result.health_id
    assert result.overall_status in ("ready", "degraded", "blocked", "unknown")
    assert result.profile_id == "azure-foundry-v2"
    assert "proposer" in result.roles
    assert "reviewer" in result.roles
    assert "assistant" in result.roles
    assert "fallback" in result.roles


def test_health_check_roles_configured_correctly(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test2.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    repo = SqliteV2AzureHealthRepository(conn)
    settings = ControlTowerSettings()
    service = V2AzureHealthService(repo, settings)

    result = service.run_health_check()

    # Fallback is disabled by default
    assert result.roles["fallback"].status == "disabled"

    # Other roles are unconfigured (no env vars set) but should still check
    assert result.roles["proposer"].configured is False


def test_health_check_structured_outputs(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test3.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    repo = SqliteV2AzureHealthRepository(conn)
    settings = ControlTowerSettings()
    service = V2AzureHealthService(repo, settings)

    result = service.run_health_check()

    schemas = {s.schema_name for s in result.structured_outputs}
    assert "PlanProposal" in schemas
    assert "RepairProposal" in schemas
    assert "ReviewerCritique" in schemas
    assert "ActionRequest" in schemas
    assert "AssistantAnswer" in schemas


def test_health_check_persistent(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test4.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    repo = SqliteV2AzureHealthRepository(conn)
    settings = ControlTowerSettings()
    service = V2AzureHealthService(repo, settings)

    result = service.run_health_check()

    # Check persistence
    row = conn.execute(
        "SELECT * FROM v2_model_health_checks WHERE health_id = ?",
        (result.health_id,),
    ).fetchone()
    assert row is not None
    assert row["overall_status"] == result.overall_status


def test_health_check_no_secret_values(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test5.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    repo = SqliteV2AzureHealthRepository(conn)
    settings = ControlTowerSettings()
    service = V2AzureHealthService(repo, settings)

    result = service.run_health_check()
    result_dict = service.health_to_dict(result)
    json_str = str(result_dict)

    # No secret values should appear
    assert "sk-" not in json_str
    assert "api_key" not in json_str.lower() if json_str else True
    assert "env_ref" not in json_str


def test_get_latest_health(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test6.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    repo = SqliteV2AzureHealthRepository(conn)
    settings = ControlTowerSettings()
    service = V2AzureHealthService(repo, settings)

    service.run_health_check()
    result = service.run_health_check()

    latest = service.get_latest_health()
    assert latest is not None
    assert latest.health_id == result.health_id


def test_get_latest_health_no_records(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test7.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    repo = SqliteV2AzureHealthRepository(conn)
    settings = ControlTowerSettings()
    service = V2AzureHealthService(repo, settings)

    latest = service.get_latest_health()
    assert latest is None


def test_health_to_dict_none(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test8.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    repo = SqliteV2AzureHealthRepository(conn)
    settings = ControlTowerSettings()
    service = V2AzureHealthService(repo, settings)

    d = service.health_to_dict(None)
    assert d["status"] == "unknown"


def test_health_check_endpoint(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.post(
        "/v1/model-profiles/azure-foundry-v2/health-check",
        headers=_mutation_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert "overall_status" in body
    assert "roles" in body
    assert "proposer" in body["roles"]
    assert "fallback" in body["roles"]
    assert "env_ref" not in str(body)


def test_health_check_endpoint_no_secrets(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.post(
        "/v1/model-profiles/azure-foundry-v2/health-check",
        headers=_mutation_headers(),
    )
    json_str = str(response.json())
    assert "sk-" not in json_str
    assert "api_key=" not in json_str.lower()
    assert "env_ref" not in json_str


def test_get_health_endpoint(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    # Run health check first
    client.post(
        "/v1/model-profiles/azure-foundry-v2/health-check",
        headers=_mutation_headers(),
    )
    # Get latest
    response = client.get(
        "/v1/model-profiles/azure-foundry-v2/health",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "overall_status" in body


def test_health_check_endpoint_different_profile(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.post(
        "/v1/model-profiles/custom-profile/health-check",
        headers=_mutation_headers(),
    )
    assert response.status_code == 200
    assert response.json()["profile_id"] == "custom-profile"


def test_health_check_append_only_trigger(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test9.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='v2_model_health_checks'"
    ).fetchall()
    trigger_names = [t["name"] for t in triggers]
    assert "v2_model_health_checks_no_update" in trigger_names
    assert "v2_model_health_checks_no_delete" in trigger_names


def test_health_check_non_blocking_by_default(tmp_path: Path) -> None:
    """Health checks should never be required for deterministic start."""
    conn = sqlite3.connect(
        tmp_path / "test10.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    repo = SqliteV2AzureHealthRepository(conn)
    settings = ControlTowerSettings()
    service = V2AzureHealthService(repo, settings)

    result = service.run_health_check()

    # Even if health is degraded/blocked, the service still runs
    assert result.overall_status in ("ready", "degraded", "blocked", "unknown")
    # The important thing is we don't raise an error
    assert result.health_id is not None
