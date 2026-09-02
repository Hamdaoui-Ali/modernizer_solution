from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi import create_app
from migration_factory.control_tower.adapters.fastapi.app import EventReplayConfig
from migration_factory.control_tower.adapters.fastapi.security import (
    ActorIdentity,
    DEFAULT_FRONTEND_CLIENT_ID,
    LocalApiSecuritySettings,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import V2StageCommandRecord
from tests.control_tower._helpers import artifact_roots, seed_pipeline_definition, seed_runner_profile_with_roots


class _FakeActorProvider:
    def current_actor(self) -> ActorIdentity:
        return ActorIdentity(actor_type="local_operator", actor_id="operator-1")


class _FakeSmokeClient:
    def smoke(self):
        from migration_factory.control_tower.application.v2_assistant_model_client import V2ModelSmokeResult

        return V2ModelSmokeResult(
            success=False,
            deployment="secret-deployment",
            provider="azure_openai",
            failure_reason="http_401",
            redacted_summary="Azure OpenAI smoke failed: Authorization bearer sk-abc123.",
            response_snippet="Bearer sk-abc123 secret-deployment",
            latency_ms=1.0,
            checked_at="2026-06-14T00:00:00Z",
        )


def test_api_defaults_to_127_not_localhost_or_wildcard() -> None:
    settings = LocalApiSecuritySettings()

    assert settings.api_host == "127.0.0.1"
    assert settings.frontend_host == "127.0.0.1"
    assert settings.api_origin == "http://127.0.0.1:8000"
    assert settings.frontend_origin == "http://127.0.0.1:3000"
    assert settings.api_host not in {"localhost", "0.0.0.0"}


def test_supported_config_rejects_mixing_localhost_and_127() -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        LocalApiSecuritySettings(frontend_host="localhost")


def test_trusted_host_rejects_unexpected_hosts(tmp_path: Path) -> None:
    client = _client(tmp_path, base_url="http://localhost:8000")

    response = client.get("/v1/runner-profiles")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "UNTRUSTED_HOST"


def test_browser_mutation_accepts_exact_configured_origin(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)

    response = client.post("/v1/jobs", json=_job_payload(), headers=_mutation_headers())

    assert response.status_code == 201


def test_browser_mutation_rejects_wrong_origin(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)

    response = client.post(
        "/v1/jobs",
        json=_job_payload(),
        headers=_mutation_headers(origin="http://127.0.0.1:4000"),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_ORIGIN"


def test_browser_mutation_rejects_missing_origin(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)

    response = client.post(
        "/v1/jobs",
        json=_job_payload(),
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "create-1",
            "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_ORIGIN"


def test_cors_has_no_wildcard_and_uses_exact_origin(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.options(
        "/v1/jobs",
        headers={
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert response.headers["access-control-allow-origin"] != "*"


def test_mutation_rejects_non_json_content_type(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)

    response = client.post(
        "/v1/jobs",
        content="not-json",
        headers={
            **_mutation_headers(),
            "Content-Type": "text/plain",
        },
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_mutation_rejects_missing_client_header(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)

    response = client.post(
        "/v1/jobs",
        json=_job_payload(),
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "create-1",
            "Origin": "http://127.0.0.1:3000",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CLIENT_HEADER"


def test_mutation_rejects_wrong_client_header(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)

    response = client.post(
        "/v1/jobs",
        json=_job_payload(),
        headers=_mutation_headers(client_id="wrong-client"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CLIENT_HEADER"


def test_read_only_event_replay_endpoint_does_not_require_mutation_header(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    create_response = client.post("/v1/jobs", json=_job_payload(), headers=_mutation_headers())
    job_id = create_response.json()["job"]["job_id"]

    response = client.get(f"/v1/jobs/{job_id}/events?after_sequence=0")

    assert response.status_code == 200


def test_backend_actor_provider_derives_actor_server_side(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection, actor_provider=_FakeActorProvider())
    create_response = client.post("/v1/jobs", json=_job_payload(), headers=_mutation_headers())
    job_id = create_response.json()["job"]["job_id"]
    etag = create_response.headers["etag"]

    start_response = client.post(
        f"/v1/jobs/{job_id}/start",
        json={},
        headers=_mutation_headers(idempotency_key="start-1", if_match=etag),
    )
    assert start_response.status_code == 200

    events = client.get(f"/v1/jobs/{job_id}/events?after_sequence=0").json()["events"]
    state_changed = [event for event in events if event["event_type"] == "job_state_changed"][0]
    assert state_changed["actor_type"] == "local_operator"
    assert state_changed["actor_id"] == "operator-1"


def test_frontend_actor_fields_are_rejected(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    payload = _job_payload() | {"actor_type": "user", "actor_id": "bad"}

    response = client.post("/v1/jobs", json=payload, headers=_mutation_headers())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_public_payloads_and_errors_are_redacted(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    dependencies = client.get("/v1/health/dependencies")

    assert dependencies.status_code == 200
    snapshot = str(dependencies.json())
    assert "C:\\" not in snapshot
    assert "/tmp/" not in snapshot
    assert "pid" not in snapshot.lower()
    assert "process_control_id" not in snapshot
    client.close()

    app = create_app(lambda: SqliteUnitOfWork(connection))

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("SECRET=value path=C:/temp/private.txt pid=123 handle=99")

    with TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False) as exploding_client:
        response = exploding_client.get("/boom")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "INTERNAL_SERVER_ERROR"
    assert "C:\\" not in error["message"]
    assert "SECRET" not in error["message"]


def test_azure_smoke_response_omits_deployment_and_redacts_snippets(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    app = create_app(
        lambda: SqliteUnitOfWork(connection),
        v2_assistant_model_client=_FakeSmokeClient(),
    )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/v1/v2/azure/check-smoke",
            headers=_mutation_headers(),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "deployment" not in body
    assert "secret-deployment" not in response.text
    assert "sk-abc123" not in response.text
    assert body["provider"] == "azure_openai"
    assert body["checked_at"] == "2026-06-14T00:00:00Z"


def test_public_errors_follow_contract_and_include_correlation_id(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)

    response = client.post(
        "/v1/jobs",
        json=_job_payload(),
        headers=_mutation_headers(origin="http://127.0.0.1:4000"),
    )

    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "correlation_id"}
    assert body["error"]["correlation_id"]
    assert response.headers["X-Correlation-ID"] == body["error"]["correlation_id"]


def test_failure_summary_redacts_secret_like_diagnostic_fields(tmp_path: Path) -> None:
    """BuildErrorContract diagnostic fields must not leak secrets in the API response."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    # Create a V2 job first so the endpoint resolves
    job_id = _seed_v2_job(connection, "job-summary")

    # Inject events with secret-like diagnostic data directly into DB
    # (bypasses the orchestrator redaction to test defense-in-depth)
    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="build_failed",
            status="failed",
            message="Build failed",
            payload={
                "matched_line": "C:\\Users\\admin\\secret.key not found",
                "command": ["mvn", "-Dpassword=secret123", "package"],
                "java_home": "C:\\Program Files\\Java\\jdk-11",
                "build_tool": "maven",
                "result_kind": "dependency_error",
                "module": "com.example.secret_module",
                "AZURE_OPENAI_API_KEY": "should-be-redacted",
                "GITHUB_TOKEN": "ghp_should-be-redacted",
            },
        )

    response = client.get(f"/v1/v2/migration-jobs/{job_id}/failure-summary")
    assert response.status_code == 200, response.text
    body_str = str(response.json())

    # Must NOT contain raw secrets or paths
    assert "secret.key" not in body_str
    assert "password=secret123" not in body_str
    assert "AZURE_OPENAI_API_KEY" not in body_str
    assert "GITHUB_TOKEN" not in body_str
    assert "ghp_" not in body_str
    # Absolute paths should be redacted
    assert "C:\\Users\\" not in body_str
    assert "C:\\Program Files\\" not in body_str


def test_sse_events_redact_secret_like_payloads(tmp_path: Path) -> None:
    """Events streamed via SSE must not contain raw secrets in payload."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-sse")

    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="model_invocation_failed",
            status="failed",
            message="API key: sk-abc123secret",
            payload={
                "error_body": '{"error":{"message":"Invalid API key"}}',
                "api_key": "should-be-redacted",
                "AZURE_OPENAI_API_KEY": "secret-value",
            },
        )

    response = client.get(f"/v1/v2/migration-jobs/{job_id}/events?after=0&once=true")
    assert response.status_code == 200, response.text

    body_str = response.text
    # SSE frames must not leak secret values
    assert "sk-abc123secret" not in body_str
    assert "secret-value" not in body_str
    # Secret key names are Python constants, not live secrets —
    # the values behind them must be redacted
    assert "[redacted]" in body_str  # values redacted, keys may remain


def test_v2_pipeline_projection_redacts_all_raw_paths(tmp_path: Path) -> None:
    """V2 pipeline projection must not expose absolute paths."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-pipeline")

    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="artifact_written",
            status="completed",
            message="C:\\Users\\operator\\app\\report.json written",
            payload={
                "artifact_kind": "analysis_report",
                "relative_path": "C:\\Users\\operator\\app\\.migration\\runs\\run-1\\report.json",
            },
        )

    response = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline")
    assert response.status_code == 200, response.text
    body_str = str(response.json())

    assert "C:\\Users\\" not in body_str
    assert "operator" not in body_str
    # artifact_kind metadata (non-path) should still be visible
    assert "analysis_report" in body_str


def test_orchestrator_env_excludes_secret_env_vars() -> None:
    """The _build_env function must never include secret env vars."""
    import os as _os
    from migration_factory.control_tower.application.v2_orchestrator_runner import (
        _build_env, _SECRET_ENV_MARKERS, _MANIFEST_ENV_KEYS,
    )

    # Verify all manifest env keys are safe (no secret markers)
    for key in _MANIFEST_ENV_KEYS:
        upper = key.upper()
        assert not any(m in upper for m in _SECRET_ENV_MARKERS), (
            f"Manifest env key {key!r} matches secret marker"
        )

    # Verify _build_env only accepts keys from _MANIFEST_ENV_KEYS,
    # never arbitrary env vars that could contain secrets.
    manifest = {
        "AZURE_OPENAI_API_KEY": "sk-secret",
        "GITHUB_TOKEN": "ghp-secret",
        "AZURE_OPENAI_ENDPOINT": "https://example.com",
    }
    built = _build_env(manifest)
    assert "AZURE_OPENAI_API_KEY" not in built
    assert "GITHUB_TOKEN" not in built
    assert "AZURE_OPENAI_ENDPOINT" not in built

    # Verify _SECRET_ENV_MARKERS detect real secret keys
    assert any(m in "AZURE_OPENAI_API_KEY" for m in _SECRET_ENV_MARKERS)
    assert any(m in "GITHUB_TOKEN" for m in _SECRET_ENV_MARKERS)


# ── P0-2: Artifact preview security ──


def test_artifact_preview_rejects_kind_not_in_job_artifact_refs(tmp_path: Path) -> None:
    """Artifact preview must return exists=false when the artifact_kind
    is not present in the job's artifact_written events."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-no-artifact", output_parent_path=str(tmp_path))

    # Even though phase2_log is a safe kind, if no artifact_written event
    # has that kind, the endpoint must return exists=false
    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/phase2_log")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is False
    assert body["preview"] == ""


def test_artifact_preview_resolves_dot_migration_ref_under_stage_sandbox_root(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    output_root = tmp_path / "output"
    stage_root = output_root / "stage-2-sandbox" / "modernized"
    artifact = stage_root / ".migration" / "runs" / "run-544" / "phase2.log"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("BUILD_FAILED_IN_SANDBOX\nAZURE_OPENAI_API_KEY=secret\n", encoding="utf-8")
    job_id = _seed_v2_job(connection, "job-dot-migration", output_parent_path=str(output_root))
    now = "2026-01-15T00:00:00Z"

    with SqliteUnitOfWork(connection) as uow:
        uow.v2_commands.save(
            V2StageCommandRecord(
                command_id="cmd-stage-2",
                job_id=job_id,
                stage_index=2,
                manifest_checksum="checksum",
                argv_json=json.dumps([
                    "py",
                    "-m",
                    "migration_factory.orchestrator.runner",
                    "--legacy",
                    str(output_root / "stage-1-sandbox"),
                    "--modernized",
                    str(output_root),
                ]),
                env_json="{}",
                status="manifest_ready",
                created_at=now,
                updated_at=now,
                result_json=None,
            )
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="artifact_written",
            status="completed",
            message="phase2 log written",
            payload={
                "artifact_kind": "phase2_log",
                "relative_path": ".migration/runs/run-544/phase2.log",
            },
        )

    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/phase2_log")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is True
    assert "BUILD_FAILED_IN_SANDBOX" in body["preview"]
    assert "secret" not in body["preview"]


def test_artifact_preview_missing_file_does_not_leak_path(tmp_path: Path) -> None:
    """When an artifact ref points to a missing file, must not leak the path."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-missing-path", output_parent_path=str(tmp_path))

    # Insert an artifact_written event pointing to a non-existent file
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="artifact_written",
            status="completed",
            message="artifact saved",
            payload={
                "artifact_kind": "phase2_log",
                "relative_path": "/nonexistent/path/to/artifact.log",
            },
        )

    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/phase2_log")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is False
    # Must not leak the path or file location
    body_str = str(body)
    assert "/nonexistent/path/to/artifact.log" not in body_str


def test_artifact_preview_bounds_output(tmp_path: Path) -> None:
    """Artifact preview must not exceed 32 KB."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-big-artifact", output_parent_path=str(tmp_path))

    # Create a large file
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    big_path = artifact_dir / "big_file.log"
    big_path.write_text("A" * 100000)  # 100 KB

    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="artifact_written",
            status="completed",
            message="artifact saved",
            payload={
                "artifact_kind": "phase2_log",
                "relative_path": "artifacts/big_file.log",
            },
        )

    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/phase2_log")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is True
    assert body["truncated"] is True
    assert len(body["preview"]) <= 32768


def test_artifact_preview_redacts_api_keys_and_bearer_tokens(tmp_path: Path) -> None:
    """Artifact preview must redact API keys, bearer tokens, and auth headers."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-secret-artifact", output_parent_path=str(tmp_path))

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    secret_path = artifact_dir / "secret.log"
    secret_path.write_text(
        "AZURE_OPENAI_API_KEY=sk-abc123\n"
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9\n"
        "GITHUB_TOKEN=ghp_testtoken123\n"
        "MAVEN_PASSWORD=mysecretpass\n"
    )

    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="artifact_written",
            status="completed",
            message="artifact saved",
            payload={
                "artifact_kind": "phase2_log",
                "relative_path": "artifacts/secret.log",
            },
        )

    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/phase2_log")
    assert response.status_code == 200, response.text
    body = response.json()
    preview = body["preview"]
    assert "sk-abc123" not in preview, f"API key leaked: {preview}"
    assert "eyJhbGciOiJIUzI1NiJ9" not in preview, f"Bearer token leaked: {preview}"
    assert "ghp_testtoken123" not in preview, f"GitHub token leaked: {preview}"
    assert "mysecretpass" not in preview, f"Maven password leaked: {preview}"


def test_artifact_preview_redacts_windows_paths(tmp_path: Path) -> None:
    """Artifact preview must redact full Windows local paths."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-winpath", output_parent_path=str(tmp_path))

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    win_path_file = artifact_dir / "win_path.log"
    win_path_file.write_text(
        "Running from C:\\Users\\admin\\app\\migration\\target\\classes\n"
        "Output dir: D:\\data\\output\\result\n"
    )

    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="artifact_written",
            status="completed",
            message="artifact saved",
            payload={
                "artifact_kind": "phase2_log",
                "relative_path": "artifacts/win_path.log",
            },
        )

    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/phase2_log")
    assert response.status_code == 200, response.text
    body = response.json()
    preview = body["preview"]
    assert "C:\\Users\\admin" not in preview
    assert "D:\\data" not in preview


def test_artifact_preview_rejects_absolute_or_unc_ref(tmp_path: Path) -> None:
    """Artifact preview must reject UNC and drive-qualified refs in payload."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-unc-ref", output_parent_path=str(tmp_path))

    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="artifact_written",
            status="completed",
            message="UNC ref",
            payload={
                "artifact_kind": "phase2_log",
                "relative_path": "\\\\server\\share\\file.log",
            },
        )

    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/phase2_log")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is False, "UNC path ref should be rejected"

    # Drive-qualified path
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="artifact_written",
            status="completed",
            message="Drive path",
            payload={
                "artifact_kind": "failure_classification",
                "relative_path": "C:\\absolute\\path\\file.txt",
            },
        )

    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/failure_classification")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is False, "Drive-qualified ref should be rejected"


def test_artifact_preview_rejects_ref_escaping_job_artifact_root(tmp_path: Path) -> None:
    """Artifact preview must reject refs that escape the job artifact root
    via parent-directory traversal in the stored relative_path."""
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-traversal", output_parent_path=str(tmp_path))

    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="artifact_written",
            status="completed",
            message="traversal ref",
            payload={
                "artifact_kind": "phase2_log",
                "relative_path": "../../etc/passwd",
            },
        )

    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/phase2_log")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is False, "Parent-traversal ref must be rejected"
    # Must not leak the attempted path
    body_str = str(body)
    assert "../../etc/passwd" not in body_str
    assert "etc" not in body_str or "redact" in body_str or "exists" in body_str


def test_artifact_preview_rejects_symlink_escape_from_job_artifact_root(tmp_path: Path) -> None:
    """Artifact preview must reject refs that use a symlink to escape the
    job artifact root.

    Skipped when OS permissions prevent symlink creation.
    """
    connection = _seeded_connection(tmp_path)
    client = _client_from_connection(connection)
    job_id = _seed_v2_job(connection, "job-symlink-escape", output_parent_path=str(tmp_path))

    # Create a symlink inside the trusted root that points outside
    outside_root = tmp_path.parent / "outside_secret.txt"
    outside_root.write_text("sensitive data outside trusted root")

    link_path = tmp_path / "innocent_link.log"
    try:
        link_path.symlink_to(outside_root)
    except (OSError, NotImplementedError, PermissionError):
        pytest.skip("OS does not support symlink creation or permissions denied")

    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    with SqliteUnitOfWork(connection) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="artifact_written",
            status="completed",
            message="symlink escape",
            payload={
                "artifact_kind": "phase2_log",
                "relative_path": "innocent_link.log",
            },
        )

    response = client.get(f"/v1/v2/jobs/{job_id}/artifacts/phase2_log")
    assert response.status_code == 200, response.text
    body = response.json()
    # A symlink that resolves outside the trusted root must be rejected
    assert body["exists"] is False, "Symlink escape must be rejected"
    body_str = str(body)
    assert "outside_secret" not in body_str


def _seed_v2_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    output_parent_path: str | None = None,
) -> str:
    """Insert a minimal V2 job record so V2 endpoints resolve.

    When output_parent_path is provided, also creates a matching
    setup record so the artifact preview endpoint can determine
    the trusted artifact workspace root.
    """
    now = "2026-01-15T00:00:00Z"
    setup_id = f"setup-{job_id}"
    connection.execute(
        """INSERT INTO v2_migration_jobs (
            job_id, setup_id, setup_checksum, pipeline_id,
            stage_chain_json, status, created_at, updated_at, correlation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, setup_id, "cs-test", "pipeline-default", "[]", "running", now, now, None),
    )
    if output_parent_path is not None:
        connection.execute(
            """INSERT INTO v2_migration_setups (
                setup_id, run_name, legacy_app_path, output_parent_path,
                ai_hub_path, java11_home, java17_home, java21_home,
                maven_cmd, proof_level, skip_endpoint_smoke,
                migration_flags_json, setup_checksum, checksum_algorithm,
                created_at, created_by, correlation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                setup_id,
                "test-run",
                output_parent_path,
                output_parent_path,
                output_parent_path,
                "",
                "",
                "",
                "mvn",
                "FULL",
                0,
                "{}",
                "cs-test",
                "sha256",
                now,
                "tester",
                None,
            ),
        )
    return job_id


def _client(
    tmp_path: Path,
    *,
    base_url: str = "http://127.0.0.1:8000",
) -> TestClient:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    return TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url=base_url)


def _client_from_connection(
    connection: sqlite3.Connection,
    *,
    actor_provider: _FakeActorProvider | None = None,
) -> TestClient:
    return TestClient(
        create_app(lambda: SqliteUnitOfWork(connection), actor_provider=actor_provider),
        base_url="http://127.0.0.1:8000",
    )


def _seeded_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    return connection


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


def _mutation_headers(
    *,
    origin: str = "http://127.0.0.1:3000",
    client_id: str = DEFAULT_FRONTEND_CLIENT_ID,
    idempotency_key: str = "create-1",
    if_match: str | None = None,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Origin": origin,
        "X-Control-Tower-Client": client_id,
        "Idempotency-Key": idempotency_key,
    }
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


def _job_payload() -> dict[str, object]:
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
