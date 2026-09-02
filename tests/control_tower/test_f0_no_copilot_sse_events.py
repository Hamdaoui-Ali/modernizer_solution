"""F0 closure: prove SSE event type lists do not include copilot event types."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi.app import _PIPELINE_PHASES, _IMPORTANT_EVENT_TYPES
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower._helpers import canonical_json, seed_runner_profile, sha256_json
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
    V2PreflightResultRecord,
)
from migration_factory.control_tower.application.v2_setup_service import CreateSetupRequest, V2SetupService
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import V2ApprovalDecisionRecord


_COPILOT_EVENT_TYPES = {
    "copilot_status_checked",
    "copilot_repair_invalid_response",
    "copilot_assist_started",
    "copilot_assist_completed",
    "copilot_final_report_started",
    "copilot_final_report_completed",
}


def test_pipeline_phases_do_not_include_copilot_types() -> None:
    all_pipeline_types: set[str] = set()
    for _key, _label, event_types in _PIPELINE_PHASES:
        all_pipeline_types.update(event_types)
    overlap = all_pipeline_types & _COPILOT_EVENT_TYPES
    assert not overlap, f"Pipeline phases include copilot event types: {overlap}"


def test_important_event_types_do_not_include_copilot_types() -> None:
    overlap = _IMPORTANT_EVENT_TYPES & _COPILOT_EVENT_TYPES
    assert not overlap, f"IMPORTANT_EVENT_TYPES includes copilot event types: {overlap}"


# --- SSE stream end-to-end: no copilot event types are emitted ---


def _mutation_headers() -> dict[str, str]:
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _seed_v2_pipeline(conn: sqlite3.Connection) -> None:
    payload = {
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
            {"stage_index": 4, "stage_id": "boot4-migration", "profile_id": "springboot-3.5-java21-to-4.0-java21", "command_jdk": "jdk-21", "input_source": {"kind": "previous_stage", "previous_stage_index": 3}, "continuation_policy_id": "default", "target": {"spring_boot": "4.0.0", "java": 21}},
        ),
    }
    conn.execute(
        """INSERT INTO pipeline_definitions (pipeline_id, pipeline_version, display_name, schema_version, graph_version, graph_state_schema_version, payload_json, payload_checksum, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (payload["pipeline_id"], payload["pipeline_version"], payload["display_name"], payload["schema_version"], payload["graph_version"], payload["graph_state_schema_version"], canonical_json(payload), sha256_json(payload), utc_now_text(), "tester"),
    )


class _FakeV2Runner:
    def __init__(self, uow_factory):
        self._uow_factory = uow_factory

    def start(self, *, job_id: str, command_id: str):
        with self._uow_factory() as uow:
            uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_started", status="running", message="fake runner started", payload={"command_id": command_id})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="analysis_started", status="running", message="analysis started", payload={})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="analysis_completed", status="completed", message="analysis completed", payload={})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="planning_completed", status="completed", message="planning completed", payload={})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="assessment_completed", status="completed", message="assessment completed", payload={})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="artifact_written", status="completed", message="fake artifact", payload={"artifact_kind": "analysis_report"})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_completed", status="completed", message="fake complete", payload={"command_id": command_id})
        return None


def _api_client(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection]:
    from migration_factory.control_tower.adapters.fastapi import create_app
    conn = sqlite3.connect(tmp_path / "f0_sse.sqlite3", check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    app = create_app(lambda: SqliteUnitOfWork(conn), v2_orchestrator_runner=_FakeV2Runner(lambda: SqliteUnitOfWork(conn)))
    return TestClient(app, base_url="http://127.0.0.1:8000"), conn


def _ready_setup(conn: sqlite3.Connection) -> str:
    seed_runner_profile(conn)
    _seed_v2_pipeline(conn)
    repo = SqliteV2SetupRepository(conn)
    service = V2SetupService(repo)
    setup = service.create_setup(
        CreateSetupRequest(
            run_name="f0-sse",
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
    import json
    ready_json = json.dumps({
        "legacy_app_exists": True, "legacy_app_has_project_file": True, "legacy_app_not_in_output_parent": True,
        "output_parent_writable": True, "ai_hub_root_exists": True, "ai_hub_profiles_ready": True,
        "ai_hub_catalogs_ready": True, "ai_hub_policies_ready": True, "jdk11_ready": True,
        "jdk17_ready": True, "jdk21_ready": True, "maven_ready": True, "pipeline_route_ready": True,
        "legacy_marker_ready": True, "output_parent_gate_ready": True, "azure_model_ready": True,
    })
    repo.save_preflight(
        V2PreflightResultRecord(
            preflight_id="pf-ready", setup_id=setup.setup_id, setup_checksum=setup.setup_checksum,
            all_ready=True, legacy_app_exists=True, legacy_app_has_project_file=True,
            legacy_app_not_in_output_parent=True, output_parent_writable=True,
            ai_hub_root_exists=True, ai_hub_profiles_ready=True, ai_hub_catalogs_ready=True,
            ai_hub_policies_ready=True, jdk11_ready=True, jdk17_ready=True, jdk21_ready=True,
            maven_ready=True, pipeline_route_ready=True, legacy_marker_ready=True,
            output_parent_gate_ready=True, readiness_json=ready_json,
            warnings_json="[]", errors_json="[]", checked_at=now, checked_by="test", correlation_id=None,
        )
    )
    return setup.setup_id


def _create_started_job(client: TestClient, setup_id: str) -> str:
    job_response = client.post("/v1/v2/migration-jobs", json={"setup_id": setup_id}, headers=_mutation_headers())
    assert job_response.status_code == 201, job_response.text
    job_id = job_response.json()["job_id"]
    start_response = client.post(
        "/v1/v2/migration-jobs/start-stage1",
        json={"job_id": job_id, "setup_id": setup_id},
        headers=_mutation_headers(),
    )
    assert start_response.status_code == 200, start_response.text
    return job_id


def test_sse_stream_emits_no_copilot_event_types(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    snapshot = client.get(f"/v1/v2/migration-jobs/{job_id}/events/snapshot")
    assert snapshot.status_code == 200
    events = snapshot.json()["events"]
    event_types = {event["type"] for event in events}

    forbidden = event_types & _COPILOT_EVENT_TYPES
    assert not forbidden, f"SSE event stream includes copilot event types: {forbidden}"


def test_sse_event_stream_has_no_copilot_events_in_sse_body(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    with client.stream("GET", f"/v1/v2/migration-jobs/{job_id}/events?after=0&once=true") as response:
        assert response.status_code == 200
        body = ""
        for chunk in response.iter_text():
            body += chunk
            if "event: stage_completed" in body:
                break

    for copilot_type in _COPILOT_EVENT_TYPES:
        assert f"event: {copilot_type}" not in body, f"SSE stream contains copilot event: {copilot_type}"
