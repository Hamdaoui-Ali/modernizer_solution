from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.commands import RegisterPipelineDefinitionCommand
from migration_factory.control_tower.application.dto import PipelineDefinitionDto
from migration_factory.control_tower.application.services import ControlTowerRegistrationService
from migration_factory.control_tower.domain.checksums import canonical_json
from migration_factory.control_tower.domain.errors import RegistrationConflictError
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower._helpers import pipeline_definition_payload


def test_valid_pipeline_definition_can_be_registered(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)

        pipeline = service.register_pipeline_definition(_register_command())

        assert pipeline.pipeline_id == "pipeline-default"
        assert pipeline.pipeline_version == "v1"
        assert pipeline.created_by == "tester"
    finally:
        connection.close()


def test_registered_pipeline_definition_can_be_read_by_id_and_version(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        registered = service.register_pipeline_definition(_register_command())

        fetched = service.get_pipeline_definition("pipeline-default", "v1")

        assert fetched == registered
    finally:
        connection.close()


def test_registered_pipeline_definitions_can_be_listed(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_pipeline_definition(_register_command())

        pipelines = service.list_pipeline_definitions()

        assert len(pipelines) == 1
        assert pipelines[0].pipeline_id == "pipeline-default"
    finally:
        connection.close()


def test_persisted_pipeline_payload_json_is_canonical(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        registered = service.register_pipeline_definition(_register_command())
        row = _pipeline_row(connection)

        assert row["payload_json"] == canonical_json(registered.payload)
    finally:
        connection.close()


def test_pipeline_checksum_equals_sha256_of_canonical_payload_json(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_pipeline_definition(_register_command())
        row = _pipeline_row(connection)

        expected = hashlib.sha256(str(row["payload_json"]).encode("utf-8")).hexdigest()
        assert row["payload_checksum"] == expected
    finally:
        connection.close()


def test_pipeline_checksum_excludes_persistence_metadata(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        registered = service.register_pipeline_definition(_register_command())

        assert "created_at" not in registered.payload
        assert "created_by" not in registered.payload
        assert "payload_checksum" not in registered.payload
    finally:
        connection.close()


def test_same_pipeline_id_version_and_checksum_is_idempotent(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        first = service.register_pipeline_definition(_register_command(actor_id="creator"))

        second = service.register_pipeline_definition(_register_command(actor_id="other"))

        assert second == first
        assert _audit_count(connection) == 1
    finally:
        connection.close()


def test_same_pipeline_id_version_with_different_checksum_is_rejected(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_pipeline_definition(_register_command())
        changed = _pipeline_payload()
        changed["display_name"] = "Changed pipeline"

        with pytest.raises(RegistrationConflictError):
            service.register_pipeline_definition(_register_command(pipeline=changed))
    finally:
        connection.close()


def test_idempotent_pipeline_registration_does_not_overwrite_created_metadata(
    tmp_path: Path,
) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        first = service.register_pipeline_definition(_register_command(actor_id="creator"))

        second = service.register_pipeline_definition(_register_command(actor_id="other"))

        assert second.created_at == first.created_at
        assert second.created_by == "creator"
    finally:
        connection.close()


def test_pipeline_registration_writes_global_audit_record(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        registered = service.register_pipeline_definition(
            _register_command(correlation_id="corr-1", causation_id="cause-1")
        )
        audit = _audit_row(connection)
        audit_payload = json.loads(str(audit["payload_json"]))

        assert audit["job_id"] is None
        assert audit["action"] == "pipeline_definition_registered"
        assert audit_payload["action"] == "pipeline_definition_registered"
        assert audit_payload["registration_type"] == "pipeline_definition"
        assert audit_payload["id"] == registered.pipeline_id
        assert audit_payload["version"] == registered.pipeline_version
        assert audit_payload["checksum"] == registered.payload_checksum
        assert audit_payload["actor_id"] == "tester"
        assert audit_payload["correlation_id"] == "corr-1"
        assert audit_payload["causation_id"] == "cause-1"
    finally:
        connection.close()


def test_pipeline_registration_creates_no_run_event(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_pipeline_definition(_register_command())

        assert _run_event_count(connection) == 0
    finally:
        connection.close()


def test_pipeline_queries_return_dtos_not_sqlite_rows(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        service = _service(connection)
        service.register_pipeline_definition(_register_command())

        fetched = service.get_pipeline_definition("pipeline-default", "v1")

        assert isinstance(fetched, PipelineDefinitionDto)
        assert not isinstance(fetched, sqlite3.Row)
    finally:
        connection.close()


def _service(connection: sqlite3.Connection) -> ControlTowerRegistrationService:
    return ControlTowerRegistrationService(lambda: SqliteUnitOfWork(connection))


def _migrated_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    apply_pending_migrations(connection)
    return connection


def _register_command(
    *,
    pipeline: dict | None = None,
    actor_id: str = "tester",
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> RegisterPipelineDefinitionCommand:
    return RegisterPipelineDefinitionCommand(
        pipeline=_pipeline_payload() if pipeline is None else pipeline,
        actor_type="user",
        actor_id=actor_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def _pipeline_payload() -> dict:
    payload = pipeline_definition_payload()
    payload["pipeline_version"] = "v1"
    return payload


def _pipeline_row(connection: sqlite3.Connection) -> sqlite3.Row:
    return connection.execute("SELECT * FROM pipeline_definitions").fetchone()


def _audit_row(connection: sqlite3.Connection) -> sqlite3.Row:
    return connection.execute("SELECT * FROM audit_records").fetchone()


def _audit_count(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0])


def _run_event_count(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM run_events").fetchone()[0])
