import json
from pathlib import Path

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load(name: str):
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_analysis_report_schema_accepts_expected_payload():
    schema = _load("analysis_report.schema.json")
    sample = {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "agent": "analysis_agent",
        "status": "PASS",
        "timestamp": "2026-05-14T00:00:00",
        "source_stack": {},
        "target_stack": {},
        "project_metadata": {},
        "rewrite": {},
        "ai_enrichment": {"status": "SKIPPED"}
    }
    jsonschema.validate(sample, schema)


def test_dependency_graph_schema_accepts_expected_payload():
    schema = _load("dependency_graph.schema.json")
    sample = {
        "available": True,
        "raw_file": "dependency-tree.raw.json",
        "format": "json",
        "warning": None,
        "root": {"name": "a:b", "version": "1", "dependencies": []}
    }
    jsonschema.validate(sample, schema)


def test_rewrite_plugin_plan_schema_enforces_status_enum():
    schema = _load("rewrite_plugin_plan.schema.json")
    payload = {
        "schema_version": "1.0.0",
        "status": "USED",
        "transformer_guidance": "x",
        "openrewrite": {},
        "apply_goals_forbidden": True,
    }
    jsonschema.validate(payload, schema)

    try:
        jsonschema.validate({**payload, "status": "SUCCESS"}, schema)
        assert False, "Expected enum validation failure"
    except jsonschema.ValidationError:
        pass


def test_rewrite_impact_summary_schema_enforces_impact_enum():
    schema = _load("rewrite_impact_summary.schema.json")
    payload = {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "agent": "analysis_agent",
        "phase": "analysis",
        "status": "PASS",
        "overall_impact": "UNKNOWN",
        "changed_files": [],
        "high_risk_files": [],
        "migration_signals": {
            "api_or_boot_upgrade": False,
            "javax_removed": False,
            "boot_2_to_3_gap": False,
            "boot_2_to_4_gap": True,
            "boot4_target": True,
            "java_11_to_17_gap": False,
            "java_8_to_21_gap": True,
            "java_21_target": True,
            "javax_present": False,
            "security_config_touched": False,
            "datasource_config_touched": False,
        },
        "blocked_reasons": [],
        "source_modified": False,
        "artifact_refs": {"self": "rewrite_impact_summary.json"},
    }
    jsonschema.validate(payload, schema)

    payload["overall_impact"] = "BLOCKED"
    payload["status"] = "FAIL"
    payload["blocked_reasons"] = ["boom"]
    jsonschema.validate(payload, schema)

    try:
        jsonschema.validate({"impact": "UNKNOWN"}, schema)
        assert False, "Expected old impact shape to fail"
    except jsonschema.ValidationError:
        pass
