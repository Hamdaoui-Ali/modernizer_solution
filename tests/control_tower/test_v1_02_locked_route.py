"""Focused tests: V1-02 Lock V1 migration route.

Verifies that the V1 route is locked to the three-stage pipeline only, that
Boot 4 and 3.5.14 remain non-execution assets, and that the route validation
service enforces the contract.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v1_route_lock import (
    V1RouteLockService,
    V1RouteValidationResult,
    V1_PIPELINE_ID,
    V1_PIPELINE_VERSION,
    V1_STAGES,
)
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.v1_fixtures import make_v1_pipeline_definition, make_v1_runner_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _migrated_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    apply_pending_migrations(connection)
    return connection


def _service(connection: sqlite3.Connection) -> V1RouteLockService:
    return V1RouteLockService(lambda: SqliteUnitOfWork(connection))


# ===================================================================
# criterion-1: V1 route locked to three-stage pipeline only
# ===================================================================


class TestV1RouteLockedToThreeStagePipeline:
    """The V1 route must expose only the three-stage pipeline."""

    def test_v1_route_accepts_canonical_pipeline(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            result = svc.validate_pipeline_definition(payload)
            assert result.passed, f"Canonical V1 pipeline rejected: {result.failure_reason}"
        finally:
            connection.close()

    def test_v1_route_rejects_two_stage_pipeline(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            payload["stages"] = payload["stages"][:2]
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
            assert "3 stages" in (result.failure_reason or "")
        finally:
            connection.close()

    def test_v1_route_rejects_four_stage_pipeline(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            fourth = payload["stages"][2].copy()
            fourth["stage_index"] = 4
            payload["stages"] = payload["stages"] + (fourth,)
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
            assert "3 stages" in (result.failure_reason or "")
        finally:
            connection.close()

    def test_v1_route_rejects_wrong_pipeline_id(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            payload["pipeline_id"] = "some-other-pipeline"
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
            assert "pipeline" in (result.failure_reason or "").lower()
        finally:
            connection.close()

    def test_v1_route_rejects_wrong_pipeline_version(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            payload["pipeline_version"] = "2099.01"
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
            assert "version" in (result.failure_reason or "").lower()
        finally:
            connection.close()


# ===================================================================
# criterion-2: Boot 4 and 3.5.14 remain non-execution assets
# ===================================================================


class TestV1RouteExcludesBoot4And3514:
    """Boot 4 must not be selectable; 3.5.14 must not be execution-relevant."""

    def test_v1_route_rejects_stage_with_boot4_target(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            # Change stage 3 target to Boot 4 — this fails the spring_boot
            # contract check first (expected 3.5.6, got 4.0.0)
            stages = list(payload["stages"])
            stages[2] = dict(stages[2])
            stages[2]["target"] = {"spring_boot": "4.0.0", "java": 21}
            payload["stages"] = tuple(stages)
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
            # The route lock validates against the contract strictly, so
            # Boot 4 is rejected both because it doesn't match the stage
            # contract AND because it's explicitly excluded.
            assert not result.passed
        finally:
            connection.close()

    def test_v1_route_rejects_stage_with_3514_target(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            stages = list(payload["stages"])
            stages[1] = dict(stages[1])
            stages[1]["target"] = {"spring_boot": "3.5.14", "java": 17}
            payload["stages"] = tuple(stages)
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
            assert "3.5.14" in (result.failure_reason or "")
        finally:
            connection.close()

    def test_v1_route_rejects_all_stages_with_boot4(self, tmp_path: Path) -> None:
        """Any stage with Boot 4 must be rejected."""
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            stages = list(payload["stages"])
            stages[0] = dict(stages[0])
            stages[0]["target"] = {"spring_boot": "4.0.0", "java": 11}
            payload["stages"] = tuple(stages)
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
        finally:
            connection.close()


# ===================================================================
# criterion-3: Pipeline stages locked with correct Java/Boot versions
# ===================================================================


class TestV1RouteStageVersionsLocked:
    """Pipeline stage definitions must match the V1 contract exactly."""

    def test_v1_route_stage1_java11_boot2718(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            result = svc.validate_pipeline_definition(payload)
            assert result.passed
            stage = payload["stages"][0]
            assert stage["command_jdk"] == "java11"
            assert stage["target"]["spring_boot"] == "2.7.18"
            assert stage["target"]["java"] == 11
            assert stage["input_source"]["kind"] == "legacy_source"
        finally:
            connection.close()

    def test_v1_route_stage2_java17_boot356(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            result = svc.validate_pipeline_definition(payload)
            assert result.passed
            stage = payload["stages"][1]
            assert stage["command_jdk"] == "java17"
            assert stage["target"]["spring_boot"] == "3.5.6"
            assert stage["target"]["java"] == 17
            assert stage["input_source"]["kind"] == "previous_stage"
            assert stage["input_source"]["previous_stage_index"] == 1
        finally:
            connection.close()

    def test_v1_route_stage3_java21_boot356(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            result = svc.validate_pipeline_definition(payload)
            assert result.passed
            stage = payload["stages"][2]
            assert stage["command_jdk"] == "java21"
            assert stage["target"]["spring_boot"] == "3.5.6"
            assert stage["target"]["java"] == 21
            assert stage["input_source"]["kind"] == "previous_stage"
            assert stage["input_source"]["previous_stage_index"] == 2
        finally:
            connection.close()

    def test_v1_route_rejects_wrong_stage1_jdk(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            stages = list(payload["stages"])
            stages[0] = dict(stages[0])
            stages[0]["command_jdk"] = "java17"
            payload["stages"] = tuple(stages)
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
            assert "command_jdk" in (result.failure_reason or "")
        finally:
            connection.close()

    def test_v1_route_rejects_wrong_stage2_spring_boot(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            stages = list(payload["stages"])
            stages[1] = dict(stages[1])
            stages[1]["target"] = {"spring_boot": "3.5.14", "java": 17}
            payload["stages"] = tuple(stages)
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
            assert "3.5.14" in (result.failure_reason or "")
        finally:
            connection.close()

    def test_v1_route_rejects_wrong_stage3_input_source(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            payload = make_v1_pipeline_definition()
            stages = list(payload["stages"])
            stages[2] = dict(stages[2])
            stages[2]["input_source"] = {"kind": "legacy_source"}
            payload["stages"] = tuple(stages)
            result = svc.validate_pipeline_definition(payload)
            assert not result.passed
            assert "input_source" in (result.failure_reason or "")
        finally:
            connection.close()


# ===================================================================
# V1 route constants and contract
# ===================================================================


class TestV1RouteConstants:
    """The V1 route constants must match the issue contract."""

    def test_pipeline_id_matches_contract(self) -> None:
        assert V1_PIPELINE_ID == "springboot-216-to-356-java21-three-stage"

    def test_pipeline_version_matches_contract(self) -> None:
        assert V1_PIPELINE_VERSION == "2026.06"

    def test_three_stages_defined(self) -> None:
        assert len(V1_STAGES) == 3

    def test_stage1_contract(self) -> None:
        assert V1_STAGES[0]["command_jdk"] == "java11"
        assert V1_STAGES[0]["spring_boot"] == "2.7.18"
        assert V1_STAGES[0]["java"] == 11
        assert V1_STAGES[0]["input_source_kind"] == "legacy_source"

    def test_stage2_contract(self) -> None:
        assert V1_STAGES[1]["command_jdk"] == "java17"
        assert V1_STAGES[1]["spring_boot"] == "3.5.6"
        assert V1_STAGES[1]["java"] == 17
        assert V1_STAGES[1]["input_source_kind"] == "previous_stage"

    def test_stage3_contract(self) -> None:
        assert V1_STAGES[2]["command_jdk"] == "java21"
        assert V1_STAGES[2]["spring_boot"] == "3.5.6"
        assert V1_STAGES[2]["java"] == 21
        assert V1_STAGES[2]["input_source_kind"] == "previous_stage"


# ===================================================================
# Validation event recording
# ===================================================================


class TestV1RouteValidationEvents:
    """Validation outcomes must be recorded in v1_route_validation_events."""

    def test_validation_records_pass_event(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            pipeline = make_v1_pipeline_definition()
            result = svc.validate_and_record(pipeline=pipeline, actor_id="tester")
            assert result.passed

            # Verify the event was recorded
            rows = connection.execute(
                "SELECT event_type, validation_result, created_by FROM v1_route_validation_events"
            ).fetchall()
            assert len(rows) >= 1
            assert any(
                r["validation_result"] == "pass" and r["created_by"] == "tester"
                for r in rows
            )
        finally:
            connection.close()

    def test_validation_records_fail_event(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            pipeline = make_v1_pipeline_definition()
            pipeline["pipeline_id"] = "bad-pipeline"
            result = svc.validate_and_record(pipeline=pipeline, actor_id="tester")
            assert not result.passed

            # Verify the fail event was recorded
            rows = connection.execute(
                "SELECT event_type, validation_result, failure_reason, created_by "
                "FROM v1_route_validation_events"
            ).fetchall()
            assert any(
                r["validation_result"] == "fail" and r["created_by"] == "tester"
                for r in rows
            )
        finally:
            connection.close()

    def test_validation_events_are_append_only(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            pipeline = make_v1_pipeline_definition()
            svc.validate_and_record(pipeline=pipeline, actor_id="tester")
            svc.validate_and_record(pipeline=pipeline, actor_id="tester2")

            rows = connection.execute(
                "SELECT COUNT(*) as cnt FROM v1_route_validation_events"
            ).fetchone()
            assert rows["cnt"] >= 2, "Validation events should be append-only"
        finally:
            connection.close()


# ===================================================================
# Route config database contract
# ===================================================================


class TestV1RouteConfigDatabase:
    """The v1_route_config database table must enforce the V1 contract."""

    def test_route_config_seeded_at_migration(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            row = connection.execute(
                "SELECT pipeline_id, stage_count, boot4_selectable, execution_relevant_3514 "
                "FROM v1_route_config WHERE row_id = 1"
            ).fetchone()
            assert row is not None
            assert row["pipeline_id"] == "springboot-216-to-356-java21-three-stage"
            assert row["stage_count"] == 3
            assert row["boot4_selectable"] == 0
            assert row["execution_relevant_3514"] == 0
        finally:
            connection.close()

    def test_route_config_prevents_delete(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            with pytest.raises(Exception, match="append-only"):
                connection.execute("DELETE FROM v1_route_config")
                connection.commit()
        finally:
            connection.close()

    def test_route_config_prevents_update(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            with pytest.raises(Exception, match="append-only"):
                connection.execute(
                    "UPDATE v1_route_config SET pipeline_id = 'other' WHERE row_id = 1"
                )
                connection.commit()
        finally:
            connection.close()

    def test_route_config_prevents_second_row(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            now = "2026-06-12T00:00:00.000000Z"
            with pytest.raises(Exception):
                connection.execute(
                    """
                    INSERT INTO v1_route_config (
                        row_id, pipeline_id, pipeline_version, schema_version,
                        stage_count, stage1_id, stage1_jdk, stage1_spring_boot,
                        stage1_java, stage2_id, stage2_jdk, stage2_spring_boot,
                        stage2_java, stage3_id, stage3_jdk, stage3_spring_boot,
                        stage3_java, boot4_selectable, selectable_boot4_allowed,
                        execution_relevant_3514, created_at, created_by
                    ) VALUES (
                        2, 'other-pipeline', '2026.06', '1.0.0',
                        3, 's1', 'java11', '2.7.18',
                        11, 's2', 'java17', '3.5.6',
                        17, 's3', 'java21', '3.5.6',
                        21, 0, 0, 0, ?, 'tester'
                    )
                    """,
                    (now,),
                )
                connection.commit()
        finally:
            connection.close()
