from __future__ import annotations

import ast
import importlib
from enum import Enum
from pathlib import Path
from types import MappingProxyType

import pytest

from migration_factory.control_tower.domain.errors import (
    ControlTowerDomainError,
    InvalidJobStateTransitionError,
)
from migration_factory.control_tower.domain.states import (
    JobState,
    StageState,
    TargetProofLevel,
)
from migration_factory.control_tower.domain.transitions import (
    JOB_STATE_TRANSITIONS,
    TERMINAL_JOB_STATES,
    allowed_job_transitions_from,
    can_transition_job_state,
    is_terminal_job_state,
    validate_job_state_transition,
)


EXPECTED_JOB_STATE_VALUES = [
    "CREATED",
    "QUEUED",
    "STARTING",
    "RUNNING",
    "PAUSED_FOR_PLAN_APPROVAL",
    "PAUSED_FOR_REPAIR",
    "RESUMING",
    "CANCELLING",
    "ORPHANED",
    "RECOVERY_REQUIRED",
    "COMPLETED",
    "FAILED",
    "REJECTED",
    "CANCELLED",
]

EXPECTED_STAGE_STATE_VALUES = [
    "PENDING",
    "READY",
    "RUNNING",
    "PAUSED",
    "PASSED",
    "PASSED_WITH_WARNINGS",
    "FAILED",
    "SKIPPED_BY_POLICY",
    "BLOCKED",
    "CANCELLED",
]

EXPECTED_TARGET_PROOF_LEVEL_VALUES = [
    "ANALYZED",
    "PLANNED",
    "TRANSFORMED",
    "BUILD_TEST_VERIFIED",
    "RUNTIME_VERIFIED",
    "ENDPOINT_VERIFIED",
]

EXPECTED_TERMINAL_JOB_STATES = {
    JobState.COMPLETED,
    JobState.FAILED,
    JobState.REJECTED,
    JobState.CANCELLED,
}

EXPECTED_JOB_TRANSITIONS = {
    JobState.CREATED: {
        JobState.QUEUED,
        JobState.REJECTED,
        JobState.CANCELLED,
    },
    JobState.QUEUED: {
        JobState.STARTING,
        JobState.CANCELLING,
        JobState.FAILED,
    },
    JobState.STARTING: {
        JobState.RUNNING,
        JobState.CANCELLING,
        JobState.FAILED,
        JobState.ORPHANED,
        JobState.RECOVERY_REQUIRED,
    },
    JobState.RUNNING: {
        JobState.PAUSED_FOR_PLAN_APPROVAL,
        JobState.PAUSED_FOR_REPAIR,
        JobState.CANCELLING,
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.ORPHANED,
        JobState.RECOVERY_REQUIRED,
    },
    JobState.PAUSED_FOR_PLAN_APPROVAL: {
        JobState.RESUMING,
        JobState.REJECTED,
        JobState.CANCELLING,
        JobState.RECOVERY_REQUIRED,
    },
    JobState.PAUSED_FOR_REPAIR: {
        JobState.RESUMING,
        JobState.FAILED,
        JobState.CANCELLING,
        JobState.RECOVERY_REQUIRED,
    },
    JobState.RESUMING: {
        JobState.RUNNING,
        JobState.FAILED,
        JobState.CANCELLING,
        JobState.RECOVERY_REQUIRED,
    },
    JobState.CANCELLING: {
        JobState.CANCELLED,
        JobState.FAILED,
        JobState.RECOVERY_REQUIRED,
    },
    JobState.ORPHANED: {
        JobState.RECOVERY_REQUIRED,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.RECOVERY_REQUIRED: {
        JobState.RESUMING,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.COMPLETED: set(),
    JobState.FAILED: set(),
    JobState.REJECTED: set(),
    JobState.CANCELLED: set(),
}


def test_exact_job_state_names_and_values() -> None:
    assert [state.name for state in JobState] == EXPECTED_JOB_STATE_VALUES
    assert [state.value for state in JobState] == EXPECTED_JOB_STATE_VALUES


def test_exact_stage_state_names_and_values() -> None:
    assert [state.name for state in StageState] == EXPECTED_STAGE_STATE_VALUES
    assert [state.value for state in StageState] == EXPECTED_STAGE_STATE_VALUES


def test_exact_target_proof_level_names_and_values() -> None:
    assert [level.name for level in TargetProofLevel] == EXPECTED_TARGET_PROOF_LEVEL_VALUES
    assert [level.value for level in TargetProofLevel] == EXPECTED_TARGET_PROOF_LEVEL_VALUES


def test_production_ready_is_absent_and_not_constructible() -> None:
    assert "PRODUCTION_READY" not in TargetProofLevel.__members__

    with pytest.raises(ValueError):
        TargetProofLevel("PRODUCTION_READY")


def test_enums_are_python_310_compatible_string_enums() -> None:
    for enum_type in (JobState, StageState, TargetProofLevel):
        assert issubclass(enum_type, str)
        assert issubclass(enum_type, Enum)
        assert enum_type.__mro__[2] is Enum


def test_enum_values_behave_as_strings_where_expected() -> None:
    assert JobState.CREATED == "CREATED"
    assert StageState.PASSED_WITH_WARNINGS == "PASSED_WITH_WARNINGS"
    assert TargetProofLevel.RUNTIME_VERIFIED == "RUNTIME_VERIFIED"
    assert JobState.CREATED.value == "CREATED"


def test_every_job_state_is_represented_in_transition_table() -> None:
    assert set(JOB_STATE_TRANSITIONS) == set(JobState)
    assert set(EXPECTED_JOB_TRANSITIONS) == set(JobState)


def test_transition_table_matches_independent_expected_matrix() -> None:
    actual = {state: set(next_states) for state, next_states in JOB_STATE_TRANSITIONS.items()}

    assert actual == EXPECTED_JOB_TRANSITIONS


def test_every_approved_job_transition_succeeds() -> None:
    for current_state, expected_next_states in EXPECTED_JOB_TRANSITIONS.items():
        for next_state in expected_next_states:
            assert can_transition_job_state(current_state, next_state)
            assert validate_job_state_transition(current_state, next_state) is None


def test_every_non_approved_job_transition_is_rejected() -> None:
    for current_state in JobState:
        for next_state in JobState:
            if next_state in EXPECTED_JOB_TRANSITIONS[current_state]:
                continue

            assert not can_transition_job_state(current_state, next_state)
            with pytest.raises(InvalidJobStateTransitionError):
                validate_job_state_transition(current_state, next_state)


def test_invalid_transition_error_is_typed_and_identifies_states() -> None:
    with pytest.raises(InvalidJobStateTransitionError) as exc_info:
        validate_job_state_transition(JobState.CREATED, JobState.RUNNING)

    error = exc_info.value
    assert isinstance(error, ControlTowerDomainError)
    assert error.current_state is JobState.CREATED
    assert error.requested_state is JobState.RUNNING
    assert str(error) == "Invalid job state transition: CREATED -> RUNNING"


def test_terminal_state_helper_identifies_terminal_and_nonterminal_states() -> None:
    assert TERMINAL_JOB_STATES == EXPECTED_TERMINAL_JOB_STATES

    for state in JobState:
        assert is_terminal_job_state(state) is (state in EXPECTED_TERMINAL_JOB_STATES)


def test_every_terminal_state_has_zero_outgoing_transitions() -> None:
    for state in EXPECTED_TERMINAL_JOB_STATES:
        assert allowed_job_transitions_from(state) == frozenset()


def test_no_self_transition_succeeds() -> None:
    for state in JobState:
        assert state not in EXPECTED_JOB_TRANSITIONS[state]
        assert not can_transition_job_state(state, state)
        with pytest.raises(InvalidJobStateTransitionError):
            validate_job_state_transition(state, state)


def test_returned_transition_collections_cannot_mutate_canonical_table() -> None:
    transitions = allowed_job_transitions_from(JobState.CREATED)

    assert isinstance(JOB_STATE_TRANSITIONS, MappingProxyType)
    assert isinstance(transitions, frozenset)

    with pytest.raises(AttributeError):
        transitions.add(JobState.RUNNING)  # type: ignore[attr-defined]

    with pytest.raises(TypeError):
        JOB_STATE_TRANSITIONS[JobState.CREATED] = frozenset({JobState.RUNNING})  # type: ignore[index]

    assert JobState.RUNNING not in allowed_job_transitions_from(JobState.CREATED)


def test_domain_package_imports_without_optional_third_party_dependencies() -> None:
    domain_package = importlib.import_module("migration_factory.control_tower.domain")

    assert domain_package.JobState is JobState


def test_domain_package_uses_only_standard_library_and_domain_imports() -> None:
    domain_dir = Path("migration_factory/control_tower/domain")
    forbidden_roots = {
        "fastapi",
        "langchain",
        "langgraph",
        "sqlite3",
    }
    forbidden_prefixes = (
        "migration_factory.agents",
        "migration_factory.approval",
        "migration_factory.assessment",
        "migration_factory.config",
        "migration_factory.contracts",
        "migration_factory.copilot_assist",
        "migration_factory.copilot_repair",
        "migration_factory.dependency_policy",
        "migration_factory.final_report",
        "migration_factory.orchestrator",
        "migration_factory.repair_loop",
    )

    for path in domain_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".", maxsplit=1)[0] not in forbidden_roots
                    assert not alias.name.startswith(forbidden_prefixes)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", maxsplit=1)[0] not in forbidden_roots
                assert not node.module.startswith(forbidden_prefixes)
