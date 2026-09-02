import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from migration_factory.assessment import (
    AssessmentArtifactError,
    write_assessment_artifacts,
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "migration_factory" / "contracts" / "schemas"


def _validate_schema(schema_name: str, payload: dict) -> None:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)


def _run_dirs(tmp_path: Path, run_id: str = "run-1") -> tuple[Path, Path, Path]:
    app_dir = tmp_path / "app"
    run_dir = app_dir / ".migration" / "runs" / run_id
    analysis_dir = run_dir / "analysis"
    planning_dir = run_dir / "planning"
    analysis_dir.mkdir(parents=True)
    planning_dir.mkdir(parents=True)
    return app_dir, analysis_dir, planning_dir


def _write_required_artifacts(analysis_dir: Path, planning_dir: Path, run_id: str = "run-1") -> None:
    (analysis_dir / "analysis_report.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "status": "PASS",
                "inventory": {
                    "build_tool": "maven",
                    "java_version": "11",
                    "spring_boot_version": "2.7",
                },
                "artifact_refs": {"self": "analysis_report.json"},
            }
        ),
        encoding="utf-8",
    )
    (analysis_dir / "dependency_graph.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    (analysis_dir / "test_inventory.json").write_text(json.dumps({"tests": []}), encoding="utf-8")
    (analysis_dir / "analysis_summary.md").write_text("analysis ok\n", encoding="utf-8")
    (analysis_dir / "rewrite_impact_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "status": "PASS",
                "overall_impact": "MEDIUM",
                "changed_files": ["src/main/java/App.java"],
                "high_risk_files": [],
                "blocked_reasons": [],
            }
        ),
        encoding="utf-8",
    )
    (analysis_dir / "read_only_verification.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "status": "PASS",
                "source_modified": False,
            }
        ),
        encoding="utf-8",
    )

    (planning_dir / "migration_plan.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "status": "WARNING",
                "risk": "MEDIUM",
                "profile": "java17",
                "source_stack": {"build_tool": "maven", "java": "11", "spring_boot": "2.7"},
                "target_stack": {"build_tool": "maven", "java": "17", "spring_boot": "3.5.14"},
                "executable": True,
                "requires_human_approval": True,
                "risks": ["[WARNING] OPENREWRITE_IMPACT_MEDIUM: Review dry-run patch."],
                "blockers": [],
                "warnings": ["Review OpenRewrite dry-run before approval."],
                "unit_references": ["baseline"],
                "artifact_refs": {
                    "self": "migration_plan.yaml",
                    "migration_units": "migration_units.yaml",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (planning_dir / "migration_units.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "status": "PASS",
                "artifact_refs": {"self": "migration_units.yaml"},
                "units": [
                    {
                        "id": "baseline",
                        "goal": "Validate baseline",
                        "writes_source": False,
                        "required": True,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (planning_dir / "plan_summary.md").write_text("plan ok\n", encoding="utf-8")
    (planning_dir / "approval_request.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "agent": "planning_agent",
                "phase": "approval",
                "status": "PASS",
                "profile": "java17",
                "requires_human_approval": True,
                "decision_options": ["approved", "rejected", "replan_required"],
                "recommended_decision": None,
                "units_to_execute": ["baseline"],
                "blockers": [],
                "warnings": ["Review OpenRewrite dry-run before approval."],
                "artifact_refs": {
                    "migration_plan": "migration_plan.yaml",
                    "migration_units": "migration_units.yaml",
                    "plan_summary": "plan_summary.md",
                },
            }
        ),
        encoding="utf-8",
    )
    (planning_dir / "plan_validation_report.json").write_text(
        json.dumps({"run_id": run_id, "status": "PASS", "reasons": []}),
        encoding="utf-8",
    )
    (planning_dir / "copilot_assist.json").write_text(
        json.dumps({"schema_version": "1.0.0", "run_id": run_id, "status": "UNAVAILABLE"}),
        encoding="utf-8",
    )


def test_assessment_fails_when_required_artifact_missing(tmp_path: Path) -> None:
    app_dir, analysis_dir, planning_dir = _run_dirs(tmp_path)
    _write_required_artifacts(analysis_dir, planning_dir)
    (planning_dir / "approval_request.json").unlink()

    with pytest.raises(AssessmentArtifactError) as exc:
        write_assessment_artifacts(app_dir, "run-1")

    assert exc.value.missing == ("planning/approval_request.json",)


def test_assessment_report_uses_artifact_refs_without_duplication(tmp_path: Path) -> None:
    app_dir, analysis_dir, planning_dir = _run_dirs(tmp_path)
    _write_required_artifacts(analysis_dir, planning_dir)

    result = write_assessment_artifacts(app_dir, "run-1")

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["artifact_refs"]["analysis_report"] == "../analysis/analysis_report.json"
    assert report["artifact_refs"]["migration_plan"] == "../planning/migration_plan.yaml"
    assert report["artifact_refs"]["read_only_verification"] == "../analysis/read_only_verification.json"
    assert "inventory" not in report["analysis"]
    assert "unit_references" not in report["planning"]
    assert report["warnings"] == ["Review OpenRewrite dry-run before approval."]


def test_assessment_summary_is_generated_and_never_claims_execution(tmp_path: Path) -> None:
    app_dir, analysis_dir, planning_dir = _run_dirs(tmp_path)
    _write_required_artifacts(analysis_dir, planning_dir)

    result = write_assessment_artifacts(app_dir, "run-1")

    _validate_schema("assessment_report.schema.json", result.report)
    summary = result.summary_path.read_text(encoding="utf-8")
    assert "OpenRewrite dry-run: PASS (MEDIUM)" in summary
    assert "Transformation was not executed." in summary
    assert "OpenRewrite apply was not executed." in summary
    assert result.report["execution_claims"] == {
        "transformation_executed": False,
        "openrewrite_apply_executed": False,
        "migrated_build_executed": False,
        "migrated_tests_executed": False,
        "final_migration_executed": False,
    }


def test_assessment_propagates_failed_read_only_status(tmp_path: Path) -> None:
    app_dir, analysis_dir, planning_dir = _run_dirs(tmp_path)
    _write_required_artifacts(analysis_dir, planning_dir)
    (analysis_dir / "read_only_verification.json").write_text(
        json.dumps({"schema_version": "1.0.0", "run_id": "run-1", "status": "FAIL", "source_modified": True}),
        encoding="utf-8",
    )

    result = write_assessment_artifacts(app_dir, "run-1")

    assert result.report["status"] == "FAIL"
    assert result.report["read_only_verification"]["status"] == "FAIL"
    assert result.report["read_only_verification"]["source_modified"] is True
    assert result.report["approval_readiness"]["status"] == "BLOCKED"


def test_assessment_blocks_schema_invalid_planning_artifact(tmp_path: Path) -> None:
    app_dir, analysis_dir, planning_dir = _run_dirs(tmp_path)
    _write_required_artifacts(analysis_dir, planning_dir)
    plan = yaml.safe_load((planning_dir / "migration_plan.yaml").read_text(encoding="utf-8"))
    plan["schema_version"] = "1.0"
    (planning_dir / "migration_plan.yaml").write_text(
        yaml.safe_dump(plan, sort_keys=False), encoding="utf-8"
    )

    result = write_assessment_artifacts(app_dir, "run-1")

    assert result.report["status"] == "FAIL"
    assert result.report["approval_readiness"]["status"] == "BLOCKED"
    assert any(
        blocker.startswith("Schema validation failed for planning/migration_plan.yaml")
        for blocker in result.report["blockers"]
    )


def test_assessment_flags_enterprise_compatibility_risks(tmp_path: Path) -> None:
    app_dir, analysis_dir, planning_dir = _run_dirs(tmp_path)
    _write_required_artifacts(analysis_dir, planning_dir)
    report = json.loads((analysis_dir / "analysis_report.json").read_text(encoding="utf-8"))
    report["imports"] = ["javax.persistence.Entity"]
    report["classes"] = ["WebSecurityConfigurerAdapter"]
    report["dependencies"] = [
        "org.hibernate:hibernate-core:5.4.0.Final",
        "com.company:internal-starter:1.0-SNAPSHOT",
    ]
    report["plugins"] = ["maven-surefire-plugin:2.22.2"]
    report["bytecode"] = ["major version 52"]
    report["test_notes"] = "missing smoke tests"
    (analysis_dir / "analysis_report.json").write_text(json.dumps(report), encoding="utf-8")

    result = write_assessment_artifacts(app_dir, "run-1")

    findings = {finding["code"] for finding in result.report["enterprise_compatibility"]["findings"]}
    assert result.report["enterprise_compatibility"]["status"] == "REVIEW_REQUIRED"
    assert {
        "old_spring_security_config",
        "javax_to_jakarta",
        "jpa_hibernate_risk",
        "maven_plugin_risk",
        "internal_corporate_dependencies",
        "unsupported_bytecode",
        "missing_tests_or_smoke_tests",
    }.issubset(findings)
