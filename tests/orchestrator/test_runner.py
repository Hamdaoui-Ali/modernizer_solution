from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path

import pytest

from migration_factory.orchestrator import graph as graph_module
from migration_factory.orchestrator import runner
from migration_factory.orchestrator.artifact_validation import ArtifactValidationResult
from migration_factory.orchestrator.phase_services import PhaseServices
from migration_factory.orchestrator.state import FULL_SANDBOX_MIGRATION_MODE, READ_ONLY_ASSESSMENT_MODE


def _argv(tmp_path: Path, *, mode: str | None = None) -> list[str]:
    argv = [
        "--run-id",
        "run-001",
        "--legacy",
        str(tmp_path / "legacy"),
        "--modernized",
        str(tmp_path / "modernized"),
        "--ai-hub",
        str(tmp_path / "ai-hub"),
        "--profile",
        "java17",
    ]
    if mode is not None:
        argv.extend(["--mode", mode])
    return argv


def _valid_inputs(tmp_path: Path) -> None:
    (tmp_path / "legacy").mkdir()
    profiles_dir = tmp_path / "ai-hub" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "java17.yaml").write_text("id: java17\n", encoding="utf-8")


def test_parse_args_maps_cli_fields(tmp_path: Path) -> None:
    args = runner.parse_args(_argv(tmp_path))

    assert args.run_id == "run-001"
    assert args.legacy == str(tmp_path / "legacy")
    assert args.modernized == str(tmp_path / "modernized")
    assert args.ai_hub == str(tmp_path / "ai-hub")
    assert args.profile == "java17"
    assert args.mode == READ_ONLY_ASSESSMENT_MODE


def test_parse_args_accepts_full_sandbox_migration_mode(tmp_path: Path) -> None:
    args = runner.parse_args(_argv(tmp_path, mode=FULL_SANDBOX_MIGRATION_MODE))

    assert args.mode == FULL_SANDBOX_MIGRATION_MODE


def test_main_returns_nonzero_for_invalid_mode(tmp_path: Path) -> None:
    assert runner.main(_argv(tmp_path, mode="transform")) != 0


def test_invalid_mode_fails_preflight(monkeypatch, tmp_path: Path, capsys) -> None:
    _valid_inputs(tmp_path)
    args = Namespace(
        run_id="run-001",
        legacy=str(tmp_path / "legacy"),
        modernized=str(tmp_path / "modernized"),
        ai_hub=str(tmp_path / "ai-hub"),
        profile="java17",
        mode="transform",
    )

    monkeypatch.setattr(runner, "parse_args", lambda argv=None: args)
    monkeypatch.setattr(
        runner,
        "build_graph",
        lambda checkpointer: (_ for _ in ()).throw(AssertionError("graph should not run")),
    )

    assert runner.main([]) == 2
    assert "mode must be read_only_assessment: transform" in capsys.readouterr().err


def test_main_builds_thread_id_from_run_id(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeGraph:
        def invoke(self, state, *, config):
            captured["state"] = state
            captured["config"] = config
            return {**state, "done": True}

    monkeypatch.setattr(runner, "validate_preflight", lambda state, config: None)
    monkeypatch.setattr(runner, "default_checkpointer", lambda: object())
    monkeypatch.setattr(runner, "build_graph", lambda checkpointer: FakeGraph())
    monkeypatch.setattr(runner, "write_orchestration_summary", lambda state: None)

    assert runner.main(_argv(tmp_path)) == 0

    assert captured["state"]["thread_id"] == "run-001"
    assert captured["config"] == {"configurable": {"thread_id": "run-001"}}


def test_main_invokes_graph(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: list[str] = []

    class FakeGraph:
        def invoke(self, state, *, config):
            calls.append("invoke")
            return {**state, "finished": True}

    monkeypatch.setattr(runner, "validate_preflight", lambda state, config: None)
    monkeypatch.setattr(runner, "default_checkpointer", lambda: "checkpointer")
    monkeypatch.setattr(runner, "build_graph", lambda checkpointer: FakeGraph())
    monkeypatch.setattr(runner, "write_orchestration_summary", lambda state: None)

    assert runner.main(_argv(tmp_path)) == 0

    assert calls == ["invoke"]
    out = capsys.readouterr().out.strip()
    if out.startswith("CONTROL_TOWER_FINAL_JSON "):
        out = out[len("CONTROL_TOWER_FINAL_JSON "):]
    assert json.loads(out)["finished"] is True


def test_main_writes_summary_after_completed_graph(monkeypatch, tmp_path: Path) -> None:
    written: list[str] = []

    class FakeGraph:
        def invoke(self, state, *, config):
            return {**state, "approval_status": "COMPLETED"}

    monkeypatch.setattr(runner, "validate_preflight", lambda state, config: None)
    monkeypatch.setattr(runner, "default_checkpointer", lambda: "checkpointer")
    monkeypatch.setattr(runner, "build_graph", lambda checkpointer: FakeGraph())
    monkeypatch.setattr(
        runner,
        "write_orchestration_summary",
        lambda state: written.append(state["run_id"]),
    )

    assert runner.main(_argv(tmp_path)) == 0

    assert written == ["run-001"]


def test_pass_flow_reaches_approval_interrupt(monkeypatch, tmp_path: Path, capsys) -> None:
    _valid_inputs(tmp_path)

    def valid_artifacts(state):
        return ArtifactValidationResult(
            valid=True,
            artifact_refs={"validated": "artifact.json"},
            blockers=[],
            warnings=[],
        )

    services = PhaseServices(
        run_analysis_phase=lambda state: {"analysis_status": "PASS"},
        run_planning_phase=lambda state: {"planning_status": "PASS"},
        run_assessment_phase=lambda state: {"assessment_status": "PASS"},
    )

    monkeypatch.setattr(graph_module, "validate_analysis_artifacts", valid_artifacts)
    monkeypatch.setattr(graph_module, "validate_planning_artifacts", valid_artifacts)
    monkeypatch.setattr(graph_module, "validate_assessment_artifacts", valid_artifacts)
    monkeypatch.setattr(
        runner,
        "build_graph",
        lambda checkpointer: graph_module.build_graph(
            checkpointer=checkpointer,
            phase_services=services,
        ),
    )
    monkeypatch.setattr(
        runner,
        "write_orchestration_summary",
        lambda state: (_ for _ in ()).throw(AssertionError("summary wrote before interrupt")),
    )

    assert runner.main(_argv(tmp_path)) == 0

    out = capsys.readouterr().out.strip()
    if out.startswith("CONTROL_TOWER_FINAL_JSON "):
        out = out[len("CONTROL_TOWER_FINAL_JSON "):]
    payload = json.loads(out)
    assert payload["status"] == "human_approval_required"
    assert payload["approval_status"] == "INTERRUPTED"
    assert payload["run_id"] == "run-001"
    assert payload["decision_options"] == ["approved", "rejected", "replan_required"]


def test_parse_args_raises_for_invalid_mode(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        runner.parse_args(_argv(tmp_path, mode="transform"))
