from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import yaml

from migration_factory.orchestrator import graph as graph_module
from migration_factory.orchestrator import resume
from migration_factory.orchestrator.state import READ_ONLY_ASSESSMENT_MODE
from migration_factory.orchestrator.artifact_validation import ArtifactValidationResult
from migration_factory.orchestrator.checkpointing import default_checkpointer
from migration_factory.orchestrator.phase_services import PhaseServices
from migration_factory.orchestrator.state import (
    FULL_SANDBOX_MIGRATION_MODE,
    build_initial_state,
)
from migration_factory.transform_v1_after_approval import STATUS_APPLIED


def test_full_sandbox_migration_stops_at_approval_interrupt_before_decision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = _initial_full_state(tmp_path)
    _patch_validators(monkeypatch)
    app = graph_module.build_graph(
        checkpointer=default_checkpointer(state["run_dir"]),
        phase_services=_passing_services(),
    )

    result = app.invoke(state, config={"configurable": {"thread_id": state["run_id"]}})

    payload = result["__interrupt__"][0].value
    assert payload["type"] == "human_approval_required"
    assert payload["run_id"] == state["run_id"]
    assert not (Path(state["run_dir"]) / "approval" / "approval_decision.json").exists()


def test_resume_approved_records_approval_and_runs_sandbox_transform(
    monkeypatch,
    tmp_path: Path,
) -> None:
    transform_calls: list[str] = []

    def fake_transform(resumed_state):
        transform_calls.append(resumed_state["approval_decision"])
        sandbox_path = Path(resumed_state["run_dir"]) / "workspaces" / "sandbox"
        log_path = Path(resumed_state["run_dir"]) / "logs" / "phase2_transform.log"
        plan_path = Path(resumed_state["run_dir"]) / "transformation" / "transformation_execution_plan.yaml"
        ledger_path = sandbox_path / ".migration" / "ledger.json"
        test_dir = Path(resumed_state["run_dir"]) / "test" / "post_transform"
        test_report = test_dir / "test_report.json"
        test_summary = test_dir / "test_summary.md"
        test_log = test_dir / "test_agent.log"
        sandbox_path.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        test_dir.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok\n", encoding="utf-8")
        plan_path.write_text(
            f"run_id: {resumed_state['run_id']}\n"
            "recipes:\n  - org.openrewrite.java.migrate.UpgradeToJava17\n",
            encoding="utf-8",
        )
        ledger_path.write_text("{}\n", encoding="utf-8")
        test_report.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "agent": "test-agent",
                    "run_id": resumed_state["run_id"],
                    "phase": "post_transform",
                    "test_status": "TEST_PASSED",
                    "totals": {"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
                    "command": ["mvn", "test"],
                    "cwd": str(sandbox_path),
                    "sandbox_path": str(sandbox_path),
                    "execution_owner": "build-agent",
                    "execution_mode": "parse_existing_surefire",
                    "report_paths": [],
                    "test_log_path": str(test_log),
                    "source_log_path": str(log_path),
                    "created_at": "2026-01-01T00:00:00Z",
                    "artifact_refs": {
                        "self": str(test_report),
                        "summary": str(test_summary),
                        "log": str(test_log),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        test_summary.write_text("# test\n", encoding="utf-8")
        test_log.write_text("ok\n", encoding="utf-8")
        return {
            "current_phase": "sandbox_transform",
            "orchestration_status": "PASS",
            "transform_status": STATUS_APPLIED,
            "build_status": "BUILD_PASSED_IN_SANDBOX",
            "test_status": "TEST_PASSED",
            "test_totals": {"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
            "test_report_path": str(test_report),
            "test_summary_path": str(test_summary),
            "test_log_path": str(test_log),
            "test_phase": "post_transform",
            "sandbox_path": str(sandbox_path),
            "transform_log_path": str(log_path),
            "final_status": STATUS_APPLIED,
            "stop_reason": "Sandbox migration candidate ready.",
            "artifact_refs": {
                **dict(resumed_state.get("artifact_refs", {})),
                "transformation_execution_plan": str(plan_path),
                "migration_ledger": str(ledger_path),
                "phase2_log": str(log_path),
                "post_transform_test_report": str(test_report),
                "post_transform_test_summary": str(test_summary),
                "post_transform_test_log": str(test_log),
            },
        }

    real_build_graph = graph_module.build_graph

    def build_graph_with_fake_transform(*args, **kwargs):
        kwargs.setdefault("phase_services", _passing_services())
        kwargs.setdefault("sandbox_transform_service", fake_transform)
        return real_build_graph(*args, **kwargs)

    monkeypatch.setattr(graph_module, "run_sandbox_transform_phase", fake_transform)
    monkeypatch.setattr(graph_module, "build_graph", build_graph_with_fake_transform)
    state = _paused_full_run(monkeypatch, tmp_path)
    _write_phase_1_run(Path(state["run_dir"]), state["run_id"])

    result = resume.resume_orchestration(
        run_id=state["run_id"],
        run_dir=Path(state["run_dir"]),
        decision="approved",
        approved_by="reviewer",
        comments="go",
    )

    approval_dir = Path(state["run_dir"]) / "approval"
    decision = json.loads((approval_dir / "approval_decision.json").read_text(encoding="utf-8"))
    assert decision["decision"] == "approved"
    assert decision["decided_by"] == "reviewer"
    assert decision["comments"] == "go"
    assert (approval_dir / "approved_plan_lock.json").is_file()
    assert transform_calls == ["approved"]
    assert result["final_status"] == STATUS_APPLIED
    assert result["orchestration_status"] == "PASS"
    assert result["orchestration_artifacts_valid"] is True
    assert result["stop_reason"] == "Sandbox migration candidate ready."
    assert result["artifact_refs"]["approval_decision"].endswith("approval_decision.json")
    assert result["artifact_refs"]["approved_plan_lock"].endswith("approved_plan_lock.json")
    assert result["artifact_refs"]["transformation_execution_plan"].endswith("transformation_execution_plan.yaml")
    assert result["artifact_refs"]["migration_ledger"].endswith("ledger.json")
    assert result["artifact_refs"]["phase2_log"].endswith("phase2_transform.log")
    assert result["artifact_refs"]["post_transform_test_report"].endswith("test_report.json")
    assert result["artifact_refs"]["post_transform_test_summary"].endswith("test_summary.md")
    assert result["artifact_refs"]["post_transform_test_log"].endswith("test_agent.log")
    assert result["artifact_refs"]["orchestration_summary"].endswith("orchestration_summary.json")
    assert _as_posix(result["artifact_refs"]["final_migration_report"]).endswith("final/migration_report.json")
    assert _as_posix(result["artifact_refs"]["final_migration_summary"]).endswith("final/migration_summary.md")
    assert _as_posix(result["artifact_refs"]["timing_report"]).endswith("performance/timing_report.json")
    assert _as_posix(result["artifact_refs"]["timing_summary"]).endswith("performance/timing_summary.md")
    assert Path(result["artifact_refs"]["timing_report"]).is_file()
    assert Path(result["artifact_refs"]["timing_summary"]).is_file()
    timing_summary = Path(result["artifact_refs"]["timing_summary"]).read_text(encoding="utf-8")
    assert "total_duration_seconds" in timing_summary
    assert "Slowest Phases" in timing_summary


def test_resume_cli_accepts_required_approval_fields(monkeypatch, tmp_path: Path, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_resume_orchestration(**kwargs):
        captured.update(kwargs)
        return {
            "run_id": kwargs["run_id"],
            "approval_decision": kwargs["decision"],
            "final_status": STATUS_APPLIED,
        }

    monkeypatch.setattr(resume, "resume_orchestration", fake_resume_orchestration)
    result = resume.main(
        [
            "--run-id",
            "run-001",
            "--run-dir",
            str(tmp_path / "run"),
            "--decision",
            "approved",
            "--approved-by",
            "reviewer",
            "--comments",
            "go",
        ]
    )

    assert result == 0
    assert captured == {
        "run_id": "run-001",
        "run_dir": tmp_path / "run",
        "decision": "approved",
        "approved_by": "reviewer",
        "comments": "go",
    }
    out = capsys.readouterr().out.strip()
    if out.startswith("CONTROL_TOWER_FINAL_JSON "):
        out = out[len("CONTROL_TOWER_FINAL_JSON "):]
    assert json.loads(out)["final_status"] == STATUS_APPLIED


def test_resume_rejected_records_decision_and_does_not_run_transform(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = _paused_full_run(monkeypatch, tmp_path)
    _write_phase_1_run(Path(state["run_dir"]), state["run_id"])

    monkeypatch.setattr(
        graph_module,
        "run_sandbox_transform_phase",
        lambda resumed_state: (_ for _ in ()).throw(AssertionError("transform should not run")),
    )

    result = resume.resume_orchestration(
        run_id=state["run_id"],
        run_dir=Path(state["run_dir"]),
        decision="rejected",
        approved_by="reviewer",
        comments="no",
    )

    decision = json.loads(
        (Path(state["run_dir"]) / "approval" / "approval_decision.json").read_text(encoding="utf-8")
    )
    assert decision["decision"] == "rejected"
    assert not (Path(state["run_dir"]) / "approval" / "approved_plan_lock.json").exists()
    assert result["approval_decision"] == "rejected"
    assert result["stop_reason"] == "Approval decision 'rejected' recorded; stopping."


def test_resume_replan_required_records_decision_and_does_not_run_transform(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = _paused_full_run(monkeypatch, tmp_path)
    _write_phase_1_run(Path(state["run_dir"]), state["run_id"])

    monkeypatch.setattr(
        graph_module,
        "run_sandbox_transform_phase",
        lambda resumed_state: (_ for _ in ()).throw(AssertionError("transform should not run")),
    )

    result = resume.resume_orchestration(
        run_id=state["run_id"],
        run_dir=Path(state["run_dir"]),
        decision="replan_required",
        approved_by="reviewer",
        comments="revise",
    )

    decision = json.loads(
        (Path(state["run_dir"]) / "approval" / "approval_decision.json").read_text(encoding="utf-8")
    )
    assert decision["decision"] == "replan_required"
    assert not (Path(state["run_dir"]) / "approval" / "approved_plan_lock.json").exists()
    assert result["approval_decision"] == "replan_required"
    assert result["stop_reason"] == "Approval decision 'replan_required' recorded; stopping."


def test_read_only_resume_approved_records_decision_and_does_not_run_transform(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = _paused_full_run(monkeypatch, tmp_path)
    state["mode"] = READ_ONLY_ASSESSMENT_MODE
    _write_phase_1_run(Path(state["run_dir"]), state["run_id"])
    snapshot_path = Path(state["run_dir"]) / "orchestration" / "approval_interrupt_state.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["mode"] = READ_ONLY_ASSESSMENT_MODE
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    monkeypatch.setattr(
        graph_module,
        "run_sandbox_transform_phase",
        lambda resumed_state: (_ for _ in ()).throw(AssertionError("transform should not run")),
    )

    result = resume.resume_orchestration(
        run_id=state["run_id"],
        run_dir=Path(state["run_dir"]),
        decision="approved",
        approved_by="reviewer",
        comments="ok",
    )

    decision = json.loads(
        (Path(state["run_dir"]) / "approval" / "approval_decision.json").read_text(encoding="utf-8")
    )
    assert decision["decision"] == "approved"
    assert result["mode"] == READ_ONLY_ASSESSMENT_MODE
    assert result["approval_decision"] == "approved"
    assert result["stop_reason"] == "Approval decision 'approved' recorded; stopping."


def _paused_full_run(monkeypatch, tmp_path: Path) -> dict:
    state = _initial_full_state(tmp_path)
    _patch_validators(monkeypatch)
    app = graph_module.build_graph(
        checkpointer=default_checkpointer(state["run_dir"]),
        phase_services=_passing_services(),
    )
    app.invoke(state, config={"configurable": {"thread_id": state["run_id"]}})
    return state


def _initial_full_state(tmp_path: Path) -> dict:
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    ai_hub = tmp_path / "ai-hub"
    legacy.mkdir()
    modernized.mkdir()
    (ai_hub / "profiles").mkdir(parents=True)
    (ai_hub / "profiles" / "java17.yaml").write_text("id: java17\n", encoding="utf-8")
    run_id = f"run-{uuid4().hex}"
    return build_initial_state(
        run_id=run_id,
        legacy_app_path=str(legacy),
        modernized_app_path=str(modernized),
        ai_hub_path=str(ai_hub),
        profile_id="java17",
        thread_id=run_id,
        mode=FULL_SANDBOX_MIGRATION_MODE,
    )


def _passing_services() -> PhaseServices:
    return PhaseServices(
        run_analysis_phase=lambda state: {"analysis_status": "PASS"},
        run_planning_phase=lambda state: {"planning_status": "PASS"},
        run_assessment_phase=lambda state: {"assessment_status": "PASS"},
    )


def _patch_validators(monkeypatch) -> None:
    valid = ArtifactValidationResult(valid=True, artifact_refs={}, blockers=[], warnings=[])
    monkeypatch.setattr(graph_module, "validate_analysis_artifacts", lambda state: valid)
    monkeypatch.setattr(graph_module, "validate_planning_artifacts", lambda state: valid)
    monkeypatch.setattr(graph_module, "validate_assessment_artifacts", lambda state: valid)


def _write_phase_1_run(run_dir: Path, run_id: str) -> None:
    analysis = run_dir / "analysis"
    planning = run_dir / "planning"
    assessment = run_dir / "assessment"
    analysis.mkdir(parents=True, exist_ok=True)
    planning.mkdir(parents=True, exist_ok=True)
    assessment.mkdir(parents=True, exist_ok=True)

    _write_json(
        analysis / "analysis_report.json",
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "status": "PASS",
            "artifact_refs": {"self": "analysis_report.json"},
        },
    )
    _write_json(analysis / "dependency_graph.json", {"nodes": [], "edges": []})
    _write_json(analysis / "test_inventory.json", {"tests": []})
    (analysis / "analysis_summary.md").write_text("# Analysis\n", encoding="utf-8")
    _write_json(
        analysis / "read_only_verification.json",
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "agent": "analysis_agent",
            "phase": "analysis",
            "status": "PASS",
            "paths": {
                "legacy_root": "legacy",
                "modernized_root": "modernized",
                "artifact": "read_only_verification.json",
            },
            "allowed_write_roots": [str(analysis)],
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
        planning / "migration_plan.yaml",
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "status": "PASS",
            "risk": "LOW",
            "artifact_refs": {"self": "migration_plan.yaml"},
        },
    )
    _write_yaml(
        planning / "migration_units.yaml",
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "status": "PASS",
            "artifact_refs": {"self": "migration_units.yaml"},
            "units": [],
        },
    )
    _write_json(
        planning / "approval_request.json",
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
            "warnings": [],
            "artifact_refs": {"self": "approval_request.json"},
        },
    )
    _write_json(assessment / "assessment_report.json", _assessment_report(run_id))


def _assessment_report(run_id: str) -> dict:
    return {
        "schema_version": "1.0.0",
        "run_id": run_id,
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
        "openrewrite_dry_run": {"status": "SKIPPED", "overall_impact": "none", "counts": {}, "artifact_ref": None},
        "migration_units": {"count": 0, "units": [], "artifact_ref": "../planning/migration_units.yaml"},
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


def _as_posix(path: str) -> str:
    return path.replace("\\", "/")
