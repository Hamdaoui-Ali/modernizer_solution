from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi.app import (
    EventReplayConfig,
    PublicEventNotifier,
    SseClientLimiter,
    _event_stream,
    create_app,
)
from migration_factory.control_tower.application.commands import (
    CreateDiagnosticJobCommand,
    StartMigrationJobCommand,
)
from migration_factory.control_tower.application.queries import (
    ControlTowerQueryService,
    parse_public_event_cursor,
)
from migration_factory.control_tower.application.services import DiagnosticJobService
from migration_factory.control_tower.domain.errors import (
    EventCursorConflictError,
    InvalidEventCursorError,
)
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.schemas.run_configuration import RunPolicy
from tests.control_tower._helpers import (
    artifact_roots,
    seed_pipeline_definition,
    seed_runner_profile_with_roots,
)
from tests.control_tower.test_fastapi_diagnostic_queue import _mutation_headers


def test_public_event_catalog_and_run_events_fk_are_extensible(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)

    event_types = {
        row["event_type"]
        for row in connection.execute("SELECT event_type FROM event_types").fetchall()
    }
    assert {"job_created", "job_state_changed", "artifact_registered", "command_queued"} <= event_types
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    service = _service(connection)
    created = service.create_diagnostic_job(_create_command("create-1"))
    service.start_migration_job(_start_command(created.job.job_id, 1, "start-1"))

    rows = connection.execute(
        """
        SELECT sequence, event_type, payload_checksum
        FROM run_events
        WHERE job_id = ?
        ORDER BY sequence
        """,
        (created.job.job_id,),
    ).fetchall()
    assert [(row["sequence"], row["event_type"]) for row in rows] == [
        (1, "job_created"),
        (2, "command_queued"),
        (3, "job_state_changed"),
    ]
    assert all(str(row["payload_checksum"]) for row in rows)


def test_replay_query_is_ordered_bounded_and_returns_dtos(tmp_path: Path) -> None:
    connection = _seeded_connection(tmp_path)
    service = _service(connection)
    created = service.create_diagnostic_job(_create_command("create-1"))
    service.start_migration_job(_start_command(created.job.job_id, 1, "start-1"))
    query = ControlTowerQueryService(lambda: SqliteUnitOfWork(connection))

    all_events = query.replay_run_events(created.job.job_id, after_sequence=0, limit=10)
    middle = query.replay_run_events(created.job.job_id, after_sequence=1, limit=10)
    bounded = query.replay_run_events(created.job.job_id, after_sequence=0, limit=2)

    assert [event.sequence for event in all_events] == [1, 2, 3]
    assert [event.sequence for event in middle] == [2, 3]
    assert [event.sequence for event in bounded] == [1, 2]
    assert not isinstance(all_events[0], sqlite3.Row)

    with pytest.raises(InvalidEventCursorError):
        query.replay_run_events(created.job.job_id, after_sequence=99, limit=10)


def test_public_event_cursor_validation() -> None:
    assert parse_public_event_cursor(after_sequence=None, last_event_id=None, latest_sequence=3) == 0
    assert parse_public_event_cursor(after_sequence="2", last_event_id="2", latest_sequence=3) == 2
    assert parse_public_event_cursor(after_sequence=None, last_event_id="2", latest_sequence=3) == 2
    assert parse_public_event_cursor(after_sequence="0", last_event_id="2", latest_sequence=3) == 2

    with pytest.raises(InvalidEventCursorError):
        parse_public_event_cursor(after_sequence="-1", last_event_id=None, latest_sequence=3)
    with pytest.raises(InvalidEventCursorError):
        parse_public_event_cursor(after_sequence="bad", last_event_id=None, latest_sequence=3)
    with pytest.raises(InvalidEventCursorError):
        parse_public_event_cursor(after_sequence="4", last_event_id=None, latest_sequence=3)
    with pytest.raises(EventCursorConflictError):
        parse_public_event_cursor(after_sequence="3", last_event_id="2", latest_sequence=3)


def test_http_event_replay_endpoint_returns_committed_events_only(tmp_path: Path) -> None:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    job_id, etag = _create_job_over_http(client)
    client.post(
        f"/v1/jobs/{job_id}/start",
        json={},
        headers=_mutation_headers(idempotency_key="start-1", if_match=etag),
    )

    response = client.get(f"/v1/jobs/{job_id}/events?after_sequence=1")

    assert response.status_code == 200
    body = response.json()
    assert body["after_sequence"] == 1
    assert body["next_after_sequence"] == 3
    assert [event["sequence"] for event in body["events"]] == [2, 3]
    assert [event["event_type"] for event in body["events"]] == ["command_queued", "job_state_changed"]
    assert "payload_json" not in body["events"][0]
    assert "pid" not in str(body).lower()
    assert "spool" not in str(body).lower()
    assert "stdout" not in str(body).lower()


def test_http_event_replay_rejects_bad_missing_and_conflicting_cursors(tmp_path: Path) -> None:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")

    assert client.get("/v1/jobs/missing/events").status_code == 404

    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    job_id, _etag = _create_job_over_http(client)

    malformed = client.get(f"/v1/jobs/{job_id}/events?after_sequence=not-an-int")
    future = client.get(f"/v1/jobs/{job_id}/events?after_sequence=99")
    conflict = client.get(
        f"/v1/jobs/{job_id}/events?after_sequence=1",
        headers={"Last-Event-ID": "0"},
    )

    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "INVALID_EVENT_CURSOR"
    assert future.status_code == 400
    assert future.json()["error"]["code"] == "INVALID_EVENT_CURSOR"
    assert conflict.status_code == 400
    assert conflict.json()["error"]["code"] == "EVENT_CURSOR_CONFLICT"


def test_http_event_replay_browser_reconnect_uses_last_event_id_over_stale_query(
    tmp_path: Path,
) -> None:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")
    job_id, etag = _create_job_over_http(client)
    client.post(
        f"/v1/jobs/{job_id}/start",
        json={},
        headers=_mutation_headers(idempotency_key="start-1", if_match=etag),
    )

    response = client.get(
        f"/v1/jobs/{job_id}/events?after_sequence=0",
        headers={"Last-Event-ID": "2"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["after_sequence"] == 2
    assert [event["sequence"] for event in body["events"]] == [3]


def test_sse_replays_committed_events_with_persisted_sequence_ids(tmp_path: Path) -> None:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")
    job_id, etag = _create_job_over_http(client)
    client.post(
        f"/v1/jobs/{job_id}/start",
        json={},
        headers=_mutation_headers(idempotency_key="start-1", if_match=etag),
    )

    text = asyncio.run(_collect_sse_frames(connection, job_id, after_sequence=1, stop_after=2))

    assert "id: 2" in text
    assert "event: command_queued" in text
    assert "id: 3" in text
    assert "event: job_state_changed" in text
    assert '"sequence":2' in text
    assert "payload_json" not in text
    assert "spool" not in text.lower()
    assert "pid" not in text.lower()


def test_sse_cursor_conflict_keepalive_and_client_limit(tmp_path: Path) -> None:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    config = EventReplayConfig(
        batch_size=10,
        max_sse_clients=0,
        poll_interval_seconds=0.01,
        keepalive_interval_seconds=0,
        reconnect_delay_ms=1000,
    )
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection), event_replay_config=config), base_url="http://127.0.0.1:8000")
    job_id, _etag = _create_job_over_http(client)

    conflict = client.get(
        f"/v1/jobs/{job_id}/events/stream?after_sequence=1",
        headers={"Last-Event-ID": "0"},
    )
    assert conflict.status_code == 400

    assert client.get(f"/v1/jobs/{job_id}/events/stream?after_sequence=1").status_code == 429
    assert asyncio.run(_collect_keepalive(connection, job_id)) == ": keepalive\n\n"


def test_sse_browser_style_reconnect_resumes_after_last_event_id(tmp_path: Path) -> None:
    connection = _api_test_connection(tmp_path)
    apply_pending_migrations(connection)
    seed_runner_profile_with_roots(connection, artifact_roots(tmp_path))
    seed_pipeline_definition(connection)
    client = TestClient(create_app(lambda: SqliteUnitOfWork(connection)), base_url="http://127.0.0.1:8000")
    job_id, etag = _create_job_over_http(client)
    client.post(
        f"/v1/jobs/{job_id}/start",
        json={},
        headers=_mutation_headers(idempotency_key="start-1", if_match=etag),
    )

    cursor = parse_public_event_cursor(after_sequence="0", last_event_id="2", latest_sequence=3)
    text = asyncio.run(_collect_sse_frames(connection, job_id, after_sequence=cursor, stop_after=1))

    assert "id: 3" in text
    assert "id: 2" not in text


def _seeded_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
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


def _service(connection: sqlite3.Connection) -> DiagnosticJobService:
    return DiagnosticJobService(lambda: SqliteUnitOfWork(connection))


def _create_command(idempotency_key: str) -> CreateDiagnosticJobCommand:
    return CreateDiagnosticJobCommand(
        idempotency_key=idempotency_key,
        runner_profile_id="runner-default",
        runner_profile_version="2026.06",
        pipeline_id="pipeline-default",
        pipeline_version="2026.06",
        legacy_source_root_id="source-root",
        legacy_source_relative_path="src",
        output_root_id="output-root",
        output_relative_path="out",
        target_proof_level=TargetProofLevel.ANALYZED,
        enabled_gates=(),
        policy=RunPolicy(),
    )


def _start_command(job_id: str, version: int, idempotency_key: str) -> StartMigrationJobCommand:
    return StartMigrationJobCommand(
        job_id=job_id,
        expected_version=version,
        idempotency_key=idempotency_key,
        actor_type="user",
        actor_id="tester",
    )


def _create_job_over_http(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/v1/jobs",
        json={
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
        },
        headers=_mutation_headers(idempotency_key="create-1"),
    )
    assert response.status_code == 201
    return str(response.json()["job"]["job_id"]), str(response.headers["etag"])


async def _collect_sse_frames(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    after_sequence: int,
    stop_after: int,
) -> str:
    limiter = SseClientLimiter(1)
    assert await limiter.acquire()
    generator = _event_stream(
        job_id=job_id,
        initial_after_sequence=after_sequence,
        request=_DisconnectingRequest(disconnect_after_checks=10),
        query_service=ControlTowerQueryService(lambda: SqliteUnitOfWork(connection)),
        notifier=PublicEventNotifier(),
        limiter=limiter,
        config=EventReplayConfig(),
    )
    frames: list[str] = []
    try:
        for _ in range(stop_after):
            frames.append(await anext(generator))
    finally:
        await generator.aclose()
    assert limiter.active_clients == 0
    return "".join(frames)


async def _collect_keepalive(connection: sqlite3.Connection, job_id: str) -> str:
    limiter = SseClientLimiter(1)
    assert await limiter.acquire()
    generator = _event_stream(
        job_id=job_id,
        initial_after_sequence=1,
        request=_DisconnectingRequest(disconnect_after_checks=10),
        query_service=ControlTowerQueryService(lambda: SqliteUnitOfWork(connection)),
        notifier=PublicEventNotifier(),
        limiter=limiter,
        config=EventReplayConfig(
            poll_interval_seconds=0.01,
            keepalive_interval_seconds=0,
        ),
    )
    try:
        frame = await anext(generator)
    finally:
        await generator.aclose()
    assert limiter.active_clients == 0
    return frame


class _DisconnectingRequest:
    def __init__(self, *, disconnect_after_checks: int) -> None:
        self._disconnect_after_checks = disconnect_after_checks
        self._checks = 0

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > self._disconnect_after_checks
