"""V2 real orchestrator subprocess runner tests."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from migration_factory.control_tower.application.v2_orchestrator_runner import V2OrchestratorRunner
from migration_factory.control_tower.application.v2_profile_runtime import RouteRuntimeProfileUnavailableError
from migration_factory.control_tower.domain.checksums import canonical_json_text, sha256_canonical_json, utc_now_text
from migration_factory.control_tower.domain.entities import RunConfigurationRecord
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import V2MigrationJobRecord
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import V2StageCommandRecord
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import V2MigrationSetupRecord
from migration_factory.control_tower.application.v2_stage_progression import (
    compute_profile_route,
    route_to_dict,
)
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.domain.entities import ArtifactRevisionRecord, PhaseGateRecord
from migration_factory.control_tower.domain.gate_checksum import gate_checksum
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import (
    V2ApprovalDecisionRecord,
    V2ResumeCommandRecord,
)


AI_HUB = Path(__file__).resolve().parents[2] / "modernizer-solution-ai-hub"


class _FakeProcess:
    def __init__(self, stdout: list[str], stderr: list[str], exit_code: int) -> None:
        self.stdout = iter(stdout)
        self.stderr = iter(stderr)
        self.pid = 12345
        self._exit_code = exit_code

    def wait(self) -> int:
        return self._exit_code


class _FakePopen:
    def __init__(self, stdout: list[str], stderr: list[str], exit_code: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> _FakeProcess:
        self.calls.append({"argv": argv, **kwargs})
        return _FakeProcess(self.stdout, self.stderr, self.exit_code)


class _SequentialFakePopen:
    def __init__(self, responses: list[tuple[list[str], list[str], int]]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []
        self._index = 0

    def __call__(self, argv: list[str], **kwargs: Any) -> _FakeProcess:
        if self._index >= len(self._responses):
            raise AssertionError(f"unexpected process launch #{self._index + 1}: {argv!r}")
        stdout, stderr, exit_code = self._responses[self._index]
        self._index += 1
        self.calls.append({"argv": argv, **kwargs})
        return _FakeProcess(stdout, stderr, exit_code)


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        tmp_path / "runner.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _save_command(conn: sqlite3.Connection, *, command_id: str = "cmd-1", job_id: str = "job-1") -> None:
    now = utc_now_text()
    SqliteUnitOfWork(conn).v2_commands.save(
        V2StageCommandRecord(
            command_id=command_id,
            job_id=job_id,
            stage_index=1,
            manifest_checksum="checksum",
            argv_json=json.dumps(["python", "-m", "migration_factory.orchestrator.runner", "--run-id", "run-1"]),
            env_json=json.dumps({
                "JAVA_HOME": "C:/jdk11",
                "JAVA11_HOME": "C:/jdk11",
                "JAVA17_HOME": "C:/jdk17",
                "JAVA21_HOME": "C:/jdk21",
                "MAVEN_CMD": "C:/maven/bin/mvn.cmd",
                "PATH_PREPEND": "C:/jdk11/bin",
            }),
            status="manifest_ready",
            created_at=now,
            updated_at=now,
            result_json=None,
        )
    )


def _save_phase_command(
    conn: sqlite3.Connection,
    *,
    command_id: str = "cmd-phase",
    job_id: str = "job-1",
    stage_index: int = 1,
    phase: str = "analysis",
) -> None:
    now = utc_now_text()
    SqliteUnitOfWork(conn).v2_commands.save(
        V2StageCommandRecord(
            command_id=command_id,
            job_id=job_id,
            stage_index=stage_index,
            manifest_checksum=f"phase:{phase}",
            argv_json="[]",
            env_json=json.dumps({
                "JAVA_HOME": "C:/jdk11",
                "JAVA11_HOME": "C:/jdk11",
                "JAVA17_HOME": "C:/jdk17",
                "JAVA21_HOME": "C:/jdk21",
                "MAVEN_CMD": "C:/maven/bin/mvn.cmd",
                "PATH_PREPEND": "C:/jdk11/bin",
            }),
            status="manifest_ready",
            created_at=now,
            updated_at=now,
            result_json=None,
        )
    )


def _seed_resume_gate(
    conn: sqlite3.Connection,
    *,
    job_id: str = "job-1",
    stage_index: int = 1,
    source_profile: str = "springboot-2.7-java11",
    target_profile: str = "springboot-4.0-java21",
    gate_source_checksum: str = "sha256:test-source",
    gate_phase: str = "approval_review",
) -> str:
    from migration_factory.control_tower.application.v2_stage_progression import (
        compute_profile_route,
        route_to_dict,
    )

    now = utc_now_text()
    route = route_to_dict(compute_profile_route(source_profile, target_profile))
    refs = [
        {
            "kind": "profile_route",
            "path_or_ref": "metadata:profile-route",
            "checksum": "sha256:route",
            "profile_metadata": route,
        }
    ]
    gate = PhaseGateRecord(
        gate_id="gate-resume-test",
        job_id=job_id,
        gate_phase=gate_phase,
        stage_index=stage_index,
        gate_status="open",
        gate_decision="pending",
        source_artifact_checksum=gate_source_checksum,
        resolved_artifact_checksum=None,
        source_artifact_refs_json=json.dumps(refs, separators=(",", ":")),
        created_at=now,
    )
    SqliteUnitOfWork(conn).phase_gates.save(gate)
    SqliteUnitOfWork(conn).artifact_revisions.save(
        ArtifactRevisionRecord(
            revision_id="rev-accepted-test",
            job_id=job_id,
            stage_index=stage_index,
            revision_kind="stage_output",
            revision_status="accepted",
            revision_order=1,
            evidence_checksum=gate_source_checksum,
            prior_revision_checksum=None,
            artifact_refs_json=json.dumps(refs, separators=(",", ":")),
            prior_revision_id=None,
            superseded_by_revision_id=None,
            accepted_at_gate_id=gate.gate_id,
            created_at=now,
            created_by="test",
            accepted_at=now,
            accepted_by="test",
        )
    )
    # Save run configuration for route computation
    payload = {
        "source_profile": source_profile,
        "target_profile": target_profile,
    }
    conn.execute(
        """INSERT INTO run_configurations (
            run_configuration_id, job_id, schema_version,
            runner_profile_id, runner_profile_version,
            pipeline_id, pipeline_version, target_proof_level,
            enabled_gates_json, policy_json, payload_json,
            payload_checksum, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"rc-{job_id}",
            job_id,
            "1",
            "runner",
            "1",
            "pipeline",
            "1",
            "BUILD_TEST_VERIFIED",
            "[]",
            "{}",
            json.dumps(payload, separators=(",", ":")),
            f"checksum-{job_id}",
            utc_now_text(),
        ),
    )
    checkpoint_checksum = gate_checksum(
        gate_id=gate.gate_id,
        job_id=gate.job_id,
        gate_phase=gate.gate_phase,
        stage_index=gate.stage_index,
        source_artifact_checksum=gate.source_artifact_checksum,
        source_artifact_refs=refs,
    )
    return checkpoint_checksum


def _seed_stage_pipeline(conn: sqlite3.Connection, *, job_id: str = "job-1", seed_run_configuration: bool = True) -> None:
    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_setups.save(
            V2MigrationSetupRecord(
                setup_id="setup-1",
                run_name="stage-pipeline",
                legacy_app_path="C:/legacy",
                output_parent_path="C:/modernized",
                ai_hub_path=str(AI_HUB),
                java11_home="C:/jdk11",
                java17_home="C:/jdk17",
                java21_home="C:/jdk21",
                maven_cmd="C:/maven/bin/mvn.cmd",
                proof_level="build_test_verified",
                skip_endpoint_smoke=False,
                migration_flags_json="{}",
                setup_checksum="checksum-setup-1",
                checksum_algorithm="sha256",
                created_at=now,
                created_by="test",
                correlation_id=None,
            )
        )
        uow.v2_jobs.save(
            V2MigrationJobRecord(
                job_id=job_id,
                setup_id="setup-1",
                setup_checksum="checksum-setup-1",
                pipeline_id="springboot-216-to-356-java21-three-stage",
                stage_chain_json='[{"stage_index":1},{"stage_index":2},{"stage_index":3}]',
                status="running",
                created_at=now,
                updated_at=now,
                correlation_id=None,
            )
        )
        if seed_run_configuration:
            run_config_payload = {
                "source_profile": "springboot-2.7-java11",
                "target_profile": "springboot-4.0-java21",
            }
            uow.run_configurations.insert(
                RunConfigurationRecord(
                    run_configuration_id=f"rc-{job_id}",
                    job_id=job_id,
                    schema_version="1.0.0",
                    runner_profile_id="runner",
                    runner_profile_version="1",
                    pipeline_id="springboot-3.5-java17-to-java21-three-stage",
                    pipeline_version="1",
                    target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
                    enabled_gates_json="[]",
                    policy_json="{}",
                    payload_json=canonical_json_text(run_config_payload),
                    payload_checksum=sha256_canonical_json(run_config_payload),
                    created_at=now,
                )
            )
        uow.artifact_revisions.save(
            ArtifactRevisionRecord(
                revision_id="rev-stage3-accepted",
                job_id=job_id,
                stage_index=3,
                revision_kind="stage_output",
                revision_status="accepted",
                revision_order=1,
                evidence_checksum="ev-chk-3",
                prior_revision_checksum=None,
                artifact_refs_json='{}',
                prior_revision_id=None,
                superseded_by_revision_id=None,
                accepted_at_gate_id="gate-accepted-3",
                created_at=now,
                created_by="test",
                accepted_at=now,
                accepted_by="test",
            )
        )
    _save_command(conn, command_id="cmd-1", job_id=job_id)


def _wait_for_event(conn: sqlite3.Connection, job_id: str, event_type: str) -> None:
    deadline = time.monotonic() + 3
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            events = SqliteUnitOfWork(conn).v2_events.list_by_job(job_id)
        except (sqlite3.Error, IndexError) as exc:
            last_error = exc
            time.sleep(0.02)
            continue
        if any(event.type == event_type for event in events):
            return
        time.sleep(0.02)
    if last_error is not None:
        raise AssertionError(f"event {event_type!r} not persisted after transient read error: {last_error}") from last_error
    raise AssertionError(f"event {event_type!r} not persisted")

def _success_result(**overrides: Any) -> dict[str, Any]:
    result = {
        "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "orchestration_status": "PASS",
        "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "PASS_WITH_WARNINGS",
        "sandbox_path": "/tmp/sandbox",
    }
    result.update(overrides)
    return result


def _reviewed_phase_result(*, phase: str = "analysis", decision: str = "accept", **overrides: Any) -> dict[str, Any]:
    """Synthetic reviewed result used to test runner-side validation only.

    The production Analysis/Planning producers do not yet create this shape.
    These tests prove the runner fails closed or opens gates when such a
    reviewed result is supplied.
    """
    det = f"det-{phase}-checksum"
    pri = f"primary-{phase}-checksum"
    rev = f"reviewer-{phase}-checksum"
    final = f"final-{phase}-checksum"
    result: dict[str, Any] = {
        "job_id": "job-1",
        "orchestration_status": "PASS",
        "sandbox_path": "/tmp/sandbox",
        "artifact_refs": {
            "deterministic_artifact": f".migration/{phase}/deterministic.json",
            "primary_llm_output": f".migration/{phase}/primary.json",
            "reviewer_llm_output": f".migration/{phase}/reviewer.json",
            "final_reviewed_markdown": f".migration/{phase}/final-reviewed.md",
        },
        "review_chain": {
            "job_id": "job-1",
            "deterministic_artifact_ref": f".migration/{phase}/deterministic.json",
            "deterministic_artifact_checksum": det,
            "primary_input_checksum": f"primary-input-{phase}",
            "primary_output_ref": f".migration/{phase}/primary.json",
            "primary_output_checksum": pri,
            "reviewer_input_checksum": f"reviewer-input-{phase}",
            "reviewer_output_checksum": rev,
            "reviewer_decision": decision,
            "review_confidence": 0.92,
            "reviewed_artifact_checksum": det,
            "reviewed_primary_output_checksum": pri,
            "final_markdown_ref": f".migration/{phase}/final-reviewed.md",
            "final_markdown_checksum": final,
            "reviewer_notes": ["grounded in deterministic artifact"],
        },
    }
    result.update(overrides)
    return result


def _run_success_chain(tmp_path: Path, conn: sqlite3.Connection) -> tuple[_SequentialFakePopen, list[Any]]:
    _seed_stage_pipeline(conn)
    popen = _SequentialFakePopen([
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-1")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-2")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-3")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-4")) + "\n"], [], 0),
    ])
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )
    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_stage4_command(conn)
    events = list(SqliteUnitOfWork(conn).v2_events.list_by_job("job-1"))
    return popen, events


def _wait_for_stage4_command(conn: sqlite3.Connection, job_id: str = "job-1") -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        cmds = SqliteUnitOfWork(conn).v2_commands.list_by_job_and_stage(job_id, 4)
        if cmds:
            return
        time.sleep(0.05)
    raise AssertionError("Stage 4 command not persisted")


def _wait_for_popen_call_containing(popen: _SequentialFakePopen, text: str) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if any(text in " ".join(call["argv"]) for call in popen.calls):
            return
        time.sleep(0.05)
    raise AssertionError(f"process launch containing {text!r} not observed")


def test_v2_runner_launches_manifest_with_shell_false_and_safe_env(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    popen = _FakePopen(
        stdout=[
            'CONTROL_TOWER_EVENT {"phase":"analysis","status":"running","message":"analysis started"}\n',
            json.dumps(_success_result(
                artifact_refs={"analysis_report": "C:/out/.migration/runs/run-1/analysis/report.json"},
                sandbox_path="C:/out/sandbox",
            )) + "\n",
        ],
        stderr=["warning from runner\n"],
        exit_code=0,
    )
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_completed")

    assert popen.calls
    call = popen.calls[0]
    assert call["shell"] is False
    assert call["cwd"] == str(tmp_path)
    assert "MAVEN_CMD" in call["env"]
    assert "AZURE_OPENAI_API_KEY" not in call["env"]

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "analysis_started" in event_types
    assert "stderr" in event_types
    assert "artifact_written" in event_types
    assert "proof_updated" in event_types
    assert "stage_completed" in event_types


def test_v2_runner_maps_failure_to_stage_failed(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=["boom\n"], exit_code=2),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    failed = [event for event in events if event.type == "stage_failed"][-1]
    assert failed.status == "failed"
    assert "code 2" in failed.message


def test_v2_runner_maps_approval_interrupt_to_card_and_blocked_events(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    result = {
        "status": "human_approval_required",
        "run_id": "run-1",
        "summary": {"analysis_status": "PASS"},
        "artifact_refs": {
            "analysis_report": "C:/out/.migration/runs/run-1/analysis/report.json",
            "dependency_graph": "C:/out/.migration/runs/run-1/analysis/dependency-graph.json",
            "config_inventory": "C:/out/.migration/runs/run-1/analysis/config-inventory.json",
            "test_inventory": "C:/out/.migration/runs/run-1/analysis/test-inventory.json",
            "migration_plan.yaml": "C:/out/.migration/runs/run-1/planning/migration-plan.yaml",
            "migration_units.yaml": "C:/out/.migration/runs/run-1/planning/migration-units.yaml",
            "assessment_report": "C:/out/.migration/runs/run-1/assessment/report.json",
            "approval_request.json": "C:/out/.migration/runs/run-1/planning/approval-request.json",
        },
        "decision_options": ["approved", "rejected", "replan_required"],
    }
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_blocked_for_approval")

    uow = SqliteUnitOfWork(conn)
    events = uow.v2_events.list_by_job("job-1")
    assert "approval_required" in [event.type for event in events]
    cards = uow.v2_approvals.list_cards_by_status("pending")
    assert len(cards) == 1
    assert cards[0].job_id == "job-1"
    assert cards[0].request_checksum
    assert len(uow.v2_approvals.list_cards_by_job("job-1")) == 1

    gates = uow.phase_gates.list_open("job-1")
    assert len(gates) == 1
    assert gates[0].gate_phase == "approval_review"
    assert gates[0].gate_status == "open"
    assert "analysis/report.json" in gates[0].source_artifact_refs_json
    assert "approval-request.json" in gates[0].source_artifact_refs_json

    # Replaying the same approval-required result must reuse the gate
    # and approval card instead of duplicating them.
    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_blocked_for_approval")
    uow2 = SqliteUnitOfWork(conn)
    assert len(uow2.phase_gates.list_open("job-1")) == 1
    assert len(uow2.v2_approvals.list_cards_by_status("pending")) == 1


def test_v2_runner_auto_approves_human_approval_when_enabled(tmp_path: Path) -> None:
    """Auto Approval ON before gate creation should auto-approve immediately.

    This test does NOT seed accepted analysis/planning revisions because the
    orchestrator auto-approval path uses approve_from_gate (same as the manual
    Approve button and `confirm checksum`), which does NOT require accepted
    revision records.  The UI shows PASS based on events, not revision records.
    """
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn)
    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_jobs.set_auto_approval_enabled(
            "job-1",
            True,
            updated_at=now,
            updated_by="test",
        )

    approval_required = {
        "status": "human_approval_required",
        "run_id": "run-1",
        "summary": {"analysis_status": "PASS"},
        "artifact_refs": {
            "analysis_report": "C:/out/.migration/runs/run-1/analysis/report.json",
            "migration_plan.yaml": "C:/out/.migration/runs/run-1/planning/migration-plan.yaml",
            "assessment_report": "C:/out/.migration/runs/run-1/assessment/report.json",
            "approval_request.json": "C:/out/.migration/runs/run-1/planning/approval-request.json",
        },
        "decision_options": ["approved", "rejected", "replan_required"],
    }
    popen = _SequentialFakePopen([
        ([json.dumps(approval_required) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/sandbox/s1")) + "\n"], [], 0),
    ])
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "approval_auto_approved")
    _wait_for_event(conn, "job-1", "resume_started")
    _wait_for_event(conn, "job-1", "stage_completed")

    uow = SqliteUnitOfWork(conn)
    cards = uow.v2_approvals.list_cards_by_job("job-1")
    assert len(cards) == 1
    assert cards[0].status == "auto_approved"
    decisions = uow.gate_decisions.list_by_job("job-1")
    assert len(decisions) == 1
    assert decisions[0].actor_type == "system"
    assert decisions[0].decided_by == "system:auto-approval"
    event_types = [event.type for event in uow.v2_events.list_by_job("job-1")]
    assert "approval_required" not in event_types
    assert "stage_blocked_for_approval" not in event_types
    assert len(popen.calls) == 2


def test_v2_runner_auto_approval_persists_across_multiple_stages(tmp_path: Path) -> None:
    """Backend Test 3: Auto Approval stays ON and auto-approves gates in every stage.

    The orchestrator reads auto_approval_enabled from the DB at every gate
    creation, so the flag persists across stages without re-toggling.
    This test verifies the flag is read fresh at gate creation time by
    running two separate stages and confirming both are auto-approved.
    """
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn)
    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_jobs.set_auto_approval_enabled(
            "job-1", True, updated_at=now, updated_by="test",
        )

    approval_required = {
        "status": "human_approval_required",
        "run_id": "run-stage1",
        "summary": {"analysis_status": "PASS"},
        "artifact_refs": {
            "analysis_report": "C:/out/.migration/runs/run-stage1/analysis/report.json",
            "migration_plan.yaml": "C:/out/.migration/runs/run-stage1/planning/migration-plan.yaml",
            "assessment_report": "C:/out/.migration/runs/run-stage1/assessment/report.json",
            "approval_request.json": "C:/out/.migration/runs/run-stage1/planning/approval-request.json",
        },
        "decision_options": ["approved", "rejected", "replan_required"],
    }

    # Stage 1: approval required -> auto-approved -> resume -> success
    popen1 = _SequentialFakePopen([
        ([json.dumps(approval_required) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/sandbox/s1")) + "\n"], [], 0),
    ])
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen1,
        cwd=tmp_path,
    )
    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "approval_auto_approved")
    _wait_for_event(conn, "job-1", "stage_completed")

    # The flag must still be ON after the first auto-approval.
    assert SqliteUnitOfWork(conn).v2_jobs.get_auto_approval_enabled("job-1") is True

    # Stage 2: a new approval gate is created and must also be auto-approved.
    # Use a different run_id AND stage_index so the gate checksum and revision
    # lookup differ from stage 1.  In a real multi-stage migration each stage
    # has a different stage_index, so there is no revision conflict.
    approval_required_stage2 = {
        **approval_required,
        "run_id": "run-stage2",
        "artifact_refs": {
            "analysis_report": "C:/out/.migration/runs/run-stage2/analysis/report.json",
            "migration_plan.yaml": "C:/out/.migration/runs/run-stage2/planning/migration-plan.yaml",
            "assessment_report": "C:/out/.migration/runs/run-stage2/assessment/report.json",
            "approval_request.json": "C:/out/.migration/runs/run-stage2/planning/approval-request.json",
        },
    }
    now = utc_now_text()
    SqliteUnitOfWork(conn).v2_commands.save(
        V2StageCommandRecord(
            command_id="cmd-2",
            job_id="job-1",
            stage_index=2,
            manifest_checksum="checksum-2",
            argv_json=json.dumps(["python", "-m", "migration_factory.orchestrator.runner", "--run-id", "run-stage2"]),
            env_json=json.dumps({
                "JAVA_HOME": "C:/jdk17",
                "JAVA11_HOME": "C:/jdk11",
                "JAVA17_HOME": "C:/jdk17",
                "JAVA21_HOME": "C:/jdk21",
                "MAVEN_CMD": "C:/maven/bin/mvn.cmd",
                "PATH_PREPEND": "C:/jdk17/bin",
            }),
            status="manifest_ready",
            created_at=now,
            updated_at=now,
            result_json=None,
        )
    )
    popen2 = _SequentialFakePopen([
        ([json.dumps(approval_required_stage2) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/sandbox/s2")) + "\n"], [], 0),
    ])
    runner2 = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen2,
        cwd=tmp_path,
    )
    runner2.start(job_id="job-1", command_id="cmd-2")

    # Wait for the SECOND approval_auto_approved event (stage 2).
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
        except (sqlite3.Error, IndexError):
            time.sleep(0.05)
            continue
        auto_count = sum(1 for e in events if e.type == "approval_auto_approved")
        if auto_count >= 2:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("second approval_auto_approved event not emitted")

    uow = SqliteUnitOfWork(conn)
    events = uow.v2_events.list_by_job("job-1")
    auto_events = [event for event in events if event.type == "approval_auto_approved"]
    assert len(auto_events) >= 2
    blocked_events = [event for event in events if event.type == "stage_blocked_for_approval"]
    assert blocked_events == []
    assert SqliteUnitOfWork(conn).v2_jobs.get_auto_approval_enabled("job-1") is True


def test_v2_runner_auto_approval_off_returns_to_manual_mode(tmp_path: Path) -> None:
    """Backend Test 4: turning Auto Approval OFF makes the next gate manual."""
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn)
    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_jobs.set_auto_approval_enabled(
            "job-1", False, updated_at=now, updated_by="test",
        )

    approval_required = {
        "status": "human_approval_required",
        "run_id": "run-1",
        "summary": {"analysis_status": "PASS"},
        "artifact_refs": {
            "analysis_report": "C:/out/.migration/runs/run-1/analysis/report.json",
            "migration_plan.yaml": "C:/out/.migration/runs/run-1/planning/migration-plan.yaml",
            "assessment_report": "C:/out/.migration/runs/run-1/assessment/report.json",
            "approval_request.json": "C:/out/.migration/runs/run-1/planning/approval-request.json",
        },
        "decision_options": ["approved", "rejected", "replan_required"],
    }
    popen = _SequentialFakePopen([
        ([json.dumps(approval_required) + "\n"], [], 0),
    ])
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_blocked_for_approval")

    uow = SqliteUnitOfWork(conn)
    event_types = [event.type for event in uow.v2_events.list_by_job("job-1")]
    assert "approval_required" in event_types
    assert "stage_blocked_for_approval" in event_types
    assert "approval_auto_approved" not in event_types
    cards = uow.v2_approvals.list_cards_by_job("job-1")
    assert len(cards) == 1
    assert cards[0].status == "pending"


def test_v2_runner_does_not_forward_copilot_env_to_product_subprocess(monkeypatch, tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    monkeypatch.setenv("AI_MIGRATION_COPILOT_PROVIDER", "copilot_cli")
    monkeypatch.setenv("AI_MIGRATION_COPILOT_MODEL", "gpt-test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    popen = _FakePopen(stdout=[json.dumps(_success_result(sandbox_path="/tmp/sandbox/s1")) + "\n"], stderr=[], exit_code=0)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_completed")

    env = popen.calls[0]["env"]
    assert "AI_MIGRATION_COPILOT_PROVIDER" not in env
    assert "AI_MIGRATION_COPILOT_MODEL" not in env
    assert "AZURE_OPENAI_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    event_types = [event.type for event in SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")]
    assert "copilot_status_checked" not in event_types


def test_phase_argv_uses_route_aware_profile_for_analysis_and_planning(tmp_path: Path) -> None:
    db_path = tmp_path / "phase_argv.sqlite3"
    seed_conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    seed_conn.row_factory = sqlite3.Row
    apply_pending_migrations(seed_conn)
    _seed_stage_pipeline(seed_conn, seed_run_configuration=False)
    _insert_run_config(
        seed_conn,
        job_id="job-1",
        rc_id="rc-1",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
        policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}),
    )
    _save_phase_command(seed_conn, command_id="cmd-analysis", job_id="job-1", stage_index=1, phase="analysis")
    _save_phase_command(seed_conn, command_id="cmd-planning", job_id="job-1", stage_index=2, phase="planning")
    seed_conn.close()

    analysis_conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    analysis_conn.row_factory = sqlite3.Row
    planning_conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    planning_conn.row_factory = sqlite3.Row
    popen = _SequentialFakePopen([
        ([json.dumps(_success_result(sandbox_path="/tmp/analysis")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/planning")) + "\n"], [], 0),
    ])
    analysis_runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(analysis_conn),
        popen_factory=popen,
        cwd=tmp_path,
    )
    planning_runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(planning_conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    analysis_runner.start(job_id="job-1", command_id="cmd-analysis")
    planning_runner.start(job_id="job-1", command_id="cmd-planning")
    _wait_for_popen_call_containing(popen, "--phase analysis")
    _wait_for_popen_call_containing(popen, "--phase planning")

    assert len(popen.calls) == 2
    assert all("--profile" in call["argv"] for call in popen.calls)
    assert all("springboot-3.5-java17-to-java21" in call["argv"] for call in popen.calls)
    assert all("springboot-2.1.6-to-2.7-java11" not in " ".join(call["argv"]) for call in popen.calls)


def test_phase_argv_no_longer_hardcodes_springboot_2_1_6_to_2_7_java11(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(
        conn,
        job_id="job-1",
        rc_id="rc-legacy-check",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
        policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}),
    )
    _save_phase_command(conn, command_id="cmd-analysis", job_id="job-1", stage_index=1, phase="analysis")
    popen = _FakePopen(stdout=[json.dumps(_success_result(sandbox_path="/tmp/analysis")) + "\n"], stderr=[], exit_code=0)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-analysis")
    _wait_for_popen_call_containing(popen, "springboot-3.5-java17-to-java21")
    assert "springboot-2.1.6-to-2.7-java11" not in " ".join(popen.calls[0]["argv"])


def test_route_profile_resolution_failure_emits_blocked_stage_event(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    now = utc_now_text()
    missing_ai_hub = tmp_path / "missing-ai-hub"
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_setups.save(
            V2MigrationSetupRecord(
                setup_id="setup-route-fail",
                run_name="route-fail",
                legacy_app_path="C:/legacy",
                output_parent_path="C:/modernized",
                ai_hub_path=str(missing_ai_hub),
                java11_home="C:/jdk11",
                java17_home="C:/jdk17",
                java21_home="C:/jdk21",
                maven_cmd="C:/maven/bin/mvn.cmd",
                proof_level="build_test_verified",
                skip_endpoint_smoke=True,
                migration_flags_json="{}",
                setup_checksum="checksum-setup-route-fail",
                checksum_algorithm="sha256",
                created_at=now,
                created_by="test",
                correlation_id=None,
            )
        )
        uow.v2_jobs.save(
            V2MigrationJobRecord(
                job_id="job-route-fail",
                setup_id="setup-route-fail",
                setup_checksum="checksum-setup-route-fail",
                pipeline_id="pipeline-route-fail",
                stage_chain_json='[{"stage_index":1}]',
                status="running",
                created_at=now,
                updated_at=now,
                correlation_id=None,
            )
        )
    _insert_run_config(
        conn,
        job_id="job-route-fail",
        rc_id="rc-route-fail",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
        policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}),
    )
    _save_phase_command(conn, command_id="cmd-route-fail", job_id="job-route-fail", stage_index=1, phase="analysis")
    popen = _FakePopen(stdout=[], stderr=[], exit_code=0)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    with pytest.raises(RouteRuntimeProfileUnavailableError):
        runner.start(job_id="job-route-fail", command_id="cmd-route-fail")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-route-fail")
    stage_failed = [event for event in events if event.type == "stage_failed"][-1]
    assert stage_failed.status == "blocked"
    assert stage_failed.message == "ROUTE_RUNTIME_PROFILE_UNAVAILABLE"
    assert missing_ai_hub.as_posix() not in stage_failed.message
    payload = json.loads(stage_failed.payload_json or "{}")
    assert missing_ai_hub.as_posix() not in json.dumps(payload)
    assert len(popen.calls) == 0


def test_v2_runner_emits_failure_repair_events_from_result(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    run_dir = tmp_path / "out" / ".migration" / "runs" / "run-1"
    result = {
        "run_id": "run-1",
        "run_dir": str(run_dir),
        "final_status": "FALLBACK_REPAIR_PLAN",
        "build_status": "BUILD_FAILED_IN_SANDBOX",
        "final_proof_level": "not_verified",
        "repair_loop_status": "FALLBACK_REPAIR_PLAN",
        "repair_fallback_generated": True,
        "failure_summary": "Compilation failed",
        "changed_files": ["pom.xml"],
        "source_profile": "springboot-2.7-java11",
        "target_profile": "springboot-3.5-java17",
        "sandbox_path": "/tmp/sandbox",
        "artifact_refs": {"analysis_report": "C:/out/.migration/runs/run-1/report.json"},
    }
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    # Wait for stage_failed (the last event in the chain) to give the runner thread
    # time to finish writing all diagnostic events before we read them.
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "build_failed" in event_types
    assert "repair_failure_evidence_written" in event_types
    assert "repair_context_pack_written" in event_types
    assert "transform_failed" in event_types
    assert "repair_started" in event_types
    assert "repair_fallback_generated" in event_types
    assert "stage_failed" in event_types
    assert "copilot_repair_invalid_response" not in event_types
    evidence_path = run_dir / "repairs" / "repair_failure_evidence.json"
    context_path = run_dir / "repairs" / "repair_context_pack.json"
    assert evidence_path.is_file()
    assert context_path.is_file()
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert evidence["failure_source"] == "build"
    assert evidence["command_id"] == "cmd-1"
    assert evidence["content_checksum"]
    assert context["failure_evidence_checksum"] == evidence["content_checksum"]
    assert context["context_pack_checksum"]
    event_payloads = {event.type: json.loads(event.payload_json) for event in events}
    assert event_payloads["repair_failure_evidence_written"]["failure_evidence_checksum"] == evidence["content_checksum"]
    assert event_payloads["repair_context_pack_written"]["context_pack_checksum"] == context["context_pack_checksum"]


def test_analysis_reviewed_result_validation_requires_final_reviewed_markdown(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner._handle_exit(
        job_id="job-1",
        stage_index=1,
        command_id="cmd-1",
        exit_code=0,
        result=_reviewed_phase_result(phase="analysis"),
        stderr="",
        command_phase="analysis",
    )

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "analysis_review_required" in event_types
    assert "reviewer_failed" not in event_types
    gate = SqliteUnitOfWork(conn).phase_gates.list_open("job-1")[0]
    assert gate.gate_phase == "analysis_review"
    assert "final-reviewed.md" in gate.source_artifact_refs_json


def test_planning_reviewed_result_validation_requires_final_reviewed_markdown(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner._handle_exit(
        job_id="job-1",
        stage_index=2,
        command_id="cmd-1",
        exit_code=0,
        result=_reviewed_phase_result(phase="planning"),
        stderr="",
        command_phase="planning",
    )

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "planning_review_required" in event_types
    assert "reviewer_failed" not in event_types
    gate = SqliteUnitOfWork(conn).phase_gates.list_open("job-1")[0]
    assert gate.gate_phase == "planning_review"
    assert "final-reviewed.md" in gate.source_artifact_refs_json


def test_missing_reviewer_fails_closed_for_phase_checkpoint(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    result = _reviewed_phase_result(phase="analysis")
    result.pop("review_chain")

    runner._handle_exit(
        job_id="job-1",
        stage_index=1,
        command_id="cmd-1",
        exit_code=0,
        result=result,
        stderr="",
        command_phase="analysis",
    )

    event_types = [event.type for event in SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")]
    assert "reviewer_failed" in event_types
    assert "analysis_review_required" not in event_types


def test_rejected_or_revision_reviewed_result_blocks_checkpoint(tmp_path: Path) -> None:
    for decision in ("reject", "request_revision"):
        case_dir = tmp_path / decision
        case_dir.mkdir()
        conn = _conn(case_dir)
        runner = V2OrchestratorRunner(
            unit_of_work_factory=lambda conn=conn: SqliteUnitOfWork(conn),
            popen_factory=_FakePopen(stdout=[], stderr=[], exit_code=0),
            cwd=tmp_path,
        )

        runner._handle_exit(
            job_id="job-1",
            stage_index=1,
            command_id="cmd-1",
            exit_code=0,
            result=_reviewed_phase_result(phase="analysis", decision=decision),
            stderr="",
            command_phase="analysis",
        )

        event_types = [event.type for event in SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")]
        assert "reviewer_failed" in event_types
        assert "analysis_review_required" not in event_types


def test_stale_or_checksum_mismatched_reviewer_fails_closed(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    result = _reviewed_phase_result(phase="planning")
    result["review_chain"]["reviewed_primary_output_checksum"] = "stale-primary"

    runner._handle_exit(
        job_id="job-1",
        stage_index=2,
        command_id="cmd-1",
        exit_code=0,
        result=result,
        stderr="",
        command_phase="planning",
    )

    event_types = [event.type for event in SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")]
    assert "reviewer_failed" in event_types
    assert "planning_review_required" not in event_types


def test_raw_primary_output_is_not_downstream_checkpoint_input(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    result = _reviewed_phase_result(phase="analysis")
    result["artifact_refs"].pop("final_reviewed_markdown")

    runner._handle_exit(
        job_id="job-1",
        stage_index=1,
        command_id="cmd-1",
        exit_code=0,
        result=result,
        stderr="",
        command_phase="analysis",
    )

    event_types = [event.type for event in SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")]
    assert "reviewer_failed" in event_types
    assert "analysis_review_required" not in event_types


def test_v2_runner_does_not_auto_queue_next_stage_on_failure(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    result = {
        "final_status": "BUILD_FAILED_IN_SANDBOX",
        "build_status": "BUILD_FAILED_IN_SANDBOX",
        "sandbox_path": "/tmp/sandbox",
    }
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "build_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    # Must not have next_stage_queued on failure
    assert "next_stage_queued" not in event_types


def test_v2_runner_does_not_auto_queue_on_test_failure(tmp_path: Path) -> None:
    """Stage with TEST_FAILED must emit stage_failed, not stage_completed."""
    conn = _conn(tmp_path)
    _save_command(conn)
    result = {
        "final_status": "TEST_FAILED",
        "test_status": "TEST_FAILED",
        "sandbox_path": "/tmp/sandbox",
    }
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "next_stage_queued" not in event_types
    assert "stage_completed" not in event_types


def test_v2_runner_does_not_auto_queue_on_transform_failure(tmp_path: Path) -> None:
    """Stage with TRANSFORM_FAILED must not auto-progress."""
    conn = _conn(tmp_path)
    _save_command(conn)
    result = {
        "final_status": "TRANSFORM_FAILED",
        "transform_status": "TRANSFORM_FAILED",
        "sandbox_path": "/tmp/sandbox",
    }
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "next_stage_queued" not in event_types
    assert "stage_completed" not in event_types


def test_v2_runner_does_not_auto_queue_without_sandbox(tmp_path: Path) -> None:
    """Stage 1 with DONE but no sandbox_path must emit stage_failed."""
    conn = _conn(tmp_path)
    _save_command(conn)
    result = {"final_status": "DONE"}  # no sandbox_path
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "next_stage_queued" not in event_types
    assert "stage_completed" not in event_types


def test_v2_runner_proof_gate_blocks_missing_orchestration_status(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    result = _success_result()
    result.pop("orchestration_status")
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "stage_completed" not in event_types
    assert "next_stage_queued" not in event_types
    failed = [event for event in events if event.type == "stage_failed"][-1]
    payload = json.loads(failed.payload_json or "{}")
    assert failed.message == "Stage 1 did not produce strict success proof: expected orchestration_status=PASS, detected=missing."
    assert payload["proof_failure_field"] == "orchestration_status"
    assert payload["proof_expected"] == "PASS"
    assert payload["proof_detected"] == "missing"
    assert payload["proof_expected_values"]["orchestration_status"] == "PASS"
    assert payload["proof_detected_values"]["orchestration_status"] == ""


def test_v2_runner_proof_gate_blocks_missing_build_status(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    result = _success_result()
    result.pop("build_status")
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "stage_completed" not in event_types
    assert "next_stage_queued" not in event_types
    failed = [event for event in events if event.type == "stage_failed"][-1]
    payload = json.loads(failed.payload_json or "{}")
    assert failed.message == "Stage 1 did not produce strict success proof: expected build_status=BUILD_PASSED_IN_SANDBOX, detected=missing."
    assert payload["proof_failure_field"] == "build_status"
    assert payload["proof_expected"] == "BUILD_PASSED_IN_SANDBOX"
    assert payload["proof_detected"] == "missing"


def test_v2_runner_proof_gate_blocks_missing_transform_or_test_status(tmp_path: Path) -> None:
    for missing_field in ("transform_status", "test_status"):
        case_dir = tmp_path / missing_field
        case_dir.mkdir()
        conn = _conn(case_dir)
        _save_command(conn)
        result = _success_result()
        result.pop(missing_field)
        runner = V2OrchestratorRunner(
            unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
            popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
            cwd=case_dir,
        )

        runner.start(job_id="job-1", command_id="cmd-1")
        _wait_for_event(conn, "job-1", "stage_failed")

        events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
        event_types = [event.type for event in events]
        assert "stage_completed" not in event_types
        assert "next_stage_queued" not in event_types
        failed = [event for event in events if event.type == "stage_failed"][-1]
        payload = json.loads(failed.payload_json or "{}")
        assert failed.message.startswith("Stage 1 did not produce strict success proof:")
        assert payload["proof_failure_field"] == missing_field
        assert payload["proof_detected"] == "missing"


def test_v2_runner_proof_gate_blocks_errors_and_blockers(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    result = _success_result(errors=["unexpected warning promoted to error"])
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "stage_completed" not in event_types
    assert "next_stage_queued" not in event_types


def test_success_proof_accepts_orchestration_status_pass_for_all_stages() -> None:
    from migration_factory.control_tower.application.v2_orchestrator_runner import _has_success_proof

    for sandbox_path in ("/tmp/stage-1", "/tmp/stage-2", "/tmp/stage-3"):
        ok, details = _has_success_proof(_success_result(sandbox_path=sandbox_path))
        assert ok is True
        assert details["detected_values"]["orchestration_status"] == "PASS"
        assert details["detected_values"]["sandbox_path"] == sandbox_path


def test_success_proof_rejects_successful_token_if_pass_missing_or_contract_mismatch() -> None:
    from migration_factory.control_tower.application.v2_orchestrator_runner import _has_success_proof

    success_token_result = _success_result(orchestration_status="successful")
    ok, details = _has_success_proof(success_token_result)
    assert ok is False
    assert details["field"] == "orchestration_status"
    assert details["expected"] == "PASS"
    assert details["detected"] == "successful"

    mismatch_result = _success_result(final_status="DONE")
    ok, details = _has_success_proof(mismatch_result)
    assert ok is False
    assert details["field"] == "final_status"
    assert details["expected"] == "TRANSFORM_APPLIED_IN_SANDBOX"
    assert details["detected"] == "DONE"


def test_success_proof_rejects_non_pass_with_detected_expected_details(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    result = _success_result(orchestration_status="FAIL")
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    failed = [event for event in events if event.type == "stage_failed"][-1]
    payload = json.loads(failed.payload_json or "{}")
    assert failed.message == "Stage 1 did not produce strict success proof: expected PASS, detected=FAIL."
    assert payload["proof_failure_field"] == "orchestration_status"
    assert payload["proof_expected"] == "PASS"
    assert payload["proof_detected"] == "FAIL"
    assert payload["proof_expected_values"]["orchestration_status"] == "PASS"
    assert payload["proof_detected_values"]["orchestration_status"] == "FAIL"


def test_stage_failed_not_emitted_for_valid_pass_contract(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    popen, events = _run_success_chain(tmp_path, conn)

    event_types = [event.type for event in events]
    assert "stage_failed" not in event_types
    assert "next_stage_queued" in event_types


def test_stage1_completion_replay_does_not_duplicate_stage2_command(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    launched: list[str] = []
    runner.start = lambda *, job_id, command_id: launched.append(command_id)  # type: ignore[method-assign]

    result = _success_result(sandbox_path="/tmp/stage-1")
    runner._handle_exit(
        job_id="job-1",
        stage_index=1,
        command_id="cmd-1",
        exit_code=0,
        result=result,
        stderr="",
        command_phase=None,
    )
    runner._handle_exit(
        job_id="job-1",
        stage_index=1,
        command_id="cmd-1",
        exit_code=0,
        result=result,
        stderr="",
        command_phase=None,
    )

    stage2_commands = SqliteUnitOfWork(conn).v2_commands.list_by_job_and_stage("job-1", 2)
    assert len(stage2_commands) == 1
    assert len(launched) >= 1


def test_v2_runner_emits_stage_completed_for_stage3(tmp_path: Path) -> None:
    """Stage 3 completion emits stage_completed event."""
    conn = _conn(tmp_path)
    # Save a Stage 3 command directly (not via _save_command which defaults to stage_index=1)
    now = utc_now_text()
    SqliteUnitOfWork(conn).v2_commands.save(
        V2StageCommandRecord(
            command_id="cmd-s3",
            job_id="job-1",
            stage_index=3,
            manifest_checksum="checksum",
            argv_json=json.dumps(["python", "-m", "migration_factory.orchestrator.runner"]),
            env_json=json.dumps({"JAVA_HOME": "C:/jdk21", "JAVA11_HOME": "C:/jdk11", "JAVA17_HOME": "C:/jdk17", "JAVA21_HOME": "C:/jdk21", "MAVEN_CMD": "C:/maven/bin/mvn.cmd"}),
            status="manifest_ready",
            created_at=now,
            updated_at=now,
            result_json=None,
        )
    )
    result = _success_result(sandbox_path="/tmp/sandbox/s3")
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    runner.start(job_id="job-1", command_id="cmd-s3")
    _wait_for_event(conn, "job-1", "stage_completed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "stage_completed" in event_types
    assert "stage_failed" not in event_types


def test_stage1_pass_contract_with_pass_with_warnings_auto_queues_stage2(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn)
    popen = _SequentialFakePopen([
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-1")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-2")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-3")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-4")) + "\n"], [], 0),
    ])
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_stage4_command(conn)

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "next_stage_queued" in event_types
    assert any(event.type == "next_stage_queued" and json.loads(event.payload_json or "{}").get("to_stage") == 2 for event in events)
    assert "stage_failed" not in event_types
    assert any(event.type == "stage_completed" and event.stage == 1 for event in events)
    assert any(event.type == "stage_started" and event.stage == 2 for event in events)


def test_stage2_pass_contract_with_pass_with_warnings_auto_queues_stage3(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn)
    popen = _SequentialFakePopen([
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-1")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-2")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-3")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-4")) + "\n"], [], 0),
    ])
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_stage4_command(conn)

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    assert any(event.type == "next_stage_queued" and json.loads(event.payload_json or "{}").get("to_stage") == 3 for event in events)
    assert any(event.type == "stage_completed" and event.stage == 2 for event in events)
    assert any(event.type == "stage_started" and event.stage == 3 for event in events)
    assert "stage_failed" not in [event.type for event in events]


def test_stage3_pass_contract_queues_stage4(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn)
    popen = _SequentialFakePopen([
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-1")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-2")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-3")) + "\n"], [], 0),
        ([json.dumps(_success_result(sandbox_path="/tmp/stage-4")) + "\n"], [], 0),
    ])
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_stage4_command(conn)
    _wait_for_popen_call_containing(popen, "v2-job-1-s4")

    commands = SqliteUnitOfWork(conn).v2_commands.list_by_job("job-1")
    stage4_commands = [command for command in commands if command.stage_index == 4]
    assert len(stage4_commands) == 1
    assert "v2-job-1-s4" in stage4_commands[0].argv_json
    assert "springboot-3.5-java21-to-4.0-java21" in stage4_commands[0].argv_json
    assert any("v2-job-1-s4" in " ".join(call["argv"]) for call in popen.calls)
    assert any("springboot-3.5-java21-to-4.0-java21" in " ".join(call["argv"]) for call in popen.calls)
    assert any("/tmp/stage-3" in " ".join(call["argv"]) for call in popen.calls)
    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "stage_failed" not in event_types
    assert "next_stage_queued" in event_types
    assert any(json.loads(event.payload_json or "{}").get("to_stage") == 4 for event in events if event.type == "next_stage_queued")
    assert not any(json.loads(event.payload_json or "{}").get("to_stage") == 5 for event in events if event.type == "next_stage_queued")


def test_v2_runner_does_not_progress_past_unapproved_card(tmp_path: Path) -> None:
    """Stage with a pending approval card must not auto-progress."""
    conn = _conn(tmp_path)
    _save_command(conn)
    now = utc_now_text()
    SqliteUnitOfWork(conn).v2_approvals.save_card(
        V2ApprovalDecisionRecord(
            card_id="card-pending",
            job_id="job-1",
            interrupt_id="run-1",
            request_checksum="chk",
            stage_index=1,
            summary="pending approval",
            status="pending",
            created_at=now,
        )
    )
    result = _success_result()
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_blocked_for_approval")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "next_stage_queued" not in event_types
    assert "stage_completed" not in event_types


def test_v2_runner_emits_approval_started_on_resume(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _save_command(conn)
    checkpoint_checksum = _seed_resume_gate(conn)
    now = utc_now_text()
    SqliteUnitOfWork(conn).v2_approvals.save_card(
        V2ApprovalDecisionRecord(
            card_id="card-1",
            job_id="job-1",
            interrupt_id="run-1",
            request_checksum=checkpoint_checksum,
            stage_index=1,
            summary="test",
            status="approved",
            created_at=now,
        )
    )
    SqliteUnitOfWork(conn).v2_approvals.save_resume(
        V2ResumeCommandRecord(
            resume_id="resume-1",
            card_id="card-1",
            decision="approved",
            job_id="job-1",
            stage_index=1,
            command_json=json.dumps(["python", "-m", "resume"]),
            created_at=now,
        )
    )
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps({"final_status": "DONE"}) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start_resume(job_id="job-1", resume_id="resume-1")
    _wait_for_event(conn, "job-1", "resume_started")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "approval_started" in event_types
    assert "resume_started" in event_types


def test_v2_runner_resume_passes_env_manifest_from_original_command(tmp_path: Path) -> None:
    """Resume must inherit env manifest (JAVA_HOME, etc.) from the original stage command."""
    conn = _conn(tmp_path)
    _save_command(conn)
    checkpoint_checksum = _seed_resume_gate(conn)
    now = utc_now_text()
    SqliteUnitOfWork(conn).v2_approvals.save_card(
        V2ApprovalDecisionRecord(
            card_id="card-1",
            job_id="job-1",
            interrupt_id="run-1",
            request_checksum=checkpoint_checksum,
            stage_index=1,
            summary="test",
            status="approved",
            created_at=now,
        )
    )
    SqliteUnitOfWork(conn).v2_approvals.save_resume(
        V2ResumeCommandRecord(
            resume_id="resume-1",
            card_id="card-1",
            decision="approved",
            job_id="job-1",
            stage_index=1,
            command_json=json.dumps(["python", "-m", "resume"]),
            created_at=now,
        )
    )
    popen = _FakePopen(stdout=[json.dumps({"final_status": "DONE"}) + "\n"], stderr=[], exit_code=0)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start_resume(job_id="job-1", resume_id="resume-1")
    _wait_for_event(conn, "job-1", "process_started")
    assert popen.calls
    env = popen.calls[0]["env"]
    # Must inherit MAVEN_CMD and JAVA_HOME from the original command's env_json
    assert env.get("MAVEN_CMD") == "C:/maven/bin/mvn.cmd"
    assert env.get("JAVA_HOME") == "C:/jdk11"
    assert env.get("JAVA11_HOME") == "C:/jdk11"
    assert env.get("JAVA17_HOME") == "C:/jdk17"
    assert env.get("JAVA21_HOME") == "C:/jdk21"


def test_v2_runner_emits_diagnostic_fields_in_build_failed(tmp_path: Path) -> None:
    """Build failure events must include matched_line, command, module, and other contract fields."""
    conn = _conn(tmp_path)
    _save_command(conn)
    result = {
        "final_status": "BUILD_FAILED_IN_SANDBOX",
        "build_status": "BUILD_FAILED_IN_SANDBOX",
        "build_validation": {
            "matched_line": "[ERROR] Failed to resolve: com.example:missing-lib:1.0",
            "command": ["mvn", "compile", "-pl", "my-module"],
            "requested_command": ["mvn", "compile"],
            "resolved_command": ["mvn", "compile", "-pl", "my-module"],
            "build_tool": "maven",
            "result_kind": "dependency_error",
            "message": "Java application dependency resolution failed",
            "module": "my-module",
        },
    }
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[json.dumps(result) + "\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "build_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    build_failed_events = [e for e in events if e.type == "build_failed"]
    assert build_failed_events
    payload = json.loads(build_failed_events[-1].payload_json)
    assert payload.get("matched_line") is not None
    assert "com.example:missing-lib" in str(payload.get("matched_line"))
    assert payload.get("build_tool") == "maven"
    assert payload.get("result_kind") == "dependency_error"
    assert payload.get("module") == "my-module"
    # Verify command fields are present
    assert payload.get("command") == ["mvn", "compile", "-pl", "my-module"]


def test_v2_runner_resume_no_env_manifest_fallback(tmp_path: Path) -> None:
    """Resume without any original stage command should still work (empty manifest)."""
    conn = _conn(tmp_path)
    now = utc_now_text()
    SqliteUnitOfWork(conn).v2_approvals.save_card(
        V2ApprovalDecisionRecord(
            card_id="card-1",
            job_id="job-1",
            interrupt_id="run-1",
            request_checksum="chk",
            stage_index=1,
            summary="test",
            status="approved",
            created_at=now,
        )
    )
    SqliteUnitOfWork(conn).v2_approvals.save_resume(
        V2ResumeCommandRecord(
            resume_id="resume-1",
            card_id="card-1",
            decision="approved",
            job_id="job-1",
            stage_index=1,
            command_json=json.dumps(["python", "-m", "resume"]),
            created_at=now,
        )
    )
    popen = _FakePopen(stdout=[json.dumps({"final_status": "DONE"}) + "\n"], stderr=[], exit_code=0)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

# ──────────────────────────────────────────────
# _extract_final_json sentinel tests
# ──────────────────────────────────────────────


def test_extract_final_json_compact_one_line_sentinel() -> None:
    """One-line compact JSON after CONTROL_TOWER_FINAL_JSON is parsed directly."""
    from migration_factory.control_tower.application.v2_orchestrator_runner import _extract_final_json

    stdout = (
        'CONTROL_TOWER_EVENT {"phase":"approval","status":"completed"}\n'
        'CONTROL_TOWER_FINAL_JSON {"run_id":"x","sandbox_path":"/tmp/s1","final_status":"TRANSFORM_APPLIED_IN_SANDBOX"}\n'
    )
    result = _extract_final_json(stdout)
    assert result is not None
    assert result["run_id"] == "x"
    assert result["sandbox_path"] == "/tmp/s1"
    assert result["final_status"] == "TRANSFORM_APPLIED_IN_SANDBOX"


def test_extract_final_json_prefers_sentinel_over_bare_json() -> None:
    """Sentinel is preferred even when bare JSON also appears in stdout."""
    from migration_factory.control_tower.application.v2_orchestrator_runner import _extract_final_json

    stdout = (
        '{"run_id":"old","final_status":"STALE"}\n'
        'CONTROL_TOWER_FINAL_JSON {"run_id":"real","sandbox_path":"/tmp/s1","final_status":"TRANSFORM_APPLIED_IN_SANDBOX"}\n'
    )
    result = _extract_final_json(stdout)
    assert result is not None
    assert result["run_id"] == "real"
    assert result["final_status"] == "TRANSFORM_APPLIED_IN_SANDBOX"


def test_extract_final_json_multi_line_sentinel_does_not_parse_partial_json() -> None:
    """Pretty multi-line sentinel is NOT silently parsed as one-line by the sentinel parser.
    The sentinel parser reads only the line containing CONTROL_TOWER_FINAL_JSON.
    If that line ends with just '{', json.loads fails and the parser falls through
    to the generic scan, which should still find the full object.
    """
    from migration_factory.control_tower.application.v2_orchestrator_runner import _extract_final_json

    # This is the OLD multi-line format — the sentinel line is just "{" which is incomplete
    stdout = (
        'CONTROL_TOWER_FINAL_JSON {\n'
        '  "run_id": "x",\n'
        '  "sandbox_path": "/tmp/s1",\n'
        '  "final_status": "TRANSFORM_APPLIED_IN_SANDBOX"\n'
        '}\n'
    )
    result = _extract_final_json(stdout)
    # The generic scanner (fallback) should still find the full JSON
    assert result is not None
    # But the result must contain ALL three critical fields, not just '{' parsed as partial object
    assert result.get("run_id") == "x"
    assert result.get("sandbox_path") == "/tmp/s1"
    assert result.get("final_status") == "TRANSFORM_APPLIED_IN_SANDBOX"


def test_extract_final_json_multi_line_sentinel_without_fallback_returns_none() -> None:
    """If multi-line sentinel is the ONLY content and the single-line parser
    can't read it, the generic fallback must be able to parse it.
    This proves no silent partial-JSON acceptance."""
    from migration_factory.control_tower.application.v2_orchestrator_runner import _extract_final_json

    # Only multi-line sentinel, no CONTROL_TOWER_EVENT lines
    stdout = (
        'CONTROL_TOWER_FINAL_JSON {\n'
        '  "run_id": "y",\n'
        '  "final_status": "DONE"\n'
        '}\n'
    )
    result = _extract_final_json(stdout)
    # Must not return {'run_id': 'y'} by cherry-picking a partial parse
    assert result is not None
    assert result["run_id"] == "y"
    assert result["final_status"] == "DONE"


def test_extract_final_json_bare_json_still_works() -> None:
    """Bare JSON without sentinel still works via generic fallback."""
    from migration_factory.control_tower.application.v2_orchestrator_runner import _extract_final_json

    stdout = '{"run_id":"fallback","sandbox_path":"/tmp/s1","final_status":"OK"}\n'
    result = _extract_final_json(stdout)
    assert result is not None
    assert result["run_id"] == "fallback"
    assert result["final_status"] == "OK"


def test_extract_final_json_empty_stdout_returns_none() -> None:
    """Empty stdout after filtering CONTROL_TOWER_EVENT returns None."""
    from migration_factory.control_tower.application.v2_orchestrator_runner import _extract_final_json

    assert _extract_final_json("") is None
    assert _extract_final_json("CONTROL_TOWER_EVENT {\"phase\":\"approval\"}\n") is None


# ──────────────────────────────────────────────
# result_contract_failed event tests
# ──────────────────────────────────────────────


def test_runner_zero_exit_missing_final_json_emits_result_contract_failed(tmp_path: Path) -> None:
    """Zero exit but no parseable JSON emits result_contract_failed before stage_failed."""
    conn = _conn(tmp_path)
    _save_command(conn)
    # Stdout has only CONTROL_TOWER_EVENT lines, no final JSON
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(
            stdout=["CONTROL_TOWER_EVENT {\"phase\":\"approval\",\"status\":\"completed\"}\n"],
            stderr=[],
            exit_code=0,
        ),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "result_contract_failed" in event_types
    # Verify payload has diagnostic fields
    for event in events:
        if event.type == "result_contract_failed":
            payload = json.loads(event.payload_json or "{}")
            assert payload.get("final_json_found") is False
            assert "exit_code" in payload
            assert "stdout_tail" in payload
            assert "stderr_tail" in payload
            assert "parse_strategy" in payload
            break
    else:
        raise AssertionError("result_contract_failed event not found")

    # stage_failed message must not say "review build logs"
    stage_failed = [e for e in events if e.type == "stage_failed"][-1]
    assert "result contract" in stage_failed.message.lower() or "parseable" in stage_failed.message.lower()


def test_runner_does_not_auto_queue_when_final_json_missing(tmp_path: Path) -> None:
    """Zero exit + missing final JSON must not auto-queue next stage."""
    conn = _conn(tmp_path)
    _save_command(conn)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=["CONTROL_TOWER_EVENT {\"phase\":\"test\",\"status\":\"completed\"}\n"], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "next_stage_queued" not in event_types
    assert "stage_completed" not in event_types


def test_runner_nonzero_exit_keeps_exit_code_message(tmp_path: Path) -> None:
    """Non-zero exit includes exit code in stage_failed message."""
    conn = _conn(tmp_path)
    _save_command(conn)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=["error log\n"], exit_code=42),
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-1")
    _wait_for_event(conn, "job-1", "stage_failed")

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    stage_failed = [e for e in events if e.type == "stage_failed"][-1]
    assert "code 42" in stage_failed.message
    payload = json.loads(stage_failed.payload_json or "{}")
    assert payload.get("exit_code") == 42


# ── AMF-265/AMF-268: Target-stop and overshoot prevention ────────────


def _insert_run_config(conn: sqlite3.Connection, *, job_id: str, rc_id: str, source_profile: str, target_profile: str, policy_json: str) -> None:
    from migration_factory.control_tower.domain.entities import RunConfigurationRecord
    from migration_factory.control_tower.domain.states import TargetProofLevel
    now = utc_now_text()
    payload = {"source_profile": source_profile, "target_profile": target_profile}
    SqliteUnitOfWork(conn).run_configurations.insert(RunConfigurationRecord(
        run_configuration_id=rc_id,
        job_id=job_id,
        schema_version="1.0",
        runner_profile_id="rp-1",
        runner_profile_version="1.0",
        pipeline_id="test-pipeline",
        pipeline_version="1.0",
        target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED,
        enabled_gates_json="[]",
        policy_json=policy_json,
        payload_json=json.dumps(payload),
        payload_checksum="",
        created_at=now,
    ))


def test_auto_queue_next_stage_blocked_at_target(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(conn, job_id="job-1", rc_id="rc-1",
                       source_profile="springboot-2.7-java11",
                       target_profile="springboot-3.5-java17",
                       policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}))
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(
            stdout=[json.dumps(_success_result(sandbox_path="/tmp/stage-2")) + "\n"],
            stderr=[],
            exit_code=0,
        ),
        cwd=tmp_path,
    )
    setattr(runner, "_last_stdout_lines", [json.dumps(_success_result(sandbox_path="/tmp/stage-2"))])
    runner._handle_exit(
        job_id="job-1",
        stage_index=2,
        command_id="cmd-1",
        exit_code=0,
        result=_success_result(sandbox_path="/tmp/stage-2"),
        stderr="",
        command_phase=None,
    )
    _wait_for_event(conn, "job-1", "migration_completed")
    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [e.type for e in events]
    assert "migration_completed" in event_types
    assert "target_reached" not in event_types


def test_higher_profile_exists_but_excluded_from_auto_queue(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(conn, job_id="job-1", rc_id="rc-2",
                       source_profile="springboot-2.7-java11",
                       target_profile="springboot-3.5-java17",
                       policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}))
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(
            stdout=[json.dumps(_success_result(sandbox_path="/tmp/stage-2")) + "\n"],
            stderr=[],
            exit_code=0,
        ),
        cwd=tmp_path,
    )
    setattr(runner, "_last_stdout_lines", [json.dumps(_success_result(sandbox_path="/tmp/stage-2"))])
    runner._handle_exit(
        job_id="job-1",
        stage_index=2,
        command_id="cmd-1",
        exit_code=0,
        result=_success_result(sandbox_path="/tmp/stage-2"),
        stderr="",
        command_phase=None,
    )
    _wait_for_event(conn, "job-1", "migration_completed")
    stage3_commands = SqliteUnitOfWork(conn).v2_commands.list_by_job_and_stage("job-1", 3)
    assert len(stage3_commands) == 0


def test_auto_queue_next_stage_continues_to_4_when_target_is_boot4(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(conn, job_id="job-1", rc_id="rc-3",
                       source_profile="springboot-2.7-java11",
                       target_profile="springboot-4.0-java21",
                       policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}))
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(
            stdout=[json.dumps(_success_result(sandbox_path="/tmp/stage-3")) + "\n"],
            stderr=[],
            exit_code=0,
        ),
        cwd=tmp_path,
    )
    setattr(runner, "_last_stdout_lines", [json.dumps(_success_result(sandbox_path="/tmp/stage-3"))])
    runner._handle_exit(
        job_id="job-1",
        stage_index=3,
        command_id="cmd-1",
        exit_code=0,
        result=_success_result(sandbox_path="/tmp/stage-3"),
        stderr="",
        command_phase=None,
    )
    _wait_for_stage4_command(conn)
    stage4_commands = SqliteUnitOfWork(conn).v2_commands.list_by_job_and_stage("job-1", 4)
    assert len(stage4_commands) >= 1


def test_auto_queue_next_stage_uses_route_step_metadata_for_skipped_stage_route(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(
        conn,
        job_id="job-1",
        rc_id="rc-route-step",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-4.0-java21",
        policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}),
    )
    now = utc_now_text()
    command_id = "cmd-route-step-1"
    current_result = _success_result(
        sandbox_path="/tmp/stage-1",
        profile_id="springboot-3.5-java17-to-java21",
        route_step_index=1,
    )
    SqliteUnitOfWork(conn).v2_commands.save(
        V2StageCommandRecord(
            command_id=command_id,
            job_id="job-1",
            stage_index=1,
            manifest_checksum="checksum-route-step-1",
            argv_json=json.dumps([
                "python",
                "-m",
                "migration_factory.orchestrator.runner",
                "--run-id",
                "v2-job-1-s1",
                "--profile",
                "springboot-3.5-java17-to-java21",
            ]),
            env_json=json.dumps(
                {
                    "JAVA_HOME": "C:/jdk21",
                    "JAVA11_HOME": "C:/jdk11",
                    "JAVA17_HOME": "C:/jdk17",
                    "JAVA21_HOME": "C:/jdk21",
                    "MAVEN_CMD": "C:/maven/bin/mvn.cmd",
                    "PATH_PREPEND": "C:/jdk21/bin",
                    "ROUTE_STEP_INDEX": "1",
                    "ROUTE_STEP_RUNTIME_PROFILE": "springboot-3.5-java17-to-java21",
                    "ROUTE_STEP_CATALOG": "springboot-3.5-java17-to-java21",
                    "ROUTE_STEP_EXECUTION_JDK": "java21",
                }
            ),
            status="completed",
            created_at=now,
            updated_at=now,
            result_json=json.dumps(current_result),
        )
    )
    popen = _FakePopen(
        stdout=[json.dumps(_success_result(sandbox_path="/tmp/stage-4")) + "\n"],
        stderr=[],
        exit_code=0,
    )
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner._handle_exit(
        job_id="job-1",
        stage_index=1,
        command_id=command_id,
        exit_code=0,
        result=current_result,
        stderr="",
        command_phase=None,
    )

    _wait_for_stage4_command(conn)
    _wait_for_popen_call_containing(popen, "springboot-3.5-java21-to-4.0-java21")

    commands = SqliteUnitOfWork(conn).v2_commands.list_by_job("job-1")
    stage3_commands = [command for command in commands if command.stage_index == 3]
    stage4_commands = [command for command in commands if command.stage_index == 4]
    assert len(stage3_commands) == 0
    assert len(stage4_commands) == 1
    assert "springboot-3.5-java21-to-4.0-java21" in stage4_commands[0].argv_json
    env = json.loads(stage4_commands[0].env_json)
    assert env.get("ROUTE_STEP_INDEX") == "2"
    assert env.get("ROUTE_STEP_RUNTIME_PROFILE") == "springboot-3.5-java21-to-4.0-java21"


def test_auto_queue_next_stage_resolves_resume_command_to_original_route_metadata(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(
        conn,
        job_id="job-1",
        rc_id="rc-resume-route-step",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-4.0-java21",
        policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}),
    )
    now = utc_now_text()
    original_command_id = "cmd-route-step-before-resume"
    resume_id = "resume-route-step-1"
    previous_output = "/tmp/stage-1-java21-output"
    current_result = _success_result(
        sandbox_path=previous_output,
        profile_id="springboot-3.5-java17-to-java21",
        route_step_index=1,
    )
    uow = SqliteUnitOfWork(conn)
    uow.v2_commands.save(
        V2StageCommandRecord(
            command_id=original_command_id,
            job_id="job-1",
            stage_index=1,
            manifest_checksum="checksum-route-step-before-resume",
            argv_json=json.dumps([
                "python",
                "-m",
                "migration_factory.orchestrator.runner",
                "--run-id",
                "v2-job-1-s1",
                "--profile",
                "springboot-3.5-java17-to-java21",
            ]),
            env_json=json.dumps(
                {
                    "JAVA_HOME": "C:/jdk21",
                    "JAVA11_HOME": "C:/jdk11",
                    "JAVA17_HOME": "C:/jdk17",
                    "JAVA21_HOME": "C:/jdk21",
                    "MAVEN_CMD": "C:/maven/bin/mvn.cmd",
                    "PATH_PREPEND": "C:/jdk21/bin",
                    "ROUTE_STEP_INDEX": "1",
                    "ROUTE_STEP_RUNTIME_PROFILE": "springboot-3.5-java17-to-java21",
                    "ROUTE_STEP_CATALOG": "springboot-3.5-java17-to-java21",
                    "ROUTE_STEP_EXECUTION_JDK": "java21",
                }
            ),
            status="completed",
            created_at=now,
            updated_at=now,
            result_json=json.dumps(current_result),
        )
    )
    uow.v2_approvals.save_resume(
        V2ResumeCommandRecord(
            resume_id=resume_id,
            card_id="card-route-step-1",
            decision="approve",
            job_id="job-1",
            stage_index=1,
            command_json=json.dumps(["python", "-m", "migration_factory.orchestrator.resume"]),
            created_at=now,
        )
    )
    popen = _FakePopen(
        stdout=[json.dumps(_success_result(sandbox_path="/tmp/stage-4")) + "\n"],
        stderr=[],
        exit_code=0,
    )
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner._handle_exit(
        job_id="job-1",
        stage_index=1,
        command_id=resume_id,
        exit_code=0,
        result=current_result,
        stderr="",
        resume=True,
        command_phase=None,
    )

    _wait_for_stage4_command(conn)
    _wait_for_popen_call_containing(popen, "springboot-3.5-java21-to-4.0-java21")
    commands = SqliteUnitOfWork(conn).v2_commands.list_by_job("job-1")
    stage4_commands = [command for command in commands if command.stage_index == 4]
    assert len(stage4_commands) == 1
    queued_argv = json.loads(stage4_commands[0].argv_json)
    assert _argv_option_value_for_test(queued_argv, "--profile") == "springboot-3.5-java21-to-4.0-java21"
    assert _argv_option_value_for_test(queued_argv, "--legacy") == previous_output
    assert _argv_option_value_for_test(queued_argv, "--legacy") != "C:/legacy"
    assert "springboot-3.5-java17-to-java21" not in queued_argv
    env = json.loads(stage4_commands[0].env_json)
    assert env.get("ROUTE_STEP_INDEX") == "2"
    assert env.get("ROUTE_STEP_RUNTIME_PROFILE") == "springboot-3.5-java21-to-4.0-java21"
    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    assert any(event.type == "next_stage_queued" for event in events)
    assert not any(event.type == "stage_progression_blocked" for event in events)


def test_auto_queue_next_stage_blocks_resume_when_original_command_metadata_missing(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(
        conn,
        job_id="job-1",
        rc_id="rc-resume-missing-command",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-4.0-java21",
        policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}),
    )
    now = utc_now_text()
    resume_id = "resume-missing-original-command"
    SqliteUnitOfWork(conn).v2_approvals.save_resume(
        V2ResumeCommandRecord(
            resume_id=resume_id,
            card_id="card-route-step-1",
            decision="approve",
            job_id="job-1",
            stage_index=1,
            command_json=json.dumps(["python", "-m", "migration_factory.orchestrator.resume"]),
            created_at=now,
        )
    )
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=[], exit_code=0),
        cwd=tmp_path,
    )

    runner._auto_queue_next_stage(
        job_id="job-1",
        stage_index=1,
        sandbox_path="/tmp/stage-1-java21-output",
        command_id=resume_id,
        result=_success_result(sandbox_path="/tmp/stage-1-java21-output"),
    )

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    blocked = [event for event in events if event.type == "stage_progression_blocked"]
    assert len(blocked) == 1
    payload = json.loads(blocked[0].payload_json or "{}")
    assert payload.get("reason") == "missing_route_step_index"
    assert "stage-1-java21-output" not in blocked[0].payload_json
    assert SqliteUnitOfWork(conn).v2_commands.list_by_job_and_stage("job-1", 4) == ()


def _argv_option_value_for_test(argv: list[str], option_name: str) -> str:
    for index, value in enumerate(argv):
        if value == option_name and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def test_target_reached_stop_condition_emitted(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(conn, job_id="job-1", rc_id="rc-4",
                       source_profile="springboot-2.7-java11",
                       target_profile="springboot-3.5-java17",
                       policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}))
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(
            stdout=[json.dumps(_success_result(sandbox_path="/tmp/stage-2")) + "\n"],
            stderr=[],
            exit_code=0,
        ),
        cwd=tmp_path,
    )
    setattr(runner, "_last_stdout_lines", [json.dumps(_success_result(sandbox_path="/tmp/stage-2"))])
    runner._handle_exit(
        job_id="job-1",
        stage_index=2,
        command_id="cmd-1",
        exit_code=0,
        result=_success_result(sandbox_path="/tmp/stage-2"),
        stderr="",
        command_phase=None,
    )
    _wait_for_event(conn, "job-1", "migration_completed")
    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    target_events = [e for e in events if e.type == "migration_completed"]
    assert len(target_events) >= 1
    payload = json.loads(target_events[0].payload_json or "{}")
    assert payload.get("reason") == "migration_completed"
    route = payload.get("route", {})
    assert route.get("source_profile") == "springboot-2.7-java11"
    assert route.get("target_profile") == "springboot-3.5-java17"


def test_migration_completed_emitted_for_boot35_java17_to_java21(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(
        conn,
        job_id="job-1",
        rc_id="rc-5",
        source_profile="springboot-3.5-java17",
        target_profile="springboot-3.5-java21",
        policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}),
    )
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(
            stdout=[json.dumps(_success_result(sandbox_path="/tmp/stage-3")) + "\n"],
            stderr=[],
            exit_code=0,
        ),
        cwd=tmp_path,
    )
    setattr(runner, "_last_stdout_lines", [json.dumps(_success_result(sandbox_path="/tmp/stage-3"))])
    runner._handle_exit(
        job_id="job-1",
        stage_index=3,
        command_id="cmd-1",
        exit_code=0,
        result=_success_result(sandbox_path="/tmp/stage-3"),
        stderr="",
        command_phase=None,
    )
    _wait_for_event(conn, "job-1", "migration_completed")
    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [e.type for e in events]
    assert "migration_completed" in event_types
    assert "stage4_started" not in event_types
    stage4_commands = SqliteUnitOfWork(conn).v2_commands.list_by_job_and_stage("job-1", 4)
    assert len(stage4_commands) == 0
