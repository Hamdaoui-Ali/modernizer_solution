from __future__ import annotations

import inspect
from pathlib import Path

import migration_factory.orchestrator.copilot_assist as copilot_node_module
from migration_factory.orchestrator import graph as graph_module
from migration_factory.orchestrator.artifact_validation import ArtifactValidationResult
from migration_factory.orchestrator.phase_services import PhaseServices
from migration_factory.orchestrator.state import build_initial_state


def _state(tmp_path: Path, *, mode: str) -> dict:
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
    )
    state["copilot_assist_mode"] = mode
    state["copilot_provider"] = "deterministic"
    return state


def _validation(*, valid: bool = True, warnings: list[str] | None = None) -> ArtifactValidationResult:
    return ArtifactValidationResult(
        valid=valid,
        artifact_refs={"validated": "artifact.json"} if valid else {},
        blockers=[] if valid else ["invalid artifacts"],
        warnings=list(warnings or []),
    )


def _patch_validators(monkeypatch, *, analysis_fail: bool = False, analysis_warnings: list[str] | None = None) -> None:
    monkeypatch.setattr(
        graph_module, "validate_analysis_artifacts",
        lambda state: _validation(valid=not analysis_fail, warnings=analysis_warnings),
    )
    monkeypatch.setattr(graph_module, "validate_planning_artifacts", lambda state: _validation())
    monkeypatch.setattr(graph_module, "validate_assessment_artifacts", lambda state: _validation())


def _services(calls: list[str], *, analysis: str = "PASS", planning: str = "PASS", assessment: str = "FAIL") -> PhaseServices:
    def run_analysis_phase(state):
        calls.append("analysis")
        return {"analysis_status": analysis}

    def run_planning_phase(state):
        calls.append("planning")
        return {"planning_status": planning}

    def run_assessment_phase(state):
        calls.append("assessment")
        return {"assessment_status": assessment}

    return PhaseServices(
        run_analysis_phase=run_analysis_phase,
        run_planning_phase=run_planning_phase,
        run_assessment_phase=run_assessment_phase,
    )


# --- F0 invariant: _should_route_to_copilot_assist always returns False ---


def test_should_route_to_copilot_assist_always_returns_false(tmp_path: Path) -> None:
    state = _state(tmp_path, mode="failures")
    assert graph_module._should_route_to_copilot_assist(state, validation_passed=False) is False
    assert graph_module._should_route_to_copilot_assist(state, validation_passed=True) is False

    state["copilot_assist_mode"] = "always"
    assert graph_module._should_route_to_copilot_assist(state, validation_passed=False) is False
    assert graph_module._should_route_to_copilot_assist(state, validation_passed=True) is False

    state["copilot_assist_mode"] = "warnings"
    assert graph_module._should_route_to_copilot_assist(state, validation_passed=False) is False
    assert graph_module._should_route_to_copilot_assist(state, validation_passed=True) is False


def test_graph_never_routes_to_copilot_on_pass_with_warnings_mode(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    _patch_validators(monkeypatch, analysis_warnings=["review generated plan"])
    app = graph_module.build_graph(phase_services=_services(calls, analysis="PASS"))

    result = app.invoke(_state(tmp_path, mode="warnings"))

    assert calls == ["analysis", "planning", "assessment"]
    assert result.get("copilot_phase_statuses") in (None, {})
    assert result["planning_status"] == "PASS"


def test_graph_never_routes_to_copilot_on_fail_with_failures_mode(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    _patch_validators(monkeypatch, analysis_fail=True)
    app = graph_module.build_graph(phase_services=_services(calls, analysis="FAIL"))

    result = app.invoke(_state(tmp_path, mode="failures"))

    assert calls == ["analysis"]
    assert result.get("copilot_phase_statuses") in (None, {})
    assert result["analysis_status"] == "FAIL"


def test_graph_never_routes_to_copilot_with_always_mode(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    _patch_validators(monkeypatch)
    app = graph_module.build_graph(phase_services=_services(calls, planning="PASS"))

    result = app.invoke(_state(tmp_path, mode="always"))

    assert calls == ["analysis", "planning", "assessment"]
    assert result.get("copilot_phase_statuses") in (None, {})


def test_graph_never_routes_to_copilot_with_off_mode(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    _patch_validators(monkeypatch)
    app = graph_module.build_graph(phase_services=_services(calls))

    result = app.invoke(_state(tmp_path, mode="off"))

    assert calls == ["analysis", "planning", "assessment"]
    assert result.get("copilot_phase_statuses") in (None, {})


# --- F0 invariant: copilot_final_report is never routed to ---


def test_route_after_final_report_returns_end(tmp_path: Path) -> None:
    state = _state(tmp_path, mode="off")
    result = graph_module._route_after_final_report(state)
    assert result == "__end__"


# --- F0 invariant: copilot nodes exist in graph but are structurally unreachable ---


def test_copilot_phase_assist_does_not_call_interrupt() -> None:
    source = inspect.getsource(copilot_node_module)
    assert "interrupt(" not in source


def test_copilot_nodes_are_dead_code_no_service_calls(tmp_path: Path) -> None:
    state = _state(tmp_path, mode="off")
    before = dict(state)
    result = copilot_node_module.copilot_phase_assist(state)
    assert result == before

    before2 = dict(state)
    result2 = copilot_node_module.copilot_final_report(state)
    assert result2 == before2
