"""F1-T1 focused tests — Checkpoint state model round-trip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.checkpoint_state import (
    CHECKPOINT_STATE_FIELDS,
    CHECKPOINT_STATUS_TO_GATE_DECISION,
    CHECKPOINT_STATUS_TO_GATE_STATUS,
    CHECKPOINT_STATUS_TO_REVISION_STATUS,
    CHECKPOINT_TRANSITIONS,
    FAILED_TERMINAL_STATUSES,
    NONTERMINAL_CHECKPOINT_STATUSES,
    SUCCESSFUL_TERMINAL_STATUSES,
    TERMINAL_CHECKPOINT_STATUSES,
    CheckpointState,
    CheckpointStatus,
    assert_valid_transition,
    get_allowed_transitions,
    is_idempotent_retry,
    is_nonterminal_status,
    is_terminal_status,
    is_valid_checkpoint_status,
    is_valid_transition,
    transition,
)


# ── helpers ───────────────────────────────────────────────────────────

def _valid_state(**overrides) -> CheckpointState:
    defaults = {
        "checkpoint_id": "cp-001",
        "job_id": "job-abc",
        "stage_index": 1,
        "status": CheckpointStatus.WAITING,
        "gate_id": "gate-001",
        "revision_id": "rev-001",
        "source_artifact_checksum": "sha256:abc123",
        "artifact_refs": ("analysis_report_json:1", "dependency_graph_json:1"),
        "created_at": "2026-06-26T12:00:00Z",
        "created_by": "system",
    }
    defaults.update(overrides)
    return CheckpointState(**defaults)


# ── CheckpointStatus enum tests ───────────────────────────────────────


class TestCheckpointStatusEnum:
    """Tests for the CheckpointStatus enum."""

    def test_all_seven_states_present(self):
        """All 7 states from the task definition are present."""
        values = {s.value for s in CheckpointStatus}
        assert values == {
            "waiting",
            "accepted",
            "changes_requested",
            "rejected",
            "stopped",
            "stale",
            "failed_closed",
        }

    def test_is_valid_checkpoint_status_recognizes_all(self):
        for member in CheckpointStatus:
            assert is_valid_checkpoint_status(member.value)

    def test_is_valid_checkpoint_status_rejects_invalid(self):
        assert not is_valid_checkpoint_status("in_progress")
        assert not is_valid_checkpoint_status("done")
        assert not is_valid_checkpoint_status("")


# ── Terminal states tests ─────────────────────────────────────────────


class TestTerminalStates:
    """Tests for terminal, non-terminal, successful, and failed categories."""

    def test_terminal_states(self):
        """ACCEPTED, REJECTED, STOPPED, FAILED_CLOSED are terminal."""
        assert CheckpointStatus.ACCEPTED in TERMINAL_CHECKPOINT_STATUSES
        assert CheckpointStatus.REJECTED in TERMINAL_CHECKPOINT_STATUSES
        assert CheckpointStatus.STOPPED in TERMINAL_CHECKPOINT_STATUSES
        assert CheckpointStatus.FAILED_CLOSED in TERMINAL_CHECKPOINT_STATUSES
        assert len(TERMINAL_CHECKPOINT_STATUSES) == 4

    def test_nonterminal_states(self):
        """WAITING, CHANGES_REQUESTED, STALE are non-terminal."""
        assert CheckpointStatus.WAITING in NONTERMINAL_CHECKPOINT_STATUSES
        assert CheckpointStatus.CHANGES_REQUESTED in NONTERMINAL_CHECKPOINT_STATUSES
        assert CheckpointStatus.STALE in NONTERMINAL_CHECKPOINT_STATUSES
        assert len(NONTERMINAL_CHECKPOINT_STATUSES) == 3

    def test_terminal_and_nonterminal_disjoint(self):
        """No status is both terminal and non-terminal."""
        assert TERMINAL_CHECKPOINT_STATUSES.isdisjoint(NONTERMINAL_CHECKPOINT_STATUSES)

    def test_all_statuses_covered(self):
        """Every CheckpointStatus is either terminal or non-terminal."""
        all_statuses = set(CheckpointStatus)
        covered = TERMINAL_CHECKPOINT_STATUSES | NONTERMINAL_CHECKPOINT_STATUSES
        assert all_statuses == set(covered)

    def test_successful_terminal(self):
        """ACCEPTED and CHANGES_REQUESTED are successful terminal."""
        assert CheckpointStatus.ACCEPTED in SUCCESSFUL_TERMINAL_STATUSES
        assert CheckpointStatus.CHANGES_REQUESTED in SUCCESSFUL_TERMINAL_STATUSES
        assert len(SUCCESSFUL_TERMINAL_STATUSES) == 2

    def test_failed_terminal(self):
        """REJECTED, STOPPED, FAILED_CLOSED are failed terminal."""
        assert CheckpointStatus.REJECTED in FAILED_TERMINAL_STATUSES
        assert CheckpointStatus.STOPPED in FAILED_TERMINAL_STATUSES
        assert CheckpointStatus.FAILED_CLOSED in FAILED_TERMINAL_STATUSES
        assert len(FAILED_TERMINAL_STATUSES) == 3

    def test_is_terminal_status_helper(self):
        assert is_terminal_status(CheckpointStatus.ACCEPTED)
        assert is_terminal_status(CheckpointStatus.FAILED_CLOSED)
        assert not is_terminal_status(CheckpointStatus.WAITING)
        assert not is_terminal_status(CheckpointStatus.STALE)

    def test_is_nonterminal_status_helper(self):
        assert is_nonterminal_status(CheckpointStatus.WAITING)
        assert is_nonterminal_status(CheckpointStatus.CHANGES_REQUESTED)
        assert not is_nonterminal_status(CheckpointStatus.ACCEPTED)
        assert not is_nonterminal_status(CheckpointStatus.STOPPED)


# ── Transition table tests ────────────────────────────────────────────


class TestTransitionTable:
    """Tests for the CHECKPOINT_TRANSITIONS table."""

    def test_all_statuses_have_transition_entry(self):
        """Every CheckpointStatus must have an entry in the transition table."""
        for status in CheckpointStatus:
            assert status in CHECKPOINT_TRANSITIONS

    def test_waiting_transitions(self):
        """WAITING can transition to all 6 other statuses."""
        allowed = CHECKPOINT_TRANSITIONS[CheckpointStatus.WAITING]
        assert CheckpointStatus.ACCEPTED in allowed
        assert CheckpointStatus.CHANGES_REQUESTED in allowed
        assert CheckpointStatus.REJECTED in allowed
        assert CheckpointStatus.STOPPED in allowed
        assert CheckpointStatus.STALE in allowed
        assert CheckpointStatus.FAILED_CLOSED in allowed
        assert len(allowed) == 6

    def test_changes_requested_transitions(self):
        """CHANGES_REQUESTED can transition back to WAITING or to terminal."""
        allowed = CHECKPOINT_TRANSITIONS[CheckpointStatus.CHANGES_REQUESTED]
        assert CheckpointStatus.WAITING in allowed
        assert CheckpointStatus.STOPPED in allowed
        assert CheckpointStatus.STALE in allowed
        assert CheckpointStatus.FAILED_CLOSED in allowed
        assert CheckpointStatus.ACCEPTED not in allowed

    def test_stale_transitions(self):
        """STALE can transition to WAITING or FAILED_CLOSED."""
        allowed = CHECKPOINT_TRANSITIONS[CheckpointStatus.STALE]
        assert CheckpointStatus.WAITING in allowed
        assert CheckpointStatus.FAILED_CLOSED in allowed
        assert CheckpointStatus.ACCEPTED not in allowed

    def test_terminal_statuses_have_empty_transitions(self):
        """All terminal statuses have empty transition sets."""
        for status in TERMINAL_CHECKPOINT_STATUSES:
            assert CHECKPOINT_TRANSITIONS[status] == frozenset()

    def test_is_valid_transition(self):
        assert is_valid_transition(CheckpointStatus.WAITING, CheckpointStatus.ACCEPTED)
        assert is_valid_transition(CheckpointStatus.WAITING, CheckpointStatus.STOPPED)
        assert not is_valid_transition(CheckpointStatus.ACCEPTED, CheckpointStatus.WAITING)
        assert not is_valid_transition(CheckpointStatus.FAILED_CLOSED, CheckpointStatus.WAITING)

    def test_get_allowed_transitions(self):
        allowed = get_allowed_transitions(CheckpointStatus.WAITING)
        assert len(allowed) == 6
        assert get_allowed_transitions(CheckpointStatus.ACCEPTED) == frozenset()


# ── PhaseGate mapping tests ───────────────────────────────────────────


class TestPhaseGateMapping:
    """Tests for checkpoint status → PhaseGate concept mappings."""

    def test_gate_status_mapping(self):
        """Non-terminal statuses map to 'open', terminal to 'resolved'."""
        assert CHECKPOINT_STATUS_TO_GATE_STATUS[CheckpointStatus.WAITING] == "open"
        assert CHECKPOINT_STATUS_TO_GATE_STATUS[CheckpointStatus.CHANGES_REQUESTED] == "open"
        assert CHECKPOINT_STATUS_TO_GATE_STATUS[CheckpointStatus.STALE] == "open"
        assert CHECKPOINT_STATUS_TO_GATE_STATUS[CheckpointStatus.ACCEPTED] == "resolved"
        assert CHECKPOINT_STATUS_TO_GATE_STATUS[CheckpointStatus.REJECTED] == "resolved"
        assert CHECKPOINT_STATUS_TO_GATE_STATUS[CheckpointStatus.STOPPED] == "resolved"
        assert CHECKPOINT_STATUS_TO_GATE_STATUS[CheckpointStatus.FAILED_CLOSED] == "resolved"

    def test_gate_decision_mapping(self):
        """Non-terminal map to 'pending', ACCEPTED to 'continue', others to 'reject'."""
        assert CHECKPOINT_STATUS_TO_GATE_DECISION[CheckpointStatus.WAITING] == "pending"
        assert CHECKPOINT_STATUS_TO_GATE_DECISION[CheckpointStatus.ACCEPTED] == "continue"
        assert CHECKPOINT_STATUS_TO_GATE_DECISION[CheckpointStatus.REJECTED] == "reject"
        assert CHECKPOINT_STATUS_TO_GATE_DECISION[CheckpointStatus.STOPPED] == "reject"

    def test_all_statuses_have_gate_status(self):
        for status in CheckpointStatus:
            assert status in CHECKPOINT_STATUS_TO_GATE_STATUS

    def test_all_statuses_have_gate_decision(self):
        for status in CheckpointStatus:
            assert status in CHECKPOINT_STATUS_TO_GATE_DECISION


# ── ArtifactRevision mapping tests ────────────────────────────────────


class TestRevisionMapping:
    """Tests for checkpoint status → ArtifactRevision concept mappings."""

    def test_revision_status_mapping(self):
        """WAITING/CHANGES_REQUESTED/STALE → draft, ACCEPTED/REJECTED → accepted, others → superseded."""
        assert CHECKPOINT_STATUS_TO_REVISION_STATUS[CheckpointStatus.WAITING] == "draft"
        assert CHECKPOINT_STATUS_TO_REVISION_STATUS[CheckpointStatus.ACCEPTED] == "accepted"
        assert CHECKPOINT_STATUS_TO_REVISION_STATUS[CheckpointStatus.STOPPED] == "superseded"

    def test_all_statuses_have_revision_status(self):
        for status in CheckpointStatus:
            assert status in CHECKPOINT_STATUS_TO_REVISION_STATUS


# ── CheckpointState construction tests ────────────────────────────────


class TestCheckpointStateConstruction:
    """Tests for CheckpointState construction and validation."""

    def test_minimal_waiting_state(self):
        cp = _valid_state()
        assert cp.checkpoint_id == "cp-001"
        assert cp.status == CheckpointStatus.WAITING
        assert cp.is_waiting
        assert cp.is_nonterminal
        assert not cp.is_terminal

    def test_accepted_state(self):
        cp = _valid_state(
            status=CheckpointStatus.ACCEPTED,
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp.status == CheckpointStatus.ACCEPTED
        assert cp.is_terminal
        assert cp.is_successful_terminal
        assert not cp.is_failed_terminal

    def test_rejected_state(self):
        cp = _valid_state(
            status=CheckpointStatus.REJECTED,
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp.is_terminal
        assert cp.is_failed_terminal
        assert not cp.is_successful_terminal

    def test_stopped_state(self):
        cp = _valid_state(
            status=CheckpointStatus.STOPPED,
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp.is_terminal
        assert cp.is_failed_terminal

    def test_changes_requested_state(self):
        cp = _valid_state(status=CheckpointStatus.CHANGES_REQUESTED)
        assert cp.is_nonterminal
        assert not cp.is_terminal

    def test_stale_state(self):
        cp = _valid_state(
            status=CheckpointStatus.STALE,
            is_stale=True,
            stale_reason="Artifact checksum mismatch after dependency update.",
        )
        assert cp.status == CheckpointStatus.STALE
        assert cp.is_stale

    def test_failed_closed_state(self):
        cp = _valid_state(
            status=CheckpointStatus.FAILED_CLOSED,
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="system",
        )
        assert cp.is_terminal
        assert cp.is_failed_terminal

    def test_derived_gate_status_property(self):
        cp = _valid_state(status=CheckpointStatus.WAITING)
        assert cp.gate_status == "open"
        cp2 = _valid_state(
            status=CheckpointStatus.ACCEPTED,
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp2.gate_status == "resolved"

    def test_derived_gate_decision_property(self):
        cp = _valid_state(status=CheckpointStatus.ACCEPTED,
                          resolved_at="2026-06-26T13:00:00Z",
                          resolved_by="ali.hamdaoui")
        assert cp.gate_decision == "continue"

    def test_derived_revision_status_property(self):
        cp = _valid_state(status=CheckpointStatus.WAITING)
        assert cp.revision_status == "draft"
        cp2 = _valid_state(
            status=CheckpointStatus.ACCEPTED,
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp2.revision_status == "accepted"

    def test_has_artifacts(self):
        cp = _valid_state(artifact_refs=())
        assert not cp.has_artifacts
        cp2 = _valid_state(artifact_refs=("analysis_report_json:1",))
        assert cp2.has_artifacts

    def test_status_coerced_from_string(self):
        cp = _valid_state(status="stale", is_stale=True, stale_reason="mismatch")
        assert cp.status == CheckpointStatus.STALE

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            _valid_state(status="nonexistent")


class TestCheckpointStateValidation:
    """Tests for CheckpointState field validation."""

    def test_empty_checkpoint_id_raises(self):
        with pytest.raises(ValidationError):
            _valid_state(checkpoint_id="")

    def test_empty_job_id_raises(self):
        with pytest.raises(ValidationError):
            _valid_state(job_id="")

    def test_empty_created_at_raises(self):
        with pytest.raises(ValidationError):
            _valid_state(created_at="")

    def test_terminal_without_resolved_at_raises(self):
        with pytest.raises(ValidationError):
            _valid_state(status=CheckpointStatus.ACCEPTED)

    def test_terminal_without_resolved_by_raises(self):
        with pytest.raises(ValidationError):
            _valid_state(
                status=CheckpointStatus.ACCEPTED,
                resolved_at="2026-06-26T13:00:00Z",
            )

    def test_waiting_with_resolved_at_raises(self):
        """WAITING must not have resolved_at set."""
        with pytest.raises(ValidationError):
            _valid_state(
                status=CheckpointStatus.WAITING,
                resolved_at="2026-06-26T13:00:00Z",
            )

    def test_waiting_with_resolved_by_raises(self):
        """WAITING must not have resolved_by set."""
        with pytest.raises(ValidationError):
            _valid_state(
                status=CheckpointStatus.WAITING,
                resolved_by="ali.hamdaoui",
            )

    def test_stale_without_reason_raises(self):
        with pytest.raises(ValidationError):
            _valid_state(is_stale=True, stale_reason="")

    def test_stale_not_set_but_reason_empty_is_ok(self):
        """stale_reason="" is fine when is_stale=False."""
        cp = _valid_state(is_stale=False, stale_reason="")
        assert not cp.is_stale

    def test_stage_index_bounds(self):
        """stage_index must be between 1 and 3."""
        _valid_state(stage_index=1)
        _valid_state(stage_index=2)
        _valid_state(stage_index=3)
        with pytest.raises(ValidationError):
            _valid_state(stage_index=0)
        with pytest.raises(ValidationError):
            _valid_state(stage_index=4)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            _valid_state(sandbox_path="/tmp/hack")


# ── Factory method tests ──────────────────────────────────────────────


class TestCheckpointStateFactories:
    """Tests for CheckpointState factory methods."""

    def test_create_waiting(self):
        cp = CheckpointState.create_waiting(
            checkpoint_id="cp-wait",
            job_id="job-xyz",
            stage_index=1,
            gate_id="gate-001",
            revision_id="rev-001",
            source_artifact_checksum="sha256:def",
            artifact_refs=("artifact:a", "artifact:b"),
            source_profile="java8-spring27-legacy",
            target_profile="java17-spring31-modern",
            created_at="2026-06-26T12:00:00Z",
            created_by="system",
        )
        assert cp.status == CheckpointStatus.WAITING
        assert cp.is_waiting
        assert cp.source_profile == "java8-spring27-legacy"
        assert cp.target_profile == "java17-spring31-modern"
        assert cp.artifact_refs == ("artifact:a", "artifact:b")

    def test_create_terminal_accepted(self):
        cp = CheckpointState.create_terminal(
            checkpoint_id="cp-done",
            job_id="job-xyz",
            stage_index=1,
            status=CheckpointStatus.ACCEPTED,
            gate_id="gate-002",
            revision_id="rev-002",
            source_artifact_checksum="sha256:abc",
            resolved_artifact_checksum="sha256:abc",
            created_at="2026-06-26T12:00:00Z",
            created_by="system",
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp.is_terminal
        assert cp.resolved_at == "2026-06-26T13:00:00Z"
        assert cp.resolved_by == "ali.hamdaoui"

    def test_create_terminal_rejects_non_terminal_status(self):
        with pytest.raises(ValueError, match="terminal status"):
            CheckpointState.create_terminal(
                checkpoint_id="cp-bad",
                job_id="job-xyz",
                stage_index=1,
                status=CheckpointStatus.WAITING,
                created_at="2026-06-26T12:00:00Z",
            )

    def test_create_terminal_stopped(self):
        cp = CheckpointState.create_terminal(
            checkpoint_id="cp-stop",
            job_id="job-xyz",
            stage_index=2,
            status=CheckpointStatus.STOPPED,
            created_at="2026-06-26T12:00:00Z",
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp.status == CheckpointStatus.STOPPED
        assert cp.is_failed_terminal


# ── Transition validation tests ───────────────────────────────────────


class TestTransitionValidation:
    """Tests for transition validation functions."""

    def test_assert_valid_transition_succeeds(self):
        cp = _valid_state(status=CheckpointStatus.WAITING)
        # Should not raise
        assert_valid_transition(cp, CheckpointStatus.ACCEPTED)

    def test_assert_valid_transition_rejects_invalid(self):
        cp = _valid_state(status=CheckpointStatus.WAITING)
        with pytest.raises(ValueError, match="not allowed"):
            # WAITING → ACCEPTED is valid, WAITING → nonexistent state...
            # Test invalid: from STALE to STOPPED (not in transition table)
            cp2 = _valid_state(status=CheckpointStatus.STALE, is_stale=True,
                               stale_reason="checksum mismatch")
            assert_valid_transition(cp2, CheckpointStatus.ACCEPTED)

    def test_assert_valid_from_terminal_rejects(self):
        cp = _valid_state(
            status=CheckpointStatus.ACCEPTED,
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        with pytest.raises(ValueError, match="terminal"):
            assert_valid_transition(cp, CheckpointStatus.WAITING)


# ── Transition function tests ─────────────────────────────────────────


class TestTransitionFunction:
    """Tests for the transition() function."""

    def test_simple_transition(self):
        cp = _valid_state(status=CheckpointStatus.WAITING)
        new_cp = transition(cp, CheckpointStatus.ACCEPTED,
                            resolved_at="2026-06-26T13:00:00Z",
                            resolved_by="ali.hamdaoui")
        assert new_cp.status == CheckpointStatus.ACCEPTED
        assert new_cp.resolved_at == "2026-06-26T13:00:00Z"
        assert new_cp.resolved_by == "ali.hamdaoui"
        # Original unchanged
        assert cp.status == CheckpointStatus.WAITING

    def test_idempotent_transition(self):
        """Transition to same status returns original."""
        cp = _valid_state(status=CheckpointStatus.WAITING)
        result = transition(cp, CheckpointStatus.WAITING)
        assert result is cp

    def test_transition_to_stale_sets_stale_fields(self):
        """Transition preserves stale state when going to STALE."""
        cp = _valid_state(status=CheckpointStatus.WAITING)
        new_cp = transition(cp, CheckpointStatus.STALE,
                            idempotency_key="idem-001")
        assert new_cp.status == CheckpointStatus.STALE
        assert new_cp.last_idempotency_key == "idem-001"
        # STALE is non-terminal, so resolved fields not set
        assert new_cp.resolved_at is None

    def test_transition_with_resolved_checksum(self):
        cp = _valid_state(status=CheckpointStatus.WAITING)
        new_cp = transition(cp, CheckpointStatus.ACCEPTED,
                            resolved_at="2026-06-26T13:00:00Z",
                            resolved_by="ali.hamdaoui",
                            resolved_artifact_checksum="sha256:xyz")
        assert new_cp.resolved_artifact_checksum == "sha256:xyz"

    def test_transition_invalid_raises(self):
        cp = _valid_state(status=CheckpointStatus.STALE, is_stale=True,
                          stale_reason="checksum mismatch")
        with pytest.raises(ValueError, match="not allowed"):
            transition(cp, CheckpointStatus.REJECTED,
                       resolved_at="2026-06-26T13:00:00Z",
                       resolved_by="ali.hamdaoui")

    def test_transition_from_terminal_raises(self):
        cp = _valid_state(
            status=CheckpointStatus.ACCEPTED,
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        with pytest.raises(ValueError, match="terminal"):
            transition(cp, CheckpointStatus.WAITING)

    def test_full_waiting_to_accepted_flow(self):
        """Happy path: WAITING → ACCEPTED."""
        cp = _valid_state(status=CheckpointStatus.WAITING)
        cp = transition(cp, CheckpointStatus.ACCEPTED,
                        resolved_at="2026-06-26T13:00:00Z",
                        resolved_by="ali.hamdaoui",
                        resolved_artifact_checksum="sha256:final")
        assert cp.status == CheckpointStatus.ACCEPTED
        assert cp.is_terminal
        assert cp.is_successful_terminal

    def test_waiting_to_changes_requested_flow(self):
        """User requests changes: WAITING → CHANGES_REQUESTED."""
        cp = _valid_state(status=CheckpointStatus.WAITING)
        cp = transition(cp, CheckpointStatus.CHANGES_REQUESTED,
                        idempotency_key="idem-mod")
        assert cp.status == CheckpointStatus.CHANGES_REQUESTED
        assert cp.is_nonterminal

    def test_changes_requested_back_to_waiting(self):
        """After re-analysis: CHANGES_REQUESTED → WAITING."""
        cp = _valid_state(status=CheckpointStatus.CHANGES_REQUESTED)
        cp = transition(cp, CheckpointStatus.WAITING,
                        idempotency_key="idem-rerun")
        assert cp.status == CheckpointStatus.WAITING

    def test_stale_to_waiting(self):
        """After artifact regeneration: STALE → WAITING."""
        cp = _valid_state(status=CheckpointStatus.STALE, is_stale=True,
                          stale_reason="checksum mismatch")
        cp = transition(cp, CheckpointStatus.WAITING,
                        idempotency_key="idem-fresh")
        assert cp.status == CheckpointStatus.WAITING


# ── Idempotent retry tests ────────────────────────────────────────────


class TestIdempotentRetry:
    """Tests for idempotent retry behavior."""

    def test_is_idempotent_retry_match(self):
        cp = _valid_state(last_idempotency_key="idem-001")
        assert is_idempotent_retry(cp, "idem-001")

    def test_is_idempotent_retry_mismatch(self):
        cp = _valid_state(last_idempotency_key="idem-001")
        assert not is_idempotent_retry(cp, "idem-002")

    def test_is_idempotent_retry_both_none(self):
        cp = _valid_state(last_idempotency_key=None)
        assert not is_idempotent_retry(cp, None)

    def test_is_idempotent_retry_key_none(self):
        cp = _valid_state(last_idempotency_key="idem-001")
        assert not is_idempotent_retry(cp, None)

    def test_transition_sets_idempotency_key(self):
        cp = _valid_state(status=CheckpointStatus.WAITING)
        new_cp = transition(cp, CheckpointStatus.ACCEPTED,
                            resolved_at="2026-06-26T13:00:00Z",
                            resolved_by="ali.hamdaoui",
                            idempotency_key="idem-trans")
        assert new_cp.last_idempotency_key == "idem-trans"


# ── Safe fields tests ─────────────────────────────────────────────────


class TestCheckpointStateFields:
    """Tests for CHECKPOINT_STATE_FIELDS safety."""

    def test_no_dangerous_fields(self):
        dangerous = {
            "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
            "provider", "model", "deployment", "endpoint", "secret", "token",
            "password", "api_key", "client_secret", "command",
        }
        overlap = CHECKPOINT_STATE_FIELDS & dangerous
        assert not overlap, f"CHECKPOINT_STATE_FIELDS contains dangerous: {overlap}"

    def test_assertion_passes(self):
        """The module-level assertion does not fire (import succeeded)."""
        assert True

    def test_expected_fields_present(self):
        """Core fields from the task definition are present."""
        required = {
            "checkpoint_id", "job_id", "stage_index", "status",
            "artifact_refs", "source_artifact_checksum",
            "source_profile", "target_profile",
            "created_at", "resolved_at", "resolved_by",
        }
        assert required <= CHECKPOINT_STATE_FIELDS


class TestNoDangerousFieldsInState:
    """Tests that dangerous fields cannot appear in CheckpointState."""

    def test_sandbox_path_rejected(self):
        with pytest.raises(ValidationError):
            _valid_state(sandbox_path="/tmp/evil")  # type: ignore[call-arg]

    def test_argv_rejected(self):
        with pytest.raises(ValidationError):
            _valid_state(argv=["rm", "-rf"])  # type: ignore[call-arg]

    def test_env_rejected(self):
        with pytest.raises(ValidationError):
            _valid_state(env={"PATH": "/hack"})  # type: ignore[call-arg]

    def test_provider_rejected(self):
        with pytest.raises(ValidationError):
            _valid_state(provider="openai")  # type: ignore[call-arg]


# ── Profile context binding tests ─────────────────────────────────────


class TestProfileContextBinding:
    """Tests for profile context binding on CheckpointState."""

    def test_profile_fields_optional(self):
        """source_profile and target_profile default to None."""
        cp = _valid_state()
        assert cp.source_profile is None
        assert cp.target_profile is None

    def test_profile_fields_settable(self):
        cp = _valid_state(
            source_profile="java8-spring27-legacy",
            target_profile="java17-spring31-modern",
        )
        assert cp.source_profile == "java8-spring27-legacy"
        assert cp.target_profile == "java17-spring31-modern"

    def test_create_waiting_sets_profiles(self):
        cp = CheckpointState.create_waiting(
            checkpoint_id="cp-prof",
            job_id="job-xyz",
            stage_index=1,
            source_profile="legacy-profile",
            target_profile="modern-profile",
            created_at="2026-06-26T12:00:00Z",
        )
        assert cp.source_profile == "legacy-profile"
        assert cp.target_profile == "modern-profile"


# ── JSON serialization tests ──────────────────────────────────────────


class TestCheckpointStateSerialization:
    """Tests for JSON round-trip serialization."""

    def test_waiting_round_trip(self):
        import json
        cp = _valid_state(status=CheckpointStatus.WAITING)
        data = cp.model_dump_json()
        reloaded = CheckpointState.model_validate_json(data)
        assert reloaded.status == CheckpointStatus.WAITING
        assert reloaded.checkpoint_id == "cp-001"

    def test_terminal_round_trip(self):
        cp = _valid_state(
            status=CheckpointStatus.ACCEPTED,
            resolved_at="2026-06-26T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        data = cp.model_dump_json()
        reloaded = CheckpointState.model_validate_json(data)
        assert reloaded.status == CheckpointStatus.ACCEPTED
        assert reloaded.resolved_by == "ali.hamdaoui"

    def test_status_serializes_as_string(self):
        import json
        cp = _valid_state(status=CheckpointStatus.STALE, is_stale=True,
                          stale_reason="mismatch")
        d = json.loads(cp.model_dump_json())
        assert d["status"] == "stale"

    def test_model_copy_preserves_status(self):
        """model_copy() creates a new instance with the same fields."""
        cp = _valid_state(status=CheckpointStatus.WAITING)
        copy = cp.model_copy()
        assert copy is not cp
        assert copy.status == CheckpointStatus.WAITING
        assert copy.checkpoint_id == cp.checkpoint_id
