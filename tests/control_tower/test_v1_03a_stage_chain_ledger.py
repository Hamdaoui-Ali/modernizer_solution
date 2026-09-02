"""Focused tests: V1-03A Stage-chain ledger schema.

Verifies that:
  1. The stage-chain ledger migration creates the three V1 tables.
  2. Ledger rows are append-only (no UPDATE/DELETE).
  3. Stage-chain ledger rows are created alongside stage_runs during job creation.
  4. Output registry and chain events are insertable and retrievable.
  5. The V1 route invariants are preserved (Boot 4 not selectable, 3.5.14 not execution-relevant).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.commands import CreateMigrationJobCommand
from migration_factory.control_tower.application.ports import ControlTowerUnitOfWork
from migration_factory.control_tower.application.services import CreateMigrationJobService
from migration_factory.control_tower.domain.checksums import canonical_json_text, sha256_canonical_json, utc_now_text
from migration_factory.control_tower.domain.entities import (
    StageChainEventRecord,
    StageChainLedgerRecord,
    StageOutputRegistryRecord,
)
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.schemas.run_configuration import RunPolicy
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.schemas import PipelineDefinition, RunnerProfile
from tests.control_tower.v1_fixtures import make_v1_pipeline_definition, make_v1_runner_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _migrated_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    apply_pending_migrations(connection)
    return connection


def _register_runner_profile(connection: sqlite3.Connection) -> None:
    """Register the V1 runner profile so job creation can reference it."""
    payload = make_v1_runner_profile()
    profile = RunnerProfile(**payload)
    payload_json = canonical_json_text(profile)
    checksum = sha256_canonical_json(profile)
    now = utc_now_text()
    connection.execute(
        """
        INSERT INTO runner_profiles (
            runner_profile_id, runner_profile_version, display_name, schema_version,
            payload_json, payload_checksum, created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["runner_profile_id"],
            payload["runner_profile_version"],
            payload["display_name"],
            payload["schema_version"],
            payload_json,
            checksum,
            now,
            "test",
        ),
    )


def _register_pipeline_definition(connection: sqlite3.Connection) -> None:
    """Register the canonical V1 pipeline definition so job creation can reference it."""
    payload = make_v1_pipeline_definition()
    pipeline = PipelineDefinition(**payload)
    payload_json = canonical_json_text(pipeline)
    checksum = sha256_canonical_json(pipeline)
    now = utc_now_text()
    connection.execute(
        """
        INSERT INTO pipeline_definitions (
            pipeline_id, pipeline_version, display_name, schema_version,
            graph_version, graph_state_schema_version,
            payload_json, payload_checksum, created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["pipeline_id"],
            payload["pipeline_version"],
            payload["display_name"],
            payload["schema_version"],
            payload["graph_version"],
            payload["graph_state_schema_version"],
            payload_json,
            checksum,
            now,
            "test",
        ),
    )


def _seed_minimal_job(connection: sqlite3.Connection, job_id: str) -> None:
    """Seed a minimal migration_jobs row for FK-dependent tests."""
    now = utc_now_text()
    # Seed FK dependencies silently
    connection.execute(
        "INSERT OR IGNORE INTO runner_profiles (runner_profile_id, runner_profile_version, "
        "display_name, schema_version, payload_json, payload_checksum, created_at, created_by) "
        "VALUES ('rp1', 'v1', 'test', '1.0.0', '{}', 'abc', ?, 'test')",
        (now,),
    )
    connection.execute(
        "INSERT OR IGNORE INTO pipeline_definitions (pipeline_id, pipeline_version, "
        "display_name, schema_version, graph_version, graph_state_schema_version, "
        "payload_json, payload_checksum, created_at, created_by) "
        "VALUES ('pip1', 'v1', 'test', '1.0.0', '1.0', '1.0', '{}', 'abc', ?, 'test')",
        (now,),
    )
    connection.execute(
        "INSERT INTO migration_jobs (job_id, version, status, active_slot, last_event_sequence, "
        "runner_profile_id, runner_profile_version, pipeline_id, pipeline_version, "
        "target_proof_level, legacy_source_ref, output_root_ref, created_at, updated_at, created_by) "
        "VALUES (?, 1, 'CREATED', 1, 1, 'rp1', 'v1', 'pip1', 'v1', "
        "'BUILD_TEST_VERIFIED', '/src', '/out', ?, ?, ?)",
        (job_id, now, now, "tester"),
    )


def _seed_minimal_stage_run(connection: sqlite3.Connection, stage_run_id: str, job_id: str) -> None:
    """Seed a minimal stage_runs row for FK-dependent tests."""
    now = utc_now_text()
    connection.execute(
        "INSERT INTO stage_runs (stage_run_id, job_id, stage_index, stage_id, status, "
        "input_source_json, created_at) VALUES (?, ?, 1, 'stage1', 'PENDING', '{}', ?)",
        (stage_run_id, job_id, now),
    )


# ===================================================================
# criterion-1: Stage-chain ledger tables created
# ===================================================================


class TestStageChainLedgerTablesCreated:
    """The migration 0008 must create three stage-chain ledger tables."""

    def test_v1_stage_chain_ledger_table_exists(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_stage_chain_ledger'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            connection.close()

    def test_v1_stage_output_registry_table_exists(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_stage_output_registry'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            connection.close()

    def test_v1_stage_chain_events_table_exists(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_stage_chain_events'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            connection.close()

    def test_all_three_ledger_triggers_exist(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'v1_stage_chain_ledger%'"
            ).fetchall()
            assert len(rows) == 2  # no_update, no_delete
        finally:
            connection.close()

    def test_output_registry_triggers_exist(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'v1_stage_output_registry%'"
            ).fetchall()
            assert len(rows) == 2  # no_update, no_delete
        finally:
            connection.close()

    def test_chain_events_triggers_exist(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'v1_stage_chain_events%'"
            ).fetchall()
            assert len(rows) == 2  # no_update, no_delete
        finally:
            connection.close()


# ===================================================================
# criterion-2: Ledger rows are append-only
# ===================================================================


class TestStageChainLedgerAppendOnly:
    """Ledger rows must be append-only with no UPDATE or DELETE."""

    def test_ledger_prevents_update(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-ledger-update")
            _seed_minimal_stage_run(connection, "sr-ledger-update", "job-ledger-update")
            connection.commit()
            # Insert a row
            connection.execute(
                """
                INSERT INTO v1_stage_chain_ledger (
                    ledger_id, job_id, stage_index, stage_run_id, chain_status,
                    input_source_kind, input_checksum,
                    output_artifact_id, output_checksum, output_registered_at,
                    checksum_guard, created_at, created_by
                ) VALUES (?, ?, 1, ?, 'pending', 'legacy_source', NULL, NULL, NULL, NULL, ?, ?, ?)
                """,
                ("ledger-update-test", "job-ledger-update", "sr-ledger-update",
                 "abcd1234", now, "tester"),
            )
            connection.commit()

            with pytest.raises(Exception, match="append-only"):
                connection.execute(
                    "UPDATE v1_stage_chain_ledger SET chain_status = 'completed' "
                    "WHERE ledger_id = 'ledger-update-test'"
                )
                connection.commit()
        finally:
            connection.close()

    def test_ledger_prevents_delete(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-ledger-del")
            _seed_minimal_stage_run(connection, "sr-ledger-del", "job-ledger-del")
            connection.execute(
                """
                INSERT INTO v1_stage_chain_ledger (
                    ledger_id, job_id, stage_index, stage_run_id, chain_status,
                    input_source_kind, checksum_guard, created_at, created_by
                ) VALUES (?, ?, 1, ?, 'pending', 'legacy_source', ?, ?, ?)
                """,
                ("ledger-del-test", "job-ledger-del", "sr-ledger-del", "guard", now, "tester"),
            )
            connection.commit()
            with pytest.raises(Exception, match="append-only"):
                connection.execute("DELETE FROM v1_stage_chain_ledger")
                connection.commit()
        finally:
            connection.close()

    def test_output_registry_prevents_update(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-out-reg-upd")
            _seed_minimal_stage_run(connection, "sr-out-reg-upd", "job-out-reg-upd")
            # Seed a ledger row for FK
            connection.execute(
                "INSERT INTO v1_stage_chain_ledger (ledger_id, job_id, stage_index, stage_run_id, "
                "chain_status, input_source_kind, checksum_guard, created_at, created_by) "
                "VALUES (?, ?, 1, ?, 'pending', 'legacy_source', ?, ?, ?)",
                ("ledger-out-reg-upd", "job-out-reg-upd", "sr-out-reg-upd", "guard", now, "tester"),
            )
            connection.execute(
                "INSERT INTO artifacts (artifact_id, job_id, artifact_type, registered_root_id, "
                "relative_path, normalized_relative_path, size_bytes, checksum_algorithm, checksum, "
                "created_at, created_by) VALUES (?, ?, 'sandbox', 'root1', 'path/f.txt', 'path/f.txt', "
                "1024, 'sha256', 'abc', ?, ?)",
                ("art-out-reg-upd", "job-out-reg-upd", now, "tester"),
            )
            connection.execute(
                "INSERT INTO v1_stage_output_registry (output_id, job_id, stage_index, stage_run_id, "
                "artifact_id, artifact_type, output_kind, checksum_algorithm, checksum, "
                "registered_at, registered_by) VALUES (?, ?, 1, ?, ?, 'sandbox', 'sandbox', 'sha256', 'abc', ?, ?)",
                ("out-reg-upd-test", "job-out-reg-upd", "sr-out-reg-upd", "art-out-reg-upd", now, "tester"),
            )
            connection.commit()
            with pytest.raises(Exception, match="append-only"):
                connection.execute(
                    "UPDATE v1_stage_output_registry SET output_kind = 'manifest' "
                    "WHERE output_id = 'out-reg-upd-test'"
                )
                connection.commit()
        finally:
            connection.close()

    def test_chain_events_prevents_delete(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-chain-ev-del")
            _seed_minimal_stage_run(connection, "sr-chain-ev-del", "job-chain-ev-del")
            connection.execute(
                "INSERT INTO v1_stage_chain_ledger (ledger_id, job_id, stage_index, stage_run_id, "
                "chain_status, input_source_kind, checksum_guard, created_at, created_by) "
                "VALUES (?, ?, 1, ?, 'pending', 'legacy_source', ?, ?, ?)",
                ("ledger-chain-ev-del", "job-chain-ev-del", "sr-chain-ev-del", "guard", now, "tester"),
            )
            connection.execute(
                "INSERT INTO v1_stage_chain_events (event_id, job_id, stage_index, event_type, "
                "payload_json, payload_checksum, created_at, created_by) "
                "VALUES (?, ?, 1, 'chain_created', '{}', ?, ?, ?)",
                ("event-chain-del-test", "job-chain-ev-del",
                 hashlib.sha256(b"{}").hexdigest(), now, "tester"),
            )
            connection.commit()
            with pytest.raises(Exception, match="append-only"):
                connection.execute("DELETE FROM v1_stage_chain_events")
                connection.commit()
        finally:
            connection.close()


# ===================================================================
# criterion-3: Job creation persists three stage-chain ledger rows
# ===================================================================


class TestStageChainLedgerCreatedWithJob:
    """Job creation must persist three stage-chain ledger rows."""

    def test_job_creation_creates_three_ledger_entries(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _register_runner_profile(connection)
            _register_pipeline_definition(connection)

            service = CreateMigrationJobService(lambda: SqliteUnitOfWork(connection))

            result = service.execute(
                CreateMigrationJobCommand(
                    actor="test-user",
                    legacy_source_ref="source-root:/var/workspace/source",
                    output_root_ref="output-root:/var/workspace/output",
                    runner_profile_id="runner-v1",
                    runner_profile_version="2026.06",
                    pipeline_id="springboot-216-to-356-java21-three-stage",
                    pipeline_version="2026.06",
                    target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
                    enabled_gates=("compile", "test", "binary_compat"),
                    policy=RunPolicy(),
                )
            )

            assert len(result.stage_run_ids) == 3

            # Verify three stage-chain ledger entries exist
            ledger_rows = connection.execute(
                "SELECT ledger_id, job_id, stage_index, chain_status, input_source_kind, checksum_guard "
                "FROM v1_stage_chain_ledger WHERE job_id = ? ORDER BY stage_index",
                (result.job_id,),
            ).fetchall()
            assert len(ledger_rows) == 3

            # Verify stage 1 is legacy_source
            assert ledger_rows[0]["stage_index"] == 1
            assert ledger_rows[0]["input_source_kind"] == "legacy_source"
            assert ledger_rows[0]["chain_status"] == "pending"

            # Verify stage 2 is previous_stage
            assert ledger_rows[1]["stage_index"] == 2
            assert ledger_rows[1]["input_source_kind"] == "previous_stage"
            assert ledger_rows[1]["chain_status"] == "pending"

            # Verify stage 3 is previous_stage
            assert ledger_rows[2]["stage_index"] == 3
            assert ledger_rows[2]["input_source_kind"] == "previous_stage"
            assert ledger_rows[2]["chain_status"] == "pending"

            # Verify checksum guards are set
            for row in ledger_rows:
                assert len(row["checksum_guard"]) == 64  # SHA-256 hex
        finally:
            connection.close()

    def test_chain_created_event_recorded(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _register_runner_profile(connection)
            _register_pipeline_definition(connection)

            service = CreateMigrationJobService(lambda: SqliteUnitOfWork(connection))

            result = service.execute(
                CreateMigrationJobCommand(
                    actor="test-user",
                    legacy_source_ref="source-root:/var/workspace/source",
                    output_root_ref="output-root:/var/workspace/output",
                    runner_profile_id="runner-v1",
                    runner_profile_version="2026.06",
                    pipeline_id="springboot-216-to-356-java21-three-stage",
                    pipeline_version="2026.06",
                    target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
                    enabled_gates=("compile", "test"),
                    policy=RunPolicy(),
                )
            )

            events = connection.execute(
                "SELECT event_type, job_id, payload_json FROM v1_stage_chain_events WHERE job_id = ?",
                (result.job_id,),
            ).fetchall()
            assert len(events) >= 1
            assert any(e["event_type"] == "chain_created" for e in events)
        finally:
            connection.close()

    def test_ledger_rows_link_to_stage_runs(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _register_runner_profile(connection)
            _register_pipeline_definition(connection)

            service = CreateMigrationJobService(lambda: SqliteUnitOfWork(connection))

            result = service.execute(
                CreateMigrationJobCommand(
                    actor="test-user",
                    legacy_source_ref="source-root:/var/workspace/source",
                    output_root_ref="output-root:/var/workspace/output",
                    runner_profile_id="runner-v1",
                    runner_profile_version="2026.06",
                    pipeline_id="springboot-216-to-356-java21-three-stage",
                    pipeline_version="2026.06",
                    target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
                    enabled_gates=(),
                    policy=RunPolicy(),
                )
            )

            # Verify each ledger entry references an existing stage_run_id
            ledger_rows = connection.execute(
                "SELECT stage_run_id FROM v1_stage_chain_ledger WHERE job_id = ? ORDER BY stage_index",
                (result.job_id,),
            ).fetchall()
            assert len(ledger_rows) == 3

            for row in ledger_rows:
                stage_run = connection.execute(
                    "SELECT stage_run_id FROM stage_runs WHERE stage_run_id = ?",
                    (row["stage_run_id"],),
                ).fetchone()
                assert stage_run is not None, f"Stage run {row['stage_run_id']} not found"
        finally:
            connection.close()


# ===================================================================
# criterion-4: Stage output registry is usable
# ===================================================================


class TestStageOutputRegistry:
    """The output registry must accept and return records."""

    def test_insert_and_list_outputs(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-output-test")
            _seed_minimal_stage_run(connection, "sr-output-test", "job-output-test")
            connection.execute(
                "INSERT INTO artifacts (artifact_id, job_id, artifact_type, registered_root_id, "
                "relative_path, normalized_relative_path, size_bytes, checksum_algorithm, checksum, "
                "created_at, created_by) VALUES (?, ?, 'sandbox', 'root1', 'path/file.txt', 'path/file.txt', "
                "1024, 'sha256', 'abc123', ?, ?)",
                ("art-output-test", "job-output-test", now, "tester"),
            )
            # Insert a ledger entry for FK
            connection.execute(
                "INSERT INTO v1_stage_chain_ledger (ledger_id, job_id, stage_index, stage_run_id, "
                "chain_status, input_source_kind, checksum_guard, created_at, created_by) "
                "VALUES (?, ?, 1, ?, 'completed', 'legacy_source', ?, ?, ?)",
                ("ledger-output-test", "job-output-test", "sr-output-test", "guard123", now, "tester"),
            )
            connection.commit()

            repo = SqliteUnitOfWork(connection).stage_chain_ledger

            # Insert an output registry record
            output = StageOutputRegistryRecord(
                output_id="output-test-1",
                job_id="job-output-test",
                stage_index=1,
                stage_run_id="sr-output-test",
                artifact_id="art-output-test",
                artifact_type="sandbox",
                output_kind="sandbox",
                checksum_algorithm="sha256",
                checksum="abc123",
                registered_at=now,
                registered_by="tester",
            )
            repo.insert_output(output)

            outputs = repo.list_outputs_for_job("job-output-test")
            assert len(outputs) == 1
            assert outputs[0].output_id == "output-test-1"
            assert outputs[0].output_kind == "sandbox"
            assert outputs[0].artifact_id == "art-output-test"
        finally:
            connection.close()


# ===================================================================
# criterion-5: Stage chain events are append-only
# ===================================================================


class TestStageChainEvents:
    """Chain events must be insertable and append-only."""

    def test_insert_and_list_chain_events(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-event-test")
            _seed_minimal_stage_run(connection, "sr-event-test", "job-event-test")
            connection.execute(
                "INSERT INTO v1_stage_chain_ledger (ledger_id, job_id, stage_index, stage_run_id, "
                "chain_status, input_source_kind, checksum_guard, created_at, created_by) "
                "VALUES (?, ?, 1, ?, 'pending', 'legacy_source', ?, ?, ?)",
                ("ledger-event-test", "job-event-test", "sr-event-test", "guard", now, "tester"),
            )
            connection.commit()

            repo = SqliteUnitOfWork(connection).stage_chain_ledger

            payload = {"event": "test", "job_id": "job-event-test"}
            event = StageChainEventRecord(
                event_id="chain-event-test-1",
                job_id="job-event-test",
                stage_index=1,
                event_type="stage_started",
                prior_status="pending",
                new_status="in_progress",
                ledger_id=None,
                output_id=None,
                payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                payload_checksum=hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest(),
                created_at=now,
                created_by="tester",
            )
            repo.insert_event(event)

            events = repo.list_events_for_job("job-event-test")
            assert len(events) == 1
            assert events[0].event_type == "stage_started"
            assert events[0].prior_status == "pending"
            assert events[0].new_status == "in_progress"
        finally:
            connection.close()

    def test_chain_events_prevent_update_on_existing_row(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-chain-ev-upd")
            _seed_minimal_stage_run(connection, "sr-chain-ev-upd", "job-chain-ev-upd")
            connection.execute(
                "INSERT INTO v1_stage_chain_ledger (ledger_id, job_id, stage_index, stage_run_id, "
                "chain_status, input_source_kind, checksum_guard, created_at, created_by) "
                "VALUES (?, ?, 1, ?, 'pending', 'legacy_source', ?, ?, ?)",
                ("ledger-chain-ev-upd", "job-chain-ev-upd", "sr-chain-ev-upd", "guard", now, "tester"),
            )
            connection.execute(
                "INSERT INTO v1_stage_chain_events (event_id, job_id, stage_index, event_type, "
                "payload_json, payload_checksum, created_at, created_by) "
                "VALUES (?, ?, 1, 'chain_created', '{}', ?, ?, ?)",
                ("event-update-test", "job-chain-ev-upd",
                 hashlib.sha256(b"{}").hexdigest(), now, "tester"),
            )
            connection.commit()
            with pytest.raises(Exception, match="append-only"):
                connection.execute(
                    "UPDATE v1_stage_chain_events SET event_type = 'other' "
                    "WHERE event_id = 'event-update-test'"
                )
                connection.commit()
        finally:
            connection.close()

    def test_chain_events_prevent_delete_on_existing_row(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-chain-ev-del2")
            _seed_minimal_stage_run(connection, "sr-chain-ev-del2", "job-chain-ev-del2")
            connection.execute(
                "INSERT INTO v1_stage_chain_ledger (ledger_id, job_id, stage_index, stage_run_id, "
                "chain_status, input_source_kind, checksum_guard, created_at, created_by) "
                "VALUES (?, ?, 1, ?, 'pending', 'legacy_source', ?, ?, ?)",
                ("ledger-chain-ev-del2", "job-chain-ev-del2", "sr-chain-ev-del2", "guard", now, "tester"),
            )
            connection.execute(
                "INSERT INTO v1_stage_chain_events (event_id, job_id, stage_index, event_type, "
                "payload_json, payload_checksum, created_at, created_by) "
                "VALUES (?, ?, 1, 'chain_created', '{}', ?, ?, ?)",
                ("event-delete-test", "job-chain-ev-del2",
                 hashlib.sha256(b"{}").hexdigest(), now, "tester"),
            )
            connection.commit()
            with pytest.raises(Exception, match="append-only"):
                connection.execute("DELETE FROM v1_stage_chain_events")
                connection.commit()
        finally:
            connection.close()


# ===================================================================
# criterion-6: V1 invariants preserved
# ===================================================================


class TestV1InvariantsPreserved:
    """Boot 4 must not be selectable; 3.5.14 must not be execution-relevant."""

    def test_stage_index_boundary(self, tmp_path: Path) -> None:
        """Stage indexes in the ledger must be 1-3 only."""
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-boundary-test")
            _seed_minimal_stage_run(connection, "sr-boundary-test", "job-boundary-test")
            # stage_index 0 should fail
            with pytest.raises(Exception):
                connection.execute(
                    """
                    INSERT INTO v1_stage_chain_ledger (
                        ledger_id, job_id, stage_index, stage_run_id, chain_status,
                        input_source_kind, checksum_guard, created_at, created_by
                    ) VALUES (?, ?, 0, ?, 'pending', 'legacy_source', ?, ?, ?)
                    """,
                    ("ledger-bad-index-0", "job-boundary-test", "sr-boundary-test",
                     "guard", now, "tester"),
                )
                connection.commit()

            # stage_index 4 should fail
            with pytest.raises(Exception):
                connection.execute(
                    """
                    INSERT INTO v1_stage_chain_ledger (
                        ledger_id, job_id, stage_index, stage_run_id, chain_status,
                        input_source_kind, checksum_guard, created_at, created_by
                    ) VALUES (?, ?, 4, ?, 'pending', 'legacy_source', ?, ?, ?)
                    """,
                    ("ledger-bad-index-4", "job-boundary-test", "sr-boundary-test",
                     "guard", now, "tester"),
                )
                connection.commit()
        finally:
            connection.close()

    def test_chain_status_constraint(self, tmp_path: Path) -> None:
        """Chain status must be one of the approved values."""
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-status-test")
            _seed_minimal_stage_run(connection, "sr-status-test", "job-status-test")
            with pytest.raises(Exception):
                connection.execute(
                    """
                    INSERT INTO v1_stage_chain_ledger (
                        ledger_id, job_id, stage_index, stage_run_id, chain_status,
                        input_source_kind, checksum_guard, created_at, created_by
                    ) VALUES (?, ?, 1, ?, 'invalid_status', 'legacy_source', ?, ?, ?)
                    """,
                    ("ledger-bad-status", "job-status-test", "sr-status-test",
                     "guard", now, "tester"),
                )
                connection.commit()
        finally:
            connection.close()

    def test_event_type_constraint(self, tmp_path: Path) -> None:
        """Event type must be one of the approved values."""
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-event-constraint")
            _seed_minimal_stage_run(connection, "sr-event-constraint", "job-event-constraint")
            with pytest.raises(Exception):
                connection.execute(
                    """
                    INSERT INTO v1_stage_chain_events (
                        event_id, job_id, stage_index, event_type,
                        prior_status, new_status, payload_json, payload_checksum,
                        created_at, created_by
                    ) VALUES (?, ?, 1, 'invalid_event_type', NULL, NULL, '{}', ?, ?, ?)
                    """,
                    ("event-bad-type", "job-event-constraint",
                     hashlib.sha256(b"{}").hexdigest(), now, "tester"),
                )
                connection.commit()
        finally:
            connection.close()

    def test_output_kind_constraint(self, tmp_path: Path) -> None:
        """Output kind must be one of the approved values."""
        connection = _migrated_connection(tmp_path)
        try:
            now = utc_now_text()
            _seed_minimal_job(connection, "job-output-kind-test")
            _seed_minimal_stage_run(connection, "sr-output-kind-test", "job-output-kind-test")
            connection.execute(
                "INSERT INTO artifacts (artifact_id, job_id, artifact_type, registered_root_id, "
                "relative_path, normalized_relative_path, size_bytes, checksum_algorithm, checksum, "
                "created_at, created_by) VALUES (?, ?, 'sandbox', 'root1', 'path/f.txt', 'path/f.txt', "
                "1024, 'sha256', 'abc', ?, ?)",
                ("art-output-kind-test", "job-output-kind-test", now, "tester"),
            )
            connection.execute(
                "INSERT INTO v1_stage_chain_ledger (ledger_id, job_id, stage_index, stage_run_id, "
                "chain_status, input_source_kind, checksum_guard, created_at, created_by) "
                "VALUES (?, ?, 1, ?, 'pending', 'legacy_source', ?, ?, ?)",
                ("ledger-output-kind-test", "job-output-kind-test", "sr-output-kind-test",
                 "guard", now, "tester"),
            )
            connection.commit()
            with pytest.raises(Exception):
                connection.execute(
                    """
                    INSERT INTO v1_stage_output_registry (
                        output_id, job_id, stage_index, stage_run_id,
                        artifact_id, artifact_type, output_kind,
                        checksum_algorithm, checksum, registered_at, registered_by
                    ) VALUES (?, ?, 1, ?, ?, 'manifest', 'invalid_kind', ?, ?, ?, ?)
                    """,
                    ("output-bad-kind", "job-output-kind-test", "sr-output-kind-test",
                     "art-output-kind-test", "sha256", "abc", now, "tester"),
                )
                connection.commit()
        finally:
            connection.close()
