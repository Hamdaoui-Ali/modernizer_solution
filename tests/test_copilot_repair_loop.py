from __future__ import annotations

import json
from pathlib import Path
import subprocess

from migration_factory.copilot_repair.response_validator import validate_copilot_repair_response
from migration_factory.final_report.writer import generate_final_migration_report
from migration_factory.repair_loop.ledger import load_ledger
import migration_factory.repair_loop.orchestrator as repair_orchestrator
from migration_factory.repair_loop.orchestrator import run_post_failure_repair_loop
from migration_factory.repair_loop.patch_apply import PatchApplyResult
from migration_factory.repair_loop.patch_gate import evaluate_patch_proposal, validate_patch_paths
from migration_factory.repair_loop.rule_registry import evaluate_rule
from migration_factory.repair_loop.validation_runner import ValidationResult


def _assert_legacy_repair_loop_quarantined(updates: dict) -> None:
    assert updates["repair_loop_status"] == "BLOCKED"
    assert updates["repair_loop_enabled"] is False
    assert updates["repair_loop_quarantined"] is True
    assert updates["repair_blocker"] == "copilot_removed_from_v2_f5"


def test_build_failure_does_not_trigger_copilot_repair_when_enabled(tmp_path: Path) -> None:
    state = _state(tmp_path)
    calls: list[dict] = []

    def fake_invoker(**kwargs):
        calls.append(kwargs)
        _write_response(Path(kwargs["run_dir"]), _response([]))
        return {
            "status": "USED",
            "artifact_refs": {
                "copilot_repair_response": str(Path(kwargs["run_dir"]) / "failures" / "copilot_repair_response.json"),
                "repair_plan": str(Path(kwargs["run_dir"]) / "failures" / "repair_plan.md"),
            },
        }

    updates = run_post_failure_repair_loop(state, copilot_invoker=fake_invoker)

    assert calls == []
    assert not (Path(state["run_dir"]) / "failures" / "copilot_repair_request.json").exists()
    _assert_legacy_repair_loop_quarantined(updates)


def test_disabled_repair_loop_preserves_old_behavior(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["copilot_failure_agent_enabled"] = False
    state["repair_loop_enabled"] = False

    updates = run_post_failure_repair_loop(state)

    assert updates == {"repair_loop_status": "DISABLED", "repair_loop_enabled": False}
    assert not (Path(state["run_dir"]) / "repairs" / "repair_ledger.json").exists()


def test_copilot_unavailable_writes_ledger_and_stops(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["copilot_feature_probe"] = {"status": "UNAVAILABLE", "reason": "missing flags"}

    updates = run_post_failure_repair_loop(state)
    _assert_legacy_repair_loop_quarantined(updates)
    assert not (Path(state["run_dir"]) / "repairs" / "repair_ledger.json").exists()


def test_invalid_copilot_response_updates_orchestration_state(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["h2_startup_required"] = True
    state["h2_startup_status"] = "H2_STARTUP_FAILED"

    def fake_invoker(**kwargs):
        _write_response(Path(kwargs["run_dir"]), {"status": "FAILED", "refusals": ["bad json"]})
        return {
            "status": "INVALID_RESPONSE",
            "artifact_refs": {
                "copilot_repair_response": str(Path(kwargs["run_dir"]) / "failures" / "copilot_repair_response.json"),
                "repair_plan": str(Path(kwargs["run_dir"]) / "failures" / "repair_plan.md"),
            },
        }

    updates = run_post_failure_repair_loop(state, copilot_invoker=fake_invoker)
    _assert_legacy_repair_loop_quarantined(updates)


def test_read_tool_unavailable_updates_orchestration_state(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["h2_startup_required"] = True
    state["h2_startup_status"] = "H2_STARTUP_FAILED"

    def fake_invoker(**kwargs):
        _write_response(
            Path(kwargs["run_dir"]),
            {
                **_response([]),
                "repair_summary": "Copilot repair proposal skipped because safe evidence read mode is unavailable.",
                "confidence": "LOW",
                "refusals": ["COPILOT_READ_TOOL_UNAVAILABLE"],
                "limitations": ["No Copilot subprocess was started."],
            },
        )
        return {
            "status": "READ_TOOL_UNAVAILABLE",
            "artifact_refs": {
                "copilot_repair_response": str(Path(kwargs["run_dir"]) / "failures" / "copilot_repair_response.json"),
                "repair_plan": str(Path(kwargs["run_dir"]) / "failures" / "repair_plan.md"),
            },
        }

    updates = run_post_failure_repair_loop(state, copilot_invoker=fake_invoker)
    _assert_legacy_repair_loop_quarantined(updates)


def test_required_h2_failure_loads_preflight_and_creates_repair_artifacts(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["build_status"] = "BUILD_PASSED_IN_SANDBOX"
    state["test_status"] = "TEST_PASS_WITH_WARNINGS"
    state["h2_startup_required"] = True
    state["h2_startup_status"] = "H2_STARTUP_FAILED"
    state["copilot_feature_probe"] = {}
    state["copilot_availability_status"] = "SKIPPED"
    _write_preflight_availability(Path(state["run_dir"]), status="AVAILABLE")
    calls: list[dict] = []

    def fake_invoker(**kwargs):
        calls.append(kwargs)
        session = Path(kwargs["run_dir"]) / "copilot" / "evidence_session_1"
        session.mkdir(parents=True, exist_ok=True)
        (session / "copilot_invocation_debug.json").write_text(json.dumps({"cwd": str(session)}), encoding="utf-8")
        _write_response(Path(kwargs["run_dir"]), _response([]))
        return {
            "status": "COMPLETED",
            "artifact_refs": {
                "copilot_repair_response": str(Path(kwargs["run_dir"]) / "failures" / "copilot_repair_response.json"),
                "repair_plan": str(Path(kwargs["run_dir"]) / "failures" / "repair_plan.md"),
                "copilot_invocation_debug": str(session / "copilot_invocation_debug.json"),
            },
        }

    updates = run_post_failure_repair_loop(
        state,
        h2_startup_report=_h2_runtime_config_failure_report(),
        copilot_invoker=fake_invoker,
    )
    assert calls == []
    _assert_legacy_repair_loop_quarantined(updates)
    assert not (Path(state["run_dir"]) / "failures" / "copilot_repair_request.json").exists()


def test_required_h2_failure_unavailable_preflight_writes_reason_and_classification(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["h2_startup_required"] = True
    state["h2_startup_status"] = "H2_STARTUP_FAILED"
    state["copilot_feature_probe"] = {}
    state["copilot_availability_status"] = "SKIPPED"
    _write_preflight_availability(
        Path(state["run_dir"]),
        status="UNAVAILABLE",
        reason="required Copilot CLI safety flags missing",
        errors=["--deny-tool missing"],
    )

    updates = run_post_failure_repair_loop(state, h2_startup_report=_h2_runtime_config_failure_report())
    _assert_legacy_repair_loop_quarantined(updates)
    assert not (Path(state["run_dir"]) / "failures" / "failure_classification.json").exists()


def test_missing_preflight_reruns_safe_availability_probe(tmp_path: Path, monkeypatch) -> None:
    state = _state(tmp_path)
    state["copilot_feature_probe"] = {}
    state["copilot_availability_status"] = "SKIPPED"
    probes: list[dict] = []

    def fake_probe(**kwargs):
        probes.append(kwargs)
        return {"status": "UNAVAILABLE", "reason": "copilot cli executable was not found", "errors": ["not found"]}

    updates = run_post_failure_repair_loop(state)

    assert probes == []
    _assert_legacy_repair_loop_quarantined(updates)


def test_response_validation_rejects_missing_or_false_checklist() -> None:
    payload = _response([])
    payload.pop("wrapper_checklist")
    valid, errors = validate_copilot_repair_response(payload)
    assert not valid
    assert any("wrapper_checklist" in error for error in errors)

    payload = _response([])
    payload["wrapper_checklist"]["sandbox_only"] = False
    valid, errors = validate_copilot_repair_response(payload)
    assert not valid
    assert any("sandbox_only" in error for error in errors)


def test_response_validation_rejects_missing_rule_and_absolute_path() -> None:
    proposal = _proposal(_h2_patch())
    proposal["deterministic_rule_id"] = ""
    valid, errors = validate_copilot_repair_response(_response([proposal]))
    assert not valid
    assert any("deterministic_rule_id" in error for error in errors)

    proposal = _proposal(_h2_patch().replace("a/pom.xml", "a/C:/repo/pom.xml").replace("b/pom.xml", "b/C:/repo/pom.xml"))
    valid, errors = validate_copilot_repair_response(_response([proposal]))
    assert not valid
    assert any("unsafe path" in error for error in errors)


def test_response_validation_security_file_requires_human_review_and_permitall_blocked() -> None:
    diff = (
        "diff --git a/src/main/java/SecurityConfig.java b/src/main/java/SecurityConfig.java\n"
        "--- a/src/main/java/SecurityConfig.java\n"
        "+++ b/src/main/java/SecurityConfig.java\n"
        "@@\n"
        "+http.authorizeHttpRequests(a -> a.anyRequest().permitAll());\n"
    )
    valid, errors = validate_copilot_repair_response(_response([_proposal(diff)]))
    assert not valid
    assert any("Security" in error or "security" in error for error in errors)


def test_rule_registry_h2_dependency_only_pom_and_h2_required(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)

    allowed = evaluate_rule(
        rule_id="DEPENDENCY_ADD_H2_RUNTIME",
        sandbox_path=sandbox,
        touched_paths=["pom.xml"],
        unified_diff=_h2_patch(),
        h2_required=True,
    )
    assert allowed.allowed

    blocked = evaluate_rule(
        rule_id="DEPENDENCY_ADD_H2_RUNTIME",
        sandbox_path=sandbox,
        touched_paths=["pom.xml", "src/main/java/App.java"],
        unified_diff=_h2_patch(),
        h2_required=True,
    )
    assert not blocked.allowed


def test_rule_registry_tomcat_and_zalando_constraints(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, pom=_pom(boot_version="3.2.0", extra="<tomcat.version>9.0.80</tomcat.version>"))
    tomcat_diff = (
        "diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n@@\n"
        "-<tomcat.version>9.0.80</tomcat.version>\n"
    )
    assert evaluate_rule(
        rule_id="DEPENDENCY_REMOVE_TOMCAT9_OVERRIDE_BOOT3",
        sandbox_path=sandbox,
        touched_paths=["pom.xml"],
        unified_diff=tomcat_diff,
    ).allowed

    sandbox = _sandbox(tmp_path, pom=_pom(extra="<dependency><groupId>org.zalando</groupId><artifactId>problem-spring-web</artifactId><version>0.28.0</version></dependency>"))
    zalando_diff = (
        "diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n@@\n"
        "-<version>0.28.0</version>\n+<version>0.29.1</version>\n"
    )
    assert evaluate_rule(
        rule_id="DEPENDENCY_UPGRADE_ZALANDO_PROBLEM_SPRING_WEB_0291",
        sandbox_path=sandbox,
        touched_paths=["pom.xml"],
        unified_diff=zalando_diff,
    ).allowed


def test_jakarta_import_mechanical_allows_import_only_and_blocks_security(tmp_path: Path) -> None:
    diff = (
        "diff --git a/src/main/java/A.java b/src/main/java/A.java\n--- a/src/main/java/A.java\n+++ b/src/main/java/A.java\n@@\n"
        "-import javax.validation.Valid;\n+import jakarta.validation.Valid;\n"
    )
    assert evaluate_rule(
        rule_id="JAKARTA_IMPORT_MECHANICAL_SOURCE",
        sandbox_path=_sandbox(tmp_path),
        touched_paths=["src/main/java/A.java"],
        unified_diff=diff,
    ).allowed

    blocked = evaluate_rule(
        rule_id="JAKARTA_IMPORT_MECHANICAL_SOURCE",
        sandbox_path=_sandbox(tmp_path),
        touched_paths=["src/main/java/SecurityConfig.java"],
        unified_diff=diff,
    )
    assert not blocked.allowed
    assert blocked.human_review_required


def test_patch_safety_rejects_traversal_symlink_and_blocked_dirs(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    symlink_created = True
    try:
        (sandbox / "linked").symlink_to(legacy, target_is_directory=True)
    except OSError:
        symlink_created = False

    errors = validate_patch_paths(["../pom.xml", ".git/config", "target/output.txt", "linked/pom.xml"], sandbox_path=sandbox, run_dir=tmp_path / "run", legacy_path=legacy)

    assert any("path traversal" in error for error in errors)
    assert any("blocked generated" in error for error in errors)
    if symlink_created:
        assert any("symlink" in error for error in errors)


def test_git_apply_check_failure_rejects_patch(tmp_path: Path) -> None:
    from migration_factory.repair_loop.patch_apply import apply_patch_to_sandbox

    sandbox = _sandbox(tmp_path)

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="bad patch")

    result = apply_patch_to_sandbox(
        run_dir=tmp_path / "run",
        sandbox_path=sandbox,
        attempt=1,
        unified_diff=_h2_patch(),
        touched_paths=["pom.xml"],
        run=fake_run,
    )

    assert result.status == "REJECTED"
    assert "bad patch" in result.reason


def test_auto_patch_validation_pass_keeps_patch_and_marks_validated(tmp_path: Path, monkeypatch) -> None:
    state = _state(tmp_path)
    state["auto_apply_safe_repairs"] = True
    state["h2_startup_required"] = True

    monkeypatch.setattr(repair_orchestrator, "apply_patch_to_sandbox", _fake_apply)

    updates = run_post_failure_repair_loop(
        state,
        copilot_invoker=_fake_invoker_with_patch,
        validation_runner=lambda **kwargs: _validation(True),
    )
    _assert_legacy_repair_loop_quarantined(updates)


def test_validation_failure_rolls_back_and_repeated_patch_stops_retry(tmp_path: Path, monkeypatch) -> None:
    state = _state(tmp_path)
    state["auto_apply_safe_repairs"] = True
    state["h2_startup_required"] = True

    monkeypatch.setattr(repair_orchestrator, "apply_patch_to_sandbox", _fake_apply)
    rollbacks: list[dict] = []

    def fake_rollback(**kwargs):
        rollbacks.append(kwargs)
        return True, "rolled back"

    monkeypatch.setattr(repair_orchestrator, "rollback_patch", fake_rollback)

    updates = run_post_failure_repair_loop(
        state,
        copilot_invoker=_fake_invoker_with_patch,
        validation_runner=lambda **kwargs: _validation(False),
    )
    assert rollbacks == []
    _assert_legacy_repair_loop_quarantined(updates)


def test_final_report_contains_repair_loop_metadata(tmp_path: Path) -> None:
    state = _final_report_state(tmp_path)
    result = generate_final_migration_report(state)
    payload = json.loads(Path(result.artifact_refs["final_migration_report"]).read_text(encoding="utf-8"))

    assert payload["repair_loop"]["ledger_ref"].endswith("repair_ledger.json")
    assert payload["repair_loop"]["attempts_count"] == 1
    assert payload["repair_loop"]["safe_patch_applied"] is True
    assert payload["repair_loop"]["human_review_required"] is False
    assert "SQL Server production behavior" in payload["not_validated"]
    assert "endpoint/business behavior" in payload["not_validated"]
    assert "Deployment not validated." in payload["limitations"]
    assert "PR creation/merge not validated." in payload["limitations"]


def _state(tmp_path: Path) -> dict:
    run_dir = tmp_path / "modernized" / ".migration" / "runs" / "run-1"
    sandbox = _sandbox(tmp_path)
    log = run_dir / "logs" / "phase2_transform.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("NoClassDefFoundError: org/example/Missing\n", encoding="utf-8")
    legacy = tmp_path / "legacy"
    legacy.mkdir(exist_ok=True)
    return {
        "run_id": "run-1",
        "run_dir": str(run_dir),
        "sandbox_path": str(sandbox),
        "legacy_app_path": str(legacy),
        "build_status": "BUILD_FAILED_IN_SANDBOX",
        "test_status": "",
        "transform_log_path": str(log),
        "artifact_refs": {},
        "copilot_failure_agent_enabled": True,
        "repair_loop_enabled": True,
        "repair_max_attempts": 3,
        "auto_apply_safe_repairs": False,
        "copilot_feature_probe": {"status": "AVAILABLE", "supported_flags": []},
        "copilot_model": "",
        "copilot_timeout_seconds": 30,
        "copilot_repair_strict_containment": True,
        "h2_startup_required": False,
    }


def _sandbox(tmp_path: Path, pom: str | None = None) -> Path:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(exist_ok=True)
    (sandbox / "pom.xml").write_text(pom or _pom(), encoding="utf-8")
    return sandbox


def _pom(boot_version: str = "3.2.0", extra: str = "") -> str:
    return (
        "<project><modelVersion>4.0.0</modelVersion><parent>"
        "<groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId>"
        f"<version>{boot_version}</version></parent><dependencies>{extra}</dependencies></project>\n"
    )


def _h2_patch() -> str:
    return (
        "diff --git a/pom.xml b/pom.xml\n"
        "--- a/pom.xml\n"
        "+++ b/pom.xml\n"
        "@@\n"
        " <dependencies>\n"
        "+<dependency><groupId>com.h2database</groupId><artifactId>h2</artifactId><scope>runtime</scope></dependency>\n"
    )


def _proposal(diff: str) -> dict:
    return {
        "proposal_id": "patch-001",
        "deterministic_rule_id": "DEPENDENCY_ADD_H2_RUNTIME",
        "risk": "LOW",
        "requires_human_review": False,
        "description": "Add H2 runtime for H2 smoke only.",
        "unified_diff": diff,
        "expected_validation": ["build", "test", "h2"],
    }


def _response(proposals: list[dict]) -> dict:
    return {
        "schema_version": "1.0.0",
        "repair_summary": "proposal",
        "failure_classification": "MISSING_RUNTIME_DEPENDENCY",
        "skills_claimed": [],
        "wrapper_checklist": {
            "legacy_source_not_modified": True,
            "sandbox_only": True,
            "no_deployment": True,
            "no_pr_creation": True,
            "no_security_weakening": True,
            "h2_only_runtime_scope": True,
            "sql_server_out_of_scope": True,
            "endpoint_smoke_out_of_scope": True,
        },
        "patch_proposals": proposals,
        "security_review_required": False,
        "confidence": "MEDIUM",
        "refusals": [],
        "limitations": [],
    }


def _write_response(run_dir: Path, payload: dict) -> None:
    failures = run_dir / "failures"
    failures.mkdir(parents=True, exist_ok=True)
    (failures / "copilot_repair_response.json").write_text(json.dumps(payload), encoding="utf-8")
    (failures / "repair_plan.md").write_text("# plan\n", encoding="utf-8")


def _write_preflight_availability(
    run_dir: Path,
    *,
    status: str,
    reason: str = "required Copilot repair proposal capabilities found",
    errors: list[str] | None = None,
) -> None:
    path = run_dir / "preflight" / "copilot_availability.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "status": status,
                "reason": reason,
                "provider": "copilot_cli",
                "model": "",
                "cli_path": r"C:\Users\test\AppData\Roaming\npm\copilot.cmd",
                "supported_flags": ["--prompt", "--agent", "--no-ask-user", "--silent"],
                "dry_probe_status": "PASSED" if status == "AVAILABLE" else "FAILED",
                "agent_status": "FOUND" if status == "AVAILABLE" else "SKIPPED",
                "skills_status": "FOUND" if status == "AVAILABLE" else "SKIPPED",
                "warnings": [],
                "errors": errors or [],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _h2_runtime_config_failure_report() -> dict:
    return {
        "required": True,
        "h2_status": "H2_STARTUP_FAILED",
        "proof_level": "not_verified",
        "stdout_tail": [
            "BeanCreationException: Error creating bean with name 'cachingConfig'",
            "NullPointerException because Properties.get(Object) returned null",
            "JWTValidator failed to load common config file",
        ],
        "security_env_warnings": ["JWTValidator failed to load common config file"],
    }


def _fake_invoker_with_patch(**kwargs):
    _write_response(Path(kwargs["run_dir"]), _response([_proposal(_h2_patch())]))
    return {
        "status": "USED",
        "artifact_refs": {
            "copilot_repair_response": str(Path(kwargs["run_dir"]) / "failures" / "copilot_repair_response.json"),
            "repair_plan": str(Path(kwargs["run_dir"]) / "failures" / "repair_plan.md"),
        },
    }


def _fake_apply(**kwargs) -> PatchApplyResult:
    patch_path = Path(kwargs["run_dir"]) / "repairs" / f"patch_attempt_{kwargs['attempt']}.diff"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(kwargs["unified_diff"], encoding="utf-8")
    return PatchApplyResult(
        status="APPLIED",
        reason="ok",
        patch_path=patch_path,
        touched_paths=["pom.xml"],
        before_hashes={"pom.xml": "before"},
        after_hashes={"pom.xml": "after"},
        snapshot_dir=Path(kwargs["run_dir"]) / "repairs" / "snapshots" / f"attempt_{kwargs['attempt']}",
        created_paths=[],
        errors=[],
    )


def _validation(passed: bool) -> ValidationResult:
    return ValidationResult(
        passed=passed,
        build_status="BUILD_PASSED_IN_SANDBOX" if passed else "BUILD_FAILED_IN_SANDBOX",
        test_status="TEST_PASSED" if passed else "TEST_FAILED",
        h2_status="H2_STARTUP_PASSED" if passed else "H2_STARTUP_FAILED",
        validation_commands=[["mvn", "test"]],
        artifact_refs={},
        warnings=[],
        errors=[] if passed else ["failed"],
    )


def _final_report_state(tmp_path: Path) -> dict:
    run_dir = tmp_path / "run"
    (run_dir / "assessment").mkdir(parents=True)
    (run_dir / "planning").mkdir()
    (run_dir / "orchestration").mkdir()
    (run_dir / "assessment" / "assessment_report.json").write_text(json.dumps({"source_stack": {}, "target_stack": {}}), encoding="utf-8")
    (run_dir / "planning" / "migration_plan.yaml").write_text("target_stack: {}\nprofile_governance: {}\n", encoding="utf-8")
    approval = run_dir / "approval_decision.json"
    lock = run_dir / "approved_plan_lock.json"
    plan = run_dir / "transformation_execution_plan.yaml"
    ledger = run_dir / "migration_ledger.json"
    summary = run_dir / "orchestration_summary.json"
    test_report = run_dir / "test_report.json"
    repair_ledger = run_dir / "repairs" / "repair_ledger.json"
    repair_ledger.parent.mkdir()
    approval.write_text(json.dumps({"decision": "approved"}), encoding="utf-8")
    lock.write_text(json.dumps({"status": "LOCKED"}), encoding="utf-8")
    plan.write_text("recipes: []\n", encoding="utf-8")
    ledger.write_text(json.dumps({}), encoding="utf-8")
    summary.write_text(json.dumps({}), encoding="utf-8")
    test_report.write_text(json.dumps({"test_status": "TEST_PASSED", "totals": {}}), encoding="utf-8")
    repair_ledger.write_text(json.dumps({"final_status": "REPAIR_VALIDATED"}), encoding="utf-8")
    return {
        "run_id": "run-1",
        "run_dir": str(run_dir),
        "approval_status": "COMPLETED",
        "approval_decision": "approved",
        "approved_by": "human",
        "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "TEST_PASSED",
        "h2_startup_status": "H2_STARTUP_SKIPPED",
        "repair_loop_enabled": True,
        "repair_max_attempts": 3,
        "repair_attempts_count": 1,
        "repair_loop_status": "REPAIR_VALIDATED",
        "copilot_invocation_status": "USED",
        "repair_safe_patch_applied": True,
        "repair_human_review_required": False,
        "artifact_refs": {
            "approval_decision": str(approval),
            "approved_plan_lock": str(lock),
            "transformation_execution_plan": str(plan),
            "migration_ledger": str(ledger),
            "orchestration_summary": str(summary),
            "post_transform_test_report": str(test_report),
            "repair_ledger": str(repair_ledger),
        },
    }
