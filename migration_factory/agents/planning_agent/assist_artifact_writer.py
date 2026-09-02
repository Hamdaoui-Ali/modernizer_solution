from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from migration_factory.contracts.constants import SCHEMA_VERSION
from migration_factory.contracts.copilot_artifacts import advisory_can_modify_flags


@dataclass(frozen=True)
class CopilotAssistArtifactPayload:
    run_id: str
    status: str
    provider: str | None = None
    auth: str | None = None
    model: str | None = None
    requested_model: str | None = None
    resolved_model: str | None = None
    model_source: str | None = None
    model_verified: bool = False
    inputs_summary: dict[str, Any] = field(default_factory=dict)
    advisory_summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def write_copilot_assist_artifact(
    modernized_app_path: str,
    payload: CopilotAssistArtifactPayload,
    run_id: str | None = None,
) -> None:
    resolved_run_id = run_id or payload.run_id
    planning_dir = Path(modernized_app_path) / ".migration" / "runs" / resolved_run_id / "planning"
    planning_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = planning_dir / "copilot_assist.json"
    artifact_body = {
        "schema_version": SCHEMA_VERSION,
        "run_id": payload.run_id,
        "agent": "planning_agent",
        "phase": "planning",
        "status": payload.status,
        "provider": payload.provider,
        "auth": payload.auth,
        "model": payload.model,
        "requested_model": payload.requested_model,
        "resolved_model": payload.resolved_model,
        "model_source": payload.model_source,
        "model_verified": payload.model_verified,
        "inputs_summary": payload.inputs_summary,
        "advisory_summary": payload.advisory_summary,
        "warnings": payload.warnings,
        "error": payload.error,
        **advisory_can_modify_flags(),
        "artifact_refs": {"self": "copilot_assist.json"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    artifact_path.write_text(
        json.dumps(artifact_body, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
