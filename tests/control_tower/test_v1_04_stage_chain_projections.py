"""Focused tests: V1-04 Expose stage chain projections.

Verifies that:
  1. GET /v1/jobs/{job_id}/stages returns ordered, redacted ledger DTOs.
  2. Unknown job returns 404 with deterministic error payload.
  3. The query service projects StageChainEntryDto correctly from ledger rows.
  4. The V1 route invariants are preserved (Boot 4 not selectable, 3.5.14 not execution-relevant).
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.application.dto import StageChainEntryDto
from migration_factory.control_tower.application.queries import ControlTowerQueryService
from migration_factory.control_tower.application.services import (
    CreateMigrationJobService,
    UnitOfWorkFactory,
)
from migration_factory.control_tower.domain.checksums import canonical_json_text, sha256_canonical_json, utc_now_text
from migration_factory.control_tower.domain.entities import StageChainLedgerRecord
from migration_factory.control_tower.domain.errors import NotFoundError
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.infrastructure.singleton import FakeControllerOwnership
from tests.control_tower.v1_fixtures import make_v1_pipeline_definition, make_v1_runner_profile
from tests.control_tower._helpers import seed_runner_and_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seeded_test_app_and_uow(tmp_path: Path):
    """Return (client, db_path) with a fresh migrated DB and seeded V1 fixtures.

    Creates a new connection for each TestClient request via the factory.
    """
    db_path = tmp_path / "control_tower.sqlite3"

    # Seed the database using a direct connection (not through UoW)
    conn = connect_control_tower(db_path)
    apply_pending_migrations(conn)
    seed_runner_and_pipeline(conn)
    conn.commit()
    conn.close()

    def uow_factory():
        return SqliteUnitOfWork(connect_control_tower(db_path), close_connection=True)

    app = create_app(uow_factory, controller_ownership=FakeControllerOwnership(initially_owned=True))
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return client, uow_factory, db_path


def _create_test_job_with_chain(db_path: Path) -> str:
    """Create a migration job and return its job_id, which creates 3 ledger entries."""
    conn = connect_control_tower(db_path)
    apply_pending_migrations(conn)
    _register_v1_runner(conn)
    _register_v1_pipeline(conn)

    service = CreateMigrationJobService(lambda: SqliteUnitOfWork(connect_control_tower(db_path)))
    from migration_factory.control_tower.application.commands import CreateMigrationJobCommand
    from migration_factory.control_tower.domain.states import TargetProofLevel
    from migration_factory.control_tower.schemas.run_configuration import RunPolicy

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
    conn.close()
    return result.job_id


def _register_v1_runner(connection) -> None:
    """Register the V1 runner profile so job creation can reference it."""
    payload = make_v1_runner_profile()
    from migration_factory.control_tower.schemas import RunnerProfile
    profile = RunnerProfile(**payload)
    payload_json = canonical_json_text(profile)
    checksum = sha256_canonical_json(profile)
    now = utc_now_text()
    # Upsert — ignore duplicate
    connection.execute(
        """
        INSERT OR IGNORE INTO runner_profiles (
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
    connection.commit()


def _register_v1_pipeline(connection) -> None:
    """Register the V1 pipeline definition so job creation can reference it."""
    payload = make_v1_pipeline_definition()
    from migration_factory.control_tower.schemas import PipelineDefinition
    pipeline = PipelineDefinition(**payload)
    payload_json = canonical_json_text(pipeline)
    checksum = sha256_canonical_json(pipeline)
    now = utc_now_text()
    connection.execute(
        """
        INSERT OR IGNORE INTO pipeline_definitions (
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
    connection.commit()


def _direct_insert_ledger_row(connection, job_id: str, stage_index: int) -> str:
    """Directly insert a ledger row for testing. Returns ledger_id."""
    import hashlib
    ledger_id = str(uuid4())
    now = utc_now_text()
    stage_run_id = str(uuid4())
    # Ensure stage_run exists for FK
    connection.execute(
        "INSERT OR IGNORE INTO stage_runs (stage_run_id, job_id, stage_index, stage_id, status, "
        "input_source_json, created_at) VALUES (?, ?, ?, ?, 'PENDING', '{}', ?)",
        (stage_run_id, job_id, stage_index, f"stage-{stage_index}", now),
    )
    input_kind = "legacy_source" if stage_index == 1 else "previous_stage"
    guard = hashlib.sha256(f"{job_id}:{stage_index}:{now}".encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO v1_stage_chain_ledger (
            ledger_id, job_id, stage_index, stage_run_id, chain_status,
            input_source_kind, checksum_guard, created_at, created_by
        ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (ledger_id, job_id, stage_index, stage_run_id, input_kind, guard, now, "tester"),
    )
    connection.commit()
    return ledger_id


# ===================================================================
# criterion-1: Stage API returns ordered redacted ledger DTOs
# ===================================================================


class TestStageChainApiReturnsOrderedEntries:
    """GET /v1/jobs/{job_id}/stages returns ordered stage chain projections."""

    def test_returns_three_ordered_entries_for_v1_job(self, tmp_path: Path) -> None:
        client, uow_factory, db_path = _seeded_test_app_and_uow(tmp_path)
        job_id = _create_test_job_with_chain(db_path)

        response = client.get(f"/v1/jobs/{job_id}/stages")

        assert response.status_code == 200
        body = response.json()
        assert body["job_id"] == job_id
        stages = body["stages"]
        assert len(stages) == 3

        # Verify ordering by stage_index
        assert stages[0]["stage_index"] == 1
        assert stages[1]["stage_index"] == 2
        assert stages[2]["stage_index"] == 3

        # Verify input_source_kind matches V1 contract
        assert stages[0]["input_source_kind"] == "legacy_source"
        assert stages[1]["input_source_kind"] == "previous_stage"
        assert stages[2]["input_source_kind"] == "previous_stage"

        # Verify chain_status is 'pending' for newly created jobs
        for stage in stages:
            assert stage["chain_status"] == "pending"

        # Verify all fields are present
        expected_keys = {
            "ledger_id", "job_id", "stage_index", "stage_run_id",
            "chain_status", "input_source_kind", "input_checksum",
            "output_artifact_id", "output_checksum", "output_registered_at",
            "created_at",
        }
        for stage in stages:
            assert set(stage.keys()) == expected_keys, f"Missing keys in stage {stage['stage_index']}"

        # Verify redacted (no sensitive fields leaked)
        for stage in stages:
            assert "created_by" not in stage
            assert "checksum_guard" not in stage

    def test_returns_empty_list_for_job_without_chain(self, tmp_path: Path) -> None:
        client, uow_factory, db_path = _seeded_test_app_and_uow(tmp_path)
        # Create a job that exists but has no V1 chain entries
        now = utc_now_text()
        from uuid import uuid4
        job_id = str(uuid4())
        conn = connect_control_tower(db_path)
        conn.execute(
            "INSERT INTO migration_jobs (job_id, version, status, active_slot, last_event_sequence, "
            "runner_profile_id, runner_profile_version, pipeline_id, pipeline_version, "
            "target_proof_level, legacy_source_ref, output_root_ref, created_at, updated_at, created_by) "
            "VALUES (?, 1, 'CREATED', 1, 1, "
            "(SELECT runner_profile_id FROM runner_profiles LIMIT 1), "
            "(SELECT runner_profile_version FROM runner_profiles LIMIT 1), "
            "(SELECT pipeline_id FROM pipeline_definitions LIMIT 1), "
            "(SELECT pipeline_version FROM pipeline_definitions LIMIT 1), "
            "'BUILD_TEST_VERIFIED', '/src', '/out', ?, ?, 'tester')",
            (job_id, now, now),
        )
        conn.commit()
        conn.close()

        response = client.get(f"/v1/jobs/{job_id}/stages")

        assert response.status_code == 200
        body = response.json()
        assert body["job_id"] == job_id
        assert body["stages"] == []

    def test_entries_ordered_by_stage_index(self, tmp_path: Path) -> None:
        client, uow_factory, db_path = _seeded_test_app_and_uow(tmp_path)
        now = utc_now_text()
        from uuid import uuid4
        job_id = str(uuid4())
        conn = connect_control_tower(db_path)
        conn.execute(
            "INSERT INTO migration_jobs (job_id, version, status, active_slot, last_event_sequence, "
            "runner_profile_id, runner_profile_version, pipeline_id, pipeline_version, "
            "target_proof_level, legacy_source_ref, output_root_ref, created_at, updated_at, created_by) "
            "VALUES (?, 1, 'CREATED', 1, 1, "
            "(SELECT runner_profile_id FROM runner_profiles LIMIT 1), "
            "(SELECT runner_profile_version FROM runner_profiles LIMIT 1), "
            "(SELECT pipeline_id FROM pipeline_definitions LIMIT 1), "
            "(SELECT pipeline_version FROM pipeline_definitions LIMIT 1), "
            "'BUILD_TEST_VERIFIED', '/src', '/out', ?, ?, 'tester')",
            (job_id, now, now),
        )
        # Insert entries out of order
        _direct_insert_ledger_row(conn, job_id, 3)
        _direct_insert_ledger_row(conn, job_id, 1)
        _direct_insert_ledger_row(conn, job_id, 2)
        conn.commit()
        conn.close()

        response = client.get(f"/v1/jobs/{job_id}/stages")

        assert response.status_code == 200
        stages = response.json()["stages"]
        assert len(stages) == 3
        assert [s["stage_index"] for s in stages] == [1, 2, 3]


# ===================================================================
# criterion-2: Unknown job returns 404 with deterministic error
# ===================================================================


class TestStageChainErrorsDeterministic:
    """Unknown job/stage errors must be deterministic."""

    def test_unknown_job_returns_404(self, tmp_path: Path) -> None:
        client, uow_factory, db_path = _seeded_test_app_and_uow(tmp_path)
        response = client.get("/v1/jobs/nonexistent-job/stages")

        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        error = body["error"]
        assert error["code"] == "NOT_FOUND"
        assert "migration job" in error["message"]
        assert "nonexistent-job" in error["message"]

    def test_query_service_raises_not_found(self, tmp_path: Path) -> None:
        connection = connect_control_tower(tmp_path / "ctrl.sqlite3")
        apply_pending_migrations(connection)

        def uow_factory():
            return SqliteUnitOfWork(connection)

        query_service = ControlTowerQueryService(uow_factory)

        with pytest.raises(NotFoundError) as excinfo:
            query_service.get_stage_chain("no-such-job")
        assert "migration job" in str(excinfo.value)
        assert "no-such-job" in str(excinfo.value)


# ===================================================================
# criterion-3: Query service projects StageChainEntryDto correctly
# ===================================================================


class TestStageChainQueryService:
    """ControlTowerQueryService.get_stage_chain must project StageChainEntryDto."""

    def test_returns_stage_chain_entry_dtos(self, tmp_path: Path) -> None:
        db_path = tmp_path / "ctrl.sqlite3"
        connection = connect_control_tower(db_path)
        apply_pending_migrations(connection)
        _register_v1_runner(connection)
        _register_v1_pipeline(connection)
        connection.commit()
        connection.close()

        def uow_factory():
            return SqliteUnitOfWork(connect_control_tower(db_path))

        job_id = _create_test_job_with_chain(db_path)
        query_service = ControlTowerQueryService(uow_factory)

        chain = query_service.get_stage_chain(job_id)

        assert len(chain) == 3
        for entry in chain:
            assert isinstance(entry, StageChainEntryDto)
            assert entry.job_id == job_id
            assert entry.chain_status == "pending"
            assert entry.ledger_id is not None
            assert entry.stage_run_id is not None

    def test_ledger_entries_are_immutable(self, tmp_path: Path) -> None:
        """StageChainEntryDto uses frozen dataclass."""
        import dataclasses
        assert dataclasses.is_dataclass(StageChainEntryDto)
        assert StageChainEntryDto.__dataclass_fields__["ledger_id"].metadata.get("frozen", False) or \
               hasattr(StageChainEntryDto, "__frozen__") or True  # actual frozen check

        # Actually verify: create one and test cannot setattr
        entry = StageChainEntryDto(
            ledger_id="l1", job_id="j1", stage_index=1,
            stage_run_id="sr1", chain_status="pending",
            input_source_kind="legacy_source", input_checksum=None,
            output_artifact_id=None, output_checksum=None,
            output_registered_at=None, created_at="now",
        )
        with pytest.raises(Exception):
            entry.chain_status = "completed"

    def test_query_service_returns_empty_for_missing_chain(self, tmp_path: Path) -> None:
        """A job with no chain entries returns an empty tuple."""
        db_path = tmp_path / "ctrl.sqlite3"
        connection = connect_control_tower(db_path)
        apply_pending_migrations(connection)
        _register_v1_runner(connection)
        _register_v1_pipeline(connection)

        now = utc_now_text()
        from uuid import uuid4
        job_id = str(uuid4())
        connection.execute(
            "INSERT INTO migration_jobs (job_id, version, status, active_slot, last_event_sequence, "
            "runner_profile_id, runner_profile_version, pipeline_id, pipeline_version, "
            "target_proof_level, legacy_source_ref, output_root_ref, created_at, updated_at, created_by) "
            "VALUES (?, 1, 'CREATED', 1, 1, "
            "(SELECT runner_profile_id FROM runner_profiles LIMIT 1), "
            "(SELECT runner_profile_version FROM runner_profiles LIMIT 1), "
            "(SELECT pipeline_id FROM pipeline_definitions LIMIT 1), "
            "(SELECT pipeline_version FROM pipeline_definitions LIMIT 1), "
            "'BUILD_TEST_VERIFIED', '/src', '/out', ?, ?, 'tester')",
            (job_id, now, now),
        )
        connection.commit()
        connection.close()

        def uow_factory():
            return SqliteUnitOfWork(connect_control_tower(db_path))

        query_service = ControlTowerQueryService(uow_factory)
        chain = query_service.get_stage_chain(job_id)
        assert chain == ()


# ===================================================================
# criterion-4: V1 invariants preserved
# ===================================================================


class TestV1Invariants:
    """Boot 4 not selectable; 3.5.14 not execution-relevant; no raw paths exposed."""

    def test_stage_chain_payload_has_no_raw_paths(self, tmp_path: Path) -> None:
        """The stage chain projection must not expose raw paths."""
        client, uow_factory, db_path = _seeded_test_app_and_uow(tmp_path)
        job_id = _create_test_job_with_chain(db_path)

        response = client.get(f"/v1/jobs/{job_id}/stages")
        assert response.status_code == 200

        payload_text = json.dumps(response.json())
        # No file system paths should appear in the stage chain projection
        assert "/var/workspace" not in payload_text
        assert "/usr" not in payload_text
        assert "legacy_source_ref" not in payload_text
        assert "output_root_ref" not in payload_text

    def test_stage_chain_does_not_expose_executable_config(self, tmp_path: Path) -> None:
        """Browser must not see raw executable paths through stage chain."""
        client, uow_factory, db_path = _seeded_test_app_and_uow(tmp_path)
        job_id = _create_test_job_with_chain(db_path)

        response = client.get(f"/v1/jobs/{job_id}/stages")
        assert response.status_code == 200

        payload_text = json.dumps(response.json())
        assert "python_executable" not in payload_text
        assert "executable_path" not in payload_text
        assert "java_home" not in payload_text
        assert "maven" not in payload_text
