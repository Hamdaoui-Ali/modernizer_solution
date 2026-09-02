import json
import importlib.util
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

from migration_factory.approval import (
    check_approval_decision,
    check_approved_plan_lock,
    read_approval_decision,
    write_approval_decision,
    write_approved_plan_lock,
)
from migration_factory.contracts import (
    APPROVAL_DECISION_VALUES,
    COPILOT_STATUS_VALUES,
    RISK_VALUES,
    SCHEMA_VERSION,
    STATUS_VALUES,
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "migration_factory" / "contracts" / "schemas"
REPORT_ASSEMBLER_PATH = (
    Path(__file__).resolve().parents[2]
    / "migration_factory"
    / "agents"
    / "analysis_agent"
    / "analysis_agent"
    / "report_assembler.py"
)


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _validate(schema_name: str, payload: dict) -> None:
    jsonschema.validate(payload, _load_schema(schema_name))


def _base(status: str = "PASS") -> dict:
    return {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "status": status,
        "artifact_refs": {"self": "artifact.json"},
    }


class _Context:
    run_id = "run-1"

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def get_output_path(self, name: str) -> str:
        return str(self.output_dir / name)


VALID_PAYLOADS = {
    "analysis_report.schema.json": {**_base(), "risk": "LOW"},
    "rewrite_impact_summary.schema.json": {
        **_base(),
        "agent": "analysis_agent",
        "phase": "analysis",
        "overall_impact": "MEDIUM",
        "changed_files": ["src/main/java/A.java"],
        "high_risk_files": ["src/main/java/A.java"],
        "migration_signals": {
            "api_or_boot_upgrade": True,
            "javax_removed": True,
            "boot_2_to_3_gap": True,
            "boot_2_to_4_gap": False,
            "boot4_target": False,
            "java_11_to_17_gap": True,
            "java_8_to_21_gap": False,
            "java_21_target": False,
            "javax_present": True,
            "security_config_touched": False,
            "datasource_config_touched": False,
        },
        "blocked_reasons": [],
        "source_modified": False,
    },
    "read_only_verification.schema.json": {
        **_base(),
        "agent": "analysis_agent",
        "phase": "analysis",
        "paths": {
            "legacy_root": "/workspace/legacy",
            "modernized_root": "/workspace/modernized",
            "artifact": ".migration/runs/run-1/analysis/read_only_verification.json",
        },
        "allowed_write_roots": [".migration/runs/run-1/analysis"],
        "checks": {
            "legacy_tree_unchanged": True,
            "modernized_source_unchanged": True,
            "ignored_generated_paths": ["target/"],
        },
        "violations": [],
        "source_modified": False,
    },
    "rewrite_plugin_plan.schema.json": {
        **_base("USED"),
        "profile_id": "springboot-2.7-to-3.5-java17",
        "catalog_path": "catalogs/openrewrite/springboot-3.5-java17.yaml",
        "catalog_id": "springboot-3.5-java17",
        "plugin": "org.openrewrite.maven:rewrite-maven-plugin:6.39.0",
        "recipe_artifacts": ["org.openrewrite.recipe:rewrite-spring:6.30.4"],
        "active_recipes": ["org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"],
        "preview_goals": ["rewrite:dryRun"],
        "selected_preview_goal": "rewrite:dryRun",
        "apply_goals_forbidden": True,
    },
    "migration_plan.schema.json": {**_base(), "risk": "HIGH"},
    "migration_units.schema.json": {**_base(), "units": [{"id": "baseline"}]},
    "approval_request.schema.json": {
        **_base(),
        "agent": "planning_agent",
        "phase": "approval",
        "profile": "java17",
        "requires_human_approval": True,
        "decision_options": ["approved", "rejected", "replan_required"],
        "recommended_decision": None,
        "units_to_execute": ["baseline"],
        "blockers": [],
        "warnings": [],
    },
    "approval_decision.schema.json": {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "agent": "human",
        "phase": "approval",
        "decision": "approved",
        "decided_by": "reviewer",
        "decided_at": "2026-05-19T00:00:00Z",
        "comments": "",
        "plan_lock_ref": "approved_plan_lock.json",
        "artifact_refs": {
            "self": "approval_decision.json",
            "approved_plan_lock": "approved_plan_lock.json",
        },
    },
    "approved_plan_lock.schema.json": {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "agent": "approval",
        "phase": "approval",
        "hash_algorithm": "sha256",
        "locked_artifacts": [
            {
                "path": "planning/migration_plan.yaml",
                "sha256": "0" * 64,
            }
        ],
        "artifact_refs": {"self": "approved_plan_lock.json"},
    },
    "copilot_assist.schema.json": {
        **_base("USED"),
        "agent": "planning_agent",
        "phase": "planning",
        "provider": "github_copilot",
        "model": "gpt-test",
        "inputs_summary": {"units_count": 1},
        "advisory_summary": {"warning_count": 0},
        "warnings": [],
        "error": None,
        "can_modify_source": False,
        "can_modify_plan": False,
        "can_modify_blockers": False,
        "can_modify_executable": False,
        "can_modify_unit_order": False,
        "can_modify_approval_decision": False,
        "can_modify_tools": False,
    },
    "assessment_report.schema.json": {
        **_base(),
        "agent": "assessment",
        "phase": "assessment",
        "profile": "java17",
        "overall_risk": "UNKNOWN",
        "source_stack": {"build_tool": "maven", "java": "11", "spring_boot": "2.7"},
        "target_stack": {"build_tool": "maven", "java": "17", "spring_boot": "3.5.14"},
        "analysis": {"status": "PASS", "artifact_ref": "../analysis/analysis_report.json"},
        "planning": {
            "status": "PASS",
            "validation_status": "PASS",
            "executable": True,
            "artifact_ref": "../planning/migration_plan.yaml",
        },
        "openrewrite_dry_run": {
            "status": "PASS",
            "overall_impact": "LOW",
            "counts": {"changed_files": 0},
            "artifact_ref": "../analysis/rewrite_impact_summary.json",
        },
        "migration_units": {
            "count": 1,
            "units": [{"id": "baseline"}],
            "artifact_ref": "../planning/migration_units.yaml",
        },
        "blockers": [],
        "warnings": [],
        "copilot": {"status": "UNAVAILABLE", "artifact_ref": "../planning/copilot_assist.json"},
        "approval_readiness": {
            "status": "READY_FOR_REVIEW",
            "requires_human_approval": True,
            "recommended_decision": None,
            "artifact_ref": "../planning/approval_request.json",
        },
        "read_only_verification": {
            "status": "PASS",
            "source_modified": False,
            "artifact_ref": "../analysis/read_only_verification.json",
        },
        "next_recommended_phase": "human_approval",
        "execution_claims": {
            "transformation_executed": False,
            "openrewrite_apply_executed": False,
            "migrated_build_executed": False,
            "migrated_tests_executed": False,
            "final_migration_executed": False,
        },
    },
    "copilot_report_context.schema.json": {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "profile": {
            "id": "springboot-2.7-to-3.5-java17",
            "risk_level": "MEDIUM",
            "source_stack": {"java": "11"},
            "target_stack": {"java": "17"},
        },
        "statuses": {
            "final": "TRANSFORM_APPLIED_IN_SANDBOX",
            "orchestration": "PASS",
            "analysis": "PASS",
            "planning": "PASS",
            "assessment": "PASS",
            "approval": "COMPLETED",
            "transform": "TRANSFORM_APPLIED_IN_SANDBOX",
            "build": "BUILD_PASSED_IN_SANDBOX",
            "tests": "TEST_PASSED",
        },
        "approval": {
            "status": "COMPLETED",
            "decision": "approved",
            "decided_by": "reviewer",
            "decided_at": "2026-05-19T00:00:00Z",
            "plan_lock_ref": "approval/approved_plan_lock.json",
            "lock_status": "LOCKED",
        },
        "migration_units": {"count": 1, "units": [{"id": "baseline"}], "source": "transformation_execution_plan"},
        "openrewrite": {
            "status": "used",
            "unit_id": "java-17",
            "plugin": "org.openrewrite.maven:rewrite-maven-plugin:6.39.0",
            "recipe_artifacts": [],
            "active_recipes": ["org.openrewrite.java.migrate.UpgradeToJava17"],
            "apply_goal": "run",
            "apply_maven_args": [],
        },
        "transformation": {
            "status": "TRANSFORM_APPLIED_IN_SANDBOX",
            "sandbox_path": "workspaces/sandbox",
            "plan_target": "workspaces/sandbox",
            "current_unit": "not_captured",
            "blocked_unit": "not_applicable",
            "next_unit_index": 1,
        },
        "build": {
            "status": "BUILD_PASSED_IN_SANDBOX",
            "command": ["mvn", "clean", "test"],
            "validations": "not_captured",
            "log_ref": "logs/phase2_transform.log",
        },
        "tests": {
            "status": "TEST_PASSED",
            "totals": {"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
            "execution_owner": "build-agent",
            "execution_mode": "parse_existing_surefire",
            "report_paths": [],
        },
        "dependency_policy": {
            "target_dependency_plan_ref": "planning/target_dependency_plan.json",
            "report_ref": "assessment/dependency_policy_report.json",
            "status": "PASS_WITH_WARNINGS",
            "risks_count": 1,
            "blockers_count": 0,
            "copilot_advisory_status": "FALLBACK",
            "policy_patch_applied": False,
            "unresolved_v2_dependency_risks": [
                {"rule_id": "DEP-TOMCAT-BOOT3-001", "severity": "WARNING"}
            ],
        },
        "security": {"status": "not_captured", "security_config_touched": False, "warnings": []},
        "scope_limits": ["No production promotion performed."],
        "artifact_refs": {
            "report_context": "final/report_context.json",
            "final_migration_report": "final/migration_report.json",
        },
        "provenance": {
            "run_id": ["final/migration_report.json#/run_id"],
            "tests.totals": ["test/post_transform/test_report.json#/totals"],
        },
        "warnings": [],
        "blockers": [],
        "errors": [],
    },
    "copilot_report_request.schema.json": {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "provider": "deterministic",
        "model": "local-template",
        "template_id": "final-report-default",
        "context_ref": "final/report_context.json",
        "advisory_only": True,
        "guardrails": {
            "no_state_mutation": True,
            "use_provided_context_only": True,
        },
    },
    "copilot_report_response.schema.json": {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "provider": "deterministic",
        "model": "local-template",
        "status": "generated",
        "fallback_used": False,
        "output_ref": "final/copilot_migration_report.md",
        "validation": {"valid": True},
        "warnings": [],
    },
}


@pytest.mark.parametrize("schema_name,payload", VALID_PAYLOADS.items())
def test_contract_schemas_accept_valid_payloads(schema_name: str, payload: dict) -> None:
    _validate(schema_name, payload)


def test_generated_analysis_report_validates_against_shared_schema(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("report_assembler", REPORT_ASSEMBLER_PATH)
    assert spec is not None
    assert spec.loader is not None
    report_assembler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report_assembler)

    report = report_assembler.assemble_report(
        _Context(tmp_path),
        {
            "source_stack": {"build_tool": "maven", "java": "11", "spring_boot": "2.7"},
            "target_stack": {"build_tool": "maven", "java": "17", "spring_boot": "3.5.14"},
            "project_structure": {"modules": ["app"]},
        },
        {
            "javax_imports": 1,
            "jakarta_imports": 0,
            "spring_imports": 2,
            "files_with_javax": ["src/main/java/App.java"],
        },
    )

    _validate("analysis_report.schema.json", report)


def test_schema_enums_match_contract_constants() -> None:
    for schema_name in VALID_PAYLOADS:
        schema = _load_schema(schema_name)
        assert schema["properties"]["schema_version"]["enum"] == [SCHEMA_VERSION]

    assert _load_schema("analysis_report.schema.json")["properties"]["status"]["enum"] == list(
        STATUS_VALUES
    )
    assert _load_schema("migration_plan.schema.json")["properties"]["risk"]["enum"] == list(
        RISK_VALUES
    )
    assert _load_schema("assessment_report.schema.json")["properties"]["overall_risk"][
        "enum"
    ] == list(RISK_VALUES)
    assert _load_schema("approval_request.schema.json")["properties"]["decision_options"][
        "enum"
    ] == [list(APPROVAL_DECISION_VALUES)]
    assert _load_schema("approval_decision.schema.json")["properties"]["decision"][
        "enum"
    ] == list(APPROVAL_DECISION_VALUES)
    legacy_assist = _load_schema("copilot_assist.schema.json")["definitions"]["legacy_assist"]
    assert legacy_assist["properties"]["status"]["enum"] == list(COPILOT_STATUS_VALUES)


@pytest.mark.parametrize("field", ["schema_version", "run_id"])
def test_analysis_report_rejects_missing_required_identity_fields(field: str) -> None:
    payload = deepcopy(VALID_PAYLOADS["analysis_report.schema.json"])
    payload.pop(field)

    with pytest.raises(jsonschema.ValidationError):
        _validate("analysis_report.schema.json", payload)


def test_analysis_report_rejects_unsupported_status() -> None:
    payload = deepcopy(VALID_PAYLOADS["analysis_report.schema.json"])
    payload["status"] = "COMPLETED"

    with pytest.raises(jsonschema.ValidationError):
        _validate("analysis_report.schema.json", payload)


def test_approval_request_rejects_approve_with_changes() -> None:
    payload = deepcopy(VALID_PAYLOADS["approval_request.schema.json"])
    payload["decision_options"] = ["approved", "approve_with_changes", "replan_required"]

    with pytest.raises(jsonschema.ValidationError):
        _validate("approval_request.schema.json", payload)


def test_approval_request_rejects_supported_options_in_wrong_order() -> None:
    payload = deepcopy(VALID_PAYLOADS["approval_request.schema.json"])
    payload["decision_options"] = ["rejected", "approved", "replan_required"]

    with pytest.raises(jsonschema.ValidationError):
        _validate("approval_request.schema.json", payload)


def test_approval_request_rejects_unsupported_decision() -> None:
    payload = deepcopy(VALID_PAYLOADS["approval_request.schema.json"])
    payload["decision"] = "approve_with_changes"

    with pytest.raises(jsonschema.ValidationError):
        _validate("approval_request.schema.json", payload)


def test_rewrite_impact_summary_rejects_impact_without_overall_impact() -> None:
    payload = deepcopy(VALID_PAYLOADS["rewrite_impact_summary.schema.json"])
    payload.pop("overall_impact")
    payload["impact"] = "LOW"

    with pytest.raises(jsonschema.ValidationError):
        _validate("rewrite_impact_summary.schema.json", payload)


def test_rewrite_impact_summary_accepts_boot4_java21_signals() -> None:
    payload = deepcopy(VALID_PAYLOADS["rewrite_impact_summary.schema.json"])
    payload["migration_signals"].update(
        {
            "boot_2_to_4_gap": True,
            "boot4_target": True,
            "java_8_to_21_gap": True,
            "java_21_target": True,
        }
    )

    _validate("rewrite_impact_summary.schema.json", payload)


def test_rewrite_impact_summary_rejects_unknown_migration_signal() -> None:
    payload = deepcopy(VALID_PAYLOADS["rewrite_impact_summary.schema.json"])
    payload["migration_signals"]["unrelated_future_signal"] = True

    with pytest.raises(jsonschema.ValidationError):
        _validate("rewrite_impact_summary.schema.json", payload)


def test_assessment_report_rejects_execution_claims() -> None:
    payload = deepcopy(VALID_PAYLOADS["assessment_report.schema.json"])
    payload["execution_claims"]["openrewrite_apply_executed"] = True

    with pytest.raises(jsonschema.ValidationError):
        _validate("assessment_report.schema.json", payload)


def test_copilot_legacy_assist_rejects_mutable_guardrail_flag() -> None:
    payload = deepcopy(VALID_PAYLOADS["copilot_assist.schema.json"])
    payload["can_modify_tools"] = True

    with pytest.raises(jsonschema.ValidationError):
        _validate("copilot_assist.schema.json", payload)


def _phase_assist_payload(phase: str) -> dict:
    return {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "phase": phase,
        "agent": f"{phase}_agent",
        "provider": "deterministic",
        "model": "local-template",
        "status": "fallback",
        "advisory_only": True,
        "trigger": "fallback",
        "validation_snapshot": {"status": "not_captured"},
        "root_cause_summary": "No external Copilot result was available.",
        "evidence": ["Provided deterministic context only."],
        "recommended_actions": ["Review deterministic artifacts."],
        "blocked_actions": ["Do not mutate official migration state."],
        "confidence": "medium",
        "fallback_used": True,
        "created_at": "2026-05-19T00:00:00Z",
    }


@pytest.mark.parametrize(
    "phase",
    ["analysis", "planning", "assessment", "transformation", "build", "quality", "security", "final"],
)
def test_copilot_assist_accepts_supported_phase_advisory_payloads(phase: str) -> None:
    _validate("copilot_assist.schema.json", _phase_assist_payload(phase))


def test_copilot_assist_rejects_new_payload_when_advisory_only_false() -> None:
    payload = _phase_assist_payload("analysis")
    payload["advisory_only"] = False

    with pytest.raises(jsonschema.ValidationError):
        _validate("copilot_assist.schema.json", payload)


def test_copilot_assist_rejects_unsupported_phase() -> None:
    payload = _phase_assist_payload("deployment")

    with pytest.raises(jsonschema.ValidationError):
        _validate("copilot_assist.schema.json", payload)


@pytest.mark.parametrize("phase,agent", [("analysis", "analysis_agent"), ("planning", "planning_agent")])
def test_copilot_assist_accepts_legacy_analysis_and_planning_payloads(phase: str, agent: str) -> None:
    payload = deepcopy(VALID_PAYLOADS["copilot_assist.schema.json"])
    payload["phase"] = phase
    payload["agent"] = agent

    _validate("copilot_assist.schema.json", payload)


def test_copilot_report_request_requires_advisory_only_true() -> None:
    payload = deepcopy(VALID_PAYLOADS["copilot_report_request.schema.json"])
    payload["advisory_only"] = False

    with pytest.raises(jsonschema.ValidationError):
        _validate("copilot_report_request.schema.json", payload)


@pytest.mark.parametrize(
    "status,fallback_used,output_ref",
    [
        ("generated", False, "final/copilot_migration_report.md"),
        ("generated_with_fallback", True, "final/copilot_migration_report.md"),
        ("failed", False, None),
    ],
)
def test_copilot_report_response_accepts_supported_statuses(
    status: str, fallback_used: bool, output_ref: str | None
) -> None:
    payload = deepcopy(VALID_PAYLOADS["copilot_report_response.schema.json"])
    payload["status"] = status
    payload["fallback_used"] = fallback_used
    payload["output_ref"] = output_ref
    if status == "failed":
        payload["validation"] = {"valid": False, "errors": ["generation failed"]}
        payload["warnings"] = ["No report generated."]

    _validate("copilot_report_response.schema.json", payload)


def test_approval_artifacts_write_and_check(tmp_path: Path) -> None:
    run_dir = tmp_path / ".migration" / "runs" / "run-1"
    _write_lock_inputs(run_dir)

    lock_path = write_approved_plan_lock(run_dir, "run-1")
    decision_path = write_approval_decision(
        run_dir,
        "run-1",
        "approved",
        decided_by="reviewer",
        decided_at="2026-05-19T00:00:00Z",
        plan_lock_ref="approved_plan_lock.json",
    )

    assert lock_path == run_dir / "approval" / "approved_plan_lock.json"
    assert decision_path == run_dir / "approval" / "approval_decision.json"
    assert read_approval_decision(run_dir)["decision"] == "approved"
    assert check_approval_decision(run_dir, expected_run_id="run-1") == ()
    assert check_approved_plan_lock(run_dir, expected_run_id="run-1") == ()


def test_approved_plan_lock_detects_changed_artifact(tmp_path: Path) -> None:
    run_dir = tmp_path / ".migration" / "runs" / "run-1"
    _write_lock_inputs(run_dir)
    write_approved_plan_lock(run_dir, "run-1")

    (run_dir / "planning" / "migration_plan.yaml").write_text("changed: true\n", encoding="utf-8")

    assert check_approved_plan_lock(run_dir) == (
        "approved_plan_lock.json artifact hashes do not match current run artifacts",
    )


def _write_lock_inputs(run_dir: Path) -> None:
    artifacts = {
        "planning/migration_plan.yaml": "schema_version: 1.0.0\n",
        "planning/migration_units.yaml": "units: []\n",
        "assessment/assessment_report.json": "{}\n",
        "analysis/rewrite_plugin_plan.json": "{}\n",
    }
    for rel_path, contents in artifacts.items():
        path = run_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
