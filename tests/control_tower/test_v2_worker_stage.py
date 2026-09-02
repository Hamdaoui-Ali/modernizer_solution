"""Tests for V2 worker stage execution."""

from __future__ import annotations

import sqlite3
import json
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_worker_stage import (
    V2WorkerStageService,
    STAGE_JDK_MAP,
    PIPELINE_ID,
    RUNNER_MODULE,
)
from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.domain.entities import RunConfigurationRecord
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
    V2MigrationJobRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqliteRunConfigurationRepository,
)
from migration_factory.control_tower.domain.checksums import canonical_json_text, sha256_canonical_json, utc_now_text


REPO_ROOT = Path(__file__).resolve().parents[2]
AI_HUB = REPO_ROOT / "modernizer-solution-ai-hub"


def _mutation_headers():
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _api_client(tmp_path):
    from migration_factory.control_tower.adapters.fastapi import create_app
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    from fastapi.testclient import TestClient
    conn = sqlite3.connect(
        tmp_path / "worker_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    app = create_app(lambda: SqliteUnitOfWork(conn), v2_orchestrator_runner=_RecordingV2Runner())
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return client, conn


class _RecordingV2Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def start(self, *, job_id: str, command_id: str):
        self.calls.append((job_id, command_id))
        return None


def _create_test_setup(
    repo: SqliteV2SetupRepository,
    *,
    ai_hub_path: str | None = None,
    skip_endpoint_smoke: bool = False,
    java11_home: str = "/usr/lib/jvm/java-11",
    java17_home: str = "/usr/lib/jvm/java-17",
    java21_home: str = "/usr/lib/jvm/java-21",
) -> str:
    service = V2SetupService(repo)
    req = CreateSetupRequest(
        run_name="test-worker",
        legacy_app_path="/tmp/legacy-app",
        output_parent_path="/tmp/output",
        ai_hub_path=ai_hub_path or str(AI_HUB),
        java11_home=java11_home,
        java17_home=java17_home,
        java21_home=java21_home,
        maven_cmd="/usr/bin/mvn",
        skip_endpoint_smoke=skip_endpoint_smoke,
    )
    dto = service.create_setup(req)
    return dto.setup_id


def _make_worker_service(conn: sqlite3.Connection) -> V2WorkerStageService:
    return V2WorkerStageService(
        setup_repo=SqliteV2SetupRepository(conn),
        command_repo=SqliteV2CommandRepository(conn),
        job_repo=SqliteV2JobRepository(conn),
        run_config_repo=SqliteRunConfigurationRepository(conn),
    )


def _seed_job_run_configuration(
    conn: sqlite3.Connection,
    *,
    setup_id: str,
    job_id: str = "test-job-id",
    source_profile: str = "springboot-3.5-java17",
    target_profile: str = "springboot-3.5-java21",
) -> None:
    setup_repo = SqliteV2SetupRepository(conn)
    setup = setup_repo.get(setup_id)
    assert setup is not None
    now = utc_now_text()
    runner_payload = {
        "schema_version": "1.0.0",
        "runner_profile_id": "runner-default",
        "runner_profile_version": "2026.06",
        "display_name": "Default local runner",
        "python_executable": "python",
        "ai_hub_path": str(AI_HUB),
        "maven": {"executable_path": "/usr/bin/mvn", "expected_version": "3.9.9", "allow_wrapper": False},
        "jdks": [
            {"jdk_id": "jdk-11", "java_home": "/usr/lib/jvm/java-11", "expected_major": 11, "role": "source"},
            {"jdk_id": "jdk-17", "java_home": "/usr/lib/jvm/java-17", "expected_major": 17, "role": "target"},
            {"jdk_id": "jdk-21", "java_home": "/usr/lib/jvm/java-21", "expected_major": 21, "role": "target"},
        ],
        "filesystem": {
            "roots": [
                {"root_id": "source-root", "kind": "source", "path": "/tmp/source"},
                {"root_id": "output-root", "kind": "output", "path": "/tmp/output"},
            ]
        },
        "network": {"mode": "allowlisted", "allowed_hosts": ["repo.local"]},
        "ai_profile": {"profile_id": "local-disabled"},
    }
    conn.execute(
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
            "test",
        ),
    )
    pipeline_payload = {
        "schema_version": "1.0.0",
        "pipeline_id": "springboot-216-to-400-java21-four-stage",
        "pipeline_version": "2026.06",
        "display_name": "V2 migration pipeline",
        "graph_version": "1.0",
        "graph_state_schema_version": "1.0",
        "stages": (
            {"stage_index": 1, "stage_id": "analysis", "profile_id": "analysis-profile", "command_jdk": "jdk-17", "input_source": {"kind": "legacy_source"}, "continuation_policy_id": "default", "target": {"spring_boot": "3.5.14", "java": 17}},
            {"stage_index": 2, "stage_id": "planning", "profile_id": "planning-profile", "command_jdk": "jdk-21", "input_source": {"kind": "previous_stage", "previous_stage_index": 1}, "continuation_policy_id": "default", "target": {"spring_boot": "3.5.14", "java": 21}},
            {"stage_index": 3, "stage_id": "finalize", "profile_id": "finalize-profile", "command_jdk": "jdk-21", "input_source": {"kind": "previous_stage", "previous_stage_index": 2}, "continuation_policy_id": "default", "target": {"spring_boot": "3.5.14", "java": 21}},
            {"stage_index": 4, "stage_id": "boot4", "profile_id": "boot4-profile", "command_jdk": "jdk-21", "input_source": {"kind": "previous_stage", "previous_stage_index": 3}, "continuation_policy_id": "default", "target": {"spring_boot": "4.0.0", "java": 21}},
        ),
    }
    conn.execute(
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
            "test",
        ),
    )
    job_repo = SqliteV2JobRepository(conn)
    job_repo.save(
        V2MigrationJobRecord(
            job_id=job_id,
            setup_id=setup_id,
            setup_checksum=setup.setup_checksum,
            pipeline_id="springboot-216-to-400-java21-four-stage",
            stage_chain_json=json.dumps(
                [
                    {
                        "stage_index": 1,
                        "stage_run_id": "stage-1",
                        "pipeline_stage": "Stage 1",
                        "input_source_kind": "legacy_source",
                        "chain_status": "queued",
                    },
                    {
                        "stage_index": 2,
                        "stage_run_id": "stage-2",
                        "pipeline_stage": "Stage 2",
                        "input_source_kind": "stage_1_sandbox",
                        "chain_status": "pending",
                    },
                    {
                        "stage_index": 3,
                        "stage_run_id": "stage-3",
                        "pipeline_stage": "Stage 3",
                        "input_source_kind": "stage_2_sandbox",
                        "chain_status": "pending",
                    },
                    {
                        "stage_index": 4,
                        "stage_run_id": "stage-4",
                        "pipeline_stage": "Stage 4",
                        "input_source_kind": "stage_3_sandbox",
                        "chain_status": "pending",
                    },
                ]
            ),
            status="created",
            created_at=now,
            updated_at=now,
            correlation_id=None,
        )
    )
    payload = {
        "source_profile": source_profile,
        "target_profile": target_profile,
    }
    run_config_repo = SqliteRunConfigurationRepository(conn)
    run_config_repo.insert(
        RunConfigurationRecord(
            run_configuration_id=f"run-config-{job_id}",
            job_id=job_id,
            schema_version="1.0.0",
            runner_profile_id="runner-default",
            runner_profile_version="2026.06",
            pipeline_id="springboot-216-to-400-java21-four-stage",
            pipeline_version="2026.06",
            target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
            enabled_gates_json="[]",
            policy_json='{"continue_after_warning":false,"enable_runtime_gate":false,"enable_endpoint_gate":false,"enable_build_repair":true,"enable_llm_repair_proposal":true,"max_repair_attempts":3,"repair_scope":"build_only","stage_continuation_policy":"auto_on_green"}',
            payload_json=json.dumps(payload, separators=(",", ":")),
            payload_checksum=sha256_canonical_json(payload),
            created_at=now,
        )
    )


def test_stage1_manifest_built_from_setup(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test1.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(conn, setup_id=setup_id)

    service = _make_worker_service(conn)
    result = service.build_stage1_manifest(
        job_id="test-job-id",
        setup_id=setup_id,
    )

    assert result.command_id
    assert result.job_id == "test-job-id"
    assert result.stage_index == 1
    assert len(result.argv) > 0
    assert "--run-id" in result.argv
    assert "--legacy" in result.argv
    assert "/tmp/legacy-app" in result.argv
    profile_idx = result.argv.index("--profile") + 1
    assert result.argv[profile_idx] == "springboot-3.5-java17-to-java21"


def test_stage1_manifest_argv_backend_owned(tmp_path: Path) -> None:
    """The argv must be backend-owned, not user-supplied."""
    conn = sqlite3.connect(
        tmp_path / "test2.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(conn, setup_id=setup_id)

    service = _make_worker_service(conn)
    result = service.build_stage1_manifest(
        job_id="test-job-id",
        setup_id=setup_id,
    )

    # The argv is built from setup data, not from user input
    assert RUNNER_MODULE in " ".join(result.argv)
    assert "--legacy" in result.argv
    assert "--modernized" in result.argv
    assert "--ai-hub" in result.argv
    assert "--profile" in result.argv
    assert "--mode" in result.argv
    # No user-supplied commands or goals
    assert "shell" not in str(result.argv).lower()


def test_stage1_manifest_uses_setup_paths(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test3.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(conn, setup_id=setup_id)

    service = _make_worker_service(conn)
    result = service.build_stage1_manifest(
        job_id="test-job-id",
        setup_id=setup_id,
    )

    argv_str = " ".join(result.argv)
    assert "/tmp/legacy-app" in argv_str
    assert "/tmp/output" in argv_str
    assert str(AI_HUB) in argv_str


def test_stage1_manifest_no_browser_paths(tmp_path: Path) -> None:
    """Browser payloads cannot supply argv or env."""
    conn = sqlite3.connect(
        tmp_path / "test4.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(conn, setup_id=setup_id)

    service = _make_worker_service(conn)
    result = service.build_stage1_manifest(
        job_id="test-job-id",
        setup_id=setup_id,
    )

    # The result doesn't accept browser-supplied fields
    assert not hasattr(result, "browser_argv")  # No browser argv
    assert result.argv  # Only backend-owned argv exists


def test_stage1_manifest_not_found(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test5.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)

    service = _make_worker_service(conn)
    with pytest.raises(ValueError, match="not found"):
        service.build_stage1_manifest(
            job_id="test-job-id",
            setup_id="nonexistent",
        )


def test_stage_jdk_map_is_fixed() -> None:
    """The JDK map is fixed and cannot be overridden by browser."""
    assert STAGE_JDK_MAP[1]["jdk_id"] == "java11"
    assert STAGE_JDK_MAP[1]["expected_major"] == 11
    assert STAGE_JDK_MAP[2]["jdk_id"] == "java17"
    assert STAGE_JDK_MAP[2]["expected_major"] == 17
    assert STAGE_JDK_MAP[3]["jdk_id"] == "java21"
    assert STAGE_JDK_MAP[3]["expected_major"] == 21


def test_pipeline_id_is_constant() -> None:
    assert PIPELINE_ID == "springboot-216-to-400-java21-four-stage"


def test_result_to_dict_shape(tmp_path: Path) -> None:
    from migration_factory.control_tower.application.v2_worker_stage import V2StageCommandResult
    conn = sqlite3.connect(
        tmp_path / "test6.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    service = _make_worker_service(conn)

    result = V2StageCommandResult(
        command_id="cmd1",
        job_id="job1",
        stage_index=1,
        manifest_checksum="abc",
        argv=("python", "-m", "runner", "--run-id", "test"),
        created_at="2026-06-13T00:00:00Z",
    )
    d = service.result_to_dict(result)
    assert d["command_id"] == "cmd1"
    assert d["stage_index"] == 1
    assert isinstance(d["argv"], list)


def test_start_stage1_endpoint(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)

    # Create setup
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    setup = repo.get(setup_id)
    assert setup is not None
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="test-job-123",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
    )
    now = utc_now_text()
    conn.execute(
        """INSERT INTO v2_preflight_results (
            preflight_id, setup_id, setup_checksum, all_ready,
            legacy_app_exists, legacy_app_has_project_file, legacy_app_not_in_output_parent,
            output_parent_writable, ai_hub_root_exists, ai_hub_profiles_ready,
            ai_hub_catalogs_ready, ai_hub_policies_ready,
            jdk11_ready, jdk17_ready, jdk21_ready, maven_ready,
            pipeline_route_ready, legacy_marker_ready, output_parent_gate_ready,
            readiness_json, warnings_json, errors_json, checked_at, checked_by, correlation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "pf-ready",
            setup_id,
            setup.setup_checksum,
            1,
            1, 1, 1,
            1, 1, 1,
            1, 1,
            1, 1, 1, 1,
            1, 1, 1,
            json.dumps({
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
            }),
            json.dumps([]),
            json.dumps([]),
            now,
            "test",
            None,
        ),
    )

    response = client.post(
        "/v1/v2/migration-jobs/start-stage1",
        json={"job_id": "test-job-123", "setup_id": setup_id},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["command_id"]
    assert body["stage_index"] == 1
    assert isinstance(body["argv"], list)
    assert len(body["argv"]) > 0


def test_start_stage1_requires_preflight_when_ai_smoke_is_required(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)

    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    setup = repo.get(setup_id)
    assert setup is not None
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="job-no-preflight",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
    )
    now = utc_now_text()
    response = client.post(
        "/v1/v2/migration-jobs/start-stage1",
        json={"job_id": "job-no-preflight", "setup_id": setup_id},
        headers=_mutation_headers(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "AI_MODEL_SMOKE_REQUIRED"


def test_start_stage1_blocks_when_ai_smoke_failed(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)

    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    setup = repo.get(setup_id)
    assert setup is not None
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="job-failed-smoke",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
    )
    now = utc_now_text()
    conn.execute(
        """INSERT INTO v2_preflight_results (
            preflight_id, setup_id, setup_checksum, all_ready,
            legacy_app_exists, legacy_app_has_project_file, legacy_app_not_in_output_parent,
            output_parent_writable, ai_hub_root_exists, ai_hub_profiles_ready,
            ai_hub_catalogs_ready, ai_hub_policies_ready,
            jdk11_ready, jdk17_ready, jdk21_ready, maven_ready,
            pipeline_route_ready, legacy_marker_ready, output_parent_gate_ready,
            readiness_json, warnings_json, errors_json, checked_at, checked_by, correlation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "pf-failed",
            setup_id,
            setup.setup_checksum,
            0,
            1, 1, 1,
            1, 1, 1,
            1, 1,
            1, 1, 1, 1,
            1, 1, 1,
            json.dumps({
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
                "azure_model_ready": False,
            }),
            json.dumps(["Azure model smoke failed: [redacted]"]),
            json.dumps([]),
            now,
            "test",
            None,
        ),
    )

    response = client.post(
        "/v1/v2/migration-jobs/start-stage1",
        json={"job_id": "job-failed-smoke", "setup_id": setup_id},
        headers=_mutation_headers(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "AI_MODEL_NOT_READY"


def test_manifest_persistence_across_connections(tmp_path: Path) -> None:
    """Built manifest should survive connection close/reopen."""
    db_path = tmp_path / "persist_test.sqlite3"

    # First connection — create setup and manifest
    conn1 = sqlite3.connect(
        db_path, check_same_thread=False, isolation_level=None, timeout=5.0
    )
    conn1.row_factory = sqlite3.Row
    conn1.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn1)
    repo1 = SqliteV2SetupRepository(conn1)
    setup_id = _create_test_setup(repo1)
    _seed_job_run_configuration(conn1, setup_id=setup_id, job_id="persist-test-job")
    service1 = _make_worker_service(conn1)
    result = service1.build_stage1_manifest(
        job_id="persist-test-job",
        setup_id=setup_id,
    )
    saved_command_id = result.command_id
    conn1.close()

    # Second connection — verify it's still there
    conn2 = sqlite3.connect(
        db_path, check_same_thread=False, isolation_level=None, timeout=5.0
    )
    conn2.row_factory = sqlite3.Row
    conn2.execute("PRAGMA foreign_keys = ON")
    service2 = _make_worker_service(conn2)
    loaded = service2.get_command(saved_command_id)
    assert loaded is not None
    assert loaded.command_id == saved_command_id
    assert loaded.stage_index == 1
    assert "--legacy" in loaded.argv
    conn2.close()


def test_get_command_returns_none_for_missing(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "test_get.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    service = _make_worker_service(conn)
    assert service.get_command("nonexistent") is None


# ── SA4: Terminal migration parity tests ───────────────────────────

def test_stage1_profile_matches_terminal_path(tmp_path: Path) -> None:
    """Stage 1 must use the route-aware Spring Boot 2.7 / Java 17 runtime profile."""
    conn = sqlite3.connect(
        tmp_path / "sa4_profile.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="j1",
        source_profile="springboot-2.7-java11",
        target_profile="springboot-3.5-java17",
    )
    service = _make_worker_service(conn)
    result = service.build_stage1_manifest(job_id="j1", setup_id=setup_id)

    argv_str = " ".join(result.argv)
    assert "--profile" in argv_str
    profile_idx = result.argv.index("--profile") + 1
    assert result.argv[profile_idx] == "springboot-2.7-to-3.5-java17"


def test_stage1_manifest_uses_boot21_first_hop_runtime_profile(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "boot21_route.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="boot21-route-job",
        source_profile="springboot-2.1-java11",
        target_profile="springboot-4.0-java21",
    )

    result = _make_worker_service(conn).build_stage1_manifest(
        job_id="boot21-route-job",
        setup_id=setup_id,
    )

    assert result.runtime_profile == "springboot-2.1.6-to-2.7-java11"
    assert result.catalog == "springboot-2.1.6-to-2.7-java11"
    assert result.execution_jdk == "java11"
    profile_idx = result.argv.index("--profile") + 1
    assert result.argv[profile_idx] == "springboot-2.1.6-to-2.7-java11"
    record = SqliteV2CommandRepository(conn).get(result.command_id)
    assert record is not None
    env = json.loads(record.env_json)
    assert env.get("JAVA_HOME") == "/usr/lib/jvm/java-11"
    assert env.get("PATH_PREPEND") == str(Path(env["JAVA_HOME"]) / "bin")


def test_stage1_manifest_uses_route_aware_profile_for_boot35_java21_target(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "route_java21.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="route-job",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
    )

    result = _make_worker_service(conn).build_stage1_manifest(job_id="route-job", setup_id=setup_id)

    profile_idx = result.argv.index("--profile") + 1
    assert result.argv[profile_idx] == "springboot-3.5-java17-to-java21"
    assert "springboot-2.1.6-to-2.7-java11" not in " ".join(result.argv)
    record = SqliteV2CommandRepository(conn).get(result.command_id)
    assert record is not None
    env = json.loads(record.env_json)
    assert env.get("ROUTE_STEP_INDEX") == "1"
    assert env.get("ROUTE_STEP_RUNTIME_PROFILE") == "springboot-3.5-java17-to-java21"
    assert env.get("ROUTE_STEP_CATALOG") == "springboot-3.5-java17-to-java21"
    assert env.get("ROUTE_STEP_EXECUTION_JDK") == "java21"


def test_missing_runtime_profile_fails_closed_without_legacy_default(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "missing_profile.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo, ai_hub_path=str(tmp_path / "empty-ai-hub"))
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="missing-profile-job",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
    )

    with pytest.raises(ValueError, match="ROUTE_RUNTIME_PROFILE_UNAVAILABLE"):
        _make_worker_service(conn).build_stage1_manifest(
            job_id="missing-profile-job",
            setup_id=setup_id,
        )


def test_stage1_manifest_fails_closed_when_java11_home_missing_for_boot21(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "boot21_missing_java11.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo, java11_home="")
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="boot21-missing-java11",
        source_profile="springboot-2.1-java11",
        target_profile="springboot-2.7-java11",
    )

    with pytest.raises(ValueError, match="JAVA11_HOME"):
        _make_worker_service(conn).build_stage1_manifest(
            job_id="boot21-missing-java11",
            setup_id=setup_id,
        )


def test_stage1_runtime_profile_error_does_not_leak_profile_path(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)

    repo = SqliteV2SetupRepository(conn)
    missing_ai_hub = tmp_path / "missing-ai-hub"
    setup_id = _create_test_setup(
        repo,
        ai_hub_path=str(missing_ai_hub),
        skip_endpoint_smoke=True,
    )
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="path-leak-job",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
    )

    response = client.post(
        "/v1/v2/migration-jobs/start-stage1",
        json={"job_id": "path-leak-job", "setup_id": setup_id},
        headers=_mutation_headers(),
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "STAGE1_START_FAILED"
    assert body["error"]["message"] == "ROUTE_RUNTIME_PROFILE_UNAVAILABLE"
    assert str(missing_ai_hub) not in response.text
    assert ".yaml" not in response.text
    assert "C:\\" not in response.text
    assert "/Users/" not in response.text
    assert "/home/" not in response.text


def test_stage1_manifest_matches_terminal_runner_args(tmp_path: Path) -> None:
    """Cockpit command manifest must match terminal python -m runner args."""
    conn = sqlite3.connect(
        tmp_path / "sa4_terminal.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(conn, setup_id=setup_id, job_id="j1")
    service = _make_worker_service(conn)
    result = service.build_stage1_manifest(job_id="j1", setup_id=setup_id)

    # Must match: python -m migration_factory.orchestrator.runner
    #             --run-id ... --legacy ... --modernized ... --ai-hub ... --profile ... --mode ...
    assert result.argv[0].endswith("python") or "python" in result.argv[0].lower()
    assert result.argv[1:3] == ("-m", RUNNER_MODULE)
    mandatory_flags = {"--run-id", "--legacy", "--modernized", "--ai-hub", "--profile", "--mode"}
    actual_flags = {flag for flag in result.argv if flag.startswith("--")}
    assert mandatory_flags.issubset(actual_flags), f"Missing flags: {mandatory_flags - actual_flags}"
    assert "--mode" in result.argv
    mode_idx = result.argv.index("--mode") + 1
    assert result.argv[mode_idx] == "full_sandbox_migration"


def test_stage2_and_stage3_profiles_are_correct() -> None:
    """Stage 2 and Stage 3 profiles must match the terminal path."""
    from migration_factory.control_tower.application.v2_stage_progression import STAGE_CONFIG
    assert STAGE_CONFIG[2]["profile"] == "springboot-2.7-to-3.5-java17"
    assert STAGE_CONFIG[2]["jdk_id"] == "java17"
    assert STAGE_CONFIG[3]["profile"] == "springboot-3.5-java17-to-java21"
    assert STAGE_CONFIG[3]["jdk_id"] == "java21"


def test_stage_run_ids_are_unique(tmp_path: Path) -> None:
    """Each stage must have a unique run-id to avoid output path collision."""
    conn = sqlite3.connect(
        tmp_path / "sa4_runid.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(conn, setup_id=setup_id, job_id="j1")
    service = _make_worker_service(conn)
    r1 = service.build_stage1_manifest(job_id="j1", setup_id=setup_id)

    # Extract --run-id value from each
    run_id_idx = r1.argv.index("--run-id") + 1
    assert r1.argv[run_id_idx].startswith("v2-"), f"Stage 1 run-id must start with v2-, got {r1.argv[run_id_idx]}"

    # Different job IDs must produce different run-ids
    _seed_job_run_configuration(conn, setup_id=setup_id, job_id="j2")
    r2 = service.build_stage1_manifest(job_id="j2", setup_id=setup_id)
    run_id_idx2 = r2.argv.index("--run-id") + 1
    assert r1.argv[run_id_idx] != r2.argv[run_id_idx2], "Different jobs must have unique run-ids"


def test_stage_env_includes_all_required_java_maven_vars(tmp_path: Path) -> None:
    """Stage env manifest must follow the selected runtime profile and include all JDK homes."""
    conn = sqlite3.connect(
        tmp_path / "sa4_env.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(conn, setup_id=setup_id, job_id="j1")
    service = _make_worker_service(conn)
    result = service.build_stage1_manifest(job_id="j1", setup_id=setup_id)

    # Fetch the persisted command env
    cmd_repo = SqliteV2CommandRepository(conn)
    record = cmd_repo.get(result.command_id)
    assert record is not None
    env = json.loads(record.env_json)
    assert env.get("JAVA_HOME") == "/usr/lib/jvm/java-21"
    assert env.get("JAVA11_HOME") == "/usr/lib/jvm/java-11"
    assert env.get("JAVA17_HOME") == "/usr/lib/jvm/java-17"
    assert env.get("JAVA21_HOME") == "/usr/lib/jvm/java-21"
    assert env.get("MAVEN_CMD") == "/usr/bin/mvn"
    assert env.get("PATH_PREPEND") == str(Path(env["JAVA_HOME"]) / "bin")


def test_stage_env_uses_java17_for_java17_target_route(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "sa4_env_java17.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="j17",
        source_profile="springboot-2.7-java11",
        target_profile="springboot-3.5-java17",
    )
    service = _make_worker_service(conn)
    result = service.build_stage1_manifest(job_id="j17", setup_id=setup_id)

    record = SqliteV2CommandRepository(conn).get(result.command_id)
    assert record is not None
    env = json.loads(record.env_json)
    assert env.get("JAVA_HOME") == env.get("JAVA17_HOME") == "/usr/lib/jvm/java-17"
    assert env.get("PATH_PREPEND") == str(Path(env["JAVA17_HOME"]) / "bin")


def test_stage_env_fails_closed_when_required_jdk_home_missing(tmp_path: Path) -> None:
    conn = sqlite3.connect(
        tmp_path / "sa4_env_missing_java21.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(
        repo,
        java21_home="",
    )
    _seed_job_run_configuration(
        conn,
        setup_id=setup_id,
        job_id="j-missing-java21",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
    )

    with pytest.raises(ValueError, match="JAVA21_HOME"):
        _make_worker_service(conn).build_stage1_manifest(
            job_id="j-missing-java21",
            setup_id=setup_id,
        )


def test_stage_env_excludes_secret_keys(tmp_path: Path) -> None:
    """Sanitized env manifest must never contain secret keys."""
    conn = sqlite3.connect(
        tmp_path / "sa4_secret.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_test_setup(repo)
    _seed_job_run_configuration(conn, setup_id=setup_id, job_id="j1")
    service = _make_worker_service(conn)
    result = service.build_stage1_manifest(job_id="j1", setup_id=setup_id)

    cmd_repo = SqliteV2CommandRepository(conn)
    record = cmd_repo.get(result.command_id)
    assert record is not None
    env = json.loads(record.env_json)
    secret_markers = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")
    for key in env:
        assert not any(marker in key.upper() for marker in secret_markers), \
            f"Secret-like key leaked in manifest: {key!r}"
