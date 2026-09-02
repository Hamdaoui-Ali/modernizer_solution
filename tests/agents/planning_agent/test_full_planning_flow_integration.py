import hashlib
import json
from pathlib import Path

import jsonschema
import yaml

from migration_factory.agents.planning_agent.node import planning_node
from migration_factory.agents.planning_agent.runner import main as planning_runner_main
from migration_factory.contracts.planning_artifacts import PLANNING_OUTPUT_ARTIFACTS

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "migration_factory" / "contracts" / "schemas"


def _validate_schema(schema_name: str, payload: dict) -> None:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)


def _write_analysis_fixture(analysis_dir: Path) -> None:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "analysis_report.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "inventory": {
                    "build_tool": "maven",
                    "java_version": "11",
                    "spring_boot_version": "2.7",
                    "javax_count": 0,
                },
            }
        ),
        encoding="utf-8",
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
  spring_boot: 3.5.14
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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_files(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = _digest(path)
    return snapshot


def test_planning_node_full_flow_writes_required_artifacts_without_mutating_sources(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    hub_dir = tmp_path / "ai-hub"
    run_id = "full-flow"

    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_analysis_fixture(analysis_dir)
    _write_profile(hub_dir)

    # Sentinels ensure no incidental writes outside planning output directory.
    app_sentinel = app_dir / "README.md"
    app_sentinel.parent.mkdir(parents=True, exist_ok=True)
    app_sentinel.write_text("sentinel\n", encoding="utf-8")
    hub_sentinel = hub_dir / "sentinel.txt"
    hub_sentinel.write_text("sentinel\n", encoding="utf-8")

    app_before = _snapshot_files(app_dir)
    hub_before = _snapshot_files(hub_dir)

    result = planning_node(_state(app_dir, hub_dir, run_id=run_id))

    assert result["planning_status"] == "PASS"

    planning_dir = app_dir / ".migration" / "runs" / run_id / "planning"
    for artifact in PLANNING_OUTPUT_ARTIFACTS:
        assert (planning_dir / artifact).exists()

    validation_payload = json.loads((planning_dir / "plan_validation_report.json").read_text(encoding="utf-8"))
    assert validation_payload["status"] == "PASS"

    plan_payload = yaml.safe_load((planning_dir / "migration_plan.yaml").read_text(encoding="utf-8"))
    units_payload = yaml.safe_load((planning_dir / "migration_units.yaml").read_text(encoding="utf-8"))
    _validate_schema("migration_plan.schema.json", plan_payload)
    _validate_schema("migration_units.schema.json", units_payload)
    assert plan_payload["schema_version"] == "1.0.0"
    assert units_payload["schema_version"] == "1.0.0"

    assist_payload = json.loads((planning_dir / "copilot_assist.json").read_text(encoding="utf-8"))
    assert assist_payload["status"] == "SKIPPED"
    assert assist_payload["agent"] == "planning_agent"
    assert assist_payload["phase"] == "planning"
    assert assist_payload["artifact_refs"] == {"self": "copilot_assist.json"}
    assert assist_payload["can_modify_source"] is False
    assert assist_payload["can_modify_plan"] is False
    assert assist_payload["can_modify_blockers"] is False
    assert assist_payload["can_modify_executable"] is False
    assert assist_payload["can_modify_unit_order"] is False
    assert assist_payload["can_modify_approval_decision"] is False
    assert assist_payload["can_modify_tools"] is False

    app_after = _snapshot_files(app_dir)
    hub_after = _snapshot_files(hub_dir)

    for rel_path, digest in app_before.items():
        if not rel_path.startswith(f".migration/runs/{run_id}/planning/"):
            assert app_after.get(rel_path) == digest

    for rel_path, digest in hub_before.items():
        assert hub_after.get(rel_path) == digest


def test_planning_node_accepts_profile_id_alias(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    hub_dir = tmp_path / "ai-hub"
    run_id = "profile-id-alias"

    _write_analysis_fixture(app_dir / ".migration" / "runs" / run_id / "analysis")
    _write_profile(hub_dir)

    state = {
        "run_id": run_id,
        "profile_id": "java17",
        "modernized_app_path": str(app_dir),
        "ai_hub_path": str(hub_dir),
    }
    result = planning_node(state)

    assert result["planning_status"] == "PASS"
    planning_dir = app_dir / ".migration" / "runs" / run_id / "planning"
    plan_text = (planning_dir / "migration_plan.yaml").read_text(encoding="utf-8")
    assert 'profile: "java17"' in plan_text


def test_java21_runtime_validation_route_reaches_planning_when_openrewrite_is_blocked(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    hub_dir = Path(__file__).resolve().parents[3] / "modernizer-solution-ai-hub"
    run_id = "runtime-validation-route"

    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_analysis_fixture(analysis_dir)
    (analysis_dir / "analysis_report.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "source_stack": {
                    "build_tool": "maven",
                    "java": "17",
                    "spring_boot": "3.5.15",
                },
                "inventory": {
                    "build_tool": "maven",
                    "java_version": "17",
                    "spring_boot_version": "3.5.15",
                    "javax_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    (analysis_dir / "rewrite_impact_summary.json").write_text(
        json.dumps(
            {
                "overall_impact": "BLOCKED",
                "blocked_reasons": ["OpenRewrite dry-run unavailable"],
            }
        ),
        encoding="utf-8",
    )

    result = planning_node(
        {
            "run_id": run_id,
            "profile": "springboot-3.5-java17-to-java21",
            "modernized_app_path": str(app_dir),
            "ai_hub_path": str(hub_dir),
        }
    )

    assert result["planning_status"] == "PASS"
    planning_dir = app_dir / ".migration" / "runs" / run_id / "planning"
    plan_payload = yaml.safe_load((planning_dir / "migration_plan.yaml").read_text(encoding="utf-8"))
    units_payload = yaml.safe_load((planning_dir / "migration_units.yaml").read_text(encoding="utf-8"))

    assert plan_payload["executable"] is True
    assert [unit["id"] for unit in units_payload["units"]] == ["baseline", "java-21-runtime-validation"]
    assert any(
        "OpenRewrite impact is blocked for a Java 21 runtime-validation route" in warning
        for warning in result["warnings"]
    )


def test_planning_runner_writes_artifacts_under_modernized_run(tmp_path: Path, monkeypatch) -> None:
    legacy_dir = tmp_path / "legacy"
    app_dir = tmp_path / "app"
    hub_dir = tmp_path / "ai-hub"
    run_id = "runner-flow"

    legacy_dir.mkdir()
    _write_analysis_fixture(app_dir / ".migration" / "runs" / run_id / "analysis")
    _write_profile(hub_dir)

    monkeypatch.setattr(
        "sys.argv",
        [
            "planning-runner",
            "--run-id",
            run_id,
            "--modernized",
            str(app_dir),
            "--legacy",
            str(legacy_dir),
            "--ai-hub",
            str(hub_dir),
            "--profile",
            "java17",
        ],
    )

    assert planning_runner_main() == 0
    planning_dir = app_dir / ".migration" / "runs" / run_id / "planning"
    for artifact in PLANNING_OUTPUT_ARTIFACTS:
        assert (planning_dir / artifact).exists()
