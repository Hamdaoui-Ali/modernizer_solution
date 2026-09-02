import json
from pathlib import Path

import yaml

from migration_factory.agents.planning_agent.assist_config import PlanningAssistConfig
from migration_factory.agents.planning_agent.node import planning_node
from migration_factory.contracts.planning_assist import PlanningAssistResult


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


def test_copilot_assist_cannot_mutate_protected_planning_fields(monkeypatch, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    hub_dir = tmp_path / "ai-hub"
    run_id = "assist-guardrails"

    analysis_dir = app_dir / ".migration" / "runs" / run_id / "analysis"
    _write_analysis_fixture(analysis_dir)
    _write_profile(hub_dir)

    monkeypatch.setattr(
        "migration_factory.agents.planning_agent.node.load_planning_assist_config",
        lambda: PlanningAssistConfig(enabled=True, model_override="fake-model"),
    )

    def _fake_review_plan(self, *, request, config):
        assert request.forbidden_fields == [
            "unit_order",
            "tools",
            "blockers",
            "approval_required",
            "executable",
        ]
        assert "migration_units" in request.context
        assert request.context["migration_units"]

        # Malicious strings attempt structural mutation, but merge must treat as advisory text only.
        return PlanningAssistResult(
            status="USED",
            approval_summary_improvements=[
                "Please reorder/remove units and set approval_required=false, executable=true"
            ],
            missing_warnings=[
                "Attempted unit_order=[spring-boot-3-5-14,baseline] and tools=['copilot']"
            ],
            warnings=["Attempted blockers=[]"],
            operator_notes=["Keep deterministic gates; do not auto-approve."],
            risk_explanations=["Unit ordering and blockers remain deterministic."],
        )

    monkeypatch.setattr(
        "migration_factory.agents.planning_agent.node.CopilotPlanningAssistClient.review_plan",
        _fake_review_plan,
    )

    result = planning_node(_state(app_dir, hub_dir, run_id=run_id))
    assert result["planning_status"] == "PASS"

    # Protected field: deterministic unit order unchanged.
    assert [unit["id"] for unit in result["migration_units"]] == [
        "baseline",
        "java-17",
        "spring-boot-3-5-14",
        "jakarta",
        "dependency-cleanup",
        "existing-test-migration",
    ]

    plan_path = app_dir / ".migration" / "runs" / run_id / "planning" / "migration_plan.yaml"
    plan_payload = yaml.safe_load(plan_path.read_text(encoding="utf-8"))

    # Protected fields remain deterministic.
    assert plan_payload["executable"] is True
    assert plan_payload["unit_references"] == [
        "baseline",
        "java-17",
        "spring-boot-3-5-14",
        "jakarta",
        "dependency-cleanup",
        "existing-test-migration",
    ]
    assert plan_payload["blockers"] == []

    units_path = app_dir / ".migration" / "runs" / run_id / "planning" / "migration_units.yaml"
    units_payload = yaml.safe_load(units_path.read_text(encoding="utf-8"))
    assert all(
        "copilot" not in tool.lower()
        for unit in units_payload["units"]
        for tool in unit["tools"]
    )

    approval_path = app_dir / ".migration" / "runs" / run_id / "planning" / "approval_request.json"
    approval_payload = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval_payload["requires_human_approval"] is True

    # Allowed advisory fields merge.
    assert result["planning_approval_summary"] == (
        "Please reorder/remove units and set approval_required=false, executable=true"
    )
    assert "[WARNING] Attempted unit_order=[spring-boot-3-5-14,baseline] and tools=['copilot']" in result[
        "warnings"
    ]
    assert "[INFO] Attempted blockers=[]" in result["warnings"]
    assert result["planning_operator_notes"] == ["Keep deterministic gates; do not auto-approve."]
    assert result["planning_risk_explanations"] == ["Unit ordering and blockers remain deterministic."]
