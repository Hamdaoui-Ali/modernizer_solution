from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from migration_factory.control_tower.application.v2_orchestrator_runner import V2OrchestratorRunner
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.test_v2_orchestrator_runner import (
    _FakePopen,
    _reviewed_phase_result,
    _save_phase_command,
    _seed_stage_pipeline,
    _success_result,
    _wait_for_popen_call_containing,
    _insert_run_config,
)


def test_phase_argv_uses_stage2_route_step_profile_for_multistage_job(tmp_path: Path) -> None:
    db_path = tmp_path / "phase_argv_multistage.sqlite3"
    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    _seed_stage_pipeline(conn, seed_run_configuration=False)
    _insert_run_config(
        conn,
        job_id="job-1",
        rc_id="rc-multistage",
        source_profile="springboot-2.1-java11",
        target_profile="springboot-4.0-java21",
        policy_json=json.dumps({"stage_continuation_policy": "auto_on_green"}),
    )
    _save_phase_command(conn, command_id="cmd-planning", job_id="job-1", stage_index=2, phase="planning")
    popen = _FakePopen(stdout=[json.dumps(_success_result(sandbox_path="/tmp/planning")) + "\n"], stderr=[], exit_code=0)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=popen,
        cwd=tmp_path,
    )

    runner.start(job_id="job-1", command_id="cmd-planning")
    _wait_for_popen_call_containing(popen, "--phase planning")

    argv = popen.calls[0]["argv"]
    assert "--profile" in argv
    assert argv[argv.index("--profile") + 1] == "springboot-2.7-to-3.5-java17"


def test_planning_reviewed_result_does_not_require_sandbox_path(tmp_path: Path) -> None:
    db_path = tmp_path / "planning_no_sandbox.sqlite3"
    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    _save_phase_command(conn, command_id="cmd-1", job_id="job-1", stage_index=2, phase="planning")
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=_FakePopen(stdout=[], stderr=[], exit_code=0),
        cwd=tmp_path,
    )
    result = _reviewed_phase_result(phase="planning")
    result.pop("sandbox_path")

    runner._handle_exit(
        job_id="job-1",
        stage_index=2,
        command_id="cmd-1",
        exit_code=0,
        result=result,
        stderr="",
        command_phase="planning",
    )

    events = SqliteUnitOfWork(conn).v2_events.list_by_job("job-1")
    event_types = [event.type for event in events]
    assert "planning_review_required" in event_types
    assert "stage_failed" not in event_types
    gate = SqliteUnitOfWork(conn).phase_gates.list_open("job-1")[0]
    assert gate.gate_phase == "planning_review"
