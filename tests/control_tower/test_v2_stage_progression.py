"""Tests for V2 stage auto-progression."""

import json
import sqlite3
from pathlib import Path
import pytest

from migration_factory.control_tower.application.v2_stage_progression import (
    V2StageProgressionService,
    STAGE_CONFIG,
    RUNNER_MODULE,
    compute_profile_route,
    next_required_stage,
    is_stage_included_in_route,
    is_stage_excluded_from_route,
    is_target_reached,
    project_route_steps,
    route_step_to_dict,
    route_to_dict,
    build_skipped_stage_ledger,
    route_checksum,
    get_stop_condition,
    get_all_stop_conditions,
    evaluate_auto_continue,
    AutoContinueDecision,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqliteRunConfigurationRepository,
)


def _create_setup(repo):
    service = V2SetupService(repo)
    req = CreateSetupRequest(
        run_name="test-progression",
        legacy_app_path="/tmp/legacy",
        output_parent_path="/tmp/output",
        ai_hub_path="/tmp/ai-hub",
        java11_home="/usr/lib/jvm/java-11",
        java17_home="/usr/lib/jvm/java-17",
        java21_home="/usr/lib/jvm/java-21",
        maven_cmd="/usr/bin/mvn",
    )
    return service.create_setup(req).setup_id


def _save_successful_stage3_command(command_repo: SqliteV2CommandRepository, *, job_id: str = "job-1") -> None:
    now = utc_now_text()
    command_repo.save(
        V2StageCommandRecord(
            command_id="cmd-stage3",
            job_id=job_id,
            stage_index=3,
            manifest_checksum="checksum-stage3",
            argv_json=json.dumps(["python", "-m", RUNNER_MODULE, "--run-id", f"v2-{job_id[:8]}-s3"]),
            env_json="{}",
            status="completed",
            created_at=now,
            updated_at=now,
            result_json=json.dumps({
                "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
                "orchestration_status": "PASS",
                "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
                "build_status": "BUILD_PASSED_IN_SANDBOX",
                "test_status": "PASS",
                "sandbox_path": "/tmp/sandbox/stage3",
            }),
        )
    )


def test_queue_stage2_from_stage1(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test1.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_setup(repo)

    service = V2StageProgressionService(repo)
    result = service.queue_next_stage(
        job_id="job-1",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
    )

    assert result.to_stage == 2
    assert result.from_stage == 1
    assert result.status == "queued"
    assert "springboot-2.7-to-3.5-java17" in " ".join(result.argv)


def test_queue_stage3_from_stage2(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test2.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_setup(repo)

    service = V2StageProgressionService(repo)
    result = service.queue_next_stage(
        job_id="job-1",
        setup_id=setup_id,
        current_stage=2,
        sandbox_path="/tmp/sandbox/stage2",
    )

    assert result.to_stage == 3
    assert "springboot-3.5-java17-to-java21" in " ".join(result.argv)


def test_queue_stage4_from_stage3_with_successful_evidence(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test3.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(repo)
    _save_successful_stage3_command(command_repo)

    service = V2StageProgressionService(repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-1",
        setup_id=setup_id,
        current_stage=3,
        sandbox_path="/tmp/sandbox/stage3",
    )

    assert result.status == "queued"
    assert result.from_stage == 3
    assert result.to_stage == 4
    assert result.command_id
    assert "--run-id" in result.argv
    assert "v2-job-1-s4" in result.argv
    assert "--legacy" in result.argv
    assert "/tmp/sandbox/stage3" in result.argv
    assert "springboot-3.5-java21-to-4.0-java21" in " ".join(result.argv)

    commands = command_repo.list_by_job_and_stage("job-1", 4)
    assert len(commands) == 1
    assert commands[0].stage_index == 4
    assert "v2-job-1-s4" in commands[0].argv_json


def test_stage4_blocks_when_stage3_success_evidence_missing(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test3_missing.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(repo)

    service = V2StageProgressionService(repo, command_repo)
    with pytest.raises(ValueError, match="successful Stage 3 output evidence"):
        service.queue_next_stage(
            job_id="job-1",
            setup_id=setup_id,
            current_stage=3,
            sandbox_path="/tmp/sandbox/stage3",
        )


def test_argv_is_backend_owned(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test4.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_setup(repo)

    service = V2StageProgressionService(repo)
    result = service.queue_next_stage(
        job_id="job-1",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
    )

    assert RUNNER_MODULE in " ".join(result.argv)
    assert "--profile" in result.argv


def test_sandbox_path_is_input(tmp_path: Path) -> None:
    """The sandbox path from previous stage becomes the --legacy input."""
    conn = sqlite3.connect(str(tmp_path / "test5.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_setup(repo)

    service = V2StageProgressionService(repo)
    result = service.queue_next_stage(
        job_id="job-1",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1-output",
    )

    assert "/tmp/sandbox/stage1-output" in " ".join(result.argv)


def test_boot4_path_is_valid(tmp_path: Path) -> None:
    """Boot 4 is a valid stage target in the four-stage pipeline."""
    assert 4 in STAGE_CONFIG
    assert STAGE_CONFIG[4]["profile"] == "springboot-3.5-java21-to-4.0-java21"
    assert STAGE_CONFIG[4]["jdk_id"] == "java21"


def test_stage_profiles_are_correct() -> None:
    assert STAGE_CONFIG[2]["profile"] == "springboot-2.7-to-3.5-java17"
    assert STAGE_CONFIG[3]["profile"] == "springboot-3.5-java17-to-java21"
    assert STAGE_CONFIG[4]["profile"] == "springboot-3.5-java21-to-4.0-java21"
    assert STAGE_CONFIG[2]["jdk_id"] == "java17"
    assert STAGE_CONFIG[3]["jdk_id"] == "java21"
    assert STAGE_CONFIG[4]["jdk_id"] == "java21"


def test_missing_setup_rejected(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test6.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)

    service = V2StageProgressionService(repo)
    with pytest.raises(ValueError, match="not found"):
        service.queue_next_stage(
            job_id="job-1",
            setup_id="nonexistent",
            current_stage=1,
            sandbox_path="/tmp/sandbox",
        )


# ── AMF-264: Profile route tests ─────────────────────────────────


def test_valid_route_stops_at_target_springboot_35_java17() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java17")
    assert route.valid is True
    assert route.included_stages == (2,)
    assert route.excluded_stages == (3, 4)
    assert route.skipped_stages == ()
    assert is_target_reached(route, 2) is True
    assert is_stage_excluded_from_route(route, 3) is True
    assert is_stage_excluded_from_route(route, 4) is True


def test_valid_route_stops_at_target_springboot_35_java21() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java21")
    assert route.valid is True
    assert route.included_stages == (2, 3)
    assert route.excluded_stages == (4,)
    assert route.skipped_stages == ()
    assert is_target_reached(route, 2) is False
    assert is_target_reached(route, 3) is True
    assert is_stage_excluded_from_route(route, 4) is True


def test_valid_route_stops_at_target_boot_4() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    assert route.valid is True
    assert route.included_stages == (2, 3, 4)
    assert route.excluded_stages == ()
    assert route.skipped_stages == ()
    assert len(route.route_steps) == 3
    assert is_target_reached(route, 4) is True


def test_valid_route_from_boot21_to_boot27_has_one_route_step() -> None:
    route = compute_profile_route("springboot-2.1-java11", "springboot-2.7-java11")
    assert route.valid is True
    assert len(route.route_steps) == 1
    assert route.route_steps[0].source_profile == "springboot-2.1-java11"
    assert route.route_steps[0].target_profile == "springboot-2.7-java11"
    assert route.route_steps[0].runtime_profile == "springboot-2.1.6-to-2.7-java11"
    assert route.route_steps[0].catalog == "springboot-2.1.6-to-2.7-java11"
    assert route.route_steps[0].execution_jdk == "java11"
    assert is_target_reached(route, 1) is True


def test_valid_route_from_boot21_to_boot4_has_four_route_steps() -> None:
    route = compute_profile_route("springboot-2.1-java11", "springboot-4.0-java21")
    assert route.valid is True
    assert len(route.route_steps) == 4
    assert [
        (step.source_profile, step.target_profile, step.runtime_profile, step.execution_jdk)
        for step in route.route_steps
    ] == [
        ("springboot-2.1-java11", "springboot-2.7-java11", "springboot-2.1.6-to-2.7-java11", "java11"),
        ("springboot-2.7-java11", "springboot-3.5-java17", "springboot-2.7-to-3.5-java17", "java17"),
        ("springboot-3.5-java17", "springboot-3.5-java21", "springboot-3.5-java17-to-java21", "java21"),
        ("springboot-3.5-java21", "springboot-4.0-java21", "springboot-3.5-java21-to-4.0-java21", "java21"),
    ]
    assert is_target_reached(route, 4) is True


def test_higher_profile_exists_but_excluded() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java17")
    assert route.valid is True
    assert 4 in route.excluded_stages
    assert is_stage_included_in_route(route, 4) is False
    assert is_stage_excluded_from_route(route, 4) is True


def test_start_from_springboot_35_java17_to_java21() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-3.5-java21")
    assert route.valid is True
    assert route.included_stages == (3,)
    assert route.skipped_stages == (2,)
    assert route.excluded_stages == (4,)


def test_stage_progression_still_routes_boot35_java17_to_java21_as_stage3_only() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-3.5-java21")

    assert route.valid is True
    assert route.included_stages == (3,)
    assert route.skipped_stages == (2,)
    assert route.excluded_stages == (4,)
    assert next_required_stage(route, current_stage=1) == 3


def test_project_route_steps_uses_execution_stage_for_offset_two_step_route() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java21")
    stages = (
        {"stage_index": 1, "chain_status": "running", "artifact_refs": ["stage-1-artifact"]},
        {"stage_index": 2, "chain_status": "failed", "artifact_refs": ["wrong-stage-2-artifact"]},
        {"stage_index": 3, "chain_status": "completed", "evidence_refs": ["stage-3-evidence"]},
        {"stage_index": 4, "chain_status": "pending"},
    )

    projected = project_route_steps(route, stages=stages)

    assert [(step.route_step_index, step.stage_index, step.status) for step in projected] == [
        (1, 2, "running"),
        (2, 3, "completed"),
    ]
    assert projected[0].artifact_refs == ("stage-1-artifact",)
    assert projected[1].evidence_refs == ("stage-3-evidence",)
    assert route_step_to_dict(projected[0], include_execution_stage=True)["execution_stage_index"] == 1
    assert route_step_to_dict(projected[1], include_execution_stage=True)["execution_stage_index"] == 3


def test_start_from_springboot_35_java17_to_boot_4() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-4.0-java21")
    assert route.valid is True
    assert route.included_stages == (3, 4)
    assert route.skipped_stages == (2,)


def test_invalid_reversed_pair_rejected() -> None:
    route = compute_profile_route("springboot-3.5-java21", "springboot-3.5-java17")
    assert route.valid is False
    assert route.reason == "target profile must be higher than source profile"


def test_invalid_same_profile_rejected() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-3.5-java17")
    assert route.valid is False
    assert route.reason == "source and target profiles must be different"


def test_invalid_unknown_source_rejected() -> None:
    route = compute_profile_route("unknown-profile", "springboot-3.5-java17")
    assert route.valid is False
    assert "source profile" in route.reason.lower()


def test_invalid_unknown_target_rejected() -> None:
    route = compute_profile_route("springboot-2.7-java11", "unknown-target")
    assert route.valid is False
    assert "target profile" in route.reason.lower()


def test_route_metadata_includes_all_stages() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java21")
    d = route_to_dict(route)
    assert d["source_profile"] == "springboot-2.7-java11"
    assert d["target_profile"] == "springboot-3.5-java21"
    assert d["included_stages"] == [2, 3]
    assert d["excluded_stages"] == [4]
    assert d["skipped_stages"] == []
    assert d["valid"] is True

    route2 = compute_profile_route("springboot-3.5-java17", "springboot-4.0-java21")
    d2 = route_to_dict(route2)
    assert d2["included_stages"] == [3, 4]
    assert d2["skipped_stages"] == [2]


def test_route_metadata_projects_skipped_stage_ledger() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-4.0-java21")
    d = route_to_dict(
        route,
        job_id="job-123",
        evidence_ref="artifact:source-profile-detection",
        evidence_checksum="sha256:detection",
        created_at="2026-06-27T10:00:00Z",
    )

    assert d["route_checksum"] == route_checksum(route)
    assert d["skipped_stages"] == [2]
    assert d["skipped_stage_ledger"] == [
        {
            "job_id": "job-123",
            "source_profile": "springboot-3.5-java17",
            "target_profile": "springboot-4.0-java21",
            "skipped_stage_index": 2,
            "skipped_stage_name": "Stage 2",
            "skipped_stage_profile": "springboot-2.7-to-3.5-java17",
            "reason": (
                "Skipped because source profile "
                "'springboot-3.5-java17' starts after stage 2."
            ),
            "evidence_ref": "artifact:source-profile-detection",
            "evidence_checksum": "sha256:detection",
            "route_checksum": route_checksum(route),
            "artifact_checksum": "",
            "created_at": "2026-06-27T10:00:00Z",
        }
    ]


def test_skipped_stage_ledger_uses_existing_route_progression() -> None:
    route = compute_profile_route("springboot-3.5-java21", "springboot-4.0-java21")
    ledger = build_skipped_stage_ledger(
        route,
        job_id="job-456",
        created_at="2026-06-27T10:00:00Z",
    )

    assert route.included_stages == (4,)
    assert route.skipped_stages == (2, 3)
    assert [entry.skipped_stage_index for entry in ledger] == [2, 3]
    assert {entry.route_checksum for entry in ledger} == {route_checksum(route)}


def test_included_stages_do_not_contain_stages_beyond_target() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java17")
    assert 3 not in route.included_stages
    assert 4 not in route.included_stages
    assert 2 in route.included_stages


# ── AMF-250: Safe auto-continue rule tests ────────────────────────


def test_analysis_checkpoint_stops_progression() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=1,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )
    assert decision.should_continue is False
    assert decision.stop_condition == "analysis_checkpoint"


def test_planning_checkpoint_stops_progression() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=2,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )
    assert decision.should_continue is False
    assert decision.stop_condition == "planning_checkpoint"


def test_build_failure_stops_progression() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=3,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
        result={"build_status": "BUILD_FAILED_IN_SANDBOX", "test_status": "PASS"},
    )
    assert decision.should_continue is False
    assert decision.stop_condition == "build_failed"


def test_test_failure_stops_progression() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=3,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
        result={"build_status": "BUILD_PASSED_IN_SANDBOX", "test_status": "TEST_FAILED"},
    )
    assert decision.should_continue is False
    assert decision.stop_condition == "test_failed"


def test_risk_detected_stops_progression() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=3,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
        has_risk=True,
    )
    assert decision.should_continue is False
    assert decision.stop_condition == "risk_detected"


def test_reviewer_failure_stops_progression() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=3,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
        has_reviewer_failure=True,
    )
    assert decision.should_continue is False
    assert decision.stop_condition == "reviewer_failed"


def test_stale_artifact_stops_progression() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=3,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
        has_stale_artifact=True,
    )
    assert decision.should_continue is False
    assert decision.stop_condition == "stale_artifact"


def test_approval_required_stops_progression() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=3,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
        has_approval_required=True,
    )
    assert decision.should_continue is False
    assert decision.stop_condition == "approval_required"


def test_target_reached_stops_progression() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java17")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=2,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
    )
    assert decision.should_continue is False
    assert decision.stop_condition == "target_reached"


def test_clean_green_stage3_auto_continues_to_4() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
    decision = evaluate_auto_continue(
        current_stage=3,
        route=route,
        policy=StageContinuationPolicy.AUTO_ON_GREEN,
        result={
            "build_status": "BUILD_PASSED_IN_SANDBOX",
            "test_status": "PASS",
        },
    )
    assert decision.should_continue is True
    assert decision.stop_condition is None


# ── AMF-251: Stop-condition matrix tests ──────────────────────────


def test_all_stop_conditions_defined() -> None:
    conditions = get_all_stop_conditions()
    names = {c.name for c in conditions}
    expected = {
        "analysis_checkpoint",
        "planning_checkpoint",
        "risk_detected",
        "build_failed",
        "test_failed",
        "target_reached",
        "stale_artifact",
        "reviewer_failed",
        "approval_required",
        "user_stopped",
        "profile_incompatible",
        "target_overshoot_blocked",
    }
    assert names == expected


def test_stop_condition_allowed_actions() -> None:
    cond = get_stop_condition("analysis_checkpoint")
    assert cond is not None
    assert "continue" in cond.allowed_actions
    assert "request_modification" in cond.allowed_actions
    assert "stop" in cond.allowed_actions

    build_cond = get_stop_condition("build_failed")
    assert build_cond is not None
    assert "request_repair_review_future" in build_cond.allowed_actions
    assert build_cond.repair_eligible is True

    target_cond = get_stop_condition("target_reached")
    assert target_cond is not None
    assert "stop" in target_cond.allowed_actions
    assert "download_artifact" in target_cond.allowed_actions


def test_restorable_stop_conditions() -> None:
    restorable = [c for c in get_all_stop_conditions() if c.restorable]
    restorable_names = {c.name for c in restorable}
    assert "analysis_checkpoint" in restorable_names
    assert "planning_checkpoint" in restorable_names
    assert "user_stopped" in restorable_names
    assert "approval_required" in restorable_names
    assert "build_failed" not in restorable_names


def test_repair_eligible_only_build_test_failure() -> None:
    repair = [c for c in get_all_stop_conditions() if c.repair_eligible]
    repair_names = {c.name for c in repair}
    assert repair_names == {"build_failed", "test_failed"}


def test_unknown_condition_returns_none() -> None:
    assert get_stop_condition("nonexistent_condition") is None


# ── AMF-265: Stop-at-target behavior tests ────────────────────────


def test_target_reached_after_stage3_for_boot35_target() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java21")
    assert is_target_reached(route, 2) is False
    assert is_target_reached(route, 3) is True
    assert is_target_reached(route, 4) is True


def test_stage_queue_blocked_when_target_reached(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test_target.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_setup(repo)

    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java17")
    service = V2StageProgressionService(repo)
    result = service.queue_next_stage(
        job_id="job-target",
        setup_id=setup_id,
        current_stage=2,
        sandbox_path="/tmp/sandbox/stage2",
        profile_route=route,
    )
    assert result.status == "blocked"
    assert result.reason == "target_reached"


def test_stage_queue_blocked_on_invalid_route(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test_invalid_route.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_setup(repo)

    route = compute_profile_route("springboot-3.5-java21", "springboot-3.5-java17")
    assert route.valid is False
    service = V2StageProgressionService(repo)
    result = service.queue_next_stage(
        job_id="job-invalid",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        profile_route=route,
    )
    assert result.status == "blocked"
    assert result.reason == "profile_incompatible"
    assert result.command_id is None
    assert result.argv == ()


def test_stage3_continues_to_4_when_target_is_boot4(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test_boot4.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(repo)
    _save_successful_stage3_command(command_repo)

    route = compute_profile_route("springboot-2.7-java11", "springboot-4.0-java21")
    service = V2StageProgressionService(repo, command_repo)
    result = service.queue_next_stage(
        job_id="job-1",
        setup_id=setup_id,
        current_stage=3,
        sandbox_path="/tmp/sandbox/stage3",
        profile_route=route,
    )
    assert result.status == "queued"
    assert result.to_stage == 4


def test_target_reached_emits_blocked_status() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java17")
    d = route_to_dict(route)
    assert d["excluded_stages"] == [3, 4]
    assert is_target_reached(route, 2) is True


# ── AMF-268: Target-overshoot prevention tests ────────────────────


def test_resume_after_target_reached_is_blocked(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "test_resume_target.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    setup_id = _create_setup(repo)

    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java17")
    service = V2StageProgressionService(repo)
    result = service.queue_next_stage(
        job_id="job-resume-target",
        setup_id=setup_id,
        current_stage=2,
        sandbox_path="/tmp/sandbox/stage2",
        profile_route=route,
    )
    assert result.status == "blocked"
    assert result.reason == "target_reached"
    assert result.argv == ()
    assert result.command_id is None


def test_higher_profile_exists_but_must_not_execute() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java17")
    assert is_stage_excluded_from_route(route, 3)
    assert is_stage_excluded_from_route(route, 4)
    assert not is_stage_included_in_route(route, 3)
    assert not is_stage_included_in_route(route, 4)


def test_excluded_skipped_stages_visible_in_route_metadata() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-4.0-java21")
    d = route_to_dict(route)
    assert d["included_stages"] == [3, 4]
    assert d["skipped_stages"] == [2]
    assert d["excluded_stages"] == []
    assert 2 not in d["included_stages"]


def test_profile_metadata_preserves_source_and_target() -> None:
    route = compute_profile_route("springboot-2.7-java11", "springboot-3.5-java21")
    d = route_to_dict(route)
    assert d["source_profile"] == "springboot-2.7-java11"
    assert d["target_profile"] == "springboot-3.5-java21"
    assert d["source_level"] == 1
    assert d["target_level"] == 3


# ── AMF-274 / F4-T6: already-modernized app start routes ───────────


def test_next_required_stage_starts_after_current_profile_for_boot35_java17() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-4.0-java21")

    assert route.valid is True
    assert route.skipped_stages == (2,)
    assert route.included_stages == (3, 4)
    assert next_required_stage(route, current_stage=1) == 3
    assert next_required_stage(route, current_stage=2) == 3
    assert 2 not in route.included_stages


def test_next_required_stage_starts_after_current_profile_for_boot35_java21() -> None:
    route = compute_profile_route("springboot-3.5-java21", "springboot-4.0-java21")

    assert route.valid is True
    assert route.skipped_stages == (2, 3)
    assert route.included_stages == (4,)
    assert next_required_stage(route, current_stage=1) == 4
    assert next_required_stage(route, current_stage=2) == 4
    assert next_required_stage(route, current_stage=3) == 4
    assert 2 not in route.included_stages
    assert 3 not in route.included_stages


def test_already_modernized_java21_source_queues_stage4_without_stage3_evidence(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(
        str(tmp_path / "test_already_java21_stage4.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(repo)
    route = compute_profile_route("springboot-3.5-java21", "springboot-4.0-java21")

    result = V2StageProgressionService(repo, command_repo).queue_next_stage(
        job_id="job-java21-source",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage1",
        profile_route=route,
    )

    assert result.status == "queued"
    assert result.to_stage == 4
    assert "springboot-3.5-java21-to-4.0-java21" in " ".join(result.argv)
    assert command_repo.list_by_job_and_stage("job-java21-source", 2) == ()
    assert command_repo.list_by_job_and_stage("job-java21-source", 3) == ()
    assert len(command_repo.list_by_job_and_stage("job-java21-source", 4)) == 1


def test_queue_stage4_from_boot35_java17_route_step_queues_boot35_java21_to_boot40_java21(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(
        str(tmp_path / "test_route_step_progression.sqlite3"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    setup_id = _create_setup(repo)
    route = compute_profile_route("springboot-3.5-java17", "springboot-4.0-java21")
    current_result = {
        "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "orchestration_status": "PASS",
        "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "PASS",
        "sandbox_path": "/tmp/sandbox/stage-1",
        "profile_id": "springboot-3.5-java17-to-java21",
        "route_step_index": 1,
    }

    result = V2StageProgressionService(repo, command_repo).queue_next_stage(
        job_id="job-route-step",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/sandbox/stage-1",
        current_stage_result=current_result,
        profile_route=route,
        current_route_step_index=1,
    )

    assert result.status == "queued"
    assert result.to_stage == 4
    assert "springboot-3.5-java21-to-4.0-java21" in " ".join(result.argv)
    assert "springboot-3.5-java17-to-java21" not in " ".join(result.argv)

    queued_commands = command_repo.list_by_job_and_stage("job-route-step", 4)
    assert len(queued_commands) == 1
    env = json.loads(queued_commands[0].env_json)
    assert env.get("ROUTE_STEP_INDEX") == "2"
    assert env.get("ROUTE_STEP_RUNTIME_PROFILE") == "springboot-3.5-java21-to-4.0-java21"


def test_next_required_stage_returns_none_after_later_target_reached() -> None:
    route = compute_profile_route("springboot-3.5-java17", "springboot-3.5-java21")

    assert route.valid is True
    assert route.included_stages == (3,)
    assert next_required_stage(route, current_stage=3) is None


def test_next_required_stage_returns_none_for_incompatible_source_target_pair() -> None:
    route = compute_profile_route("springboot-3.5-java21", "springboot-3.5-java17")

    assert route.valid is False
    assert route.reason == "target profile must be higher than source profile"
    assert next_required_stage(route, current_stage=1) is None


def test_queue_next_stage_ignores_stale_stage2_command_with_wrong_profile(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "stale_stage2.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    repo = SqliteV2SetupRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    run_config_repo = SqliteRunConfigurationRepository(conn)
    setup_id = _create_setup(repo)

    payload_json = json.dumps(
        {"source_profile": "springboot-2.1-java11", "target_profile": "springboot-4.0-java21"},
        separators=(",", ":"),
    )
    conn.execute(
        """INSERT INTO run_configurations (
            run_configuration_id, job_id, schema_version, runner_profile_id, runner_profile_version,
            pipeline_id, pipeline_version, target_proof_level, enabled_gates_json, policy_json,
            payload_json, payload_checksum, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "rc-stale-stage2",
            "job-stale-stage2",
            "1.0.0",
            "runner",
            "1",
            "pipeline",
            "1",
            "BUILD_TEST_VERIFIED",
            "[]",
            "{}",
            payload_json,
            "checksum-stale-stage2",
            utc_now_text(),
        ),
    )

    now = utc_now_text()
    command_repo.save(
        V2StageCommandRecord(
            command_id="stale-stage2",
            job_id="job-stale-stage2",
            stage_index=2,
            manifest_checksum="v2-stage2",
            argv_json=json.dumps(
                [
                    "python",
                    "-m",
                    RUNNER_MODULE,
                    "--run-id",
                    "v2-job-stal-s2",
                    "--legacy",
                    "/tmp/stage1-sandbox",
                    "--modernized",
                    "/tmp/output",
                    "--ai-hub",
                    "/tmp/ai-hub",
                    "--profile",
                    "springboot-2.1.6-to-2.7-java11",
                    "--mode",
                    "full_sandbox_migration",
                ]
            ),
            env_json="{}",
            status="manifest_ready",
            created_at=now,
            updated_at=now,
            result_json=None,
        )
    )

    service = V2StageProgressionService(repo, command_repo, run_config_repo=run_config_repo)
    result = service.queue_next_stage(
        job_id="job-stale-stage2",
        setup_id=setup_id,
        current_stage=1,
        sandbox_path="/tmp/stage1-sandbox",
    )

    assert result.status == "queued"
    assert result.command_id != "stale-stage2"
    assert "springboot-2.7-to-3.5-java17" in " ".join(result.argv)

    stage2_commands = command_repo.list_by_job_and_stage("job-stale-stage2", 2)
    assert len(stage2_commands) == 2
    assert stage2_commands[0].command_id == result.command_id
    assert "springboot-2.7-to-3.5-java17" in stage2_commands[0].argv_json
