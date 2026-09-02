from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from migration_factory.control_tower.infrastructure.sqlite.connection import (
    UnsupportedJournalModeError,
    configure_control_tower_journal_mode,
    connect_control_tower,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    AppliedMigrationChecksumMismatchError,
    AppliedMigrationMissingError,
    MigrationDiscoveryError,
    MigrationExecutionError,
    apply_pending_migrations,
    discover_migrations,
    split_sql_statements,
)


def test_foreign_keys_enabled_on_every_connection(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    try:
        value = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        assert value == 1
    finally:
        connection.close()


def test_busy_timeout_is_configured(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    try:
        value = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        assert value == 5000
    finally:
        connection.close()


def test_default_journal_mode_is_delete(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    try:
        value = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).upper()
        assert value == "DELETE"
    finally:
        connection.close()


def test_unsupported_journal_mode_is_rejected(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    try:
        with pytest.raises(UnsupportedJournalModeError):
            configure_control_tower_journal_mode(connection, journal_mode="MEMORY")
    finally:
        connection.close()


def test_migrations_are_discovered_and_ordered_correctly(tmp_path: Path) -> None:
    _write_sql(tmp_path, "0001_first.sql", "CREATE TABLE first_table (id INTEGER PRIMARY KEY);")
    _write_sql(tmp_path, "0002_second.sql", "CREATE TABLE second_table (id INTEGER PRIMARY KEY);")

    migrations = discover_migrations(tmp_path)

    assert [migration.version for migration in migrations] == [1, 2]
    assert [migration.name for migration in migrations] == ["first", "second"]


def test_duplicate_migration_versions_are_rejected(tmp_path: Path) -> None:
    _write_sql(tmp_path, "0001_first.sql", "CREATE TABLE first_table (id INTEGER PRIMARY KEY);")
    _write_sql(tmp_path, "0001_second.sql", "CREATE TABLE second_table (id INTEGER PRIMARY KEY);")

    with pytest.raises(MigrationDiscoveryError, match="Duplicate migration version"):
        discover_migrations(tmp_path)


def test_changed_checksum_for_applied_migration_is_rejected(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_first.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """,
    )
    _write_sql(migrations_dir, "0002_second.sql", "CREATE TABLE example_table (id INTEGER PRIMARY KEY);")

    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    try:
        apply_pending_migrations(connection, migrations_dir=migrations_dir)
        _write_sql(migrations_dir, "0002_second.sql", "CREATE TABLE example_table (id INTEGER PRIMARY KEY, name TEXT NOT NULL);")

        with pytest.raises(AppliedMigrationChecksumMismatchError):
            apply_pending_migrations(connection, migrations_dir=migrations_dir)
    finally:
        connection.close()


def test_applied_migration_missing_from_disk_is_rejected_outside_dev_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_foundation.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE retained_table (id INTEGER PRIMARY KEY);
        """,
    )
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    monkeypatch.setenv("CONTROL_TOWER_DEV_MODE", "0")
    try:
        apply_pending_migrations(connection, migrations_dir=migrations_dir)
        connection.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (2, 'removed', 'stale', 'now')"
        )

        with pytest.raises(AppliedMigrationMissingError, match="Applied migration missing from disk: 0002"):
            apply_pending_migrations(connection, migrations_dir=migrations_dir)
    finally:
        connection.close()


def test_applied_migration_missing_from_disk_resets_dev_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_foundation.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE retained_table (id INTEGER PRIMARY KEY);
        """,
    )
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    monkeypatch.setenv("CONTROL_TOWER_DEV_MODE", "1")
    try:
        apply_pending_migrations(connection, migrations_dir=migrations_dir)
        connection.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (2, 'removed', 'stale', 'now')"
        )
        connection.execute("CREATE TABLE stale_branch_table (id INTEGER PRIMARY KEY)")

        pending = apply_pending_migrations(connection, migrations_dir=migrations_dir)

        assert [migration.version for migration in pending] == [1]
        versions = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert [row[0] for row in versions] == [1]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'retained_table'"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'stale_branch_table'"
        ).fetchone() is None
    finally:
        connection.close()

def test_migration_execution_does_not_use_executescript() -> None:
    import migration_factory.control_tower.infrastructure.sqlite.migrations as migrations_module

    assert "executescript" not in migrations_module.__file__
    source = Path(migrations_module.__file__).read_text(encoding="utf-8")
    assert "executescript" not in source


def test_each_migration_uses_begin_immediate(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    trace: list[str] = []
    connection.set_trace_callback(trace.append)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_schema_migrations.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """,
    )
    _write_sql(migrations_dir, "0002_demo.sql", "CREATE TABLE demo_table (id INTEGER PRIMARY KEY);")
    try:
        apply_pending_migrations(connection, migrations_dir=migrations_dir)
    finally:
        connection.close()

    assert sum(1 for statement in trace if statement == "BEGIN IMMEDIATE") == 2


def test_schema_changes_and_schema_history_insertion_are_atomic(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_schema_migrations.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """,
    )
    _write_sql(
        migrations_dir,
        "0002_fail.sql",
        """
        CREATE TABLE atomic_table (id INTEGER PRIMARY KEY);
        CREATE UNIQUE INDEX ux_atomic_duplicate ON atomic_table (missing_column);
        """,
    )
    try:
        with pytest.raises(MigrationExecutionError):
            apply_pending_migrations(connection, migrations_dir=migrations_dir)

        table_exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'atomic_table'"
        ).fetchone()
        history = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

        assert table_exists is None
        assert [row[0] for row in history] == [1]
    finally:
        connection.close()


def test_failed_migration_rolls_back_completely(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_schema_migrations.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """,
    )
    _write_sql(
        migrations_dir,
        "0002_fail.sql",
        """
        CREATE TABLE rollback_table (id INTEGER PRIMARY KEY);
        INSERT INTO missing_table (id) VALUES (1);
        """,
    )
    try:
        with pytest.raises(MigrationExecutionError):
            apply_pending_migrations(connection, migrations_dir=migrations_dir)

        table_exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'rollback_table'"
        ).fetchone()
        assert table_exists is None
    finally:
        connection.close()


def test_foreign_key_check_happens_before_commit(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    trace: list[str] = []
    connection.set_trace_callback(trace.append)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_schema_migrations.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """,
    )
    try:
        apply_pending_migrations(connection, migrations_dir=migrations_dir)
    finally:
        connection.close()

    foreign_key_check_index = trace.index("PRAGMA foreign_key_check")
    commit_index = trace.index("COMMIT")
    assert foreign_key_check_index < commit_index


def test_trigger_bodies_with_internal_semicolons_work(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_schema_migrations.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE parent_table (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE child_table (id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, FOREIGN KEY (parent_id) REFERENCES parent_table (id));
        CREATE TRIGGER child_table_touch
        AFTER INSERT ON parent_table
        BEGIN
            INSERT INTO child_table (id, parent_id) VALUES (NEW.id, NEW.id);
            INSERT INTO child_table (id, parent_id) VALUES (NEW.id + 100, NEW.id);
        END;
        """,
    )
    try:
        apply_pending_migrations(connection, migrations_dir=migrations_dir)
        connection.execute("INSERT INTO parent_table (id, value) VALUES (1, 'ok')")
        child_rows = connection.execute(
            "SELECT COUNT(*) FROM child_table WHERE parent_id = 1"
        ).fetchone()[0]
        assert child_rows == 2
    finally:
        connection.close()


def test_quoted_semicolons_work() -> None:
    statements = split_sql_statements(
        """
        CREATE TABLE quoted_values (
            id INTEGER PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ';quoted;value;'
        );
        INSERT INTO quoted_values (id, value) VALUES (1, 'a;b;c');
        """
    )

    assert len(statements) == 2


def test_transaction_statements_are_rejected(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_begin.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        BEGIN;
        """,
    )
    try:
        with pytest.raises(MigrationExecutionError, match="forbidden transaction-control"):
            apply_pending_migrations(connection, migrations_dir=migrations_dir)
    finally:
        connection.close()


def test_dangerous_pragmas_are_rejected(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_sql(
        migrations_dir,
        "0001_schema_migrations.sql",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """,
    )
    _write_sql(
        migrations_dir,
        "0002_bad_pragma.sql",
        "PRAGMA foreign_keys = OFF;",
    )
    try:
        with pytest.raises(MigrationExecutionError, match="forbidden PRAGMA"):
            apply_pending_migrations(connection, migrations_dir=migrations_dir)
    finally:
        connection.close()


def test_all_m1_tables_exist_after_foundation_migration(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        actual_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()

    assert {
        "schema_migrations",
        "runner_profiles",
        "pipeline_definitions",
        "migration_jobs",
        "run_configurations",
        "stage_runs",
        "run_events",
        "artifacts",
        "audit_records",
    }.issubset(actual_tables)


def test_schema_migration_timestamps_use_microseconds(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        applied_at = connection.execute(
            "SELECT applied_at FROM schema_migrations WHERE version = 1"
        ).fetchone()[0]
    finally:
        connection.close()

    assert applied_at.endswith("Z")
    assert "." in applied_at
    assert len(applied_at.split(".")[1].rstrip("Z")) == 6


def test_runner_profiles_schema_matches_contract(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        columns = _table_columns(connection, "runner_profiles")
        pk_columns = _primary_key_columns(connection, "runner_profiles")
    finally:
        connection.close()

    assert set(columns) == {
        "runner_profile_id",
        "runner_profile_version",
        "display_name",
        "schema_version",
        "payload_json",
        "payload_checksum",
        "created_at",
        "created_by",
    }
    assert pk_columns == ["runner_profile_id", "runner_profile_version"]
    assert "profile_id" not in columns
    assert "config_json" not in columns


def test_pipeline_definitions_schema_matches_contract(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        columns = _table_columns(connection, "pipeline_definitions")
        pk_columns = _primary_key_columns(connection, "pipeline_definitions")
    finally:
        connection.close()

    assert set(columns) == {
        "pipeline_id",
        "pipeline_version",
        "display_name",
        "schema_version",
        "graph_version",
        "graph_state_schema_version",
        "payload_json",
        "payload_checksum",
        "created_at",
        "created_by",
    }
    assert pk_columns == ["pipeline_id", "pipeline_version"]
    assert "pipeline_name" not in columns


def test_migration_jobs_composite_foreign_keys_match_contract(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        columns = _table_columns(connection, "migration_jobs")
        foreign_keys = _foreign_keys_grouped(connection, "migration_jobs")
    finally:
        connection.close()

    assert "runner_profile_version" in columns
    assert "pipeline_version" in columns
    assert {
        "table": "runner_profiles",
        "from": ["runner_profile_id", "runner_profile_version"],
        "to": ["runner_profile_id", "runner_profile_version"],
    } in foreign_keys
    assert {
        "table": "migration_jobs",
        "from": ["job_id"],
        "to": ["job_id"],
    } not in foreign_keys
    assert {
        "table": "pipeline_definitions",
        "from": ["pipeline_id", "pipeline_version"],
        "to": ["pipeline_id", "pipeline_version"],
    } in foreign_keys


def test_run_configurations_schema_and_foreign_keys_match_contract(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        columns = _table_columns(connection, "run_configurations")
        foreign_keys = _foreign_keys_grouped(connection, "run_configurations")
    finally:
        connection.close()

    for expected_column in (
        "payload_json",
        "payload_checksum",
        "enabled_gates_json",
        "policy_json",
        "runner_profile_version",
        "pipeline_version",
    ):
        assert expected_column in columns

    assert {
        "table": "runner_profiles",
        "from": ["runner_profile_id", "runner_profile_version"],
        "to": ["runner_profile_id", "runner_profile_version"],
    } in foreign_keys
    assert {
        "table": "pipeline_definitions",
        "from": ["pipeline_id", "pipeline_version"],
        "to": ["pipeline_id", "pipeline_version"],
    } in foreign_keys


def test_audit_records_schema_matches_contract_and_legacy_columns_absent(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        columns = _table_columns(connection, "audit_records")
    finally:
        connection.close()

    for expected_column in (
        "audit_id",
        "actor_type",
        "actor_id",
        "prior_state",
        "new_state",
        "job_version",
        "correlation_id",
        "causation_id",
        "payload_json",
        "created_at",
    ):
        assert expected_column in columns

    for unexpected_column in (
        "audit_record_id",
        "recorded_utc",
        "entity_type",
        "entity_id",
        "stage_run_id",
        "actor",
    ):
        assert unexpected_column not in columns


def test_required_indexes_exist(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        indexes = _all_index_names(connection)
    finally:
        connection.close()

    assert {
        "ix_migration_jobs_status",
        "ix_migration_jobs_created_at",
        "ix_stage_runs_job_id",
        "ix_run_events_job_sequence",
        "ix_artifacts_job_id",
        "ix_audit_records_job_created_at",
        "ux_one_active_job",
    }.issubset(indexes)


def test_audit_records_update_delete_is_blocked_by_triggers(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        _seed_foundation_references(connection)
        connection.execute(
            """
            INSERT INTO audit_records (
                audit_id, job_id, actor_type, actor_id, action, prior_state, new_state,
                job_version, correlation_id, causation_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "audit-1",
                "job-1",
                "user",
                "tester",
                "job_state_changed",
                "QUEUED",
                "RUNNING",
                1,
                "corr-1",
                "cause-1",
                "{}",
                "2026-01-01T00:00:00Z",
            ),
        )

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE audit_records SET actor_id = ? WHERE audit_id = ?",
                ("other", "audit-1"),
            )

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM audit_records WHERE audit_id = ?",
                ("audit-1",),
            )
    finally:
        connection.close()


def test_one_active_job_index_and_status_active_slot_check_exist_and_work(tmp_path: Path) -> None:
    connection = _migrated_connection(tmp_path)
    try:
        _seed_runner_profile(connection)
        _seed_pipeline_definition(connection)
        connection.execute(
            """
            INSERT INTO migration_jobs (
                job_id, version, status, active_slot, last_event_sequence,
                runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
                created_at, updated_at, started_at, finished_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-1",
                1,
                "RUNNING",
                1,
                0,
                "profile-1",
                "v1",
                "pipeline-1",
                "v1",
                "ANALYZED",
                None,
                "legacy-ref",
                "output-ref",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                None,
                "tester",
            ),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO migration_jobs (
                    job_id, version, status, active_slot, last_event_sequence,
                    runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                    target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
                    created_at, updated_at, started_at, finished_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "job-2",
                    1,
                    "QUEUED",
                    1,
                    0,
                    "profile-1",
                    "v1",
                    "pipeline-1",
                    "v1",
                    "ANALYZED",
                    None,
                    "legacy-ref",
                    "output-ref",
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                    None,
                    None,
                    "tester",
                ),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO migration_jobs (
                    job_id, version, status, active_slot, last_event_sequence,
                    runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                    target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
                    created_at, updated_at, started_at, finished_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "job-3",
                    1,
                    "COMPLETED",
                    1,
                    0,
                    "profile-1",
                    "v1",
                    "pipeline-1",
                    "v1",
                    "ANALYZED",
                    "ANALYZED",
                    "legacy-ref",
                    "output-ref",
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                    None,
                    "2026-01-01T01:00:00Z",
                    "tester",
                ),
            )

        connection.execute(
            """
            INSERT INTO migration_jobs (
                job_id, version, status, active_slot, last_event_sequence,
                runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
                target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
                created_at, updated_at, started_at, finished_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-4",
                2,
                "COMPLETED",
                None,
                4,
                "profile-1",
                "v1",
                "pipeline-1",
                "v1",
                "BUILD_TEST_VERIFIED",
                "BUILD_TEST_VERIFIED",
                "legacy-ref",
                "output-ref",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "2026-01-01T01:00:00Z",
                "tester",
            ),
        )
    finally:
        connection.close()


def _apply_up_to_0045(conn: sqlite3.Connection) -> None:
    from migration_factory.control_tower.infrastructure.sqlite.migrations import (
        _apply_single_migration,
        discover_migrations,
    )
    for m in discover_migrations():
        _apply_single_migration(conn, m)
        if m.version == 45:
            break


def test_migration_0046_upgrade_preserves_data(tmp_path: Path) -> None:
    connection = connect_control_tower(tmp_path / "test_0046.sqlite3")
    try:
        _apply_up_to_0045(connection)

        job_id = "job-0046"

        # Seed all seven stage-bearing tables with Stage 1-3 data

        # 1. v2_stage_commands — include gate_id/decision_id from 0043
        connection.execute(
            "INSERT INTO v2_stage_commands (command_id, job_id, stage_index, manifest_checksum, created_at, updated_at, gate_id, decision_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("cmd-1", job_id, 1, "chk1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", None, None),
        )
        connection.execute(
            "INSERT INTO v2_stage_commands (command_id, job_id, stage_index, manifest_checksum, created_at, updated_at, gate_id, decision_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("cmd-2", job_id, 3, "chk2", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "gate-1", "dec-1"),
        )

        # 2. v2_approval_decisions — includes job_id from 0035
        connection.execute(
            "INSERT INTO v2_approval_decisions (card_id, interrupt_id, request_checksum, stage_index, summary, status, created_at, job_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("card-1", "int-1", "chk1", 2, "", "pending", "2026-01-01T00:00:00Z", job_id),
        )

        # 3. v2_resume_commands
        connection.execute(
            "INSERT INTO v2_resume_commands (resume_id, card_id, decision, job_id, stage_index, command_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("res-1", "card-1", "continue", job_id, 2, "[]", "2026-01-01T00:00:00Z"),
        )

        # 4. v2_pending_action_drafts
        connection.execute(
            "INSERT INTO v2_pending_action_drafts (action_id, job_id, action_type, reason, stage_index, payload_checksum, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("act-1", job_id, "repair", "", 1, "", "draft", "2026-01-01T00:00:00Z"),
        )

        # 5. v2_job_events
        connection.execute(
            "INSERT INTO v2_job_events (event_id, job_id, stage, type, status, message, payload_json, created_at, sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("evt-1", job_id, 1, "stage_start", "running", "", "{}", "2026-01-01T00:00:00Z", 1),
        )
        connection.execute(
            "INSERT INTO v2_job_events (event_id, job_id, stage, type, status, message, payload_json, created_at, sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("evt-2", job_id, 3, "stage_end", "completed", "", "{}", "2026-01-01T00:00:00Z", 2),
        )

        # 6. v2_phase_gates
        connection.execute(
            "INSERT INTO v2_phase_gates (gate_id, job_id, gate_phase, stage_index, gate_status, gate_decision, source_artifact_checksum, source_artifact_refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("gate-0046", job_id, "analysis_review", 1, "open", "pending", "chk", "[]", "2026-01-01T00:00:00Z"),
        )

        # 7. v2_artifact_revisions
        connection.execute(
            "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, revision_status, revision_order, evidence_checksum, artifact_refs_json, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("rev-0046", job_id, 2, "analysis", "draft", 0, "chk", "[]", "2026-01-01T00:00:00Z", "system"),
        )

        # Apply 0046 migration
        apply_pending_migrations(connection)

        # ── Verify values survive ──────────────────────────────────

        # 1. v2_stage_commands
        row = connection.execute("SELECT stage_index, gate_id, decision_id FROM v2_stage_commands WHERE command_id = 'cmd-1'").fetchone()
        assert row["stage_index"] == 1
        assert row["gate_id"] is None
        row = connection.execute("SELECT stage_index, gate_id, decision_id FROM v2_stage_commands WHERE command_id = 'cmd-2'").fetchone()
        assert row["stage_index"] == 3
        assert row["gate_id"] == "gate-1"
        assert row["decision_id"] == "dec-1"

        # 2. v2_approval_decisions
        row = connection.execute("SELECT stage_index, job_id FROM v2_approval_decisions WHERE card_id = 'card-1'").fetchone()
        assert row["stage_index"] == 2
        assert row["job_id"] == job_id

        # 3. v2_resume_commands
        row = connection.execute("SELECT stage_index FROM v2_resume_commands WHERE resume_id = 'res-1'").fetchone()
        assert row["stage_index"] == 2

        # 4. v2_pending_action_drafts
        row = connection.execute("SELECT stage_index FROM v2_pending_action_drafts WHERE action_id = 'act-1'").fetchone()
        assert row["stage_index"] == 1

        # 5. v2_job_events
        row = connection.execute("SELECT stage FROM v2_job_events WHERE event_id = 'evt-1'").fetchone()
        assert row["stage"] == 1
        row = connection.execute("SELECT stage FROM v2_job_events WHERE event_id = 'evt-2'").fetchone()
        assert row["stage"] == 3

        # 6. v2_phase_gates
        row = connection.execute("SELECT stage_index FROM v2_phase_gates WHERE gate_id = 'gate-0046'").fetchone()
        assert row["stage_index"] == 1

        # 7. v2_artifact_revisions
        row = connection.execute("SELECT stage_index FROM v2_artifact_revisions WHERE revision_id = 'rev-0046'").fetchone()
        assert row["stage_index"] == 2

        # ── Stage 4 inserts succeed in all seven tables ────────────
        connection.execute(
            "INSERT INTO v2_stage_commands (command_id, job_id, stage_index, manifest_checksum, created_at, updated_at) VALUES ('cmd-s4', ?, 4, 'chk', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO v2_approval_decisions (card_id, interrupt_id, request_checksum, stage_index, created_at, job_id) VALUES ('card-s4', 'int', 'chk', 4, '2026-01-01T00:00:00Z', ?)",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO v2_resume_commands (resume_id, card_id, decision, job_id, stage_index, created_at) VALUES ('res-s4', 'card-s4', 'continue', ?, 4, '2026-01-01T00:00:00Z')",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO v2_pending_action_drafts (action_id, job_id, action_type, stage_index, created_at) VALUES ('act-s4', ?, 'repair', 4, '2026-01-01T00:00:00Z')",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO v2_job_events (event_id, job_id, stage, type, status, created_at, sequence) VALUES ('evt-s4', ?, 4, 'stage_start', 'running', '2026-01-01T00:00:00Z', 100)",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO v2_phase_gates (gate_id, job_id, gate_phase, stage_index, source_artifact_checksum, source_artifact_refs_json, created_at) VALUES ('gate-s4', ?, 'review', 4, 'chk', '[]', '2026-01-01T00:00:00Z')",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, evidence_checksum, artifact_refs_json, created_at, created_by) VALUES ('rev-s4', ?, 4, 'analysis', 'chk', '[]', '2026-01-01T00:00:00Z', 'system')",
            (job_id,),
        )

        # ── Stage 5 inserts fail in all seven tables ───────────────
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO v2_stage_commands (command_id, job_id, stage_index, manifest_checksum, created_at, updated_at) VALUES ('cmd-s5', ?, 5, 'chk', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (job_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO v2_approval_decisions (card_id, interrupt_id, request_checksum, stage_index, created_at, job_id) VALUES ('card-s5', 'int', 'chk', 5, '2026-01-01T00:00:00Z', ?)",
                (job_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO v2_resume_commands (resume_id, card_id, decision, job_id, stage_index, created_at) VALUES ('res-s5', 'card-s5', 'continue', ?, 5, '2026-01-01T00:00:00Z')",
                (job_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO v2_pending_action_drafts (action_id, job_id, action_type, stage_index, created_at) VALUES ('act-s5', ?, 'repair', 5, '2026-01-01T00:00:00Z')",
                (job_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO v2_job_events (event_id, job_id, stage, type, status, created_at, sequence) VALUES ('evt-s5', ?, 5, 'stage_start', 'running', '2026-01-01T00:00:00Z', 200)",
                (job_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO v2_phase_gates (gate_id, job_id, gate_phase, stage_index, source_artifact_checksum, source_artifact_refs_json, created_at) VALUES ('gate-s5', ?, 'review', 5, 'chk', '[]', '2026-01-01T00:00:00Z')",
                (job_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO v2_artifact_revisions (revision_id, job_id, stage_index, revision_kind, evidence_checksum, artifact_refs_json, created_at, created_by) VALUES ('rev-s5', ?, 5, 'analysis', 'chk', '[]', '2026-01-01T00:00:00Z', 'system')",
                (job_id,),
            )
    finally:
        connection.close()


def _migrated_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    apply_pending_migrations(connection)
    return connection


def _table_columns(connection: sqlite3.Connection, table_name: str) -> dict[str, sqlite3.Row]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]): row for row in rows}


def _primary_key_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [
        str(row["name"])
        for row in sorted(rows, key=lambda row: int(row["pk"]))
        if int(row["pk"]) > 0
    ]


def _foreign_keys_grouped(connection: sqlite3.Connection, table_name: str) -> list[dict[str, object]]:
    rows = connection.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
    grouped: dict[int, dict[str, object]] = {}
    for row in rows:
        group = grouped.setdefault(
            int(row["id"]),
            {"table": str(row["table"]), "from": [], "to": []},
        )
        group["from"].append(str(row["from"]))  # type: ignore[union-attr]
        group["to"].append(str(row["to"]))  # type: ignore[union-attr]
    return list(grouped.values())


def _all_index_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'index'
          AND name NOT LIKE 'sqlite_autoindex_%'
        """
    ).fetchall()
    return {str(row["name"]) for row in rows}


def _seed_runner_profile(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO runner_profiles (
            runner_profile_id, runner_profile_version, display_name, schema_version,
            payload_json, payload_checksum, created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "profile-1",
            "v1",
            "Profile",
            "runner-profile/v1",
            "{}",
            "checksum-runner",
            "2026-01-01T00:00:00Z",
            "tester",
        ),
    )


def _seed_pipeline_definition(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO pipeline_definitions (
            pipeline_id, pipeline_version, display_name, schema_version,
            graph_version, graph_state_schema_version, payload_json, payload_checksum,
            created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "pipeline-1",
            "v1",
            "Pipeline",
            "pipeline-definition/v1",
            "graph-v1",
            "graph-state/v1",
            "{}",
            "checksum-pipeline",
            "2026-01-01T00:00:00Z",
            "tester",
        ),
    )


def _seed_foundation_references(connection: sqlite3.Connection) -> None:
    _seed_runner_profile(connection)
    _seed_pipeline_definition(connection)
    connection.execute(
        """
        INSERT INTO migration_jobs (
            job_id, version, status, active_slot, last_event_sequence,
            runner_profile_id, runner_profile_version, pipeline_id, pipeline_version,
            target_proof_level, achieved_proof_level, legacy_source_ref, output_root_ref,
            created_at, updated_at, started_at, finished_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job-1",
            1,
            "RUNNING",
            1,
            1,
            "profile-1",
            "v1",
            "pipeline-1",
            "v1",
            "ANALYZED",
            None,
            "legacy-ref",
            "output-ref",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            None,
            "tester",
        ),
    )
    connection.execute(
        """
        INSERT INTO stage_runs (
            stage_run_id, job_id, stage_index, stage_id, status,
            input_source_json, created_at, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "stage-1",
            "job-1",
            1,
            "analysis",
            "RUNNING",
            "{}",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            None,
        ),
    )


def _write_sql(directory: Path, name: str, sql: str) -> Path:
    path = directory / name
    path.write_text(sql.strip() + "\n", encoding="utf-8")
    return path
