from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from migration_factory.final_report.context_builder import build_report_context, write_report_context


SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "migration_factory"
    / "contracts"
    / "schemas"
    / "copilot_report_context.schema.json"
)


def test_report_context_writes_complete_artifact_with_relative_refs_and_provenance(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)

    context_path = write_report_context(run_dir)

    assert context_path == run_dir / "final" / "report_context.json"
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    jsonschema.validate(payload, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    assert payload["run_id"] == "run-001"
    assert payload["profile"]["id"] == "springboot-2.7-to-3.5-java17"
    assert payload["statuses"]["transform"] == "TRANSFORM_APPLIED_IN_SANDBOX"
    assert payload["approval"]["decision"] == "approved"
    assert payload["migration_units"]["count"] == 2
    assert payload["openrewrite"]["status"] == "used"
    assert payload["openrewrite"]["unit_id"] == "java-17"
    assert payload["openrewrite"]["active_recipes"] == ["org.openrewrite.java.migrate.UpgradeToJava17"]
    assert payload["tests"]["totals"]["passed"] == 3
    assert payload["ai_trace"] == []
    assert payload["artifact_refs"]["final_migration_report"] == "final/migration_report.json"
    assert payload["artifact_refs"]["report_context"] == "final/report_context.json"
    assert all(not Path(ref).is_absolute() for ref in payload["artifact_refs"].values())
    assert "final/migration_report.json#/run_id" in payload["provenance"]["run_id"]
    assert "test/post_transform/test_report.json#/totals" in payload["provenance"]["tests.totals"]


def test_report_context_redacts_secrets_db_urls_auth_output_and_home_paths(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    report_path = run_dir / "final" / "migration_report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["github_token"] = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    payload["database_url"] = "postgres://user:secret@localhost:5432/app"
    payload["auth_output"] = "Logged in with bearer abcdefghijklmnop"
    payload["sandbox_path"] = str(Path.home() / "private" / "sandbox")
    payload["warnings"] = [
        "token ghp_abcdefghijklmnopqrstuvwxyz123456",
        "db postgres://user:secret@localhost:5432/app",
        "auth bearer abcdefghijklmnop",
    ]
    report_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    context = build_report_context(run_dir)
    content = json.dumps(context, sort_keys=True)

    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in content
    assert "user:secret@" not in content
    assert "bearer abcdefghijklmnop" not in content.lower()
    assert str(Path.home()) not in content
    assert "[REDACTED]" in content
    assert "%USERPROFILE%" in content


def test_report_context_ai_trace_uses_existing_records_and_redacts(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    report_path = run_dir / "final" / "migration_report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["ai_trace"] = [
        {
            "event_type": "build_failed",
            "agent_name": "v2-failure-diagnosis",
            "evidence_refs": [
                str(run_dir / "logs" / "phase2_transform.log"),
                "Authorization: Bearer abcdefghijklmnop",
            ],
            "context_pack_checksum": "cp-abc",
            "failure_type": "DEPENDENCY_ERROR",
            "repair_proposal_id": str(run_dir / "repairs" / "proposal-1.json"),
            "proposal_checksum": "prop-abc",
            "reviewer_decision": "accept",
            "approval_decision": "approved",
            "validation_status": "REPAIR_VALIDATED",
            "ledger_ref": str(run_dir / "repairs" / "repair_ledger.json"),
        }
    ]
    report_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    context = build_report_context(run_dir)

    jsonschema.validate(context, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    trace = context["ai_trace"]
    assert len(trace) == 1
    assert trace[0]["event"] == "build_failed"
    assert trace[0]["agent"] == "v2-failure-diagnosis"
    assert trace[0]["evidence_refs"][0] == "logs/phase2_transform.log"
    assert trace[0]["evidence_refs"][1] == "[REDACTED]"
    assert trace[0]["diagnosis"] == "DEPENDENCY_ERROR"
    assert trace[0]["proposal_ref"] == "repairs/proposal-1.json"
    assert trace[0]["reviewer_verdict"] == "accept"
    assert trace[0]["human_decision"] == "approved"
    assert trace[0]["ledger_ref"] == "repairs/repair_ledger.json"
    assert "final/migration_report.json#/ai_trace" in context["provenance"]["ai_trace"]
    serialized = json.dumps(context)
    assert "Bearer abcdef" not in serialized
    assert str(run_dir) not in serialized


def test_report_context_tolerates_missing_optional_artifacts_with_sentinels(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-001"
    (run_dir / "final").mkdir(parents=True)
    (run_dir / "final" / "migration_report.json").write_text(
        json.dumps({"run_id": "run-001", "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX"}) + "\n",
        encoding="utf-8",
    )

    context = build_report_context(run_dir)

    assert context["run_id"] == "run-001"
    assert context["profile"]["id"] == "not_captured"
    assert context["statuses"]["tests"] == "not_run"
    assert context["openrewrite"]["status"] == "not_run"
    assert context["migration_units"]["count"] == 0
    assert context["artifact_refs"]["report_context"] == "final/report_context.json"


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run-001"
    for rel in (
        "analysis",
        "planning",
        "assessment",
        "approval",
        "transformation",
        "workspaces/sandbox/.migration",
        "logs",
        "test/post_transform",
        "orchestration",
        "final",
    ):
        (run_dir / rel).mkdir(parents=True, exist_ok=True)

    (run_dir / "analysis" / "analysis_report.json").write_text(
        json.dumps({"migration_signals": {"security_config_touched": True}}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "analysis" / "rewrite_plugin_plan.json").write_text(
        json.dumps(
            {
                "plugin": "org.openrewrite.maven:rewrite-maven-plugin:6.39.0",
                "recipe_artifacts": ["org.openrewrite.recipe:rewrite-migrate-java:3.8.0"],
                "active_recipes": ["org.openrewrite.java.migrate.UpgradeToJava17"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "planning" / "migration_plan.yaml").write_text(
        """
profile: springboot-2.7-to-3.5-java17
risk: MEDIUM
requires_human_approval: true
target_stack:
  java: "17"
  spring_boot: "3.5.0"
profile_governance:
  strategy: direct_openrewrite_sandbox
  production_allowed: false
""".lstrip(),
        encoding="utf-8",
    )
    (run_dir / "planning" / "migration_units.yaml").write_text(
        "units:\n  - id: baseline\n  - id: java-17\n",
        encoding="utf-8",
    )
    (run_dir / "assessment" / "assessment_report.json").write_text(
        json.dumps(
            {
                "profile": "springboot-2.7-to-3.5-java17",
                "source_stack": {"java": "11", "spring_boot": "2.7.18"},
                "target_stack": {"java": "17", "spring_boot": "3.5.0"},
                "warnings": ["Spring Security configuration changed"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "approval" / "approval_decision.json").write_text(
        json.dumps(
            {
                "run_id": "run-001",
                "decision": "approved",
                "decided_by": "ada",
                "decided_at": "2026-05-24T00:00:00Z",
                "plan_lock_ref": "approval/approved_plan_lock.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "approval" / "approved_plan_lock.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "transformation" / "transformation_execution_plan.yaml").write_text(
        """
migration_units:
  - id: baseline
    goal: Baseline validation
    writes_source: false
    transformations:
      - type: custom_code_change
        status: recorded_not_executed
  - id: java-17
    goal: Upgrade Java
    writes_source: true
    transformations:
      - type: openrewrite
        active_recipes:
          - org.openrewrite.java.migrate.UpgradeToJava17
        apply_goal: run
""".lstrip(),
        encoding="utf-8",
    )
    (run_dir / "transformation" / "openrewrite-plugin.xml").write_text(
        "<plugin><groupId>org.openrewrite.maven</groupId><artifactId>rewrite-maven-plugin</artifactId><version>6.39.0</version></plugin>\n",
        encoding="utf-8",
    )
    (run_dir / "workspaces" / "sandbox" / ".migration" / "ledger.json").write_text(
        json.dumps({"completed_units": ["baseline", "java-17"], "next_unit_index": 2}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "test" / "post_transform" / "test_report.json").write_text(
        json.dumps(
            {
                "test_status": "TEST_PASSED",
                "totals": {"tests": 3, "passed": 3, "failures": 0, "errors": 0, "skipped": 0},
                "execution_owner": "build-agent",
                "execution_mode": "parse_existing_surefire",
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
                "approval_status": "COMPLETED",
                "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
                "build_status": "BUILD_PASSED_IN_SANDBOX",
                "test_status": "TEST_PASSED",
                "artifact_refs": {"final_migration_report": str(run_dir / "final" / "migration_report.json")},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "final" / "migration_report.json").write_text(
        json.dumps(
            {
                "run_id": "run-001",
                "source_stack": {"java": "11", "spring_boot": "2.7.18"},
                "target_stack": {"java": "17", "spring_boot": "3.5.0"},
                "risk_level": "MEDIUM",
                "approval": {"status": "COMPLETED", "decision": "approved", "approved_by": "ada"},
                "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
                "build_status": "BUILD_PASSED_IN_SANDBOX",
                "test_status": "TEST_PASSED",
                "test_totals": {"tests": 3, "passed": 3, "failures": 0, "errors": 0, "skipped": 0},
                "sandbox_path": str(run_dir / "workspaces" / "sandbox"),
                "limitations": [
                    "No production promotion performed.",
                    "No pull request creation performed.",
                    "No deployment performed.",
                    "No automatic merge performed.",
                ],
                "warnings": ["Spring Security configuration changed"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir
