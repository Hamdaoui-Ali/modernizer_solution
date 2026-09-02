from __future__ import annotations

import json
import inspect
from subprocess import CompletedProcess, TimeoutExpired
from pathlib import Path

import pytest

import migration_factory.orchestrator.copilot_assist as orchestrator_copilot_module
import migration_factory.orchestrator.graph as graph_module
import migration_factory.final_report.copilot as copilot_module
from migration_factory.copilot_assist.providers import DeterministicCopilotProvider, ProviderResult
from migration_factory.final_report.copilot import (
    CopilotAdapterStatus,
    build_copilot_report_request,
    detect_copilot_cli_status,
    generate_copilot_report,
    generate_copilot_report_skeleton,
    load_copilot_report_manifest,
    render_copilot_report_template,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
AI_HUB = REPO_ROOT / "modernizer-solution-ai-hub"


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


def test_graph_runs_report_context_before_copilot_final_report(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    _patch_successful_graph(monkeypatch, tmp_path, calls)
    app = graph_module.build_graph(
        phase_services=_graph_services(calls),
        approval_record_service=_recording_approval_record(calls),
        sandbox_transform_service=_recording_sandbox_transform(calls),
    )
    state = _graph_state(tmp_path, copilot_report_enabled=True)

    result = app.invoke(state)

    assert calls == ["analysis", "planning", "assessment", "approval", "approval_record", "sandbox_transform", "final_report"]
    assert result["copilot_phase_statuses"] == {}
    assert result["copilot_artifact_refs"] == {}
    assert result["final_status"] == "TRANSFORM_APPLIED_IN_SANDBOX"
    assert result["orchestration_status"] == "PASS"


def test_graph_skips_disabled_copilot_final_report(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    _patch_successful_graph(monkeypatch, tmp_path, calls)

    class FailingService:
        def __init__(self, state):
            raise AssertionError("Copilot final report should be skipped")

    app = graph_module.build_graph(
        phase_services=_graph_services(calls),
        approval_record_service=_recording_approval_record(calls),
        sandbox_transform_service=_recording_sandbox_transform(calls),
    )

    result = app.invoke(_graph_state(tmp_path, copilot_report_enabled=False))

    assert calls == ["analysis", "planning", "assessment", "approval", "approval_record", "sandbox_transform", "final_report"]
    assert result["copilot_phase_statuses"] == {}


def test_copilot_final_report_cannot_change_verdict_or_statuses(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    _patch_successful_graph(monkeypatch, tmp_path, calls)
    app = graph_module.build_graph(
        phase_services=_graph_services(calls),
        approval_record_service=_recording_approval_record(calls),
        sandbox_transform_service=_recording_sandbox_transform(calls),
    )
    state = _graph_state(tmp_path, copilot_report_enabled=True)

    result = app.invoke(state)

    assert result["final_status"] == "TRANSFORM_APPLIED_IN_SANDBOX"
    assert result["orchestration_status"] == "PASS"
    assert result["analysis_status"] == "PASS"
    assert result["planning_status"] == "PASS"
    assert result["assessment_status"] == "PASS"
    assert result["blockers"] == []
    assert result["warnings"] == []
    assert result["errors"] == []


def test_summary_does_not_hide_copilot_final_report_execution() -> None:
    import migration_factory.orchestrator.summary as summary_module

    source = inspect.getsource(summary_module)

    assert "generate_copilot_report(" not in source
    assert "generate_final_report(" not in source
    assert "_generate_optional_copilot_final_report" not in source
    assert "interrupt(" not in inspect.getsource(orchestrator_copilot_module)


def test_manifest_loads_from_ai_hub_and_resolves_paths() -> None:
    manifest = load_copilot_report_manifest(AI_HUB)

    assert manifest.id == "copilot_final_migration_report_v1"
    assert manifest.version == "1.0.0"
    assert manifest.engine == "github_copilot"
    assert manifest.advisory_only is True
    assert manifest.template_path == AI_HUB / "templates" / "reports" / "copilot_final_migration_report_v1.md"
    assert manifest.output_file == Path("final/copilot_migration_report.md")
    assert manifest.request_file == Path("final/copilot_report_request.json")
    assert manifest.response_file == Path("final/copilot_report_response.json")


def test_missing_required_artifact_fails_report_generation(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    (run_dir / "approval" / "approval_decision.json").unlink()
    manifest = load_copilot_report_manifest(AI_HUB)

    request = build_copilot_report_request(run_dir, manifest)

    assert request.missing_required == ["approval/approval_decision.json"]
    with pytest.raises(ValueError, match="approval/approval_decision.json"):
        generate_copilot_report_skeleton(run_dir, AI_HUB)
    assert not (run_dir / "final" / "copilot_migration_report.md").exists()


def test_missing_optional_artifact_is_warning_not_blocker(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    manifest = load_copilot_report_manifest(AI_HUB)

    request = build_copilot_report_request(run_dir, manifest)

    assert request.missing_required == []
    assert request.missing_optional == [
        "performance/timing_report.json",
        "workspaces/sandbox/.migration/ledger.json",
        "transformation/transformation_execution_plan.yaml",
    ]
    assert any("missing optional Copilot report artifact" in warning for warning in request.warnings)


def test_deterministic_render_writes_request_response_and_report(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)

    result = generate_copilot_report_skeleton(
        run_dir,
        AI_HUB,
        context={
            "application_name": "payments-service",
            "profile_id": "springboot-3.5-java17",
            "legacy_app_path": "/legacy",
            "final_verdict": "TRANSFORM_APPLIED_IN_SANDBOX",
        },
        status=CopilotAdapterStatus(
            model="configured:gpt-5",
            connectivity="connected",
            report_status="generated",
        ),
    )

    request_path = Path(result["artifact_refs"]["copilot_report_request"])
    response_path = Path(result["artifact_refs"]["copilot_report_response"])
    report_path = Path(result["artifact_refs"]["copilot_migration_report"])
    assert request_path == run_dir / "final" / "copilot_report_request.json"
    assert response_path == run_dir / "final" / "copilot_report_response.json"
    assert report_path == run_dir / "final" / "copilot_migration_report.md"
    assert request_path.is_file()
    assert response_path.is_file()
    assert report_path.is_file()

    report = report_path.read_text(encoding="utf-8")
    assert "{{" not in report
    assert "payments-service" in report
    assert "`github_copilot`" in report
    assert "`gpt-5`" in report

    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert response["provider"] == "github_copilot"
    assert response["adapter"] == "local_deterministic_template"
    assert response["connectivity"] == "connected"
    assert response["model"] == "gpt-5"
    assert response["auth_status"] == "unknown"
    assert response["cli_status"] == "not_installed"
    assert response["report_status"] == "generated"
    assert response["advisory_only"] is True
    assert response["can_approve"] is False
    assert response["can_transform"] is False
    assert response["can_change_gates"] is False
    assert response["can_mutate_source"] is False
    assert response["can_override_status"] is False


def test_deterministic_provider_returns_final_report_fallback_result() -> None:
    provider = DeterministicCopilotProvider()

    result = provider.final_report_fallback(
        run_id="run-001",
        context={
            "statuses": {
                "final": "TRANSFORM_APPLIED_IN_SANDBOX",
                "build": "BUILD_PASSED_IN_SANDBOX",
                "tests": "TEST_PASSED",
            },
            "warnings": ["manual review required"],
        },
        output_ref="final/copilot_migration_report.md",
        warnings=["copilot CLI unavailable; deterministic fallback used"],
    )
    payload = result.to_dict()

    assert isinstance(result, ProviderResult)
    assert payload["schema_version"] == "1.0.0"
    assert payload["run_id"] == "run-001"
    assert payload["provider"] == "deterministic"
    assert payload["model"] == "local-template"
    assert payload["status"] == "generated_with_fallback"
    assert payload["advisory_only"] is True
    assert payload["fallback_used"] is True
    assert payload["output_ref"] == "final/copilot_migration_report.md"
    assert payload["validation"]["valid"] is True
    assert payload["validation"]["uses_provided_context_only"] is True
    assert payload["warnings"] == ["copilot CLI unavailable; deterministic fallback used"]
    assert payload["content"]["statuses"]["final"] == "TRANSFORM_APPLIED_IN_SANDBOX"


def test_application_name_is_derived_from_legacy_path(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    manifest = load_copilot_report_manifest(AI_HUB)

    request = build_copilot_report_request(
        run_dir,
        manifest,
        context={
            "legacy_app_path": r"%USERPROFILE%\Desktop\shoppoc-app",
            "profile_id": "springboot-2.7-to-3.5-java17",
        },
    )

    assert request.payload["template_context"]["application_name"] == "shoppoc-app"


def test_source_framework_is_safely_derived_from_boot_27_profile(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    manifest = load_copilot_report_manifest(AI_HUB)

    request = build_copilot_report_request(
        run_dir,
        manifest,
        context={"profile_id": "springboot-2.7-to-3.5-java17"},
    )

    context = request.payload["template_context"]
    assert context["source_spring_framework_version"] == "Spring Framework 5.x"


def test_empty_blockers_render_one_clean_line(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)

    result = generate_copilot_report_skeleton(run_dir, AI_HUB)
    report = Path(result["artifact_refs"]["copilot_migration_report"]).read_text(encoding="utf-8")
    blocker_section = report.split("Blockers:", 1)[1].split("Risk summary:", 1)[0]

    assert "No blockers recorded." in report
    assert "| `` | `` |" not in blocker_section


def test_empty_deterministic_patches_render_one_clean_row(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)

    result = generate_copilot_report_skeleton(run_dir, AI_HUB)
    report = Path(result["artifact_refs"]["copilot_migration_report"]).read_text(encoding="utf-8")
    patch_section = report.split("## 8. Deterministic Patches", 1)[1].split("Patch traceability:", 1)[0]

    assert patch_section.count("No deterministic patches recorded for this profile.") == 1
    assert "`not_available`" not in patch_section


def test_review_focus_is_enriched_from_security_warning(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    final_report = run_dir / "final" / "migration_report.json"
    payload = json.loads(final_report.read_text(encoding="utf-8"))
    payload["warnings"] = ["OPENREWRITE_SECURITY_CONFIG_TOUCHED: OpenRewrite touched security config."]
    final_report.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = generate_copilot_report_skeleton(run_dir, AI_HUB)
    report = Path(result["artifact_refs"]["copilot_migration_report"]).read_text(encoding="utf-8")

    assert "Review Spring Security/auth behavior." in report


def test_live_success_metadata_does_not_report_fallback_used(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)

    result = generate_copilot_report_skeleton(
        run_dir,
        AI_HUB,
        status=CopilotAdapterStatus(
            adapter="copilot_cli",
            connectivity="connected",
            report_status="generated",
            auth_status="authenticated",
            cli_status="installed",
        ),
    )
    report = Path(result["artifact_refs"]["copilot_migration_report"]).read_text(encoding="utf-8")

    assert "| Adapter | `copilot_cli` |" in report
    assert "| Fallback Used | `false` |" in report


def test_output_validation_rejects_missing_application_name_when_legacy_path_exists(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    manifest = load_copilot_report_manifest(AI_HUB)
    request = build_copilot_report_request(
        run_dir,
        manifest,
        context={"legacy_app_path": r"%USERPROFILE%\Desktop\shoppoc-app"},
    )
    bad_report = VALID_COPILOT_MARKDOWN.replace("Generated.", "| Application | `not_available` |", 1)
    good_report = VALID_COPILOT_MARKDOWN.replace("Generated.", "| Application | `shoppoc-app` |", 1)

    with pytest.raises(RuntimeError, match="application is not_available"):
        copilot_module._validate_copilot_markdown(bad_report, request_payload=request.payload)
    copilot_module._validate_copilot_markdown(good_report, request_payload=request.payload)


def test_renderer_uses_deterministic_placeholder_substitution(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    template.write_text("A={{a}}\nB={{b}}\nMissing={{missing}}\n", encoding="utf-8")

    assert render_copilot_report_template(template, {"a": "one", "b": 2}) == "A=one\nB=2\nMissing=\n"


def test_copilot_cli_detector_reports_not_installed(monkeypatch) -> None:
    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", lambda name: None)

    status = detect_copilot_cli_status(env={})

    assert status.to_dict() == {
        "provider": "github_copilot",
        "adapter": "local_deterministic_template",
        "model": "gpt-5-mini",
        "connectivity": "not_configured",
        "report_status": "skipped",
        "auth_status": "unknown",
        "cli_status": "not_installed",
        "resolved_executable_basename": "",
    }


def test_copilot_cli_detector_reports_installed_with_gh_auth(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        return f"/tools/{name}" if name in {"copilot", "gh"} else None

    def fake_run(*args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args[0], returncode=0, stdout="Logged in to github.com account ada\n", stderr="")

    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", fake_which)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    status = detect_copilot_cli_status(env={"AI_MIGRATION_COPILOT_MODEL": "gpt-5"})

    assert status.connectivity == "connected"
    assert status.adapter == "copilot_cli"
    assert status.model == "gpt-5"
    assert status.auth_status == "authenticated"
    assert status.cli_status == "installed"


def test_copilot_cli_detector_treats_version_update_error_as_installed(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        if name == "copilot":
            return r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd"
        if name == "gh":
            return r"C:\Program Files\GitHub CLI\gh.exe"
        if name == "where.exe":
            return r"C:\Windows\System32\where.exe"
        return None

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        calls.append(list(args))
        if args[1:] == ["version"]:
            return CompletedProcess(
                args=args,
                returncode=1,
                stdout="GitHub Copilot CLI 1.0.51\n",
                stderr="rate limit exceeded while checking for updates\n",
            )
        if args[1:] == ["auth", "status"]:
            return CompletedProcess(args=args, returncode=0, stdout="Logged in to github.com account ada\n", stderr="")
        if args[1:] == ["copilot"]:
            return CompletedProcess(args=args, returncode=0, stdout=r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", fake_which)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    status = detect_copilot_cli_status(
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
        }
    )

    assert status.provider == "github_copilot"
    assert status.adapter == "copilot_cli"
    assert status.model == "gpt-5-mini"
    assert status.connectivity == "connected"
    assert status.auth_status == "authenticated"
    assert status.cli_status == "installed"
    assert calls == [
        [r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd", "version"],
        [r"C:\Program Files\GitHub CLI\gh.exe", "auth", "status"],
    ]


def test_copilot_cli_detector_uses_where_when_which_misses_copilot(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        if name == "where.exe":
            return r"C:\Windows\System32\where.exe"
        return None

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        calls.append(list(args))
        if args[1:] == ["copilot"]:
            return CompletedProcess(
                args=args,
                returncode=0,
                stdout="C:\\Users\\ada\\AppData\\Roaming\\npm\\copilot\nC:\\Users\\ada\\AppData\\Roaming\\npm\\copilot.cmd\n",
                stderr="",
            )
        if args[1:] == ["version"]:
            return CompletedProcess(args=args, returncode=1, stdout="GitHub Copilot CLI 1.0.51\n", stderr="update check failed")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", fake_which)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    status = detect_copilot_cli_status(env={"AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli"})

    assert status.adapter == "copilot_cli"
    assert status.connectivity == "unavailable"
    assert status.auth_status == "unknown"
    assert status.cli_status == "installed"
    assert status.resolved_executable == r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd"
    assert status.to_dict()["resolved_executable_basename"] == "copilot.cmd"
    assert calls[:2] == [
        [r"C:\Windows\System32\where.exe", "copilot"],
        [r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd", "version"],
    ]


def test_copilot_cli_detector_prefers_cmd_on_windows(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        if name == "copilot.cmd":
            return r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd"
        if name == "copilot":
            return r"C:\Users\ada\AppData\Roaming\npm\copilot"
        return None

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        calls.append(list(args))
        if args[1:] == ["version"]:
            return CompletedProcess(args=args, returncode=0, stdout="GitHub Copilot CLI 1.0.51\n", stderr="")
        return CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(copilot_module, "_is_windows", lambda: True)
    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", fake_which)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    status = detect_copilot_cli_status(env={"AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli"})

    assert status.cli_status == "installed"
    assert status.resolved_executable == r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd"
    assert calls[0] == [r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd", "version"]


def test_copilot_cli_detector_windows_patch_does_not_mutate_os_name(monkeypatch) -> None:
    import os

    original_os_name = os.name
    monkeypatch.setattr(copilot_module, "_is_windows", lambda: True)

    detect_copilot_cli_status(env={"AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli"})

    assert os.name == original_os_name
    Path.cwd()


def test_copilot_cli_detector_normalizes_configured_model(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        return f"/tools/{name}" if name in {"copilot", "gh"} else None

    def fake_run(*args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args[0], returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", fake_which)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    status = detect_copilot_cli_status(env={"AI_MIGRATION_COPILOT_MODEL": "configured:gpt-5-mini"})

    assert status.model == "gpt-5-mini"


def test_copilot_cli_detector_reports_auth_unknown_without_gh(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        return "/tools/copilot" if name == "copilot" else None

    def fake_run(*args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args[0], returncode=0, stdout="GitHub Copilot CLI 1.0.51\n", stderr="")

    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", fake_which)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    status = detect_copilot_cli_status(env={})

    assert status.connectivity == "unavailable"
    assert status.adapter == "copilot_cli"
    assert status.auth_status == "unknown"
    assert status.cli_status == "installed"


def test_request_response_and_report_do_not_persist_secrets(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    token = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    final_report = run_dir / "final" / "migration_report.json"
    payload = json.loads(final_report.read_text(encoding="utf-8"))
    payload["github_token"] = token
    payload["nested"] = {"password": "do-not-store"}
    final_report.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = generate_copilot_report_skeleton(
        run_dir,
        AI_HUB,
        context={"application_name": token, "profile_id": "secret-profile"},
    )

    for ref in result["artifact_refs"].values():
        content = Path(ref).read_text(encoding="utf-8")
        assert token not in content
        assert "do-not-store" not in content
    request_payload = json.loads(Path(result["artifact_refs"]["copilot_report_request"]).read_text(encoding="utf-8"))
    assert request_payload["artifacts"]["required"]["final/migration_report.json"]["github_token"] == "[REDACTED]"
    assert request_payload["artifacts"]["required"]["final/migration_report.json"]["nested"]["password"] == "[REDACTED]"


def test_invoke_copilot_cli_uses_stdin_programmatic_mode(monkeypatch) -> None:
    resolved_path = r"C:\Users\x\AppData\Roaming\npm\copilot.cmd"
    seen: dict[str, object] = {}

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        seen["args"] = list(args)
        seen["kwargs"] = dict(kwargs)
        return CompletedProcess(args=args, returncode=0, stdout=VALID_COPILOT_MARKDOWN, stderr="")

    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    markdown = copilot_module._invoke_copilot_cli("hello", "gpt-5-mini", 12, resolved_path)

    assert markdown.startswith("# Copilot Final Migration Report")
    assert seen["args"][:5] == [resolved_path, "-s", "--no-ask-user", "--model", "gpt-5-mini"]
    assert "--log-dir" in seen["args"]
    assert "--log-level" in seen["args"]
    assert seen["kwargs"]["input"] == "hello"
    assert seen["kwargs"]["text"] is True
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["timeout"] == 30
    assert "-p" not in seen["args"]
    assert "--prompt" not in seen["args"]


def test_strict_prompt_includes_template_context_and_markdown_only_instruction(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    manifest = load_copilot_report_manifest(AI_HUB)
    request = build_copilot_report_request(run_dir, manifest)
    template = manifest.template_path.read_text(encoding="utf-8")

    prompt = copilot_module._build_strict_copilot_prompt(request.payload, template)

    assert template in prompt
    assert '"artifact_refs_summary": {' in prompt
    assert '"template_context": {' in prompt
    assert "Return markdown only" in prompt
    assert "Use this template exactly" in prompt


def test_copilot_output_validation_accepts_required_template_sections() -> None:
    copilot_module._validate_copilot_markdown(VALID_COPILOT_MARKDOWN)


def test_copilot_output_validation_rejects_missing_required_sections() -> None:
    with pytest.raises(RuntimeError, match="missing section"):
        copilot_module._validate_copilot_markdown("# Copilot Final Migration Report\n\n## 1. Summary\n")


def test_copilot_output_validation_rejects_forbidden_execution_claims() -> None:
    with pytest.raises(RuntimeError, match="forbidden Copilot execution claim"):
        copilot_module._validate_copilot_markdown(
            VALID_COPILOT_MARKDOWN + "\nCopilot approved and deployed the application.\n"
        )


def test_copilot_cli_provider_writes_live_markdown_and_response(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return f"/tools/{name}" if name in {"copilot", "gh"} else None

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        calls.append(list(args))
        if args[1:] == ["version"]:
            return CompletedProcess(args=args, returncode=0, stdout="GitHub Copilot CLI 1.0.51\n", stderr="")
        if args[1:] == ["auth", "status"]:
            return CompletedProcess(args=args, returncode=0, stdout="logged in\n", stderr="")
        return CompletedProcess(args=args, returncode=0, stdout=VALID_COPILOT_MARKDOWN, stderr="")

    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", fake_which)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    result = generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "configured:gpt-5-mini",
        },
    )

    response = json.loads(Path(result["artifact_refs"]["copilot_report_response"]).read_text(encoding="utf-8"))
    report = Path(result["artifact_refs"]["copilot_migration_report"]).read_text(encoding="utf-8")
    assert response["adapter"] == "copilot_cli"
    assert response["report_status"] == "generated"
    assert response["model"] == "gpt-5-mini"
    assert "# Copilot Final Migration Report" in report
    assert any(call[:2] == ["/tools/copilot", "-s"] for call in calls)
    prompt_call = next(call for call in calls if call[:2] == ["/tools/copilot", "-s"])
    assert "-p" not in prompt_call
    assert "--prompt" not in prompt_call
    assert "--no-ask-user" in prompt_call
    assert "--model" in prompt_call


def test_copilot_cli_provider_writes_safe_invocation_log(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    resolved_path = r"C:\Users\x\AppData\Roaming\npm\copilot.cmd"

    def fake_detect(**kwargs) -> CopilotAdapterStatus:
        return CopilotAdapterStatus(
            adapter="copilot_cli",
            model="gpt-5-mini",
            connectivity="connected",
            auth_status="authenticated",
            cli_status="installed",
            resolved_executable_path=resolved_path,
        )

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args, returncode=0, stdout=VALID_COPILOT_MARKDOWN, stderr="token=ghp_abcdefghijklmnopqrstuvwxyz123456")

    monkeypatch.setattr(copilot_module, "detect_copilot_cli_status", fake_detect)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
        },
    )

    invocation_path = run_dir / "logs" / "copilot" / "copilot_cli_invocation.json"
    text = invocation_path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["input_mode"] == "stdin"
    assert payload["command_basename"] == "copilot.cmd"
    assert payload["validation_status"] == "passed"
    assert resolved_path not in text
    assert "ghp_" not in text
    assert "environment" not in text.lower()
    assert "Approved compact deterministic context" not in text


def test_copilot_cli_provider_timeout_falls_back_with_diagnostics(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    resolved_path = r"C:\Users\x\AppData\Roaming\npm\copilot.cmd"

    def fake_detect(**kwargs) -> CopilotAdapterStatus:
        return CopilotAdapterStatus(
            adapter="copilot_cli",
            model="gpt-5-mini",
            connectivity="connected",
            auth_status="authenticated",
            cli_status="installed",
            resolved_executable_path=resolved_path,
        )

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        raise TimeoutExpired(cmd=args, timeout=kwargs["timeout"], output="", stderr="still waiting")

    monkeypatch.setattr(copilot_module, "detect_copilot_cli_status", fake_detect)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    result = generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
            "AI_MIGRATION_COPILOT_TIMEOUT_SECONDS": "45",
        },
    )

    response = json.loads(Path(result["artifact_refs"]["copilot_report_response"]).read_text(encoding="utf-8"))
    invocation = json.loads((run_dir / "logs" / "copilot" / "copilot_cli_invocation.json").read_text(encoding="utf-8"))
    assert response["adapter"] == "local_deterministic_template"
    assert response["report_status"] == "generated_with_fallback"
    assert response["fallback_reason"] == "timeout"
    assert response["timed_out"] is True
    assert response["copilot_timeout_seconds"] == 45
    assert response["copilot_input_mode"] == "stdin"
    assert response["copilot_prompt_chars"] > 0
    assert response["copilot_log_dir"] == "logs/copilot"
    assert invocation["timed_out"] is True
    assert invocation["timeout_seconds"] == 45


def test_copilot_prompt_is_compact_and_omits_report_paths(tmp_path: Path) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    report = run_dir / "final" / "migration_report.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["report_paths"] = [f"long/path/{index}/test-report.json" for index in range(2000)]
    report.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    manifest = load_copilot_report_manifest(AI_HUB)
    request = build_copilot_report_request(run_dir, manifest)
    template = manifest.template_path.read_text(encoding="utf-8")

    prompt = copilot_module._build_strict_copilot_prompt(request.payload, template)
    raw_request = json.dumps(request.payload, indent=2, sort_keys=True)

    assert template in prompt
    assert "Use this template exactly" in prompt
    assert '"run_id": "run-001"' in prompt
    assert '"application_name":' in prompt
    assert '"source_stack": {' in prompt
    assert '"target_stack": {' in prompt
    assert "report_paths" not in prompt
    assert len(prompt) < len(raw_request)


def test_copilot_cli_redacts_captured_stderr_tail(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    secret_stderr = (
        "Authorization: Bearer abcdefghijklmnop\n"
        "TOKEN=gho_abcdefghijklmnopqrstuvwxyz123456\n"
        "github_pat_abcdefghijklmnopqrstuvwxyz123456\n"
    )

    def fake_detect(**kwargs) -> CopilotAdapterStatus:
        return CopilotAdapterStatus(
            adapter="copilot_cli",
            model="gpt-5-mini",
            connectivity="connected",
            auth_status="authenticated",
            cli_status="installed",
            resolved_executable_path="/tools/copilot",
        )

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args, returncode=1, stdout="", stderr=secret_stderr)

    monkeypatch.setattr(copilot_module, "detect_copilot_cli_status", fake_detect)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
        },
    )

    stderr_text = (run_dir / "logs" / "copilot" / "copilot_cli_stderr.redacted.log").read_text(encoding="utf-8")
    response_text = (run_dir / "final" / "copilot_report_response.json").read_text(encoding="utf-8")
    assert "gho_" not in stderr_text
    assert "github_pat_" not in stderr_text
    assert "Bearer abcdef" not in stderr_text
    assert "Authorization:" not in stderr_text
    assert "gho_" not in response_text
    assert "github_pat_" not in response_text


def test_copilot_cli_provider_uses_resolved_cmd_path_on_windows(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        if name == "copilot.cmd":
            return r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd"
        if name == "copilot":
            return r"C:\Users\ada\AppData\Roaming\npm\copilot"
        if name == "gh":
            return r"C:\Program Files\GitHub CLI\gh.exe"
        return None

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        calls.append(list(args))
        if args[1:] == ["version"]:
            return CompletedProcess(args=args, returncode=0, stdout="GitHub Copilot CLI 1.0.51\n", stderr="")
        if args[1:] == ["auth", "status"]:
            return CompletedProcess(args=args, returncode=0, stdout="logged in\n", stderr="")
        if args[0].endswith("copilot.cmd") and args[1] == "-s":
            return CompletedProcess(args=args, returncode=0, stdout=VALID_COPILOT_MARKDOWN, stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(copilot_module, "_is_windows", lambda: True)
    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", fake_which)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    result = generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
        },
    )

    response = json.loads(Path(result["artifact_refs"]["copilot_report_response"]).read_text(encoding="utf-8"))
    assert response["adapter"] == "copilot_cli"
    assert response["report_status"] == "generated"
    assert response["resolved_executable_basename"] == "copilot.cmd"
    assert any(call[:2] == [r"C:\Users\ada\AppData\Roaming\npm\copilot.cmd", "-s"] for call in calls)


def test_copilot_cli_provider_uses_internal_resolved_path_without_publishing_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    resolved_path = r"C:\Users\x\AppData\Roaming\npm\copilot.cmd"
    calls: list[list[str]] = []

    def fake_detect(**kwargs) -> CopilotAdapterStatus:
        return CopilotAdapterStatus(
            adapter="copilot_cli",
            model="gpt-5-mini",
            connectivity="connected",
            auth_status="authenticated",
            cli_status="installed",
            resolved_executable_path=resolved_path,
        )

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        calls.append(list(args))
        return CompletedProcess(args=args, returncode=0, stdout=VALID_COPILOT_MARKDOWN, stderr="")

    monkeypatch.setattr(copilot_module, "detect_copilot_cli_status", fake_detect)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    result = generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
        },
    )

    response_path = Path(result["artifact_refs"]["copilot_report_response"])
    request_path = Path(result["artifact_refs"]["copilot_report_request"])
    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert response["adapter"] == "copilot_cli"
    assert response["report_status"] == "generated"
    assert response["resolved_executable_basename"] == "copilot.cmd"
    assert calls[0][:5] == [resolved_path, "-s", "--no-ask-user", "--model", "gpt-5-mini"]
    assert "--log-dir" in calls[0]
    assert "--log-level" in calls[0]
    assert resolved_path not in response_path.read_text(encoding="utf-8")
    assert resolved_path not in request_path.read_text(encoding="utf-8")


def test_copilot_cli_provider_falls_back_when_live_output_fails_template_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)
    resolved_path = r"C:\Users\x\AppData\Roaming\npm\copilot.cmd"

    def fake_detect(**kwargs) -> CopilotAdapterStatus:
        return CopilotAdapterStatus(
            adapter="copilot_cli",
            model="gpt-5-mini",
            connectivity="connected",
            auth_status="authenticated",
            cli_status="installed",
            resolved_executable_path=resolved_path,
        )

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args, returncode=0, stdout="# Wrong Report\n\nCopilot created a PR.\n", stderr="")

    monkeypatch.setattr(copilot_module, "detect_copilot_cli_status", fake_detect)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    result = generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
        },
    )

    response = json.loads(Path(result["artifact_refs"]["copilot_report_response"]).read_text(encoding="utf-8"))
    report = Path(result["artifact_refs"]["copilot_migration_report"]).read_text(encoding="utf-8")
    assert response["adapter"] == "local_deterministic_template"
    assert response["report_status"] == "generated_with_fallback"
    assert "missing section # Copilot Final Migration Report" in "\n".join(response["warnings"])
    assert report.startswith("# Copilot Final Migration Report")


def test_copilot_cli_provider_falls_back_when_installed_status_lacks_internal_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)

    def fake_detect(**kwargs) -> CopilotAdapterStatus:
        return CopilotAdapterStatus(
            adapter="copilot_cli",
            model="gpt-5-mini",
            connectivity="connected",
            auth_status="authenticated",
            cli_status="installed",
        )

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(copilot_module, "detect_copilot_cli_status", fake_detect)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    result = generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
        },
    )

    response = json.loads(Path(result["artifact_refs"]["copilot_report_response"]).read_text(encoding="utf-8"))
    warning = "\n".join(response["warnings"])
    assert response["adapter"] == "local_deterministic_template"
    assert response["cli_status"] == "installed"
    assert response["report_status"] == "generated_with_fallback"
    assert "Copilot executable path was not resolved for live call" in warning
    assert "internal_resolved_executable_path_present=false" in warning
    assert "AppData" not in warning


def test_copilot_cli_provider_falls_back_on_empty_output(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)

    def fake_which(name: str) -> str | None:
        return f"/tools/{name}" if name in {"copilot", "gh"} else None

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        if args[1:] == ["version"]:
            return CompletedProcess(args=args, returncode=0, stdout="GitHub Copilot CLI 1.0.51\n", stderr="")
        if args[1:] == ["auth", "status"]:
            return CompletedProcess(args=args, returncode=0, stdout="logged in\n", stderr="")
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", fake_which)
    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    result = generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
        },
    )

    response = json.loads(Path(result["artifact_refs"]["copilot_report_response"]).read_text(encoding="utf-8"))
    report = Path(result["artifact_refs"]["copilot_migration_report"]).read_text(encoding="utf-8")
    assert response["adapter"] == "local_deterministic_template"
    assert response["report_status"] == "generated_with_fallback"
    assert any(
        "RuntimeError: copilot CLI returned empty output" in warning
        for warning in response["warnings"]
    )
    assert "Copilot Final Migration Report" in report


def test_copilot_cli_provider_fallback_warning_is_debug_safe_when_path_unresolved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = _run_dir_with_required_artifacts(tmp_path)

    monkeypatch.setattr("migration_factory.final_report.copilot.shutil.which", lambda name: None)

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr("migration_factory.final_report.copilot.subprocess.run", fake_run)

    result = generate_copilot_report(
        run_dir,
        AI_HUB,
        env={
            "AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5-mini",
        },
    )

    response = json.loads(Path(result["artifact_refs"]["copilot_report_response"]).read_text(encoding="utf-8"))
    warning = "\n".join(response["warnings"])
    assert response["adapter"] == "local_deterministic_template"
    assert response["report_status"] == "generated_with_fallback"
    assert "FileNotFoundError: Copilot executable path was not resolved for live call" in warning
    assert "ghp_" not in warning


def _run_dir_with_required_artifacts(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run-001"
    for directory in (
        run_dir / "final",
        run_dir / "orchestration",
        run_dir / "approval",
        run_dir / "test" / "post_transform",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    (run_dir / "final" / "migration_report.json").write_text(
        json.dumps(
            {
                "run_id": "run-001",
                "source_stack": {"java": "11", "spring_boot": "2.7.18", "build_tool": "maven"},
                "target_stack": {"java": "17", "spring_boot": "3.5.0", "build_tool": "maven"},
                "risk_level": "medium",
                "requires_human_approval": True,
                "production_allowed": False,
                "approval": {"status": "COMPLETED", "decision": "approved", "approved_by": "ada"},
                "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
                "build_status": "BUILD_PASSED_IN_SANDBOX",
                "test_status": "TEST_PASSED",
                "test_totals": {"tests": 4, "passed": 4, "failures": 0, "errors": 0, "skipped": 0},
                "sandbox_path": "/sandbox",
                "warnings": ["review javax leftovers"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "orchestration" / "orchestration_summary.json").write_text(
        json.dumps(
            {
                "run_id": "run-001",
                "orchestration_status": "PASS",
                "orchestration_artifacts_valid": True,
                "blockers": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "approval" / "approval_decision.json").write_text(
        json.dumps({"decision": "approved", "approved_by": "ada", "source": "cli"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "approval" / "approved_plan_lock.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "test" / "post_transform" / "test_report.json").write_text(
        json.dumps(
            {
                "test_status": "TEST_PASSED",
                "totals": {"tests": 4, "passed": 4, "failures": 0, "errors": 0, "skipped": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def _graph_state(tmp_path: Path, *, copilot_report_enabled: bool) -> dict:
    from migration_factory.orchestrator.state import FULL_SANDBOX_MIGRATION_MODE, build_initial_state

    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
        mode=FULL_SANDBOX_MIGRATION_MODE,
    )
    state["copilot_report_enabled"] = copilot_report_enabled
    state["copilot_provider"] = "deterministic"
    return state


def _graph_services(calls: list[str]):
    from migration_factory.orchestrator.phase_services import PhaseServices

    def run_analysis_phase(state):
        calls.append("analysis")
        return {"analysis_status": "PASS"}

    def run_planning_phase(state):
        calls.append("planning")
        return {"planning_status": "PASS"}

    def run_assessment_phase(state):
        calls.append("assessment")
        return {"assessment_status": "PASS"}

    return PhaseServices(
        run_analysis_phase=run_analysis_phase,
        run_planning_phase=run_planning_phase,
        run_assessment_phase=run_assessment_phase,
    )


def _patch_successful_graph(monkeypatch, tmp_path: Path, calls: list[str]) -> None:
    from migration_factory.orchestrator.artifact_validation import ArtifactValidationResult

    validation = ArtifactValidationResult(valid=True, artifact_refs={}, blockers=[], warnings=[])
    monkeypatch.setattr(graph_module, "validate_analysis_artifacts", lambda state: validation)
    monkeypatch.setattr(graph_module, "validate_planning_artifacts", lambda state: validation)
    monkeypatch.setattr(graph_module, "validate_assessment_artifacts", lambda state: validation)

    def approval(state):
        calls.append("approval")
        return {"approval_status": "COMPLETED", "approval_decision": "approved"}

    def finalize(state):
        calls.append("final_report")
        result = dict(state)
        final_dir = Path(result["run_dir"]) / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        (final_dir / "migration_report.json").write_text(json.dumps({"final_status": result["final_status"]}) + "\n", encoding="utf-8")
        (final_dir / "migration_summary.md").write_text("# Summary\n", encoding="utf-8")
        (final_dir / "report_context.json").write_text(json.dumps({"run_id": result["run_id"], "statuses": {"final": result["final_status"]}}) + "\n", encoding="utf-8")
        result["artifact_refs"] = {
            **dict(result.get("artifact_refs", {}) or {}),
            "final_migration_report": str(final_dir / "migration_report.json"),
            "final_migration_summary": str(final_dir / "migration_summary.md"),
            "copilot_report_context": str(final_dir / "report_context.json"),
        }
        return result

    monkeypatch.setattr(graph_module, "approval_node", approval)
    monkeypatch.setattr(graph_module, "finalize_orchestration_state", finalize)


def _approval_record(state):
    return {
        "approval_status": "COMPLETED",
        "approval_decision": "approved",
        "orchestration_status": "PASS",
    }


def _recording_approval_record(calls: list[str]):
    def run(state):
        calls.append("approval_record")
        return _approval_record(state)

    return run


def _sandbox_transform(state):
    return {
        "orchestration_status": "PASS",
        "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "TEST_PASSED",
        "test_totals": {"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
        "sandbox_path": "sandbox",
        "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
    }


def _recording_sandbox_transform(calls: list[str]):
    def run(state):
        calls.append("sandbox_transform")
        return _sandbox_transform(state)

    return run


class _RecordingFinalCopilotService:
    def __init__(self, state):
        self.state = state

    def generate_final_report(self, state):
        context_path = Path(state["run_dir"]) / "final" / "report_context.json"
        assert context_path.is_file()
        state["copilot_phase_statuses"] = {**dict(state.get("copilot_phase_statuses", {}) or {}), "final": "generated"}
        state["copilot_artifact_refs"] = {
            **dict(state.get("copilot_artifact_refs", {}) or {}),
            "copilot_migration_report": "final/copilot_migration_report.md",
        }
        state["final_status"] = "FAILED"
        state["orchestration_status"] = "FAIL"
        state["analysis_status"] = "FAIL"
        state["planning_status"] = "FAIL"
        state["assessment_status"] = "FAIL"
        state["blockers"] = ["copilot blocker"]
        state["warnings"] = ["copilot warning"]
        state["errors"] = ["copilot error"]
