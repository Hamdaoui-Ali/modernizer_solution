from __future__ import annotations

import sqlite3
from pathlib import Path

from migration_factory.control_tower.application.services import ControlTowerRegistrationService
from migration_factory.control_tower.domain.states import JobState
from migration_factory.control_tower.domain.transitions import active_slot_for
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork


_DEFAULT_ACTIVE_SLOT = object()


def migrated_connection(tmp_path: Path, name: str = "control_tower.sqlite3") -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / name)
    apply_pending_migrations(connection)
    return connection


def service(connection: sqlite3.Connection) -> ControlTowerRegistrationService:
    return ControlTowerRegistrationService(lambda: SqliteUnitOfWork(connection))


def seed_job(
    connection: sqlite3.Connection,
    *,
    job_id: str = "job-1",
    version: int = 1,
    status: JobState = JobState.CREATED,
    active_slot: int | None | object = _DEFAULT_ACTIVE_SLOT,
    last_event_sequence: int = 0,
    created_at: str = "2026-01-01T00:00:00Z",
    updated_at: str = "2026-01-01T00:00:00Z",
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    seed_foundation_references(connection)
    resolved_active_slot = active_slot_for(status) if active_slot is _DEFAULT_ACTIVE_SLOT else active_slot
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
            job_id,
            version,
            status.value,
            resolved_active_slot,
            last_event_sequence,
            "profile-1",
            "v1",
            "pipeline-1",
            "v1",
            "ANALYZED",
            None,
            "legacy-ref",
            "output-ref",
            created_at,
            updated_at,
            started_at,
            finished_at,
            "tester",
        ),
    )


def fetch_job(connection: sqlite3.Connection, job_id: str = "job-1") -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM migration_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert row is not None
    return row


def count_run_events(connection: sqlite3.Connection, job_id: str = "job-1") -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM run_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
    )


def count_audit_records(connection: sqlite3.Connection, job_id: str = "job-1") -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM audit_records WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
    )


def seed_foundation_references(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO runner_profiles (
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
    connection.execute(
        """
        INSERT OR IGNORE INTO pipeline_definitions (
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
