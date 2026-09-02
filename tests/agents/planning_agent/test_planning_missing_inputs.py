import json
from pathlib import Path

from migration_factory.agents.planning_agent.node import planning_node
from migration_factory.contracts.planning_artifacts import PLANNING_OUTPUT_ARTIFACTS


def _write_analysis_fixture(analysis_dir: Path) -> None:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "analysis_report.json").write_text(
        json.dumps({"status": "PASS"}), encoding="utf-8"
    )
    (analysis_dir / "dependency_graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
    )
    (analysis_dir / "test_inventory.json").write_text(
        json.dumps({"tests": []}), encoding="utf-8"
    )
    (analysis_dir / "analysis_summary.md").write_text("analysis ok\n", encoding="utf-8")


def _write_profile(ai_hub_dir: Path, profile_id: str = "java17") -> None:
    profiles_dir = ai_hub_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / f"{profile_id}.yaml").write_text(
        """
source:
  java: 11
  spring_boot: 2.7
  build: maven
target:
  java: 17
  spring_boot: 3.2
  build: maven
rules: {}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _state(app_dir: Path, hub_dir: Path, run_id: str = "run-1", profile: str = "java17") -> dict:
    return {
        "run_id": run_id,
        "profile": profile,
        "modernized_app_path": str(app_dir),
        "ai_hub_path": str(hub_dir),
    }


def test_missing_required_analysis_artifact_blocks_planning(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    hub_dir = tmp_path / "ai-hub"
    run_id = "missing-analysis"

    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_analysis_fixture(analysis_dir)
    _write_profile(hub_dir)
    (analysis_dir / "analysis_summary.md").unlink()

    result = planning_node(_state(app_dir, hub_dir, run_id=run_id))

    assert result["planning_status"] == "FAIL"
    assert "Missing required analysis artifacts: analysis_summary.md" in result["errors"]
    assert result["planning_assist_error"] == "Planning skipped due to analysis artifact load failure."

    planning_dir = app_dir / ".migration" / "runs" / run_id / "planning"
    for artifact in PLANNING_OUTPUT_ARTIFACTS:
        assert not (planning_dir / artifact).exists()


def test_missing_profile_blocks_planning(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    hub_dir = tmp_path / "ai-hub"
    run_id = "missing-profile"

    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_analysis_fixture(analysis_dir)

    result = planning_node(_state(app_dir, hub_dir, run_id=run_id, profile="does-not-exist"))

    assert result["planning_status"] == "FAIL"
    assert any("Profile not found:" in msg for msg in result["errors"])
    assert result["planning_assist_error"] == "Planning skipped due to migration profile load failure."

    planning_dir = app_dir / ".migration" / "runs" / run_id / "planning"
    for artifact in PLANNING_OUTPUT_ARTIFACTS:
        assert not (planning_dir / artifact).exists()
