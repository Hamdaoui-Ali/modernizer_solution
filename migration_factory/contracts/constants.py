SCHEMA_VERSION = "1.0.0"

DIRECTORY_NAMES: tuple[str, ...] = (
    "analysis",
    "planning",
    "assessment",
)

ANALYSIS_REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "analysis_report.json",
    "dependency_graph.json",
    "test_inventory.json",
    "analysis_summary.md",
)

ANALYSIS_OPTIONAL_ARTIFACTS: tuple[str, ...] = (
    "config_inventory.json",
    "rewrite_plugin_plan.json",
    "rewrite_preview.json",
    "rewrite_dry_run.patch",
    "rewrite_impact_summary.json",
    "read_only_verification.json",
)

PLANNING_REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "migration_plan.yaml",
    "migration_units.yaml",
    "plan_summary.md",
    "approval_request.json",
    "plan_validation_report.json",
)

PLANNING_OPTIONAL_ARTIFACTS: tuple[str, ...] = (
    "copilot_assist.json",
    "target_dependency_plan.json",
)

ASSESSMENT_REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "assessment_report.json",
    "assessment_summary.md",
)

STATUS_VALUES: tuple[str, ...] = (
    "PASS",
    "FAIL",
    "WARNING",
    "SKIPPED",
)

RISK_VALUES: tuple[str, ...] = (
    "LOW",
    "MEDIUM",
    "HIGH",
    "BLOCKED",
    "UNKNOWN",
)

APPROVAL_DECISION_VALUES: tuple[str, ...] = (
    "approved",
    "rejected",
    "replan_required",
)

# LEGACY: retained for backward compatibility with existing schemas.
# Copilot has been removed; these values no longer control runtime behavior.
COPILOT_STATUS_VALUES: tuple[str, ...] = (
    "USED",
    "SKIPPED",
    "UNAVAILABLE",
    "ERROR",
)

ALL_REQUIRED_ARTIFACTS: tuple[str, ...] = (
    *ANALYSIS_REQUIRED_ARTIFACTS,
    *PLANNING_REQUIRED_ARTIFACTS,
    *ASSESSMENT_REQUIRED_ARTIFACTS,
)
