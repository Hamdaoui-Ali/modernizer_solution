"""Local ASGI bootstrap for manual Control Tower diagnostic testing.

Sets ``CONTROL_TOWER_DEV_MODE=1`` so the migration runner auto-resets the
local SQLite database on checksum mismatch (e.g. after a branch pull that
changed migration files). In production this flag is absent and any
checksum mismatch is a hard crash.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

# Enable dev-mode auto-reset for local development.
os.environ.setdefault("CONTROL_TOWER_DEV_MODE", "1")

from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.domain.checksums import (
    canonical_json,
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteControlTowerUnitOfWork
from migration_factory.control_tower.schemas.pipeline_definition import PipelineDefinition


def _dev_root() -> Path:
    configured = os.environ.get("CONTROL_TOWER_DEV_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.cwd() / ".control-tower-dev"


def _db_path() -> Path:
    configured = os.environ.get("CONTROL_TOWER_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return _dev_root() / "control_tower.sqlite3"


def _unit_of_work_factory():
    return SqliteControlTowerUnitOfWork(connect_control_tower(_db_path()), close_connection=True)


def _ensure_seed_data() -> None:
    root = _dev_root()
    source = root / "source"
    output = root / "output"
    workspace = root / "workspace"
    for path in (source, output, workspace):
        path.mkdir(parents=True, exist_ok=True)

    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_control_tower(db_path)
    try:
        apply_pending_migrations(connection)
        _ensure_runner_profile(
            connection,
            runner_profile_id="runner-default",
            runner_profile_version="2026.06",
            source=source,
            output=output,
            workspace=workspace,
        )
        _ensure_pipeline(
            connection,
            pipeline_id="pipeline-default",
            pipeline_version="2026.06",
            display_name="Foundation diagnostic pipeline",
            stages=(
                {
                    "stage_index": 1,
                    "stage_id": "foundation-diagnostic",
                    "profile_id": "diagnostic-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "legacy_source"},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
            ),
        )
        _ensure_pipeline(
            connection,
            pipeline_id="springboot-216-to-356-java21-three-stage",
            pipeline_version="2026.06",
            display_name="V2 migration pipeline (3-stage)",
            stages=(
                {
                    "stage_index": 1,
                    "stage_id": "analysis",
                    "profile_id": "analysis-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "legacy_source"},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
                {
                    "stage_index": 2,
                    "stage_id": "planning",
                    "profile_id": "planning-profile",
                    "command_jdk": "jdk-21",
                    "input_source": {"kind": "previous_stage", "previous_stage_index": 1},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 21},
                },
                {
                    "stage_index": 3,
                    "stage_id": "finalize",
                    "profile_id": "finalize-profile",
                    "command_jdk": "jdk-21",
                    "input_source": {"kind": "previous_stage", "previous_stage_index": 2},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 21},
                },
            ),
        )
        _ensure_pipeline(
            connection,
            pipeline_id="springboot-216-to-400-java21-four-stage",
            pipeline_version="2026.06",
            display_name="V2 migration pipeline (4-stage with Boot 4)",
            stages=(
                {
                    "stage_index": 1,
                    "stage_id": "analysis",
                    "profile_id": "analysis-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "legacy_source"},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
                {
                    "stage_index": 2,
                    "stage_id": "planning",
                    "profile_id": "planning-profile",
                    "command_jdk": "jdk-21",
                    "input_source": {"kind": "previous_stage", "previous_stage_index": 1},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 21},
                },
                {
                    "stage_index": 3,
                    "stage_id": "finalize",
                    "profile_id": "finalize-profile",
                    "command_jdk": "jdk-21",
                    "input_source": {"kind": "previous_stage", "previous_stage_index": 2},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "3.5.14", "java": 21},
                },
                {
                    "stage_index": 4,
                    "stage_id": "boot4-migration",
                    "profile_id": "springboot-3.5-java21-to-4.0-java21",
                    "command_jdk": "jdk-21",
                    "input_source": {"kind": "previous_stage", "previous_stage_index": 3},
                    "continuation_policy_id": "default",
                    "target": {"spring_boot": "4.0.0", "java": 21},
                },
            ),
        )
    finally:
        connection.close()


def _ensure_runner_profile(
    connection,
    *,
    runner_profile_id: str,
    runner_profile_version: str,
    source: Path,
    output: Path,
    workspace: Path,
) -> None:
    payload = {
        "schema_version": "1.0.0",
        "runner_profile_id": runner_profile_id,
        "runner_profile_version": runner_profile_version,
        "display_name": "Default local runner",
        "python_executable": sys.executable,
        "ai_hub_path": str(_dev_root()),
        "maven": {
            "executable_path": "mvn",
            "expected_version": "3.9.9",
            "allow_wrapper": False,
        },
        "jdks": [
            {
                "jdk_id": "jdk-17",
                "java_home": str(_dev_root() / "jdk-17"),
                "expected_major": 17,
                "role": "source",
            },
            {
                "jdk_id": "jdk-21",
                "java_home": str(_dev_root() / "jdk-21"),
                "expected_major": 21,
                "role": "target",
            },
        ],
        "filesystem": {
            "roots": [
                {"root_id": "source-root", "kind": "source", "path": str(source)},
                {"root_id": "output-root", "kind": "output", "path": str(output)},
                {"root_id": "working-root", "kind": "output", "path": str(workspace)},
            ],
        },
        "network": {"mode": "allowlisted", "allowed_hosts": ["repo.local"]},
        "ai_profile": {"profile_id": "local-disabled"},
    }
    exists = connection.execute(
        """
        SELECT 1
        FROM runner_profiles
        WHERE runner_profile_id = ? AND runner_profile_version = ?
        """,
        (runner_profile_id, runner_profile_version),
    ).fetchone()
    if exists is None:
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
                canonical_json(payload),
                sha256_canonical_json(payload),
                utc_now_text(),
                "local-dev",
            ),
        )


def _ensure_pipeline(
    connection,
    *,
    pipeline_id: str,
    pipeline_version: str,
    display_name: str,
    stages: tuple[dict[str, object], ...],
) -> None:
    payload = {
        "schema_version": "1.0.0",
        "pipeline_id": pipeline_id,
        "pipeline_version": pipeline_version,
        "display_name": display_name,
        "graph_version": "1.0",
        "graph_state_schema_version": "1.0",
        "stages": tuple(stages),
    }
    validated_payload = PipelineDefinition.model_validate(payload).model_dump(mode="json")
    connection.execute(
        """
        INSERT INTO pipeline_definitions (
            pipeline_id, pipeline_version, display_name, schema_version,
            graph_version, graph_state_schema_version, payload_json, payload_checksum,
            created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pipeline_id, pipeline_version) DO UPDATE SET
            display_name = excluded.display_name,
            schema_version = excluded.schema_version,
            graph_version = excluded.graph_version,
            graph_state_schema_version = excluded.graph_state_schema_version,
            payload_json = excluded.payload_json,
            payload_checksum = excluded.payload_checksum,
            created_at = excluded.created_at,
            created_by = excluded.created_by
        """,
        (
            validated_payload["pipeline_id"],
            validated_payload["pipeline_version"],
            validated_payload["display_name"],
            validated_payload["schema_version"],
            validated_payload["graph_version"],
            validated_payload["graph_state_schema_version"],
            canonical_json(validated_payload),
            sha256_canonical_json(validated_payload),
            utc_now_text(),
            "local-dev",
        ),
    )


_ensure_seed_data()

app = create_app(_unit_of_work_factory)
