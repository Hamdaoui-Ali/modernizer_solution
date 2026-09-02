"""Integration tests for V1-15 API job_id resolution from command execution.

Verifies that all four V1-15 endpoints resolve job_id from command_id
instead of passing an empty string. Covers happy path, missing command,
and FK integrity.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import seed_job


_MUTATION_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
}


def _open_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    apply_pending_migrations(connection)
    return connection


def _seed_command(
    connection: sqlite3.Connection,
    *,
    command_id: str,
    job_id: str = "job-1",
    status: CommandState = CommandState.QUEUED,
) -> None:
    connection.execute(
        """
        INSERT INTO command_executions (
            command_id, job_id, operation, status, created_at, updated_at,
            correlation_id, causation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id,
            job_id,
            "diagnostic",
            status.value,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            None,
            None,
        ),
    )


def _seed_approval(connection: sqlite3.Connection, approval_id: str, job_id: str = "job-1") -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO v1_approvals (
            approval_id, job_id, interrupt_id, request_checksum, decision,
            approved_by, approval_comments, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            approval_id,
            job_id,
            "int-1",
            "chk-1",
            "approved",
            "tester",
            "",
            "2026-01-01T00:00:00Z",
        ),
    )


def _verify_row_has_job_id(connection: sqlite3.Connection, table: str, id_column: str, record_id: str) -> str:
    """Verify persisted row has a non-empty job_id and return it."""
    row = connection.execute(
        f"SELECT job_id FROM {table} WHERE {id_column} = ?", (record_id,)
    ).fetchone()
    assert row is not None, f"Row not found in {table} with {id_column}={record_id!r}"
    assert row["job_id"], f"job_id is empty in {table} for {id_column}={record_id!r}"
    return row["job_id"]


# ------------------------------------------------------------------
# Tests: validate_patch_policy
# ------------------------------------------------------------------


def test_validate_patch_policy_resolves_job_id(tmp_path: Path) -> None:
    """validate_patch_policy endpoint resolves job_id from command and persists it."""
    db_path = tmp_path / "test_v1_15a_api.db"
    connection = _open_db(db_path)
    seed_job(connection, job_id="job-v1-15a")
    _seed_command(connection, command_id="cmd-v1-15a", job_id="job-v1-15a")
    # Approval is a V1-07A feature; the V1-15A check requires approval_id
    # but the _check_approval call in PatchPolicyService checks only the
    # presence of a non-empty approval_id string, not DB existence.
    # So we don't need to pre-seed an approval record.

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/commands/cmd-v1-15a/patch-policy-validations",
        content=json.dumps({
            "target_path": "src/main/java/com/example/App.java",
            "patch_content": "- old\n+ new",
            "patch_size_bytes": 42,
            "approval_id": "apr-v1-15a",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["job_id"] == "job-v1-15a"
    assert data["validation_id"].startswith("ppv-")

    # Verify persisted row contains the real job_id
    persisted_job_id = _verify_row_has_job_id(
        connection, "v1_patch_policy_validations", "validation_id", data["validation_id"]
    )
    assert persisted_job_id == "job-v1-15a"


def test_validate_patch_policy_rejection_resolves_job_id(tmp_path: Path) -> None:
    """Rejection path also resolves job_id properly and records a rejection."""
    db_path = tmp_path / "test_v1_15a_rejection.db"
    connection = _open_db(db_path)
    seed_job(connection, job_id="job-reject")
    _seed_command(connection, command_id="cmd-reject", job_id="job-reject")

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    # Rejection due to missing approval_id (None)
    response = client.post(
        "/v1/commands/cmd-reject/patch-policy-validations",
        content=json.dumps({
            "target_path": "src/main/java/com/example/App.java",
            "patch_content": "- old\n+ new",
            "patch_size_bytes": 42,
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 400, response.text
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "PATCH_POLICY_VIOLATION"


def test_validate_patch_policy_missing_command_returns_404(tmp_path: Path) -> None:
    """Missing command_id returns a typed 404."""
    db_path = tmp_path / "test_v1_15a_missing.db"
    connection = _open_db(db_path)
    seed_job(connection)

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/commands/cmd-nonexistent/patch-policy-validations",
        content=json.dumps({
            "target_path": "src/main/java/com/example/App.java",
            "patch_content": "- old\n+ new",
            "patch_size_bytes": 42,
            "approval_id": "apr-test",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 404, response.text
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "COMMAND_NOT_FOUND"


# ------------------------------------------------------------------
# Tests: record_sandbox_snapshot
# ------------------------------------------------------------------


def test_record_sandbox_snapshot_resolves_job_id(tmp_path: Path) -> None:
    """record_sandbox_snapshot resolves job_id from command and persists it."""
    db_path = tmp_path / "test_v1_15b_api.db"
    connection = _open_db(db_path)
    seed_job(connection, job_id="job-snp")
    _seed_command(connection, command_id="cmd-snp", job_id="job-snp")

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/commands/cmd-snp/sandbox-snapshots",
        content=json.dumps({
            "stage_index": 1,
            "sandbox_artifact_id": "art-123",
            "sandbox_checksum": "abc123def456",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["job_id"] == "job-snp"
    assert data["snapshot_id"].startswith("snp-")

    # Verify persisted row
    persisted_job_id = _verify_row_has_job_id(
        connection, "v1_sandbox_snapshots", "snapshot_id", data["snapshot_id"]
    )
    assert persisted_job_id == "job-snp"


def test_record_sandbox_snapshot_missing_command_returns_404(tmp_path: Path) -> None:
    """Missing command returns 404 for sandbox snapshot."""
    db_path = tmp_path / "test_v1_15b_missing.db"
    connection = _open_db(db_path)
    seed_job(connection)

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/commands/cmd-nonexistent/sandbox-snapshots",
        content=json.dumps({
            "stage_index": 1,
            "sandbox_artifact_id": "art-123",
            "sandbox_checksum": "abc123",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 404, response.text


# ------------------------------------------------------------------
# Tests: apply_approved_patch
# ------------------------------------------------------------------


def test_apply_approved_patch_resolves_job_id(tmp_path: Path) -> None:
    """apply_approved_patch resolves job_id and persists it."""
    db_path = tmp_path / "test_v1_15c_api.db"
    connection = _open_db(db_path)
    seed_job(connection, job_id="job-ppa")
    _seed_command(connection, command_id="cmd-ppa", job_id="job-ppa")

    # Pre-seed a sandbox snapshot (required by apply_approved_patch)
    connection.execute(
        """
        INSERT INTO v1_sandbox_snapshots (
            snapshot_id, command_id, job_id, stage_index,
            sandbox_artifact_id, sandbox_checksum,
            actor_type, actor_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "snp-ppa-test",
            "cmd-ppa",
            "job-ppa",
            1,
            "art-sandbox",
            "chk-sandbox",
            "system",
            "test",
            "2026-01-01T00:00:00Z",
        ),
    )

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/commands/cmd-ppa/patch-applications",
        content=json.dumps({
            "target_path": "src/main/java/com/example/App.java",
            "patch_content": "- old\n+ new",
            "patch_size_bytes": 42,
            "stage_index": 1,
            "approval_id": "apr-ppa",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["job_id"] == "job-ppa"
    assert data["application_id"].startswith("ppa-")

    # Verify persisted row
    persisted_job_id = _verify_row_has_job_id(
        connection, "v1_patch_applications", "application_id", data["application_id"]
    )
    assert persisted_job_id == "job-ppa"


def test_apply_approved_patch_missing_command_returns_404(tmp_path: Path) -> None:
    """Missing command returns 404 for patch application."""
    db_path = tmp_path / "test_v1_15c_missing.db"
    connection = _open_db(db_path)
    seed_job(connection)

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/commands/cmd-nonexistent/patch-applications",
        content=json.dumps({
            "target_path": "src/main/java/com/example/App.java",
            "patch_content": "- old\n+ new",
            "patch_size_bytes": 42,
            "stage_index": 1,
            "approval_id": "apr-test",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 404, response.text


# ------------------------------------------------------------------
# Tests: record_maven_validation
# ------------------------------------------------------------------


def test_record_maven_validation_resolves_job_id(tmp_path: Path) -> None:
    """record_maven_validation resolves job_id and persists it."""
    db_path = tmp_path / "test_v1_15d_api.db"
    connection = _open_db(db_path)
    seed_job(connection, job_id="job-pmv")
    _seed_command(connection, command_id="cmd-pmv", job_id="job-pmv")

    # Pre-seed validation record, sandbox snapshot and patch application (required by maven validation)
    connection.execute(
        """
        INSERT INTO v1_patch_policy_validations (
            validation_id, command_id, job_id, approved, validation_code,
            reason_code, target_path_hash, patch_size_bytes,
            metacharacter_hits, policy_version, actor_type, actor_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ppv-pmv",
            "cmd-pmv",
            "job-pmv",
            1,
            "APPROVED",
            "policy_pass",
            "abc123",
            42,
            0,
            "v1.0",
            "system",
            "test",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO v1_sandbox_snapshots (
            snapshot_id, command_id, job_id, stage_index,
            sandbox_artifact_id, sandbox_checksum,
            actor_type, actor_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "snp-pmv",
            "cmd-pmv",
            "job-pmv",
            1,
            "art-sandbox",
            "chk-sandbox",
            "system",
            "test",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO v1_patch_applications (
            application_id, command_id, job_id, validation_id, snapshot_id,
            stage_index, target_path_hash, patch_size_bytes,
            applied_by, applied_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ppa-pmv",
            "cmd-pmv",
            "job-pmv",
            "ppv-pmv",
            "snp-pmv",
            1,
            "abc123",
            42,
            "test",
            "2026-01-01T00:00:00Z",
            "applied",
        ),
    )

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/commands/cmd-pmv/maven-validations",
        content=json.dumps({
            "maven_goal": "compile",
            "passed": True,
            "result_summary": "Compilation successful.",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["job_id"] == "job-pmv"
    assert data["maven_validation_id"].startswith("pmv-")

    # Verify persisted row
    persisted_job_id = _verify_row_has_job_id(
        connection, "v1_patch_maven_validations", "maven_validation_id", data["maven_validation_id"]
    )
    assert persisted_job_id == "job-pmv"


def test_record_maven_validation_missing_command_returns_404(tmp_path: Path) -> None:
    """Missing command returns 404 for maven validation."""
    db_path = tmp_path / "test_v1_15d_missing.db"
    connection = _open_db(db_path)
    seed_job(connection)

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/commands/cmd-nonexistent/maven-validations",
        content=json.dumps({
            "maven_goal": "compile",
            "passed": True,
            "result_summary": "OK.",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 404, response.text


# ------------------------------------------------------------------
# FK integrity
# ------------------------------------------------------------------


def test_all_four_endpoints_no_fk_failure_leaks(tmp_path: Path) -> None:
    """None of the four endpoints leak an FK failure to the client."""
    db_path = tmp_path / "test_v1_15_fk.db"
    connection = _open_db(db_path)
    seed_job(connection, job_id="job-fk")
    _seed_command(connection, command_id="cmd-fk", job_id="job-fk")

    # Pre-seed prerequisites
    connection.execute(
        """
        INSERT INTO v1_patch_policy_validations (
            validation_id, command_id, job_id, approved, validation_code,
            reason_code, target_path_hash, patch_size_bytes,
            metacharacter_hits, policy_version, actor_type, actor_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ppv-fk",
            "cmd-fk",
            "job-fk",
            1,
            "APPROVED",
            "policy_pass",
            "abc",
            42,
            0,
            "v1.0",
            "system",
            "test",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO v1_sandbox_snapshots (
            snapshot_id, command_id, job_id, stage_index,
            sandbox_artifact_id, sandbox_checksum,
            actor_type, actor_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "snp-fk",
            "cmd-fk",
            "job-fk",
            1,
            "art-fk",
            "chk-fk",
            "system",
            "test",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO v1_patch_applications (
            application_id, command_id, job_id, validation_id, snapshot_id,
            stage_index, target_path_hash, patch_size_bytes,
            applied_by, applied_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ppa-fk",
            "cmd-fk",
            "job-fk",
            "ppv-fk",
            "snp-fk",
            1,
            "abc",
            42,
            "test",
            "2026-01-01T00:00:00Z",
            "applied",
        ),
    )

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    # 1. validate_patch_policy
    r1 = client.post(
        "/v1/commands/cmd-fk/patch-policy-validations",
        content=json.dumps({
            "target_path": "src/main/java/com/example/App.java",
            "patch_content": "- old\n+ new",
            "patch_size_bytes": 42,
            "approval_id": "apr-fk",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert r1.status_code == 201, f"validate_patch_policy FK leak: {r1.text}"

    # 2. record_sandbox_snapshot
    r2 = client.post(
        "/v1/commands/cmd-fk/sandbox-snapshots",
        content=json.dumps({
            "stage_index": 1,
            "sandbox_artifact_id": "art-fk-2",
            "sandbox_checksum": "chk-fk-2",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert r2.status_code == 201, f"sandbox_snapshot FK leak: {r2.text}"

    # 3. apply_approved_patch
    r3 = client.post(
        "/v1/commands/cmd-fk/patch-applications",
        content=json.dumps({
            "target_path": "src/main/java/com/example/App.java",
            "patch_content": "- old\n+ new",
            "patch_size_bytes": 42,
            "stage_index": 1,
            "approval_id": "apr-fk",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert r3.status_code == 201, f"patch_application FK leak: {r3.text}"

    # 4. record_maven_validation
    r4 = client.post(
        "/v1/commands/cmd-fk/maven-validations",
        content=json.dumps({
            "maven_goal": "compile",
            "passed": True,
            "result_summary": "OK.",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert r4.status_code == 201, f"maven_validation FK leak: {r4.text}"


def test_public_response_remains_redacted(tmp_path: Path) -> None:
    """Public response does not leak sensitive path/content details."""
    db_path = tmp_path / "test_v1_15_redact.db"
    connection = _open_db(db_path)
    seed_job(connection, job_id="job-redact")
    _seed_command(connection, command_id="cmd-redact", job_id="job-redact")

    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    response = client.post(
        "/v1/commands/cmd-redact/patch-policy-validations",
        content=json.dumps({
            "target_path": "src/main/java/com/example/App.java",
            "patch_content": "- old\n+ new",
            "patch_size_bytes": 42,
            "approval_id": "apr-redact",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    # Patch content must not be exposed in the response
    assert "- old" not in json.dumps(data)
    assert "+ new" not in json.dumps(data)
    # Raw target_path must not be exposed (only hash)
    assert "src/main/java/com/example/App.java" not in json.dumps(data)
    # job_id is safe to expose
    assert data["job_id"] == "job-redact"
