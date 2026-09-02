from migration_factory.contracts import (
    ANALYSIS_OPTIONAL_ARTIFACTS,
    ANALYSIS_REQUIRED_ARTIFACTS,
    APPROVAL_DECISION_VALUES,
    ASSESSMENT_REQUIRED_ARTIFACTS,
    COPILOT_STATUS_VALUES,
    DIRECTORY_NAMES,
    PLANNING_OPTIONAL_ARTIFACTS,
    PLANNING_REQUIRED_ARTIFACTS,
    RISK_VALUES,
    SCHEMA_VERSION,
    STATUS_VALUES,
)


def test_contract_enums_are_frozen() -> None:
    assert SCHEMA_VERSION == "1.0.0"
    assert STATUS_VALUES == ("PASS", "FAIL", "WARNING", "SKIPPED")
    assert RISK_VALUES == ("LOW", "MEDIUM", "HIGH", "BLOCKED", "UNKNOWN")
    assert APPROVAL_DECISION_VALUES == ("approved", "rejected", "replan_required")
    assert COPILOT_STATUS_VALUES == ("USED", "SKIPPED", "UNAVAILABLE", "ERROR")


def test_required_artifact_names_are_frozen() -> None:
    assert DIRECTORY_NAMES == ("analysis", "planning", "assessment")
    assert ANALYSIS_REQUIRED_ARTIFACTS == (
        "analysis_report.json",
        "dependency_graph.json",
        "test_inventory.json",
        "analysis_summary.md",
    )
    assert ANALYSIS_OPTIONAL_ARTIFACTS == (
        "config_inventory.json",
        "rewrite_plugin_plan.json",
        "rewrite_preview.json",
        "rewrite_dry_run.patch",
        "rewrite_impact_summary.json",
        "read_only_verification.json",
    )
    assert PLANNING_REQUIRED_ARTIFACTS == (
        "migration_plan.yaml",
        "migration_units.yaml",
        "plan_summary.md",
        "approval_request.json",
        "plan_validation_report.json",
    )
    assert PLANNING_OPTIONAL_ARTIFACTS == (
        "copilot_assist.json",
        "target_dependency_plan.json",
    )
    assert ASSESSMENT_REQUIRED_ARTIFACTS == (
        "assessment_report.json",
        "assessment_summary.md",
    )
