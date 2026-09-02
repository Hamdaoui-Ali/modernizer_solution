import json
from pathlib import Path

import yaml

from migration_factory.orchestrator.artifact_validation import (
    validate_analysis_artifacts,
    validate_assessment_artifacts,
    validate_planning_artifacts,
)
from migration_factory.orchestrator.state import MigrationState, build_initial_state


def _state(tmp_path: Path) -> MigrationState:
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
        ai_hub_path=str(tmp_path / "ai-hub"),
        profile_id="java17",
    )
    Path(state["analysis_dir"]).mkdir(parents=True)
    Path(state["planning_dir"]).mkdir(parents=True)
    Path(state["assessment_dir"]).mkdir(parents=True)
    return state


def test_missing_analysis_artifact_blocks(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _write_analysis_artifacts(state)
    (Path(state["analysis_dir"]) / "dependency_graph.json").unlink()

    result = validate_analysis_artifacts(state)

    assert result.valid is False
    assert result.blockers == ["Missing required artifact: dependency_graph.json"]


def test_invalid_schema_backed_artifact_blocks(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _write_analysis_artifacts(state)
    _write_json(Path(state["analysis_dir"]) / "analysis_report.json", {"schema_version": "bad"})

    result = validate_analysis_artifacts(state)

    assert result.valid is False
    assert any("Invalid artifact schema for analysis_report.json" in blocker for blocker in result.blockers)


def test_source_modified_true_blocks(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _write_analysis_artifacts(state, source_modified=True)

    result = validate_analysis_artifacts(state)

    assert result.valid is False
    assert "read_only_verification.json source_modified must be false" in result.blockers


def test_java17_analysis_artifacts_allow_boot4_java21_rewrite_signals(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["profile_id"] = "springboot-2.7-to-3.5-java17"
    _write_analysis_artifacts(state)
    _write_rewrite_impact_summary(
        state,
        {
            "boot_2_to_4_gap": False,
            "boot4_target": False,
            "java_8_to_21_gap": False,
            "java_21_target": False,
        },
    )

    result = validate_analysis_artifacts(state)

    assert result.valid is True
    assert result.blockers == []


def test_analysis_artifacts_reject_unknown_rewrite_migration_signal(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _write_analysis_artifacts(state)
    _write_rewrite_impact_summary(state, {"unrelated_future_signal": True})

    result = validate_analysis_artifacts(state)

    assert result.valid is False
    assert any(
        "Invalid artifact schema for rewrite_impact_summary.json" in blocker
        and "unrelated_future_signal" in blocker
        for blocker in result.blockers
    )


def test_assessment_not_ready_blocks(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _write_assessment_artifacts(state, approval_readiness="BLOCKED")

    result = validate_assessment_artifacts(state)

    assert result.valid is False
    assert "assessment_report.json approval_readiness must be READY_FOR_REVIEW" in result.blockers


def test_assessment_execution_claim_true_blocks(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _write_assessment_artifacts(state, execution_claims={"migrated_tests_executed": True})

    result = validate_assessment_artifacts(state)

    assert result.valid is False
    assert "assessment_report.json execution claim migrated_tests_executed must be false" in result.blockers


def test_valid_planning_artifacts_pass(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _write_planning_artifacts(state)

    result = validate_planning_artifacts(state)

    assert result.valid is True
    assert set(result.artifact_refs) == {
        "migration_plan.yaml",
        "migration_units.yaml",
        "plan_summary.md",
        "approval_request.json",
        "plan_validation_report.json",
    }
    assert result.blockers == []


def _write_analysis_artifacts(state: MigrationState, *, source_modified: bool = False) -> None:
    analysis_dir = Path(state["analysis_dir"])
    _write_json(
        analysis_dir / "analysis_report.json",
        {
            "schema_version": "1.0.0",
            "run_id": state["run_id"],
            "status": "PASS",
            "artifact_refs": {"self": "analysis_report.json"},
        },
    )
    _write_json(analysis_dir / "dependency_graph.json", {"nodes": [], "edges": []})
    _write_json(analysis_dir / "test_inventory.json", {"tests": []})
    (analysis_dir / "analysis_summary.md").write_text("# Analysis\n", encoding="utf-8")
    _write_json(
        analysis_dir / "read_only_verification.json",
        {
            "schema_version": "1.0.0",
            "run_id": state["run_id"],
            "agent": "analysis_agent",
            "phase": "analysis",
            "status": "PASS",
            "paths": {
                "legacy_root": state["legacy_app_path"],
                "modernized_root": state["modernized_app_path"],
                "artifact": "read_only_verification.json",
            },
            "allowed_write_roots": [state["analysis_dir"]],
            "checks": {
                "legacy_tree_unchanged": True,
                "modernized_source_unchanged": True,
                "ignored_generated_paths": [],
            },
            "violations": [],
            "source_modified": source_modified,
            "artifact_refs": {"self": "read_only_verification.json"},
        },
    )


def _write_planning_artifacts(state: MigrationState) -> None:
    planning_dir = Path(state["planning_dir"])
    _write_yaml(
        planning_dir / "migration_plan.yaml",
        {
            "schema_version": "1.0.0",
            "run_id": state["run_id"],
            "status": "PASS",
            "risk": "LOW",
            "artifact_refs": {"self": "migration_plan.yaml"},
        },
    )
    _write_yaml(
        planning_dir / "migration_units.yaml",
        {
            "schema_version": "1.0.0",
            "run_id": state["run_id"],
            "status": "PASS",
            "artifact_refs": {"self": "migration_units.yaml"},
            "units": [],
        },
    )
    (planning_dir / "plan_summary.md").write_text("# Plan\n", encoding="utf-8")
    _write_json(
        planning_dir / "approval_request.json",
        {
            "schema_version": "1.0.0",
            "run_id": state["run_id"],
            "agent": "planning_agent",
            "phase": "approval",
            "status": "PASS",
            "profile": "java17",
            "requires_human_approval": True,
            "decision_options": ["approved", "rejected", "replan_required"],
            "recommended_decision": None,
            "units_to_execute": [],
            "blockers": [],
            "warnings": [],
            "artifact_refs": {"self": "approval_request.json"},
        },
    )
    _write_json(planning_dir / "plan_validation_report.json", {"valid": True})


def _write_rewrite_impact_summary(
    state: MigrationState, migration_signal_overrides: dict[str, bool]
) -> None:
    signals = {
        "api_or_boot_upgrade": True,
        "javax_removed": True,
        "boot_2_to_3_gap": True,
        "boot_2_to_4_gap": False,
        "boot4_target": False,
        "java_11_to_17_gap": True,
        "java_8_to_21_gap": False,
        "java_21_target": False,
        "javax_present": True,
        "security_config_touched": False,
        "datasource_config_touched": False,
    }
    signals.update(migration_signal_overrides)
    _write_json(
        Path(state["analysis_dir"]) / "rewrite_impact_summary.json",
        {
            "schema_version": "1.0.0",
            "run_id": state["run_id"],
            "agent": "analysis_agent",
            "phase": "analysis",
            "status": "PASS",
            "overall_impact": "MEDIUM",
            "changed_files": ["src/main/java/A.java"],
            "high_risk_files": ["src/main/java/A.java"],
            "migration_signals": signals,
            "blocked_reasons": [],
            "source_modified": False,
            "artifact_refs": {"self": "rewrite_impact_summary.json"},
        },
    )


def _write_assessment_artifacts(
    state: MigrationState,
    *,
    approval_readiness: str = "READY_FOR_REVIEW",
    execution_claims: dict[str, bool] | None = None,
) -> None:
    claims = {
        "transformation_executed": False,
        "openrewrite_apply_executed": False,
        "migrated_build_executed": False,
        "migrated_tests_executed": False,
        "final_migration_executed": False,
    }
    claims.update(execution_claims or {})
    assessment_dir = Path(state["assessment_dir"])
    _write_json(
        assessment_dir / "assessment_report.json",
        {
            "schema_version": "1.0.0",
            "run_id": state["run_id"],
            "agent": "assessment",
            "phase": "assessment",
            "status": "PASS",
            "profile": "java17",
            "overall_risk": "LOW",
            "source_stack": {},
            "target_stack": {},
            "analysis": {"status": "PASS", "artifact_ref": "../analysis/analysis_report.json"},
            "planning": {
                "status": "PASS",
                "validation_status": "PASS",
                "executable": True,
                "artifact_ref": "../planning/migration_plan.yaml",
            },
            "openrewrite_dry_run": {
                "status": "SKIPPED",
                "overall_impact": "none",
                "counts": {},
                "artifact_ref": None,
            },
            "migration_units": {
                "count": 0,
                "units": [],
                "artifact_ref": "../planning/migration_units.yaml",
            },
            "blockers": [],
            "warnings": [],
            "copilot": {"status": "SKIPPED", "artifact_ref": None},
            "approval_readiness": {
                "status": approval_readiness,
                "requires_human_approval": True,
                "recommended_decision": None,
                "artifact_ref": "../planning/approval_request.json",
            },
            "read_only_verification": {
                "status": "PASS",
                "source_modified": False,
                "artifact_ref": "../analysis/read_only_verification.json",
            },
            "next_recommended_phase": "human_approval",
            "execution_claims": claims,
            "artifact_refs": {"self": "assessment_report.json"},
        },
    )
    (assessment_dir / "assessment_summary.md").write_text("# Assessment\n", encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_yaml(path: Path, payload: object) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
