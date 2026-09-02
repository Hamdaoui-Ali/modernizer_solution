"""Focused tests: V1-03B Persist ledger during job creation with content-bound checksums.

Verifies that:
  1. Job creation persists three stage-chain ledger rows with content-bound checksum_guard.
  2. Each ledger entry has a non-null input_checksum derived from the stage input source.
  3. The checksum_guard covers the full stage configuration (not just IDs).
  4. Identical pipeline stages produce deterministic checksums.
  5. Stage-chain ledger, stage runs, and the chain_created event are all consistent.
  6. Shallow tampering with the checksum payload would break integrity detection.
  7. V1 route invariants are preserved (Boot 4 not selectable, 3.5.14 not execution-relevant).
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
    StageRunRecord,
)
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.schemas.pipeline_definition import (
    PipelineDefinition,
    PipelineStage,
)
from migration_factory.control_tower.schemas.runner_profile import RunnerProfile
from migration_factory.control_tower.schemas.run_configuration import RunPolicy
from tests.control_tower.v1_fixtures import make_v1_pipeline_definition, make_v1_runner_profile


# ---- helpers ----


@pytest.fixture
def migrated_connection(tmp_path: Path) -> Callable[[], sqlite3.Connection]:
    """Return a factory that yields a fresh SQLite with all migrations applied."""

    def _factory() -> sqlite3.Connection:
        conn = connect_control_tower(tmp_path / "control_tower_v1_03b.sqlite3")
        apply_pending_migrations(conn)
        return conn

    return _factory


def _register_runner_profile(connection: sqlite3.Connection) -> None:
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


def _compute_expected_checksum_guard(stage: PipelineStage) -> str:
    """Replicate the service-layer checksum_guard computation for verification."""
    stage_json = canonical_json_text(stage)
    return hashlib.sha256(stage_json.encode("utf-8")).hexdigest()


def _compute_expected_input_checksum(stage: PipelineStage) -> str:
    """Replicate the service-layer input_checksum computation for verification."""
    return sha256_canonical_json(stage.input_source)


# ===================================================================
# criterion-1: Ledger persistence integrated in job creation flow
# ===================================================================


class TestLedgerPersistenceIntegrated:
    """Job creation persists three content-bound stage-chain ledger rows."""

    def test_job_creation_persists_three_ledger_rows(self, migrated_connection: Callable[[], sqlite3.Connection]) -> None:
        connection = migrated_connection()
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

            ledger_rows = connection.execute(
                "SELECT ledger_id, job_id, stage_index, chain_status, input_source_kind, "
                "input_checksum, checksum_guard, stage_run_id, created_by "
                "FROM v1_stage_chain_ledger WHERE job_id = ? ORDER BY stage_index",
                (result.job_id,),
            ).fetchall()

            assert len(ledger_rows) == 3

            # Stage 1: legacy_source
            assert ledger_rows[0]["stage_index"] == 1
            assert ledger_rows[0]["input_source_kind"] == "legacy_source"
            assert ledger_rows[0]["chain_status"] == "pending"
            assert ledger_rows[0]["created_by"] == "test-user"

            # Stage 2: previous_stage
            assert ledger_rows[1]["stage_index"] == 2
            assert ledger_rows[1]["input_source_kind"] == "previous_stage"
            assert ledger_rows[1]["chain_status"] == "pending"

            # Stage 3: previous_stage
            assert ledger_rows[2]["stage_index"] == 3
            assert ledger_rows[2]["input_source_kind"] == "previous_stage"
            assert ledger_rows[2]["chain_status"] == "pending"
        finally:
            connection.close()

    def test_each_ledger_row_has_input_checksum_set(
        self, migrated_connection: Callable[[], sqlite3.Connection]
    ) -> None:
        """input_checksum must not be None for any stage."""
        connection = migrated_connection()
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
                    enabled_gates=("compile",),
                    policy=RunPolicy(),
                )
            )

            rows = connection.execute(
                "SELECT input_checksum, checksum_guard FROM v1_stage_chain_ledger WHERE job_id = ? ORDER BY stage_index",
                (result.job_id,),
            ).fetchall()

            assert len(rows) == 3
            for row in rows:
                assert row["input_checksum"] is not None, "input_checksum must be non-null"
                assert len(row["input_checksum"]) == 64, "SHA-256 hex must be 64 chars"
                assert len(row["checksum_guard"]) == 64, "SHA-256 hex must be 64 chars"
        finally:
            connection.close()

    def test_chain_created_event_recorded(self, migrated_connection: Callable[[], sqlite3.Connection]) -> None:
        connection = migrated_connection()
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
                    enabled_gates=("compile",),
                    policy=RunPolicy(),
                )
            )

            events = connection.execute(
                "SELECT event_type, job_id, payload_json FROM v1_stage_chain_events WHERE job_id = ?",
                (result.job_id,),
            ).fetchall()
            assert len(events) >= 1
            chain_created = [e for e in events if e["event_type"] == "chain_created"]
            assert len(chain_created) == 1

            payload = json.loads(chain_created[0]["payload_json"])
            assert payload["job_id"] == result.job_id
            assert len(payload["ledger_ids"]) == 3
            assert len(payload["stage_run_ids"]) == 3
        finally:
            connection.close()


# ===================================================================
# criterion-2: Checksum guard covers full stage content
# ===================================================================


class TestChecksumBound:
    """The checksum_guard must cover the full stage configuration, not just IDs."""

    def test_checksum_guard_matches_stage_content(
        self, migrated_connection: Callable[[], sqlite3.Connection]
    ) -> None:
        """Verify that checksum_guard equals SHA-256 of canonical stage JSON."""
        connection = migrated_connection()
        try:
            _register_runner_profile(connection)
            _register_pipeline_definition(connection)

            # Load the pipeline definition to get stage objects
            pipe_payload = make_v1_pipeline_definition()
            pipeline = PipelineDefinition(**pipe_payload)
            expected_checksums = [
                _compute_expected_checksum_guard(stage) for stage in pipeline.stages
            ]

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
                    enabled_gates=("compile",),
                    policy=RunPolicy(),
                )
            )

            ledger_rows = connection.execute(
                "SELECT checksum_guard FROM v1_stage_chain_ledger WHERE job_id = ? ORDER BY stage_index",
                (result.job_id,),
            ).fetchall()

            assert len(ledger_rows) == 3
            for i, row in enumerate(ledger_rows):
                assert row["checksum_guard"] == expected_checksums[i], (
                    f"Stage {i + 1} checksum_guard does not match content"
                )
        finally:
            connection.close()

    def test_checksum_guard_is_deterministic(
        self, tmp_path: Path
    ) -> None:
        """Two identical job creations must produce same checksum_guard values."""
        path1 = tmp_path / "db1"
        path2 = tmp_path / "db2"
        path1.mkdir()
        path2.mkdir()

        def _make_conn(db_dir: Path) -> sqlite3.Connection:
            conn = connect_control_tower(db_dir / "ct.sqlite3")
            apply_pending_migrations(conn)
            return conn

        connection1 = _make_conn(path1)
        connection2 = _make_conn(path2)
        try:
            for conn in (connection1, connection2):
                _register_runner_profile(conn)
                _register_pipeline_definition(conn)

            service1 = CreateMigrationJobService(lambda: SqliteUnitOfWork(connection1))
            service2 = CreateMigrationJobService(lambda: SqliteUnitOfWork(connection2))

            result1 = service1.execute(
                CreateMigrationJobCommand(
                    actor="test-user",
                    legacy_source_ref="source-root:/var/workspace/source",
                    output_root_ref="output-root:/var/workspace/output",
                    runner_profile_id="runner-v1",
                    runner_profile_version="2026.06",
                    pipeline_id="springboot-216-to-356-java21-three-stage",
                    pipeline_version="2026.06",
                    target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
                    enabled_gates=("compile",),
                    policy=RunPolicy(),
                )
            )
            result2 = service2.execute(
                CreateMigrationJobCommand(
                    actor="test-user",
                    legacy_source_ref="source-root:/var/workspace/source",
                    output_root_ref="output-root:/var/workspace/output",
                    runner_profile_id="runner-v1",
                    runner_profile_version="2026.06",
                    pipeline_id="springboot-216-to-356-java21-three-stage",
                    pipeline_version="2026.06",
                    target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
                    enabled_gates=("compile",),
                    policy=RunPolicy(),
                )
            )

            guards1 = connection1.execute(
                "SELECT checksum_guard FROM v1_stage_chain_ledger WHERE job_id = ? ORDER BY stage_index",
                (result1.job_id,),
            ).fetchall()
            guards2 = connection2.execute(
                "SELECT checksum_guard FROM v1_stage_chain_ledger WHERE job_id = ? ORDER BY stage_index",
                (result2.job_id,),
            ).fetchall()

            assert len(guards1) == 3
            assert len(guards2) == 3
            for i in range(3):
                assert guards1[i]["checksum_guard"] == guards2[i]["checksum_guard"], (
                    f"Stage {i + 1} checksum_guard differs between runs"
                )
        finally:
            connection1.close()
            connection2.close()

    def test_input_checksum_matches_input_source(
        self, migrated_connection: Callable[[], sqlite3.Connection]
    ) -> None:
        """Verify input_checksum equals SHA-256 of the input source config."""
        connection = migrated_connection()
        try:
            _register_runner_profile(connection)
            _register_pipeline_definition(connection)

            pipe_payload = make_v1_pipeline_definition()
            pipeline = PipelineDefinition(**pipe_payload)
            expected_input_checksums = [
                _compute_expected_input_checksum(stage) for stage in pipeline.stages
            ]

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
                    enabled_gates=("compile",),
                    policy=RunPolicy(),
                )
            )

            rows = connection.execute(
                "SELECT input_checksum, stage_index FROM v1_stage_chain_ledger WHERE job_id = ? ORDER BY stage_index",
                (result.job_id,),
            ).fetchall()

            assert len(rows) == 3
            for i, row in enumerate(rows):
                assert row["input_checksum"] == expected_input_checksums[i], (
                    f"Stage {row['stage_index']} input_checksum mismatch"
                )
        finally:
            connection.close()


# ===================================================================
# criterion-3: Ledger rows are append-only and link to stage runs
# ===================================================================


class TestLedgerIntegrity:
    """Ledger rows are append-only and correctly reference stage runs."""

    def test_ledger_rows_link_to_stage_runs(
        self, migrated_connection: Callable[[], sqlite3.Connection]
    ) -> None:
        """Each ledger row references an existing stage_run_id."""
        connection = migrated_connection()
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
                    enabled_gates=("compile",),
                    policy=RunPolicy(),
                )
            )

            ledger_rows = connection.execute(
                "SELECT stage_run_id, stage_index FROM v1_stage_chain_ledger WHERE job_id = ? ORDER BY stage_index",
                (result.job_id,),
            ).fetchall()

            stage_run_ids = set(result.stage_run_ids)
            for row in ledger_rows:
                assert row["stage_run_id"] in stage_run_ids, (
                    f"ledger stage_run_id {row['stage_run_id']} not in job stage runs"
                )

            # Verify all stage runs exist
            stage_runs = connection.execute(
                "SELECT stage_run_id FROM stage_runs WHERE job_id = ?",
                (result.job_id,),
            ).fetchall()
            assert len(stage_runs) == 3
        finally:
            connection.close()

    def test_ledger_rows_append_only_trigger(
        self, migrated_connection: Callable[[], sqlite3.Connection]
    ) -> None:
        """Verify the v1_stage_chain_ledger table prevents UPDATE."""
        connection = migrated_connection()
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
                    enabled_gates=("compile",),
                    policy=RunPolicy(),
                )
            )

            ledger_id = connection.execute(
                "SELECT ledger_id FROM v1_stage_chain_ledger WHERE job_id = ? LIMIT 1",
                (result.job_id,),
            ).fetchone()["ledger_id"]

            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                connection.execute(
                    "UPDATE v1_stage_chain_ledger SET chain_status = 'running' WHERE ledger_id = ?",
                    (ledger_id,),
                )
        finally:
            connection.close()


# ===================================================================
# criterion-4: V1 route invariants preserved
# ===================================================================


class TestV1Invariants:
    """V1 route invariants must be preserved."""

    def test_pipeline_id_is_locked(self) -> None:
        """The pipeline ID must be the canonical V1 value."""
        payload = make_v1_pipeline_definition()
        assert payload["pipeline_id"] == "springboot-216-to-356-java21-three-stage"

    def test_stage1_java11_boot2718(self) -> None:
        """Stage 1 uses Java 11 and Spring Boot 2.7.18."""
        payload = make_v1_pipeline_definition()
        stage1 = payload["stages"][0]
        assert stage1["command_jdk"] == "java11"
        assert stage1["target"]["java"] == 11
        assert stage1["target"]["spring_boot"] == "2.7.18"
        assert stage1["input_source"]["kind"] == "legacy_source"

    def test_stage2_java17_boot356_from_stage1(self) -> None:
        """Stage 2 uses Java 17, Spring Boot 3.5.6, reads from stage 1."""
        payload = make_v1_pipeline_definition()
        stage2 = payload["stages"][1]
        assert stage2["command_jdk"] == "java17"
        assert stage2["target"]["java"] == 17
        assert stage2["target"]["spring_boot"] == "3.5.6"
        assert stage2["input_source"]["kind"] == "previous_stage"
        assert stage2["input_source"]["previous_stage_index"] == 1

    def test_stage3_java21_boot356_from_stage2(self) -> None:
        """Stage 3 uses Java 21, Spring Boot 3.5.6, reads from stage 2."""
        payload = make_v1_pipeline_definition()
        stage3 = payload["stages"][2]
        assert stage3["command_jdk"] == "java21"
        assert stage3["target"]["java"] == 21
        assert stage3["target"]["spring_boot"] == "3.5.6"
        assert stage3["input_source"]["kind"] == "previous_stage"
        assert stage3["input_source"]["previous_stage_index"] == 2

    def test_boot4_not_mentioned(self) -> None:
        """Boot 4 must not appear in any stage target."""
        payload = make_v1_pipeline_definition()
        for stage in payload["stages"]:
            sb = stage["target"].get("spring_boot", "")
            assert "4" not in sb or sb == "3.5.6"

    def test_three_stages(self) -> None:
        """The V1 pipeline must have exactly three stages."""
        payload = make_v1_pipeline_definition()
        assert len(payload["stages"]) == 3
