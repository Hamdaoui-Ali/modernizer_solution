from __future__ import annotations

import json
from pathlib import Path

import yaml

from migration_factory.approval import check_approved_plan_lock
from migration_factory.approval.approve_run import main


RUN_ID = "run-1"


def test_approve_run_writes_approval_decision_json(tmp_path: Path) -> None:
    run_dir = _write_phase_1_run(tmp_path)

    result = main(_argv(run_dir))

    assert result == 0
    payload = json.loads((run_dir / "approval" / "approval_decision.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0.0"
    assert payload["run_id"] == RUN_ID
    assert payload["agent"] == "human"
    assert payload["phase"] == "approval"
    assert payload["decision"] == "approved"
    assert payload["decided_by"] == "reviewer"
    assert payload["source"] == "approve_run_cli"
    assert payload["comments"] == "approved for phase 2"
    assert payload["artifact_refs"]["approval_request"] == "../planning/approval_request.json"
    assert payload["artifact_refs"]["assessment_report"] == "../assessment/assessment_report.json"


def test_approve_run_writes_approved_plan_lock_json(tmp_path: Path) -> None:
    run_dir = _write_phase_1_run(tmp_path)

    result = main(_argv(run_dir))

    assert result == 0
    assert (run_dir / "approval" / "approved_plan_lock.json").is_file()
    assert check_approved_plan_lock(run_dir, expected_run_id=RUN_ID) == ()


def test_approval_json_has_no_utf8_bom(tmp_path: Path) -> None:
    run_dir = _write_phase_1_run(tmp_path)

    assert main(_argv(run_dir)) == 0

    data = (run_dir / "approval" / "approval_decision.json").read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")


def test_missing_required_phase_1_artifact_fails(tmp_path: Path, capsys) -> None:
    run_dir = _write_phase_1_run(tmp_path)
    (run_dir / "analysis" / "dependency_graph.json").unlink()

    result = main(_argv(run_dir))

    assert result == 1
    output = capsys.readouterr().out
    assert "APPROVAL_FAILED" in output
    assert "ERROR: Missing required artifact: analysis/dependency_graph.json" in output
    assert not (run_dir / "approval" / "approval_decision.json").exists()


def test_invalid_decision_fails(tmp_path: Path, capsys) -> None:
    run_dir = _write_phase_1_run(tmp_path)
    argv = _argv(run_dir)
    argv[argv.index("--decision") + 1] = "maybe"

    result = main(argv)

    assert result == 1
    assert "ERROR: Unsupported approval decision: maybe" in capsys.readouterr().out


def test_non_ready_assessment_fails(tmp_path: Path, capsys) -> None:
    run_dir = _write_phase_1_run(tmp_path, approval_readiness="BLOCKED")

    result = main(_argv(run_dir))

    assert result == 1
    assert "assessment_report.json approval_readiness must be READY_FOR_REVIEW" in capsys.readouterr().out


def test_source_modified_true_fails(tmp_path: Path, capsys) -> None:
    run_dir = _write_phase_1_run(tmp_path, source_modified=True)

    result = main(_argv(run_dir))

    assert result == 1
    assert "read_only_verification.json source_modified must be false" in capsys.readouterr().out


def test_true_execution_claim_fails(tmp_path: Path, capsys) -> None:
    run_dir = _write_phase_1_run(tmp_path, execution_claims={"migrated_tests_executed": True})

    result = main(_argv(run_dir))

    assert result == 1
    assert "assessment_report.json execution claim migrated_tests_executed must be false" in capsys.readouterr().out


def test_approved_lock_detects_tampering_with_approved_plan_inputs(tmp_path: Path) -> None:
    run_dir = _write_phase_1_run(tmp_path)
    assert main(_argv(run_dir)) == 0

    (run_dir / "assessment" / "assessment_report.json").write_text('{"changed": true}\n', encoding="utf-8")

    assert check_approved_plan_lock(run_dir, expected_run_id=RUN_ID) == (
        "approved_plan_lock.json artifact hashes do not match current run artifacts",
    )


def test_cli_prints_approved_for_phase_2_on_success(tmp_path: Path, capsys) -> None:
    run_dir = _write_phase_1_run(tmp_path)

    result = main(_argv(run_dir))

    assert result == 0
    output = capsys.readouterr().out
    assert "APPROVAL_RECORDED" in output
    assert f"approval_decision: {run_dir.resolve() / 'approval' / 'approval_decision.json'}" in output
    assert f"approved_plan_lock: {run_dir.resolve() / 'approval' / 'approved_plan_lock.json'}" in output
    assert "APPROVED_FOR_PHASE_2" in output


def _argv(run_dir: Path) -> list[str]:
    return [
        "--run-dir",
        str(run_dir),
        "--run-id",
        RUN_ID,
        "--approved-by",
        "reviewer",
        "--decision",
        "approved",
        "--comments",
        "approved for phase 2",
    ]


def _write_phase_1_run(
    tmp_path: Path,
    *,
    approval_readiness: str = "READY_FOR_REVIEW",
    source_modified: bool = False,
    execution_claims: dict[str, bool] | None = None,
) -> Path:
    run_dir = tmp_path / ".migration" / "runs" / RUN_ID
    analysis_dir = run_dir / "analysis"
    planning_dir = run_dir / "planning"
    assessment_dir = run_dir / "assessment"
    analysis_dir.mkdir(parents=True)
    planning_dir.mkdir(parents=True)
    assessment_dir.mkdir(parents=True)

    _write_json(
        analysis_dir / "analysis_report.json",
        {
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
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
            "run_id": RUN_ID,
            "agent": "analysis_agent",
            "phase": "analysis",
            "status": "FAIL" if source_modified else "PASS",
            "paths": {
                "legacy_root": str(tmp_path / "legacy"),
                "modernized_root": str(tmp_path / "modernized"),
                "artifact": "read_only_verification.json",
            },
            "allowed_write_roots": [str(analysis_dir)],
            "checks": {
                "legacy_tree_unchanged": not source_modified,
                "modernized_source_unchanged": not source_modified,
                "ignored_generated_paths": [],
            },
            "violations": [{"tree": "modernized", "path": "pom.xml", "change_type": "modified"}]
            if source_modified
            else [],
            "source_modified": source_modified,
            "artifact_refs": {"self": "read_only_verification.json"},
        },
    )

    _write_yaml(
        planning_dir / "migration_plan.yaml",
        {
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
            "status": "PASS",
            "risk": "LOW",
            "artifact_refs": {"self": "migration_plan.yaml"},
        },
    )
    _write_yaml(
        planning_dir / "migration_units.yaml",
        {
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
            "status": "PASS",
            "artifact_refs": {"self": "migration_units.yaml"},
            "units": [],
        },
    )
    _write_json(
        planning_dir / "approval_request.json",
        {
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
            "agent": "planning_agent",
            "phase": "approval",
            "status": "PASS",
            "profile": "java17",
            "requires_human_approval": True,
            "decision_options": ["approved", "rejected", "replan_required"],
            "recommended_decision": None,
            "units_to_execute": ["baseline"],
            "blockers": [],
            "warnings": [],
            "artifact_refs": {"self": "approval_request.json"},
        },
    )
    _write_json(
        assessment_dir / "assessment_report.json",
        _assessment_report(
            tmp_path,
            approval_readiness=approval_readiness,
            execution_claims=execution_claims,
        ),
    )
    return run_dir


def _assessment_report(
    tmp_path: Path,
    *,
    approval_readiness: str,
    execution_claims: dict[str, bool] | None,
) -> dict[str, object]:
    claims = {
        "transformation_executed": False,
        "openrewrite_apply_executed": False,
        "migrated_build_executed": False,
        "migrated_tests_executed": False,
        "final_migration_executed": False,
    }
    claims.update(execution_claims or {})
    return {
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
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
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_yaml(path: Path, payload: object) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
