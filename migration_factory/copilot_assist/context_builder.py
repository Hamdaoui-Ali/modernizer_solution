"""Context builders for advisory Copilot assist service calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from migration_factory.final_report.context_builder import _redact as redact_value


def build_phase_assist_context(state: Mapping[str, Any], phase: str) -> dict[str, Any]:
    """Build a compact advisory-only context without mutating migration state."""

    return redact_value(
        {
            "run_id": state.get("run_id", ""),
            "phase": phase,
            "statuses": {
                "analysis": state.get("analysis_status", ""),
                "planning": state.get("planning_status", ""),
                "assessment": state.get("assessment_status", ""),
                "orchestration": state.get("orchestration_status", ""),
                "approval": state.get("approval_status", ""),
                "transform": state.get("transform_status", ""),
                "build": state.get("build_status", ""),
                "tests": state.get("test_status", ""),
                "final": state.get("final_status", ""),
            },
            "warnings": list(state.get("warnings") or []),
            "blockers": list(state.get("blockers") or []),
            "errors": list(state.get("errors") or []),
            "artifact_refs": dict(state.get("artifact_refs") or {}),
            "guardrails": _guardrails(),
        }
    )


def load_final_report_context(run_dir: str | Path) -> dict[str, Any]:
    context_path = Path(run_dir) / "final" / "report_context.json"
    return redact_value(json.loads(context_path.read_text(encoding="utf-8")))


def build_final_report_request(
    *,
    run_id: str,
    provider: str,
    model: str,
    context_ref: str = "final/report_context.json",
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return redact_value(
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "provider": provider,
            "model": model,
            "template_id": "copilot_final_migration_report_v1",
            "context_ref": context_ref,
            "advisory_only": True,
            "guardrails": _guardrails(),
            "context": dict(context or {}),
        }
    )


def _guardrails() -> dict[str, bool]:
    return {
        "can_approve": False,
        "can_transform": False,
        "can_mutate_source": False,
        "can_change_gates": False,
        "can_override_status": False,
        "can_create_pr": False,
        "can_deploy": False,
    }


__all__ = ["build_final_report_request", "build_phase_assist_context", "load_final_report_context"]
