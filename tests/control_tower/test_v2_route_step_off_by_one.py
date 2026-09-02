"""Regression tests for the route-step off-by-one bug (springboot-2.1 → 4.0 full route).

The bug: queue_next_stage computed next_stage from
route.included_stages[route_step_index - 1], but included_stages excludes
stage 1 (the base stage). For the full route springboot-2.1-java11 →
springboot-4.0-java21, included_stages = (2, 3, 4) has 3 elements while
route_steps has 4 elements (stage_index 1, 2, 3, 4). Indexing
included_stages by route_step_index shifted every stage by +1:

    route step 2 → next_stage = included_stages[1] = 3  (should be 2)
    route step 3 → next_stage = included_stages[2] = 4  (should be 3)
    route step 4 → included_stages[3] → IndexError crash

This caused:
  - sandbox folders: route step 2 wrote to -s3 instead of -s2
  - events: stage_started carried stage=3 instead of stage=2
  - frontend: Route step 3 card showed RUNNING instead of Route step 2

The fix: derive next_stage from next_route_step.stage_index (the canonical
route step object), not from indexing included_stages.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.application.v2_stage_progression import (
    V2StageProgressionService,
    compute_profile_route,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.schemas.run_configuration import (
    StageContinuationPolicy,
)

FULL_ROUTE_SOURCE = "springboot-2.1-java11"
FULL_ROUTE_TARGET = "springboot-4.0-java21"


def _connection(tmp_path: Path, name: str = "route_offbyone.sqlite3") -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(tmp_path / name),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _create_setup(repo: SqliteV2SetupRepository) -> str:
    service = V2SetupService(repo)
    req = CreateSetupRequest(
        run_name="test-route-offbyone",
        legacy_app_path="/tmp/legacy",
        output_parent_path="/tmp/output",
        ai_hub_path="/tmp/ai-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
    )
    return service.create_setup(req).setup_id


def _success_result(**overrides: Any) -> dict[str, Any]:
    result = {
        "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "orchestration_status": "PASS",
        "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "PASS",
        "sandbox_path": "/tmp/sandbox",
    }
    result.update(overrides)
    return result


def _run_id_suffix(argv: tuple[str, ...]) -> str:
    argv_list = list(argv)
    idx = argv_list.index("--run-id")
    run_id = argv_list[idx + 1]
    return run_id.rsplit("-s", 1)[-1] if "-s" in run_id else run_id


def _full_route() -> Any:
    return compute_profile_route(FULL_ROUTE_SOURCE, FULL_ROUTE_TARGET)


def _queue(
    service: V2StageProgressionService,
    *,
    setup_id: str,
    job_id: str,
    completed_step_index: int,
    route: Any,
    current_stage_result: dict[str, Any] | None = None,
) -> Any:
    return service.queue_next_stage(
        job_id=job_id,
        setup_id=setup_id,
        current_stage=completed_step_index,
        sandbox_path=f"/tmp/sandbox/s{completed_step_index}",
        profile_route=route,
        current_route_step_index=completed_step_index,
        current_stage_result=current_stage_result,
    )


# ── Test 1: sandbox folder naming for full route ───────────────────


def test_sandbox_folder_naming_for_full_route(tmp_path: Path) -> None:
    """Route step N must produce sandbox folder -sN, not -s(N+1).

    Before the fix, route step 2 wrote to -s3, route step 3 wrote to -s4,
    and route step 4 crashed with IndexError.
    """
    conn = _connection(tmp_path)
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    route = _full_route()
    service = V2StageProgressionService(setup_repo, command_repo)

    assert len(route.route_steps) == 4
    assert route.included_stages == (2, 3, 4)

    # Route step 1 → stage 2
    r1 = _queue(service, setup_id=setup_id, job_id="job-full", completed_step_index=1, route=route)
    assert r1.status == "queued"
    assert r1.to_stage == 2, f"route step 2 should target stage 2, got {r1.to_stage}"
    assert _run_id_suffix(r1.argv) == "2", f"route step 2 run-id should end -s2, got {_run_id_suffix(r1.argv)}"

    # Route step 2 → stage 3
    r2 = _queue(service, setup_id=setup_id, job_id="job-full", completed_step_index=2, route=route)
    assert r2.status == "queued"
    assert r2.to_stage == 3, f"route step 3 should target stage 3, got {r2.to_stage}"
    assert _run_id_suffix(r2.argv) == "3", f"route step 3 run-id should end -s3, got {_run_id_suffix(r2.argv)}"

    # Route step 3 → stage 4 (requires successful stage 3 evidence)
    r3 = _queue(
        service,
        setup_id=setup_id,
        job_id="job-full",
        completed_step_index=3,
        route=route,
        current_stage_result=_success_result(sandbox_path="/tmp/sandbox/s3"),
    )
    assert r3.status == "queued"
    assert r3.to_stage == 4, f"route step 4 should target stage 4, got {r3.to_stage}"
    assert _run_id_suffix(r3.argv) == "4", f"route step 4 run-id should end -s4, got {_run_id_suffix(r3.argv)}"

    conn.close()


# ── Test 2: backend event identity for route step 2 ────────────────


def test_backend_event_identity_for_route_step_2(tmp_path: Path) -> None:
    """When route step 2 starts, the command and event identity must be stage=2.

    The orchestrator runner emits events with stage=command.stage_index.
    The next_stage_queued event uses stage=queued.to_stage. Both derive
    from the value returned by queue_next_stage as next_stage / to_stage.

    Before the fix, next_stage was 3 (from included_stages[1]) instead of
    2 (from next_route_step.stage_index), so events carried stage=3 and
    the run-id ended with -s3.
    """
    conn = _connection(tmp_path)
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    route = _full_route()
    service = V2StageProgressionService(setup_repo, command_repo)

    queued = _queue(service, setup_id=setup_id, job_id="job-evt", completed_step_index=1, route=route)

    # to_stage is what next_stage_queued event carries as `stage`.
    assert queued.to_stage == 2, (
        f"next_stage_queued event stage = queued.to_stage = {queued.to_stage}, expected 2"
    )
    assert queued.to_stage != 3, "next_stage_queued event must NOT carry stage=3 for route step 2"

    # The run-id suffix is the sandbox folder name.
    assert _run_id_suffix(queued.argv) == "2", "sandbox folder must end with -s2"
    assert not _run_id_suffix(queued.argv) == "3", "sandbox folder must NOT end with -s3"

    # The persisted command record carries stage_index which becomes
    # stage_started / stage_completed / approval_required event `stage`.
    commands = command_repo.list_by_job_and_stage("job-evt", 2)
    assert len(commands) >= 1, "command for stage 2 should be persisted"
    cmd = commands[0]
    assert cmd.stage_index == 2, (
        f"stage_started event stage = command.stage_index = {cmd.stage_index}, expected 2"
    )
    assert cmd.stage_index != 3, "command must NOT have stage_index=3"

    # Env manifest carries the canonical route step identity.
    env = json.loads(cmd.env_json)
    assert env.get("ROUTE_STEP_INDEX") == "2"
    assert env.get("ROUTE_STEP_RUNTIME_PROFILE") == "springboot-2.7-to-3.5-java17"
    assert env.get("ROUTE_STEP_CATALOG") == "springboot-3.5-java17"

    # The run-id in the persisted argv must end with -s2.
    argv = json.loads(cmd.argv_json)
    run_id = argv[argv.index("--run-id") + 1]
    assert run_id.endswith("-s2"), f"persisted run-id must end with -s2, got {run_id}"

    conn.close()


# ── Test 4: approval gate identity for route step 2 ────────────────


def test_approval_gate_identity_for_route_step_2(tmp_path: Path) -> None:
    """The approval gate for route step 2 must belong to stage 2.

    The orchestrator runner creates approval gates/cards with
    stage_index=command.stage_index (v2_orchestrator_runner.py:676).
    Before the fix, the command had stage_index=3, so the approval gate
    would have been associated with stage 3 (route step 3) instead of
    stage 2 (route step 2).
    """
    conn = _connection(tmp_path)
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    route = _full_route()
    service = V2StageProgressionService(setup_repo, command_repo)

    _queue(service, setup_id=setup_id, job_id="job-gate", completed_step_index=1, route=route)

    # The command that would trigger an approval gate has stage_index=2.
    commands = command_repo.list_by_job_and_stage("job-gate", 2)
    assert len(commands) >= 1
    cmd = commands[0]
    assert cmd.stage_index == 2, (
        f"approval gate derives stage_index from command; "
        f"command has stage_index={cmd.stage_index}, expected 2"
    )
    assert cmd.stage_index != 3, "command must NOT have stage_index=3 (would mis-associate approval with route step 3)"

    # No stage 3 command should exist when route step 2 is queued.
    stage3_commands = command_repo.list_by_job_and_stage("job-gate", 3)
    assert len(stage3_commands) == 0, (
        "no stage 3 command should exist when route step 2 is queued; "
        f"found {len(stage3_commands)} stage 3 commands"
    )

    conn.close()


# ── Test 5: no off-by-one regression ───────────────────────────────


def test_no_off_by_one_full_route_explicit(tmp_path: Path) -> None:
    """Explicit assertions that route step N maps to stage N, not N+1.

    - route step 2 never writes to -s3
    - route step 3 never writes to -s4
    - route step 4 does not crash with IndexError
    - migration_completed is not shown before route step 4 completes
    """
    conn = _connection(tmp_path)
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    route = _full_route()
    service = V2StageProgressionService(setup_repo, command_repo)

    # Route step 1 → stage 2
    r1 = _queue(service, setup_id=setup_id, job_id="job-reg", completed_step_index=1, route=route)
    suffix1 = _run_id_suffix(r1.argv)
    assert suffix1 == "2", f"route step 2 → -s2, got -s{suffix1}"
    assert suffix1 != "3", "route step 2 must NOT write to -s3"

    # Route step 2 → stage 3
    r2 = _queue(service, setup_id=setup_id, job_id="job-reg", completed_step_index=2, route=route)
    suffix2 = _run_id_suffix(r2.argv)
    assert suffix2 == "3", f"route step 3 → -s3, got -s{suffix2}"
    assert suffix2 != "4", "route step 3 must NOT write to -s4"

    # Route step 3 → stage 4 (must not crash with IndexError)
    r3 = _queue(
        service,
        setup_id=setup_id,
        job_id="job-reg",
        completed_step_index=3,
        route=route,
        current_stage_result=_success_result(sandbox_path="/tmp/sandbox/s3"),
    )
    suffix3 = _run_id_suffix(r3.argv)
    assert suffix3 == "4", f"route step 4 → -s4, got -s{suffix3}"

    # Route step 4 completes → migration_completed (not before)
    r4 = _queue(
        service,
        setup_id=setup_id,
        job_id="job-reg",
        completed_step_index=4,
        route=route,
        current_stage_result=_success_result(sandbox_path="/tmp/sandbox/s4"),
    )
    assert r4.status == "completed", (
        f"migration should be completed after route step 4, got status={r4.status}"
    )
    assert r4.reason == "migration_completed"

    conn.close()


def test_route_step_stage_index_matches_for_non_base_source(tmp_path: Path) -> None:
    """For routes that don't start at 2.1 (e.g. 2.7 → 4.0), included_stages
    and route_steps have the same length, so the old indexing happened to
    work. This test ensures the fix doesn't break that case.
    """
    conn = _connection(tmp_path)
    setup_repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(setup_repo)

    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    service = V2StageProgressionService(setup_repo, command_repo)

    assert len(route.route_steps) == 3
    assert route.included_stages == (2, 3, 4)
    # For non-base source, route_step_index != stage_index:
    #   route step 1 → stage 2, route step 2 → stage 3, route step 3 → stage 4
    assert [s.stage_index for s in route.route_steps] == [2, 3, 4]

    # Route step 1 (stage 2) completes → route step 2 (stage 3)
    r1 = _queue(service, setup_id=setup_id, job_id="job-27", completed_step_index=1, route=route)
    assert r1.to_stage == 3
    assert _run_id_suffix(r1.argv) == "3"

    # Route step 2 (stage 3) completes → route step 3 (stage 4, requires stage 3 evidence)
    r2 = _queue(
        service,
        setup_id=setup_id,
        job_id="job-27",
        completed_step_index=2,
        route=route,
        current_stage_result=_success_result(sandbox_path="/tmp/sandbox/s2"),
    )
    assert r2.to_stage == 4
    assert _run_id_suffix(r2.argv) == "4"

    # Route step 3 (stage 4) completes → migration_completed
    r3 = _queue(
        service,
        setup_id=setup_id,
        job_id="job-27",
        completed_step_index=3,
        route=route,
        current_stage_result=_success_result(sandbox_path="/tmp/sandbox/s3"),
    )
    assert r3.status == "completed"
    assert r3.reason == "migration_completed"

    conn.close()
