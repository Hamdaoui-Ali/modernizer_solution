from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

import migration_factory.orchestrator.preflight as preflight_module
import migration_factory.copilot_cli as copilot_cli_module
import migration_factory.repair_loop.orchestrator as repair_orchestrator
from migration_factory.orchestrator import phase_services as phase_services_module
from migration_factory.agents.failure_classifier import classify_failure
from migration_factory.agents.h2_runtime_startup_agent import build_h2_startup_report, write_h2_config
from migration_factory.agents.openrewrite_diff_safety_agent import scan_openrewrite_diff
from migration_factory.copilot_repair.evidence_session import create_evidence_session, finalize_evidence_session
from migration_factory.copilot_repair.adapter import invoke_copilot_repair
from migration_factory.copilot_repair.feature_probe import probe_copilot_availability
from migration_factory.copilot_repair.response_validator import (
    parse_copilot_stdout,
    validate_copilot_repair_response,
)
from migration_factory.copilot_repair.request_builder import COPILOT_RESPONSE_TEMPLATE
from migration_factory.copilot_repair.skill_validator import validate_agent_file, validate_skill_file
from migration_factory.orchestrator.preflight import PreflightError, build_langgraph_config, validate_preflight
from migration_factory.orchestrator.state import build_initial_state
from migration_factory.transform_v1_after_approval import STATUS_APPLIED, TransformSandboxResult


HELP_ALL_FLAGS = """
--prompt --agent --model --available-tools --deny-tool --no-ask-user --silent
--no-custom-instructions --no-remote --disable-builtin-mcps
"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_copilot_feature_probe_requires_required_flags(tmp_path: Path) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="--prompt --agent", stderr="")

    result = probe_copilot_availability(
        repo_root=_repo_root(),
        run_dir=tmp_path,
        provider="copilot_cli",
        run=fake_run,
    )

    assert result["status"] == "UNAVAILABLE"
    assert "--no-ask-user" in result["missing_required_flags"]
    assert "--deny-tool" in result["missing_required_flags"]


def test_copilot_feature_probe_windows_uses_full_cmd_path(tmp_path: Path, monkeypatch) -> None:
    resolved = r"C:\Users\test\AppData\Roaming\npm\copilot.cmd"
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return resolved if name == "copilot.cmd" else None

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "--help":
            return subprocess.CompletedProcess(args, 0, stdout=HELP_ALL_FLAGS, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="GitHub Copilot CLI 1.0.56\n", stderr="")

    monkeypatch.setattr(copilot_cli_module, "_is_windows", lambda: True)
    monkeypatch.setattr(copilot_cli_module.shutil, "which", fake_which)

    result = probe_copilot_availability(
        repo_root=_repo_root(),
        run_dir=tmp_path,
        provider="copilot_cli",
        run=fake_run,
    )
    artifact = json.loads((tmp_path / "preflight" / "copilot_availability.json").read_text(encoding="utf-8"))

    assert result["cli_path"] == resolved
    assert artifact["cli_path"] == resolved
    assert calls[0][0] == resolved
    assert calls[1][0] == resolved
    assert calls[0][0] not in {"copilot", "copilot.CMD"}


def test_copilot_resolver_falls_back_to_copilot_on_non_windows(monkeypatch) -> None:
    calls: list[str] = []

    def fake_which(name: str) -> str | None:
        calls.append(name)
        return "/usr/local/bin/copilot" if name == "copilot" else None

    monkeypatch.setattr(copilot_cli_module, "_is_windows", lambda: False)
    monkeypatch.setattr(copilot_cli_module.shutil, "which", fake_which)

    assert copilot_cli_module.resolve_copilot_cli_executable() == "/usr/local/bin/copilot"
    assert calls == ["copilot"]


def test_copilot_resolver_returns_none_when_unresolved(monkeypatch) -> None:
    monkeypatch.setattr(copilot_cli_module, "_is_windows", lambda: True)
    monkeypatch.setattr(copilot_cli_module.shutil, "which", lambda name: None)

    assert copilot_cli_module.resolve_copilot_cli_executable() is None


def test_copilot_repair_subprocess_uses_availability_full_path(tmp_path: Path) -> None:
    resolved = r"C:\Users\test\AppData\Roaming\npm\copilot.cmd"
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(_valid_response()), stderr="")

    invoke_copilot_repair(
        repo_root=_repo_root(),
        run_dir=tmp_path,
        run_id="run-1",
        request_payload={"failure": "x"},
        availability={"status": "AVAILABLE", "cli_path": resolved, "supported_flags": list(HELP_ALL_FLAGS.split())},
        run=fake_run,
    )

    assert calls[0][0] == resolved
    assert calls[0][0] not in {"copilot", "copilot.CMD"}


def test_copilot_availability_required_does_not_block_preflight(tmp_path: Path, monkeypatch) -> None:
    state = _valid_state(tmp_path)
    state["copilot_required"] = True

    validate_preflight(state, build_langgraph_config(state["run_id"]))


def test_evidence_session_does_not_use_repo_or_sandbox_cwd(tmp_path: Path) -> None:
    run_dir = tmp_path / "modernized" / ".migration" / "runs" / "run-1"
    sandbox = run_dir / "workspaces" / "sandbox"
    sandbox.mkdir(parents=True)

    session = create_evidence_session(
        repo_root=_repo_root(),
        run_dir=run_dir,
        run_id="run-1",
        evidence={"message": "no secrets"},
    )

    assert session.session_dir.is_relative_to(run_dir.resolve())
    assert session.session_dir != _repo_root().resolve()
    assert session.session_dir != sandbox.resolve()
    assert (session.session_dir / "evidence" / "copilot_repair_response.schema.json").is_file()
    assert (session.session_dir / "evidence" / "copilot_repair_response.template.json").is_file()
    assert "evidence/copilot_repair_response.schema.json" in session.manifest["files"]
    assert "evidence/copilot_repair_response.template.json" in session.manifest["files"]


def test_evidence_session_snapshot_detects_mutation(tmp_path: Path) -> None:
    run_dir = tmp_path / "modernized" / ".migration" / "runs" / "run-1"
    session = create_evidence_session(
        repo_root=_repo_root(),
        run_dir=run_dir,
        run_id="run-1",
        evidence={"message": "x"},
    )
    (session.session_dir / "unexpected.txt").write_text("changed\n", encoding="utf-8")

    manifest = finalize_evidence_session(session.session_dir, strict=True)

    assert {"path": "unexpected.txt", "status": "created"} in manifest["unexpected_mutations"]
    assert manifest["errors"]


def test_skill_frontmatter_required(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: bad\n---\nbody\n", encoding="utf-8")

    valid, errors = validate_skill_file(skill)

    assert not valid
    assert any("description" in error for error in errors)


def test_skill_forbids_broad_tools(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "---\nname: bad\ndescription: Bad skill\nallowed-tools: write,shell\n---\nbody\n",
        encoding="utf-8",
    )

    valid, errors = validate_skill_file(skill)

    assert not valid
    assert any("broad tools" in error for error in errors)


def test_agent_validator_rejects_unsafe_agent(tmp_path: Path) -> None:
    agent = tmp_path / "unsafe.agent.md"
    agent.write_text(
        "---\nname: unsafe\ndescription: Unsafe\n---\nYou may deploy and create a PR.\n",
        encoding="utf-8",
    )

    valid, errors = validate_agent_file(agent)

    assert not valid
    assert any("unsafe instruction" in error for error in errors)


def test_openrewrite_deleted_source_file_high_risk() -> None:
    report = scan_openrewrite_diff(
        run_id="run-1",
        diff_text="diff --git a/src/main/java/A.java b/src/main/java/A.java\ndeleted file mode 100644\n",
    )

    assert report["risk_level"] == "HIGH"
    assert report["requires_human_review"] is True


def test_openrewrite_security_change_blocked() -> None:
    report = scan_openrewrite_diff(
        run_id="run-1",
        diff_text=(
            "diff --git a/src/main/java/SecurityConfig.java b/src/main/java/SecurityConfig.java\n"
            "+++ b/src/main/java/SecurityConfig.java\n"
            "+@Bean SecurityFilterChain chain(HttpSecurity http) { return http.authorizeHttpRequests(a -> a.anyRequest().permitAll()).build(); }\n"
        ),
    )

    assert report["status"] == "BLOCKED"
    assert any("permitAll" in item for item in report["high_risk_changes"])


def test_openrewrite_pom_only_aligned_change_low_risk() -> None:
    report = scan_openrewrite_diff(
        run_id="run-1",
        diff_text="diff --git a/pom.xml b/pom.xml\n+++ b/pom.xml\n-<version>2.7.18</version>\n+<version>3.5.0</version>\n",
        planned_pom_changes=["pom.xml"],
    )

    assert report["status"] == "LOW_RISK"
    assert report["requires_human_review"] is False


def test_h2_startup_optional_failure_does_not_break_old_success(tmp_path: Path) -> None:
    class Result:
        succeeded = False
        stdout = ["APPLICATION FAILED TO START"]
        stderr = []

    report = build_h2_startup_report(
        run_id="run-1",
        run_dir=tmp_path,
        sandbox_path=tmp_path,
        required=False,
        runner=lambda **kwargs: Result(),
    )

    assert report["h2_status"] == "H2_STARTUP_WARNING"
    assert report["required"] is False


def test_h2_startup_required_failure_blocks_proof(tmp_path: Path) -> None:
    class Result:
        succeeded = False
        stdout = ["APPLICATION FAILED TO START"]
        stderr = []

    report = build_h2_startup_report(
        run_id="run-1",
        run_dir=tmp_path,
        sandbox_path=tmp_path,
        required=True,
        runner=lambda **kwargs: Result(),
    )

    assert report["h2_status"] == "H2_STARTUP_FAILED"
    assert report["proof_level"] == "not_verified"


def test_h2_config_written_under_run_dir(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    config = write_h2_config(tmp_path / "run")

    assert config.is_relative_to((tmp_path / "run").resolve())
    assert not (sandbox / "src/main/resources/application-migration-smoke.properties").exists()


def test_h2_command_uses_argv_no_shell(tmp_path: Path) -> None:
    calls: list[dict] = []

    class Result:
        succeeded = True
        stdout = ["Started DemoApplication in 1.0 seconds"]
        stderr = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return Result()

    build_h2_startup_report(
        run_id="run-1",
        run_dir=tmp_path,
        sandbox_path=tmp_path,
        runner=fake_runner,
    )

    assert isinstance(calls[0]["command"], list)
    assert calls[0]["command"][0] == "mvn"


def test_h2_report_records_resolved_maven_cmd(tmp_path: Path, monkeypatch) -> None:
    maven_cmd = r"C:\Tools\apache-maven-3.9.15\bin\mvn.cmd"
    monkeypatch.setenv("MAVEN_CMD", maven_cmd)

    class Result:
        succeeded = True
        stdout = ["Started DemoApplication in 1.0 seconds"]
        stderr = []
        resolved_command = [maven_cmd, "spring-boot:run"]

    report = build_h2_startup_report(
        run_id="run-1",
        run_dir=tmp_path,
        sandbox_path=tmp_path,
        runner=lambda **kwargs: Result(),
    )

    assert report["command"][0] == maven_cmd
    assert report["maven_command"] == maven_cmd


def test_failure_classifier_class_not_found_missing_runtime_dependency() -> None:
    report = classify_failure(run_id="run-1", evidence_text="NoClassDefFoundError: org/example/Missing")

    assert report["failure_type"] == "MISSING_RUNTIME_DEPENDENCY"
    assert report["send_to_copilot"] is True


def test_failure_classifier_jakarta_class_not_found() -> None:
    report = classify_failure(run_id="run-1", evidence_text="ClassNotFoundException: jakarta.servlet.Servlet")

    assert report["failure_type"] == "JAKARTA_CLASS_NOT_FOUND"


def test_failure_classifier_keystore_jwt_security_env_warning() -> None:
    report = classify_failure(run_id="run-1", evidence_text="Missing JWT keystore secret")

    assert report["failure_type"] == "SECURITY_ENV_WARNING"
    assert report["migration_blocker"] is False
    assert report["security_env_warning"] is True


def test_failure_classifier_caching_config_npe_is_runtime_config_missing_property() -> None:
    text = (
        "BeanCreationException creating bean 'cachingConfig'\n"
        "NullPointerException: Cannot invoke \"java.lang.Integer.intValue()\" "
        "because java.util.Properties.get(Object) returned null\n"
        "JWTValidator failed to load common config file from common-utils."
    )

    report = classify_failure(
        run_id="run-1",
        evidence_text=text,
        h2_report={"required": True, "h2_status": "H2_STARTUP_FAILED"},
    )

    assert report["failure_type"] == "H2_STARTUP_FAILURE"
    assert report["root_cause"] == "RUNTIME_CONFIG_MISSING_PROPERTY"
    assert "cachingConfig" in report["likely_root_cause"]
    assert "cachingConfig" in report["evidence"]
    assert "Properties.get(Object) returned null" in report["evidence"]
    assert "SECURITY_ENV_WARNING" in report["related_warnings"]


def test_failure_classifier_caching_config_return_value_null_wording() -> None:
    text = (
        "BeanCreationException: Error creating bean with name 'cachingConfig'\n"
        "NullPointerException: Cannot invoke \"java.lang.Integer.intValue()\" "
        "because the return value of \"java.util.Properties.get(Object)\" is null"
    )

    report = classify_failure(
        run_id="run-1",
        evidence_text=text,
        h2_report={"required": True, "h2_status": "H2_STARTUP_FAILED"},
    )

    assert report["failure_type"] == "H2_STARTUP_FAILURE"
    assert report["root_cause"] == "RUNTIME_CONFIG_MISSING_PROPERTY"
    assert "Properties.get(Object) returned null" in report["evidence"]


def test_failure_classifier_caching_config_return_value_null_json_evidence() -> None:
    evidence_text = json.dumps(
        {
            "h2_startup_report": {
                "h2_status": "H2_STARTUP_FAILED",
                "stdout_tail": (
                    "BeanCreationException: Error creating bean with name 'cachingConfig' "
                    "because the return value of \"java.util.Properties.get(Object)\" is null"
                ),
            }
        }
    )

    report = classify_failure(
        run_id="run-1",
        evidence_text=evidence_text,
        h2_report={"required": True, "h2_status": "H2_STARTUP_FAILED"},
    )

    assert report["root_cause"] == "RUNTIME_CONFIG_MISSING_PROPERTY"


def test_failure_classifier_jwt_warning_not_direct_h2_blocker() -> None:
    report = classify_failure(
        run_id="run-1",
        evidence_text="JWTValidator failed to load common config file from common-utils.",
        h2_report={"required": False, "h2_status": "H2_STARTUP_WARNING"},
    )

    assert report["failure_type"] == "SECURITY_ENV_WARNING"
    assert report["send_to_copilot"] is False


def test_required_h2_failure_routes_into_repair_loop_after_sandbox_transform(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "modernized" / ".migration" / "runs" / "run-1"
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True)
    log_path = run_dir / "logs" / "phase2_transform.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("transform ok\n", encoding="utf-8")
    repair_calls: list[dict] = []

    def fake_transform(**kwargs):
        return TransformSandboxResult(
            exit_code=0,
            status=STATUS_APPLIED,
            message="applied",
            sandbox_path=sandbox,
            log_file=log_path,
            build_status="BUILD_PASSED_IN_SANDBOX",
            test_status="TEST_PASS_WITH_WARNINGS",
            test_totals={"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
        )

    def fake_h2_report(**kwargs):
        return {
            "required": True,
            "h2_status": "H2_STARTUP_FAILED",
            "proof_level": "not_verified",
            "stdout_tail": [
                "BeanCreationException: Error creating bean with name 'cachingConfig'",
                "NullPointerException because Properties.get(Object) returned null",
            ],
            "security_env_warnings": ["JWTValidator failed to load common config file"],
        }

    monkeypatch.setattr("migration_factory.transform_v1_after_approval.apply_approved_sandbox_transform", fake_transform)
    monkeypatch.setattr("migration_factory.agents.h2_runtime_startup_agent.build_h2_startup_report", fake_h2_report)
    # The repair loop is now deferred — _merge_repair_updates sets REPAIR_REVIEW_REQUIRED.
    # run_post_failure_repair_loop is no longer called from this code path.

    result = phase_services_module.run_sandbox_transform_phase(
        {
            "run_id": "run-1",
            "run_dir": str(run_dir),
            "legacy_app_path": str(tmp_path / "legacy"),
            "modernized_app_path": str(tmp_path / "modernized"),
            "ai_hub_path": str(tmp_path / "ai-hub"),
            "profile_id": "java17",
            "approved_by": "reviewer",
            "artifact_refs": {},
            "h2_startup_required": True,
            "copilot_failure_agent_enabled": True,
            "repair_loop_enabled": True,
            "auto_apply_safe_repairs": False,
        }
    )

    assert result["h2_startup_status"] == "H2_STARTUP_FAILED"
    assert result["final_status"] == "REPAIR_REVIEW_REQUIRED"
    assert result["repair_loop_status"] == "REPAIR_REVIEW_REQUIRED"
    assert result["repair_blocker"] == "f5_reviewed_repair_required"
    assert result["final_proof_level"] == "not_verified"


def test_required_h2_invalid_copilot_response_merges_repair_state(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "modernized" / ".migration" / "runs" / "run-1"
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True)
    log_path = run_dir / "logs" / "phase2_transform.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("transform ok\n", encoding="utf-8")

    def fake_transform(**kwargs):
        return TransformSandboxResult(
            exit_code=0,
            status=STATUS_APPLIED,
            message="applied",
            sandbox_path=sandbox,
            log_file=log_path,
            build_status="BUILD_PASSED_IN_SANDBOX",
            test_status="TEST_PASS_WITH_WARNINGS",
            test_totals={"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
        )

    def fake_h2_report(**kwargs):
        return {
            "required": True,
            "h2_status": "H2_STARTUP_FAILED",
            "proof_level": "not_verified",
            "stdout_tail": ["BeanCreationException creating bean 'cachingConfig'"],
        }

    monkeypatch.setattr("migration_factory.transform_v1_after_approval.apply_approved_sandbox_transform", fake_transform)
    monkeypatch.setattr("migration_factory.agents.h2_runtime_startup_agent.build_h2_startup_report", fake_h2_report)
    # The repair loop is now deferred — _merge_repair_updates sets REPAIR_REVIEW_REQUIRED.
    # Previously passed copilot state is not merged in the new flow.

    result = phase_services_module.run_sandbox_transform_phase(
        {
            "run_id": "run-1",
            "run_dir": str(run_dir),
            "legacy_app_path": str(tmp_path / "legacy"),
            "modernized_app_path": str(tmp_path / "modernized"),
            "ai_hub_path": str(tmp_path / "ai-hub"),
            "profile_id": "java17",
            "approved_by": "reviewer",
            "artifact_refs": {},
            "h2_startup_required": True,
            "copilot_failure_agent_enabled": True,
            "repair_loop_enabled": True,
            "auto_apply_safe_repairs": False,
        }
    )

    assert result["h2_startup_status"] == "H2_STARTUP_FAILED"
    assert result["final_status"] == "REPAIR_REVIEW_REQUIRED"
    assert result["repair_loop_status"] == "REPAIR_REVIEW_REQUIRED"
    assert result["repair_blocker"] == "f5_reviewed_repair_required"
    assert result["final_proof_level"] == "not_verified"


def test_copilot_response_rejects_free_form_output() -> None:
    payload, errors = parse_copilot_stdout("Here is the plan")

    assert payload is None
    assert errors


def test_copilot_response_extracts_single_embedded_json_object() -> None:
    payload, errors = parse_copilot_stdout("Reading request...\n" + json.dumps(_valid_response()) + "\n")

    assert errors == []
    assert payload is not None
    assert payload["repair_summary"] == "Add missing runtime dependency."


def test_copilot_prompt_uses_evidence_session_cwd_and_evidence_file_read(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_run(args, **kwargs):
        calls.append({"args": list(args), **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(_valid_response()), stderr="")

    result = invoke_copilot_repair(
        repo_root=_repo_root(),
        run_dir=tmp_path,
        run_id="run-1",
        request_payload={"failure": "x" * 40000},
        availability={
            "status": "AVAILABLE",
            "cli_path": "copilot",
            "supported_flags": [
                "--prompt",
                "--silent",
                "--no-ask-user",
                "--agent",
                "--available-tools",
                "--deny-tool",
            ],
        },
        run=fake_run,
    )

    assert result["status"] == "COMPLETED"
    session_dir = tmp_path / "copilot" / "evidence_session_1"
    assert Path(calls[0]["cwd"]) == session_dir
    prompt = calls[0]["args"][calls[0]["args"].index("--prompt") + 1]
    assert len(prompt) < 1000
    assert "./evidence/copilot_repair_request.json" in prompt
    assert "./evidence/copilot_repair_response.schema.json" in prompt
    assert "./evidence/copilot_repair_response.template.json" in prompt
    assert '"failure"' not in prompt
    assert '"additionalProperties": false' not in prompt
    assert '"patch_proposals": []' not in prompt
    assert "x" * 100 not in prompt
    assert "Return exactly one JSON object" in prompt
    assert "Do not apply patches." in prompt
    assert "--available-tools=read,skill" in calls[0]["args"]
    assert "--deny-tool=write,shell,url,memory" in calls[0]["args"]
    assert "--deny-tool=read,write,shell,url,memory" not in calls[0]["args"]
    debug = json.loads(Path(result["artifact_refs"]["copilot_invocation_debug"]).read_text(encoding="utf-8"))
    assert debug["cwd"] == str(session_dir)
    assert debug["prompt_mode"] == "evidence_file_read"
    assert debug["prompt_size_chars"] == len(prompt)
    assert debug["prompt_size_chars"] < 1000
    assert debug["read_tool_enabled"] is True
    assert "evidence/copilot_repair_request.json" in debug["files"]
    assert "evidence/copilot_repair_response.schema.json" in debug["files"]
    assert "evidence/copilot_repair_response.template.json" in debug["files"]
    assert debug["evidence_files_present"] == [
        "evidence/copilot_repair_request.json",
        "evidence/copilot_repair_response.schema.json",
        "evidence/copilot_repair_response.template.json",
    ]
    assert "[REDACTED]" in debug["command"]
    command_text = " ".join(debug["command"])
    assert "x" * 100 not in command_text
    assert "copilot_repair_request.json" not in command_text
    assert '"failure"' not in command_text
    assert "x" * 100 not in debug["prompt_excerpt"]


def test_copilot_read_tool_unavailable_fails_closed_before_invocation(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_run(args, **kwargs):
        calls.append({"args": list(args), **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(_valid_response()), stderr="")

    result = invoke_copilot_repair(
        repo_root=_repo_root(),
        run_dir=tmp_path,
        run_id="run-1",
        request_payload={"failure_classification": {"failure_type": "H2_STARTUP_FAILURE"}},
        availability={
            "status": "AVAILABLE",
            "cli_path": "copilot",
            "supported_flags": ["--prompt", "--silent", "--no-ask-user", "--agent", "--deny-tool"],
        },
        run=fake_run,
    )

    assert calls == []
    assert result["status"] == "READ_TOOL_UNAVAILABLE"
    response = json.loads(Path(result["artifact_refs"]["copilot_repair_response"]).read_text(encoding="utf-8"))
    assert response["repair_summary"] == "Copilot repair proposal skipped because safe evidence read mode is unavailable."
    assert response["failure_classification"] == "H2_STARTUP_FAILURE"
    assert response["patch_proposals"] == []
    assert response["confidence"] == "LOW"
    assert response["refusals"] == ["COPILOT_READ_TOOL_UNAVAILABLE"]
    debug = json.loads(Path(result["artifact_refs"]["copilot_invocation_debug"]).read_text(encoding="utf-8"))
    assert debug["prompt_mode"] == "evidence_file_read"
    assert debug["read_tool_enabled"] is False


def test_copilot_response_schema_validates() -> None:
    valid, errors = validate_copilot_repair_response(_valid_response())

    assert valid
    assert errors == []


def test_copilot_response_template_validates_when_uncertain() -> None:
    payload = dict(COPILOT_RESPONSE_TEMPLATE)
    payload["limitations"] = ["Unable to identify a deterministic patch from evidence."]

    valid, errors = validate_copilot_repair_response(payload)

    assert valid
    assert errors == []


def test_copilot_response_schema_rejects_unexpected_top_level_fields() -> None:
    payload = _valid_response()
    payload["overall_recommendation"] = "repair"
    payload["generated_by"] = "copilot"

    valid, errors = validate_copilot_repair_response(payload)

    assert not valid
    assert any("unexpected top-level fields" in error for error in errors)
    assert any("overall_recommendation" in error for error in errors)
    assert any("generated_by" in error for error in errors)


def test_copilot_response_schema_rejects_missing_required_fields() -> None:
    valid, errors = validate_copilot_repair_response({"status": "FAILED", "refusals": ["bad json"]})

    assert not valid
    assert any("schema_version" in error for error in errors)
    assert any("patch_proposals" in error for error in errors)


def test_copilot_response_schema_rejects_wrong_field_names() -> None:
    payload = _valid_response()
    payload["repair_proposals"] = payload.pop("patch_proposals")
    payload["needs_human_review"] = payload.pop("security_review_required")

    valid, errors = validate_copilot_repair_response(payload)

    assert not valid
    assert any("patch_proposals" in error for error in errors)
    assert any("security_review_required" in error for error in errors)
    assert any("repair_proposals" in error for error in errors)
    assert any("needs_human_review" in error for error in errors)
    assert any("Copilot ignored response template/schema." in error for error in errors)


def test_copilot_invocation_rejects_wrong_response_field_names(tmp_path: Path) -> None:
    payload = _valid_response()
    payload["repair_proposals"] = payload.pop("patch_proposals")
    payload["needs_human_review"] = payload.pop("security_review_required")

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    result = invoke_copilot_repair(
        repo_root=_repo_root(),
        run_dir=tmp_path,
        run_id="run-1",
        request_payload={"failure": "x"},
        availability={
            "status": "AVAILABLE",
            "cli_path": "copilot",
            "supported_flags": ["--prompt", "--available-tools", "--deny-tool"],
        },
        run=fake_run,
    )
    response = json.loads(Path(result["artifact_refs"]["copilot_repair_response"]).read_text(encoding="utf-8"))

    assert result["status"] == "INVALID_RESPONSE"
    assert response["status"] == "FAILED"
    assert any("patch_proposals" in item for item in response["refusals"])
    assert any("security_review_required" in item for item in response["refusals"])
    assert any("unexpected top-level fields" in item for item in response["refusals"])
    assert any("Copilot ignored response template/schema." in item for item in response["refusals"])


def test_final_report_never_claims_sqlserver_or_endpoint_validation(tmp_path: Path) -> None:
    from tests.test_final_report import _successful_state
    from migration_factory.orchestrator.summary import finalize_orchestration_state

    result = finalize_orchestration_state(_successful_state(tmp_path))
    payload = json.loads(Path(result["artifact_refs"]["final_migration_report"]).read_text(encoding="utf-8"))

    assert "SQL Server production behavior" in payload["not_validated"]
    assert "endpoint/business behavior" in payload["not_validated"]
    assert "SQL Server production behavior not validated." in payload["limitations"]


def _valid_response() -> dict:
    return {
        "schema_version": "1.0.0",
        "repair_summary": "Add missing runtime dependency.",
        "failure_classification": "MISSING_RUNTIME_DEPENDENCY",
        "skills_claimed": ["dependency-repair"],
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
        "patch_proposals": [],
        "security_review_required": False,
        "confidence": "MEDIUM",
        "refusals": [],
        "limitations": [],
    }


def _valid_state(tmp_path: Path) -> dict:
    legacy_app_path = tmp_path / "legacy"
    modernized_app_path = tmp_path / "modernized"
    ai_hub_path = tmp_path / "ai-hub"
    legacy_app_path.mkdir()
    (ai_hub_path / "profiles").mkdir(parents=True)
    (ai_hub_path / "profiles" / "java17.yaml").write_text("id: java17\n", encoding="utf-8")
    return build_initial_state(
        run_id="run-001",
        legacy_app_path=str(legacy_app_path),
        modernized_app_path=str(modernized_app_path),
        ai_hub_path=str(ai_hub_path),
        profile_id="java17",
    )
