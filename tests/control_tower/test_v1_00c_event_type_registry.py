"""Focused tests for V1-00C: V1 event type registry."""

from __future__ import annotations

import sqlite3

import pytest

from migration_factory.control_tower.domain.events import (
    ALL_V1_EVENT_TYPES,
    COMMAND_LIFECYCLE_EVENTS,
    JOB_LIFECYCLE_EVENTS,
    ROUTE_VALIDATION_EVENTS,
    STAGE_CHAIN_LIFECYCLE_EVENTS,
    V1EventType,
)


class TestV1EventTypeEnum:
    """V1EventType enum provides canonical, typed event type constants."""

    def test_all_members_are_strings(self) -> None:
        """Every V1EventType member is a str (for str, Enum)."""
        for member in V1EventType:
            assert isinstance(member.value, str)
            assert member.value  # non-empty

    def test_all_members_are_unique(self) -> None:
        """No two V1EventType members share the same value."""
        values = [m.value for m in V1EventType]
        assert len(values) == len(set(values))

    def test_contains_all_known_event_types(self) -> None:
        """Registry includes every event_type value used in production code."""
        expected_values = {
            "job_created",
            "artifact_registered",
            "command_queued",
            "command_starting",
            "command_running",
            "command_finalized",
            "pipeline_validation",
            "runner_validation",
            "chain_created",
            "chain_started",
            "chain_completed",
            "chain_failed",
            "stage_started",
            "stage_completed",
            "stage_failed",
            "output_registered",
            "model_invocation_recorded",
        }
        actual_values = {m.value for m in V1EventType}
        assert actual_values == expected_values

    def test_all_lifecycle_subsets_are_proper(self) -> None:
        """Lifecycle convenience sets cover disjoint subsets of V1EventType."""
        # MODEL_INVOCATION_RECORDED is not part of any lifecycle subset;
        # it belongs to the model invocation audit category.
        lifecycle_subsets = (
            JOB_LIFECYCLE_EVENTS
            | COMMAND_LIFECYCLE_EVENTS
            | ROUTE_VALIDATION_EVENTS
            | STAGE_CHAIN_LIFECYCLE_EVENTS
        )
        expected = lifecycle_subsets | {V1EventType.MODEL_INVOCATION_RECORDED}
        assert expected == ALL_V1_EVENT_TYPES

    def test_lifecycle_subsets_are_disjoint(self) -> None:
        """No event type belongs to more than one lifecycle category."""
        sets = [
            JOB_LIFECYCLE_EVENTS,
            COMMAND_LIFECYCLE_EVENTS,
            ROUTE_VALIDATION_EVENTS,
            STAGE_CHAIN_LIFECYCLE_EVENTS,
        ]
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                assert sets[i].isdisjoint(sets[j])

    def test_enum_value_matches_stage_chain_check_constraint(self) -> None:
        """V1EventType covers all event_types in v1_stage_chain_events CHECK."""
        chain_values = {
            V1EventType.CHAIN_CREATED.value,
            V1EventType.CHAIN_STARTED.value,
            V1EventType.CHAIN_COMPLETED.value,
            V1EventType.CHAIN_FAILED.value,
            V1EventType.STAGE_STARTED.value,
            V1EventType.STAGE_COMPLETED.value,
            V1EventType.STAGE_FAILED.value,
            V1EventType.OUTPUT_REGISTERED.value,
        }
        expected_chain = {
            "chain_created",
            "chain_started",
            "chain_completed",
            "chain_failed",
            "stage_started",
            "stage_completed",
            "stage_failed",
            "output_registered",
        }
        assert chain_values == expected_chain

    def test_v1_invariants_preserved_in_docstring(self) -> None:
        """V1 invariants are documented in the enum docstring (not enforced)."""
        doc = V1EventType.__doc__ or ""
        assert "Boot 4 is NOT selectable" in doc
        assert "3.5.14 is NOT execution-relevant" in doc
        assert "LLM never executes" in doc


class TestV1EventTypeRegistryMigration:
    """v1_event_type_registry SQL table is correctly seeded."""

    def test_registry_table_schema_and_seed(self, tmp_path) -> None:
        """Create the migration SQL from file, run it, and verify seed data."""
        db_path = tmp_path / "test_v1_event_registry.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Read and apply the migration.
        migration_path = (
            "migration_factory/control_tower/infrastructure/sqlite/migrations"
            "/0010_v1_event_type_registry.sql"
        )
        with open(migration_path) as f:
            cur.executescript(f.read())

        # Verify table exists.
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_event_type_registry'"
        )
        assert cur.fetchone() is not None

        # Verify all canonical event types are seeded.
        cur.execute("SELECT event_type, category, is_active FROM v1_event_type_registry ORDER BY row_id")
        rows = cur.fetchall()
        assert len(rows) == 16  # one per V1EventType member

        # Spot-check specific rows.
        row_map = {r["event_type"]: r for r in rows}
        assert row_map["job_created"]["category"] == "job_lifecycle"
        assert row_map["job_created"]["is_active"] == 1
        assert row_map["chain_created"]["category"] == "stage_chain_lifecycle"
        assert row_map["pipeline_validation"]["category"] == "route_validation"
        assert row_map["command_queued"]["category"] == "command_lifecycle"

        conn.close()

    def test_registry_is_append_only(self, tmp_path) -> None:
        """UPDATE and DELETE triggers raise errors."""
        db_path = tmp_path / "test_v1_event_registry_append_only.db"
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        migration_path = (
            "migration_factory/control_tower/infrastructure/sqlite/migrations"
            "/0010_v1_event_type_registry.sql"
        )
        with open(migration_path) as f:
            cur.executescript(f.read())

        # UPDATE should fail.
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            cur.execute("UPDATE v1_event_type_registry SET description = 'X' WHERE event_type = 'job_created'")

        # DELETE should fail.
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            cur.execute("DELETE FROM v1_event_type_registry WHERE event_type = 'job_created'")

        conn.close()

    def test_registry_allows_insert_new_event_type(self, tmp_path) -> None:
        """New event types can be inserted (append-only allows INSERT)."""
        db_path = tmp_path / "test_v1_event_registry_insert.db"
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        migration_path = (
            "migration_factory/control_tower/infrastructure/sqlite/migrations"
            "/0010_v1_event_type_registry.sql"
        )
        with open(migration_path) as f:
            cur.executescript(f.read())

        cur.execute(
            "INSERT INTO v1_event_type_registry "
            "(event_type, category, description, is_active, created_at, created_by) "
            "VALUES (?, ?, ?, 1, '2026-06-12T00:00:00.000000Z', 'system')",
            ("new_event_type", "job_lifecycle", "Test new event type"),
        )
        cur.execute(
            "SELECT event_type FROM v1_event_type_registry WHERE event_type = ?",
            ("new_event_type",),
        )
        assert cur.fetchone() is not None

        conn.close()
