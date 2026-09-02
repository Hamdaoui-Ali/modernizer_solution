"""Runtime wiring regression for repair gate diagnosis callbacks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi import create_app
from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteUnitOfWork,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
    V2PreflightResultRecord,
)
from ._helpers import canonical_json, seed_runner_profile, sha256_json
from .v1_fixtures import make_v1_pipeline_definition, make_v2_pipeline_definition


def _mutation_headers() -> dict[str, str]:
    from migration_factory.control_tower.adapters.fastapi.security import (
        DEFAULT_FRONTEND_CLIENT_ID,
    )

    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _app_and_client(tmp_path: Path) -> tuple[object, TestClient, sqlite3.Connection]:
    conn = sqlite3.connect(
        str(tmp_path / "repair_runtime.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    app = create_app(lambda: SqliteUnitOfWork(conn))
    return app, TestClient(app, base_url="http://127.0.0.1:8000"), conn


def _ready_setup(conn: sqlite3.Connection) -> str:
    repo = SqliteV2SetupRepository(conn)
    service = V2SetupService(repo)
    setup = service.create_setup(
        CreateSetupRequest(
            run_name="repair-runtime",
            legacy_app_path="C:/work/legacy",
            output_parent_path="C:/work/out",
            ai_hub_path="C:/work/ai-hub",
            java11_home="C:/java/11",
            java17_home="C:/java/17",
            java21_home="C:/java/21",
            maven_cmd="C:/maven/bin/mvn.cmd",
        )
    )
    now = utc_now_text()
    ready_json = json.dumps(
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
        }
    )
    repo.save_preflight(
        V2PreflightResultRecord(
            preflight_id="pf-ready",
            setup_id=setup.setup_id,
            setup_checksum=setup.setup_checksum,
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
            readiness_json=ready_json,
            warnings_json="[]",
            errors_json="[]",
            checked_at=now,
            checked_by="test",
            correlation_id=None,
        )
    )
    seed_runner_profile(conn)
    for pipeline_payload in (make_v1_pipeline_definition(), make_v2_pipeline_definition()):
        conn.execute(
            """
            INSERT INTO pipeline_definitions (
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
                canonical_json(pipeline_payload),
                sha256_json(pipeline_payload),
                now,
                "test",
            ),
        )
    return setup.setup_id


def _create_job(client: TestClient, setup_id: str, policy: dict | None = None) -> str:
    payload: dict = {"setup_id": setup_id}
    if policy is not None:
        payload["policy"] = policy
    response = client.post(
        "/v1/v2/migration-jobs",
        json=payload,
        headers=_mutation_headers(),
    )
    assert response.status_code == 201, response.text
    return response.json()["job_id"]


def test_fastapi_create_app_repair_gate_callback_creates_repair_review_gate(tmp_path: Path) -> None:
    app, client, conn = _app_and_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)

    callback = app.state.v2_orchestrator_runner._diagnosis_callback
    callback(
        job_id,
        1,
        "cmd-build-1",
        "build_failed",
        {
            "build_status": "FAILED",
            "message": "build exploded",
            "stderr": "boom",
            "artifact_refs": {"analysis": "analysis:1"},
        },
    )

    with SqliteUnitOfWork(conn) as uow:
        open_gates = uow.phase_gates.list_open(job_id)
        assert open_gates
        assert any(gate.gate_phase == "repair_review" for gate in open_gates)


def test_fastapi_create_app_skips_repair_gate_when_disabled(tmp_path: Path) -> None:
    app, client, conn = _app_and_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id, policy={
        "stage_continuation_policy": "auto_on_green",
        "enable_build_repair": False,
    })

    callback = app.state.v2_orchestrator_runner._diagnosis_callback
    callback(
        job_id,
        1,
        "cmd-build-1",
        "build_failed",
        {
            "build_status": "FAILED",
            "message": "build exploded",
            "stderr": "boom",
            "artifact_refs": {"analysis": "analysis:1"},
        },
    )

    with SqliteUnitOfWork(conn) as uow:
        open_gates = uow.phase_gates.list_open(job_id)
        assert not any(gate.gate_phase == "repair_review" for gate in open_gates)
