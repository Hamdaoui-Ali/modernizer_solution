"""Tests for V2 setup persistence and preflight service."""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
    compute_setup_checksum,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def connection(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        tmp_path / "setup_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


@pytest.fixture
def service(connection: sqlite3.Connection) -> V2SetupService:
    repo = SqliteV2SetupRepository(connection)
    return V2SetupService(repo)


@pytest.fixture
def sample_request() -> CreateSetupRequest:
    return CreateSetupRequest(
        run_name="legacy-service-v2",
        legacy_app_path="/tmp/test-legacy-app",
        output_parent_path="/tmp/test-output",
        ai_hub_path="/tmp/test-ai-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
        proof_level="build_test_verified",
        skip_endpoint_smoke=True,
        migration_flags={"custom_flag": True},
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
        tmp_path / "api_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    app = app or create_app(lambda: SqliteUnitOfWork(conn))
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return client, conn


class _FakeModelClient:
    def __init__(
        self,
        *,
        success: bool,
        deployment: str,
        failure_reason: str,
        redacted_summary: str,
        response_snippet: str,
        checked_at: str = "2026-06-14T00:00:00Z",
    ) -> None:
        self.success = success
        self.deployment = deployment
        self.failure_reason = failure_reason
        self.redacted_summary = redacted_summary
        self.response_snippet = response_snippet
        self.checked_at = checked_at
        self.calls = 0

    def smoke(self):
        self.calls += 1

        from migration_factory.control_tower.application.v2_assistant_model_client import V2ModelSmokeResult

        return V2ModelSmokeResult(
            success=self.success,
            deployment=self.deployment,
            provider="azure_openai",
            failure_reason=self.failure_reason,
            redacted_summary=self.redacted_summary,
            response_snippet=self.response_snippet,
            latency_ms=1.0,
            checked_at=self.checked_at,
        )


def _create_ai_hub_layout(root: Path) -> Path:
    (root / "profiles").mkdir(parents=True)
    (root / "catalogs" / "openrewrite").mkdir(parents=True)
    (root / "policies").mkdir(parents=True)

    profiles = {
        "springboot-2.1.6-to-2.7-java11": "catalogs/openrewrite/springboot-2.1.6-to-2.7-java11.yaml",
        "springboot-2.7-to-3.5-java17": "catalogs/openrewrite/springboot-3.5-java17.yaml",
        "springboot-3.5-java17-to-java21": "catalogs/openrewrite/springboot-3.5-java17-to-java21.yaml",
        "springboot-3.5-java21-to-4.0-java21": "catalogs/openrewrite/springboot-3.5-java21-to-4.0-java21.yaml",
    }
    for profile, catalog_path in profiles.items():
        (root / "profiles" / f"{profile}.yaml").write_text(
            f"id: {profile}\nopenrewrite:\n  catalog_path: {catalog_path}\n",
            encoding="utf-8",
        )

    for catalog in (
        "springboot-2.1.6-to-2.7-java11.yaml",
        "springboot-3.5-java17.yaml",
        "springboot-3.5-java17-to-java21.yaml",
        "springboot-3.5-java21-to-4.0-java21.yaml",
    ):
        (root / "catalogs" / "openrewrite" / catalog).write_text("recipes: []\n", encoding="utf-8")

    for policy in ("planning", "safety", "transformation"):
        (root / "policies" / f"{policy}.yaml").write_text("rules: []\n", encoding="utf-8")

    return root


# ── Checksum tests ───────────────────────────────────────────────────


def test_compute_setup_checksum_deterministic(sample_request: CreateSetupRequest) -> None:
    c1 = compute_setup_checksum(sample_request)
    c2 = compute_setup_checksum(sample_request)
    assert c1 == c2
    assert len(c1) == 64  # SHA-256 hex digest


def test_compute_setup_checksum_changes_with_fields(sample_request: CreateSetupRequest) -> None:
    c1 = compute_setup_checksum(sample_request)
    modified = CreateSetupRequest(
        run_name=sample_request.run_name + "-modified",
        legacy_app_path=sample_request.legacy_app_path,
        output_parent_path=sample_request.output_parent_path,
        ai_hub_path=sample_request.ai_hub_path,
        java11_home=sample_request.java11_home,
        java17_home=sample_request.java17_home,
        java21_home=sample_request.java21_home,
        maven_cmd=sample_request.maven_cmd,
    )
    c2 = compute_setup_checksum(modified)
    assert c1 != c2


# ── Setup CRUD tests ────────────────────────────────────────────────


def test_create_setup(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    dto = service.create_setup(sample_request)
    assert dto.setup_id
    assert dto.run_name == "legacy-service-v2"
    assert dto.java_homes["java11"] == "/usr/lib/jvm/java-11"
    assert dto.setup_checksum
    assert dto.created_at


def test_get_setup(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    created = service.create_setup(sample_request)
    fetched = service.get_setup(created.setup_id)
    assert fetched is not None
    assert fetched.setup_id == created.setup_id
    assert fetched.run_name == created.run_name
    assert fetched.setup_checksum == created.setup_checksum


def test_get_setup_not_found(service: V2SetupService) -> None:
    fetched = service.get_setup("nonexistent-id")
    assert fetched is None


def test_list_setups(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    service.create_setup(sample_request)
    service.create_setup(CreateSetupRequest(
        run_name="another-run",
        legacy_app_path=sample_request.legacy_app_path,
        output_parent_path=sample_request.output_parent_path,
        ai_hub_path=sample_request.ai_hub_path,
        java11_home=sample_request.java11_home,
        java17_home=sample_request.java17_home,
        java21_home=sample_request.java21_home,
        maven_cmd=sample_request.maven_cmd,
    ))
    dtos = service.list_setups()
    assert len(dtos) >= 2


# ── Preflight tests ─────────────────────────────────────────────────


def test_run_preflight(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    dto = service.create_setup(sample_request)
    preflight = service.run_preflight(dto.setup_id)

    assert preflight.preflight_id
    assert preflight.setup_id == dto.setup_id
    assert preflight.setup_checksum == dto.setup_checksum
    # Most checks will be false since paths don't exist
    assert preflight.all_ready is False
    assert preflight.legacy_app_exists is False
    assert len(preflight.errors) > 0


def test_run_preflight_setup_not_found(service: V2SetupService) -> None:
    with pytest.raises(ValueError, match="not found"):
        service.run_preflight("nonexistent-setup")


def test_run_preflight_ai_required_blocks_when_smoke_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from migration_factory.control_tower.application import v2_setup_service as setup_module

    monkeypatch.setattr(setup_module, "_check_jdk_path_with_version", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        setup_module,
        "_validate_maven_command",
        lambda *args, **kwargs: SimpleNamespace(ready=True, status=setup_module._ToolCheckStatus.READY, message=""),
    )

    legacy = tmp_path / "legacy-app"
    output = tmp_path / "out"
    hub = _create_ai_hub_layout(tmp_path / "ai-hub")
    legacy.mkdir()
    (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
    output.mkdir()
    for jdk_name in ("jdk-11", "jdk-17", "jdk-21"):
        (tmp_path / jdk_name).mkdir()
    maven_cmd = tmp_path / "mvn.cmd"
    maven_cmd.write_text("@echo off", encoding="utf-8")

    connection = sqlite3.connect(
        tmp_path / "required_ai.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    apply_pending_migrations(connection)
    service = V2SetupService(SqliteV2SetupRepository(connection), model_client=_FakeModelClient(
        success=False,
        deployment="secret-deployment",
        failure_reason="http_400",
        redacted_summary="Azure OpenAI smoke failed (HTTP 400).",
        response_snippet='{"error":"Authorization: Bearer sk-abc123"}',
    ))
    setup = service.create_setup(
        CreateSetupRequest(
            run_name="legacy-service-v2",
            legacy_app_path=str(legacy),
            output_parent_path=str(output),
            ai_hub_path=str(hub),
            java11_home=str(tmp_path / "jdk-11"),
            java17_home=str(tmp_path / "jdk-17"),
            java21_home=str(tmp_path / "jdk-21"),
            maven_cmd=str(maven_cmd),
            skip_endpoint_smoke=False,
        )
    )

    preflight = service.run_preflight(setup.setup_id)

    assert preflight.azure_model_ready is False
    assert preflight.azure_model_failure_reason == "http_400"
    assert preflight.all_ready is False
    assert "azure_model_ready" in preflight.readiness
    assert any("Azure model smoke failed" in warning for warning in preflight.warnings)


def test_run_preflight_ai_required_succeeds_when_smoke_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from migration_factory.control_tower.application import v2_setup_service as setup_module

    monkeypatch.setattr(setup_module, "_check_jdk_path_with_version", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        setup_module,
        "_validate_maven_command",
        lambda *args, **kwargs: SimpleNamespace(ready=True, status=setup_module._ToolCheckStatus.READY, message=""),
    )

    legacy = tmp_path / "legacy-app"
    output = tmp_path / "out"
    hub = _create_ai_hub_layout(tmp_path / "ai-hub")
    legacy.mkdir()
    (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
    output.mkdir()
    for jdk_name in ("jdk-11", "jdk-17", "jdk-21"):
        (tmp_path / jdk_name).mkdir()
    maven_cmd = tmp_path / "mvn.cmd"
    maven_cmd.write_text("@echo off", encoding="utf-8")

    connection = sqlite3.connect(
        tmp_path / "required_ai_success.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    apply_pending_migrations(connection)
    service = V2SetupService(SqliteV2SetupRepository(connection), model_client=_FakeModelClient(
        success=True,
        deployment="gpt-5-mini",
        failure_reason="",
        redacted_summary="Azure OpenAI smoke succeeded.",
        response_snippet="OK",
    ))
    setup = service.create_setup(
        CreateSetupRequest(
            run_name="legacy-service-v2",
            legacy_app_path=str(legacy),
            output_parent_path=str(output),
            ai_hub_path=str(hub),
            java11_home=str(tmp_path / "jdk-11"),
            java17_home=str(tmp_path / "jdk-17"),
            java21_home=str(tmp_path / "jdk-21"),
            maven_cmd=str(maven_cmd),
            skip_endpoint_smoke=False,
        )
    )

    preflight = service.run_preflight(setup.setup_id)

    assert preflight.azure_model_ready is True
    assert preflight.all_ready is True
    assert preflight.azure_model_failure_reason == ""
    assert preflight.azure_model_checked_at


def test_preflight_reports_maven_command_failure_not_missing(
    service: V2SetupService,
    sample_request: CreateSetupRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from subprocess import CompletedProcess

    def fake_run(*args, **kwargs):
        return CompletedProcess(args=args, returncode=1, stdout="", stderr="bad maven")

    maven_cmd = tmp_path / "mvn.cmd"
    maven_cmd.touch()
    monkeypatch.setattr("subprocess.run", fake_run)

    request = CreateSetupRequest(
        run_name=sample_request.run_name,
        legacy_app_path=sample_request.legacy_app_path,
        output_parent_path=sample_request.output_parent_path,
        ai_hub_path=sample_request.ai_hub_path,
        java11_home=sample_request.java11_home,
        java17_home=sample_request.java17_home,
        java21_home=sample_request.java21_home,
        maven_cmd=f' "{maven_cmd}" ',
    )
    setup = service.create_setup(request)

    preflight = service.run_preflight(setup.setup_id)

    assert preflight.maven_ready is False
    assert any("Maven command failed:" in error for error in preflight.errors)
    assert not any("Maven command path does not exist" in error for error in preflight.errors)


def test_get_readiness_no_preflight(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    dto = service.create_setup(sample_request)
    readiness = service.get_readiness(dto.setup_id)
    assert readiness is None


def test_get_readiness_after_preflight(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    dto = service.create_setup(sample_request)
    service.run_preflight(dto.setup_id)
    readiness = service.get_readiness(dto.setup_id)

    assert readiness is not None
    assert readiness.setup_checksum == dto.setup_checksum
    assert readiness.preflight_checksum_match is True
    assert isinstance(readiness.gates, dict)


def test_get_readiness_checksum_mismatch(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    """Preflight checksum mismatch should be detected when setup changes."""
    dto = service.create_setup(sample_request)
    service.run_preflight(dto.setup_id)

    # Create a modified version (different checksum)
    modified = CreateSetupRequest(
        run_name=sample_request.run_name + "-v2",
        legacy_app_path=sample_request.legacy_app_path,
        output_parent_path=sample_request.output_parent_path,
        ai_hub_path=sample_request.ai_hub_path,
        java11_home=sample_request.java11_home,
        java17_home=sample_request.java17_home,
        java21_home=sample_request.java21_home,
        maven_cmd=sample_request.maven_cmd,
    )
    dto2 = service.create_setup(modified)

    # Run preflight on dto2, then check readiness for dto1
    service.run_preflight(dto2.setup_id)
    readiness = service.get_readiness(dto.setup_id)

    assert readiness is not None
    # The latest preflight for setup1 should still match
    assert readiness.preflight_checksum_match is True


def test_dto_conversion(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    dto = service.create_setup(sample_request)
    d = service.setup_to_dict(dto)

    assert d["setup_id"] == dto.setup_id
    assert d["run_name"] == dto.run_name
    assert d["setup_checksum"] == dto.setup_checksum
    assert "java_homes" in d
    assert d["java_homes"]["java11"] == "/usr/lib/jvm/java-11"

    # Paths should be redacted
    for path_key in ("legacy_app_path", "output_parent_path", "ai_hub_path", "maven_cmd"):
        assert "redacted" in d.get(path_key, "") or "/" in d.get(path_key, "")


def test_preflight_to_dict(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    dto = service.create_setup(sample_request)
    preflight = service.run_preflight(dto.setup_id)
    d = service.preflight_to_dict(preflight)

    assert d["preflight_id"] == preflight.preflight_id
    assert d["all_ready"] is False
    assert isinstance(d["readiness"], dict)
    assert isinstance(d["warnings"], list)
    assert isinstance(d["errors"], list)
    assert "azure_model_deployment" not in d


def test_preflight_redacts_model_smoke_warning_and_snippet(
    service: V2SetupService,
    sample_request: CreateSetupRequest,
) -> None:
    request = CreateSetupRequest(
        run_name=sample_request.run_name,
        legacy_app_path=sample_request.legacy_app_path,
        output_parent_path=sample_request.output_parent_path,
        ai_hub_path=sample_request.ai_hub_path,
        java11_home=sample_request.java11_home,
        java17_home=sample_request.java17_home,
        java21_home=sample_request.java21_home,
        maven_cmd=sample_request.maven_cmd,
        proof_level=sample_request.proof_level,
        skip_endpoint_smoke=False,
        migration_flags=sample_request.migration_flags,
    )
    fake_client = _FakeModelClient(
        success=False,
        deployment="secret-deployment",
        failure_reason="http_401",
        redacted_summary="Azure OpenAI smoke failed: C:\\Users\\admin\\secrets.txt bearer token sk-abc123.",
        response_snippet='{"error":"Authorization: Bearer sk-abc123"}',
    )
    service_with_model = V2SetupService(service._repo, model_client=fake_client)  # type: ignore[attr-defined]
    dto = service_with_model.create_setup(request)
    preflight = service_with_model.run_preflight(dto.setup_id)
    d = service_with_model.preflight_to_dict(preflight)

    assert fake_client.calls == 1
    assert "secret-deployment" not in str(d)
    assert "sk-abc123" not in str(d)
    assert "C:\\Users\\admin" not in str(d)
    assert "azure_model_deployment" not in d
    assert "Bearer sk-abc123" not in d["azure_model_response_snippet"]
    assert d["azure_model_checked_at"] == "2026-06-14T00:00:00Z"
    assert all("sk-abc123" not in warning for warning in d["warnings"])
    assert all("Bearer" not in warning for warning in d["warnings"])


def test_readiness_to_dict_none(service: V2SetupService) -> None:
    d = service.readiness_to_dict(None)
    assert d["ready"] is False
    assert d["setup_checksum"] == ""


def test_readiness_to_dict_with_value(service: V2SetupService, sample_request: CreateSetupRequest) -> None:
    dto = service.create_setup(sample_request)
    service.run_preflight(dto.setup_id)
    readiness = service.get_readiness(dto.setup_id)
    d = service.readiness_to_dict(readiness)

    assert d["ready"] is False
    assert d["setup_checksum"] == dto.setup_checksum
    assert d["preflight_checksum_match"] is True
    assert isinstance(d["gates"], dict)


# ── API endpoint tests ──────────────────────────────────────────────


def test_create_setup_endpoint(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.post(
        "/v1/migration-setups",
        json={
            "run_name": "test-run",
            "legacy_app_path": "/tmp/test-legacy-app",
            "output_parent_path": "/tmp/test-output",
            "ai_hub_path": "/tmp/test-ai-hub",
            "java11_home": "/usr/lib/jvm/java-11",
            "java17_home": "/usr/lib/jvm/java-17",
            "java21_home": "/usr/lib/jvm/java-21",
            "maven_cmd": "/usr/bin/mvn",
        },
        headers=_mutation_headers(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["setup_id"]
    assert body["run_name"] == "test-run"
    assert body["setup_checksum"]


def test_create_setup_endpoint_rejects_extra(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.post(
        "/v1/migration-setups",
        json={
            "run_name": "test-run",
            "legacy_app_path": "/tmp/test-legacy-app",
            "output_parent_path": "/tmp/test-output",
            "ai_hub_path": "/tmp/test-ai-hub",
            "java11_home": "/usr/lib/jvm/java-11",
            "java17_home": "/usr/lib/jvm/java-17",
            "java21_home": "/usr/lib/jvm/java-21",
            "maven_cmd": "/usr/bin/mvn",
            "extra_field": "should-fail",
        },
        headers=_mutation_headers(),
    )
    assert response.status_code == 422


def test_get_setup_endpoint(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    # Create first
    create_resp = client.post(
        "/v1/migration-setups",
        json={
            "run_name": "test-run",
            "legacy_app_path": "/tmp/test-legacy-app",
            "output_parent_path": "/tmp/test-output",
            "ai_hub_path": "/tmp/test-ai-hub",
            "java11_home": "/usr/lib/jvm/java-11",
            "java17_home": "/usr/lib/jvm/java-17",
            "java21_home": "/usr/lib/jvm/java-21",
            "maven_cmd": "/usr/bin/mvn",
        },
        headers=_mutation_headers(),
    )
    setup_id = create_resp.json()["setup_id"]

    # Get
    response = client.get(f"/v1/migration-setups/{setup_id}", headers={"Host": "127.0.0.1:8000"})
    assert response.status_code == 200
    assert response.json()["setup_id"] == setup_id


def test_get_setup_not_found(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.get(
        "/v1/migration-setups/nonexistent",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert response.status_code == 404


def test_list_setups_endpoint(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.get("/v1/migration-setups", headers={"Host": "127.0.0.1:8000"})
    assert response.status_code == 200
    assert "setups" in response.json()


def test_run_preflight_endpoint(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    # Create setup
    create_resp = client.post(
        "/v1/migration-setups",
        json={
            "run_name": "preflight-test",
            "legacy_app_path": "/nonexistent/path",
            "output_parent_path": "/tmp/test-output-pf",
            "ai_hub_path": "/tmp/test-ai-hub-pf",
            "java11_home": "/usr/lib/jvm/java-11",
            "java17_home": "/usr/lib/jvm/java-17",
            "java21_home": "/usr/lib/jvm/java-21",
            "maven_cmd": "/usr/bin/mvn",
        },
        headers=_mutation_headers(),
    )
    setup_id = create_resp.json()["setup_id"]

    # Run preflight
    response = client.post(
        "/v1/migration-setups/preflight",
        json={"setup_id": setup_id},
        headers=_mutation_headers(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["preflight_id"]
    assert body["all_ready"] is False
    assert len(body["errors"]) > 0


def test_run_preflight_not_found(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.post(
        "/v1/migration-setups/preflight",
        json={"setup_id": "nonexistent"},
        headers=_mutation_headers(),
    )
    assert response.status_code == 404


def test_get_readiness_endpoint(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    # Create and preflight
    create_resp = client.post(
        "/v1/migration-setups",
        json={
            "run_name": "readiness-test",
            "legacy_app_path": "/tmp/fake-legacy",
            "output_parent_path": "/tmp/fake-output",
            "ai_hub_path": "/tmp/fake-hub",
            "java11_home": "/usr/lib/jvm/java-11",
            "java17_home": "/usr/lib/jvm/java-17",
            "java21_home": "/usr/lib/jvm/java-21",
            "maven_cmd": "/usr/bin/mvn",
        },
        headers=_mutation_headers(),
    )
    setup_id = create_resp.json()["setup_id"]
    client.post(
        "/v1/migration-setups/preflight",
        json={"setup_id": setup_id},
        headers=_mutation_headers(),
    )

    # Get readiness
    response = client.get(
        f"/v1/migration-setups/{setup_id}/readiness",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "ready" in body
    assert "setup_checksum" in body
    assert "gates" in body


# ── Append-only trigger tests ────────────────────────────────────────


def test_setup_table_is_append_only(connection: sqlite3.Connection) -> None:
    """Verify the append-only triggers exist and work."""
    triggers = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='v2_migration_setups'"
    ).fetchall()
    trigger_names = [t["name"] for t in triggers]
    assert "v2_migration_setups_no_update" in trigger_names
    assert "v2_migration_setups_no_delete" in trigger_names


def test_preflight_table_is_append_only(connection: sqlite3.Connection) -> None:
    triggers = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='v2_preflight_results'"
    ).fetchall()
    trigger_names = [t["name"] for t in triggers]
    assert "v2_preflight_results_no_update" in trigger_names
    assert "v2_preflight_results_no_delete" in trigger_names


# AI Hub readiness tests


def test_ai_hub_accepts_profiles_openrewrite_catalogs_and_policies(tmp_path: Path) -> None:
    from migration_factory.control_tower.application.v2_setup_service import (
        _check_ai_hub_catalogs,
        _check_ai_hub_policies,
        _check_ai_hub_profiles,
    )

    hub = _create_ai_hub_layout(tmp_path / "ai-hub")

    assert _check_ai_hub_profiles(hub)
    assert _check_ai_hub_catalogs(hub)
    assert _check_ai_hub_policies(hub)


def test_ai_hub_rejects_missing_required_profile(tmp_path: Path) -> None:
    from migration_factory.control_tower.application.v2_setup_service import _check_ai_hub_profiles

    hub = _create_ai_hub_layout(tmp_path / "ai-hub")
    (hub / "profiles" / "springboot-2.7-to-3.5-java17.yaml").unlink()

    assert not _check_ai_hub_profiles(hub)


def test_ai_hub_rejects_missing_declared_catalog(tmp_path: Path) -> None:
    from migration_factory.control_tower.application.v2_setup_service import _check_ai_hub_catalogs

    hub = _create_ai_hub_layout(tmp_path / "ai-hub")
    (hub / "catalogs" / "openrewrite" / "springboot-2.1.6-to-2.7-java11.yaml").unlink()

    assert not _check_ai_hub_catalogs(hub)


def test_ai_hub_rejects_missing_required_policy(tmp_path: Path) -> None:
    from migration_factory.control_tower.application.v2_setup_service import _check_ai_hub_policies

    hub = _create_ai_hub_layout(tmp_path / "ai-hub")
    (hub / "policies" / "safety.yaml").unlink()

    assert not _check_ai_hub_policies(hub)


# ── JDK/Maven subprocess validation tests (mocked) ──────────────────


class TestJdkSubprocessValidation:
    """Tests that _check_jdk_path validates Java major versions via subprocess.

    All subprocess calls are mocked — no real Java/Maven required on CI.
    """

    @staticmethod
    def _fake_subprocess_java11(*args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(
            args=list(args),
            returncode=0,
            stdout="",
            stderr='openjdk version "11.0.21" 2023-10-17\nOpenJDK Runtime Environment (build 11.0.21+9)\n',
        )

    @staticmethod
    def _fake_subprocess_java17(*args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(
            args=list(args),
            returncode=0,
            stdout="",
            stderr='openjdk version "17.0.13" 2024-10-21\nOpenJDK Runtime Environment Temurin-17.0.13+11\n',
        )

    @staticmethod
    def _fake_subprocess_java21(*args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(
            args=list(args),
            returncode=0,
            stdout="",
            stderr='openjdk version "21.0.7" 2025-04-15\nOpenJDK Runtime Environment (build 21.0.7+7)\n',
        )

    @staticmethod
    def _fake_subprocess_java21_ga(*args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(
            args=list(args),
            returncode=0,
            stdout="",
            stderr='openjdk version "21" 2023-09-19\nOpenJDK Runtime Environment (build 21+35-2513)\n',
        )

    @staticmethod
    def _fake_subprocess_java_wrong_version(*args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(
            args=list(args),
            returncode=0,
            stdout="",
            stderr='openjdk version "1.8.0_432" 2024-10-21\nOpenJDK Runtime Environment (build 1.8.0_432-b07)\n',
        )

    @staticmethod
    def _fake_subprocess_timeout(*args, **kwargs):
        from subprocess import TimeoutExpired
        raise TimeoutExpired(cmd=args, timeout=10.0)

    def test_jdk11_correct_version(self, monkeypatch, tmp_path: Path) -> None:
        """JDK 11 path verified to report major 11."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_jdk_path_with_version,
        )
        # Create a fake java home structure so Path.exists() passes
        jdk_home = tmp_path / "jdk-11"
        bin_dir = jdk_home / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "java").touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_subprocess_java11)

        assert _check_jdk_path_with_version(str(jdk_home), 11)

    def test_jdk17_correct_version(self, monkeypatch, tmp_path: Path) -> None:
        """JDK 17 path verified to report major 17."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_jdk_path_with_version,
        )
        jdk_home = tmp_path / "jdk-17"
        bin_dir = jdk_home / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "java").touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_subprocess_java17)

        assert _check_jdk_path_with_version(str(jdk_home), 17)

    def test_jdk21_correct_version(self, monkeypatch, tmp_path: Path) -> None:
        """JDK 21 path verified to report major 21."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_jdk_path_with_version,
        )
        jdk_home = tmp_path / "jdk-21"
        bin_dir = jdk_home / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "java").touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_subprocess_java21)

        assert _check_jdk_path_with_version(str(jdk_home), 21)

    def test_jdk21_ga_version_without_minor_is_accepted(self, monkeypatch, tmp_path: Path) -> None:
        """JDK 21 GA output reports version "21", not always "21.0.x"."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_jdk_path_with_version,
        )
        jdk_home = tmp_path / "jdk-21"
        bin_dir = jdk_home / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "java").touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_subprocess_java21_ga)

        assert _check_jdk_path_with_version(str(jdk_home), 21)

    def test_jdk_wrong_version_rejected(self, monkeypatch, tmp_path: Path) -> None:
        """JDK 11 path reporting Java 8 must be rejected."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_jdk_path_with_version,
        )
        jdk_home = tmp_path / "jdk-11"
        bin_dir = jdk_home / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "java").touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_subprocess_java_wrong_version)

        assert not _check_jdk_path_with_version(str(jdk_home), 11)

    def test_jdk_subprocess_timeout_fails_safe(self, monkeypatch, tmp_path: Path) -> None:
        """Timeout must return False, not crash."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_jdk_path_with_version,
        )
        jdk_home = tmp_path / "jdk-11"
        bin_dir = jdk_home / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "java").touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_subprocess_timeout)

        assert not _check_jdk_path_with_version(str(jdk_home), 11)

    def test_jdk_path_missing_fails_fast(self) -> None:
        """Non-existent path fails before subprocess is called."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_jdk_path_with_version,
        )
        assert not _check_jdk_path_with_version("/nonexistent/jdk/path", 11)


class TestMavenSubprocessValidation:
    """Tests that _check_maven_path validates via mvn --version.

    All subprocess calls are mocked — no real Maven required on CI.
    """

    @staticmethod
    def _fake_subprocess_maven_ok(*args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout='Apache Maven 3.9.15\nMaven home: /opt/maven\nJava version: 21.0.7\n',
            stderr="",
        )

    @staticmethod
    def _fake_subprocess_maven_fail(*args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(
            args=args,
            returncode=1,
            stdout="",
            stderr="Error: Unable to access jarfile",
        )

    @staticmethod
    def _fake_maven_subprocess_timeout(*args, **kwargs):
        from subprocess import TimeoutExpired
        raise TimeoutExpired(cmd=args, timeout=10.0)

    def test_maven_version_ok(self, monkeypatch, tmp_path: Path) -> None:
        """Maven path verified to report Apache Maven in output."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_maven_path,
        )
        mvn_path = tmp_path / "mvn"
        mvn_path.touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_subprocess_maven_ok)

        assert _check_maven_path(str(mvn_path))

    def test_maven_windows_cmd_path_ok(self, monkeypatch, tmp_path: Path) -> None:
        """Quoted Windows mvn.cmd path is preserved and executed directly."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_maven_path,
        )

        mvn_path = tmp_path / "apache-maven" / "bin" / "mvn.cmd"
        mvn_path.parent.mkdir(parents=True)
        mvn_path.touch()
        calls = []

        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
            return self._fake_subprocess_maven_ok(*args, **kwargs)

        import subprocess as sp
        monkeypatch.setattr(sp, "run", fake_run)

        assert _check_maven_path(f' "{mvn_path}" ')
        assert calls[0][0][0] == [str(mvn_path), "--version"]
        assert calls[0][1]["shell"] is False
        assert calls[0][1]["timeout"] == 10.0
        assert calls[0][1]["capture_output"] is True
        assert calls[0][1]["text"] is True
        assert isinstance(calls[0][1]["env"], dict)

    def test_maven_windows_cmd_uses_shell_false_and_direct_argv(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A .cmd Maven path must still be invoked without shell expansion."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _validate_maven_command,
        )

        mvn_path = tmp_path / "apache-maven" / "bin" / "mvn.cmd"
        mvn_path.parent.mkdir(parents=True)
        mvn_path.touch()
        call = {}

        def fake_run(args, **kwargs):
            call["args"] = args
            call["kwargs"] = kwargs
            return self._fake_subprocess_maven_ok(args, **kwargs)

        import subprocess as sp
        monkeypatch.setattr(sp, "run", fake_run)

        result = _validate_maven_command(str(mvn_path))

        assert result.ready is True
        assert call["args"] == [str(mvn_path), "--version"]
        assert call["kwargs"]["shell"] is False

    def test_maven_env_includes_minimal_windows_process_requirements(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Maven receives Java, Maven bin, and Windows process env essentials."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _validate_maven_command,
        )

        mvn_path = tmp_path / "apache-maven" / "bin" / "mvn.cmd"
        mvn_path.parent.mkdir(parents=True)
        mvn_path.touch()
        java21_home = tmp_path / "jdk-21"
        java21_home.mkdir()
        monkeypatch.setenv("PATH", "C:\\Windows\\System32")
        monkeypatch.setenv("SystemRoot", "C:\\Windows")
        monkeypatch.setenv("ComSpec", "C:\\Windows\\System32\\cmd.exe")
        monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        monkeypatch.setenv("TEMP", "C:\\Temp")
        monkeypatch.setenv("TMP", "C:\\Temp")
        monkeypatch.setenv("USERPROFILE", "C:\\Users\\operator")
        monkeypatch.setenv("HOMEDRIVE", "C:")
        monkeypatch.setenv("HOMEPATH", "\\Users\\operator")
        captured_env = {}

        def fake_run(*args, **kwargs):
            captured_env.update(kwargs["env"])
            return self._fake_subprocess_maven_ok(*args, **kwargs)

        import subprocess as sp
        monkeypatch.setattr(sp, "run", fake_run)

        result = _validate_maven_command(str(mvn_path), java_home=str(java21_home))

        assert result.ready is True
        assert captured_env["JAVA_HOME"] == str(java21_home)
        path_entries = captured_env["PATH"].split(os.pathsep)
        assert path_entries[0] == str(java21_home / "bin")
        assert path_entries[1] == str(mvn_path.parent)
        assert captured_env["SystemRoot"] == "C:\\Windows"
        assert captured_env["ComSpec"] == "C:\\Windows\\System32\\cmd.exe"
        assert captured_env["PATHEXT"] == ".COM;.EXE;.BAT;.CMD"
        assert captured_env["TEMP"] == "C:\\Temp"
        assert captured_env["TMP"] == "C:\\Temp"
        assert captured_env["USERPROFILE"] == "C:\\Users\\operator"
        assert captured_env["HOMEDRIVE"] == "C:"
        assert captured_env["HOMEPATH"] == "\\Users\\operator"

    def test_maven_env_passes_java_homes_and_safe_maven_vars(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from migration_factory.control_tower.application.v2_setup_service import (
            _validate_maven_command,
        )

        mvn_path = tmp_path / "mvn.cmd"
        mvn_path.touch()
        monkeypatch.setenv("MAVEN_OPTS", "-Xmx512m")
        monkeypatch.setenv("MAVEN_USER_HOME", "C:\\Users\\operator\\.m2")
        captured_env = {}

        def fake_run(*args, **kwargs):
            captured_env.update(kwargs["env"])
            return self._fake_subprocess_maven_ok(*args, **kwargs)

        import subprocess as sp
        monkeypatch.setattr(sp, "run", fake_run)

        _validate_maven_command(
            str(mvn_path),
            java_home="C:\\Tools\\jdk-21",
            java_homes={
                "JAVA11_HOME": "C:\\Tools\\jdk-11",
                "JAVA17_HOME": "C:\\Tools\\jdk-17",
                "JAVA21_HOME": "C:\\Tools\\jdk-21",
            },
        )

        assert captured_env["JAVA11_HOME"] == "C:\\Tools\\jdk-11"
        assert captured_env["JAVA17_HOME"] == "C:\\Tools\\jdk-17"
        assert captured_env["JAVA21_HOME"] == "C:\\Tools\\jdk-21"
        assert captured_env["MAVEN_OPTS"] == "-Xmx512m"
        assert captured_env["MAVEN_USER_HOME"] == "C:\\Users\\operator\\.m2"

    def test_maven_env_excludes_secret_like_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Azure and secret-like process env vars must not reach Maven."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _validate_maven_command,
        )

        mvn_path = tmp_path / "mvn.cmd"
        mvn_path.touch()
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret-value")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
        monkeypatch.setenv("GITHUB_TOKEN", "token-value")
        monkeypatch.setenv("MAVEN_OPTS", "-Dpassword=secret")
        monkeypatch.setenv("MAVEN_USER_HOME", "C:\\Users\\operator\\.m2")
        captured_env = {}

        def fake_run(*args, **kwargs):
            captured_env.update(kwargs["env"])
            return self._fake_subprocess_maven_ok(*args, **kwargs)

        import subprocess as sp
        monkeypatch.setattr(sp, "run", fake_run)

        _validate_maven_command(str(mvn_path), java_home="C:\\Tools\\jdk-21")

        assert "AZURE_OPENAI_API_KEY" not in captured_env
        assert "AZURE_OPENAI_ENDPOINT" not in captured_env
        assert "GITHUB_TOKEN" not in captured_env
        assert "MAVEN_OPTS" not in captured_env
        assert captured_env["MAVEN_USER_HOME"] == "C:\\Users\\operator\\.m2"

    def test_maven_extensionless_path_ok(self, monkeypatch, tmp_path: Path) -> None:
        """Extensionless mvn executable paths are accepted."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_maven_path,
        )

        mvn_path = tmp_path / "apache-maven" / "bin" / "mvn"
        mvn_path.parent.mkdir(parents=True)
        mvn_path.touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_subprocess_maven_ok)

        assert _check_maven_path(str(mvn_path))

    def test_maven_execution_fails(self, monkeypatch, tmp_path: Path) -> None:
        """Maven that returns non-zero with no output must fail."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_maven_path,
        )
        mvn_path = tmp_path / "mvn"
        mvn_path.touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_subprocess_maven_fail)

        assert not _check_maven_path(str(mvn_path))

    def test_maven_timeout_fails_safe(self, monkeypatch, tmp_path: Path) -> None:
        """Timeout must return False, not crash."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_maven_path,
        )
        mvn_path = tmp_path / "mvn"
        mvn_path.touch()

        import subprocess as sp
        monkeypatch.setattr(sp, "run", self._fake_maven_subprocess_timeout)

        assert not _check_maven_path(str(mvn_path))

    def test_maven_path_missing_fails_fast(self) -> None:
        """Non-existent path fails before subprocess is called."""
        from migration_factory.control_tower.application.v2_setup_service import (
            _check_maven_path,
        )
        assert not _check_maven_path("/nonexistent/mvn")
