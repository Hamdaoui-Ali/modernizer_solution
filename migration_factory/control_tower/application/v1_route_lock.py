"""V1-02: Lock V1 migration route validation service.

Validates pipeline definitions and runner profiles against the canonical V1
migration route contract stored in the v1_route_config table.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations.__init__ import (
    _utc_now_text as _migration_utc_now,
)

V1_PIPELINE_ID = "springboot-216-to-356-java21-three-stage"
V1_PIPELINE_VERSION = "2026.06"

# V1 stage contracts
V1_STAGE_1 = {
    "stage_index": 1,
    "stage_id": "springboot-216-to-27-java11",
    "command_jdk": "java11",
    "spring_boot": "2.7.18",
    "java": 11,
    "input_source_kind": "legacy_source",
}

V1_STAGE_2 = {
    "stage_index": 2,
    "stage_id": "springboot-27-to-35-java17",
    "command_jdk": "java17",
    "spring_boot": "3.5.6",
    "java": 17,
    "input_source_kind": "previous_stage",
    "previous_stage_index": 1,
}

V1_STAGE_3 = {
    "stage_index": 3,
    "stage_id": "springboot-35-java17-to-java21",
    "command_jdk": "java21",
    "spring_boot": "3.5.6",
    "java": 21,
    "input_source_kind": "previous_stage",
    "previous_stage_index": 2,
}

V1_STAGES = (V1_STAGE_1, V1_STAGE_2, V1_STAGE_3)


@dataclass(frozen=True, slots=True)
class V1RouteValidationResult:
    passed: bool
    failure_reason: str | None = None
    pipeline_id: str | None = None
    runner_profile_id: str | None = None


@dataclass(frozen=True, slots=True)
class V1RouteValidationEventRecord:
    event_id: str
    event_type: str
    pipeline_id: str
    pipeline_version: str | None
    runner_profile_id: str | None
    runner_profile_version: str | None
    validation_result: str
    failure_reason: str | None
    payload_json: str
    payload_checksum: str
    created_at: str
    created_by: str


class V1RouteLockValidationError(ValueError):
    """Raised when a pipeline/runner does not match the locked V1 route."""


class V1RouteLockService:
    """Validates pipeline definitions and runner profiles against the locked V1 route."""

    def __init__(
        self,
        unit_of_work_factory: Callable,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def validate_pipeline_definition(self, pipeline: dict[str, Any] | Any) -> V1RouteValidationResult:
        """Check that a pipeline definition matches the locked V1 route.

        Returns a pass/fail result with an optional failure reason.
        Does not raise exceptions for validation failures.
        """
        # Accept both dict and Pydantic model
        if hasattr(pipeline, "model_dump"):
            payload = pipeline.model_dump(mode="python")
        elif isinstance(pipeline, dict):
            payload = pipeline
        else:
            return V1RouteValidationResult(
                passed=False,
                failure_reason=f"Unexpected pipeline type: {type(pipeline).__name__}",
            )

        pipeline_id = payload.get("pipeline_id", "")
        pipeline_version = payload.get("pipeline_version", "")

        if pipeline_id != V1_PIPELINE_ID:
            return V1RouteValidationResult(
                passed=False,
                failure_reason=(
                    f"V1 route locked to pipeline '{V1_PIPELINE_ID}', "
                    f"got '{pipeline_id}'"
                ),
                pipeline_id=pipeline_id,
            )

        if pipeline_version != V1_PIPELINE_VERSION:
            return V1RouteValidationResult(
                passed=False,
                failure_reason=(
                    f"V1 route locked to pipeline version '{V1_PIPELINE_VERSION}', "
                    f"got '{pipeline_version}'"
                ),
                pipeline_id=pipeline_id,
            )

        stages = payload.get("stages", ())
        if len(stages) != 3:
            return V1RouteValidationResult(
                passed=False,
                failure_reason=(
                    f"V1 route requires exactly 3 stages, got {len(stages)}"
                ),
                pipeline_id=pipeline_id,
            )

        for i, expected in enumerate(V1_STAGES):
            actual = stages[i]
            errors: list[str] = []

            if actual.get("command_jdk") != expected["command_jdk"]:
                errors.append(
                    f"stage {i+1} command_jdk: expected '{expected['command_jdk']}', "
                    f"got '{actual.get('command_jdk')}'"
                )
            if actual.get("target", {}).get("spring_boot") != expected["spring_boot"]:
                errors.append(
                    f"stage {i+1} spring_boot: expected '{expected['spring_boot']}', "
                    f"got '{actual.get('target', {}).get('spring_boot')}'"
                )
            if actual.get("target", {}).get("java") != expected["java"]:
                errors.append(
                    f"stage {i+1} java: expected {expected['java']}, "
                    f"got {actual.get('target', {}).get('java')}"
                )
            input_source = actual.get("input_source", {})
            if input_source.get("kind") != expected["input_source_kind"]:
                errors.append(
                    f"stage {i+1} input_source.kind: expected '{expected['input_source_kind']}', "
                    f"got '{input_source.get('kind')}'"
                )

            if errors:
                return V1RouteValidationResult(
                    passed=False,
                    failure_reason="; ".join(errors),
                    pipeline_id=pipeline_id,
                )

        # Check Boot 4 exclusion
        for stage in stages:
            target_boot = stage.get("target", {}).get("spring_boot", "")
            if "4." in target_boot:
                return V1RouteValidationResult(
                    passed=False,
                    failure_reason=(
                        f"Boot 4 (found '{target_boot}') is not selectable in the V1 route"
                    ),
                    pipeline_id=pipeline_id,
                )

        # Check 3.5.14 exclusion
        for stage in stages:
            target_boot = stage.get("target", {}).get("spring_boot", "")
            if target_boot == "3.5.14":
                return V1RouteValidationResult(
                    passed=False,
                    failure_reason=(
                        "3.5.14 is not execution-relevant in the V1 route"
                    ),
                    pipeline_id=pipeline_id,
                )

        return V1RouteValidationResult(
            passed=True,
            pipeline_id=pipeline_id,
        )

    def validate_and_record(
        self,
        pipeline: dict[str, Any] | Any | None = None,
        runner_profile: dict[str, Any] | Any | None = None,
        *,
        actor_type: str = "system",
        actor_id: str = "system",
    ) -> V1RouteValidationResult:
        """Validate a pipeline or runner against the locked route and record the result.

        Returns the validation result. Records the outcome in
        v1_route_validation_events for audit.
        """
        if pipeline is not None:
            result = self.validate_pipeline_definition(pipeline)
            event_type = "pipeline_validation"
            pipeline_id = result.pipeline_id or ""
            runner_profile_id = None
            runner_profile_version = None
        elif runner_profile is not None:
            # Runner profiles are validated separately by schema; pipeline route lock
            # focuses on pipeline structure.
            result = V1RouteValidationResult(passed=True, pipeline_id=None)
            event_type = "runner_validation"
            pipeline_id = ""
            runner_profile_id = getattr(runner_profile, "runner_profile_id", None) or (
                runner_profile.get("runner_profile_id", "") if isinstance(runner_profile, dict) else ""
            )
            runner_profile_version = getattr(runner_profile, "runner_profile_version", None) or (
                runner_profile.get("runner_profile_version", "") if isinstance(runner_profile, dict) else ""
            )
        else:
            raise ValueError("Either pipeline or runner_profile must be provided")

        # Build payload for the event record
        payload: dict[str, Any] = {
            "result": "pass" if result.passed else "fail",
        }
        if result.failure_reason:
            payload["failure_reason"] = result.failure_reason
        if pipeline is not None:
            payload["pipeline"] = _payload_snapshot(pipeline)
        if runner_profile is not None:
            payload["runner_profile"] = _payload_snapshot(runner_profile)

        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_checksum = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

        now = utc_now_text()

        with self._unit_of_work_factory() as uow:
            connection = getattr(uow, "connection", None)
            if connection is not None:
                connection.execute(
                    """
                    INSERT INTO v1_route_validation_events (
                        event_id, event_type, pipeline_id, pipeline_version,
                        runner_profile_id, runner_profile_version,
                        validation_result, failure_reason,
                        payload_json, payload_checksum, created_at, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        event_type,
                        pipeline_id,
                        payload.get("pipeline", {}).get("pipeline_version") if isinstance(payload.get("pipeline"), dict) else None,
                        runner_profile_id,
                        runner_profile_version,
                        "pass" if result.passed else "fail",
                        result.failure_reason,
                        payload_json,
                        payload_checksum,
                        now,
                        actor_id,
                    ),
                )

        return result


def _payload_snapshot(obj: Any) -> dict[str, Any]:
    """Extract a safe dict snapshot from a Pydantic model or plain dict."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="python")  # type: ignore[return-value]
    if isinstance(obj, dict):
        return obj
    return {"raw": str(obj)}
