from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from migration_factory.copilot_assist.providers import CopilotCliProvider, DeterministicCopilotProvider, ProviderResult
from migration_factory.copilot_assist.providers.cli_provider import (
    FORBIDDEN_FLAGS,
    LARGE_PROMPT_BYTES,
    UnsafeCopilotCliConfig,
    _validate_cli_args,
)
from migration_factory.copilot_assist.service import generate_final_report, generate_phase_assist


PROTECTED_STATE = {
    "status": "PASS",
    "final_status": "TRANSFORM_APPLIED_IN_SANDBOX",
    "approval_status": "COMPLETED",
    "approval_decision": "approved",
    "blockers": ["manual follow-up"],
    "warnings": ["deterministic warning"],
    "errors": ["deterministic error"],
    "verdict": "ready_for_review",
    "artifact_refs": {"migration_report": "final/migration_report.json"},
}


def test_deterministic_provider_phase_assist_returns_data_only_and_keeps_state_unchanged() -> None:
    provider = DeterministicCopilotProvider()
    state = deepcopy(PROTECTED_STATE)
    before = deepcopy(state)

    result = provider.phase_assist_fallback(
        run_id="run-001",
        phase="build",
        agent="build_agent",
        context=state,
    )
    payload = result.to_dict()

    assert isinstance(result, ProviderResult)
    assert state == before
    assert payload["schema_version"] == "1.0.0"
    assert payload["run_id"] == "run-001"
    assert payload["phase"] == "build"
    assert payload["agent"] == "build_agent"
    assert payload["status"] == "fallback"
    assert payload["provider"] == "deterministic"
    assert payload["advisory_only"] is True
    assert payload["fallback_used"] is True
    assert payload["confidence"] == "medium"
    assert "official migration statuses" in payload["blocked_actions"][0]


def test_deterministic_provider_rejects_unsupported_phase_without_mutating_state() -> None:
    provider = DeterministicCopilotProvider()
    state = deepcopy(PROTECTED_STATE)
    before = deepcopy(state)

    with pytest.raises(ValueError, match="unsupported Copilot assist phase"):
        provider.phase_assist_fallback(
            run_id="run-001",
            phase="deployment",
            agent="deploy_agent",
            context=state,
        )

    assert state == before


def test_deterministic_provider_final_report_keeps_official_state_fields_unchanged() -> None:
    provider = DeterministicCopilotProvider()
    state = deepcopy(PROTECTED_STATE)
    before = deepcopy(state)

    result = provider.final_report_fallback(run_id="run-001", context=state)
    payload = result.to_dict()

    assert state == before
    assert payload["status"] == "generated_with_fallback"
    assert payload["provider"] == "deterministic"
    assert payload["advisory_only"] is True
    assert payload["fallback_used"] is True
    assert payload["validation"]["uses_provided_context_only"] is True


def test_cli_provider_rejects_forbidden_flags() -> None:
    with pytest.raises(UnsafeCopilotCliConfig):
        _validate_cli_args(["copilot", "-s", "--allow-all", "--model", "gpt-5-mini"])

    for flag in FORBIDDEN_FLAGS:
        with pytest.raises(UnsafeCopilotCliConfig):
            _validate_cli_args(["copilot", flag])


def test_cli_provider_uses_neutral_cwd_and_allowed_args_only(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        calls.append({"args": list(args), "cwd": kwargs["cwd"], "input": kwargs.get("input")})
        return CompletedProcess(args=args, returncode=0, stdout="advisory output\n", stderr="")

    monkeypatch.setattr("migration_factory.copilot_assist.providers.cli_provider.subprocess.run", fake_run)
    provider = CopilotCliProvider(model="gpt-5-mini", executable_path=r"C:\tools\copilot.cmd")

    result = provider.final_report(run_id="run-001", run_dir=tmp_path / "run-001", context={"status": "PASS"})

    assert result.provider == "cli"
    assert result.fallback_used is False
    assert calls[0]["cwd"] == str(tmp_path / "run-001" / "logs" / "copilot")
    args = calls[0]["args"]
    assert args[:6] == [r"C:\tools\copilot.cmd", "-s", "--no-ask-user", "--model", "gpt-5-mini", "--no-color"]
    assert "--allow-all" not in args
    assert "--yolo" not in args
    log_text = (tmp_path / "run-001" / "logs" / "copilot" / "copilot_cli_invocation.json").read_text(encoding="utf-8")
    assert r"C:\tools\copilot.cmd" not in log_text
    assert "copilot.cmd" in log_text


def test_cli_provider_large_prompt_uses_stdin_and_does_not_log_prompt(tmp_path: Path, monkeypatch) -> None:
    secret_prompt = "PROMPT_SECRET_" + ("x" * LARGE_PROMPT_BYTES)
    calls: list[dict[str, object]] = []

    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        calls.append({"args": list(args), "input": kwargs.get("input")})
        return CompletedProcess(args=args, returncode=0, stdout="large advisory output\n", stderr="")

    monkeypatch.setattr("migration_factory.copilot_assist.providers.cli_provider.subprocess.run", fake_run)
    provider = CopilotCliProvider(executable_path="/tools/copilot")

    provider.final_report(run_id="run-001", run_dir=tmp_path / "run-001", prompt=secret_prompt)

    assert calls[0]["input"] == secret_prompt
    assert "--prompt" not in calls[0]["args"]
    invocation = json.loads((tmp_path / "run-001" / "logs" / "copilot" / "copilot_cli_invocation.json").read_text(encoding="utf-8"))
    assert invocation["input_mode"] == "stdin"
    assert invocation["prompt_bytes"] == len(secret_prompt.encode("utf-8"))
    assert secret_prompt not in json.dumps(invocation)


def test_cli_provider_falls_back_on_cli_failure(tmp_path: Path, monkeypatch) -> None:
    def fake_run(args, **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(args=args, returncode=2, stdout="", stderr="token=ghp_abcdefghijklmnopqrstuvwxyz123456")

    monkeypatch.setattr("migration_factory.copilot_assist.providers.cli_provider.subprocess.run", fake_run)
    provider = CopilotCliProvider(executable_path="/tools/copilot")

    result = provider.final_report(run_id="run-001", run_dir=tmp_path / "run-001", context={"statuses": {"final": "PASS"}})
    payload = result.to_dict()

    assert payload["provider"] == "deterministic"
    assert payload["fallback_used"] is True
    assert payload["status"] == "generated_with_fallback"
    log_text = (tmp_path / "run-001" / "logs" / "copilot" / "copilot_cli_invocation.json").read_text(encoding="utf-8")
    assert "ghp_" not in log_text


def test_service_phase_assist_writes_only_copilot_artifacts_and_state(tmp_path: Path) -> None:
    state = _service_state(tmp_path, provider="deterministic")
    before = deepcopy({key: value for key, value in state.items() if not key.startswith("copilot_")})

    result = generate_phase_assist(state, "build")

    assert isinstance(result, ProviderResult)
    assert (tmp_path / "run-001" / "build" / "copilot_assist.json").is_file()
    assert (tmp_path / "run-001" / "build" / "copilot_assist.md").is_file()
    assert not (tmp_path / "run-001" / "final" / "migration_report.json").exists()
    assert not (tmp_path / "run-001" / "final" / "migration_summary.md").exists()
    after = {key: value for key, value in state.items() if not key.startswith("copilot_")}
    assert after == before
    assert state["copilot_phase_statuses"]["build"] == "fallback"
    assert "build_copilot_assist" in state["copilot_artifact_refs"]


def test_service_final_report_consumes_report_context_and_writes_only_copilot_artifacts(tmp_path: Path) -> None:
    state = _service_state(tmp_path, provider="deterministic")
    final_dir = tmp_path / "run-001" / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "report_context.json").write_text(
        json.dumps({"run_id": "run-001", "statuses": {"final": "PASS"}, "warnings": ["review only"]}) + "\n",
        encoding="utf-8",
    )
    before = deepcopy({key: value for key, value in state.items() if not key.startswith("copilot_")})

    generate_final_report(state)

    assert (final_dir / "copilot_report_request.json").is_file()
    assert (final_dir / "copilot_report_response.json").is_file()
    assert (final_dir / "copilot_migration_report.md").is_file()
    assert not (final_dir / "migration_report.json").exists()
    assert not (final_dir / "migration_summary.md").exists()
    after = {key: value for key, value in state.items() if not key.startswith("copilot_")}
    assert after == before
    assert state["copilot_fallback_used"] is True
    assert state["copilot_artifact_refs"]["copilot_report_request"] == "final/copilot_report_request.json"


def test_service_missing_report_context_fails_safely_without_official_artifacts(tmp_path: Path) -> None:
    state = _service_state(tmp_path, provider="deterministic")

    generate_final_report(state)

    final_dir = tmp_path / "run-001" / "final"
    response = json.loads((final_dir / "copilot_report_response.json").read_text(encoding="utf-8"))
    assert response["status"] == "failed"
    assert response["fallback_used"] is True
    assert "missing required final/report_context.json" in state["copilot_errors"]
    assert not (final_dir / "migration_report.json").exists()
    assert not (final_dir / "migration_summary.md").exists()


def _service_state(tmp_path: Path, *, provider: str) -> dict[str, object]:
    return {
        **deepcopy(PROTECTED_STATE),
        "run_id": "run-001",
        "run_dir": str(tmp_path / "run-001"),
        "copilot_provider": provider,
        "copilot_model": "gpt-5-mini",
        "copilot_timeout_seconds": 30,
        "copilot_phase_statuses": {},
        "copilot_artifact_refs": {},
        "copilot_warnings": [],
        "copilot_errors": [],
        "copilot_fallback_used": False,
    }
