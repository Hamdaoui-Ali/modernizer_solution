from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import seed_job


_MUTATION_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
}


def _seed_command(connection, *, command_id: str, status: CommandState = CommandState.FAILED) -> None:
    connection.execute(
        """
        INSERT INTO command_executions (
            command_id, job_id, operation, status, created_at, updated_at,
            correlation_id, causation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id,
            "job-1",
            "diagnostic",
            status.value,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            None,
            None,
        ),
    )


def _open_api_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    apply_pending_migrations(connection)
    return connection


def test_generated_fake_repair_proposal_api_returns_safe_deterministic_projection(tmp_path) -> None:
    db_path = tmp_path / "v1_14c_api.db"
    connection = _open_api_db(db_path)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-api")
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    classify = client.post(
        "/v1/commands/cmd-api/repair-classifications",
        content=json.dumps({
            "evidence_kind": "stderr_excerpt",
            "failure_summary": (
                r"ImportError in C:\Users\secret\app.py TOKEN=abc "
                r"deployment_id=dep-123 mvn test Traceback (most recent call last):"
            ),
        }),
        headers=_MUTATION_HEADERS,
    )
    assert classify.status_code == 200, classify.text

    first = client.post(
        "/v1/commands/cmd-api/fake-repair-proposals",
        content=json.dumps({}),
        headers=_MUTATION_HEADERS,
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/v1/commands/cmd-api/fake-repair-proposals",
        content=json.dumps({}),
        headers=_MUTATION_HEADERS,
    )
    assert second.status_code == 200, second.text

    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["proposal_id"] == second_payload["proposal_id"]
    assert first_payload["proposal_checksum"] == second_payload["proposal_checksum"]
    assert first_payload["proposal_kind"] == "generated"
    assert first_payload["recommendation_type"] == "dependency_alignment"
    assert first_payload["applicable"] is True

    blob = json.dumps({
        "classification": classify.json(),
        "proposal": first_payload,
    })
    assert r"C:\Users\secret" not in blob
    assert "TOKEN=abc" not in blob
    assert "deployment_id=dep-123" not in blob
    assert "mvn test" not in blob
    assert "Traceback (most recent call last):" not in blob

    listing = client.get("/v1/commands/cmd-api/fake-repair-proposals")
    assert listing.status_code == 200, listing.text
    assert len(listing.json()["proposals"]) == 1


def test_generated_fake_repair_proposal_request_rejects_forbidden_browser_fields(tmp_path) -> None:
    db_path = tmp_path / "v1_14c_forbidden.db"
    connection = _open_api_db(db_path)
    seed_job(connection)
    _seed_command(connection, command_id="cmd-forbidden")
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")
    classify = client.post(
        "/v1/commands/cmd-forbidden/repair-classifications",
        content=json.dumps({
            "evidence_kind": "stderr_excerpt",
            "failure_summary": "Compilation failure: javac failed to compile source",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert classify.status_code == 200, classify.text

    bad = client.post(
        "/v1/commands/cmd-forbidden/fake-repair-proposals",
        content=json.dumps({
            "route": "forbidden",
        }),
        headers=_MUTATION_HEADERS,
    )
    assert bad.status_code == 422
