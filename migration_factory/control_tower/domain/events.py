"""V1 event type registry for the AI Migration Control Tower domain.

This module defines the canonical event type constants used across all V1
event records (RunEventRecord, StageChainEventRecord, v1_route_validation_events,
v1_stage_chain_events). Every event_type value stored in V1 append-only event
tables must be one of these constants.

Usage:
    from migration_factory.control_tower.domain.events import V1EventType
    event_type = V1EventType.JOB_CREATED
    event_type.value  # "job_created"
"""

from __future__ import annotations

from enum import Enum


class V1EventType(str, Enum):
    """Canonical registry of V1 event types.

    Each member pairs a Python-safe name with the exact event_type string
    persisted in Control Tower event records and append-only event tables.

    Invariants:
    * All V1 RunEventRecord.event_type values come from this registry.
    * All V1 RouteChainEventRecord.event_type values come from this registry.
    * All v1_route_validation_events.event_type values come from this registry.
    * All v1_stage_chain_events.event_type values come from this registry.
    * Boot 4 is NOT selectable and 3.5.14 is NOT execution-relevant for V1.
    * LLM never executes, approves, or writes files directly.
    * Browser payloads never choose raw paths, Maven goals, shell commands,
      working directories, or model deployment IDs.
    """

    # ── Migration job lifecycle events ──────────────────────────────
    JOB_CREATED = "job_created"
    ARTIFACT_REGISTERED = "artifact_registered"

    # ── Command lifecycle events ────────────────────────────────────
    COMMAND_QUEUED = "command_queued"
    COMMAND_STARTING = "command_starting"
    COMMAND_RUNNING = "command_running"
    COMMAND_FINALIZED = "command_finalized"

    # ── V1 locked route validation events ──────────────────────────
    PIPELINE_VALIDATION = "pipeline_validation"
    RUNNER_VALIDATION = "runner_validation"

    # ── V1 stage chain lifecycle events ────────────────────────────
    CHAIN_CREATED = "chain_created"
    CHAIN_STARTED = "chain_started"
    CHAIN_COMPLETED = "chain_completed"
    CHAIN_FAILED = "chain_failed"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    OUTPUT_REGISTERED = "output_registered"

    # ── Model invocation audit events ────────────────────────────
    MODEL_INVOCATION_RECORDED = "model_invocation_recorded"


# ── Convenience sets ────────────────────────────────────────────────
"""Event types that start a V1 job lifecycle."""
JOB_LIFECYCLE_EVENTS = frozenset(
    {
        V1EventType.JOB_CREATED,
        V1EventType.ARTIFACT_REGISTERED,
    }
)

"""Event types that describe command execution lifecycle."""
COMMAND_LIFECYCLE_EVENTS = frozenset(
    {
        V1EventType.COMMAND_QUEUED,
        V1EventType.COMMAND_STARTING,
        V1EventType.COMMAND_RUNNING,
        V1EventType.COMMAND_FINALIZED,
    }
)

"""Event types that describe V1 locked route validation."""
ROUTE_VALIDATION_EVENTS = frozenset(
    {
        V1EventType.PIPELINE_VALIDATION,
        V1EventType.RUNNER_VALIDATION,
    }
)

"""Event types that describe V1 stage chain lifecycle."""
STAGE_CHAIN_LIFECYCLE_EVENTS = frozenset(
    {
        V1EventType.CHAIN_CREATED,
        V1EventType.CHAIN_STARTED,
        V1EventType.CHAIN_COMPLETED,
        V1EventType.CHAIN_FAILED,
        V1EventType.STAGE_STARTED,
        V1EventType.STAGE_COMPLETED,
        V1EventType.STAGE_FAILED,
        V1EventType.OUTPUT_REGISTERED,
    }
)

"""All canonical V1 event types."""
ALL_V1_EVENT_TYPES = frozenset(V1EventType)
