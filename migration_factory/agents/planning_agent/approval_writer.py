from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from migration_factory.agents.planning_agent.paths import get_run_planning_dir
from migration_factory.agents.planning_agent.unit_builder import MigrationUnit
from migration_factory.contracts.constants import APPROVAL_DECISION_VALUES, SCHEMA_VERSION


DECISION_OPTIONS = APPROVAL_DECISION_VALUES


@dataclass(frozen=True)
class ApprovalRequestPayload:
    run_id: str
    profile: str
    summary: str
    units: tuple[MigrationUnit, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


def write_approval_request(
    modernized_app_path: str,
    payload: ApprovalRequestPayload,
) -> Path:
    planning_dir = get_run_planning_dir(modernized_app_path, payload.run_id)
    planning_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = planning_dir / "approval_request.json"
    artifact_path.write_text(
        json.dumps(_build_payload(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact_path


def _build_payload(payload: ApprovalRequestPayload) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": payload.run_id,
        "agent": "planning_agent",
        "phase": "approval",
        "status": "PASS" if not payload.blockers else "FAIL",
        "profile": payload.profile,
        "requires_human_approval": True,
        "decision_options": list(DECISION_OPTIONS),
        "recommended_decision": None,
        "summary": payload.summary,
        "units_to_execute": [unit.id for unit in payload.units],
        "blockers": list(payload.blockers),
        "warnings": list(payload.warnings),
        "artifact_refs": {
            "migration_plan": "migration_plan.yaml",
            "migration_units": "migration_units.yaml",
            "plan_summary": "plan_summary.md",
        },
    }
