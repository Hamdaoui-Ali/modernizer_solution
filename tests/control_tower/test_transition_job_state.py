from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from migration_factory.control_tower.application.commands import TransitionJobStateCommand
from migration_factory.control_tower.application.dto import MigrationJobDto
from migration_factory.control_tower.application.services import ControlTowerRegistrationService
from migration_factory.control_tower.domain.errors import (
    ConcurrencyConflictError,
    ExpectedVersionRequiredError,
    InvalidJobStateTransitionError,
    NotFoundError,
    StaleVersionError,
)
from migration_factory.control_tower.domain.states import JobState
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from tests.control_tower.transition_helpers import (
    count_audit_records,
    count_run_events,
    fetch_job,
    migrated_connection,
    seed_job,
    service,
)


def test_valid_transition_succeeds_and_returns_dto(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED)

        result = service(connection).transition_job_state(_command(JobState.QUEUED))

        assert isinstance(result, MigrationJobDto)
        assert not isinstance(result, sqlite3.Row)
        assert result.job_id == "job-1"
        assert result.status is JobState.QUEUED
        assert result.version == 2
        assert result.active_slot == 1
        assert result.last_event_sequence == 1
    finally:
        connection.close()


def test_invalid_transition_fails_without_changes(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED, version=1, last_event_sequence=3)

        with pytest.raises(InvalidJobStateTransitionError):
            service(connection).transition_job_state(_command(JobState.RUNNING))

        row = fetch_job(connection)
        assert row["status"] == "CREATED"
        assert row["version"] == 1
        assert row["active_slot"] == 1
        assert row["last_event_sequence"] == 3
        assert count_run_events(connection) == 0
        assert count_audit_records(connection) == 0
    finally:
        connection.close()


def test_missing_expected_version_fails_before_database_access() -> None:
    service_with_no_database = ControlTowerRegistrationService(_uow_factory_that_should_not_run)

    with pytest.raises(ExpectedVersionRequiredError):
        service_with_no_database.transition_job_state(
            _command(JobState.QUEUED, expected_version=None)
        )


def test_stale_expected_version_is_rejected_without_changes(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED, version=2)

        with pytest.raises(StaleVersionError):
            service(connection).transition_job_state(
                _command(JobState.QUEUED, expected_version=1)
            )

        row = fetch_job(connection)
        assert row["status"] == "CREATED"
        assert row["version"] == 2
        assert row["last_event_sequence"] == 0
        assert count_run_events(connection) == 0
        assert count_audit_records(connection) == 0
    finally:
        connection.close()


def test_missing_job_raises_not_found_without_history(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        with pytest.raises(NotFoundError):
            service(connection).transition_job_state(_command(JobState.QUEUED))

        assert count_run_events(connection) == 0
        assert count_audit_records(connection) == 0
    finally:
        connection.close()


def test_successful_transition_increments_job_version_once(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED, version=7)

        result = service(connection).transition_job_state(
            _command(JobState.QUEUED, expected_version=7)
        )

        assert result.version == 8
        assert fetch_job(connection)["version"] == 8
    finally:
        connection.close()


def test_terminal_transition_releases_active_slot(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.RUNNING, version=3)

        result = service(connection).transition_job_state(
            _command(JobState.COMPLETED, expected_version=3)
        )

        assert result.status is JobState.COMPLETED
        assert result.active_slot is None
        assert fetch_job(connection)["active_slot"] is None
    finally:
        connection.close()


def test_nonterminal_transition_keeps_active_slot(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.RUNNING)

        result = service(connection).transition_job_state(
            _command(JobState.PAUSED_FOR_PLAN_APPROVAL)
        )

        assert result.status is JobState.PAUSED_FOR_PLAN_APPROVAL
        assert result.active_slot == 1
        assert fetch_job(connection)["active_slot"] == 1
    finally:
        connection.close()


def test_transition_updates_updated_at(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.CREATED, updated_at="2026-01-01T00:00:00Z")

        result = service(connection).transition_job_state(_command(JobState.QUEUED))

        assert result.updated_at != "2026-01-01T00:00:00Z"
        assert fetch_job(connection)["updated_at"] == result.updated_at
    finally:
        connection.close()


def test_terminal_state_has_no_outgoing_transition(tmp_path: Path) -> None:
    connection = migrated_connection(tmp_path)
    try:
        seed_job(connection, status=JobState.COMPLETED, version=5)

        with pytest.raises(InvalidJobStateTransitionError):
            service(connection).transition_job_state(
                _command(JobState.RUNNING, expected_version=5)
            )

        row = fetch_job(connection)
        assert row["status"] == "COMPLETED"
        assert row["version"] == 5
        assert row["active_slot"] is None
    finally:
        connection.close()


def test_only_one_concurrent_update_with_same_expected_version_succeeds(tmp_path: Path) -> None:
    db_path = tmp_path / "control_tower.sqlite3"
    setup = connect_control_tower(db_path)
    try:
        apply_pending_migrations(setup)
        seed_job(setup, status=JobState.CREATED, version=1)
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    outcomes: list[MigrationJobDto | BaseException] = []
    lock = threading.Lock()

    def attempt_transition() -> None:
        connection = connect_control_tower(db_path)
        try:
            transition_service = service(connection)
            barrier.wait(timeout=5)
            outcome: MigrationJobDto | BaseException = transition_service.transition_job_state(
                _command(JobState.QUEUED)
            )
        except BaseException as exc:  # pragma: no cover - asserted via outcomes
            outcome = exc
        finally:
            connection.close()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=attempt_transition) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(outcomes) == 2
    successes = [outcome for outcome in outcomes if isinstance(outcome, MigrationJobDto)]
    errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], StaleVersionError | ConcurrencyConflictError)

    verification = connect_control_tower(db_path)
    try:
        row = fetch_job(verification)
        assert row["status"] == "QUEUED"
        assert row["version"] == 2
        assert row["last_event_sequence"] == 1
        assert count_run_events(verification) == 1
        assert count_audit_records(verification) == 1
    finally:
        verification.close()


def _command(
    target_state: JobState,
    *,
    expected_version: int | None = 1,
) -> TransitionJobStateCommand:
    return TransitionJobStateCommand(
        job_id="job-1",
        expected_version=expected_version,
        target_state=target_state,
        actor_type="user",
        actor_id="tester",
        reason="advance lifecycle",
        correlation_id="corr-1",
        causation_id="cause-1",
    )


def _uow_factory_that_should_not_run() -> object:
    raise AssertionError("unit of work should not be opened")
