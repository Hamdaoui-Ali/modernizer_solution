from __future__ import annotations

import json
from pathlib import Path

from migration_factory.orchestrator.state import build_initial_state
from migration_factory.orchestrator import summary as summary_module
from migration_factory.orchestrator.summary import (
    build_orchestration_summary,
    finalize_orchestration_state,
    write_orchestration_summary,
)


VALID_COPILOT_MARKDOWN = """# Copilot Final Migration Report

## 1. Summary

Generated.

## 2. Source Of Truth

Deterministic artifacts are authoritative.

## 10. Test Results

Passed.

## 15. Copilot Advisory Scope

Copilot is advisory only.

## 18. Final Verdict

Ready for manual review.
"""


def _state(tmp_path: Path):
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
        ai_hub_path=str(tmp_path / "ai-hub"),
        profile_id="java17",
    )
    state.update(
        {
            "current_phase": "assessment",
            "analysis_status": "PASS",
            "planning_status": "PASS",
            "assessment_status": "PASS",
            "approval_status": "COMPLETED",
            "approval_decision": "approved",
            "stop_reason": "approved",
            "blockers": ["manual follow-up"],
            "warnings": ["warning"],
            "errors": ["error"],
            "artifact_refs": {"assessment_report": "assessment_report.json"},
        }
    )
    return state


def test_summary_includes_phase_statuses_and_stop_reason(tmp_path: Path) -> None:
    summary = build_orchestration_summary(_state(tmp_path))

    assert summary["run_id"] == "run-001"
    assert summary["final_status"] == "FAILED"
    assert summary["current_phase"] == "assessment"
    assert summary["analysis_status"] == "PASS"
    assert summary["planning_status"] == "PASS"
    assert summary["assessment_status"] == "PASS"
    assert summary["orchestration_status"] == "PENDING"
    assert summary["approval_status"] == "COMPLETED"
    assert summary["stop_reason"] == "approved"
    assert summary["blockers"] == ["manual follow-up"]
    assert summary["warnings"] == ["warning"]
    assert summary["errors"] == ["error"]
    assert summary["artifact_refs"] == {"assessment_report": "assessment_report.json"}


def test_summary_includes_approval_decision_when_present(tmp_path: Path) -> None:
    summary = build_orchestration_summary(_state(tmp_path))

    assert summary["approval_decision"] == "approved"


def test_summary_has_false_execution_claims_and_no_completion_claim(tmp_path: Path) -> None:
    summary = build_orchestration_summary(_state(tmp_path))

    assert summary["transformation_executed"] is False
    assert summary["openrewrite_apply_executed"] is False
    assert summary["migrated_build_executed"] is False
    assert summary["migrated_tests_executed"] is False
    assert summary["final_migration_executed"] is False
    assert "migration_complete" not in summary


def test_summary_excludes_transformation_status(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["transformation_status"] = "PASS"

    assert "transformation_status" not in build_orchestration_summary(state)


def test_summary_migrated_tests_executed_requires_test_passed(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.update(
        {
            "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
            "build_status": "BUILD_PASSED_IN_SANDBOX",
            "test_status": "TEST_FAILED",
        }
    )
    summary = build_orchestration_summary(state)
    assert summary["migrated_build_executed"] is True
    assert summary["migrated_tests_executed"] is False


def test_summary_includes_full_sandbox_migration_outputs(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.update(
        {
            "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
            "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
            "build_status": "BUILD_PASSED_IN_SANDBOX",
            "test_status": "TEST_PASSED",
            "test_totals": {"tests": 5, "passed": 5, "failures": 0, "errors": 0, "skipped": 0},
            "test_report_path": "test/post_transform/test_report.json",
            "test_summary_path": "test/post_transform/test_summary.md",
            "test_log_path": "test/post_transform/test_agent.log",
            "test_phase": "post_transform",
            "sandbox_path": str(tmp_path / "run" / "workspaces" / "sandbox"),
            "transform_log_path": str(tmp_path / "run" / "logs" / "phase2_transform.log"),
            "stop_reason": "Sandbox migration candidate ready.",
            "orchestration_status": "PASS",
            "artifact_refs": {
                "approval_decision": "approval/approval_decision.json",
                "approved_plan_lock": "approval/approved_plan_lock.json",
                "transformation_execution_plan": "transformation/transformation_execution_plan.yaml",
                "migration_ledger": "workspaces/sandbox/.migration/ledger.json",
                "phase2_log": "logs/phase2_transform.log",
                "post_transform_test_report": "test/post_transform/test_report.json",
                "post_transform_test_summary": "test/post_transform/test_summary.md",
                "post_transform_test_log": "test/post_transform/test_agent.log",
            },
        }
    )

    summary = build_orchestration_summary(state)

    assert summary["final_status"] == "TRANSFORM_APPLIED_IN_SANDBOX"
    assert summary["orchestration_status"] == "PASS"
    assert summary["approval_status"] == "COMPLETED"
    assert summary["approval_decision"] == "approved"
    assert summary["approved_by"] == ""
    assert summary["transform_status"] == "TRANSFORM_APPLIED_IN_SANDBOX"
    assert summary["build_status"] == "BUILD_PASSED_IN_SANDBOX"
    assert summary["test_status"] == "TEST_PASSED"
    assert summary["test_totals"]["tests"] == 5
    assert summary["test_report_path"].endswith("test_report.json")
    assert summary["test_summary_path"].endswith("test_summary.md")
    assert summary["test_log_path"].endswith("test_agent.log")
    assert summary["test_phase"] == "post_transform"
    assert summary["sandbox_path"].endswith("workspaces\\sandbox") or summary["sandbox_path"].endswith("workspaces/sandbox")
    assert summary["log_path"].endswith("phase2_transform.log")
    assert summary["stop_reason"] == "Sandbox migration candidate ready."
    assert summary["blockers"] == ["manual follow-up"]
    assert summary["errors"] == ["error"]
    assert summary["warnings"] == ["warning"]
    assert summary["artifact_refs"]["approval_decision"] == "approval/approval_decision.json"
    assert summary["artifact_refs"]["approved_plan_lock"] == "approval/approved_plan_lock.json"
    assert summary["artifact_refs"]["transformation_execution_plan"].endswith("transformation_execution_plan.yaml")
    assert summary["artifact_refs"]["migration_ledger"].endswith("ledger.json")
    assert summary["artifact_refs"]["phase2_log"].endswith("phase2_transform.log")
    assert summary["artifact_refs"]["post_transform_test_report"].endswith("test_report.json")
    assert summary["artifact_refs"]["post_transform_test_summary"].endswith("test_summary.md")
    assert summary["artifact_refs"]["post_transform_test_log"].endswith("test_agent.log")
    assert summary["transformation_executed"] is True
    assert summary["migrated_build_executed"] is True
    assert summary["migrated_tests_executed"] is True
    assert summary["final_migration_executed"] is False


def test_write_orchestration_summary_uses_orchestration_dir_under_run_dir(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    summary_path = write_orchestration_summary(state)

    assert summary_path == (
        Path(state["run_dir"]) / "orchestration" / "orchestration_summary.json"
    )
    assert summary_path.is_file()
    assert json.loads(summary_path.read_text(encoding="utf-8"))["run_id"] == "run-001"


def test_finalize_writes_summary_with_final_report_refs(tmp_path: Path) -> None:
    state = _state(tmp_path)
    run_dir = Path(state["run_dir"])
    sandbox = run_dir / "workspaces" / "sandbox"
    approval = run_dir / "approval"
    transform = run_dir / "transformation"
    logs = run_dir / "logs"
    test_dir = run_dir / "test" / "post_transform"
    for directory in (Path(state["analysis_dir"]), Path(state["assessment_dir"]), sandbox, approval, transform, logs, test_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (Path(state["assessment_dir"]) / "assessment_report.json").write_text(
        json.dumps({"source_stack": {}, "target_stack": {}}) + "\n",
        encoding="utf-8",
    )
    (Path(state["analysis_dir"]) / "analysis_report.json").write_text("{}\n", encoding="utf-8")
    (approval / "approval_decision.json").write_text(json.dumps({"decision": "approved"}) + "\n", encoding="utf-8")
    (approval / "approved_plan_lock.json").write_text("{}\n", encoding="utf-8")
    (transform / "transformation_execution_plan.yaml").write_text("recipes: []\n", encoding="utf-8")
    (sandbox / ".migration").mkdir(parents=True, exist_ok=True)
    (sandbox / ".migration" / "ledger.json").write_text("{}\n", encoding="utf-8")
    (logs / "phase2_transform.log").write_text("ok\n", encoding="utf-8")
    (test_dir / "test_summary.md").write_text("# test\n", encoding="utf-8")
    (test_dir / "test_agent.log").write_text("ok\n", encoding="utf-8")
    (test_dir / "test_report.json").write_text(
        json.dumps(
            {
                "test_status": "TEST_PASSED",
                "totals": {"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
                "test_log_path": str(test_dir / "test_agent.log"),
                "source_log_path": str(logs / "phase2_transform.log"),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    state.update(
        {
            "mode": "full_sandbox_migration",
            "approval_status": "COMPLETED",
            "approval_decision": "approved",
            "orchestration_status": "PASS",
            "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
            "build_status": "BUILD_PASSED_IN_SANDBOX",
            "test_status": "TEST_PASSED",
            "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
            "sandbox_path": str(sandbox),
            "artifact_refs": {
                "approval_decision": str(approval / "approval_decision.json"),
                "approved_plan_lock": str(approval / "approved_plan_lock.json"),
                "transformation_execution_plan": str(transform / "transformation_execution_plan.yaml"),
                "migration_ledger": str(sandbox / ".migration" / "ledger.json"),
                "phase2_log": str(logs / "phase2_transform.log"),
                "post_transform_test_report": str(test_dir / "test_report.json"),
                "post_transform_test_summary": str(test_dir / "test_summary.md"),
                "post_transform_test_log": str(test_dir / "test_agent.log"),
            },
        }
    )

    result = finalize_orchestration_state(state)
    summary = json.loads((Path(state["orchestration_dir"]) / "orchestration_summary.json").read_text(encoding="utf-8"))

    assert _as_posix(result["artifact_refs"]["final_migration_report"]).endswith("final/migration_report.json")
    assert _as_posix(result["artifact_refs"]["final_migration_summary"]).endswith("final/migration_summary.md")
    assert _as_posix(summary["artifact_refs"]["final_migration_report"]).endswith("final/migration_report.json")
    assert _as_posix(summary["artifact_refs"]["final_migration_summary"]).endswith("final/migration_summary.md")


def test_finalize_live_copilot_report_uses_internal_resolved_cmd_path(tmp_path: Path, monkeypatch) -> None:
    state = _state(tmp_path)
    run_dir = Path(state["run_dir"])
    sandbox = run_dir / "workspaces" / "sandbox"
    approval = run_dir / "approval"
    transform = run_dir / "transformation"
    logs = run_dir / "logs"
    test_dir = run_dir / "test" / "post_transform"
    for directory in (Path(state["analysis_dir"]), Path(state["assessment_dir"]), sandbox, approval, transform, logs, test_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (Path(state["assessment_dir"]) / "assessment_report.json").write_text(
        json.dumps({"source_stack": {}, "target_stack": {}}) + "\n",
        encoding="utf-8",
    )
    (Path(state["analysis_dir"]) / "analysis_report.json").write_text("{}\n", encoding="utf-8")
    (approval / "approval_decision.json").write_text(json.dumps({"decision": "approved"}) + "\n", encoding="utf-8")
    (approval / "approved_plan_lock.json").write_text("{}\n", encoding="utf-8")
    (transform / "transformation_execution_plan.yaml").write_text("recipes: []\n", encoding="utf-8")
    (sandbox / ".migration").mkdir(parents=True, exist_ok=True)
    (sandbox / ".migration" / "ledger.json").write_text("{}\n", encoding="utf-8")
    (logs / "phase2_transform.log").write_text("ok\n", encoding="utf-8")
    (test_dir / "test_summary.md").write_text("# test\n", encoding="utf-8")
    (test_dir / "test_agent.log").write_text("ok\n", encoding="utf-8")
    (test_dir / "test_report.json").write_text(
        json.dumps(
            {
                "test_status": "TEST_PASSED",
                "totals": {"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
                "test_log_path": str(test_dir / "test_agent.log"),
                "source_log_path": str(logs / "phase2_transform.log"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    resolved_path = r"C:\Users\x\AppData\Roaming\npm\copilot.cmd"
    calls: list[list[str]] = []
    monkeypatch.setenv("AI_MIGRATION_ENABLE_COPILOT_REPORT", "true")
    monkeypatch.setenv("AI_MIGRATION_COPILOT_PROVIDER", "copilot_cli")
    monkeypatch.setenv("AI_MIGRATION_COPILOT_MODEL", "gpt-5-mini")

    def fake_generate(state):
        calls.append(["legacy-copilot-hook"])

    monkeypatch.setattr(summary_module, "_maybe_generate_copilot_final_report", fake_generate)
    state.update(
        {
            "mode": "full_sandbox_migration",
            "approval_status": "COMPLETED",
            "approval_decision": "approved",
            "orchestration_status": "PASS",
            "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
            "build_status": "BUILD_PASSED_IN_SANDBOX",
            "test_status": "TEST_PASSED",
            "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
            "sandbox_path": str(sandbox),
            "transform_log_path": str(logs / "phase2_transform.log"),
            "test_report_path": str(test_dir / "test_report.json"),
            "test_summary_path": str(test_dir / "test_summary.md"),
            "test_log_path": str(test_dir / "test_agent.log"),
            "test_phase": "post_transform",
            "artifact_refs": {
                "approval_decision": str(approval / "approval_decision.json"),
                "approved_plan_lock": str(approval / "approved_plan_lock.json"),
                "transformation_execution_plan": str(transform / "transformation_execution_plan.yaml"),
                "migration_ledger": str(sandbox / ".migration" / "ledger.json"),
                "phase2_log": str(logs / "phase2_transform.log"),
                "post_transform_test_report": str(test_dir / "test_report.json"),
                "post_transform_test_summary": str(test_dir / "test_summary.md"),
                "post_transform_test_log": str(test_dir / "test_agent.log"),
            },
        }
    )

    result = finalize_orchestration_state(state)

    assert calls == [["legacy-copilot-hook"]]
    assert "copilot_report_response" not in result["artifact_refs"]
    assert "copilot_report_request" not in result["artifact_refs"]
    assert "copilot.cmd" not in json.dumps(result)
    assert resolved_path not in json.dumps(result)


def _as_posix(path: str) -> str:
    return path.replace("\\", "/")
