"""Tests for V2 migration job creation from setup."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.application.v2_job_service import (
    PIPELINE_ID,
    V2MigrationJobService,
)
from migration_factory.control_tower.adapters.fastapi.app import _v2_stages_from_job
from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.domain.checksums import (
    canonical_json_text,
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.domain.entities import RunConfigurationRecord
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
    V2PreflightResultRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqlitePipelineDefinitionRepository,
    SqliteRunConfigurationRepository,
    SqliteRunnerProfileRepository,
)
from migration_factory.control_tower.schemas.pipeline_definition import PipelineDefinition


def _mutation_headers() -> dict[str, str]:
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID

    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _api_client(tmp_path: Path, app=None) -> tuple[TestClient, sqlite3.Connection]:
    from migration_factory.control_tower.adapters.fastapi import create_app
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork

    conn = sqlite3.connect(
        tmp_path / "job_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    app = app or create_app(lambda: SqliteUnitOfWork(conn))
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return client, conn


def _make_setup(repo: SqliteV2SetupRepository) -> tuple[str, str]:
    service = V2SetupService(repo)
    req = CreateSetupRequest(
        run_name="test-job",
        legacy_app_path="/tmp/test-legacy",
        output_parent_path="/tmp/test-output",
        ai_hub_path="/tmp/test-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
    )
    dto = service.create_setup(req)
    return dto.setup_id, dto.setup_checksum


def _save_ready_preflight(
    repo: SqliteV2SetupRepository,
    *,
    setup_id: str,
    setup_checksum: str,
) -> None:
    now = utc_now_text()
    repo.save_preflight(
        V2PreflightResultRecord(
            preflight_id=f"preflight-{setup_id}",
            setup_id=setup_id,
            setup_checksum=setup_checksum,
            all_ready=True,
            legacy_app_exists=True,
            legacy_app_has_project_file=True,
            legacy_app_not_in_output_parent=True,
            output_parent_writable=True,
            ai_hub_root_exists=True,
            ai_hub_profiles_ready=True,
            ai_hub_catalogs_ready=True,
            ai_hub_policies_ready=True,
            jdk11_ready=True,
            jdk17_ready=True,
            jdk21_ready=True,
            maven_ready=True,
            pipeline_route_ready=True,
            legacy_marker_ready=True,
            output_parent_gate_ready=True,
            readiness_json=json.dumps(
                {
                    "legacy_app_exists": True,
                    "legacy_app_has_project_file": True,
                    "legacy_app_not_in_output_parent": True,
                    "output_parent_writable": True,
                    "ai_hub_root_exists": True,
                    "ai_hub_profiles_ready": True,
                    "ai_hub_catalogs_ready": True,
                    "ai_hub_policies_ready": True,
                    "jdk11_ready": True,
                    "jdk17_ready": True,
                    "jdk21_ready": True,
                    "maven_ready": True,
                    "pipeline_route_ready": True,
                    "legacy_marker_ready": True,
                    "output_parent_gate_ready": True,
                    "azure_model_ready": True,
                },
                separators=(",", ":"),
            ),
            warnings_json="[]",
            errors_json="[]",
            checked_at=now,
            checked_by="tester",
            correlation_id=None,
        )
    )


def _make_job_service(conn: sqlite3.Connection) -> V2MigrationJobService:
    return V2MigrationJobService(
        setup_repo=SqliteV2SetupRepository(conn),
        job_repo=SqliteV2JobRepository(conn),
        run_config_repo=SqliteRunConfigurationRepository(conn),
        runner_profile_repo=SqliteRunnerProfileRepository(conn),
        pipeline_repo=SqlitePipelineDefinitionRepository(conn),
    )


def _seed_exact_v2_dependencies(connection: sqlite3.Connection) -> None:
    now = utc_now_text()
    runner_payload = {
        "schema_version": "1.0.0",
        "runner_profile_id": "runner-default",
        "runner_profile_version": "2026.06",
        "display_name": "Default local runner",
        "python_executable": "/usr/bin/python",
        "ai_hub_path": "/tmp/ai-hub",
        "maven": {"executable_path": "mvn", "expected_version": "3.9.9", "allow_wrapper": False},
        "jdks": [
            {"jdk_id": "jdk-17", "java_home": "/tmp/jdk-17", "expected_major": 17, "role": "source"},
            {"jdk_id": "jdk-21", "java_home": "/tmp/jdk-21", "expected_major": 21, "role": "target"},
        ],
        "filesystem": {
            "roots": [
                {"root_id": "source-root", "kind": "source", "path": "/tmp/source"},
                {"root_id": "output-root", "kind": "output", "path": "/tmp/output"},
                {"root_id": "working-root", "kind": "output", "path": "/tmp/workspace"},
            ]
        },
        "network": {"mode": "allowlisted", "allowed_hosts": ["repo.local"]},
        "ai_profile": {"profile_id": "local-disabled"},
    }
    connection.execute(
        """
        INSERT OR IGNORE INTO runner_profiles (
            runner_profile_id, runner_profile_version, display_name, schema_version,
            payload_json, payload_checksum, created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            runner_payload["runner_profile_id"],
            runner_payload["runner_profile_version"],
            runner_payload["display_name"],
            runner_payload["schema_version"],
            canonical_json_text(runner_payload),
            sha256_canonical_json(runner_payload),
            now,
            "tester",
        ),
    )

    pipeline_payload = {
        "schema_version": "1.0.0",
        "pipeline_id": PIPELINE_ID,
        "pipeline_version": "2026.06",
        "display_name": "V2 migration pipeline",
        "graph_version": "1.0",
        "graph_state_schema_version": "1.0",
        "stages": (
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
    }
    connection.execute(
        """
        INSERT OR IGNORE INTO pipeline_definitions (
            pipeline_id, pipeline_version, display_name, schema_version,
            graph_version, graph_state_schema_version, payload_json, payload_checksum,
            created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pipeline_payload["pipeline_id"],
            pipeline_payload["pipeline_version"],
            pipeline_payload["display_name"],
            pipeline_payload["schema_version"],
            pipeline_payload["graph_version"],
            pipeline_payload["graph_state_schema_version"],
            canonical_json_text(pipeline_payload),
            sha256_canonical_json(pipeline_payload),
            now,
            "tester",
        ),
    )


def test_create_job_requires_setup(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test1.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    service = _make_job_service(conn)

    with pytest.raises(ValueError, match="not found"):
        service.create_job("nonexistent-setup")


def test_create_job_requires_preflight(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test2.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id, _ = _make_setup(repo)

    job_service = _make_job_service(conn)
    with pytest.raises(ValueError, match="No preflight"):
        job_service.create_job(setup_id)


def test_create_job_with_preflight_and_readiness(tmp_path: Path) -> None:
    """Setup with preflight but not yet ready should block job creation."""
    conn = sqlite3.connect(
        tmp_path / "test3.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id, _ = _make_setup(repo)

    # Run preflight (will be NOT ready since paths don't exist)
    setup_service = V2SetupService(repo)
    setup_service.run_preflight(setup_id)

    # Job creation should fail because preflight returns all_ready=False
    job_service = _make_job_service(conn)
    with pytest.raises(ValueError, match="not ready"):
        job_service.create_job(setup_id)


def test_create_job_persists_run_configuration_and_defaults_auto_on_green_policy(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "create_job.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    _seed_exact_v2_dependencies(conn)

    setup_repo = SqliteV2SetupRepository(conn)
    setup_id, setup_checksum = _make_setup(setup_repo)
    _save_ready_preflight(setup_repo, setup_id=setup_id, setup_checksum=setup_checksum)

    service = _make_job_service(conn)
    result = service.create_job(setup_id)

    assert result.pipeline_id == PIPELINE_ID
    assert result.stage_continuation_policy == "auto_on_green"

    run_config_row = conn.execute(
        """
        SELECT run_configuration_id, job_id, schema_version, runner_profile_id,
               runner_profile_version, pipeline_id, pipeline_version, target_proof_level,
               enabled_gates_json, policy_json, payload_json, payload_checksum, created_at
        FROM run_configurations
        WHERE job_id = ?
        """,
        (result.job_id,),
    ).fetchone()
    assert run_config_row is not None
    assert run_config_row["job_id"] == result.job_id
    assert run_config_row["runner_profile_id"] == "runner-default"
    assert run_config_row["pipeline_id"] == PIPELINE_ID
    payload = json.loads(run_config_row["payload_json"])
    assert payload["source_profile"] == "springboot-2.7-java11"
    assert payload["target_profile"] == "springboot-4.0-java21"
    assert run_config_row["policy_json"] == canonical_json_text(
        {
            "continue_after_warning": False,
            "enable_build_repair": True,
            "enable_runtime_gate": False,
            "enable_endpoint_gate": False,
            "enable_llm_repair_proposal": True,
            "max_repair_attempts": 3,
            "repair_scope": "build_only",
            "stage_continuation_policy": "auto_on_green",
        }
    )



def test_cancel_v2_migration_job_endpoint_is_idempotent(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    _seed_exact_v2_dependencies(conn)
    setup_repo = SqliteV2SetupRepository(conn)
    setup_id, setup_checksum = _make_setup(setup_repo)
    _save_ready_preflight(setup_repo, setup_id=setup_id, setup_checksum=setup_checksum)
    job = _make_job_service(conn).create_job(setup_id)

    response = client.post(
        f"/v1/v2/migration-jobs/{job.job_id}/cancel",
        json={},
        headers=_mutation_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cancelled"
    assert payload["process"] == {
        "process_found": False,
        "terminated": False,
        "process_count": 0,
    }

    second_response = client.post(
        f"/v1/v2/migration-jobs/{job.job_id}/cancel",
        json={},
        headers=_mutation_headers(),
    )
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "already_cancelled"

    with conn:
        events = conn.execute(
            "SELECT type, status FROM v2_job_events WHERE job_id = ? ORDER BY sequence",
            (job.job_id,),
        ).fetchall()
    assert [(row["type"], row["status"]) for row in events][-3:] == [
        ("migration_cancelling", "cancelling"),
        ("stage_cancelled", "cancelled"),
        ("migration_cancelled", "cancelled"),
    ]


def test_cancelled_v2_job_projects_cancelled_stage_and_allows_new_job(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    _seed_exact_v2_dependencies(conn)
    setup_repo = SqliteV2SetupRepository(conn)
    setup_id, setup_checksum = _make_setup(setup_repo)
    _save_ready_preflight(setup_repo, setup_id=setup_id, setup_checksum=setup_checksum)
    service = _make_job_service(conn)
    job = service.create_job(setup_id)

    response = client.post(
        f"/v1/v2/migration-jobs/{job.job_id}/cancel",
        json={},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200

    stages_response = client.get(f"/v1/v2/migration-jobs/{job.job_id}/stages")
    assert stages_response.status_code == 200
    stages = stages_response.json()["stages"]
    assert stages[0]["chain_status"] == "cancelled"

    new_job = service.create_job(setup_id)
    assert new_job.job_id != job.job_id

def test_missing_exact_pipeline_seed_returns_clear_api_error(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    _seed_exact_v2_dependencies(conn)
    conn.execute(
        "DELETE FROM pipeline_definitions WHERE pipeline_id = ? AND pipeline_version = ?",
        (PIPELINE_ID, "2026.06"),
    )

    setup_repo = SqliteV2SetupRepository(conn)
    setup_id, setup_checksum = _make_setup(setup_repo)
    _save_ready_preflight(setup_repo, setup_id=setup_id, setup_checksum=setup_checksum)

    response = client.post(
        "/v1/v2/migration-jobs",
        json={"setup_id": setup_id},
        headers=_mutation_headers(),
    )
    assert response.status_code == 400
    assert "pipeline definition" in response.json()["error"]["message"].lower()
    assert PIPELINE_ID in response.json()["error"]["message"]


def test_dev_app_seeds_exact_v2_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dev_root = tmp_path / "dev-root"
    db_path = dev_root / "control_tower.sqlite3"
    monkeypatch.setenv("CONTROL_TOWER_DEV_MODE", "0")
    monkeypatch.setenv("CONTROL_TOWER_DEV_ROOT", str(dev_root))
    monkeypatch.setenv("CONTROL_TOWER_DB_PATH", str(db_path))

    from migration_factory.control_tower.adapters.fastapi import dev_app

    dev_app._ensure_seed_data()

    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    try:
        runner_row = conn.execute(
            """
            SELECT runner_profile_id, runner_profile_version, payload_json
            FROM runner_profiles
            WHERE runner_profile_id = ? AND runner_profile_version = ?
            """,
            ("runner-default", "2026.06"),
        ).fetchone()
        pipeline_row = conn.execute(
            """
            SELECT pipeline_id, pipeline_version, payload_json
            FROM pipeline_definitions
            WHERE pipeline_id = ? AND pipeline_version = ?
            """,
            (PIPELINE_ID, "2026.06"),
        ).fetchone()
    finally:
        conn.close()

    assert runner_row is not None
    assert pipeline_row is not None
    pipeline = PipelineDefinition.model_validate_json(pipeline_row["payload_json"])
    assert pipeline.pipeline_id == PIPELINE_ID
    assert len(pipeline.stages) == 4
    assert [stage.input_source.kind for stage in pipeline.stages] == [
        "legacy_source",
        "previous_stage",
        "previous_stage",
        "previous_stage",
    ]
    assert pipeline.stages[1].input_source.previous_stage_index == 1
    assert pipeline.stages[2].input_source.previous_stage_index == 2
    assert pipeline.stages[3].input_source.previous_stage_index == 3


def test_dev_app_repairs_invalid_seeded_v2_pipeline_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev_root = tmp_path / "dev-root"
    db_path = dev_root / "control_tower.sqlite3"
    monkeypatch.setenv("CONTROL_TOWER_DEV_MODE", "0")
    monkeypatch.setenv("CONTROL_TOWER_DEV_ROOT", str(dev_root))
    monkeypatch.setenv("CONTROL_TOWER_DB_PATH", str(db_path))

    from importlib import reload
    from migration_factory.control_tower.adapters.fastapi import dev_app

    reload(dev_app)

    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    try:
        bad_payload = {
            "schema_version": "1.0.0",
            "pipeline_id": PIPELINE_ID,
            "pipeline_version": "2026.06",
            "display_name": "Broken V2 migration pipeline",
            "graph_version": "1.0",
            "graph_state_schema_version": "1.0",
            "stages": [
                {
                    "stage_index": 1,
                    "stage_id": "analysis",
                    "profile_id": "analysis-profile",
                    "command_jdk": "jdk-17",
                    "input_source": {"kind": "legacy_source"},
                    "continuation_policy_id": "manual",
                    "target": {"spring_boot": "3.5.14", "java": 17},
                },
                {
                    "stage_index": 2,
                    "stage_id": "planning",
                    "profile_id": "planning-profile",
                    "command_jdk": "jdk-21",
                    "input_source": {"kind": "stage_1_sandbox"},
                    "continuation_policy_id": "manual",
                    "target": {"spring_boot": "3.5.14", "java": 21},
                },
                {
                    "stage_index": 3,
                    "stage_id": "finalize",
                    "profile_id": "finalize-profile",
                    "command_jdk": "jdk-21",
                    "input_source": {"kind": "stage_2_sandbox"},
                    "continuation_policy_id": "manual",
                    "target": {"spring_boot": "3.5.14", "java": 21},
                },
            ],
        }
        conn.execute(
            """
            UPDATE pipeline_definitions
            SET display_name = ?,
                payload_json = ?,
                payload_checksum = ?,
                created_at = ?,
                created_by = ?
            WHERE pipeline_id = ? AND pipeline_version = ?
            """,
            (
                bad_payload["display_name"],
                json.dumps(bad_payload, separators=(",", ":")),
                "broken-checksum",
                utc_now_text(),
                "tester",
                PIPELINE_ID,
                "2026.06",
            ),
        )
        conn.commit()

        dev_app._ensure_seed_data()

        row = conn.execute(
            """
            SELECT payload_json
            FROM pipeline_definitions
            WHERE pipeline_id = ? AND pipeline_version = ?
            """,
            (PIPELINE_ID, "2026.06"),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    pipeline = PipelineDefinition.model_validate_json(row["payload_json"])
    assert pipeline.stages[0].continuation_policy_id == "default"
    assert [stage.input_source.kind for stage in pipeline.stages] == [
        "legacy_source",
        "previous_stage",
        "previous_stage",
        "previous_stage",
    ]
    assert pipeline.stages[1].input_source.previous_stage_index == 1
    assert pipeline.stages[2].input_source.previous_stage_index == 2
    assert pipeline.stages[3].input_source.previous_stage_index == 3


def test_create_job_endpoint_returns_201_with_seeded_dependencies(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    _seed_exact_v2_dependencies(conn)

    setup_repo = SqliteV2SetupRepository(conn)
    setup_id, setup_checksum = _make_setup(setup_repo)
    _save_ready_preflight(setup_repo, setup_id=setup_id, setup_checksum=setup_checksum)

    response = client.post(
        "/v1/v2/migration-jobs",
        json={"setup_id": setup_id},
        headers=_mutation_headers(),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["pipeline_id"] == PIPELINE_ID
    assert data["source_profile"] == "springboot-2.7-java11"
    assert data["target_profile"] == "springboot-4.0-java21"
    assert data["stage_continuation_policy"] == "auto_on_green"
    assert data["run_configuration_id"]
    assert data["validation_status"] == "valid"
    assert data["included_stages"] == [2, 3, 4]
    assert data["excluded_stages"] == []
    assert data["skipped_stages"] == []


def test_create_job_endpoint_accepts_explicit_profile_selection(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    _seed_exact_v2_dependencies(conn)

    setup_repo = SqliteV2SetupRepository(conn)
    setup_id, setup_checksum = _make_setup(setup_repo)
    _save_ready_preflight(setup_repo, setup_id=setup_id, setup_checksum=setup_checksum)

    response = client.post(
        "/v1/v2/migration-jobs",
        json={
            "setup_id": setup_id,
            "source_profile": "springboot-3.5-java17",
            "target_profile": "springboot-4.0-java21",
        },
        headers=_mutation_headers(),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["source_profile"] == "springboot-3.5-java17"
    assert data["target_profile"] == "springboot-4.0-java21"
    assert data["validation_status"] == "valid"
    assert data["included_stages"] == [3, 4]
    assert data["excluded_stages"] == []
    assert data["skipped_stages"] == [2]

    row = conn.execute(
        "SELECT payload_json FROM run_configurations WHERE job_id = ?",
        (data["job_id"],),
    ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["source_profile"] == "springboot-3.5-java17"
    assert payload["target_profile"] == "springboot-4.0-java21"


def test_create_job_endpoint_accepts_explicit_auto_on_green_policy_contract(
    tmp_path: Path,
) -> None:
    client, conn = _api_client(tmp_path)
    _seed_exact_v2_dependencies(conn)

    setup_repo = SqliteV2SetupRepository(conn)
    setup_id, setup_checksum = _make_setup(setup_repo)
    _save_ready_preflight(setup_repo, setup_id=setup_id, setup_checksum=setup_checksum)

    response = client.post(
        "/v1/v2/migration-jobs",
        json={
            "setup_id": setup_id,
            "policy": {"stage_continuation_policy": "auto_on_green"},
        },
        headers=_mutation_headers(),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["stage_continuation_policy"] == "auto_on_green"
    assert data["run_configuration_id"]


def test_create_job_endpoint_accepts_partial_policy_payload(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    _seed_exact_v2_dependencies(conn)

    setup_repo = SqliteV2SetupRepository(conn)
    setup_id, setup_checksum = _make_setup(setup_repo)
    _save_ready_preflight(setup_repo, setup_id=setup_id, setup_checksum=setup_checksum)

    response = client.post(
        "/v1/v2/migration-jobs",
        json={
            "setup_id": setup_id,
            "policy": {"stage_continuation_policy": "auto_on_green"},
        },
        headers=_mutation_headers(),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["stage_continuation_policy"] == "auto_on_green"
    row = conn.execute(
        "SELECT policy_json FROM run_configurations WHERE job_id = ?",
        (data["job_id"],),
    ).fetchone()
    assert row is not None
    assert json.loads(row["policy_json"]) == {
        "continue_after_warning": False,
        "enable_runtime_gate": False,
        "enable_endpoint_gate": False,
        "enable_build_repair": True,
        "enable_llm_repair_proposal": True,
        "max_repair_attempts": 3,
        "repair_scope": "build_only",
        "stage_continuation_policy": "auto_on_green",
    }


def test_stale_run_configurations_fk_allows_job_id_without_migration_job_row(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(
        tmp_path / "fk_check.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    _seed_exact_v2_dependencies(conn)

    run_config_repo = SqliteRunConfigurationRepository(conn)
    record = RunConfigurationRecord(
        run_configuration_id="run-config-test",
        job_id="missing-job-id",
        schema_version="1.0.0",
        runner_profile_id="runner-default",
        runner_profile_version="2026.06",
        pipeline_id=PIPELINE_ID,
        pipeline_version="2026.06",
        target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
        enabled_gates_json="[]",
        policy_json='{"continue_after_warning":false,"enable_runtime_gate":false,"enable_endpoint_gate":false,"enable_build_repair":true,"enable_llm_repair_proposal":true,"max_repair_attempts":3,"repair_scope":"build_only","stage_continuation_policy":"auto_on_green"}',
        payload_json='{"schema_version":"1.0.0"}',
        payload_checksum="checksum",
        created_at=utc_now_text(),
    )
    run_config_repo.insert(record)

    row = conn.execute(
        "SELECT job_id FROM run_configurations WHERE run_configuration_id = ?",
        ("run-config-test",),
    ).fetchone()
    assert row is not None
    assert row["job_id"] == "missing-job-id"


def test_create_job_requires_valid_setup_id(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test5.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    service = _make_job_service(conn)

    with pytest.raises(ValueError):
        service.create_job("")


def test_stage_inputs_are_fixed(tmp_path: Path) -> None:
    """Stage inputs must come from STAGE_INPUTS, not from user."""
    from migration_factory.control_tower.application.v2_job_service import STAGE_INPUTS

    assert STAGE_INPUTS[1]["input_kind"] == "legacy_source"
    assert STAGE_INPUTS[2]["input_kind"] == "stage_1_sandbox"
    assert STAGE_INPUTS[3]["input_kind"] == "stage_2_sandbox"
    assert STAGE_INPUTS[4]["input_kind"] == "stage_3_sandbox"


def test_create_job_endpoint_rejects_missing_setup(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.post(
        "/v1/v2/migration-jobs",
        json={"setup_id": "nonexistent"},
        headers=_mutation_headers(),
    )
    assert response.status_code == 400
    assert "not found" in response.json()["error"]["message"].lower()


def test_create_job_endpoint_rejects_wrong_payload(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.post(
        "/v1/v2/migration-jobs",
        json={"setup_id": "test", "extra_field": "bad"},
        headers=_mutation_headers(),
    )
    assert response.status_code == 422


def test_result_to_dict_has_correct_shape(tmp_path: Path) -> None:
    from migration_factory.control_tower.application.v2_job_service import V2MigrationJobResult

    conn = sqlite3.connect(
        tmp_path / "test_shape.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    service = _make_job_service(conn)

    result = V2MigrationJobResult(
        job_id="test-job-id",
        setup_id="test-setup-id",
        setup_checksum="abc123",
        pipeline_id=PIPELINE_ID,
        stages=(
            {"stage_index": 1, "stage_run_id": "run1", "pipeline_stage": "Stage 1",
             "input_source_kind": "legacy_source", "chain_status": "queued"},
            {"stage_index": 2, "stage_run_id": "run2", "pipeline_stage": "Stage 2",
             "input_source_kind": "stage_1_sandbox", "chain_status": "pending"},
            {"stage_index": 3, "stage_run_id": "run3", "pipeline_stage": "Stage 3",
             "input_source_kind": "stage_2_sandbox", "chain_status": "pending"},
            {"stage_index": 4, "stage_run_id": "run4", "pipeline_stage": "Stage 4",
             "input_source_kind": "stage_3_sandbox", "chain_status": "pending"},
        ),
        created_at="2026-06-13T00:00:00Z",
        source_profile="springboot-2.7-java11",
        target_profile="springboot-4.0-java21",
    )
    d = service.result_to_dict(result)
    assert d["job_id"] == "test-job-id"
    assert d["pipeline_id"] == PIPELINE_ID
    assert len(d["stages"]) == 4
    assert d["stages"][0]["chain_status"] == "queued"
    assert d["stages"][1]["chain_status"] == "pending"
    assert d["stages"][2]["input_source_kind"] == "stage_2_sandbox"
    assert d["stages"][3]["stage_index"] == 4


def test_stage4_projection_uses_backend_command_status() -> None:
    job = SimpleNamespace(
        stage_chain_json=json.dumps([
            {"stage_index": 1, "stage_run_id": "run1", "pipeline_stage": "Stage 1", "input_source_kind": "legacy_source", "chain_status": "pending"},
            {"stage_index": 2, "stage_run_id": "run2", "pipeline_stage": "Stage 2", "input_source_kind": "stage_1_sandbox", "chain_status": "pending"},
            {"stage_index": 3, "stage_run_id": "run3", "pipeline_stage": "Stage 3", "input_source_kind": "stage_2_sandbox", "chain_status": "pending"},
            {"stage_index": 4, "stage_run_id": "run4", "pipeline_stage": "Stage 4", "input_source_kind": "stage_3_sandbox", "chain_status": "pending"},
        ])
    )
    events = (
        SimpleNamespace(stage=1, type="stage_completed", status="completed", payload_json="{}", sequence=1),
        SimpleNamespace(stage=2, type="stage_completed", status="completed", payload_json="{}", sequence=2),
        SimpleNamespace(stage=3, type="stage_completed", status="completed", payload_json="{}", sequence=3),
    )

    stages_without_command = _v2_stages_from_job(job, (), events)
    assert stages_without_command[3]["stage_index"] == 4
    assert stages_without_command[3]["chain_status"] == "pending"

    stages_with_command = _v2_stages_from_job(
        job,
        (SimpleNamespace(stage_index=4),),
        events,
    )
    assert stages_with_command[3]["stage_index"] == 4
    assert stages_with_command[3]["chain_status"] == "queued"


def test_create_job_persistence_across_connections(tmp_path: Path) -> None:
    """Created job should survive connection close/reopen."""
    db_path = tmp_path / "persist_test.sqlite3"

    conn1 = sqlite3.connect(
        db_path, check_same_thread=False, isolation_level=None, timeout=5.0
    )
    conn1.row_factory = sqlite3.Row
    conn1.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn1)
    _seed_exact_v2_dependencies(conn1)
    repo1 = SqliteV2SetupRepository(conn1)
    setup_id, setup_checksum = _make_setup(repo1)
    _save_ready_preflight(repo1, setup_id=setup_id, setup_checksum=setup_checksum)
    conn1.close()

    conn2 = sqlite3.connect(
        db_path, check_same_thread=False, isolation_level=None, timeout=5.0
    )
    conn2.row_factory = sqlite3.Row
    conn2.execute("PRAGMA foreign_keys = ON")
    service = _make_job_service(conn2)
    result = service.create_job(setup_id)
    assert result.job_id
    conn2.close()

    conn3 = sqlite3.connect(
        db_path, check_same_thread=False, isolation_level=None, timeout=5.0
    )
    conn3.row_factory = sqlite3.Row
    conn3.execute("PRAGMA foreign_keys = ON")
    job_repo3 = SqliteV2JobRepository(conn3)
    loaded = job_repo3.get(result.job_id)
    assert loaded is not None
    assert loaded.job_id == result.job_id
    assert loaded.status == "created"
    conn3.close()


def test_get_job_returns_none_for_missing(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test_get.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    service = _make_job_service(conn)
    assert service.get_job("nonexistent") is None


def test_list_jobs_returns_empty(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test_list.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    service = _make_job_service(conn)
    assert service.list_jobs() == ()
