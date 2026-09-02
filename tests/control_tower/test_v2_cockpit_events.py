"""V2 cockpit read, event, and OpenAPI regressions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from migration_factory.control_tower.application.v2_setup_service import CreateSetupRequest, V2SetupService
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
    V2PreflightResultRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import V2ApprovalDecisionRecord
from tests.control_tower._helpers import canonical_json, seed_runner_profile, sha256_json


def _mutation_headers() -> dict[str, str]:
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID

    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _api_client(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection]:
    from migration_factory.control_tower.adapters.fastapi import create_app

    conn = sqlite3.connect(
        tmp_path / "v2_cockpit.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    app = create_app(lambda: SqliteUnitOfWork(conn), v2_orchestrator_runner=_FakeV2Runner(lambda: SqliteUnitOfWork(conn)))
    return TestClient(app, base_url="http://127.0.0.1:8000"), conn


class _FakeV2Runner:
    def __init__(self, uow_factory):
        self._uow_factory = uow_factory

    def start(self, *, job_id: str, command_id: str):
        with self._uow_factory() as uow:
            uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_started", status="running", message="fake runner started", payload={"command_id": command_id})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="command_started", status="running", message="fake command started", payload={"command_id": command_id})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="stdout", status="running", message="raw log spam", payload={"command_id": command_id})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="analysis_started", status="running", message="analysis started", payload={})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="analysis_completed", status="completed", message="analysis completed", payload={})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="planning_completed", status="completed", message="planning completed", payload={})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="assessment_completed", status="completed", message="assessment completed", payload={})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="artifact_written", status="completed", message="fake artifact", payload={"artifact_kind": "analysis_report"})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="proof_updated", status="completed", message="fake proof", payload={})
            uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_completed", status="completed", message="fake complete", payload={"command_id": command_id})
        return None


def _seed_v2_pipeline(conn: sqlite3.Connection) -> None:
    payload = {
        "schema_version": "1.0.0",
        "pipeline_id": "springboot-216-to-400-java21-four-stage",
        "pipeline_version": "2026.06",
        "display_name": "V2 migration pipeline (4-stage with Boot 4)",
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


def _ready_setup(conn: sqlite3.Connection) -> str:
    seed_runner_profile(conn)
    _seed_v2_pipeline(conn)
    repo = SqliteV2SetupRepository(conn)
    service = V2SetupService(repo)
    setup = service.create_setup(
        CreateSetupRequest(
            run_name="cockpit-uat",
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
    ready_json = json.dumps({
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
    })
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
    return setup.setup_id


def _create_started_job(client: TestClient, setup_id: str) -> str:
    job_response = client.post(
        "/v1/v2/migration-jobs",
        json={"setup_id": setup_id},
        headers=_mutation_headers(),
    )
    assert job_response.status_code == 201, job_response.text
    job_id = job_response.json()["job_id"]
    start_response = client.post(
        "/v1/v2/migration-jobs/start-stage1",
        json={"job_id": job_id, "setup_id": setup_id},
        headers=_mutation_headers(),
    )
    assert start_response.status_code == 200, start_response.text
    assert start_response.json()["job_id"] == job_id
    return job_id


def test_v2_job_read_stages_and_empty_approvals(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    job_response = client.get(f"/v1/v2/migration-jobs/{job_id}")
    assert job_response.status_code == 200
    assert job_response.json()["job_id"] == job_id

    stages_response = client.get(f"/v1/v2/migration-jobs/{job_id}/stages")
    assert stages_response.status_code == 200
    stages = stages_response.json()["stages"]
    assert [stage["stage_index"] for stage in stages] == [1, 2, 3, 4]
    assert stages[0]["chain_status"] == "completed"
    assert stages[1]["chain_status"] == "pending"
    assert stages[2]["input_source_kind"] == "stage_2_sandbox"
    assert stages[3]["chain_status"] == "pending"

    approvals_response = client.get(f"/v1/v2/jobs/{job_id}/approvals")
    assert approvals_response.status_code == 200
    assert approvals_response.json()["approvals"] == []


def test_valid_job_with_pending_approval_returns_card(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)
    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_approvals.save_card(
            V2ApprovalDecisionRecord(
                card_id="card-visible",
                job_id=job_id,
                interrupt_id="run-visible",
                request_checksum="checksum-visible",
                stage_index=1,
                summary="Human approval required before sandbox transform.",
                status="pending",
                created_at=now,
            )
        )

    approvals_response = client.get(f"/v1/v2/jobs/{job_id}/approvals")
    assert approvals_response.status_code == 200
    approvals = approvals_response.json()["approvals"]
    assert len(approvals) == 1
    assert approvals[0]["card_id"] == "card-visible"
    assert approvals[0]["request_checksum"] == "checksum-visible"


def test_v2_pipeline_projection_groups_phases_and_raw_logs(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    response = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline")
    assert response.status_code == 200
    body = response.json()
    labels = [row["label"] for row in body["rows"]]
    assert "Analysis Agent" in labels
    assert "Planning Agent" in labels
    assert "Assessment Agent" in labels
    assert any(event["type"] == "stdout" for event in body["raw_logs"])
    assert all(event["type"] != "stdout" for event in body["evidence"])


def test_v2_nonexistent_reads_return_404(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    assert client.get("/v1/v2/migration-jobs/missing").status_code == 404
    assert client.get("/v1/v2/migration-jobs/missing/stages").status_code == 404
    assert client.get("/v1/v2/jobs/missing/approvals").status_code == 404
    assert client.get("/v1/v2/migration-jobs/missing/events/snapshot").status_code == 404
    assert client.get("/v1/v2/migration-jobs/missing/events").status_code == 404


def test_v2_start_stage1_emits_ordered_events_and_resume_cursor(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    snapshot = client.get(f"/v1/v2/migration-jobs/{job_id}/events/snapshot")
    assert snapshot.status_code == 200
    events = snapshot.json()["events"]
    event_types = [event["type"] for event in events]
    assert event_types[:3] == ["job_created", "stage_queued", "stage_started"]
    assert "command_started" in event_types
    assert "artifact_written" in event_types
    assert "proof_updated" in event_types
    assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)

    after = events[1]["sequence"]
    resumed = client.get(f"/v1/v2/migration-jobs/{job_id}/events/snapshot?after={after}")
    assert [event["sequence"] for event in resumed.json()["events"]] == [
        event["sequence"] for event in events if event["sequence"] > after
    ]


def test_v2_sse_stream_replays_events(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    with client.stream("GET", f"/v1/v2/migration-jobs/{job_id}/events?after=0&once=true") as response:
        assert response.status_code == 200
        body = ""
        for chunk in response.iter_text():
            body += chunk
            if "event: stage_started" in body:
                break
    assert "event: job_created" in body
    assert "event: stage_queued" in body
    assert "event: stage_started" in body


def test_v2_events_are_redacted_and_bounded(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="stdout",
            status="running",
            message="x" * 5000,
            payload={"path": "C:/secret/path", "api_key": "sk-secret"},
        )

    snapshot = client.get(f"/v1/v2/migration-jobs/{job_id}/events/snapshot")
    last = snapshot.json()["events"][-1]
    assert len(last["message"]) <= 4110
    serialized = json.dumps(last)
    assert "sk-secret" not in serialized
    assert "C:/secret/path" not in serialized


def test_openapi_json_includes_v2_paths(tmp_path: Path) -> None:
    client, _ = _api_client(tmp_path)
    response = client.get("/openapi.json")
    assert response.status_code == 200, response.text
    paths = response.json()["paths"]
    assert "/v1/v2/migration-jobs" in paths
    assert "/v1/v2/migration-jobs/{job_id}/stages" in paths
    assert "/v1/v2/jobs/{job_id}/approvals" in paths
    assert "/v1/v2/migration-jobs/{job_id}/events" in paths
    assert "/v1/v2/migration-jobs/{job_id}/pipeline" in paths
    assert "/v1/v2/migration-jobs/{job_id}/failure-summary" in paths


def test_v2_alias_routes_reuse_existing_handlers(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    aliases = [
        f"/v1/v2/jobs/{job_id}/pipeline",
        f"/v1/v2/jobs/{job_id}/stages",
        f"/v1/v2/jobs/{job_id}/failure-summary",
        f"/v1/v2/jobs/{job_id}/events/snapshot",
    ]
    for path in aliases:
        response = client.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
        assert response.json()["job_id"] == job_id


def test_v2_failure_summary_endpoint_when_no_failures(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    response = client.get(f"/v1/v2/migration-jobs/{job_id}/failure-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["has_failures"] is False
    assert body["repair_loop_active"] is False


def test_test_validation_skipped_when_active_stage_build_failed_before_tests(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="stage_started",
            status="running",
            message="Stage 2 started",
            payload={},
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="build_failed",
            status="failed",
            message="Sandbox build failed",
            payload={"build_status": "BUILD_FAILED_IN_SANDBOX"},
        )

    response = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline")
    assert response.status_code == 200, response.text
    row = [item for item in response.json()["rows"] if item["key"] == "test_validation"][0]
    assert row["status"] == "skipped"
    assert row["latest_message"] == "Not run because sandbox build failed."


def test_v2_failure_summary_endpoint_with_failures(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="build_failed",
            status="failed",
            message="Build result kind: dependency_error",
            payload={
                "build_status": "BUILD_FAILED_IN_SANDBOX",
                "final_status": "FALLBACK_REPAIR_PLAN",
                "final_proof_level": "not_verified",
                "repair_loop_status": "FALLBACK_REPAIR_PLAN",
                "repair_fallback_generated": True,
            },
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="repair_started",
            status="running",
            message="Repair loop active",
            payload={"repair_loop_status": "FALLBACK_REPAIR_PLAN"},
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="repair_completed",
            status="completed",
            message="Deterministic repair fallback applied",
            payload={"repair_fallback_generated": True},
        )

    response = client.get(f"/v1/v2/migration-jobs/{job_id}/failure-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["has_failures"] is True
    assert body["repair_loop_active"] is True
    assert len(body["failures"]) >= 1
    assert any(f["build_status"] == "BUILD_FAILED_IN_SANDBOX" for f in body["failures"])
    assert any(f["repair_loop_status"] == "FALLBACK_REPAIR_PLAN" for f in body["failures"])
    assert any(f["final_proof_level"] == "not_verified" for f in body["failures"])


def test_v2_failure_summary_projects_ai_supervision_records(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="build_failed",
            status="failed",
            message="Build failed in sandbox",
            payload={"build_status": "BUILD_FAILED_IN_SANDBOX", "result_kind": "dependency_error"},
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="ai_diagnosis_created",
            status="completed",
            message="AI diagnosis created",
            payload={
                "diagnosis_id": "diag-1",
                "context_pack_id": "pack-1",
                "context_pack_checksum": "ctx-abc",
                "command_id": "cmd-1",
                "event_type": "build_failed",
                "failure_type": "DEPENDENCY_ERROR",
                "repair_proposal_id": "proposal-1",
                "model_invocation_id": "model-1",
                "redaction_status": "redacted",
            },
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="pom_summary_created",
            status="completed",
            message="POM summary created",
            payload={
                "pom_summary_ref": "pom-summary:1",
                "spring_boot_version": "2.7.18",
                "java_version": "11",
                "packaging": "jar",
                "candidate_rules": ["pom_dependency_alignment"],
            },
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="repair_proposal_revised",
            status="completed",
            message="Proposal revised",
            payload={
                "revised_proposal_id": "proposal-2",
                "source_proposal_id": "proposal-1",
                "revision_number": 2,
                "allowed_scope": "pom_only",
                "command_id": "cmd-1",
            },
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="reviewer_critique_created",
            status="completed",
            message="Reviewer critique created",
            payload={
                "critique_id": "crit-1",
                "proposal_id": "proposal-2",
                "proposal_type": "repair_proposal",
                "proposal_checksum": "prop-checksum",
                "context_pack_checksum": "ctx-abc",
                "decision": "accept",
                "reasoning": "Evidence and scope are acceptable.",
                "missing_evidence": [],
                "unsafe_assumptions": [],
            },
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="repair_patch_gate_completed",
            status="completed",
            message="Patch gate completed",
            payload={
                "proposal_id": "proposal-2",
                "binding_checksum": "bind-1",
                "patch_gate_status": "ALLOWED",
                "deterministic_rule_id": "pom_dependency_alignment",
                "touched_paths": ["pom.xml"],
                "ledger_ref": "repair_ledger.json",
            },
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="repair_validation_completed",
            status="completed",
            message="Validation completed",
            payload={
                "proposal_id": "proposal-2",
                "passed": True,
                "build_status": "BUILD_PASSED_IN_SANDBOX",
                "test_status": "TESTS_PASSED",
                "h2_status": "NOT_REQUIRED",
                "artifact_refs": {"repair_ledger": "repair_ledger.json"},
                "ledger_ref": "repair_ledger.json",
            },
        )

    response = client.get(f"/v1/v2/migration-jobs/{job_id}/failure-summary")
    assert response.status_code == 200, response.text
    body = response.json()
    failures = [failure for failure in body["failures"] if failure["stage"] == 2]
    assert len(failures) == 1
    trace = failures[0]["supervision_trace"]
    assert trace["ai_diagnosis"]["diagnosis_id"] == "diag-1"
    assert trace["ai_diagnosis"]["context_pack_checksum"] == "ctx-abc"
    assert trace["pom_analysis"]["pom_summary_ref"] == "pom-summary:1"
    assert trace["repair_proposal"]["proposal_id"] == "proposal-2"
    assert trace["repair_proposal"]["allowed_scope"] == "pom_only"
    assert trace["reviewer_verdict"]["decision"] == "accept"
    assert trace["validation_result"]["patch_gate_status"] == "ALLOWED"
    assert trace["validation_result"]["build_status"] == "BUILD_PASSED_IN_SANDBOX"
    assert trace["validation_result"]["ledger_ref"] == "repair_ledger.json"
    assert trace["evidence_used"] == ["pack-1", "ctx-abc", "pom-summary:1"]


def test_failure_summary_groups_real_stage2_dependency_build_failure_sequence(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    sequence = [
        ("sandbox_transform_failed", "failed", "Dependency error during sandbox transform", {"result_kind": "dependency_error"}),
        ("sandbox_transform_failed", "failed", "Sandbox build failed", {"transform_status": "BUILD_FAILED_IN_SANDBOX"}),
        ("build_failed", "failed", "Build failed in sandbox", {"build_status": "BUILD_FAILED_IN_SANDBOX", "result_kind": "dependency_error"}),
        ("repair_started", "running", "Repair started", {"repair_loop_status": "FALLBACK_REPAIR_PLAN"}),
        ("repair_fallback_generated", "completed", "Deterministic fallback repair plan generated", {"repair_fallback_generated": True}),
        ("repair_proposal_revised", "completed", "Repair proposal revised via deterministic fallback", {"repair_fallback_generated": True}),
        ("transform_failed", "failed", "Transform failed", {"final_status": "FALLBACK_REPAIR_PLAN"}),
        ("stage_failed", "failed", "Stage failed", {"final_status": "FALLBACK_REPAIR_PLAN"}),
    ]
    with SqliteUnitOfWork(conn) as uow:
        for event_type, event_status, message, payload in sequence:
            uow.v2_events.save(
                job_id=job_id,
                stage=2,
                event_type=event_type,
                status=event_status,
                message=message,
                payload=payload,
            )

    response = client.get(f"/v1/v2/migration-jobs/{job_id}/failure-summary")
    assert response.status_code == 200, response.text
    body = response.json()
    failures = [failure for failure in body["failures"] if failure["stage"] == 2]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["title"] == "Stage 2 Dependency/Build Failure"
    assert failure["type"] == "build_failed"
    assert failure["result_kind"] == "dependency_error"
    assert failure["build_status"] == "BUILD_FAILED_IN_SANDBOX"
    assert failure["final_status"] == "FALLBACK_REPAIR_PLAN"
    assert failure["repair_loop_status"] == "FALLBACK_REPAIR_PLAN"
    assert failure["event_types"] == [
        "build_failed",
        "sandbox_transform_failed",
        "transform_failed",
        "stage_failed",
    ]
    assert [event["type"] for event in failure["repair_events"]] == [
        "repair_started",
        "repair_fallback_generated",
        "repair_proposal_revised",
    ]
    assert body["repair_events"] == []


def test_v2_approval_lifecycle_pipeline_transitions(tmp_path: Path) -> None:
    """Human Approval must show pass/approved after approval events, not blocked."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="approval_required",
            status="blocked",
            message="Human approval required",
            payload={"card_id": "card-1"},
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="stage_blocked_for_approval",
            status="blocked",
            message="Stage blocked",
            payload={"card_id": "card-1"},
        )

    # At this point, approval should be blocked
    pipeline1 = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline").json()
    approval_row1 = [r for r in pipeline1["rows"] if r["key"] == "human_approval"][0]
    assert approval_row1["status"] == "blocked"

    # After approval_resume_queued, approval should be pass
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="approval_resume_queued",
            status="queued",
            message="Approval accepted",
            payload={"card_id": "card-1"},
        )

    pipeline2 = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline").json()
    approval_row2 = [r for r in pipeline2["rows"] if r["key"] == "human_approval"][0]
    assert approval_row2["status"] == "pass", f"Expected pass but got {approval_row2['status']}"

    # After transform starts, approval must still be pass (not reverted)
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="sandbox_transform_started",
            status="running",
            message="Transform started",
            payload={},
        )

    pipeline3 = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline").json()
    approval_row3 = [r for r in pipeline3["rows"] if r["key"] == "human_approval"][0]
    assert approval_row3["status"] == "pass", f"Expected pass after transform but got {approval_row3['status']}"
    transform_row = [r for r in pipeline3["rows"] if r["key"] == "sandbox_transform"][0]
    assert transform_row["status"] == "running"


def test_v2_approval_lifecycle_with_failure_after_approval(tmp_path: Path) -> None:
    """Transform failure must not revert Human Approval back to blocked."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_started_job(client, setup_id)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_required", status="blocked", message="blocked", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_resume_queued", status="queued", message="resume queued", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="sandbox_transform_started", status="running", message="transform started", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="sandbox_transform_failed", status="failed", message="transform failed", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="build_failed", status="failed", message="Build result: dependency_error", payload={"build_status": "BUILD_FAILED_IN_SANDBOX"})

    pipeline = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline").json()
    approval_row = [r for r in pipeline["rows"] if r["key"] == "human_approval"][0]
    assert approval_row["status"] == "pass", f"Expected pass but got {approval_row['status']}"
    transform_row = [r for r in pipeline["rows"] if r["key"] == "sandbox_transform"][0]
    assert transform_row["status"] == "failed"


# ── Stage status lifecycle regression tests (V2 cockpit state model) ──


def _create_job_only(client: TestClient, setup_id: str, conn: sqlite3.Connection) -> str:
    """Create a V2 migration job WITHOUT triggering the fake runner start."""
    job_response = client.post(
        "/v1/v2/migration-jobs",
        json={"setup_id": setup_id},
        headers=_mutation_headers(),
    )
    assert job_response.status_code == 201, job_response.text
    return job_response.json()["job_id"]


def _stages_status(client: TestClient, job_id: str) -> dict[int, str]:
    """Return {stage_index: chain_status} from the stages endpoint."""
    response = client.get(f"/v1/v2/migration-jobs/{job_id}/stages")
    assert response.status_code == 200, response.text
    return {s["stage_index"]: s["chain_status"] for s in response.json()["stages"]}


def _pipeline_row(client: TestClient, job_id: str, key: str) -> dict[str, str]:
    """Return a specific pipeline row (status, label, etc.)."""
    response = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline")
    assert response.status_code == 200, response.text
    matches = [r for r in response.json()["rows"] if r["key"] == key]
    assert len(matches) == 1, f"Expected 1 pipeline row key={key}, got {len(matches)}"
    return matches[0]


def test_final_route_stage_completed_by_migration_completed_event(tmp_path: Path) -> None:
    """A selected route ending at Stage 3 must not stay running after migration_completed."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=3, event_type="stage_started", status="running", message="stage 3 started", payload={})
        uow.v2_events.save(job_id=job_id, stage=3, event_type="sandbox_transform_started", status="running", message="sandbox started", payload={})
        uow.v2_events.save(job_id=job_id, stage=3, event_type="sandbox_transform_completed", status="completed", message="sandbox passed", payload={})
        uow.v2_events.save(
            job_id=job_id,
            stage=3,
            event_type="migration_completed",
            status="completed",
            message="selected target profile reached",
            payload={"from_stage": 3, "to_stage": 3, "reason": "migration_completed"},
        )

    stages = _stages_status(client, job_id)
    assert stages[3] == "completed"

    transform_row = _pipeline_row(client, job_id, "sandbox_transform")
    assert transform_row["status"] == "pass"

def test_stage_blocked_while_approval_pending(tmp_path: Path) -> None:
    """Stage 1 must be BLOCKED when only approval events exist (no resume/transform)."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_required", status="blocked", message="blocked", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_blocked_for_approval", status="blocked", message="stage blocked", payload={"card_id": "c1"})

    stages = _stages_status(client, job_id)
    assert stages[1] == "blocked", f"Expected blocked, got {stages[1]}"
    approval_row = _pipeline_row(client, job_id, "human_approval")
    assert approval_row["status"] == "blocked", f"Expected blocked, got {approval_row['status']}"


def test_stage_running_after_approval_completed(tmp_path: Path) -> None:
    """Stage 1 must be RUNNING after approval_completed (not stuck on blocked)."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_required", status="blocked", message="blocked", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_completed", status="completed", message="approved", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="resume_started", status="running", message="resumed", payload={})

    stages = _stages_status(client, job_id)
    assert stages[1] == "running", f"Expected running, got {stages[1]}"
    approval_row = _pipeline_row(client, job_id, "human_approval")
    assert approval_row["status"] == "pass", f"Expected pass, got {approval_row['status']}"


def test_stage_running_after_sandbox_transform_started(tmp_path: Path) -> None:
    """Stage 1 must be RUNNING after sandbox transform starts."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_required", status="blocked", message="blocked", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_resume_queued", status="queued", message="accepted", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="sandbox_transform_started", status="running", message="transform started", payload={})

    stages = _stages_status(client, job_id)
    assert stages[1] == "running", f"Expected running, got {stages[1]}"
    approval_row = _pipeline_row(client, job_id, "human_approval")
    assert approval_row["status"] == "pass", f"Expected pass, got {approval_row['status']}"
    transform_row = _pipeline_row(client, job_id, "sandbox_transform")
    assert transform_row["status"] == "running", f"Expected running, got {transform_row['status']}"


def test_stage_failed_after_sandbox_transform_failed(tmp_path: Path) -> None:
    """Stage 1 must be FAILED after sandbox transform fails."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_required", status="blocked", message="blocked", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_resume_queued", status="queued", message="accepted", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="sandbox_transform_started", status="running", message="transform started", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="sandbox_transform_failed", status="failed", message="transform failed", payload={})

    stages = _stages_status(client, job_id)
    assert stages[1] == "failed", f"Expected failed, got {stages[1]}"
    approval_row = _pipeline_row(client, job_id, "human_approval")
    assert approval_row["status"] == "pass", f"Expected pass, got {approval_row['status']}"
    transform_row = _pipeline_row(client, job_id, "sandbox_transform")
    assert transform_row["status"] == "failed", f"Expected failed, got {transform_row['status']}"


def test_stage_completed_after_stage_completed(tmp_path: Path) -> None:
    """Stage 1 must be COMPLETED after a stage_completed event."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_started", status="running", message="started", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_completed", status="completed", message="completed", payload={})

    stages = _stages_status(client, job_id)
    assert stages[1] == "completed", f"Expected completed, got {stages[1]}"


def test_terminal_migration_event_completes_active_stage(tmp_path: Path) -> None:
    """A terminal migration event must complete the active execution stage."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="stage_started",
            status="running",
            message="analysis started",
            payload={},
        )
        uow.v2_events.save(
            job_id=job_id,
            stage=1,
            event_type="migration_completed",
            status="completed",
            message="selected target reached",
            payload={"reason": "migration_completed"},
        )

    stages = _stages_status(client, job_id)
    assert stages[1] == "completed", f"Expected completed, got {stages[1]}"


def test_old_blocked_event_does_not_override_later_transform_started(tmp_path: Path) -> None:
    """An early blocked event must NOT prevent Stage 1 from becoming RUNNING."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        # approval_blocked first (precedence "blocked")
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_required", status="blocked", message="blocked", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_blocked_for_approval", status="blocked", message="stage blocked", payload={"card_id": "c1"})
        # Then approval and transform — these must NOT be suppressed by old blocked
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_resume_queued", status="queued", message="accepted", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="sandbox_transform_started", status="running", message="transform started", payload={})

    stages = _stages_status(client, job_id)
    assert stages[1] == "running", f"Expected running, got {stages[1]}"


def test_old_blocked_event_does_not_override_later_stage_failed(tmp_path: Path) -> None:
    """An early blocked event must NOT prevent Stage 1 from becoming FAILED."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_required", status="blocked", message="blocked", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_blocked_for_approval", status="blocked", message="stage blocked", payload={"card_id": "c1"})
        # Later — stage fails (must win over old blocked)
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_failed", status="failed", message="failed", payload={})

    stages = _stages_status(client, job_id)
    assert stages[1] == "failed", f"Expected failed, got {stages[1]}"


def test_pipeline_and_stage_status_are_consistent_after_approval(tmp_path: Path) -> None:
    """After full approval→resume→transform→complete cycle, Stage 1 must be COMPLETED
    and Human Approval must still be pass (not blocked)."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_required", status="blocked", message="blocked", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="approval_resume_queued", status="queued", message="accepted", payload={"card_id": "c1"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="sandbox_transform_started", status="running", message="transform started", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_completed", status="completed", message="done", payload={})

    stages = _stages_status(client, job_id)
    assert stages[1] == "completed", f"Expected completed, got {stages[1]}"
    approval_row = _pipeline_row(client, job_id, "human_approval")
    assert approval_row["status"] == "pass", f"Expected pass, got {approval_row['status']}"
    transform_row = _pipeline_row(client, job_id, "sandbox_transform")
    assert transform_row["status"] == "running", f"Expected running, got {transform_row['status']}"


def test_pipeline_projection_does_not_mark_valid_pass_stage_failed(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_started", status="running", message="stage 1 started", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="sandbox_transform_started", status="running", message="transform started", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="build_completed", status="completed", message="build completed", payload={"build_status": "BUILD_PASSED_IN_SANDBOX"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="test_completed", status="completed", message="tests accepted", payload={"test_status": "PASS_WITH_WARNINGS"})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_completed", status="completed", message="stage 1 completed", payload={})
        uow.v2_events.save(job_id=job_id, stage=2, event_type="stage_started", status="running", message="stage 2 started", payload={})

    stages = _stages_status(client, job_id)
    assert stages[1] == "completed"
    assert stages[2] == "running"
    assert stages[3] in {"pending", "queued"}


def test_pipeline_projection_does_not_show_stage1_and_stage2_both_running_after_completion(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_started", status="running", message="stage 1 started", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_completed", status="completed", message="stage 1 completed", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="next_stage_queued", status="queued", message="stage 2 queued", payload={"from_stage": 1, "to_stage": 2, "sandbox_path": "stage1 sandbox"})
        uow.v2_events.save(job_id=job_id, stage=2, event_type="stage_started", status="running", message="stage 2 started", payload={})
        uow.v2_events.save(job_id=job_id, stage=2, event_type="command_started", status="running", message="stage 2 command started", payload={})

    stages = _stages_status(client, job_id)
    assert stages[1] == "completed"
    assert stages[2] == "running"
    assert not (stages[1] == "running" and stages[2] == "running")


def test_final_report_only_passes_after_stage3_completion(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job_only(client, setup_id, conn)

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=1, event_type="stage_completed", status="completed", message="stage 1 completed", payload={})
        uow.v2_events.save(job_id=job_id, stage=1, event_type="next_stage_queued", status="queued", message="stage 2 queued", payload={"from_stage": 1, "to_stage": 2, "sandbox_path": "stage1 sandbox"})
        uow.v2_events.save(job_id=job_id, stage=2, event_type="stage_completed", status="completed", message="stage 2 completed", payload={})
        uow.v2_events.save(job_id=job_id, stage=2, event_type="next_stage_queued", status="queued", message="stage 3 queued", payload={"from_stage": 2, "to_stage": 3, "sandbox_path": "stage2 sandbox"})
        uow.v2_events.save(job_id=job_id, stage=3, event_type="stage_started", status="running", message="stage 3 started", payload={})

    pipeline = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline").json()
    final_report = [row for row in pipeline["rows"] if row["key"] == "final_report"][0]
    assert final_report["status"] == "pending"

    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(job_id=job_id, stage=3, event_type="final_report_started", status="running", message="final report started", payload={})
        uow.v2_events.save(job_id=job_id, stage=3, event_type="final_report_completed", status="completed", message="final report completed", payload={})

    pipeline_after = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline").json()
    final_report_after = [row for row in pipeline_after["rows"] if row["key"] == "final_report"][0]
    assert final_report_after["status"] == "pass"
