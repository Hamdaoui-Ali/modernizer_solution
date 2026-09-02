import json
from pathlib import Path

import yaml

from migration_factory.assessment.runner import main


def _write_required_artifacts(analysis_dir: Path, planning_dir: Path, run_id: str) -> None:
    (analysis_dir / "analysis_report.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "status": "PASS",
                "artifact_refs": {"self": "analysis_report.json"},
                "inventory": {
                    "build_tool": "maven",
                    "java_version": "11",
                    "spring_boot_version": "2.7",
                },
            }
        ),
        encoding="utf-8",
    )
    (analysis_dir / "dependency_graph.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    (analysis_dir / "test_inventory.json").write_text(json.dumps({"tests": []}), encoding="utf-8")
    (analysis_dir / "analysis_summary.md").write_text("analysis ok\n", encoding="utf-8")
    (planning_dir / "migration_plan.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "status": "PASS",
                "risk": "UNKNOWN",
                "profile": "java17",
                "source_stack": {"build_tool": "maven", "java": "11", "spring_boot": "2.7"},
                "target_stack": {"build_tool": "maven", "java": "17", "spring_boot": "3.5.14"},
                "executable": True,
                "requires_human_approval": True,
                "risks": [],
                "blockers": [],
                "warnings": [],
                "unit_references": [],
                "artifact_refs": {"self": "migration_plan.yaml"},
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
                "units": [],
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
                "units_to_execute": [],
                "blockers": [],
                "warnings": [],
                "artifact_refs": {"self": "approval_request.json"},
            }
        ),
        encoding="utf-8",
    )
    (planning_dir / "plan_validation_report.json").write_text(
        json.dumps({"run_id": run_id, "status": "PASS", "reasons": []}),
        encoding="utf-8",
    )


def test_assessment_runner_writes_assessment_artifacts(tmp_path: Path) -> None:
    run_id = "run-1"
    app_dir = tmp_path / "app"
    run_dir = app_dir / ".migration" / "runs" / run_id
    analysis_dir = run_dir / "analysis"
    planning_dir = run_dir / "planning"
    analysis_dir.mkdir(parents=True)
    planning_dir.mkdir(parents=True)
    _write_required_artifacts(analysis_dir, planning_dir, run_id)

    exit_code = main(["--run-id", run_id, "--modernized", str(app_dir)])

    assert exit_code == 0
    assert (run_dir / "assessment" / "assessment_report.json").exists()
    assert (run_dir / "assessment" / "assessment_summary.md").exists()
