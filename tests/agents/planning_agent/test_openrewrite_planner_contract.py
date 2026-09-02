import json
from pathlib import Path

import yaml

from migration_factory.agents.planning_agent.artifact_reader import (
    LoadedAnalysisArtifacts,
    load_analysis_artifacts,
)
from migration_factory.agents.planning_agent.node import planning_node
from migration_factory.agents.planning_agent.profile_compatibility import StackFingerprint
from migration_factory.agents.planning_agent.risk_classifier import classify_planning_risks
from migration_factory.contracts.planning_artifacts import OPTIONAL_ANALYSIS_INPUT_ARTIFACTS


OPENREWRITE_OPTIONAL_ARTIFACTS = (
    "rewrite_plugin_plan.json",
    "rewrite_impact_summary.json",
)


def _write_required_analysis_artifacts(analysis_dir: Path) -> None:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "analysis_report.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "inventory": {
                    "build_tool": "maven",
                    "java_version": "11",
                    "spring_boot_version": "2.7",
                    "javax_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    (analysis_dir / "dependency_graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}),
        encoding="utf-8",
    )
    (analysis_dir / "test_inventory.json").write_text(
        json.dumps({"tests": []}),
        encoding="utf-8",
    )
    (analysis_dir / "analysis_summary.md").write_text("analysis ok\n", encoding="utf-8")


def _write_profile(ai_hub_dir: Path) -> None:
    profiles_dir = ai_hub_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / "java17.yaml").write_text(
        """
source:
  java: 11
  spring_boot: 2.7
  build: maven
target:
  java: 17
  spring_boot: 3.5.14
  build: maven
rules: {}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _state(app_dir: Path, hub_dir: Path, run_id: str) -> dict[str, str]:
    return {
        "run_id": run_id,
        "profile": "java17",
        "modernized_app_path": str(app_dir),
        "ai_hub_path": str(hub_dir),
    }


def _loaded_with_openrewrite_impact(impact: str) -> LoadedAnalysisArtifacts:
    return LoadedAnalysisArtifacts(
        required={
            "analysis_report.json": {"status": "PASS"},
            "dependency_graph.json": {},
            "test_inventory.json": {},
            "analysis_summary.md": "analysis ok\n",
        },
        optional={
            "rewrite_impact_summary.json": {
                "overall_impact": impact,
                "requires_manual_review": impact == "HIGH",
                "blocked_reasons": ["Recipe cannot be selected safely."]
                if impact == "BLOCKED"
                else [],
            }
        },
        errors=[],
        ok=True,
    )


def _find_openrewrite_risk(result, impact: str):
    expected_code = f"OPENREWRITE_IMPACT_{impact}"
    return next((risk for risk in result.risks if risk.code == expected_code), None)


def test_openrewrite_artifacts_are_optional_analysis_inputs() -> None:
    for artifact_name in OPENREWRITE_OPTIONAL_ARTIFACTS:
        assert artifact_name in OPTIONAL_ANALYSIS_INPUT_ARTIFACTS


def test_openrewrite_artifacts_missing_still_loads_ok(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    run_id = "missing-openrewrite"
    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_required_analysis_artifacts(analysis_dir)

    loaded = load_analysis_artifacts(app_dir, run_id)

    assert loaded.ok is True
    assert loaded.missing_required == []
    assert not any(name in loaded.optional for name in OPENREWRITE_OPTIONAL_ARTIFACTS)


def test_openrewrite_invalid_json_records_optional_error_without_hard_failure(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    run_id = "invalid-openrewrite"
    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_required_analysis_artifacts(analysis_dir)
    for artifact_name in OPENREWRITE_OPTIONAL_ARTIFACTS:
        (analysis_dir / artifact_name).write_text("{not-json", encoding="utf-8")

    loaded = load_analysis_artifacts(app_dir, run_id)

    assert loaded.ok is True
    for artifact_name in OPENREWRITE_OPTIONAL_ARTIFACTS:
        assert artifact_name not in loaded.optional
        assert any(
            error.startswith(f"Optional artifact {artifact_name} failed to load:")
            for error in loaded.errors
        )


def test_openrewrite_valid_object_json_loads_as_optional_artifacts(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    run_id = "valid-openrewrite"
    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_required_analysis_artifacts(analysis_dir)
    plugin_plan = {"recipes": [{"name": "org.openrewrite.java.migrate.UpgradeToJava17"}]}
    impact_summary = {"overall_impact": "MEDIUM", "recipes": []}
    (analysis_dir / "rewrite_plugin_plan.json").write_text(
        json.dumps(plugin_plan),
        encoding="utf-8",
    )
    (analysis_dir / "rewrite_impact_summary.json").write_text(
        json.dumps(impact_summary),
        encoding="utf-8",
    )

    loaded = load_analysis_artifacts(app_dir, run_id)

    assert loaded.ok is True
    assert loaded.errors == []
    assert loaded.optional["rewrite_plugin_plan.json"] == plugin_plan
    assert loaded.optional["rewrite_impact_summary.json"] == impact_summary


def test_openrewrite_non_object_json_records_optional_error_only(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    run_id = "non-object-openrewrite"
    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_required_analysis_artifacts(analysis_dir)
    for artifact_name in OPENREWRITE_OPTIONAL_ARTIFACTS:
        (analysis_dir / artifact_name).write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    loaded = load_analysis_artifacts(app_dir, run_id)

    assert loaded.ok is True
    for artifact_name in OPENREWRITE_OPTIONAL_ARTIFACTS:
        assert artifact_name not in loaded.optional
        assert any(
            error
            == f"Optional artifact {artifact_name} must be JSON object."
            for error in loaded.errors
        )


def test_openrewrite_low_impact_is_info_and_keeps_plan_executable() -> None:
    result = classify_planning_risks(
        _loaded_with_openrewrite_impact("LOW"),
        StackFingerprint(build_tool="maven", java="11", spring_boot="2.7"),
    )

    risk = _find_openrewrite_risk(result, "LOW")
    assert risk is not None
    assert risk.severity == "INFO"
    assert result.ok is True


def test_openrewrite_medium_impact_is_warning() -> None:
    result = classify_planning_risks(
        _loaded_with_openrewrite_impact("MEDIUM"),
        StackFingerprint(build_tool="maven", java="11", spring_boot="2.7"),
    )

    risk = _find_openrewrite_risk(result, "MEDIUM")
    assert risk is not None
    assert risk.severity == "WARNING"
    assert result.ok is True


def test_openrewrite_high_impact_is_warning_with_manual_review_note() -> None:
    result = classify_planning_risks(
        _loaded_with_openrewrite_impact("HIGH"),
        StackFingerprint(build_tool="maven", java="11", spring_boot="2.7"),
    )

    risk = _find_openrewrite_risk(result, "HIGH")
    assert risk is not None
    assert risk.severity == "WARNING"
    assert "manual review" in risk.message.lower()
    assert result.ok is True


def test_openrewrite_blocked_impact_is_blocker_and_non_executable() -> None:
    result = classify_planning_risks(
        _loaded_with_openrewrite_impact("BLOCKED"),
        StackFingerprint(build_tool="maven", java="11", spring_boot="2.7"),
    )

    risk = _find_openrewrite_risk(result, "BLOCKED")
    assert risk is not None
    assert risk.severity == "BLOCKER"
    assert result.ok is False


def test_openrewrite_unknown_impact_is_warning() -> None:
    result = classify_planning_risks(
        _loaded_with_openrewrite_impact("UNKNOWN"),
        StackFingerprint(build_tool="maven", java="11", spring_boot="2.7"),
    )

    risk = _find_openrewrite_risk(result, "UNKNOWN")
    assert risk is not None
    assert risk.severity == "WARNING"
    assert result.ok is True


def test_openrewrite_impact_schema_mismatch_is_unknown_warning() -> None:
    loaded = LoadedAnalysisArtifacts(
        required={
            "analysis_report.json": {"status": "PASS"},
            "dependency_graph.json": {},
            "test_inventory.json": {},
            "analysis_summary.md": "analysis ok\n",
        },
        optional={
            "rewrite_impact_summary.json": {
                "impact": "HIGH",
                "blocked_reasons": [],
            }
        },
        errors=[],
        ok=True,
    )

    result = classify_planning_risks(
        loaded,
        StackFingerprint(build_tool="maven", java="11", spring_boot="2.7"),
    )

    assert _find_openrewrite_risk(result, "UNKNOWN") is not None
    mismatch = next(
        (
            risk
            for risk in result.risks
            if risk.code == "OPENREWRITE_IMPACT_SCHEMA_MISMATCH"
        ),
        None,
    )
    assert mismatch is not None
    assert mismatch.severity == "WARNING"
    assert _find_openrewrite_risk(result, "HIGH") is None
    assert result.ok is True


def test_planning_node_blocked_openrewrite_impact_writes_non_executable_plan(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    hub_dir = tmp_path / "ai-hub"
    run_id = "blocked-openrewrite"
    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_required_analysis_artifacts(analysis_dir)
    _write_profile(hub_dir)
    (analysis_dir / "rewrite_impact_summary.json").write_text(
        json.dumps(
            {
                "overall_impact": "BLOCKED",
                "blocked_reasons": ["No safe OpenRewrite recipe path was found."],
            }
        ),
        encoding="utf-8",
    )

    result = planning_node(_state(app_dir, hub_dir, run_id))

    assert result["planning_status"] == "FAIL"
    planning_dir = app_dir / ".migration" / "runs" / run_id / "planning"
    plan_payload = yaml.safe_load((planning_dir / "migration_plan.yaml").read_text(encoding="utf-8"))
    validation_payload = json.loads((planning_dir / "plan_validation_report.json").read_text(encoding="utf-8"))

    assert plan_payload["executable"] is False
    assert any("OPENREWRITE_IMPACT_BLOCKED" in risk for risk in plan_payload["risks"])
    assert validation_payload["status"] == "PASS"
