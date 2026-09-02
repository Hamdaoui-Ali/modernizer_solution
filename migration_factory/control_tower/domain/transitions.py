"""Job lifecycle transition rules for the Control Tower domain."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from migration_factory.control_tower.domain.errors import InvalidJobStateTransitionError
from migration_factory.control_tower.domain.states import JobState

TERMINAL_JOB_STATES = frozenset(
    {
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.REJECTED,
        JobState.CANCELLED,
    }
)

JOB_STATE_TRANSITIONS: Mapping[JobState, frozenset[JobState]] = MappingProxyType(
    {
        JobState.CREATED: frozenset(
            {
                JobState.QUEUED,
                JobState.REJECTED,
                JobState.CANCELLED,
            }
        ),
        JobState.QUEUED: frozenset(
            {
                JobState.STARTING,
                JobState.CANCELLING,
                JobState.FAILED,
            }
        ),
        JobState.STARTING: frozenset(
            {
                JobState.RUNNING,
                JobState.CANCELLING,
                JobState.FAILED,
                JobState.ORPHANED,
                JobState.RECOVERY_REQUIRED,
            }
        ),
        JobState.RUNNING: frozenset(
            {
                JobState.PAUSED_FOR_PLAN_APPROVAL,
                JobState.PAUSED_FOR_REPAIR,
                JobState.CANCELLING,
                JobState.COMPLETED,
                JobState.FAILED,
                JobState.ORPHANED,
                JobState.RECOVERY_REQUIRED,
            }
        ),
        JobState.PAUSED_FOR_PLAN_APPROVAL: frozenset(
            {
                JobState.RESUMING,
                JobState.REJECTED,
                JobState.CANCELLING,
                JobState.RECOVERY_REQUIRED,
            }
        ),
        JobState.PAUSED_FOR_REPAIR: frozenset(
            {
                JobState.RESUMING,
                JobState.FAILED,
                JobState.CANCELLING,
                JobState.RECOVERY_REQUIRED,
            }
        ),
        JobState.RESUMING: frozenset(
            {
                JobState.RUNNING,
                JobState.FAILED,
                JobState.CANCELLING,
                JobState.RECOVERY_REQUIRED,
            }
        ),
        JobState.CANCELLING: frozenset(
            {
                JobState.CANCELLED,
                JobState.FAILED,
                JobState.RECOVERY_REQUIRED,
            }
        ),
        JobState.ORPHANED: frozenset(
            {
                JobState.RECOVERY_REQUIRED,
                JobState.FAILED,
                JobState.CANCELLED,
            }
        ),
        JobState.RECOVERY_REQUIRED: frozenset(
            {
                JobState.RESUMING,
                JobState.FAILED,
                JobState.CANCELLED,
            }
        ),
        JobState.COMPLETED: frozenset(),
        JobState.FAILED: frozenset(),
        JobState.REJECTED: frozenset(),
        JobState.CANCELLED: frozenset(),
    }
)


def is_terminal_job_state(state: JobState) -> bool:
    return state in TERMINAL_JOB_STATES


def active_slot_for(state: JobState) -> int | None:
    return None if is_terminal_job_state(state) else 1


def allowed_job_transitions_from(state: JobState) -> frozenset[JobState]:
    return JOB_STATE_TRANSITIONS[state]


def can_transition_job_state(
    current_state: JobState,
    next_state: JobState,
) -> bool:
    return next_state in allowed_job_transitions_from(current_state)


def validate_job_state_transition(
    current_state: JobState,
    next_state: JobState,
) -> None:
    if not can_transition_job_state(current_state, next_state):
        raise InvalidJobStateTransitionError(current_state, next_state)
