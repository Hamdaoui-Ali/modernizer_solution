"""AMF-273 / F4-T5 resume-from-checkpoint profile validation tests."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from migration_factory.control_tower.application.v2_orchestrator_runner import (
    V2OrchestratorRunner,
)
from migration_factory.control_tower.application.v2_stage_progression import (
    compute_profile_route,
    route_to_dict,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.entities import (
    ArtifactRevisionRecord,
    PhaseGateRecord,
)
from migration_factory.control_tower.domain.gate_checksum import gate_checksum
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteUnitOfWork,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import (
    V2ApprovalDecisionRecord,
    V2ResumeCommandRecord,
)


class _FakePopen:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.pid = 1234
        self.stdout = iter(['{"final_status":"DONE"}\n'])
        self.stderr = iter(())

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        return self

    def wait(self) -> int:
        return 0


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(tmp_path / "resume_profile.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _insert_run_config(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    source_profile: str,
    target_profile: str,
) -> None:
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


def _seed_resume_checkpoint(
    conn: sqlite3.Connection,
    *,
    job_id: str = "job-resume",
    stage_index: int = 2,
    source_profile: str = "springboot-2.7-java11",
    target_profile: str = "springboot-3.5-java17",
    checkpoint_source_profile: str | None = None,
    checkpoint_target_profile: str | None = None,
    accepted_checksum: str = "sha256:accepted",
    gate_source_checksum: str | None = None,
    gate_status: str = "open",
    include_accepted_revision: bool = True,
    include_profile_metadata: bool = True,
) -> str:
    _insert_run_config(
        conn,
        job_id=job_id,
        source_profile=source_profile,
        target_profile=target_profile,
    )
    route = route_to_dict(compute_profile_route(
        checkpoint_source_profile or source_profile,
        checkpoint_target_profile or target_profile,
    ))
    refs = (
        [
            {
                "kind": "profile_route",
                "path_or_ref": "metadata:profile-route",
                "checksum": "sha256:route",
                "profile_metadata": route,
            }
        ]
        if include_profile_metadata
        else [
            {
                "kind": "profile_route",
                "path_or_ref": "metadata:profile-route",
                "checksum": "sha256:route",
            }
        ]
    )
    now = utc_now_text()
    source_checksum = gate_source_checksum or accepted_checksum
    gate = PhaseGateRecord(
        gate_id="gate-resume",
        job_id=job_id,
        gate_phase="planning_review",
        stage_index=stage_index,
        gate_status=gate_status,
        gate_decision="pending",
        source_artifact_checksum=source_checksum,
        resolved_artifact_checksum=None,
        source_artifact_refs_json=json.dumps(refs, separators=(",", ":")),
        created_at=now,
    )
    SqliteUnitOfWork(conn).phase_gates.save(gate)
    checkpoint_checksum = gate_checksum(
        gate_id=gate.gate_id,
        job_id=gate.job_id,
        gate_phase=gate.gate_phase,
        stage_index=gate.stage_index,
        source_artifact_checksum=gate.source_artifact_checksum,
        source_artifact_refs=refs,
    )
    if include_accepted_revision:
        SqliteUnitOfWork(conn).artifact_revisions.save(
            ArtifactRevisionRecord(
                revision_id="rev-accepted",
                job_id=job_id,
                stage_index=stage_index,
                revision_kind="planning",
                revision_status="accepted",
                revision_order=1,
                evidence_checksum=accepted_checksum,
                prior_revision_checksum=None,
                artifact_refs_json=json.dumps(refs, separators=(",", ":")),
                prior_revision_id=None,
                superseded_by_revision_id=None,
                accepted_at_gate_id=gate.gate_id,
                created_at=now,
                created_by="test",
                accepted_at=now,
                accepted_by="human",
            )
        )
    SqliteUnitOfWork(conn).v2_approvals.save_card(
        V2ApprovalDecisionRecord(
            card_id="card-resume",
            job_id=job_id,
            interrupt_id="interrupt-resume",
            request_checksum=checkpoint_checksum,
            stage_index=stage_index,
            summary="approved checkpoint",
            status="approved",
            created_at=now,
        )
    )
    SqliteUnitOfWork(conn).v2_approvals.save_resume(
        V2ResumeCommandRecord(
            resume_id="resume-1",
            card_id="card-resume",
            decision="approved",
            job_id=job_id,
            stage_index=stage_index,
            command_json=json.dumps(["python", "-m", "resume"]),
            created_at=now,
        )
    )
    return "resume-1"


def _wait_for_event(conn: sqlite3.Connection, job_id: str, event_type: str) -> None:
    deadline = time.time() + 2
    while time.time() < deadline:
        events = SqliteUnitOfWork(conn).v2_events.list_by_job(job_id)
        if any(event.type == event_type for event in events):
            return
        time.sleep(0.01)
    raise AssertionError(f"event {event_type!r} was not emitted")


def _wait_for_popen_calls(popen: _FakePopen) -> None:
    deadline = time.time() + 2
    while time.time() < deadline:
        if popen.calls:
            return
        time.sleep(0.01)
    raise AssertionError("process launch was not observed")


def test_valid_resume_launches_only_after_profile_and_checksum_validation(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    resume_id = _seed_resume_checkpoint(conn)
    popen = _FakePopen()
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    result = runner.start_resume(job_id="job-resume", resume_id=resume_id)
    _wait_for_event(conn, "job-resume", "resume_started")

    assert result.status == "started"
    _wait_for_popen_calls(popen)


def test_approval_acceptance_persists_profile_metadata_for_resume_checkpoint(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    resume_id = _seed_resume_checkpoint(conn)
    popen = _FakePopen()
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    result = runner.start_resume(job_id="job-resume", resume_id=resume_id)
    _wait_for_event(conn, "job-resume", "resume_started")

    assert result.status == "started"
    accepted = SqliteUnitOfWork(conn).artifact_revisions.find_accepted("job-resume", 2, "planning")
    assert accepted is not None
    refs = json.loads(accepted.artifact_refs_json)
    assert isinstance(refs, list)
    profile_refs = [
        ref for ref in refs
        if isinstance(ref, dict) and isinstance(ref.get("profile_metadata"), dict)
    ]
    assert profile_refs, "accepted revision should persist profile_metadata for resume validation"
    profile_metadata = profile_refs[0]["profile_metadata"]
    assert profile_metadata["source_profile"] == "springboot-2.7-java11"
    assert profile_metadata["target_profile"] == "springboot-3.5-java17"
    assert profile_metadata["included_stages"] == [2]
    assert profile_metadata["excluded_stages"] == [3, 4]
    assert profile_metadata["skipped_stages"] == []
    _wait_for_popen_calls(popen)


def test_approval_acceptance_persists_boot21_route_metadata_for_resume_checkpoint(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    resume_id = _seed_resume_checkpoint(
        conn,
        source_profile="springboot-2.1-java11",
        target_profile="springboot-4.0-java21",
    )
    popen = _FakePopen()
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    result = runner.start_resume(job_id="job-resume", resume_id=resume_id)
    _wait_for_event(conn, "job-resume", "resume_started")

    assert result.status == "started"
    accepted = SqliteUnitOfWork(conn).artifact_revisions.find_accepted("job-resume", 2, "planning")
    assert accepted is not None
    refs = json.loads(accepted.artifact_refs_json)
    profile_refs = [
        ref for ref in refs
        if isinstance(ref, dict) and isinstance(ref.get("profile_metadata"), dict)
    ]
    assert profile_refs
    profile_metadata = profile_refs[0]["profile_metadata"]
    assert profile_metadata["source_profile"] == "springboot-2.1-java11"
    assert profile_metadata["target_profile"] == "springboot-4.0-java21"
    assert len(profile_metadata["route_steps"]) == 4
    assert profile_metadata["route_steps"][0]["runtime_profile"] == "springboot-2.1.6-to-2.7-java11"
    _wait_for_popen_calls(popen)


def test_resume_rejects_stale_artifact_checksum_before_launch(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    resume_id = _seed_resume_checkpoint(
        conn,
        accepted_checksum="sha256:accepted",
        gate_source_checksum="sha256:stale",
    )
    popen = _FakePopen()
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    result = runner.start_resume(job_id="job-resume", resume_id=resume_id)

    assert result.status == "rejected"
    assert result.message == "accepted_artifact_not_found"
    assert popen.calls == []


def test_resume_rejects_changed_source_target_route_before_launch(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    resume_id = _seed_resume_checkpoint(
        conn,
        source_profile="springboot-3.5-java17",
        target_profile="springboot-4.0-java21",
        checkpoint_source_profile="springboot-2.7-java11",
        checkpoint_target_profile="springboot-3.5-java17",
    )
    popen = _FakePopen()
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    result = runner.start_resume(job_id="job-resume", resume_id=resume_id)

    assert result.status == "rejected"
    assert result.message == "checkpoint_route_changed"
    assert popen.calls == []


def test_resume_rejects_missing_accepted_artifact_before_launch(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    resume_id = _seed_resume_checkpoint(conn, include_accepted_revision=False)
    popen = _FakePopen()
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    result = runner.start_resume(job_id="job-resume", resume_id=resume_id)

    assert result.status == "rejected"
    assert result.message == "accepted_artifact_not_found"
    assert popen.calls == []


def test_resume_rejected_when_profile_metadata_missing(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    resume_id = _seed_resume_checkpoint(conn, include_profile_metadata=False)
    popen = _FakePopen()
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    result = runner.start_resume(job_id="job-resume", resume_id=resume_id)

    assert result.status == "rejected"
    assert result.message == "checkpoint_profile_metadata_missing"
    assert popen.calls == []
