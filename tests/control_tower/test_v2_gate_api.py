"""Focused regression tests for the F15 gate API."""

from __future__ import annotations

import io
import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi import create_app
from migration_factory.control_tower.application.v2_phase_gate_service import (
    CreateGateRequest,
    V2PhaseGateService,
)
from migration_factory.control_tower.application.v2_orchestrator_runner import (
    V2OrchestratorStart,
)
from migration_factory.control_tower.application.v2_stage_progression import (
    compute_profile_route,
    route_to_dict,
)
from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.entities import ArtifactRevisionRecord, PhaseGateRecord, RunConfigurationRecord
from migration_factory.control_tower.domain.gate_checksum import gate_checksum
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteUnitOfWork,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import (
    SqliteV2ApprovalRepository,
    V2ApprovalDecisionRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
    V2PreflightResultRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from tests.control_tower.transition_helpers import seed_job


def _mutation_headers() -> dict[str, str]:
    from migration_factory.control_tower.adapters.fastapi.security import (
        DEFAULT_FRONTEND_CLIENT_ID,
    )

    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _api_client(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection]:
    conn = sqlite3.connect(
        str(tmp_path / "gate_api.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    _seed_fk_refs(conn)
    app = create_app(lambda: SqliteUnitOfWork(conn))
    return TestClient(app, base_url="http://127.0.0.1:8000"), conn


class _FakePopen:
    def __init__(self, *, stdout: list[str], stderr: list[str], exit_code: int = 0) -> None:
        self.stdout = io.StringIO("".join(stdout))
        self.stderr = io.StringIO("".join(stderr))
        self._exit_code = exit_code
        self.pid = 4321

    def wait(self) -> int:
        return self._exit_code


class _FakePopenFactory:
    def __init__(self, *, stdout: list[str], stderr: list[str], exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.calls: list[dict[str, object]] = []

    def __call__(self, *args, **kwargs) -> _FakePopen:
        self.calls.append({"args": args, "kwargs": kwargs})
        return _FakePopen(stdout=self.stdout, stderr=self.stderr, exit_code=self.exit_code)


def _wait_for_event(conn: sqlite3.Connection, job_id: str, event_type: str) -> None:
    deadline = time.time() + 3
    while time.time() < deadline:
        events = SqliteUnitOfWork(conn).v2_events.list_by_job(job_id)
        if any(event.type == event_type for event in events):
            return
        time.sleep(0.02)
    raise AssertionError(f"event {event_type!r} was not emitted")


def _seed_fk_refs(conn: sqlite3.Connection) -> None:
    from migration_factory.control_tower.domain.checksums import (
        canonical_json_text,
        sha256_canonical_json,
        utc_now_text,
    )
    now = utc_now_text()
    runner_payload = {
        "schema_version": "1.0.0",
        "runner_profile_id": "runner-default",
        "runner_profile_version": "2026.06",
        "display_name": "Default local runner",
        "python_executable": "C:/Python/python.exe",
        "ai_hub_path": "C:/work/ai-hub",
        "maven": {"executable_path": "mvn", "expected_version": "3.9.9", "allow_wrapper": False},
        "jdks": [
            {"jdk_id": "jdk-17", "java_home": "C:/java/17", "expected_major": 17, "role": "source"},
            {"jdk_id": "jdk-21", "java_home": "C:/java/21", "expected_major": 21, "role": "target"},
        ],
        "filesystem": {
            "roots": [
                {"root_id": "source-root", "kind": "source", "path": "C:/work/legacy"},
                {"root_id": "output-root", "kind": "output", "path": "C:/work/out"},
            ]
        },
        "network": {"mode": "allowlisted", "allowed_hosts": ["repo.local"]},
        "ai_profile": {"profile_id": "local-disabled"},
    }
    conn.execute(
        """INSERT OR IGNORE INTO runner_profiles (
            runner_profile_id, runner_profile_version, display_name, schema_version,
            payload_json, payload_checksum, created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
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
    v1_pipeline = {
        "schema_version": "1.0.0",
        "pipeline_id": "springboot-216-to-356-java21-three-stage",
        "pipeline_version": "2026.06",
        "display_name": "F15 test pipeline",
        "graph_version": "1.0",
        "graph_state_schema_version": "1.0",
        "stages": [
            {
                "stage_index": 1,
                "stage_id": "foundation-diagnostic",
                "profile_id": "diagnostic-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "default",
                "target": {"java": 17, "spring_boot": "3.5.6"},
            },
        ],
    }
    v2_pipeline = {
        "schema_version": "1.0.0",
        "pipeline_id": "springboot-216-to-400-java21-four-stage",
        "pipeline_version": "2026.06",
        "display_name": "V2 migration pipeline (4-stage with Boot 4)",
        "graph_version": "1.0",
        "graph_state_schema_version": "1.0",
        "stages": [
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
        ],
    }
    for pipeline_payload in (v1_pipeline, v2_pipeline):
        conn.execute(
            """INSERT OR IGNORE INTO pipeline_definitions (
                pipeline_id, pipeline_version, display_name, schema_version,
                graph_version, graph_state_schema_version, payload_json, payload_checksum,
                created_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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


def _ready_setup(conn: sqlite3.Connection) -> str:
    repo = SqliteV2SetupRepository(conn)
    service = V2SetupService(repo)
    setup = service.create_setup(
        CreateSetupRequest(
            run_name="gate-api",
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
    return setup.setup_id


def _create_job(client: TestClient, setup_id: str) -> str:
    response = client.post(
        "/v1/v2/migration-jobs",
        json={"setup_id": setup_id},
        headers=_mutation_headers(),
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    return job_id


def _create_gate(conn: sqlite3.Connection, job_id: str, phase: str = "approval_review") -> str:
    with SqliteUnitOfWork(conn) as uow:
        gate_service = V2PhaseGateService(uow.phase_gates)
        result = gate_service.create_gate(
            CreateGateRequest(
                job_id=job_id,
                gate_phase=phase,
                stage_index=2,
                source_artifact_checksum="sha256:gate",
                source_artifact_refs=("analysis:1", "plan:1"),
            )
    )
    assert result.status == "created"
    return result.gate_id


def _create_profile_gate(conn: sqlite3.Connection, job_id: str, *, stage_index: int = 3) -> str:
    route = route_to_dict(compute_profile_route("springboot-3.5-java17", "springboot-3.5-java21"))
    refs = [
        {
            "kind": "profile_route",
            "path_or_ref": "metadata:profile-route",
            "checksum": "sha256:route",
            "profile_metadata": route,
        }
    ]
    now = utc_now_text()
    gate = PhaseGateRecord(
        gate_id=uuid4().hex,
        job_id=job_id,
        gate_phase="approval_review",
        stage_index=stage_index,
        gate_status="open",
        gate_decision="pending",
        source_artifact_checksum="sha256:gate",
        resolved_artifact_checksum=None,
        source_artifact_refs_json=json.dumps(refs, separators=(",", ":")),
        created_at=now,
        resolved_at=None,
        resolved_by=None,
    )
    SqliteUnitOfWork(conn).phase_gates.save(gate)
    return gate.gate_id


def _seed_source_profile_override(conn: sqlite3.Connection, job_id: str) -> None:
    now = utc_now_text()
    SqliteUnitOfWork(conn).artifact_revisions.save(
        ArtifactRevisionRecord(
            revision_id=uuid4().hex,
            job_id=job_id,
            stage_index=1,
            revision_kind="source_profile_override",
            revision_status="accepted",
            revision_order=1,
            evidence_checksum="sha256:source-profile-override",
            prior_revision_checksum=None,
            artifact_refs_json=json.dumps(
                {
                    "requested_source_profile": "springboot-3.5-java17",
                    "target_profile": "springboot-3.5-java21",
                    "detected_source_profile": "springboot-2.7-java11",
                },
                separators=(",", ":"),
            ),
            prior_revision_id=None,
            superseded_by_revision_id=None,
            accepted_at_gate_id="gate-source-profile-override",
            created_at=now,
            created_by="human",
            accepted_at=now,
            accepted_by="human",
        )
    )


def _seed_approval_card(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    checksum: str,
    stage_index: int = 2,
) -> str:
    approval_repo = SqliteV2ApprovalRepository(conn)
    card_id = f"approval-card-{stage_index}"
    approval_repo.save_card(
        V2ApprovalDecisionRecord(
            card_id=card_id,
            job_id=job_id,
            interrupt_id="run-1",
            request_checksum=checksum,
            stage_index=stage_index,
            summary="Pre-transform review",
            status="pending",
            created_at=utc_now_text(),
        )
    )
    return card_id


def _seed_approval_resume_command(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    stage_index: int = 2,
    run_id: str = "run-1",
) -> str:
    command_id = f"command-{stage_index}"
    now = utc_now_text()
    record = V2StageCommandRecord(
        command_id=command_id,
        job_id=job_id,
        stage_index=stage_index,
        manifest_checksum="manifest-checksum",
        argv_json=json.dumps(
            [
                "python",
                "-m",
                "migration_factory.orchestrator.runner",
                "--run-id",
                run_id,
                "--modernized",
                "C:/work/modernized",
            ],
            separators=(",", ":"),
        ),
        env_json="{}",
        status="completed",
        created_at=now,
        updated_at=now,
        result_json=None,
    )
    SqliteV2CommandRepository(conn).save(record)
    return command_id


def _seed_accepted_revisions(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    stage_index: int = 2,
) -> None:
    now = utc_now_text()
    for revision_kind in ("analysis", "planning"):
        SqliteUnitOfWork(conn).artifact_revisions.save(
            ArtifactRevisionRecord(
                revision_id=f"accepted-{revision_kind}-{job_id}-{stage_index}",
                job_id=job_id,
                stage_index=stage_index,
                revision_kind=revision_kind,
                revision_status="accepted",
                revision_order=1,
                evidence_checksum=f"sha256:{revision_kind}",
                prior_revision_checksum=None,
                artifact_refs_json="[]",
                prior_revision_id=None,
                superseded_by_revision_id=None,
                accepted_at_gate_id=f"gate-{revision_kind}",
                created_at=now,
                created_by="test",
                accepted_at=now,
                accepted_by="test",
            )
        )


class _AutoApprovalLaunchRunner:
    """Fake runner that records start_resume and emits transform-start events."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.started: list[str] = []

    def start_resume(self, *, job_id: str, resume_id: str) -> V2OrchestratorStart:
        self.started.append(resume_id)
        with SqliteUnitOfWork(self.connection) as uow:
            uow.v2_events.save(
                job_id=job_id,
                stage=2,
                event_type="resume_started",
                status="running",
                message="Stage resume started after auto approval.",
                payload={"command_id": resume_id},
            )
            uow.v2_events.save(
                job_id=job_id,
                stage=2,
                event_type="sandbox_transform_started",
                status="running",
                message="Transform started after auto approval.",
                payload={"command_id": resume_id},
            )
        return V2OrchestratorStart(
            command_id=resume_id,
            job_id=job_id,
            stage_index=2,
            pid=None,
            status="started",
            message="",
        )

    def start(self, *, job_id: str, command_id: str) -> V2OrchestratorStart:
        raise AssertionError("transform launch is not expected during auto approval")


def test_v2_approval_mode_preflight_allows_patch_from_local_frontend(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)

    response = client.options(
        f"/v1/v2/migration-jobs/{job_id}/approval-mode",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type,x-control-tower-client",
        },
    )

    assert response.status_code in {200, 204}, response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()

def test_v2_approval_mode_endpoint_defaults_false_and_toggles(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)

    job_response = client.get(f"/v1/v2/migration-jobs/{job_id}")
    assert job_response.status_code == 200, job_response.text
    assert job_response.json()["auto_approval_enabled"] is False

    enable_response = client.patch(
        f"/v1/v2/migration-jobs/{job_id}/approval-mode",
        json={"autoApprovalEnabled": True},
        headers=_mutation_headers(),
    )
    assert enable_response.status_code == 200, enable_response.text
    assert enable_response.json()["auto_approval_enabled"] is True
    assert enable_response.json()["autoApprovalEnabled"] is True
    assert enable_response.json()["job"]["auto_approval_enabled"] is True
    assert SqliteUnitOfWork(conn).v2_jobs.get_auto_approval_enabled(job_id) is True

    disable_response = client.patch(
        f"/v1/v2/jobs/{job_id}/approval-mode",
        json={"auto_approval_enabled": False},
        headers=_mutation_headers(),
    )
    assert disable_response.status_code == 200, disable_response.text
    assert disable_response.json()["auto_approval_enabled"] is False
    assert disable_response.json()["autoApprovalEnabled"] is False
    assert SqliteUnitOfWork(conn).v2_jobs.get_auto_approval_enabled(job_id) is False

    events = SqliteUnitOfWork(conn).v2_events.list_by_job(job_id)
    mode_events = [event for event in events if event.type == "approval_mode_updated"]
    assert [json.loads(event.payload_json)["auto_approval_enabled"] for event in mode_events] == [True, False]


def test_v2_approval_mode_enabling_with_no_open_gate_does_not_continue(tmp_path: Path) -> None:
    """Backend Test 1: enabling Auto Approval with no open gate updates the mode only."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)

    response = client.patch(
        f"/v1/v2/migration-jobs/{job_id}/approval-mode",
        json={"autoApprovalEnabled": True},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auto_approval_enabled"] is True
    assert body["autoApprovalEnabled"] is True
    # No open gate -> no auto-approval outcome and no resume queued.
    assert body["auto_approved"] is None
    assert SqliteUnitOfWork(conn).v2_jobs.get_auto_approval_enabled(job_id) is True

    events = SqliteUnitOfWork(conn).v2_events.list_by_job(job_id)
    event_types = [event.type for event in events]
    assert "approval_mode_updated" in event_types
    assert "approval_auto_approved" not in event_types
    assert "approval_resume_queued" not in event_types


def test_v2_approval_mode_enabling_auto_approves_already_open_valid_gate(tmp_path: Path) -> None:
    """Backend Test 2: enabling Auto Approval with an already-open valid gate auto-approves it.

    This test does NOT seed accepted analysis/planning revisions because the
    auto-approval path uses approve_from_gate (same as the manual Approve
    button and the `confirm checksum` assistant command), which does NOT
    require accepted revision records.  The UI shows PASS based on events,
    not on revision records.
    """
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    _seed_approval_resume_command(conn, job_id=job_id, stage_index=2, run_id="run-1")
    gate_id = _create_gate(conn, job_id)
    checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]
    card_id = _seed_approval_card(conn, job_id=job_id, checksum=checksum)

    # Sanity: gate is open and card is pending before enabling Auto Approval.
    uow_before = SqliteUnitOfWork(conn)
    assert uow_before.phase_gates.get(gate_id).gate_status == "open"
    assert uow_before.v2_approvals.get_card(card_id).status == "pending"

    runner = _AutoApprovalLaunchRunner(conn)
    client.app.state.v2_orchestrator_runner = runner

    response = client.patch(
        f"/v1/v2/migration-jobs/{job_id}/approval-mode",
        json={"autoApprovalEnabled": True},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auto_approval_enabled"] is True
    assert body["auto_approved"] is not None
    assert body["auto_approved"]["gate_id"] == gate_id
    assert body["auto_approved"]["resume_id"]

    # The open gate was auto-approved (resolved) and the card is auto_approved.
    uow_after = SqliteUnitOfWork(conn)
    gate_after = uow_after.phase_gates.get(gate_id)
    assert gate_after.gate_status == "resolved"
    assert gate_after.gate_decision == "approve"
    card_after = uow_after.v2_approvals.get_card(card_id)
    assert card_after.status == "auto_approved"

    # A system auto-approval decision was recorded.
    decisions = uow_after.gate_decisions.list_by_job(job_id)
    assert len(decisions) == 1
    assert decisions[0].actor_type == "system"
    assert decisions[0].decided_by == "system:auto-approval"

    # Events show auto-approval + resume continuation toward transform.
    events = uow_after.v2_events.list_by_job(job_id)
    event_types = [event.type for event in events]
    assert "approval_auto_approved" in event_types
    assert "approval_resume_queued" in event_types
    auto_event = next(event for event in events if event.type == "approval_auto_approved")
    auto_payload = json.loads(auto_event.payload_json)
    assert auto_payload["decision_source"] == "auto_approval"
    assert auto_payload["approval_mode"] == "auto"
    assert auto_payload["gate_id"] == gate_id

    # The resume command was launched (transform phase starts).
    assert runner.started == [body["auto_approved"]["resume_id"]]
    assert "resume_started" in event_types
    assert "sandbox_transform_started" in event_types

    # The stage_blocked_for_approval event must NOT appear after auto-approval.
    blocked_events = [event for event in events if event.type == "stage_blocked_for_approval"]
    assert blocked_events == []


def test_v2_auto_approval_skips_unsafe_gate_when_job_already_failed(tmp_path: Path) -> None:
    """Backend Test 4: Auto Approval must not approve a gate when the job is already failed."""
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    _seed_approval_resume_command(conn, job_id=job_id, stage_index=2, run_id="run-1")
    gate_id = _create_gate(conn, job_id)
    checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]
    card_id = _seed_approval_card(conn, job_id=job_id, checksum=checksum)

    # Mark the job as failed via a stage_failed event so the terminal-status
    # guard blocks auto-approval.
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=2,
            event_type="stage_failed",
            status="failed",
            message="Stage failed before auto approval was enabled.",
            payload={},
        )

    runner = _AutoApprovalLaunchRunner(conn)
    client.app.state.v2_orchestrator_runner = runner

    response = client.patch(
        f"/v1/v2/migration-jobs/{job_id}/approval-mode",
        json={"autoApprovalEnabled": True},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auto_approval_enabled"] is True
    # Failed job -> no auto-approval outcome.
    assert body["auto_approved"] is None

    # The gate remains open and the card remains pending (still blocked).
    uow_after = SqliteUnitOfWork(conn)
    assert uow_after.phase_gates.get(gate_id).gate_status == "open"
    assert uow_after.v2_approvals.get_card(card_id).status == "pending"

    # No resume was launched.
    assert runner.started == []

    events = uow_after.v2_events.list_by_job(job_id)
    event_types = [event.type for event in events]
    assert "approval_auto_approved" not in event_types
    assert "approval_resume_queued" not in event_types


def test_v2_gate_list_open_detail_and_legacy_proof_route(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    gate_id = _create_gate(conn, job_id)

    open_before = client.get(f"/v1/v2/jobs/{job_id}/gates/open")
    assert open_before.status_code == 200
    open_gate = open_before.json()["gate"]
    assert open_gate["gate_id"] == gate_id
    assert open_gate["source_artifact_refs"] == ["analysis:1", "plan:1"]

    list_response = client.get(f"/v1/v2/jobs/{job_id}/gates")
    assert list_response.status_code == 200
    gates = list_response.json()["gates"]
    assert len(gates) == 1
    assert gates[0]["gate_id"] == gate_id
    assert gates[0]["gate_phase"] == "approval_review"
    assert gates[0]["available_actions"]

    detail_response = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["gate"]["gate_id"] == gate_id
    assert detail["gate"]["checksum"]
    assert detail["gate"]["available_actions"]
    assert "evidence" in detail

    proof_response = client.get(f"/v1/jobs/{job_id}/proof-gates")
    assert proof_response.status_code in {200, 400}


def test_v2_gate_action_rejects_assistant_authoritative_actions(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    gate_id = _create_gate(conn, job_id)

    payload = {
        "action": "reject",
        "expected_gate_checksum": client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"],
        "idempotency_key": "idem-assistant",
        "decided_by": "assistant-1",
        "actor_type": "assistant",
        "reason": "needs more work",
    }
    response = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json=payload,
        headers=_mutation_headers(),
    )
    assert response.status_code == 403, response.text


def test_v2_gate_action_blocks_approve_after_revision_requested(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    gate_id = _create_gate(conn, job_id)
    checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]

    approval_repo = SqliteV2ApprovalRepository(conn)
    approval_repo.save_card(
        V2ApprovalDecisionRecord(
            card_id="approval-card-blocked",
            interrupt_id="run-1",
            request_checksum=checksum,
            stage_index=2,
            summary="Revision requested",
            status="blocked",
            created_at=utc_now_text(),
            job_id=job_id,
        )
    )

    payload = {
        "action": "approve",
        "expected_gate_checksum": checksum,
        "idempotency_key": "idem-approve-blocked",
        "decided_by": "human-1",
        "actor_type": "human",
    }
    response = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json=payload,
        headers=_mutation_headers(),
    )
    assert response.status_code == 422, response.text
    assert "A revision request is pending" in response.text


def test_v2_gate_action_success_idempotency_and_conflict(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    gate_id = _create_gate(conn, job_id)
    checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]

    base_payload = {
        "action": "reject",
        "expected_gate_checksum": checksum,
        "idempotency_key": "idem-human",
        "decided_by": "human-1",
        "actor_type": "human",
        "reason": "not ready",
    }

    first = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json=base_payload,
        headers=_mutation_headers(),
    )
    assert first.status_code == 200, first.text
    first_result = first.json()["result"]
    assert first_result["status"] == "executed"

    second = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json=base_payload,
        headers=_mutation_headers(),
    )
    assert second.status_code == 409, second.text
    assert "gate is resolved" in second.text.lower()

    conflict_payload = dict(base_payload)
    conflict_payload["reason"] = "different reason"
    conflict = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json=conflict_payload,
        headers=_mutation_headers(),
    )
    assert conflict.status_code == 409, conflict.text


def test_v2_gate_action_rejects_unsafe_fields_and_unsupported_action(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    gate_id = _create_gate(conn, job_id)
    checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]

    unsafe_response = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json={
            "action": "reject",
            "expected_gate_checksum": checksum,
            "idempotency_key": "idem-unsafe",
            "decided_by": "human-1",
            "actor_type": "human",
            "reason": "not ready",
            "sandbox_path": "/tmp/evil",
            "argv": ["rm", "-rf", "/"],
            "env": {"PATH": "bad"},
            "raw_command": "rm -rf /",
            "filesystem_target": "C:/evil",
        },
        headers=_mutation_headers(),
    )
    assert unsafe_response.status_code == 422, unsafe_response.text

    unsupported_response = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json={
            "action": "bogus",
            "expected_gate_checksum": checksum,
            "idempotency_key": "idem-unsupported",
            "decided_by": "human-1",
            "actor_type": "human",
        },
        headers=_mutation_headers(),
    )
    assert unsupported_response.status_code == 422, unsupported_response.text


def test_v2_gate_action_accepts_source_profile_override_contract(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    gate_id = _create_gate(conn, job_id, phase="analysis_review")
    checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]

    response = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json={
            "action": "override_source_profile",
            "expected_gate_checksum": checksum,
            "idempotency_key": "idem-source-profile-override",
            "decided_by": "human-1",
            "actor_type": "human",
            "reason": "Detection confidence was low.",
            "comments": "Project files show this is already Boot 3.5 on Java 17.",
            "detection_artifact_ref": "analysis:1",
            "detected_source_profile": "springboot-2.7-java11",
            "requested_source_profile": "springboot-3.5-java17",
            "target_profile": "springboot-4.0-java21",
            "expected_detection_artifact_checksum": "sha256:gate",
        },
        headers=_mutation_headers(),
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["action"] == "override_source_profile"
    assert result["status"] == "executed"
    assert result["result_revision_id"]
    forbidden = {
        "sandbox_path",
        "argv",
        "env",
        "raw_command",
        "endpoint",
        "deployment",
        "env_ref",
        "filesystem_target",
        "user_supplied_file_path",
    }
    assert forbidden.isdisjoint(result)

    row = conn.execute(
        "SELECT artifact_refs_json FROM v2_artifact_revisions WHERE revision_id = ?",
        (result["result_revision_id"],),
    ).fetchone()
    assert row is not None
    artifact = json.loads(row["artifact_refs_json"])
    assert artifact["requested_source_profile"] == "springboot-3.5-java17"
    assert artifact["profile_validation"]["valid"] is True
    assert forbidden.isdisjoint(artifact)


def test_v2_gate_action_rejects_source_profile_override_bad_contract(
    tmp_path: Path,
) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    gate_id = _create_gate(conn, job_id, phase="analysis_review")
    checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]

    missing_comments = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json={
            "action": "override_source_profile",
            "expected_gate_checksum": checksum,
            "idempotency_key": "idem-source-profile-missing-comments",
            "decided_by": "human-1",
            "actor_type": "human",
            "reason": "Detection confidence was low.",
            "comments": "",
            "detection_artifact_ref": "analysis:1",
            "detected_source_profile": "springboot-2.7-java11",
            "requested_source_profile": "springboot-3.5-java17",
            "target_profile": "springboot-4.0-java21",
            "expected_detection_artifact_checksum": "sha256:gate",
        },
        headers=_mutation_headers(),
    )
    assert missing_comments.status_code == 422, missing_comments.text

    unsafe = client.post(
        f"/v1/v2/jobs/{job_id}/gates/{gate_id}/actions",
        json={
            "action": "override_source_profile",
            "expected_gate_checksum": checksum,
            "idempotency_key": "idem-source-profile-unsafe",
            "decided_by": "human-1",
            "actor_type": "human",
            "reason": "Detection confidence was low.",
            "comments": "Valid operator explanation.",
            "detection_artifact_ref": "analysis:1",
            "detected_source_profile": "springboot-2.7-java11",
            "requested_source_profile": "springboot-3.5-java17",
            "target_profile": "springboot-4.0-java21",
            "expected_detection_artifact_checksum": "sha256:gate",
            "raw_command": "mvn test",
            "filesystem_target": "C:/work/legacy",
        },
        headers=_mutation_headers(),
    )
    assert unsafe.status_code == 422, unsafe.text


def test_v2_approval_route_retries_when_resume_launch_is_locked(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    _seed_approval_resume_command(conn, job_id=job_id, stage_index=2, run_id="run-1")
    gate_id = _create_gate(conn, job_id)
    checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]
    card_id = _seed_approval_card(conn, job_id=job_id, checksum=checksum)

    class _LockedRunner:
        def __init__(self) -> None:
            self.started: list[str] = []

        def start_resume(self, *, job_id: str, resume_id: str):
            self.started.append(resume_id)
            raise sqlite3.OperationalError("database is locked")

        def start(self, *, job_id: str, command_id: str):
            raise AssertionError("transform commands must not be launched here")

    runner = _LockedRunner()
    client.app.state.v2_orchestrator_runner = runner

    response = client.post(
        f"/v1/v2/jobs/{job_id}/approvals/{card_id}/approve",
        json={"expected_checksum": checksum},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["launch_status"] == "retrying"
    assert runner.started == [data["resume_id"]]

    repeat = client.post(
        f"/v1/v2/jobs/{job_id}/approvals/{card_id}/approve",
        json={"expected_checksum": checksum},
        headers=_mutation_headers(),
    )
    assert repeat.status_code == 200, repeat.text
    repeat_data = repeat.json()
    assert repeat_data["launch_status"] == "retrying"
    assert runner.started == [data["resume_id"]]


def test_approval_acceptance_persists_profile_metadata_for_resume_checkpoint(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    _seed_source_profile_override(conn, job_id)
    _seed_approval_resume_command(conn, job_id=job_id, stage_index=3, run_id="run-approval")
    gate_id = _create_profile_gate(conn, job_id)
    gate = SqliteUnitOfWork(conn).phase_gates.get(gate_id)
    assert gate is not None
    checksum = gate_checksum(
        gate_id=gate.gate_id,
        job_id=gate.job_id,
        gate_phase=gate.gate_phase,
        stage_index=gate.stage_index,
        source_artifact_checksum=gate.source_artifact_checksum,
        source_artifact_refs=json.loads(gate.source_artifact_refs_json),
    )
    card_id = _seed_approval_card(conn, job_id=job_id, checksum=checksum, stage_index=3)

    class _ApprovalLaunchRunner:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.started: list[str] = []

        def start_resume(self, *, job_id: str, resume_id: str) -> V2OrchestratorStart:
            self.started.append(resume_id)
            with SqliteUnitOfWork(self.connection) as uow:
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="approval_started",
                    status="running",
                    message="Approval accepted; orchestrator resume process starting.",
                    payload={"command_id": resume_id},
                )
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="resume_started",
                    status="running",
                    message="Stage 3 real orchestrator resume started.",
                    payload={"command_id": resume_id},
                )
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="sandbox_transform_started",
                    status="running",
                    message="Transform started.",
                    payload={"command_id": resume_id},
                )
            return V2OrchestratorStart(
                command_id=resume_id,
                job_id=job_id,
                stage_index=3,
                pid=None,
                status="started",
                message="",
            )

        def start(self, *, job_id: str, command_id: str) -> V2OrchestratorStart:
            raise AssertionError("transform launch is not expected in this test")

    runner = _ApprovalLaunchRunner(conn)
    client.app.state.v2_orchestrator_runner = runner

    response = client.post(
        f"/v1/v2/jobs/{job_id}/approvals/{card_id}/approve",
        json={"expected_checksum": checksum},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["launch_status"] == "started"
    assert runner.started == [body["resume_id"]]

    _wait_for_event(conn, job_id, "resume_started")
    _wait_for_event(conn, job_id, "sandbox_transform_started")

    accepted = SqliteUnitOfWork(conn).artifact_revisions.find_accepted(job_id, 3, "approval_review")
    assert accepted is not None
    assert accepted.evidence_checksum == "sha256:gate"
    refs = json.loads(accepted.artifact_refs_json)
    assert isinstance(refs, list)
    profile_refs = [
        ref for ref in refs
        if isinstance(ref, dict) and isinstance(ref.get("profile_metadata"), dict)
    ]
    assert profile_refs, "accepted approval_review revision should persist profile_metadata"
    profile_metadata = profile_refs[0]["profile_metadata"]
    assert profile_metadata["source_profile"] == "springboot-3.5-java17"
    assert profile_metadata["target_profile"] == "springboot-3.5-java21"
    assert profile_metadata["runtime_profile"] == "springboot-3.5-java17-to-java21"
    assert profile_metadata["included_stages"] == [3]
    assert profile_metadata["skipped_stages"] == [2]
    assert profile_metadata["excluded_stages"] == [4]
    assert profile_metadata["stage_index"] == 3
    assert profile_metadata["run_id"] == "run-approval"


def test_button_approve_resolves_approval_review_gate(tmp_path: Path) -> None:
    """The HTTP Approve button must resolve the approval_review phase gate.

    Regression: approve_decision_card approved the card and queued the resume
    but never resolved the phase gate (unlike the assistant `confirm checksum`
    path). The unresolved earlier-stage gate then permanently shadowed later
    pending stages in GET /gates/open, so the frontend stopped showing Approve
    buttons for later stages.
    """
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    _seed_approval_resume_command(conn, job_id=job_id, stage_index=2, run_id="run-1")
    gate_id = _create_gate(conn, job_id)
    checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]
    card_id = _seed_approval_card(conn, job_id=job_id, checksum=checksum, stage_index=2)

    class _StartedResumeRunner:
        def __init__(self) -> None:
            self.started: list[str] = []

        def start_resume(self, *, job_id: str, resume_id: str) -> V2OrchestratorStart:
            self.started.append(resume_id)
            return V2OrchestratorStart(
                command_id=resume_id,
                job_id=job_id,
                stage_index=2,
                pid=None,
                status="started",
                message="",
            )

        def start(self, *, job_id: str, command_id: str) -> V2OrchestratorStart:
            raise AssertionError("transform launch is not expected here")

    runner = _StartedResumeRunner()
    client.app.state.v2_orchestrator_runner = runner

    response = client.post(
        f"/v1/v2/jobs/{job_id}/approvals/{card_id}/approve",
        json={"expected_checksum": checksum},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["launch_status"] == "started"
    assert runner.started, "resume command should have been launched"

    # KEY REGRESSION ASSERTION: the approval_review gate is now resolved.
    gate = SqliteUnitOfWork(conn).phase_gates.get(gate_id)
    assert gate is not None
    assert gate.gate_status == "resolved"
    assert gate.gate_decision == "approve"

    # GET /gates/open must no longer surface this resolved earlier-stage gate.
    open_resp = client.get(f"/v1/v2/jobs/{job_id}/gates/open")
    assert open_resp.status_code == 200
    open_gate = open_resp.json().get("gate")
    assert open_gate is None or open_gate["gate_id"] != gate_id


def test_open_gate_endpoint_advances_past_resolved_stage_to_later_stage(tmp_path: Path) -> None:
    """GET /gates/open must surface a later pending stage once earlier stages are resolved.

    An approved old gate must not shadow a newer pending gate. This is the
    backend contract the frontend relies on to render later-stage Approve
    buttons.
    """
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    now = utc_now_text()
    gate2 = PhaseGateRecord(
        gate_id=uuid4().hex,
        job_id=job_id,
        gate_phase="approval_review",
        stage_index=2,
        gate_status="resolved",
        gate_decision="approve",
        source_artifact_checksum="sha256:gate-2",
        resolved_artifact_checksum=None,
        source_artifact_refs_json=json.dumps(["analysis:1", "plan:1"]),
        created_at=now,
        resolved_at=now,
        resolved_by="human",
    )
    gate3 = PhaseGateRecord(
        gate_id=uuid4().hex,
        job_id=job_id,
        gate_phase="approval_review",
        stage_index=3,
        gate_status="open",
        gate_decision="pending",
        source_artifact_checksum="sha256:gate-3",
        resolved_artifact_checksum=None,
        source_artifact_refs_json=json.dumps(["analysis:1", "plan:1"]),
        created_at=now,
        resolved_at=None,
        resolved_by=None,
    )
    with SqliteUnitOfWork(conn) as uow:
        uow.phase_gates.save(gate2)
        uow.phase_gates.save(gate3)

    open_resp = client.get(f"/v1/v2/jobs/{job_id}/gates/open")
    assert open_resp.status_code == 200
    open_gate = open_resp.json()["gate"]
    assert open_gate is not None
    assert open_gate["gate_id"] == gate3.gate_id
    assert open_gate["stage_index"] == 3


def test_stage3_java21_route_assistant_confirm_does_not_start_transform(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    _seed_source_profile_override(conn, job_id)
    _seed_approval_resume_command(conn, job_id=job_id, stage_index=3, run_id="run-ask")
    gate_id = _create_profile_gate(conn, job_id)
    gate = SqliteUnitOfWork(conn).phase_gates.get(gate_id)
    assert gate is not None
    checksum = gate_checksum(
        gate_id=gate.gate_id,
        job_id=gate.job_id,
        gate_phase=gate.gate_phase,
        stage_index=gate.stage_index,
        source_artifact_checksum=gate.source_artifact_checksum,
        source_artifact_refs=json.loads(gate.source_artifact_refs_json),
    )
    card_id = _seed_approval_card(conn, job_id=job_id, checksum=checksum, stage_index=3)

    class _ApprovalLaunchRunner:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.started: list[str] = []

        def start_resume(self, *, job_id: str, resume_id: str) -> V2OrchestratorStart:
            self.started.append(resume_id)
            with SqliteUnitOfWork(self.connection) as uow:
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="approval_started",
                    status="running",
                    message="Approval accepted; orchestrator resume process starting.",
                    payload={"command_id": resume_id},
                )
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="resume_started",
                    status="running",
                    message="Stage 3 real orchestrator resume started.",
                    payload={"command_id": resume_id},
                )
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="sandbox_transform_started",
                    status="running",
                    message="Transform started.",
                    payload={"command_id": resume_id},
                )
            return V2OrchestratorStart(
                command_id=resume_id,
                job_id=job_id,
                stage_index=3,
                pid=None,
                status="started",
                message="",
            )

        def start(self, *, job_id: str, command_id: str) -> V2OrchestratorStart:
            raise AssertionError("transform launch is not expected in this test")

    runner = _ApprovalLaunchRunner(conn)
    client.app.state.v2_orchestrator_runner = runner

    preview = client.post(
        f"/v1/v2/jobs/{job_id}/assistant/ask",
        json={"question": "approve"},
        headers=_mutation_headers(),
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body.get("executed") is False

    confirm = client.post(
        f"/v1/v2/jobs/{job_id}/assistant/ask",
        json={"question": f"confirm checksum {checksum}"},
        headers=_mutation_headers(),
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body.get("executed") is False
    assert "execution_result" not in body
    assert runner.started == []
    with SqliteUnitOfWork(conn, transaction_mode="read") as uow:
        stored_gate = uow.phase_gates.get(gate_id)
        stored_card = uow.v2_approvals.get_card(card_id)
        events = uow.v2_events.list_by_job(job_id)
    assert stored_gate is not None and stored_gate.gate_status == "open"
    assert stored_card is not None and stored_card.status == "pending"
    assert not [event for event in events if event.type == "resume_started"]
    assert not [event for event in events if event.type == "sandbox_transform_started"]


def test_stage3_java21_route_approval_resume_starts_transform(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    _seed_source_profile_override(conn, job_id)
    _seed_approval_resume_command(conn, job_id=job_id, stage_index=3, run_id="run-stage3")
    gate_id = _create_profile_gate(conn, job_id)
    gate = SqliteUnitOfWork(conn).phase_gates.get(gate_id)
    assert gate is not None
    checksum = gate_checksum(
        gate_id=gate.gate_id,
        job_id=gate.job_id,
        gate_phase=gate.gate_phase,
        stage_index=gate.stage_index,
        source_artifact_checksum=gate.source_artifact_checksum,
        source_artifact_refs=json.loads(gate.source_artifact_refs_json),
    )
    card_id = _seed_approval_card(conn, job_id=job_id, checksum=checksum, stage_index=3)

    class _ApprovalLaunchRunner:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.started: list[str] = []

        def start_resume(self, *, job_id: str, resume_id: str) -> V2OrchestratorStart:
            self.started.append(resume_id)
            with SqliteUnitOfWork(self.connection) as uow:
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="approval_started",
                    status="running",
                    message="Approval accepted; orchestrator resume process starting.",
                    payload={"command_id": resume_id},
                )
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="resume_started",
                    status="running",
                    message="Stage 3 real orchestrator resume started.",
                    payload={"command_id": resume_id},
                )
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="sandbox_transform_started",
                    status="running",
                    message="Transform started.",
                    payload={"command_id": resume_id},
                )
            return V2OrchestratorStart(
                command_id=resume_id,
                job_id=job_id,
                stage_index=3,
                pid=None,
                status="started",
                message="",
            )

        def start(self, *, job_id: str, command_id: str) -> V2OrchestratorStart:
            raise AssertionError("transform launch is not expected in this test")

    runner = _ApprovalLaunchRunner(conn)
    client.app.state.v2_orchestrator_runner = runner

    response = client.post(
        f"/v1/v2/jobs/{job_id}/approvals/{card_id}/approve",
        json={"expected_checksum": checksum},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["launch_status"] == "started"
    assert runner.started == [body["resume_id"]]

    _wait_for_event(conn, job_id, "resume_started")
    _wait_for_event(conn, job_id, "sandbox_transform_started")


def test_v2_approval_route_surfaces_resume_rejection_without_queued_event(tmp_path: Path) -> None:
    client, conn = _api_client(tmp_path)
    setup_id = _ready_setup(conn)
    job_id = _create_job(client, setup_id)
    seed_job(conn, job_id=job_id)
    _seed_source_profile_override(conn, job_id)
    _seed_approval_resume_command(conn, job_id=job_id, stage_index=3, run_id="run-reject")
    gate_id = _create_gate(conn, job_id)
    gate = SqliteUnitOfWork(conn).phase_gates.get(gate_id)
    assert gate is not None
    checksum = gate_checksum(
        gate_id=gate.gate_id,
        job_id=gate.job_id,
        gate_phase=gate.gate_phase,
        stage_index=gate.stage_index,
        source_artifact_checksum=gate.source_artifact_checksum,
        source_artifact_refs=json.loads(gate.source_artifact_refs_json),
    )
    card_id = _seed_approval_card(conn, job_id=job_id, checksum=checksum, stage_index=3)

    class _RejectedResumeRunner:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.started: list[str] = []

        def start_resume(self, *, job_id: str, resume_id: str) -> V2OrchestratorStart:
            self.started.append(resume_id)
            with SqliteUnitOfWork(self.connection) as uow:
                uow.v2_events.save(
                    job_id=job_id,
                    stage=3,
                    event_type="resume_rejected",
                    status="blocked",
                    message="Resume checkpoint validation rejected the request.",
                    payload={
                        "command_id": resume_id,
                        "reason": "checkpoint_profile_metadata_missing",
                    },
                )
            return V2OrchestratorStart(
                command_id=resume_id,
                job_id=job_id,
                stage_index=3,
                pid=None,
                status="rejected",
                message="checkpoint_profile_metadata_missing",
            )

        def start(self, *, job_id: str, command_id: str) -> V2OrchestratorStart:
            raise AssertionError("transform launch is not expected in this test")

    runner = _RejectedResumeRunner(conn)
    client.app.state.v2_orchestrator_runner = runner

    response = client.post(
        f"/v1/v2/jobs/{job_id}/approvals/{card_id}/approve",
        json={"expected_checksum": checksum},
        headers=_mutation_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["launch_status"] == "rejected"
    assert runner.started == [body["resume_id"]]

    _wait_for_event(conn, job_id, "resume_rejected")
    events = SqliteUnitOfWork(conn).v2_events.list_by_job(job_id)
    assert not any(event.type == "approval_resume_queued" for event in events)
    pipeline = client.get(f"/v1/v2/migration-jobs/{job_id}/pipeline").json()
    approval_row = [row for row in pipeline["rows"] if row["key"] == "human_approval"][0]
    assert approval_row["status"] == "blocked"


class TestV2JobPolicyPersistence:
    def test_create_job_defaults_to_auto_on_green_policy(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)

        response = client.post(
            "/v1/v2/migration-jobs",
            json={"setup_id": setup_id},
            headers=_mutation_headers(),
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["stage_continuation_policy"] == "auto_on_green"
        assert data["run_configuration_id"]

        row = conn.execute(
            "SELECT job_id, policy_json FROM run_configurations WHERE job_id = ?",
            (data["job_id"],),
        ).fetchone()
        assert row is not None
        policy = json.loads(row["policy_json"])
        assert policy["stage_continuation_policy"] == "auto_on_green"

    def test_create_job_with_explicit_manual_policy(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)

        response = client.post(
            "/v1/v2/migration-jobs",
            json={
                "setup_id": setup_id,
                "policy": {"stage_continuation_policy": "manual"},
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["stage_continuation_policy"] == "manual"

        row = conn.execute(
            "SELECT policy_json FROM run_configurations WHERE job_id = ?",
            (data["job_id"],),
        ).fetchone()
        assert row is not None
        policy = json.loads(row["policy_json"])
        assert policy["stage_continuation_policy"] == "manual"

    def test_create_job_with_auto_on_green_policy(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)

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
        policy = json.loads(row["policy_json"])
        assert policy["stage_continuation_policy"] == "auto_on_green"

    def test_create_job_with_manual_on_warning_policy(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)

        response = client.post(
            "/v1/v2/migration-jobs",
            json={
                "setup_id": setup_id,
                "policy": {"stage_continuation_policy": "manual_on_warning_or_failure"},
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["stage_continuation_policy"] == "manual_on_warning_or_failure"

    def test_create_job_rejects_unknown_policy(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)

        response = client.post(
            "/v1/v2/migration-jobs",
            json={
                "setup_id": setup_id,
                "policy": {"stage_continuation_policy": "skip_stages"},
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 422, response.text

    def test_get_job_returns_policy_for_existing_job(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)

        create_resp = client.post(
            "/v1/v2/migration-jobs",
            json={
                "setup_id": setup_id,
                "policy": {"stage_continuation_policy": "manual"},
            },
            headers=_mutation_headers(),
        )
        job_id = create_resp.json()["job_id"]

        get_resp = client.get(f"/v1/v2/migration-jobs/{job_id}")
        assert get_resp.status_code == 200, get_resp.text
        data = get_resp.json()
        assert data["stage_continuation_policy"] == "manual"
        assert data["run_configuration_id"]


# ── F5: Reviewed repair approval endpoint tests ───────────────────────


def _create_repair_review_gate(conn: sqlite3.Connection, job_id: str) -> str:
    from migration_factory.control_tower.domain.checksums import sha256_canonical_json

    binding = {
        "failure_evidence_checksum": "sha256:ev",
        "context_pack_checksum": "sha256:cp",
        "primary_output_checksum": "sha256:po",
        "reviewer_output_checksum": "sha256:ro",
        "final_reviewed_diff_checksum": "sha256:rd",
        "policy_validation_checksum": "sha256:pv",
        "base_repo_state_checksum": "sha256:rs",
        "final_artifact_checksum": "sha256:fa",
    }
    source_checksum = sha256_canonical_json(binding)
    with SqliteUnitOfWork(conn) as uow:
        gate_service = V2PhaseGateService(uow.phase_gates)
        result = gate_service.create_gate(
            CreateGateRequest(
                job_id=job_id,
                gate_phase="repair_review",
                stage_index=3,
                source_artifact_checksum=source_checksum,
                source_artifact_refs=tuple(
                    f"{key}:{value}" for key, value in binding.items()
                ),
            )
        )
    assert result.status == "created"
    return result.gate_id


class TestReviewedRepairApprovalEndpoint:
    """F5 TASK 1: Reviewed repair approval endpoint API tests."""

    def _seed_repair_proposal(self, conn: sqlite3.Connection, proposal_id: str) -> None:
        from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
            SqliteV2RepairRepository,
            V2RepairProposalRecord,
        )
        from migration_factory.control_tower.domain.checksums import utc_now_text
        repo = SqliteV2RepairRepository(conn)
        repo.save_proposal(
            V2RepairProposalRecord(
                proposal_id=proposal_id,
                command_id="cmd-f5",
                failure_summary="Build failed",
                hypothesis="Missing dependency",
                patch_summary="Add H2 runtime",
                affected_paths_json=json.dumps(["pom.xml"]),
                status="draft",
                approval_checksum=None,
                created_at=utc_now_text(),
                proposal_checksum="sha256:prop",
                source_proposal_id=None,
                revision_of=None,
                revision_number=None,
                context_pack_checksum="sha256:cp",
                allowed_scope=None,
            )
        )

    def _seed_reviewer_critique(
        self, conn: sqlite3.Connection, proposal_id: str, decision: str = "accept"
    ) -> None:
        from migration_factory.control_tower.infrastructure.sqlite.v2_reviewer_repository import (
            SqliteV2ReviewerRepository,
            V2ReviewerCritiqueRecord,
        )
        from migration_factory.control_tower.domain.checksums import utc_now_text
        repo = SqliteV2ReviewerRepository(conn)
        repo.save_critique(
            V2ReviewerCritiqueRecord(
                critique_id=f"critique-{proposal_id}",
                proposal_id=proposal_id,
                proposal_type="repair",
                proposal_checksum="sha256:prop",
                context_pack_checksum="sha256:cp",
                decision=decision,
                reasoning="Looks correct",
                missing_evidence_json="[]",
                unsafe_assumptions_json="[]",
                model_invocation_id=None,
                created_at=utc_now_text(),
            )
        )

    def test_approve_reviewed_repair_succeeds_with_checksums_only(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)
        job_id = _create_job(client, setup_id)
        seed_job(conn, job_id=job_id)
        gate_id = _create_repair_review_gate(conn, job_id)
        self._seed_repair_proposal(conn, gate_id)
        self._seed_reviewer_critique(conn, gate_id, decision="accept")

        gate_detail = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()
        checksum = gate_detail["checksum"]

        payload = {
            "expected_gate_checksum": checksum,
            "proposal_checksum": "sha256:prop",
            "context_pack_checksum": "sha256:cp",
            "reviewer_output_checksum": "sha256:ro",
            "final_reviewed_diff_checksum": "sha256:rd",
            "policy_validation_checksum": "sha256:pv",
            "base_repo_state_checksum": "sha256:rs",
            "decided_by": "human-1",
            "comments": "Looks good",
        }
        response = client.post(
            f"/v1/v2/jobs/{job_id}/gates/{gate_id}/approve-reviewed-repair",
            json=payload,
            headers=_mutation_headers(),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["result"]["status"] in ("executed", "idempotent")
        assert data["result"]["job_id"] == job_id

    def test_approve_reviewed_repair_rejects_extra_forbidden_fields(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)
        job_id = _create_job(client, setup_id)
        seed_job(conn, job_id=job_id)
        gate_id = _create_repair_review_gate(conn, job_id)
        self._seed_repair_proposal(conn, gate_id)

        gate_detail = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()
        checksum = gate_detail["checksum"]

        payload = {
            "expected_gate_checksum": checksum,
            "proposal_checksum": "sha256:prop",
            "context_pack_checksum": "sha256:cp",
            "reviewer_output_checksum": "sha256:ro",
            "final_reviewed_diff_checksum": "sha256:rd",
            "policy_validation_checksum": "sha256:pv",
            "base_repo_state_checksum": "sha256:rs",
            "decided_by": "human-1",
            "raw_diff": "evil diff here",
        }
        response = client.post(
            f"/v1/v2/jobs/{job_id}/gates/{gate_id}/approve-reviewed-repair",
            json=payload,
            headers=_mutation_headers(),
        )
        assert response.status_code == 422, response.text

    def test_approve_reviewed_repair_rejects_missing_reviewer_checksum(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)
        job_id = _create_job(client, setup_id)
        seed_job(conn, job_id=job_id)
        gate_id = _create_repair_review_gate(conn, job_id)
        self._seed_repair_proposal(conn, gate_id)

        payload = {
            "expected_gate_checksum": "any-checksum",
            "proposal_checksum": "sha256:prop",
            "context_pack_checksum": "sha256:cp",
            "reviewer_output_checksum": "",
            "final_reviewed_diff_checksum": "sha256:rd",
            "policy_validation_checksum": "sha256:pv",
            "base_repo_state_checksum": "sha256:rs",
            "decided_by": "human-1",
        }
        response = client.post(
            f"/v1/v2/jobs/{job_id}/gates/{gate_id}/approve-reviewed-repair",
            json=payload,
            headers=_mutation_headers(),
        )
        assert response.status_code == 422, response.text

    def test_approve_reviewed_repair_rejects_missing_policy_checksum(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)
        job_id = _create_job(client, setup_id)
        seed_job(conn, job_id=job_id)
        gate_id = _create_repair_review_gate(conn, job_id)
        self._seed_repair_proposal(conn, gate_id)

        payload = {
            "expected_gate_checksum": "any-checksum",
            "proposal_checksum": "sha256:prop",
            "context_pack_checksum": "sha256:cp",
            "reviewer_output_checksum": "sha256:ro",
            "final_reviewed_diff_checksum": "sha256:rd",
            "policy_validation_checksum": "",
            "base_repo_state_checksum": "sha256:rs",
            "decided_by": "human-1",
        }
        response = client.post(
            f"/v1/v2/jobs/{job_id}/gates/{gate_id}/approve-reviewed-repair",
            json=payload,
            headers=_mutation_headers(),
        )
        assert response.status_code in (422, 409), response.text

    def test_approve_reviewed_repair_rejects_wrong_gate_phase(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)
        job_id = _create_job(client, setup_id)
        seed_job(conn, job_id=job_id)
        gate_id = _create_gate(conn, job_id, phase="approval_review")

        payload = {
            "expected_gate_checksum": "any-checksum",
            "proposal_checksum": "sha256:prop",
            "context_pack_checksum": "sha256:cp",
            "reviewer_output_checksum": "sha256:ro",
            "final_reviewed_diff_checksum": "sha256:rd",
            "policy_validation_checksum": "sha256:pv",
            "base_repo_state_checksum": "sha256:rs",
            "decided_by": "human-1",
        }
        response = client.post(
            f"/v1/v2/jobs/{job_id}/gates/{gate_id}/approve-reviewed-repair",
            json=payload,
            headers=_mutation_headers(),
        )
        assert response.status_code == 422, response.text

    def test_approve_reviewed_repair_rejects_non_open_gate(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)
        job_id = _create_job(client, setup_id)
        seed_job(conn, job_id=job_id)
        gate_id = _create_repair_review_gate(conn, job_id)
        self._seed_repair_proposal(conn, gate_id)
        self._seed_reviewer_critique(conn, gate_id, decision="accept")

        gate_detail = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()
        checksum = gate_detail["checksum"]

        payload = {
            "expected_gate_checksum": checksum,
            "proposal_checksum": "sha256:prop",
            "context_pack_checksum": "sha256:cp",
            "reviewer_output_checksum": "sha256:ro",
            "final_reviewed_diff_checksum": "sha256:rd",
            "policy_validation_checksum": "sha256:pv",
            "base_repo_state_checksum": "sha256:rs",
            "decided_by": "human-1",
        }
        # First approval — should succeed
        response1 = client.post(
            f"/v1/v2/jobs/{job_id}/gates/{gate_id}/approve-reviewed-repair",
            json=payload,
            headers=_mutation_headers(),
        )
        assert response1.status_code == 200, response1.text

        # Second approval — gate already resolved
        response2 = client.post(
            f"/v1/v2/jobs/{job_id}/gates/{gate_id}/approve-reviewed-repair",
            json=payload,
            headers=_mutation_headers(),
        )
        assert response2.status_code == 409, response2.text

    def test_approve_reviewed_repair_response_does_not_leak_secrets(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)
        job_id = _create_job(client, setup_id)
        seed_job(conn, job_id=job_id)
        gate_id = _create_repair_review_gate(conn, job_id)
        self._seed_repair_proposal(conn, gate_id)
        self._seed_reviewer_critique(conn, gate_id, decision="accept")

        gate_detail = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()
        checksum = gate_detail["checksum"]

        payload = {
            "expected_gate_checksum": checksum,
            "proposal_checksum": "sha256:prop",
            "context_pack_checksum": "sha256:cp",
            "reviewer_output_checksum": "sha256:ro",
            "final_reviewed_diff_checksum": "sha256:rd",
            "policy_validation_checksum": "sha256:pv",
            "base_repo_state_checksum": "sha256:rs",
            "decided_by": "human-1",
        }
        response = client.post(
            f"/v1/v2/jobs/{job_id}/gates/{gate_id}/approve-reviewed-repair",
            json=payload,
            headers=_mutation_headers(),
        )
        data = response.json()
        result_json = json.dumps(data)
        for forbidden in ("sandbox_path", "argv", "env", "raw_command", "endpoint", "deployment", "env_ref"):
            assert forbidden not in result_json.lower(), f"forbidden key {forbidden!r} leaked"

    def test_approve_reviewed_repair_applies_backend_artifact_when_runtime_context_is_available(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from migration_factory.control_tower.application.v2_repair_flow import (
            V2RepairFlowService,
        )
        from migration_factory.control_tower.application.v2_repair_flow import SandboxAction
        from migration_factory.control_tower.domain.checksums import sha256_canonical_json, utc_now_text
        from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
            SqliteV2CommandRepository,
            V2StageCommandRecord,
        )
        from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
            SqliteV2RepairRepository,
            V2RepairProposalRecord,
        )

        client, conn = _api_client(tmp_path)
        setup_id = _ready_setup(conn)
        job_id = _create_job(client, setup_id)
        seed_job(conn, job_id=job_id)

        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "src").mkdir()
        (sandbox / "src" / "App.java").write_text("class App {}\n", encoding="utf-8")

        modernized = tmp_path / "modernized"
        modernized.mkdir()
        run_id = "run-1"
        run_dir = modernized / ".migration" / "runs" / run_id
        repairs_dir = run_dir / "repairs"
        repairs_dir.mkdir(parents=True, exist_ok=True)

        deterministic_artifact = repairs_dir / "deterministic_repair_artifact.json"
        primary_output = repairs_dir / "primary_repair_llm_output.json"
        final_artifact = repairs_dir / "final_reviewed_repair_artifact.json"
        final_diff = repairs_dir / "final_reviewed_repair.diff"
        reviewer_output = repairs_dir / "reviewer_repair_llm_output.json"

        deterministic_artifact.write_text(
            json.dumps(
                {
                    "deterministic_rule_id": "DEPENDENCY_ADD_H2_RUNTIME",
                    "source_profile": "analysis",
                    "target_profile": "finalize",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        diff_text = """diff --git a/src/App.java b/src/App.java
--- a/src/App.java
+++ b/src/App.java
@@
-class App {}
+class App { int version = 2; }
"""
        final_diff.write_text(diff_text, encoding="utf-8")
        primary_output.write_text(
            json.dumps(
                {
                    "risk": "LOW",
                    "proposed_diff": diff_text,
                    "deterministic_rule_id": "DEPENDENCY_ADD_H2_RUNTIME",
                    "changed_files": ["src/App.java"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        reviewer_output.write_text(
            json.dumps({"decision": "accept"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        final_artifact.write_text(
            json.dumps({"risk": "LOW", "reviewer_decision": "accept"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rerun_result_path = repairs_dir / "repair_rerun_result.json"
        proof_path = repairs_dir / "repair_proof.json"
        rerun_result_path.write_text(
            json.dumps({"passed": True, "artifact_checksum": "sha256:rerun"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        proof_path.write_text(
            json.dumps({"status": "REPAIR_VALIDATED", "artifact_checksum": "sha256:proof"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        command_id = "cmd-reviewed-repair"
        command_repo = SqliteV2CommandRepository(conn)
        command_repo.save(
            V2StageCommandRecord(
                command_id=command_id,
                job_id=job_id,
                stage_index=3,
                manifest_checksum="manifest-reviewed",
                argv_json=json.dumps(
                    [
                        "python",
                        "-m",
                        "migration_factory.orchestrator.runner",
                        "--run-id",
                        run_id,
                        "--modernized",
                        str(modernized),
                    ],
                    separators=(",", ":"),
                ),
                env_json=json.dumps({}, separators=(",", ":")),
                status="completed",
                created_at=utc_now_text(),
                updated_at=utc_now_text(),
                result_json=json.dumps(
                    {"sandbox_path": str(sandbox)},
                    separators=(",", ":"),
                ),
            )
        )

        binding = {
            "failure_evidence_checksum": "sha256:ev",
            "context_pack_checksum": "sha256:cp",
            "primary_output_checksum": "sha256:po",
            "reviewer_output_checksum": "sha256:ro",
            "final_reviewed_diff_checksum": "sha256:rd",
            "policy_validation_checksum": "sha256:pv",
            "base_repo_state_checksum": "sha256:rs",
            "final_artifact_checksum": "sha256:fa",
        }
        gate_result = V2PhaseGateService(SqliteUnitOfWork(conn).phase_gates).create_gate(
            CreateGateRequest(
                job_id=job_id,
                gate_phase="repair_review",
                stage_index=3,
                source_artifact_checksum=sha256_canonical_json(binding),
                source_artifact_refs=(
                    str(deterministic_artifact),
                    str(primary_output),
                    str(reviewer_output),
                    str(final_artifact),
                    str(final_diff),
                    *(f"{key}:{value}" for key, value in binding.items()),
                ),
            )
        )
        assert gate_result.status == "created"
        gate_id = gate_result.gate_id

        repair_repo = SqliteV2RepairRepository(conn)
        repair_repo.save_proposal(
            V2RepairProposalRecord(
                proposal_id=gate_id,
                command_id=command_id,
                failure_summary="Build failed",
                hypothesis="Missing dependency",
                patch_summary="Add H2 runtime",
                affected_paths_json=json.dumps(["src/App.java"]),
                status="draft",
                approval_checksum=None,
                created_at=utc_now_text(),
                proposal_checksum="sha256:prop",
                source_proposal_id=None,
                revision_of=None,
                revision_number=None,
                context_pack_checksum="sha256:cp",
                allowed_scope=None,
            )
        )
        self._seed_reviewer_critique(conn, gate_id, decision="accept")

        gate_checksum = client.get(f"/v1/v2/jobs/{job_id}/gates/{gate_id}").json()["checksum"]
        payload = {
            "expected_gate_checksum": gate_checksum,
            "proposal_checksum": "sha256:prop",
            "context_pack_checksum": "sha256:cp",
            "reviewer_output_checksum": "sha256:ro",
            "final_reviewed_diff_checksum": "sha256:rd",
            "policy_validation_checksum": "sha256:pv",
            "base_repo_state_checksum": "sha256:rs",
            "decided_by": "human-1",
            "comments": "Apply exact reviewed diff",
        }

        calls: dict[str, object] = {}

        def fake_apply(self, **kwargs):
            calls.update(kwargs)
            return SandboxAction(
                action_id="action-1",
                proposal_id=kwargs["proposal_id"],
                target_path=kwargs["target_path"],
                patch_content=str(final_diff.read_text(encoding="utf-8")),
                status="applied",
                result_summary="patched and validated",
                created_at=utc_now_text(),
            )

        monkeypatch.setattr(V2RepairFlowService, "apply_reviewed_repair_diff", fake_apply)

        response = client.post(
            f"/v1/v2/jobs/{job_id}/gates/{gate_id}/approve-reviewed-repair",
            json=payload,
            headers=_mutation_headers(),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["result"]["apply_status"] == "applied"
        assert calls["final_diff_ref"].endswith("final_reviewed_repair.diff")
        assert calls["target_path"] == "src/App.java"
        assert calls["deterministic_rule_id"] == "DEPENDENCY_ADD_H2_RUNTIME"
