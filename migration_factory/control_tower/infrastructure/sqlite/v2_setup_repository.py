"""SQLite repository for V2 migration setup drafts and preflight results."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class V2MigrationSetupRecord:
    setup_id: str
    run_name: str
    legacy_app_path: str
    output_parent_path: str
    ai_hub_path: str
    java11_home: str
    java17_home: str
    java21_home: str
    maven_cmd: str
    proof_level: str
    skip_endpoint_smoke: bool
    migration_flags_json: str
    setup_checksum: str
    checksum_algorithm: str
    created_at: str
    created_by: str
    correlation_id: str | None


@dataclass(frozen=True)
class V2PreflightResultRecord:
    preflight_id: str
    setup_id: str
    setup_checksum: str
    all_ready: bool
    legacy_app_exists: bool
    legacy_app_has_project_file: bool
    legacy_app_not_in_output_parent: bool
    output_parent_writable: bool
    ai_hub_root_exists: bool
    ai_hub_profiles_ready: bool
    ai_hub_catalogs_ready: bool
    ai_hub_policies_ready: bool
    jdk11_ready: bool
    jdk17_ready: bool
    jdk21_ready: bool
    maven_ready: bool
    pipeline_route_ready: bool
    legacy_marker_ready: bool
    output_parent_gate_ready: bool
    readiness_json: str
    warnings_json: str
    errors_json: str
    checked_at: str
    checked_by: str
    correlation_id: str | None


class SqliteV2SetupRepository:
    """Repository for V2 migration setup drafts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, record: V2MigrationSetupRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_migration_setups (
                setup_id, run_name, legacy_app_path, output_parent_path,
                ai_hub_path, java11_home, java17_home, java21_home,
                maven_cmd, proof_level, skip_endpoint_smoke,
                migration_flags_json, setup_checksum, checksum_algorithm,
                created_at, created_by, correlation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.setup_id,
                record.run_name,
                record.legacy_app_path,
                record.output_parent_path,
                record.ai_hub_path,
                record.java11_home,
                record.java17_home,
                record.java21_home,
                record.maven_cmd,
                record.proof_level,
                1 if record.skip_endpoint_smoke else 0,
                record.migration_flags_json,
                record.setup_checksum,
                record.checksum_algorithm,
                record.created_at,
                record.created_by,
                record.correlation_id,
            ),
        )

    def get(self, setup_id: str) -> V2MigrationSetupRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_migration_setups WHERE setup_id = ?",
            (setup_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_setup(row)

    def get_by_checksum(self, checksum: str) -> V2MigrationSetupRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_migration_setups WHERE setup_checksum = ? ORDER BY created_at DESC LIMIT 1",
            (checksum,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_setup(row)

    def list(self) -> tuple[V2MigrationSetupRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM v2_migration_setups ORDER BY created_at DESC"
        ).fetchall()
        return tuple(self._row_to_setup(row) for row in rows)

    def save_preflight(self, record: V2PreflightResultRecord) -> None:
        self._connection.execute(
            """INSERT INTO v2_preflight_results (
                preflight_id, setup_id, setup_checksum, all_ready,
                legacy_app_exists, legacy_app_has_project_file,
                legacy_app_not_in_output_parent, output_parent_writable,
                ai_hub_root_exists, ai_hub_profiles_ready,
                ai_hub_catalogs_ready, ai_hub_policies_ready,
                jdk11_ready, jdk17_ready, jdk21_ready, maven_ready,
                pipeline_route_ready, legacy_marker_ready,
                output_parent_gate_ready, readiness_json, warnings_json,
                errors_json, checked_at, checked_by, correlation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.preflight_id,
                record.setup_id,
                record.setup_checksum,
                1 if record.all_ready else 0,
                1 if record.legacy_app_exists else 0,
                1 if record.legacy_app_has_project_file else 0,
                1 if record.legacy_app_not_in_output_parent else 0,
                1 if record.output_parent_writable else 0,
                1 if record.ai_hub_root_exists else 0,
                1 if record.ai_hub_profiles_ready else 0,
                1 if record.ai_hub_catalogs_ready else 0,
                1 if record.ai_hub_policies_ready else 0,
                1 if record.jdk11_ready else 0,
                1 if record.jdk17_ready else 0,
                1 if record.jdk21_ready else 0,
                1 if record.maven_ready else 0,
                1 if record.pipeline_route_ready else 0,
                1 if record.legacy_marker_ready else 0,
                1 if record.output_parent_gate_ready else 0,
                record.readiness_json,
                record.warnings_json,
                record.errors_json,
                record.checked_at,
                record.checked_by,
                record.correlation_id,
            ),
        )

    def get_latest_preflight(self, setup_id: str) -> V2PreflightResultRecord | None:
        row = self._connection.execute(
            "SELECT * FROM v2_preflight_results WHERE setup_id = ? ORDER BY checked_at DESC LIMIT 1",
            (setup_id,),
        ).fetchone()
        if row is None:
            return None
        return V2PreflightResultRecord(
            preflight_id=str(row["preflight_id"]),
            setup_id=str(row["setup_id"]),
            setup_checksum=str(row["setup_checksum"]),
            all_ready=bool(row["all_ready"]),
            legacy_app_exists=bool(row["legacy_app_exists"]),
            legacy_app_has_project_file=bool(row["legacy_app_has_project_file"]),
            legacy_app_not_in_output_parent=bool(row["legacy_app_not_in_output_parent"]),
            output_parent_writable=bool(row["output_parent_writable"]),
            ai_hub_root_exists=bool(row["ai_hub_root_exists"]),
            ai_hub_profiles_ready=bool(row["ai_hub_profiles_ready"]),
            ai_hub_catalogs_ready=bool(row["ai_hub_catalogs_ready"]),
            ai_hub_policies_ready=bool(row["ai_hub_policies_ready"]),
            jdk11_ready=bool(row["jdk11_ready"]),
            jdk17_ready=bool(row["jdk17_ready"]),
            jdk21_ready=bool(row["jdk21_ready"]),
            maven_ready=bool(row["maven_ready"]),
            pipeline_route_ready=bool(row["pipeline_route_ready"]),
            legacy_marker_ready=bool(row["legacy_marker_ready"]),
            output_parent_gate_ready=bool(row["output_parent_gate_ready"]),
            readiness_json=str(row["readiness_json"]),
            warnings_json=str(row["warnings_json"]),
            errors_json=str(row["errors_json"]),
            checked_at=str(row["checked_at"]),
            checked_by=str(row["checked_by"]),
            correlation_id=str(row["correlation_id"]) if row["correlation_id"] else None,
        )

    def get_latest_preflight_by_checksum(self, checksum: str) -> V2PreflightResultRecord | None:
        row = self._connection.execute(
            """SELECT * FROM v2_preflight_results
               WHERE setup_checksum = ?
               ORDER BY checked_at DESC LIMIT 1""",
            (checksum,),
        ).fetchone()
        if row is None:
            return None
        return V2PreflightResultRecord(
            preflight_id=str(row["preflight_id"]),
            setup_id=str(row["setup_id"]),
            setup_checksum=str(row["setup_checksum"]),
            all_ready=bool(row["all_ready"]),
            legacy_app_exists=bool(row["legacy_app_exists"]),
            legacy_app_has_project_file=bool(row["legacy_app_has_project_file"]),
            legacy_app_not_in_output_parent=bool(row["legacy_app_not_in_output_parent"]),
            output_parent_writable=bool(row["output_parent_writable"]),
            ai_hub_root_exists=bool(row["ai_hub_root_exists"]),
            ai_hub_profiles_ready=bool(row["ai_hub_profiles_ready"]),
            ai_hub_catalogs_ready=bool(row["ai_hub_catalogs_ready"]),
            ai_hub_policies_ready=bool(row["ai_hub_policies_ready"]),
            jdk11_ready=bool(row["jdk11_ready"]),
            jdk17_ready=bool(row["jdk17_ready"]),
            jdk21_ready=bool(row["jdk21_ready"]),
            maven_ready=bool(row["maven_ready"]),
            pipeline_route_ready=bool(row["pipeline_route_ready"]),
            legacy_marker_ready=bool(row["legacy_marker_ready"]),
            output_parent_gate_ready=bool(row["output_parent_gate_ready"]),
            readiness_json=str(row["readiness_json"]),
            warnings_json=str(row["warnings_json"]),
            errors_json=str(row["errors_json"]),
            checked_at=str(row["checked_at"]),
            checked_by=str(row["checked_by"]),
            correlation_id=str(row["correlation_id"]) if row["correlation_id"] else None,
        )

    def _row_to_setup(self, row: sqlite3.Row) -> V2MigrationSetupRecord:
        return V2MigrationSetupRecord(
            setup_id=str(row["setup_id"]),
            run_name=str(row["run_name"]),
            legacy_app_path=str(row["legacy_app_path"]),
            output_parent_path=str(row["output_parent_path"]),
            ai_hub_path=str(row["ai_hub_path"]),
            java11_home=str(row["java11_home"]),
            java17_home=str(row["java17_home"]),
            java21_home=str(row["java21_home"]),
            maven_cmd=str(row["maven_cmd"]),
            proof_level=str(row["proof_level"]),
            skip_endpoint_smoke=bool(row["skip_endpoint_smoke"]),
            migration_flags_json=str(row["migration_flags_json"]),
            setup_checksum=str(row["setup_checksum"]),
            checksum_algorithm=str(row["checksum_algorithm"]),
            created_at=str(row["created_at"]),
            created_by=str(row["created_by"]),
            correlation_id=str(row["correlation_id"]) if row["correlation_id"] else None,
        )
