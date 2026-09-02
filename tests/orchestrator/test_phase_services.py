from dataclasses import fields
from types import SimpleNamespace
from pathlib import Path

import pytest

from migration_factory.orchestrator.phase_services import (
    PhaseServices,
    _merge_repair_updates,
    default_phase_services,
    run_analysis_phase,
    run_sandbox_transform_phase,
)
from migration_factory.orchestrator.state import MigrationState, build_initial_state


def _state(tmp_path: Path) -> MigrationState:
    return build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
        ai_hub_path=str(tmp_path / "ai-hub"),
        profile_id="java17",
    )


def test_default_phase_services_has_exact_three_phases() -> None:
    services = default_phase_services()

    assert [field.name for field in fields(PhaseServices)] == [
        "run_analysis_phase",
        "run_planning_phase",
        "run_assessment_phase",
    ]
    assert services.run_analysis_phase is run_analysis_phase
    assert callable(services.run_planning_phase)
    assert callable(services.run_assessment_phase)


def test_fake_phase_services_injection_works(tmp_path: Path) -> None:
    def fake_analysis(state: MigrationState) -> MigrationState:
        return {**state, "analysis_status": "PASS", "current_phase": "analysis"}

    def fake_planning(state: MigrationState) -> MigrationState:
        return {**state, "planning_status": "PASS", "current_phase": "planning"}

    def fake_assessment(state: MigrationState) -> MigrationState:
        return {**state, "assessment_status": "PASS", "current_phase": "assessment"}

    services = PhaseServices(
        run_analysis_phase=fake_analysis,
        run_planning_phase=fake_planning,
        run_assessment_phase=fake_assessment,
    )

    state = services.run_assessment_phase(
        services.run_planning_phase(services.run_analysis_phase(_state(tmp_path)))
    )

    assert state["analysis_status"] == "PASS"
    assert state["planning_status"] == "PASS"
    assert state["assessment_status"] == "PASS"
    assert state["current_phase"] == "assessment"


def test_phase_services_has_no_transformation_service_or_attr() -> None:
    services = default_phase_services()

    assert "run_transformation_phase" not in [field.name for field in fields(PhaseServices)]
    assert not hasattr(services, "run_transformation_phase")


def test_phase_failure_sets_fail_and_blocker_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_analysis(state: MigrationState) -> MigrationState:
        raise RuntimeError("analysis exploded")

    monkeypatch.setattr(
        "migration_factory.orchestrator.phase_services._run_analysis_service",
        fail_analysis,
    )

    state = run_analysis_phase(_state(tmp_path))

    assert state["analysis_status"] == "FAIL"
    assert state["current_phase"] == "analysis"
    assert state["current_unit"] == "analysis"
    assert state["errors"] == ["analysis phase failed: analysis exploded"]
    assert state["blockers"] == ["analysis phase failed: analysis exploded"]


def test_repair_merge_does_not_call_legacy_copilot_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import migration_factory.repair_loop.orchestrator as legacy_repair

    def fail_if_called(*args, **kwargs):
        raise AssertionError("legacy Copilot repair loop must not be called")

    monkeypatch.setattr(legacy_repair, "run_post_failure_repair_loop", fail_if_called)

    result = _merge_repair_updates(
        {
            **_state(tmp_path),
            "artifact_refs": {"failure": "failure.json"},
            "final_status": "BUILD_FAILED",
            "stop_reason": "build failed",
        },
        h2_startup_report={"status": "FAILED"},
    )

    assert result["repair_loop_status"] == "REPAIR_REVIEW_REQUIRED"
    assert result["repair_blocker"] == "f5_reviewed_repair_required"
    assert result["artifact_refs"] == {"failure": "failure.json"}


def test_sandbox_transform_phase_resolves_runtime_profile_from_route_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}

    def fake_apply_approved_sandbox_transform(*, profile: str, **kwargs):
        captured["profile"] = profile
        run_dir = Path(kwargs["run_dir"])
        sandbox_path = run_dir / "workspaces" / "sandbox"
        log_file = run_dir / "logs" / "phase2_transform.log"
        sandbox_path.mkdir(parents=True, exist_ok=True)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("ok\n", encoding="utf-8")
        return SimpleNamespace(
            exit_code=0,
            status="TRANSFORM_APPLIED_IN_SANDBOX",
            message="ok",
            sandbox_path=sandbox_path,
            log_file=log_file,
            generated_plan=None,
            plugin_xml=None,
            ledger_file=None,
            build_status="BUILD_PASSED_IN_SANDBOX",
            test_status="TEST_PASSED",
            test_totals={},
            test_report_path=None,
            test_summary_path=None,
            test_log_path=None,
            test_phase="post_transform",
            dependency_policy_artifact_refs={},
            dependency_policy_report_path=None,
            dependency_policy_summary_path=None,
            dependency_policy_status="SKIPPED",
            dependency_policy_risks_count=0,
            dependency_policy_blockers_count=0,
            copilot_dependency_advisory_status="SKIPPED",
            policy_patch_applied=False,
        )

    monkeypatch.setattr(
        "migration_factory.transform_v1_after_approval.apply_approved_sandbox_transform",
        fake_apply_approved_sandbox_transform,
    )

    state = build_initial_state(
        run_id="run-route",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
        ai_hub_path=str(tmp_path / "ai-hub"),
        profile_id="springboot-2.7-java11",
        mode="full_sandbox_migration",
    )
    Path(state["legacy_app_path"]).mkdir(parents=True, exist_ok=True)
    Path(state["modernized_app_path"]).mkdir(parents=True, exist_ok=True)
    Path(state["ai_hub_path"]).mkdir(parents=True, exist_ok=True)
    Path(state["run_dir"]).mkdir(parents=True, exist_ok=True)
    state["approved_by"] = "reviewer"
    state["artifact_refs"] = {
        "approval_review": {
            "profile_metadata": {
                "source_profile": "springboot-3.5-java17",
                "target_profile": "springboot-3.5-java21",
            }
        }
    }

    result = run_sandbox_transform_phase(state)

    assert captured["profile"] == "springboot-3.5-java17-to-java21"
    assert result["profile_id"] == "springboot-3.5-java17-to-java21"
