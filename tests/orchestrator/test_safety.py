from __future__ import annotations

import hashlib
from pathlib import Path

from migration_factory.orchestrator import graph as graph_module
from migration_factory.orchestrator import runner


def _source_snapshot(*roots: Path) -> dict[Path, str]:
    snapshot: dict[Path, str] = {}
    for root in roots:
        for path in sorted((root / "src").rglob("*")):
            if path.is_file():
                snapshot[path] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def test_orchestrator_does_not_modify_source_files(
    monkeypatch,
    initial_state,
    legacy_app_path,
    modernized_app_path,
    fake_successful_phase_services,
    valid_artifact_result,
    fresh_checkpointer,
) -> None:
    before = _source_snapshot(legacy_app_path, modernized_app_path)
    monkeypatch.setattr(graph_module, "validate_analysis_artifacts", lambda state: valid_artifact_result)
    monkeypatch.setattr(graph_module, "validate_planning_artifacts", lambda state: valid_artifact_result)
    monkeypatch.setattr(graph_module, "validate_assessment_artifacts", lambda state: valid_artifact_result)

    graph = graph_module.build_graph(
        checkpointer=fresh_checkpointer,
        phase_services=fake_successful_phase_services,
    )
    graph.invoke(
        initial_state,
        config={"configurable": {"thread_id": initial_state["thread_id"]}},
    )

    class FakeGraph:
        def invoke(self, state, *, config):
            return {**state, "approval_status": "COMPLETED"}

    monkeypatch.setattr(runner, "build_graph", lambda checkpointer: FakeGraph())
    monkeypatch.setattr(runner, "write_orchestration_summary", lambda state: None)

    exit_code = runner.main(
        [
            "--run-id",
            f"{initial_state['run_id']}-runner",
            "--legacy",
            str(legacy_app_path),
            "--modernized",
            str(modernized_app_path),
            "--ai-hub",
            initial_state["ai_hub_path"],
            "--profile",
            initial_state["profile_id"],
        ]
    )

    assert exit_code == 0
    assert _source_snapshot(legacy_app_path, modernized_app_path) == before
