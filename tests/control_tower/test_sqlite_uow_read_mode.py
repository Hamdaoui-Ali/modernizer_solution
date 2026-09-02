import asyncio
import sqlite3
from dataclasses import dataclass

from migration_factory.control_tower.adapters.fastapi.app import (
    EventReplayConfig,
    PublicEventNotifier,
    _read_unit_of_work,
    _v2_event_stream,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteControlTowerUnitOfWork,
)


class RecordingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.statements: list[str] = []

    def execute(self, sql, parameters=(), /):
        self.statements.append(str(sql))
        return super().execute(sql, parameters)


def _recording_connection() -> RecordingConnection:
    return sqlite3.connect(
        ":memory:",
        isolation_level=None,
        factory=RecordingConnection,
    )


def test_read_only_uow_does_not_execute_begin_immediate() -> None:
    connection = _recording_connection()
    try:
        with SqliteControlTowerUnitOfWork(connection, transaction_mode="read"):
            pass
    finally:
        connection.close()

    assert "BEGIN IMMEDIATE" not in [statement.upper() for statement in connection.statements]


def test_write_uow_still_executes_begin_immediate() -> None:
    connection = _recording_connection()
    try:
        with SqliteControlTowerUnitOfWork(connection, transaction_mode="write"):
            pass
    finally:
        connection.close()

    assert "BEGIN IMMEDIATE" in [statement.upper() for statement in connection.statements]


def test_read_unit_of_work_helper_switches_to_read_mode() -> None:
    class FakeUow:
        def __init__(self) -> None:
            self.transaction_mode = "write"
            self.entered_mode = ""

        def __enter__(self):
            self.entered_mode = self.transaction_mode
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    fake = FakeUow()
    with _read_unit_of_work(lambda: fake) as uow:
        assert uow.entered_mode == "read"


@dataclass
class _FakeEvent:
    event_id: str = "evt-1"
    job_id: str = "job-1"
    stage: int = 3
    type: str = "stage_completed"
    status: str = "completed"
    message: str = "Stage completed."
    payload_json: str = "{}"
    created_at: str = "2026-06-16T00:00:00Z"
    sequence: int = 1


class _FakeV2Events:
    def list_after_sequence(self, job_id: str, after: int):
        assert job_id == "job-1"
        assert after == 0
        return [_FakeEvent()]


class _FakeStreamUow:
    def __init__(self, entered_modes: list[str]) -> None:
        self.transaction_mode = "write"
        self.entered_modes = entered_modes
        self.v2_events = _FakeV2Events()

    def __enter__(self):
        self.entered_modes.append(self.transaction_mode)
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_v2_event_stream_uses_short_lived_read_uow() -> None:
    entered_modes: list[str] = []

    async def run_once() -> str:
        stream = _v2_event_stream(
            job_id="job-1",
            initial_after_sequence=0,
            request=_ConnectedRequest(),
            unit_of_work_factory=lambda: _FakeStreamUow(entered_modes),
            notifier=PublicEventNotifier(),
            config=EventReplayConfig(),
            once=True,
        )
        return await anext(stream)

    frame = asyncio.run(run_once())

    assert entered_modes == ["read"]
    assert "event: stage_completed" in frame
