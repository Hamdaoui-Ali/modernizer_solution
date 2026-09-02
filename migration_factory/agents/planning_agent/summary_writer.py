from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from migration_factory.agents.planning_agent.paths import get_run_planning_dir
from migration_factory.agents.planning_agent.profile_compatibility import StackFingerprint
from migration_factory.agents.planning_agent.unit_builder import MigrationUnit


@dataclass(frozen=True)
class PlanSummaryPayload:
    run_id: str
    profile: str
    source_stack: StackFingerprint
    target_stack: StackFingerprint
    risks: tuple[str, ...]
    warnings: tuple[str, ...]
    units: tuple[MigrationUnit, ...]


def write_plan_summary(
    modernized_app_path: str,
    payload: PlanSummaryPayload,
) -> Path:
    planning_dir = get_run_planning_dir(modernized_app_path, payload.run_id)
    planning_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = planning_dir / "plan_summary.md"
    artifact_path.write_text(_render_markdown(payload), encoding="utf-8")
    return artifact_path


def _render_markdown(payload: PlanSummaryPayload) -> str:
    source_stack = _format_stack(payload.source_stack)
    target_stack = _format_stack(payload.target_stack)
    unit_lines = [f"1. `{unit.id}` - {unit.goal}" for unit in payload.units]
    risk_lines = _bullets(payload.risks)
    warnings_line = ", ".join(payload.warnings) if payload.warnings else "none"
    next_command = f"migration-factory run transform --run-id {payload.run_id}"

    lines = [
        "# Migration Plan Summary",
        "",
        "## Source Stack",
        source_stack,
        "",
        "## Target Stack",
        target_stack,
        "",
        "## Migration Unit Order",
        *unit_lines,
        "",
        "## Required Approval",
        "Human approval is required before transformation execution.",
        "",
        "## Risks",
        *risk_lines,
        "",
        "## Test Strategy",
        "Run deterministic unit validations in listed order and gate on each unit validation commands before apply.",
        "",
        "## What Will Not Happen",
        "No Copilot/LLM execution tools will run. Assist metadata remains advisory only and not part of execution toolchain.",
        "No source writes occur in planning summary generation.",
        "No approval decisions are made automatically.",
        "",
        "## Next Command",
        f"`{next_command}`",
        "",
        f"Profile: `{payload.profile}`",
        f"Warnings: {warnings_line}",
        "",
    ]
    return "\n".join(lines)


def _format_stack(stack: StackFingerprint) -> str:
    return (
        f"- Build tool: `{stack.build_tool or 'unknown'}`\n"
        f"- Java: `{stack.java or 'unknown'}`\n"
        f"- Spring Boot: `{stack.spring_boot or 'unknown'}`\n"
        f"- Spring Framework: `{stack.spring_framework or 'unknown'}`"
    )


def _bullets(values: tuple[str, ...]) -> list[str]:
    if not values:
        return ["- none"]
    return [f"- {value}" for value in values]
