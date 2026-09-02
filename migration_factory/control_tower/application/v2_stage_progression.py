"""V2 stage auto-progression from previous stage sandboxes."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
    V2MigrationSetupRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
)
from migration_factory.control_tower.schemas.profile_model import (
    default_source_profile_id,
    default_target_profile_id,
    get_migration_profile,
    list_migration_profiles,
)
from migration_factory.control_tower.schemas.profile_validation import (
    ProfilePairValidation,
    validate_profile_pair,
)
from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
from migration_factory.control_tower.schemas.profile_checkpoint_metadata import (
    SkippedStageLedgerEntry,
)
from migration_factory.control_tower.application.v2_profile_runtime import (
    resolve_catalog_for_runtime_profile,
    resolve_execution_jdk_id_for_runtime_profile,
    resolve_execution_jdk_env_var_for_runtime_profile,
    resolve_runtime_profile_for_route,
)


TERMINAL_STAGE_INDEX = 4

STAGE_CONFIG = {
    2: {
        "profile": "springboot-2.7-to-3.5-java17",
        "jdk_env": "JAVA17_HOME",
        "jdk_id": "java17",
        "expected_major": 17,
    },
    3: {
        "profile": "springboot-3.5-java17-to-java21",
        "jdk_env": "JAVA21_HOME",
        "jdk_id": "java21",
        "expected_major": 21,
    },
    4: {
        "profile": "springboot-3.5-java21-to-4.0-java21",
        "jdk_env": "JAVA21_HOME",
        "jdk_id": "java21",
        "expected_major": 21,
    },
}

RUNNER_MODULE = "migration_factory.orchestrator.runner"


# ── profile route model (AMF-264 / F3-T3) ─────────────────────────

_PROFILE_ORDERING: tuple[str, ...] = tuple(
    profile.profile_id for profile in list_migration_profiles()
)

_PROFILE_TO_STAGE_INDEX: dict[str, int] = {
    profile.profile_id: profile.stage_index
    for profile in list_migration_profiles()
}

@dataclass(frozen=True)
class RouteStep:
    route_step_index: int
    stage_index: int
    source_profile: str
    target_profile: str
    runtime_profile: str
    catalog: str
    execution_jdk: str
    status: str = "pending"
    approval_gate_id: str = ""
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProfileRoute:
    source_profile: str
    target_profile: str
    source_level: int
    target_level: int
    included_stages: tuple[int, ...]
    excluded_stages: tuple[int, ...]
    skipped_stages: tuple[int, ...]
    route_steps: tuple[RouteStep, ...]
    valid: bool
    reason: str = ""


def compute_profile_route(
    source_profile: str,
    target_profile: str,
) -> ProfileRoute:
    pair_validation = validate_profile_pair(source_profile, target_profile)
    if not pair_validation.valid:
        return _invalid_route_from_validation(pair_validation)

    source_definition = get_migration_profile(source_profile)
    target_definition = get_migration_profile(target_profile)
    if source_definition is None:
        return ProfileRoute(
            source_profile=source_profile,
            target_profile=target_profile,
            source_level=-1,
            target_level=-1,
            included_stages=(),
            excluded_stages=(),
            skipped_stages=(),
            route_steps=(),
            valid=False,
            reason="source profile is not recognized",
        )
    if target_definition is None:
        return ProfileRoute(
            source_profile=source_profile,
            target_profile=target_profile,
            source_level=-1,
            target_level=-1,
            included_stages=(),
            excluded_stages=(),
            skipped_stages=(),
            route_steps=(),
            valid=False,
            reason="target profile is not recognized",
        )

    source_idx = source_definition.order_index
    target_idx = target_definition.order_index

    source_stage = _PROFILE_TO_STAGE_INDEX[source_profile]
    target_stage = _PROFILE_TO_STAGE_INDEX[target_profile]

    included = tuple(
        stage for stage in STAGE_CONFIG
        if source_stage < stage <= target_stage
    )
    excluded = tuple(
        stage for stage in STAGE_CONFIG
        if stage > target_stage
    )
    skipped = tuple(
        stage for stage in STAGE_CONFIG
        if stage <= source_stage
    )
    route_steps = build_route_steps(source_profile, target_profile)

    print(
        "[route-built]",
        {
            "source_profile": source_profile,
            "target_profile": target_profile,
            "included_stages": list(included),
            "route_steps": [
                {
                    "route_step_index": s.route_step_index,
                    "stage_index": s.stage_index,
                    "source_profile": s.source_profile,
                    "target_profile": s.target_profile,
                    "runtime_profile": s.runtime_profile,
                    "catalog": s.catalog,
                }
                for s in route_steps
            ],
        },
        file=sys.stderr,
        flush=True,
    )

    return ProfileRoute(
        source_profile=source_profile,
        target_profile=target_profile,
        source_level=source_idx,
        target_level=target_idx,
        included_stages=included,
        excluded_stages=excluded,
        skipped_stages=skipped,
        route_steps=route_steps,
        valid=True,
    )


def is_stage_included_in_route(route: ProfileRoute, stage_index: int) -> bool:
    return route.valid and stage_index in route.included_stages


def is_stage_excluded_from_route(route: ProfileRoute, stage_index: int) -> bool:
    return route.valid and stage_index in route.excluded_stages


def is_target_reached(route: ProfileRoute, current_stage: int) -> bool:
    if not route.valid:
        return True
    return current_stage >= max(route.included_stages) if route.included_stages else True


def next_required_stage(route: ProfileRoute, current_stage: int) -> int | None:
    if not route.valid:
        return None
    for stage in route.included_stages:
        if stage > current_stage:
            return stage
    return None


def _route_step_for_stage(route: ProfileRoute, stage_index: int) -> RouteStep | None:
    if not route.valid or not route.route_steps:
        return None
    for route_step in route.route_steps:
        if route_step.stage_index == stage_index:
            return route_step
    return None


def _command_matches_expected_route(
    command: V2StageCommandRecord,
    *,
    expected_profile: str,
    expected_legacy_path: str,
) -> bool:
    try:
        argv = json.loads(command.argv_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(argv, list):
        return False

    profile_value = ""
    legacy_value = ""
    for index, token in enumerate(argv):
        if token == "--profile" and index + 1 < len(argv):
            profile_value = str(argv[index + 1])
        if token == "--legacy" and index + 1 < len(argv):
            legacy_value = str(argv[index + 1])

    return profile_value == expected_profile and legacy_value == expected_legacy_path


def build_route_steps(source_profile: str, target_profile: str) -> tuple[RouteStep, ...]:
    source_definition = get_migration_profile(source_profile)
    target_definition = get_migration_profile(target_profile)
    if source_definition is None or target_definition is None:
        return ()

    if target_definition.order_index <= source_definition.order_index:
        return ()

    route_steps: list[RouteStep] = []
    ordered_profiles = list(_PROFILE_ORDERING[source_definition.order_index : target_definition.order_index + 1])
    for route_step_index, (step_source, step_target) in enumerate(zip(ordered_profiles, ordered_profiles[1:]), start=1):
        runtime_profile = resolve_runtime_profile_for_route(step_source, step_target)
        route_steps.append(
            RouteStep(
                route_step_index=route_step_index,
                stage_index=_PROFILE_TO_STAGE_INDEX[step_target],
                source_profile=step_source,
                target_profile=step_target,
                runtime_profile=runtime_profile,
                catalog=resolve_catalog_for_runtime_profile(runtime_profile),
                execution_jdk=resolve_execution_jdk_id_for_runtime_profile(runtime_profile),
            )
        )
    return tuple(route_steps)


def route_step_execution_stage_index(route_step: RouteStep) -> int:
    """Return the backend execution stage that emits live events for a route step."""
    return 1 if route_step.route_step_index == 1 else route_step.stage_index


def project_route_steps(
    route: ProfileRoute,
    *,
    stages: tuple[dict[str, Any], ...] = (),
) -> tuple[RouteStep, ...]:
    if not route.valid or not route.route_steps:
        return ()

    stage_state_by_index = {
        int(stage_state.get("stage_index") or 0): stage_state
        for stage_state in stages
        if isinstance(stage_state, dict)
    }
    projected: list[RouteStep] = []
    for step in route.route_steps:
        execution_stage_index = route_step_execution_stage_index(step)
        stage_state = stage_state_by_index.get(execution_stage_index, {})
        projected.append(
            RouteStep(
                route_step_index=step.route_step_index,
                stage_index=step.stage_index,
                source_profile=step.source_profile,
                target_profile=step.target_profile,
                runtime_profile=step.runtime_profile,
                catalog=step.catalog,
                execution_jdk=step.execution_jdk,
                status=str(stage_state.get("chain_status") or step.status or "pending"),
                approval_gate_id=str(stage_state.get("approval_gate_id") or step.approval_gate_id),
                artifact_refs=tuple(str(value) for value in stage_state.get("artifact_refs", ()) if str(value).strip())
                if isinstance(stage_state.get("artifact_refs"), (list, tuple))
                else step.artifact_refs,
                evidence_refs=tuple(str(value) for value in stage_state.get("evidence_refs", ()) if str(value).strip())
                if isinstance(stage_state.get("evidence_refs"), (list, tuple))
                else step.evidence_refs,
            )
        )
    return tuple(projected)


def route_checksum(route: ProfileRoute) -> str:
    return sha256_canonical_json({
        "source_profile": route.source_profile,
        "target_profile": route.target_profile,
        "source_level": route.source_level,
        "target_level": route.target_level,
        "included_stages": list(route.included_stages),
        "excluded_stages": list(route.excluded_stages),
        "skipped_stages": list(route.skipped_stages),
        "route_steps": [route_step_to_dict(step) for step in route.route_steps],
        "valid": route.valid,
        "reason": route.reason,
    })


def route_step_to_dict(route_step: RouteStep, *, include_execution_stage: bool = False) -> dict[str, Any]:
    payload = {
        "route_step_index": route_step.route_step_index,
        "stage_index": route_step.stage_index,
        "source_profile": route_step.source_profile,
        "target_profile": route_step.target_profile,
        "runtime_profile": route_step.runtime_profile,
        "catalog": route_step.catalog,
        "execution_jdk": route_step.execution_jdk,
        "status": route_step.status,
        "approval_gate_id": route_step.approval_gate_id,
        "artifact_refs": list(route_step.artifact_refs),
        "evidence_refs": list(route_step.evidence_refs),
    }
    if include_execution_stage:
        payload["execution_stage_index"] = route_step_execution_stage_index(route_step)
    return payload


def build_skipped_stage_ledger(
    route: ProfileRoute,
    *,
    job_id: str = "",
    evidence_ref: str = "",
    evidence_checksum: str = "",
    artifact_checksum: str = "",
    created_at: str | None = None,
) -> tuple[SkippedStageLedgerEntry, ...]:
    if not route.valid:
        return ()

    checksum = route_checksum(route)
    timestamp = created_at or utc_now_text()
    entries: list[SkippedStageLedgerEntry] = []
    for stage in route.skipped_stages:
        stage_profile = str(STAGE_CONFIG.get(stage, {}).get("profile", ""))
        entries.append(
            SkippedStageLedgerEntry(
                job_id=job_id,
                source_profile=route.source_profile,
                target_profile=route.target_profile,
                skipped_stage_index=stage,
                skipped_stage_name=f"Stage {stage}",
                skipped_stage_profile=stage_profile,
                reason=(
                    "Skipped because source profile "
                    f"{route.source_profile!r} starts after stage {stage}."
                ),
                evidence_ref=evidence_ref,
                evidence_checksum=evidence_checksum,
                route_checksum=checksum,
                artifact_checksum=artifact_checksum,
                created_at=timestamp,
            )
        )
    return tuple(entries)


def route_to_dict(
    route: ProfileRoute,
    *,
    job_id: str = "",
    evidence_ref: str = "",
    evidence_checksum: str = "",
    artifact_checksum: str = "",
    created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "source_profile": route.source_profile,
        "target_profile": route.target_profile,
        "source_level": route.source_level,
        "target_level": route.target_level,
        "included_stages": list(route.included_stages),
        "excluded_stages": list(route.excluded_stages),
        "skipped_stages": list(route.skipped_stages),
        "route_steps": [route_step_to_dict(step) for step in route.route_steps],
        "valid": route.valid,
        "reason": route.reason,
        "route_checksum": route_checksum(route),
        "skipped_stage_ledger": [
            entry.to_dict()
            for entry in build_skipped_stage_ledger(
                route,
                job_id=job_id,
                evidence_ref=evidence_ref,
                evidence_checksum=evidence_checksum,
                artifact_checksum=artifact_checksum,
                created_at=created_at,
            )
        ],
    }


def _invalid_route_from_validation(validation: ProfilePairValidation) -> ProfileRoute:
    source_definition = get_migration_profile(validation.source_profile)
    target_definition = get_migration_profile(validation.target_profile)
    source_level = source_definition.order_index if source_definition is not None else -1
    target_level = target_definition.order_index if target_definition is not None else -1
    return ProfileRoute(
        source_profile=validation.source_profile,
        target_profile=validation.target_profile,
        source_level=source_level,
        target_level=target_level,
        included_stages=(),
        excluded_stages=(),
        skipped_stages=(),
        route_steps=(),
        valid=False,
        reason=validation.reason,
    )


# ── stop-condition model (AMF-251 / F1-T6) ───────────────────────

_STOP_CONDITION_EVENT_TYPES: dict[str, str] = {
    "analysis_checkpoint": "analysis_review_required",
    "planning_checkpoint": "planning_review_required",
    "risk_detected": "risk_detected",
    "build_failed": "build_failed",
    "test_failed": "test_failed",
    "target_reached": "target_reached",
    "stale_artifact": "stale_artifact",
    "reviewer_failed": "reviewer_failed",
    "approval_required": "approval_required",
    "user_stopped": "user_stopped",
    "profile_incompatible": "profile_incompatible",
    "target_overshoot_blocked": "target_overshoot_blocked",
}

_ALLOWED_ACTIONS_PER_CONDITION: dict[str, tuple[str, ...]] = {
    "analysis_checkpoint": ("continue", "request_modification", "stop", "download_artifact"),
    "planning_checkpoint": ("continue", "request_modification", "stop", "download_artifact"),
    "risk_detected": ("continue_with_risk_acknowledgment", "stop", "download_artifact"),
    "build_failed": ("stop", "download_artifact", "request_repair_review_future"),
    "test_failed": ("stop", "download_artifact", "request_repair_review_future"),
    "target_reached": ("stop", "download_artifact"),
    "stale_artifact": ("stop", "request_modification"),
    "reviewer_failed": ("stop", "download_artifact"),
    "approval_required": ("continue", "stop", "request_modification"),
    "user_stopped": ("resume", "stop"),
    "profile_incompatible": ("stop",),
    "target_overshoot_blocked": ("stop",),
}

_KNOWN_STOP_CONDITIONS: frozenset[str] = frozenset(_STOP_CONDITION_EVENT_TYPES.keys())


@dataclass(frozen=True)
class StopCondition:
    name: str
    event_type: str
    allowed_actions: tuple[str, ...]
    restorable: bool = False
    repair_eligible: bool = False


def get_stop_condition(name: str) -> StopCondition | None:
    if name not in _KNOWN_STOP_CONDITIONS:
        return None
    return StopCondition(
        name=name,
        event_type=_STOP_CONDITION_EVENT_TYPES[name],
        allowed_actions=_ALLOWED_ACTIONS_PER_CONDITION.get(name, ()),
        restorable=name in ("analysis_checkpoint", "planning_checkpoint", "user_stopped", "approval_required"),
        repair_eligible=name in ("build_failed", "test_failed"),
    )


def get_all_stop_conditions() -> tuple[StopCondition, ...]:
    return tuple(
        StopCondition(
            name=name,
            event_type=_STOP_CONDITION_EVENT_TYPES[name],
            allowed_actions=_ALLOWED_ACTIONS_PER_CONDITION.get(name, ()),
            restorable=name in ("analysis_checkpoint", "planning_checkpoint", "user_stopped", "approval_required"),
            repair_eligible=name in ("build_failed", "test_failed"),
        )
        for name in _KNOWN_STOP_CONDITIONS
    )


# ── auto-continue policy (AMF-250 / F1-T5) ───────────────────────

@dataclass(frozen=True)
class AutoContinueDecision:
    should_continue: bool
    stop_condition: str | None = None
    reason: str = ""


def evaluate_auto_continue(
    *,
    current_stage: int,
    route: ProfileRoute,
    policy: StageContinuationPolicy,
    result: dict[str, Any] | None = None,
    has_risk: bool = False,
    has_reviewer_failure: bool = False,
    has_stale_artifact: bool = False,
    has_approval_required: bool = False,
    has_user_stopped: bool = False,
    is_profile_incompatible: bool = False,
) -> AutoContinueDecision:
    from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy as SCP

    forced_stops: list[tuple[str, str]] = []
    if is_profile_incompatible:
        forced_stops.append(("profile_incompatible", "source/target profile pair is incompatible"))
    if has_user_stopped:
        forced_stops.append(("user_stopped", "user has explicitly stopped the pipeline"))
    if has_approval_required:
        forced_stops.append(("approval_required", "human approval is required before continuing"))
    if has_stale_artifact:
        forced_stops.append(("stale_artifact", "artifact evidence is stale"))
    if has_reviewer_failure:
        forced_stops.append(("reviewer_failed", "reviewer LLM failed to produce valid critique"))
    if has_risk:
        forced_stops.append(("risk_detected", "migration risk was detected"))

    if result is not None:
        build_status = str(result.get("build_status", "")).strip()
        test_status = str(result.get("test_status", "")).strip()
        if _is_failure_status(build_status):
            forced_stops.append(("build_failed", f"build failed: {build_status}"))
        if _is_failure_status(test_status):
            forced_stops.append(("test_failed", f"test failed: {test_status}"))

    if forced_stops:
        return AutoContinueDecision(
            should_continue=False,
            stop_condition=forced_stops[0][0],
            reason=forced_stops[0][1],
        )

    if is_target_reached(route, current_stage):
        return AutoContinueDecision(
            should_continue=False,
            stop_condition="target_reached",
            reason="selected target profile has been reached",
        )

    if current_stage == 1:
        return AutoContinueDecision(
            should_continue=False,
            stop_condition="analysis_checkpoint",
            reason="analysis checkpoint requires user review",
        )
    if current_stage == 2:
        return AutoContinueDecision(
            should_continue=False,
            stop_condition="planning_checkpoint",
            reason="planning checkpoint requires user review",
        )

    if policy in (SCP.MANUAL, SCP.MANUAL_ON_WARNING_OR_FAILURE):
        has_warnings = bool(result) and _result_has_warnings_static(result)
        if policy == SCP.MANUAL or has_warnings:
            return AutoContinueDecision(
                should_continue=False,
                stop_condition="approval_required",
                reason="stage_continuation_policy_manual" if policy == SCP.MANUAL else "stage_continuation_policy_warning_or_failure",
            )

    return AutoContinueDecision(should_continue=True)


def _is_failure_status(value: str) -> bool:
    text = value.upper()
    if not text:
        return False
    _TERMINAL_FAILURES = {
        "BUILD_FAILED_IN_SANDBOX",
        "TEST_FAILED",
        "TEST_FAILED_IN_SANDBOX",
        "FALLBACK_REPAIR_PLAN",
        "TRANSFORM_FAILED",
        "FAILED",
        "FAIL",
    }
    if text in _TERMINAL_FAILURES:
        return True
    if "FAILED" in text or text.endswith("_FAIL") or text == "FAIL":
        return True
    if "FALLBACK_REPAIR_PLAN" in text:
        return True
    return False


def _result_has_warnings_static(result: dict[str, Any] | None) -> bool:
    if result is None:
        return False
    test_status = str(result.get("test_status", "")).strip()
    if test_status == "PASS_WITH_WARNINGS":
        return True
    build_status = str(result.get("build_status", "")).strip()
    if build_status == "PASS_WITH_WARNINGS":
        return True
    orchestration_status = str(result.get("orchestration_status", "")).strip()
    if orchestration_status == "PASS_WITH_WARNINGS":
        return True
    explicit_warnings = result.get("warnings")
    if explicit_warnings:
        if isinstance(explicit_warnings, (list, tuple)) and len(explicit_warnings) > 0:
            return True
        if isinstance(explicit_warnings, str) and explicit_warnings.strip():
            return True
    return False


def is_terminal_stage(stage_index: int) -> bool:
    return stage_index == TERMINAL_STAGE_INDEX


@dataclass(frozen=True)
class StageContinuationResult:
    continuation_id: str
    job_id: str
    from_stage: int
    to_stage: int
    sandbox_path: str
    argv: tuple[str, ...]
    status: str  # queued, blocked, completed
    reason: str = ""
    command_id: str | None = None


class V2StageProgressionService:
    """Auto-queue the next configured migration stage from prior output."""

    def __init__(
        self,
        setup_repo: SqliteV2SetupRepository,
        command_repo: SqliteV2CommandRepository | None = None,
        artifact_revision_repo: SqliteArtifactRevisionRepository | None = None,
        run_config_repo: Any | None = None,
    ) -> None:
        self._setup_repo = setup_repo
        self._command_repo = command_repo
        self._artifact_revision_repo = artifact_revision_repo
        self._run_config_repo = run_config_repo

    def compute_route_for_job(
        self,
        job_id: str,
        run_config: Any | None = None,
    ) -> ProfileRoute:
        if run_config is None and self._run_config_repo is not None:
            run_config = self._run_config_repo.get_for_job(job_id)

        source = ""
        target = ""
        if run_config is not None:
            source = str(getattr(run_config, "source_profile", "") or "")
            target = str(getattr(run_config, "target_profile", "") or "")
            payload_json = getattr(run_config, "payload_json", "") or ""
            if payload_json:
                try:
                    payload = json.loads(payload_json)
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                if isinstance(payload, dict):
                    source = str(payload.get("source_profile") or source)
                    target = str(payload.get("target_profile") or target)
        override = self._accepted_source_profile_override(job_id)
        if override is not None:
            source = str(override.get("requested_source_profile") or source)
            target = str(override.get("target_profile") or target)
        if not source:
            source = default_source_profile_id()
        if not target:
            target = default_target_profile_id()
        return compute_profile_route(str(source), str(target))

    def _accepted_source_profile_override(self, job_id: str) -> dict[str, Any] | None:
        if self._artifact_revision_repo is None:
            return None

        revisions = self._artifact_revision_repo.list_by_job_and_stage(job_id, 1)
        accepted = [
            revision
            for revision in revisions
            if revision.revision_kind == "source_profile_override"
            and revision.revision_status == "accepted"
            and revision.superseded_by_revision_id is None
        ]
        if not accepted:
            return None

        latest = max(accepted, key=lambda revision: revision.revision_order)
        try:
            artifact = json.loads(latest.artifact_refs_json)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(artifact, dict):
            return None

        requested = str(artifact.get("requested_source_profile") or "").strip()
        target = str(artifact.get("target_profile") or "").strip()
        detected = str(artifact.get("detected_source_profile") or "").strip()
        if not requested or not target or requested == detected:
            return None
        return artifact

    def queue_next_stage(
        self,
        job_id: str,
        setup_id: str,
        current_stage: int,
        sandbox_path: str,
        stage_continuation_policy: StageContinuationPolicy | str = StageContinuationPolicy.AUTO_ON_GREEN,
        gate_id: str | None = None,
        decision_id: str | None = None,
        current_stage_result: dict[str, Any] | None = None,
        profile_route: ProfileRoute | None = None,
        current_route_step_index: int | None = None,
    ) -> StageContinuationResult:
        """Queue the next stage from the current stage sandbox.

        Args:
            job_id: The V2 job ID.
            setup_id: The setup ID to load paths from.
            current_stage: The completed stage.
            sandbox_path: The sandbox output path from the completed stage.
            stage_continuation_policy: Backend-owned policy from run configuration.
            gate_id: Optional gate ID that triggered this continuation.
            decision_id: Optional decision ID that resolved the gate.
            current_stage_result: Optional backend-owned result already validated
                by the runner for the completed stage.
            profile_route: Optional pre-computed ProfileRoute for target-stop enforcement.

        Returns:
            StageContinuationResult with the next stage details.

        Raises:
            ValueError: If the stage cannot progress (invalid stage,
                        missing setup, sandbox path issues).
        """
        route = profile_route or self.compute_route_for_job(job_id)
        if not route.valid:
            return StageContinuationResult(
                continuation_id=uuid4().hex,
                job_id=job_id,
                from_stage=current_stage,
                to_stage=current_stage,
                sandbox_path=sandbox_path,
                argv=(),
                status="blocked",
                reason="profile_incompatible",
                command_id=None,
            )

        policy = _coerce_stage_continuation_policy(stage_continuation_policy)
        if route.route_steps:
            if current_stage_result is not None and not self._result_has_successful_stage_output(
                current_stage_result,
                expected_sandbox_path=sandbox_path,
            ):
                build_status = str(current_stage_result.get("build_status", ""))
                test_status = str(current_stage_result.get("test_status", ""))
                if _is_failure_status(build_status):
                    reason = "build_failed"
                elif _is_failure_status(test_status):
                    reason = "test_failed"
                else:
                    reason = "target_reached"
                return StageContinuationResult(
                    continuation_id=uuid4().hex,
                    job_id=job_id,
                    from_stage=current_stage,
                    to_stage=current_stage,
                    sandbox_path=sandbox_path,
                    argv=(),
                    status="blocked",
                    reason=reason,
                    command_id=None,
                )

            resolved_route_step_index = _coerce_route_step_index(current_route_step_index)
            if resolved_route_step_index is not None:
                if resolved_route_step_index > len(route.route_steps):
                    raise ValueError(
                        f"Cannot progress from route step {resolved_route_step_index}: "
                        "route step is out of range for the selected route"
                    )
                next_route_step_index = resolved_route_step_index + 1
                if next_route_step_index > len(route.route_steps):
                    if current_stage_result is not None and self._result_has_successful_stage_output(
                        current_stage_result,
                        expected_sandbox_path=sandbox_path,
                    ):
                        return StageContinuationResult(
                            continuation_id=uuid4().hex,
                            job_id=job_id,
                            from_stage=current_stage,
                            to_stage=current_stage,
                            sandbox_path=sandbox_path,
                            argv=(),
                            status="completed",
                            reason="migration_completed",
                            command_id=None,
                        )
                    return StageContinuationResult(
                        continuation_id=uuid4().hex,
                        job_id=job_id,
                        from_stage=current_stage,
                        to_stage=current_stage,
                        sandbox_path=sandbox_path,
                        argv=(),
                        status="blocked",
                        reason="target_reached",
                        command_id=None,
                    )
                next_route_step = route.route_steps[next_route_step_index - 1]
                next_stage = next_route_step.stage_index
                print(
                    "[route-step-start]",
                    {
                        "job_id": job_id,
                        "route_step_number": next_route_step.route_step_index,
                        "stage_id": next_stage,
                        "source_profile": next_route_step.source_profile,
                        "target_profile": next_route_step.target_profile,
                        "runtime_profile": next_route_step.runtime_profile,
                        "catalog": next_route_step.catalog,
                    },
                    file=sys.stderr,
                    flush=True,
                )
            else:
                next_stage = next_required_stage(route, current_stage)
                if next_stage is None:
                    if current_stage_result is not None and self._result_has_successful_stage_output(
                        current_stage_result,
                        expected_sandbox_path=sandbox_path,
                    ):
                        return StageContinuationResult(
                            continuation_id=uuid4().hex,
                            job_id=job_id,
                            from_stage=current_stage,
                            to_stage=current_stage,
                            sandbox_path=sandbox_path,
                            argv=(),
                            status="completed",
                            reason="migration_completed",
                            command_id=None,
                        )
                    return StageContinuationResult(
                        continuation_id=uuid4().hex,
                        job_id=job_id,
                        from_stage=current_stage,
                        to_stage=current_stage,
                        sandbox_path=sandbox_path,
                        argv=(),
                        status="blocked",
                        reason="target_reached",
                        command_id=None,
                    )

                next_route_step = _route_step_for_stage(route, next_stage)
                if next_route_step is None:
                    raise ValueError(
                        f"Cannot progress from stage {current_stage}: "
                        f"no route step exists for stage {next_stage}"
                    )
            setup = self._setup_repo.get(setup_id)
            if setup is None:
                raise ValueError(f"Setup {setup_id!r} not found")

            validation_stage = (
                route.route_steps[resolved_route_step_index - 1].stage_index
                if resolved_route_step_index is not None
                else current_stage
            )
            if next_stage == TERMINAL_STAGE_INDEX and 3 in route.included_stages:
                self._validate_stage4_input(
                    job_id,
                    validation_stage,
                    sandbox_path=sandbox_path,
                    current_stage_result=current_stage_result,
                )

            if policy in (StageContinuationPolicy.MANUAL, StageContinuationPolicy.MANUAL_ON_WARNING_OR_FAILURE):
                reason = (
                    "stage_continuation_policy_manual"
                    if policy == StageContinuationPolicy.MANUAL
                    else "stage_continuation_policy_warning_or_failure"
                )
                return StageContinuationResult(
                    continuation_id=uuid4().hex,
                    job_id=job_id,
                    from_stage=current_stage,
                    to_stage=next_stage,
                    sandbox_path=sandbox_path,
                    argv=(),
                    status="blocked",
                    reason=reason,
                    command_id=None,
                )

            # Route-step continuation is backend-owned. The selected route step
            # provides the runtime profile, catalog, and execution JDK.
            jdk_env_var = resolve_execution_jdk_env_var_for_runtime_profile(next_route_step.runtime_profile)
            jdk_home = _get_jdk_home(setup, jdk_env_var)
            path_prepend = str(Path(jdk_home) / "bin")

            argv = (
                sys.executable,
                "-m",
                RUNNER_MODULE,
                "--run-id", f"v2-{job_id[:8]}-s{next_stage}",
                "--legacy", sandbox_path,
                "--modernized", setup.output_parent_path,
                "--ai-hub", setup.ai_hub_path,
                "--profile", next_route_step.runtime_profile,
                "--mode", "full_sandbox_migration",
            )
            print(
                "[sandbox-folder-selected]",
                {
                    "job_id": job_id,
                    "route_step_number": next_route_step.route_step_index,
                    "stage_id": next_stage,
                    "runtime_profile": next_route_step.runtime_profile,
                    "catalog": next_route_step.catalog,
                    "sandbox_folder": f"v2-{job_id[:8]}-s{next_stage}",
                },
                file=sys.stderr,
                flush=True,
            )

            existing_command_id: str | None = None
            if self._command_repo is not None:
                existing = self._command_repo.list_by_job_and_stage(job_id, next_stage)
                reusable = next(
                    (
                        record
                        for record in existing
                        if _command_matches_expected_route(
                            record,
                            expected_profile=next_route_step.runtime_profile,
                            expected_legacy_path=sandbox_path,
                        )
                    ),
                    None,
                )
                if reusable is not None:
                    existing_command_id = reusable.command_id
                    return StageContinuationResult(
                        continuation_id=uuid4().hex,
                        job_id=job_id,
                        from_stage=current_stage,
                        to_stage=next_stage,
                        sandbox_path=sandbox_path,
                        argv=argv,
                        status="queued",
                        reason="existing_next_stage_command",
                        command_id=existing_command_id,
                    )

            if self._command_repo is not None:
                command_id = uuid4().hex
                now = utc_now_text()
                env_manifest = {
                    "JAVA_HOME": jdk_home,
                    "JAVA11_HOME": setup.java11_home,
                    "JAVA17_HOME": setup.java17_home,
                    "JAVA21_HOME": setup.java21_home,
                    "MAVEN_CMD": setup.maven_cmd,
                    "PATH_PREPEND": path_prepend,
                    "ROUTE_STEP_INDEX": str(next_route_step.route_step_index),
                    "ROUTE_STEP_RUNTIME_PROFILE": next_route_step.runtime_profile,
                    "ROUTE_STEP_CATALOG": next_route_step.catalog,
                    "ROUTE_STEP_EXECUTION_JDK": next_route_step.execution_jdk,
                }
                command_record = V2StageCommandRecord(
                    command_id=command_id,
                    job_id=job_id,
                    stage_index=next_stage,
                    manifest_checksum=f"v2-stage{next_stage}",
                    argv_json=json.dumps(list(argv), separators=(",", ":")),
                    env_json=json.dumps(env_manifest, separators=(",", ":")),
                    status="manifest_ready",
                    created_at=now,
                    updated_at=now,
                    result_json=None,
                    gate_id=gate_id,
                    decision_id=decision_id,
                )
                self._command_repo.save(command_record)
                existing_command_id = command_id

            return StageContinuationResult(
                continuation_id=uuid4().hex,
                job_id=job_id,
                from_stage=current_stage,
                to_stage=next_stage,
                sandbox_path=sandbox_path,
                argv=argv,
                status="queued",
                command_id=existing_command_id,
            )

        next_stage = next_required_stage(route, current_stage) or current_stage + 1

        if next_stage not in STAGE_CONFIG:
            raise ValueError(
                f"Cannot progress from stage {current_stage}: "
                f"stage {next_stage} is not a valid target"
            )

        if is_stage_excluded_from_route(route, next_stage):
            return StageContinuationResult(
                continuation_id=uuid4().hex,
                job_id=job_id,
                from_stage=current_stage,
                to_stage=next_stage,
                sandbox_path=sandbox_path,
                argv=(),
                status="blocked",
                reason="target_reached",
                command_id=None,
            )

        if next_stage == TERMINAL_STAGE_INDEX and not (
            current_stage != 3 and 3 in route.skipped_stages
        ):
            self._validate_stage4_input(
                job_id,
                current_stage,
                sandbox_path=sandbox_path,
                current_stage_result=current_stage_result,
            )

        if policy in (StageContinuationPolicy.MANUAL, StageContinuationPolicy.MANUAL_ON_WARNING_OR_FAILURE):
            reason = (
                "stage_continuation_policy_manual"
                if policy == StageContinuationPolicy.MANUAL
                else "stage_continuation_policy_warning_or_failure"
            )
            return StageContinuationResult(
                continuation_id=uuid4().hex,
                job_id=job_id,
                from_stage=current_stage,
                to_stage=next_stage,
                sandbox_path=sandbox_path,
                argv=(),
                status="blocked",
                reason=reason,
                command_id=None,
            )

        setup = self._setup_repo.get(setup_id)
        if setup is None:
            raise ValueError(f"Setup {setup_id!r} not found")

        config = STAGE_CONFIG[next_stage]

        # Build backend-owned argv/env for next stage from the selected runtime profile.
        jdk_env_var = resolve_execution_jdk_env_var_for_runtime_profile(config["profile"])
        jdk_home = _get_jdk_home(setup, jdk_env_var)
        path_prepend = str(Path(jdk_home) / "bin")

        argv = (
            sys.executable,
            "-m",
            RUNNER_MODULE,
            "--run-id", f"v2-{job_id[:8]}-s{next_stage}",
            "--legacy", sandbox_path,
            "--modernized", setup.output_parent_path,
            "--ai-hub", setup.ai_hub_path,
            "--profile", config["profile"],
            "--mode", "full_sandbox_migration",
        )

        existing_command_id: str | None = None
        if self._command_repo is not None:
            existing = self._command_repo.list_by_job_and_stage(job_id, next_stage)
            reusable = next(
                (
                    record
                    for record in existing
                    if _command_matches_expected_route(
                        record,
                        expected_profile=config["profile"],
                        expected_legacy_path=sandbox_path,
                    )
                ),
                None,
            )
            if reusable is not None:
                existing_command_id = reusable.command_id
                return StageContinuationResult(
                    continuation_id=uuid4().hex,
                    job_id=job_id,
                    from_stage=current_stage,
                    to_stage=next_stage,
                    sandbox_path=sandbox_path,
                    argv=argv,
                    status="queued",
                    reason="existing_next_stage_command",
                    command_id=existing_command_id,
                )

        # Persist the next stage command if repo available
        if self._command_repo is not None:
            command_id = uuid4().hex
            now = utc_now_text()
            env_manifest = {
                "JAVA_HOME": jdk_home,
                "JAVA11_HOME": setup.java11_home,
                "JAVA17_HOME": setup.java17_home,
                "JAVA21_HOME": setup.java21_home,
                "MAVEN_CMD": setup.maven_cmd,
                "PATH_PREPEND": path_prepend,
            }
            command_record = V2StageCommandRecord(
                command_id=command_id,
                job_id=job_id,
                stage_index=next_stage,
                manifest_checksum=f"v2-stage{next_stage}",
                argv_json=json.dumps(list(argv), separators=(",", ":")),
                env_json=json.dumps(env_manifest, separators=(",", ":")),
                status="manifest_ready",
                created_at=now,
                updated_at=now,
                result_json=None,
                gate_id=gate_id,
                decision_id=decision_id,
            )
            self._command_repo.save(command_record)
            existing_command_id = command_id

        return StageContinuationResult(
            continuation_id=uuid4().hex,
            job_id=job_id,
            from_stage=current_stage,
            to_stage=next_stage,
            sandbox_path=sandbox_path,
            argv=argv,
            status="queued",
            command_id=existing_command_id,
        )

    # ── gate-driven queue (with gate/decision tracing) ───────────────

    def queue_next_stage_from_gate(
        self,
        job_id: str,
        setup_id: str,
        current_stage: int,
        sandbox_path: str,
        gate_id: str,
        decision_id: str,
        stage_continuation_policy: StageContinuationPolicy | str = StageContinuationPolicy.AUTO_ON_GREEN,
        current_stage_result: dict[str, Any] | None = None,
        profile_route: ProfileRoute | None = None,
        current_route_step_index: int | None = None,
    ) -> StageContinuationResult:
        """Queue next stage tracking the gate decision that triggered it.

        Like queue_next_stage but requires gate_id and decision_id so
        the resulting command can be traced back to the gate resolution.

        For AUTO_ON_GREEN (no gate), callers should use queue_next_stage
        directly without gate/decision IDs (backward compatible).
        """
        return self.queue_next_stage(
            job_id=job_id,
            setup_id=setup_id,
            current_stage=current_stage,
            sandbox_path=sandbox_path,
            stage_continuation_policy=stage_continuation_policy,
            gate_id=gate_id,
            decision_id=decision_id,
            current_stage_result=current_stage_result,
            profile_route=profile_route,
            current_route_step_index=current_route_step_index,
        )

    def validate_stage_chain(
        self,
        job_id: str,
        current_stage: int,
        target_stage: int,
    ) -> tuple[bool, str]:
        """Validate that stage progression follows the required chain.

        Two rules:
        1. target_stage must be exactly current_stage + 1 (no skipping).
        2. All stages BEFORE current_stage must have completed output
           persisted in the command repository.

        Args:
            job_id: The V2 job ID.
            current_stage: The supposedly completed stage (1 or 2).
            target_stage: The desired next stage (current_stage + 1).

        Returns:
            Tuple of (is_valid, reason).
        """
        # Rule 1: No skipping — target must be exactly next
        if target_stage != current_stage + 1:
            return (
                False,
                f"Cannot skip from stage {current_stage} to stage {target_stage}: "
                f"must progress one stage at a time",
            )

        if current_stage < 1 or current_stage > TERMINAL_STAGE_INDEX:
            return (
                False,
                f"Current stage {current_stage} is out of range (1-{TERMINAL_STAGE_INDEX})",
            )

        if target_stage not in STAGE_CONFIG and target_stage not in (2, 3, 4):
            return (
                False,
                f"Target stage {target_stage} is not a valid migration stage",
            )

        # Rule 2: All stages before current_stage must have completed output
        for stage in range(1, current_stage):
            output = self.resolve_prior_stage_output(job_id, stage)
            if output is None:
                return (
                    False,
                    f"Stage {stage} has no completed output — "
                    f"cannot progress to stage {target_stage}",
                )

        return (True, "")

    def resolve_prior_stage_output(
        self,
        job_id: str,
        current_stage: int,
    ) -> str | None:
        """Resolve the sandbox output path from the prior stage's command result.

        Looks up the latest V2StageCommandRecord for the given stage
        and extracts the sandbox_path from its persisted result_json.

        This eliminates reliance on frontend/chatbot-supplied sandbox_path
        for F15 progression — the backend resolves prior-stage output
        from persisted command/event evidence.

        Args:
            job_id: The V2 job ID.
            current_stage: The completed stage (1 or 2) whose output
                is needed as input for the next stage.

        Returns:
            The sandbox output path string, or None if it cannot be
            resolved (no commands found, no result_json, or missing
            sandbox_path in result).
        """
        if self._command_repo is None:
            return None

        commands = self._command_repo.list_by_job_and_stage(job_id, current_stage)
        if not commands:
            return None

        # Most recent command for the stage
        last = commands[0]
        if last.result_json is None:
            return None

        try:
            result = json.loads(last.result_json)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(result, dict):
            return None

        # Extract sandbox_path from result (same logic as orchestrator runner)
        sandbox_path = result.get("sandbox_path")
        if sandbox_path and isinstance(sandbox_path, str):
            return sandbox_path

        # Fallback: check artifact_refs sub-dict
        artifact_refs = result.get("artifact_refs")
        if isinstance(artifact_refs, dict):
            for key in ("sandbox", "sandbox_path", "modernized_app", "modernized_app_path"):
                val = artifact_refs.get(key)
                if val and isinstance(val, str):
                    return val

        return None

    def queue_next_stage_from_persisted(
        self,
        job_id: str,
        setup_id: str,
        current_stage: int,
        stage_continuation_policy: StageContinuationPolicy | str = StageContinuationPolicy.AUTO_ON_GREEN,
        current_stage_result: dict[str, Any] | None = None,
        profile_route: ProfileRoute | None = None,
        current_route_step_index: int | None = None,
    ) -> StageContinuationResult:
        """Queue next stage using persisted output from the prior stage.

        Resolves the sandbox_path from the prior stage's command result
        instead of requiring it as a parameter. This is the F15-safe
        entry point that does not accept frontend/chatbot-supplied paths.

        Args:
            job_id: The V2 job ID.
            setup_id: The setup ID to load paths from.
            current_stage: The completed stage.
            stage_continuation_policy: Backend-owned policy.
            profile_route: Optional pre-computed ProfileRoute for target-stop enforcement.

        Returns:
            StageContinuationResult with resolved sandbox_path, or
            status='blocked' with reason if output cannot be resolved.

        Raises:
            ValueError: If the stage cannot progress.
        """
        sandbox_path = self.resolve_prior_stage_output(job_id, current_stage)
        if sandbox_path is None:
            return StageContinuationResult(
                continuation_id=uuid4().hex,
                job_id=job_id,
                from_stage=current_stage,
                to_stage=current_stage + 1,
                sandbox_path="",
                argv=(),
                status="blocked",
                reason="prior_stage_output_not_resolved",
            )

        return self.queue_next_stage(
            job_id=job_id,
            setup_id=setup_id,
            current_stage=current_stage,
            sandbox_path=sandbox_path,
            stage_continuation_policy=stage_continuation_policy,
            current_stage_result=current_stage_result,
            profile_route=profile_route,
            current_route_step_index=current_route_step_index,
        )

    def _validate_stage4_input(
        self,
        job_id: str,
        current_stage: int,
        *,
        sandbox_path: str,
        current_stage_result: dict[str, Any] | None,
    ) -> None:
        if current_stage != 3:
            raise ValueError(
                f"Cannot progress from stage {current_stage} to stage 4: "
                "must progress from stage 3"
            )

        if self._artifact_revision_repo is not None:
            accepted = self._artifact_revision_repo.find_accepted(
                job_id, 3, "stage_output"
            )
            if accepted is not None:
                if accepted.revision_status != "accepted":
                    raise ValueError(
                        f"Stage 4 requires accepted Stage 3 output evidence, "
                        f"but found artifact revision status {accepted.revision_status!r}."
                    )
                if accepted.superseded_by_revision_id is not None:
                    raise ValueError(
                        "Stage 4 requires accepted Stage 3 output evidence "
                        "that has not been superseded."
                    )
                return

        if current_stage_result is not None and self._result_has_successful_stage_output(
            current_stage_result,
            expected_sandbox_path=sandbox_path,
        ):
            return

        if not self._has_successful_stage_output(job_id, 3):
            raise ValueError(
                "Stage 4 requires successful Stage 3 output evidence. "
                "No completed Stage 3 command output with sandbox, build, and test proof was found."
            )

    def _has_successful_stage_output(self, job_id: str, stage_index: int) -> bool:
        if self._command_repo is None:
            return False

        for command in self._command_repo.list_by_job_and_stage(job_id, stage_index):
            if command.result_json is None:
                continue
            try:
                result = json.loads(command.result_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(result, dict):
                continue
            if self._result_has_successful_stage_output(result):
                return True
        return False

    def _result_has_successful_stage_output(
        self,
        result: dict[str, Any],
        *,
        expected_sandbox_path: str | None = None,
    ) -> bool:
        result_sandbox_path = _result_sandbox_path(result)
        if not result_sandbox_path:
            return False
        if expected_sandbox_path and result_sandbox_path != expected_sandbox_path:
            return False
        if result.get("final_status") != "TRANSFORM_APPLIED_IN_SANDBOX":
            return False
        if result.get("build_status") != "BUILD_PASSED_IN_SANDBOX":
            return False
        test_status = result.get("test_status")
        return test_status in {"PASS", "TEST_PASSED", "PASS_WITH_WARNINGS"}

    def continuation_to_dict(self, result: StageContinuationResult) -> dict[str, Any]:
        """Internal diagnostic projection.

        This includes backend execution details and must not be returned from
        product API/frontend contracts.
        """
        return {
            "continuation_id": result.continuation_id,
            "job_id": result.job_id,
            "from_stage": result.from_stage,
            "to_stage": result.to_stage,
            "sandbox_path": result.sandbox_path,
            "argv": list(result.argv),
            "status": result.status,
            "reason": result.reason,
            "command_id": result.command_id,
        }

    def continuation_to_public_dict(self, result: StageContinuationResult) -> dict[str, Any]:
        """Safe product projection for stage continuation responses."""
        return {
            "continuation_id": result.continuation_id,
            "job_id": result.job_id,
            "from_stage": result.from_stage,
            "to_stage": result.to_stage,
            "status": result.status,
            "reason": result.reason,
            "command_id": result.command_id,
        }


def _get_jdk_home(setup: V2MigrationSetupRecord, env_var: str) -> str:
    mapping = {
        "JAVA11_HOME": setup.java11_home,
        "JAVA17_HOME": setup.java17_home,
        "JAVA21_HOME": setup.java21_home,
    }
    jdk_home = mapping.get(env_var, "")
    if not jdk_home:
        raise ValueError(
            f"Required JDK home {env_var!r} is missing for the selected runtime profile"
        )
    return jdk_home


def _coerce_stage_continuation_policy(
    value: StageContinuationPolicy | str,
) -> StageContinuationPolicy:
    if isinstance(value, StageContinuationPolicy):
        return value
    return StageContinuationPolicy(value)


def _coerce_route_step_index(value: int | str | None) -> int | None:
    if value is None:
        return None
    try:
        route_step_index = int(value)
    except (TypeError, ValueError):
        return None
    return route_step_index if route_step_index >= 1 else None


def _result_sandbox_path(result: dict[str, Any]) -> str | None:
    sandbox_path = result.get("sandbox_path")
    if sandbox_path and isinstance(sandbox_path, str):
        return sandbox_path

    artifact_refs = result.get("artifact_refs")
    if isinstance(artifact_refs, dict):
        for key in ("sandbox", "sandbox_path", "modernized_app", "modernized_app_path"):
            value = artifact_refs.get(key)
            if value and isinstance(value, str):
                return value

    return None
