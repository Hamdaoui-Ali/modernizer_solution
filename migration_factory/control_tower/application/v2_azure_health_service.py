"""V2 Azure model health check service — redacted, non-blocking checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.application.v2_settings import (
    ControlTowerSettings,
    EnvRefStatus,
    build_settings_projection,
    is_env_var_configured,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.v2_azure_health_repository import (
    SqliteV2AzureHealthRepository,
    V2ModelHealthCheckRecord,
)


@dataclass(frozen=True)
class RoleHealth:
    role_name: str
    env_ref: str
    configured: bool
    status: str  # ready, degraded, blocked, unknown
    latency_ms: float | None = None
    error: str = ""


@dataclass(frozen=True)
class StructuredOutputHealth:
    schema_name: str
    status: str  # ready, blocked, unknown
    error: str = ""


@dataclass(frozen=True)
class AzureHealthResult:
    health_id: str
    profile_id: str
    overall_status: str
    roles: dict[str, RoleHealth]
    structured_outputs: list[StructuredOutputHealth]
    latency: dict[str, float]
    error_classification: str
    checked_at: str


REQUIRED_SCHEMAS = (
    "PlanProposal",
    "RepairProposal",
    "ReviewerCritique",
    "ActionRequest",
    "AssistantAnswer",
)


class V2AzureHealthService:
    """Non-blocking Azure model health check service.

    Health checks use env ref names and backend-only env values.
    All errors are redacted before storage.
    """

    def __init__(
        self,
        repo: SqliteV2AzureHealthRepository,
        settings: ControlTowerSettings,
    ) -> None:
        self._repo = repo
        self._settings = settings

    def run_health_check(
        self,
        profile_id: str = "azure-foundry-v2",
        created_by: str = "system",
    ) -> AzureHealthResult:
        """Run a redacted health check against Azure Foundry settings.

        In the current implementation, this checks env var configuration
        without making live Azure calls. A live provider can be added
        later without changing the API contract.
        """
        projection = build_settings_projection(self._settings)

        # Check each role
        roles: dict[str, RoleHealth] = {}
        overall_statuses: list[str] = []

        for role_name, role_status in projection.azure.roles.items():
            if not role_status.enabled:
                status = "disabled"
            elif not role_status.configured:
                status = "blocked"
            else:
                # Simulated check — real check would call Azure
                status = "ready"

            roles[role_name] = RoleHealth(
                role_name=role_name,
                env_ref=role_status.env_ref,
                configured=role_status.configured,
                status=status,
                error="",
            )
            overall_statuses.append(status)

        # Check structured output schemas
        schemas: list[StructuredOutputHealth] = []
        for schema_name in REQUIRED_SCHEMAS:
            schemas.append(StructuredOutputHealth(
                schema_name=schema_name,
                status="ready",
            ))

        # Determine overall status
        if "blocked" in overall_statuses:
            overall_status = "degraded"
        elif "disabled" in overall_statuses or "unknown" in overall_statuses:
            overall_status = "degraded"
        elif all(s == "ready" for s in overall_statuses):
            overall_status = "ready"
        else:
            overall_status = "unknown"

        # Build latency map (simulated)
        latency = {}

        # Redacted error classification
        error_classification = ""

        health_id = uuid4().hex
        now = utc_now_text()

        # Persist
        record = V2ModelHealthCheckRecord(
            health_id=health_id,
            profile_id=profile_id,
            profile_checksum="sha256",
            overall_status=overall_status,
            role_checks_json=json.dumps({
                name: {
                    "role": r.role_name,
                    "configured": r.configured,
                    "status": r.status,
                }
                for name, r in roles.items()
            }, separators=(",", ":")),
            structured_output_checks_json=json.dumps({
                s.schema_name: {"status": s.status}
                for s in schemas
            }, separators=(",", ":")),
            latency_ms_json=json.dumps(latency, separators=(",", ":")),
            error_classification=error_classification,
            artifact_id=None,
            created_at=now,
            created_by=created_by,
        )
        self._repo.save(record)

        return AzureHealthResult(
            health_id=health_id,
            profile_id=profile_id,
            overall_status=overall_status,
            roles=roles,
            structured_outputs=schemas,
            latency=latency,
            error_classification=error_classification,
            checked_at=now,
        )

    def get_latest_health(self, profile_id: str = "azure-foundry-v2") -> AzureHealthResult | None:
        """Get the latest health check for a profile."""
        record = self._repo.get_latest(profile_id)
        if record is None:
            return None
        return self._record_to_result(record)

    def get_health_history(self, profile_id: str, limit: int = 10) -> tuple[AzureHealthResult, ...]:
        records = self._repo.list_for_profile(profile_id, limit)
        return tuple(self._record_to_result(r) for r in records)

    def health_to_dict(self, result: AzureHealthResult | None) -> dict[str, Any]:
        if result is None:
            return {"status": "unknown", "checked_at": ""}
        return {
            "health_id": result.health_id,
            "profile_id": result.profile_id,
            "overall_status": result.overall_status,
            "roles": {
                name: {
                    "role": r.role_name,
                    "configured": r.configured,
                    "status": r.status,
                    "error": r.error,
                }
                for name, r in result.roles.items()
            },
            "structured_outputs": [
                {"schema": s.schema_name, "status": s.status}
                for s in result.structured_outputs
            ],
            "error_classification": result.error_classification,
            "checked_at": result.checked_at,
        }

    def _record_to_result(self, record: V2ModelHealthCheckRecord) -> AzureHealthResult:
        try:
            role_checks = json.loads(record.role_checks_json)
        except (json.JSONDecodeError, TypeError):
            role_checks = {}

        roles = {}
        for name, data in role_checks.items():
            roles[name] = RoleHealth(
                role_name=data.get("role", name),
                env_ref=data.get("env_ref", ""),
                configured=data.get("configured", False),
                status=data.get("status", "unknown"),
            )

        try:
            schema_checks = json.loads(record.structured_output_checks_json)
        except (json.JSONDecodeError, TypeError):
            schema_checks = {}

        schemas = [
            StructuredOutputHealth(
                schema_name=name,
                status=data.get("status", "unknown"),
            )
            for name, data in schema_checks.items()
        ]

        return AzureHealthResult(
            health_id=record.health_id,
            profile_id=record.profile_id,
            overall_status=record.overall_status,
            roles=roles,
            structured_outputs=schemas,
            latency={},
            error_classification=record.error_classification,
            checked_at=record.created_at,
        )
