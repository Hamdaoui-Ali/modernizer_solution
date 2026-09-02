from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from migration_factory.agents.planning_agent.paths import get_run_planning_dir
from migration_factory.agents.planning_agent.profile_compatibility import StackFingerprint
from migration_factory.agents.planning_agent.unit_builder import MigrationUnit
from migration_factory.contracts.constants import SCHEMA_VERSION


@dataclass(frozen=True)
class MigrationPlanPayload:
    run_id: str
    profile: str
    source_stack: StackFingerprint
    target_stack: StackFingerprint
    risks: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    units: tuple[MigrationUnit, ...]
    strategy: str | None = None
    risk_level: str | None = None
    production_allowed: bool | None = None
    fallback_profile: str | None = None


def write_migration_plan(
    modernized_app_path: str,
    payload: MigrationPlanPayload,
) -> Path:
    planning_dir = get_run_planning_dir(modernized_app_path, payload.run_id)
    planning_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = planning_dir / "migration_plan.yaml"
    artifact_path.write_text(_render_plan_yaml(payload), encoding="utf-8")
    return artifact_path


def write_migration_units(
    modernized_app_path: str,
    run_id: str,
    units: tuple[MigrationUnit, ...],
) -> Path:
    planning_dir = get_run_planning_dir(modernized_app_path, run_id)
    planning_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = planning_dir / "migration_units.yaml"
    artifact_path.write_text(_render_units_yaml(run_id, units), encoding="utf-8")
    return artifact_path


def _render_plan_yaml(payload: MigrationPlanPayload) -> str:
    executable = not bool(payload.blockers)
    unit_refs = [unit.id for unit in payload.units]
    status = _status(payload.blockers, payload.warnings, payload.risks)
    risk = _risk(payload.blockers, payload.risks)

    lines: list[str] = [
        f"schema_version: {_yaml_quote(SCHEMA_VERSION)}",
        f"run_id: {_yaml_quote(payload.run_id)}",
        f"status: {_yaml_quote(status)}",
        f"risk: {_yaml_quote(risk)}",
        f"profile: {_yaml_quote(payload.profile)}",
        "source_stack:",
        f"  build_tool: {_yaml_scalar(payload.source_stack.build_tool)}",
        f"  java: {_yaml_scalar(payload.source_stack.java)}",
        f"  spring_boot: {_yaml_scalar(payload.source_stack.spring_boot)}",
        "target_stack:",
        f"  build_tool: {_yaml_scalar(payload.target_stack.build_tool)}",
        f"  java: {_yaml_scalar(payload.target_stack.java)}",
        f"  spring_boot: {_yaml_scalar(payload.target_stack.spring_boot)}",
        f"  spring_framework: {_yaml_scalar(payload.target_stack.spring_framework)}",
        f"executable: {'true' if executable else 'false'}",
        "requires_human_approval: true",
        "risks:",
    ]
    lines.extend(_yaml_list(payload.risks, indent=2))
    lines.append("blockers:")
    lines.extend(_yaml_list(payload.blockers, indent=2))
    lines.append("warnings:")
    lines.extend(_yaml_list(payload.warnings, indent=2))
    if any(
        value is not None
        for value in (
            payload.strategy,
            payload.risk_level,
            payload.production_allowed,
            payload.fallback_profile,
        )
    ):
        lines.append("profile_governance:")
        lines.append(f"  strategy: {_yaml_scalar(payload.strategy)}")
        lines.append(f"  risk_level: {_yaml_scalar(payload.risk_level)}")
        if payload.production_allowed is None:
            lines.append("  production_allowed: null")
        else:
            lines.append(
                f"  production_allowed: {'true' if payload.production_allowed else 'false'}"
            )
        lines.append(f"  fallback_profile: {_yaml_scalar(payload.fallback_profile)}")
    lines.append("unit_references:")
    lines.extend(_yaml_list(tuple(unit_refs), indent=2))
    lines.append("artifact_refs:")
    lines.append("  self: \"migration_plan.yaml\"")
    lines.append("  migration_units: \"migration_units.yaml\"")
    lines.append("  plan_summary: \"plan_summary.md\"")
    lines.append("  approval_request: \"approval_request.json\"")
    lines.append("")
    return "\n".join(lines)


def _render_units_yaml(run_id: str, units: tuple[MigrationUnit, ...]) -> str:
    lines: list[str] = [
        f"schema_version: {_yaml_quote(SCHEMA_VERSION)}",
        f"run_id: {_yaml_quote(run_id)}",
        "status: \"PASS\"",
        "artifact_refs:",
        "  self: \"migration_units.yaml\"",
        "  migration_plan: \"migration_plan.yaml\"",
        "units:",
    ]

    if not units:
        lines.append("  []")
        lines.append("")
        return "\n".join(lines)

    for unit in units:
        lines.append(f"  - id: {_yaml_quote(unit.id)}")
        lines.append(f"    goal: {_yaml_quote(unit.goal)}")
        lines.append("    tools:")
        lines.extend(_yaml_list(unit.tools, indent=6))
        lines.append("    validation:")
        lines.extend(_yaml_list(unit.validation, indent=6))
        lines.append(f"    writes_source: {'true' if unit.writes_source else 'false'}")
        lines.append(f"    required: {'true' if unit.required else 'false'}")
        lines.append("    expected_artifacts:")
        lines.extend(_yaml_list(unit.expected_artifacts, indent=6))
        lines.append(f"    rollback_strategy: {_yaml_quote(unit.rollback_strategy)}")
        lines.append(f"    blocking_gate: {_yaml_quote(unit.blocking_gate)}")
        lines.append("    assist_policy:")
        lines.append(
            "      copilot_sdk_allowed: "
            f"{'true' if unit.assist_policy.copilot_sdk_allowed else 'false'}"
        )
        lines.append(
            "      copilot_sdk_mode: "
            f"{_yaml_quote(unit.assist_policy.copilot_sdk_mode)}"
        )

    lines.append("")
    return "\n".join(lines)


def _yaml_list(values: tuple[str, ...], indent: int) -> list[str]:
    pad = " " * indent
    if not values:
        return [f"{pad}[]"]
    return [f"{pad}- {_yaml_quote(value)}" for value in values]


def _yaml_scalar(value: str | None) -> str:
    if value is None:
        return "null"
    return _yaml_quote(value)


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _status(blockers: tuple[str, ...], warnings: tuple[str, ...], risks: tuple[str, ...]) -> str:
    if blockers or any("[BLOCKER]" in risk for risk in risks):
        return "FAIL"
    if warnings or risks:
        return "WARNING"
    return "PASS"


def _risk(blockers: tuple[str, ...], risks: tuple[str, ...]) -> str:
    if blockers or any("[BLOCKER]" in risk for risk in risks):
        return "BLOCKED"
    if any("[HIGH]" in risk or "HIGH" in risk for risk in risks):
        return "HIGH"
    if any("[WARNING]" in risk or "MEDIUM" in risk for risk in risks):
        return "MEDIUM"
    return "LOW" if risks else "UNKNOWN"
