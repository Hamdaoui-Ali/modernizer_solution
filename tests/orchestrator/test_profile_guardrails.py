from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import yaml

from migration_factory.approval.approve_run import record_approval_decision_for_run
from migration_factory.agents.build_agent.agent import BuildRunResult
from migration_factory.agents.transformation_agent.agent import TransformationRunResult
from migration_factory.agents.test_agent.agent import TestAgentResult
from migration_factory.contracts.migration import LedgerStatus
from migration_factory.transform_v1_after_approval import (
    STATUS_APPROVAL_FAILED,
    STATUS_APPLIED,
    STATUS_FAILED,
    apply_approved_sandbox_transform,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
AI_HUB = REPO_ROOT / "modernizer-solution-ai-hub"
RUN_ID = "run-1"


def test_experimental_profile_blocks_full_sandbox_transform_after_approval(tmp_path: Path) -> None:
    legacy, modernized, run_dir = _approved_run(tmp_path, profile="springboot-2.1-to-3.5-java17-library-experimental")

    result = apply_approved_sandbox_transform(
        run_dir=run_dir,
        legacy_app=legacy,
        modernized_app=modernized,
        ai_hub=str(AI_HUB),
        profile="springboot-2.1-to-3.5-java17-library-experimental",
        approved_by="reviewer",
        quiet=True,
        status_writer=None,
    )

    assert result.status == STATUS_FAILED
    assert "Profile guardrails block sandbox source-changing transformation" in result.message
    assert "openrewrite.apply_allowed=false" in result.message
    assert result.sandbox_path is None
    assert not (run_dir / "workspaces" / "sandbox").exists()
    assert not (run_dir / "transformation" / "transformation_execution_plan.yaml").exists()


def test_transform_does_not_mutate_or_create_sandbox_before_approval(tmp_path: Path) -> None:
    legacy, modernized, run_dir = _phase1_run(tmp_path, profile="springboot-2.1.6-to-2.7-java11")
    source_file = legacy / "src" / "main" / "java" / "App.java"
    before = source_file.read_text(encoding="utf-8")

    result = apply_approved_sandbox_transform(
        run_dir=run_dir,
        legacy_app=legacy,
        modernized_app=modernized,
        ai_hub=str(AI_HUB),
        profile="springboot-2.1.6-to-2.7-java11",
        approved_by="reviewer",
        quiet=True,
        status_writer=None,
    )

    assert result.status == STATUS_APPROVAL_FAILED
    assert source_file.read_text(encoding="utf-8") == before
    assert not (run_dir / "workspaces" / "sandbox").exists()
    assert not (run_dir / "transformation" / "transformation_execution_plan.yaml").exists()


def test_final_contract_records_build_and_test_after_boot_version_satisfied(tmp_path: Path) -> None:
    legacy, modernized, run_dir = _approved_run(tmp_path, profile="springboot-2.7-to-3.5-java17")
    ledger_file = run_dir / ".migration" / "ledger.json"
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    ledger_file.write_text(
        json.dumps(
            {
                "status": "AWAITING_BUILD_AGENT",
                "current_unit": "spring-boot-3-5",
                "build_validation": {
                    "required": True,
                    "status": "PENDING",
                    "unit_id": "spring-boot-3-5",
                    "command": ["mvn", "clean", "test", "-DskipITs"],
                },
            }
        ),
        encoding="utf-8",
    )

    with mock.patch("migration_factory.transform_v1_after_approval.run_transformation_agent") as run_transform_agent_mock, mock.patch(
        "migration_factory.transform_v1_after_approval.run_build_agent"
    ) as run_build_agent_mock, mock.patch(
        "migration_factory.transform_v1_after_approval.run_test_agent"
    ) as run_test_agent_mock, mock.patch(
        "migration_factory.transform_v1_after_approval._next_unit_after",
        return_value=None,
    ), mock.patch(
        "migration_factory.transform_v1_after_approval._run_dependency_policy_layer",
        return_value={
            "dependency_policy_report_path": None,
            "dependency_policy_summary_path": None,
            "dependency_policy_status": "SKIPPED",
            "dependency_policy_risks_count": 0,
            "dependency_policy_blockers_count": 0,
            "copilot_dependency_advisory_status": "SKIPPED",
            "policy_patch_applied": False,
            "artifact_refs": {},
        },
    ):
        run_transform_agent_mock.return_value = TransformationRunResult(
            ledger_file=ledger_file,
            status=LedgerStatus.AWAITING_BUILD_AGENT,
            completed_units=["spring-boot-3-5"],
        )
        run_build_agent_mock.return_value = BuildRunResult(
            succeeded=True,
            result_kind="success",
            message="build passed",
            error_contract_path=None,
            exit_code=0,
            matched_line=None,
            command=["mvn", "clean", "test", "-DskipITs"],
            cwd=legacy,
            command_duration_seconds=0.1,
        )
        run_test_agent_mock.return_value = TestAgentResult(
            test_status="TEST_PASSED",
            totals={"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
            report_path=run_dir / "test" / "post_transform" / "test_report.json",
            summary_path=run_dir / "test" / "post_transform" / "test_summary.md",
            log_path=run_dir / "test" / "post_transform" / "test_agent.log",
            report_paths=[],
            parse_duration_seconds=0.01,
        )

        result = apply_approved_sandbox_transform(
            run_dir=run_dir,
            legacy_app=legacy,
            modernized_app=modernized,
            ai_hub=str(AI_HUB),
            profile="springboot-2.7-to-3.5-java17",
            approved_by="reviewer",
            quiet=True,
            status_writer=None,
        )

    assert result.status == STATUS_APPLIED
    assert result.build_status == "BUILD_PASSED_IN_SANDBOX"
    assert result.test_status == "TEST_PASSED"
    assert result.sandbox_path is not None


def _approved_run(tmp_path: Path, *, profile: str) -> tuple[Path, Path, Path]:
    legacy, modernized, run_dir = _phase1_run(tmp_path, profile=profile)
    record_approval_decision_for_run(
        run_dir=run_dir,
        run_id=RUN_ID,
        decided_by="reviewer",
        decision="approved",
        comments="go",
        source="test",
        require_approved=True,
    )
    return legacy, modernized, run_dir


def _phase1_run(tmp_path: Path, *, profile: str) -> tuple[Path, Path, Path]:
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    run_dir = modernized / ".migration" / "runs" / RUN_ID
    analysis_dir = run_dir / "analysis"
    planning_dir = run_dir / "planning"
    assessment_dir = run_dir / "assessment"
    source_dir = legacy / "src" / "main" / "java"
    source_dir.mkdir(parents=True)
    modernized.mkdir()
    analysis_dir.mkdir(parents=True)
    planning_dir.mkdir(parents=True)
    assessment_dir.mkdir(parents=True)
    (source_dir / "App.java").write_text("class App {}\n", encoding="utf-8")

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
            "status": "PASS",
            "paths": {
                "legacy_root": str(legacy),
                "modernized_root": str(modernized),
                "artifact": "read_only_verification.json",
            },
            "allowed_write_roots": [str(analysis_dir)],
            "checks": {
                "legacy_tree_unchanged": True,
                "modernized_source_unchanged": True,
                "ignored_generated_paths": [],
            },
            "violations": [],
            "source_modified": False,
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
            "profile": profile,
            "source_stack": {"build_tool": "maven", "java": "11", "spring_boot": "2.1"},
            "target_stack": {"build_tool": "maven", "java": "17", "spring_boot": "3.5.14"},
            "executable": True,
            "requires_human_approval": True,
            "risks": [],
            "blockers": [],
            "warnings": [],
            "unit_references": ["baseline"],
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
            "units": [{"id": "baseline", "goal": "Validate", "writes_source": False, "required": True}],
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
            "profile": profile,
            "requires_human_approval": True,
            "decision_options": ["approved", "rejected", "replan_required"],
            "recommended_decision": None,
            "units_to_execute": ["baseline"],
            "blockers": [],
            "warnings": [],
            "artifact_refs": {"self": "approval_request.json"},
        },
    )
    _write_json(assessment_dir / "assessment_report.json", _assessment_report(profile))
    return legacy, modernized, run_dir


def _assessment_report(profile: str) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "agent": "assessment",
        "phase": "assessment",
        "status": "PASS",
        "profile": profile,
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
        "openrewrite_dry_run": {"status": "SKIPPED", "overall_impact": "none", "counts": {}, "artifact_ref": None},
        "migration_units": {"count": 1, "units": [{"id": "baseline"}], "artifact_ref": "../planning/migration_units.yaml"},
        "blockers": [],
        "warnings": [],
        "copilot": {"status": "SKIPPED", "artifact_ref": None},
        "approval_readiness": {
            "status": "READY_FOR_REVIEW",
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
        "execution_claims": {
            "transformation_executed": False,
            "openrewrite_apply_executed": False,
            "migrated_build_executed": False,
            "migrated_tests_executed": False,
            "final_migration_executed": False,
        },
        "artifact_refs": {"self": "assessment_report.json"},
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_yaml(path: Path, payload: object) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
