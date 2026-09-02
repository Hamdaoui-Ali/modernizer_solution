"""F1-T2 focused tests — User decision contract round-trip."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.user_decision_contract import (
    DECISIONS_REQUIRING_REASON,
    GATE_DECISION_TO_USER_DECISIONS,
    MODIFICATION_USER_DECISIONS,
    READ_ONLY_USER_DECISIONS,
    SUCCESSFUL_USER_DECISION_OUTCOMES,
    TERMINAL_USER_DECISIONS,
    USER_DECISION_FIELDS,
    USER_DECISION_TO_GATE_DECISION,
    UserDecision,
    UserDecisionOutcome,
    UserDecisionRejectionCode,
    UserDecisionRequest,
    UserDecisionResponse,
    get_gate_decision,
    is_modification_decision,
    is_read_only_decision,
    is_terminal_decision,
    is_valid_decision,
    is_valid_outcome,
    is_valid_rejection_code,
    validate_user_decision_fields,
)


# ── helpers ───────────────────────────────────────────────────────────

def _valid_request(**overrides) -> UserDecisionRequest:
    defaults = {
        "checkpoint_id": "acp-001",
        "job_id": "job-abc",
        "revision_id": "rev-001",
        "checksum": "sha256:abc123def456",
        "decision": UserDecision.CONTINUE,
        "reason": "All looks good, proceed.",
        "comment_text": "",
        "idempotency_key": "idem-001",
    }
    defaults.update(overrides)
    return UserDecisionRequest(**defaults)


def _valid_response(**overrides) -> UserDecisionResponse:
    defaults = {
        "decision_id": "dec-001",
        "checkpoint_id": "acp-001",
        "job_id": "job-abc",
        "decision": UserDecision.CONTINUE,
        "outcome": UserDecisionOutcome.DECISION_ACCEPTED,
        "gate_decision": "continue",
        "message": "Decision accepted",
        "idempotency_key": "idem-001",
        "decided_at": "2026-06-26T12:00:00Z",
        "decided_by": "ali.hamdaoui",
    }
    defaults.update(overrides)
    return UserDecisionResponse(**defaults)


# ── UserDecision enum tests ───────────────────────────────────────────


class TestUserDecisionEnum:
    """Tests for the UserDecision enum."""

    def test_all_expected_values_present(self):
        """Verify all six decision types are defined."""
        values = {d.value for d in UserDecision}
        assert values == {
            "continue",
            "stop",
            "request_analysis_modification",
            "request_planning_modification",
            "download_artifact",
            "resume",
        }

    def test_is_valid_decision_recognizes_all(self):
        """is_valid_decision returns True for all enum members."""
        for member in UserDecision:
            assert is_valid_decision(member.value)

    def test_is_valid_decision_rejects_invalid(self):
        """is_valid_decision returns False for unknown strings."""
        assert not is_valid_decision("delete_everything")
        assert not is_valid_decision("approve")
        assert not is_valid_decision("")
        assert not is_valid_decision("CONTINUE")  # case-sensitive

    def test_terminal_decisions(self):
        """STOP and CONTINUE are terminal decisions."""
        assert UserDecision.STOP in TERMINAL_USER_DECISIONS
        assert UserDecision.CONTINUE in TERMINAL_USER_DECISIONS
        assert UserDecision.RESUME not in TERMINAL_USER_DECISIONS
        assert UserDecision.DOWNLOAD_ARTIFACT not in TERMINAL_USER_DECISIONS

    def test_read_only_decisions(self):
        """Only DOWNLOAD_ARTIFACT is read-only."""
        assert UserDecision.DOWNLOAD_ARTIFACT in READ_ONLY_USER_DECISIONS
        assert len(READ_ONLY_USER_DECISIONS) == 1

    def test_modification_decisions(self):
        """Analysis and planning modification requests are modification decisions."""
        assert UserDecision.REQUEST_ANALYSIS_MODIFICATION in MODIFICATION_USER_DECISIONS
        assert UserDecision.REQUEST_PLANNING_MODIFICATION in MODIFICATION_USER_DECISIONS
        assert len(MODIFICATION_USER_DECISIONS) == 2

    def test_decisions_requiring_reason(self):
        """STOP and modification decisions require a reason."""
        assert UserDecision.STOP in DECISIONS_REQUIRING_REASON
        assert UserDecision.REQUEST_ANALYSIS_MODIFICATION in DECISIONS_REQUIRING_REASON
        assert UserDecision.REQUEST_PLANNING_MODIFICATION in DECISIONS_REQUIRING_REASON
        assert UserDecision.CONTINUE not in DECISIONS_REQUIRING_REASON

    def test_is_terminal_decision_helper(self):
        """is_terminal_decision helper returns correct values."""
        assert is_terminal_decision(UserDecision.STOP)
        assert is_terminal_decision(UserDecision.CONTINUE)
        assert not is_terminal_decision(UserDecision.RESUME)
        assert not is_terminal_decision(UserDecision.DOWNLOAD_ARTIFACT)

    def test_is_modification_decision_helper(self):
        """is_modification_decision helper returns correct values."""
        assert is_modification_decision(UserDecision.REQUEST_ANALYSIS_MODIFICATION)
        assert is_modification_decision(UserDecision.REQUEST_PLANNING_MODIFICATION)
        assert not is_modification_decision(UserDecision.CONTINUE)
        assert not is_modification_decision(UserDecision.STOP)

    def test_is_read_only_decision_helper(self):
        """is_read_only_decision helper returns correct values."""
        assert is_read_only_decision(UserDecision.DOWNLOAD_ARTIFACT)
        assert not is_read_only_decision(UserDecision.CONTINUE)
        assert not is_read_only_decision(UserDecision.STOP)


# ── UserDecision → GateDecision mapping tests ─────────────────────────


class TestUserDecisionToGateMapping:
    """Tests for the user decision ↔ gate decision mappings."""

    def test_continue_maps_to_continue(self):
        assert USER_DECISION_TO_GATE_DECISION[UserDecision.CONTINUE] == "continue"

    def test_resume_maps_to_continue(self):
        assert USER_DECISION_TO_GATE_DECISION[UserDecision.RESUME] == "continue"

    def test_analysis_modification_maps_to_reanalyze(self):
        assert (
            USER_DECISION_TO_GATE_DECISION[UserDecision.REQUEST_ANALYSIS_MODIFICATION]
            == "reanalyze"
        )

    def test_planning_modification_maps_to_revise(self):
        assert (
            USER_DECISION_TO_GATE_DECISION[UserDecision.REQUEST_PLANNING_MODIFICATION]
            == "revise"
        )

    def test_stop_has_no_gate_decision(self):
        assert USER_DECISION_TO_GATE_DECISION[UserDecision.STOP] is None

    def test_download_has_no_gate_decision(self):
        assert USER_DECISION_TO_GATE_DECISION[UserDecision.DOWNLOAD_ARTIFACT] is None

    def test_all_user_decisions_have_mapping_entries(self):
        """Every UserDecision must have an entry in the mapping."""
        for decision in UserDecision:
            assert decision in USER_DECISION_TO_GATE_DECISION

    def test_get_gate_decision_helper(self):
        """get_gate_decision returns the correct gate decision."""
        assert get_gate_decision(UserDecision.CONTINUE) == "continue"
        assert get_gate_decision(UserDecision.STOP) is None
        assert get_gate_decision(UserDecision.RESUME) == "continue"

    def test_gate_continue_maps_to_continue_and_resume(self):
        """Gate 'continue' maps to both CONTINUE and RESUME user decisions."""
        mapped = GATE_DECISION_TO_USER_DECISIONS["continue"]
        assert UserDecision.CONTINUE in mapped
        assert UserDecision.RESUME in mapped

    def test_gate_reanalyze_maps_to_analysis_modification(self):
        mapped = GATE_DECISION_TO_USER_DECISIONS["reanalyze"]
        assert UserDecision.REQUEST_ANALYSIS_MODIFICATION in mapped

    def test_gate_revise_maps_to_planning_modification(self):
        mapped = GATE_DECISION_TO_USER_DECISIONS["revise"]
        assert UserDecision.REQUEST_PLANNING_MODIFICATION in mapped

    def test_gate_approve_and_reject_have_no_user_decisions(self):
        """Approve/reject gate decisions have no direct user decision mapping."""
        assert len(GATE_DECISION_TO_USER_DECISIONS["approve"]) == 0
        assert len(GATE_DECISION_TO_USER_DECISIONS["reject"]) == 0


# ── UserDecisionOutcome tests ──────────────────────────────────────────


class TestUserDecisionOutcome:
    """Tests for UserDecisionOutcome enum."""

    def test_all_outcomes_present(self):
        values = {o.value for o in UserDecisionOutcome}
        assert values == {
            "decision_accepted",
            "decision_rejected",
            "decision_stale",
            "decision_idempotent",
            "decision_terminal",
        }

    def test_successful_outcomes(self):
        """Only ACCEPTED and IDEMPOTENT are successful."""
        assert UserDecisionOutcome.DECISION_ACCEPTED in SUCCESSFUL_USER_DECISION_OUTCOMES
        assert UserDecisionOutcome.DECISION_IDEMPOTENT in SUCCESSFUL_USER_DECISION_OUTCOMES
        assert UserDecisionOutcome.DECISION_REJECTED not in SUCCESSFUL_USER_DECISION_OUTCOMES

    def test_is_valid_outcome_helper(self):
        """is_valid_outcome returns correct values."""
        assert is_valid_outcome("decision_accepted")
        assert is_valid_outcome("decision_rejected")
        assert not is_valid_outcome("invalid_outcome")
        assert not is_valid_outcome("")


# ── UserDecisionRejectionCode tests ────────────────────────────────────


class TestUserDecisionRejectionCode:
    """Tests for UserDecisionRejectionCode enum."""

    def test_all_rejection_codes_present(self):
        codes = {c.value for c in UserDecisionRejectionCode}
        assert codes == {
            "checkpoint_not_found",
            "checkpoint_already_resolved",
            "checkpoint_stale",
            "checksum_mismatch",
            "invalid_decision",
            "invalid_revision",
            "missing_reason",
            "unauthorized",
            "backend_failure",
            "forbidden_field_present",
        }

    def test_is_valid_rejection_code_helper(self):
        """is_valid_rejection_code returns correct values."""
        assert is_valid_rejection_code("checkpoint_not_found")
        assert is_valid_rejection_code("checksum_mismatch")
        assert not is_valid_rejection_code("not_a_code")
        assert not is_valid_rejection_code("")


# ── UserDecisionRequest construction tests ─────────────────────────────


class TestUserDecisionRequestConstruction:
    """Tests for UserDecisionRequest construction and validation."""

    def test_minimal_continue_request(self):
        req = _valid_request()
        assert req.checkpoint_id == "acp-001"
        assert req.decision == UserDecision.CONTINUE
        assert req.is_terminal
        assert not req.is_read_only
        assert not req.is_modification

    def test_stop_request(self):
        req = _valid_request(
            decision=UserDecision.STOP,
            reason="User decided to stop the migration.",
        )
        assert req.decision == UserDecision.STOP
        assert req.is_terminal

    def test_analysis_modification_request(self):
        req = _valid_request(
            decision=UserDecision.REQUEST_ANALYSIS_MODIFICATION,
            reason="Need to re-check dependency graph.",
            comment_text="The dependency graph missed javax.persistence imports.",
        )
        assert req.decision == UserDecision.REQUEST_ANALYSIS_MODIFICATION
        assert req.is_modification
        assert not req.is_terminal

    def test_planning_modification_request(self):
        req = _valid_request(
            decision=UserDecision.REQUEST_PLANNING_MODIFICATION,
            comment_text="The migration units should be split differently.",
        )
        assert req.decision == UserDecision.REQUEST_PLANNING_MODIFICATION
        assert req.is_modification

    def test_download_request(self):
        req = _valid_request(
            decision=UserDecision.DOWNLOAD_ARTIFACT,
            reason="",
            comment_text="",
        )
        assert req.decision == UserDecision.DOWNLOAD_ARTIFACT
        assert req.is_read_only
        assert not req.is_terminal

    def test_resume_request(self):
        req = _valid_request(
            decision=UserDecision.RESUME,
            reason="Resuming after review.",
        )
        assert req.decision == UserDecision.RESUME
        assert not req.is_terminal

    def test_gate_decision_property(self):
        """The gate_decision property returns the mapped gate decision."""
        req = _valid_request(decision=UserDecision.CONTINUE)
        assert req.gate_decision == "continue"

        req = _valid_request(decision=UserDecision.STOP)
        assert req.gate_decision is None

    def test_decision_coerced_from_string(self):
        req = _valid_request(decision="continue")
        assert req.decision == UserDecision.CONTINUE

    def test_invalid_decision_raises(self):
        with pytest.raises(ValidationError):
            _valid_request(decision="delete_everything")


class TestUserDecisionRequestValidation:
    """Tests for UserDecisionRequest field validation."""

    def test_empty_checkpoint_id_raises(self):
        with pytest.raises(ValidationError):
            _valid_request(checkpoint_id="")

    def test_empty_job_id_raises(self):
        with pytest.raises(ValidationError):
            _valid_request(job_id="")

    def test_empty_revision_id_raises(self):
        with pytest.raises(ValidationError):
            _valid_request(revision_id="")

    def test_empty_checksum_raises(self):
        with pytest.raises(ValidationError):
            _valid_request(checksum="")

    def test_empty_idempotency_key_raises(self):
        with pytest.raises(ValidationError):
            _valid_request(idempotency_key="")

    def test_comment_text_max_length(self):
        """comment_text is capped at 2000 characters."""
        req = _valid_request(comment_text="x" * 2000)
        assert len(req.comment_text) == 2000

    def test_comment_text_too_long_raises(self):
        with pytest.raises(ValidationError):
            _valid_request(comment_text="x" * 2001)

    def test_modification_requires_comment_text(self):
        """Requesting analysis modification without comment_text raises."""
        with pytest.raises(ValidationError):
            _valid_request(
                decision=UserDecision.REQUEST_ANALYSIS_MODIFICATION,
                comment_text="",
            )

    def test_modification_with_whitespace_only_comment_raises(self):
        """Whitespace-only comment_text does not satisfy the requirement."""
        with pytest.raises(ValidationError):
            _valid_request(
                decision=UserDecision.REQUEST_PLANNING_MODIFICATION,
                comment_text="   ",
            )

    def test_stop_requires_reason_or_comment(self):
        """STOP decision requires at least a reason or comment_text."""
        with pytest.raises(ValidationError):
            _valid_request(
                decision=UserDecision.STOP,
                reason="",
                comment_text="",
            )

    def test_stop_with_comment_succeeds(self):
        """STOP with only comment_text (no reason) is allowed."""
        req = _valid_request(
            decision=UserDecision.STOP,
            reason="",
            comment_text="Stopping because the migration target is wrong.",
        )
        assert req.decision == UserDecision.STOP

    def test_extra_fields_forbidden(self):
        """Extra fields on a request raise ValidationError."""
        with pytest.raises(ValidationError):
            _valid_request(sandbox_path="/tmp/hack")


# ── UserDecisionResponse construction tests ────────────────────────────


class TestUserDecisionResponseConstruction:
    """Tests for UserDecisionResponse construction and validation."""

    def test_minimal_accepted_response(self):
        resp = _valid_response()
        assert resp.decision_id == "dec-001"
        assert resp.outcome == UserDecisionOutcome.DECISION_ACCEPTED
        assert resp.is_successful
        assert not resp.is_rejected

    def test_rejected_response(self):
        resp = _valid_response(
            outcome=UserDecisionOutcome.DECISION_REJECTED,
            rejection_code=UserDecisionRejectionCode.CHECKSUM_MISMATCH,
            gate_decision=None,
            message="Checksum does not match current artifact.",
        )
        assert resp.outcome == UserDecisionOutcome.DECISION_REJECTED
        assert resp.rejection_code == UserDecisionRejectionCode.CHECKSUM_MISMATCH
        assert resp.is_rejected
        assert not resp.is_successful

    def test_rejected_without_rejection_code_raises(self):
        """A rejected response must have a rejection_code."""
        with pytest.raises(ValidationError):
            _valid_response(
                outcome=UserDecisionOutcome.DECISION_REJECTED,
                rejection_code=None,
            )

    def test_accepted_with_rejection_code_raises(self):
        """An accepted response must not have a rejection_code."""
        with pytest.raises(ValidationError):
            _valid_response(
                outcome=UserDecisionOutcome.DECISION_ACCEPTED,
                rejection_code=UserDecisionRejectionCode.CHECKSUM_MISMATCH,
            )

    def test_coerces_decision_from_string(self):
        resp = _valid_response(decision="stop")
        assert resp.decision == UserDecision.STOP

    def test_coerces_outcome_from_string(self):
        resp = _valid_response(outcome="decision_idempotent")
        assert resp.outcome == UserDecisionOutcome.DECISION_IDEMPOTENT

    def test_coerces_rejection_code_from_string(self):
        resp = _valid_response(
            outcome=UserDecisionOutcome.DECISION_REJECTED,
            rejection_code="checkpoint_stale",
        )
        assert resp.rejection_code == UserDecisionRejectionCode.CHECKPOINT_STALE

    def test_none_rejection_code_preserved(self):
        """rejection_code=None is preserved for non-rejected outcomes."""
        resp = _valid_response(rejection_code=None)
        assert resp.rejection_code is None

    def test_terminal_outcome(self):
        resp = _valid_response(
            outcome=UserDecisionOutcome.DECISION_TERMINAL,
            rejection_code=None,  # terminal can have no rejection
            gate_decision=None,
        )
        assert resp.is_terminal_outcome

    def test_extra_fields_forbidden(self):
        """Extra fields on a response raise ValidationError."""
        with pytest.raises(ValidationError):
            _valid_response(sandbox_path="/tmp/leak")


# ── Factory method tests ───────────────────────────────────────────────


class TestUserDecisionResponseFactories:
    """Tests for UserDecisionResponse factory methods."""

    def test_idempotent_factory(self):
        resp = UserDecisionResponse.idempotent(
            decision_id="dec-002",
            checkpoint_id="acp-001",
            job_id="job-abc",
            decision=UserDecision.CONTINUE,
            idempotency_key="idem-001",
            existing_outcome=UserDecisionOutcome.DECISION_ACCEPTED,
            existing_gate_decision="continue",
            decided_at="2026-06-26T12:00:00Z",
            decided_by="ali.hamdaoui",
        )
        assert resp.outcome == UserDecisionOutcome.DECISION_IDEMPOTENT
        assert resp.gate_decision == "continue"
        assert resp.is_successful
        assert "Duplicate" in resp.message

    def test_accepted_factory(self):
        resp = UserDecisionResponse.accepted(
            decision_id="dec-003",
            checkpoint_id="acp-002",
            job_id="job-xyz",
            decision=UserDecision.CONTINUE,
            idempotency_key="idem-002",
            gate_decision="continue",
            message="Proceeding to next stage.",
            next_stage="planning",
            decided_at="2026-06-26T13:00:00Z",
            decided_by="ali.hamdaoui",
        )
        assert resp.outcome == UserDecisionOutcome.DECISION_ACCEPTED
        assert resp.next_stage == "planning"
        assert resp.message == "Proceeding to next stage."

    def test_rejected_factory(self):
        resp = UserDecisionResponse.rejected(
            decision_id="dec-004",
            checkpoint_id="acp-003",
            job_id="job-abc",
            decision=UserDecision.STOP,
            idempotency_key="idem-003",
            rejection_code=UserDecisionRejectionCode.CHECKPOINT_STALE,
            decided_at="2026-06-26T14:00:00Z",
            decided_by="backend",
        )
        assert resp.outcome == UserDecisionOutcome.DECISION_REJECTED
        assert resp.rejection_code == UserDecisionRejectionCode.CHECKPOINT_STALE
        assert "checkpoint_stale" in resp.message

    def test_factory_next_stage_is_backend_owned(self):
        """next_stage in factory methods is set by backend, not user."""
        resp = UserDecisionResponse.accepted(
            decision_id="dec-005",
            checkpoint_id="acp-004",
            job_id="job-xyz",
            decision=UserDecision.RESUME,
            idempotency_key="idem-004",
            next_stage="build",
            decided_at="2026-06-26T15:00:00Z",
            decided_by="backend",
        )
        assert resp.next_stage == "build"


# ── from_dict tests ────────────────────────────────────────────────────


class TestUserDecisionResponseFromDict:
    """Tests for UserDecisionResponse.from_dict."""

    def test_from_dict_basic(self):
        data = {
            "decision_id": "dec-010",
            "checkpoint_id": "acp-010",
            "job_id": "job-xyz",
            "decision": "continue",
            "outcome": "decision_accepted",
            "gate_decision": "continue",
            "message": "Proceed to planning.",
            "idempotency_key": "idem-010",
            "decided_at": "2026-06-26T12:00:00Z",
            "decided_by": "ali.hamdaoui",
        }
        resp = UserDecisionResponse.from_dict(data)
        assert resp.decision_id == "dec-010"
        assert resp.decision == UserDecision.CONTINUE
        assert resp.outcome == UserDecisionOutcome.DECISION_ACCEPTED
        assert resp.is_successful

    def test_from_dict_none_values_guarded(self):
        """None values do not become 'None' strings."""
        data = {
            "decision_id": None,
            "checkpoint_id": None,
            "job_id": None,
            "decision": "continue",
            "outcome": "decision_accepted",
            "idempotency_key": None,
        }
        resp = UserDecisionResponse.from_dict(data)
        # None/empty → "unknown" for NonEmptyString fields
        assert resp.decision_id == "unknown"
        assert resp.checkpoint_id == "unknown"
        assert resp.job_id == "unknown"
        assert resp.idempotency_key == "unknown"

    def test_from_dict_guard_optional_none_fields(self):
        """Optional fields with None are preserved as None, not 'None'."""
        data = {
            "decision_id": "dec-011",
            "checkpoint_id": "acp-011",
            "job_id": "job-xyz",
            "decision": "continue",
            "outcome": "decision_accepted",
            "idempotency_key": "idem-011",
            "gate_decision": None,
            "next_stage": None,
            "correlation_id": None,
            "causation_id": None,
        }
        resp = UserDecisionResponse.from_dict(data)
        assert resp.gate_decision is None
        assert resp.next_stage is None
        assert resp.correlation_id is None
        assert resp.causation_id is None

    def test_from_dict_unknown_outcome_defaults_to_rejected(self):
        """Unknown outcome string defaults to DECISION_REJECTED."""
        data = {
            "decision_id": "dec-012",
            "checkpoint_id": "acp-012",
            "job_id": "job-xyz",
            "decision": "continue",
            "outcome": "nonexistent_outcome",
            "idempotency_key": "idem-012",
        }
        resp = UserDecisionResponse.from_dict(data)
        assert resp.outcome == UserDecisionOutcome.DECISION_REJECTED
        # Fallback: rejected outcome without rejection_code → BACKEND_FAILURE
        assert resp.rejection_code == UserDecisionRejectionCode.BACKEND_FAILURE

    def test_from_dict_rejected_without_code_defaults_backend_failure(self):
        """Rejected outcome without rejection_code → BACKEND_FAILURE fallback."""
        data = {
            "decision_id": "dec-013",
            "checkpoint_id": "acp-013",
            "job_id": "job-xyz",
            "decision": "stop",
            "outcome": "decision_rejected",
            "idempotency_key": "idem-013",
            "rejection_code": None,
        }
        resp = UserDecisionResponse.from_dict(data)
        assert resp.outcome == UserDecisionOutcome.DECISION_REJECTED
        assert resp.rejection_code == UserDecisionRejectionCode.BACKEND_FAILURE

    def test_from_dict_unknown_rejection_code_falls_back(self):
        """Unknown rejection code string defaults to BACKEND_FAILURE for rejected outcome."""
        data = {
            "decision_id": "dec-014",
            "checkpoint_id": "acp-014",
            "job_id": "job-xyz",
            "decision": "stop",
            "outcome": "decision_rejected",
            "idempotency_key": "idem-014",
            "rejection_code": "some_unknown_code",
        }
        resp = UserDecisionResponse.from_dict(data)
        assert resp.rejection_code == UserDecisionRejectionCode.BACKEND_FAILURE

    def test_from_dict_missing_keys_default_to_empty(self):
        """Missing keys get empty defaults."""
        data = {
            "decision_id": "dec-015",
            "checkpoint_id": "acp-015",
            "job_id": "job-xyz",
            "decision": "continue",
            "outcome": "decision_accepted",
            "idempotency_key": "idem-015",
        }
        resp = UserDecisionResponse.from_dict(data)
        assert resp.message == ""
        assert resp.decided_at == ""
        assert resp.decided_by == ""

    def test_from_dict_unknown_decision_defaults_to_continue(self):
        """Unknown decision string defaults to CONTINUE."""
        data = {
            "decision_id": "dec-016",
            "checkpoint_id": "acp-016",
            "job_id": "job-xyz",
            "decision": "unknown_decision",
            "outcome": "decision_accepted",
            "idempotency_key": "idem-016",
        }
        resp = UserDecisionResponse.from_dict(data)
        assert resp.decision == UserDecision.CONTINUE


# ── Serialization tests ────────────────────────────────────────────────


class TestUserDecisionSerialization:
    """Tests for JSON round-trip serialization."""

    def test_request_round_trip_json(self):
        req = _valid_request(
            decision=UserDecision.REQUEST_ANALYSIS_MODIFICATION,
            comment_text="Re-check the javax deps.",
        )
        data = req.model_dump_json()
        reloaded = UserDecisionRequest.model_validate_json(data)
        assert reloaded.decision == UserDecision.REQUEST_ANALYSIS_MODIFICATION
        assert reloaded.comment_text == "Re-check the javax deps."

    def test_response_round_trip_json(self):
        resp = _valid_response(
            outcome=UserDecisionOutcome.DECISION_REJECTED,
            rejection_code=UserDecisionRejectionCode.CHECKPOINT_NOT_FOUND,
            gate_decision=None,
        )
        data = resp.model_dump_json()
        reloaded = UserDecisionResponse.model_validate_json(data)
        assert reloaded.outcome == UserDecisionOutcome.DECISION_REJECTED
        assert reloaded.rejection_code == UserDecisionRejectionCode.CHECKPOINT_NOT_FOUND

    def test_request_serializes_decision_as_string(self):
        """decision serializes as its string value."""
        req = _valid_request(decision=UserDecision.STOP, reason="stop reason")
        d = json.loads(req.model_dump_json())
        assert d["decision"] == "stop"

    def test_response_serializes_outcome_as_string(self):
        resp = _valid_response()
        d = json.loads(resp.model_dump_json())
        assert d["outcome"] == "decision_accepted"

    def test_idempotent_response_round_trip(self):
        resp = UserDecisionResponse.idempotent(
            decision_id="dec-020",
            checkpoint_id="acp-020",
            job_id="job-xyz",
            decision=UserDecision.CONTINUE,
            idempotency_key="idem-020",
            existing_outcome=UserDecisionOutcome.DECISION_ACCEPTED,
        )
        data = resp.model_dump_json()
        reloaded = UserDecisionResponse.model_validate_json(data)
        assert reloaded.outcome == UserDecisionOutcome.DECISION_IDEMPOTENT


# ── Safe fields tests ──────────────────────────────────────────────────


class TestUserDecisionFields:
    """Tests for USER_DECISION_FIELDS safety."""

    def test_no_dangerous_fields_in_user_decision_fields(self):
        """USER_DECISION_FIELDS must not contain any dangerous field names."""
        dangerous = {
            "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
            "provider", "model", "deployment", "endpoint", "secret", "token",
            "password", "api_key", "client_secret", "command",
        }
        overlap = USER_DECISION_FIELDS & dangerous
        assert not overlap, f"USER_DECISION_FIELDS contains dangerous fields: {overlap}"

    def test_assertion_passes(self):
        """The module-level assertion does not fire (import succeeds)."""
        # If the import succeeded, the assertion already passed.
        # This test exists to make the verification explicit.
        assert True


class TestNoDangerousFieldsInOutput:
    """Tests that dangerous fields cannot appear in request/response."""

    def test_sandbox_path_rejected_in_request(self):
        with pytest.raises(ValidationError):
            UserDecisionRequest(
                checkpoint_id="acp-001",
                job_id="job-abc",
                revision_id="rev-001",
                checksum="sha256:abc",
                decision=UserDecision.CONTINUE,
                idempotency_key="idem-001",
                sandbox_path="/tmp/evil",  # type: ignore[call-arg]
            )

    def test_argv_rejected_in_request(self):
        with pytest.raises(ValidationError):
            UserDecisionRequest(
                checkpoint_id="acp-001",
                job_id="job-abc",
                revision_id="rev-001",
                checksum="sha256:abc",
                decision=UserDecision.CONTINUE,
                idempotency_key="idem-001",
                argv=["rm", "-rf"],  # type: ignore[call-arg]
            )

    def test_env_rejected_in_request(self):
        with pytest.raises(ValidationError):
            UserDecisionRequest(
                checkpoint_id="acp-001",
                job_id="job-abc",
                revision_id="rev-001",
                checksum="sha256:abc",
                decision=UserDecision.CONTINUE,
                idempotency_key="idem-001",
                env={"PATH": "/evil"},  # type: ignore[call-arg]
            )

    def test_sandbox_path_rejected_in_response(self):
        with pytest.raises(ValidationError):
            UserDecisionResponse(
                decision_id="dec-001",
                checkpoint_id="acp-001",
                job_id="job-abc",
                decision=UserDecision.CONTINUE,
                outcome=UserDecisionOutcome.DECISION_ACCEPTED,
                idempotency_key="idem-001",
                sandbox_path="/tmp/evil",  # type: ignore[call-arg]
            )


# ── validate_user_decision_fields tests ────────────────────────────────


class TestValidateUserDecisionFields:
    """Tests for the validate_user_decision_fields helper."""

    def test_clean_data_passes(self):
        is_valid, forbidden = validate_user_decision_fields({
            "checkpoint_id": "acp-001",
            "decision": "continue",
            "reason": "looks good",
        })
        assert is_valid
        assert forbidden == []

    def test_detects_sandbox_path(self):
        is_valid, forbidden = validate_user_decision_fields({
            "checkpoint_id": "acp-001",
            "sandbox_path": "/tmp/leak",
        })
        assert not is_valid
        assert "sandbox_path" in forbidden

    def test_detects_multiple_forbidden_fields(self):
        is_valid, forbidden = validate_user_decision_fields({
            "checkpoint_id": "acp-001",
            "sandbox_path": "/tmp/leak",
            "argv": ["bad"],
            "env": {"KEY": "val"},
        })
        assert not is_valid
        assert len(forbidden) == 3
        assert "sandbox_path" in forbidden
        assert "argv" in forbidden
        assert "env" in forbidden

    def test_detects_endpoint(self):
        is_valid, forbidden = validate_user_decision_fields({
            "endpoint": "https://evil.com",
        })
        assert not is_valid
        assert "endpoint" in forbidden

    def test_detects_provider(self):
        is_valid, forbidden = validate_user_decision_fields({
            "provider": "openai",
        })
        assert not is_valid
        assert "provider" in forbidden

    def test_detects_deployment(self):
        is_valid, forbidden = validate_user_decision_fields({
            "deployment": "prod",
        })
        assert not is_valid
        assert "deployment" in forbidden


# ── Property tests ─────────────────────────────────────────────────────


class TestUserDecisionRequestProperties:
    """Tests for UserDecisionRequest property methods."""

    def test_continue_properties(self):
        req = _valid_request(decision=UserDecision.CONTINUE)
        assert req.is_terminal
        assert not req.is_read_only
        assert not req.is_modification

    def test_stop_properties(self):
        req = _valid_request(decision=UserDecision.STOP, reason="done")
        assert req.is_terminal
        assert not req.is_read_only
        assert not req.is_modification

    def test_download_properties(self):
        req = _valid_request(decision=UserDecision.DOWNLOAD_ARTIFACT)
        assert not req.is_terminal
        assert req.is_read_only
        assert not req.is_modification

    def test_resume_properties(self):
        req = _valid_request(decision=UserDecision.RESUME, reason="continue")
        assert not req.is_terminal
        assert not req.is_read_only
        assert not req.is_modification

    def test_modification_properties(self):
        req = _valid_request(
            decision=UserDecision.REQUEST_ANALYSIS_MODIFICATION,
            comment_text="Change this",
        )
        assert not req.is_terminal
        assert not req.is_read_only
        assert req.is_modification


class TestUserDecisionResponseProperties:
    """Tests for UserDecisionResponse property methods."""

    def test_is_successful(self):
        resp = _valid_response(outcome=UserDecisionOutcome.DECISION_ACCEPTED)
        assert resp.is_successful
        assert not resp.is_rejected

    def test_is_rejected(self):
        resp = _valid_response(
            outcome=UserDecisionOutcome.DECISION_REJECTED,
            rejection_code=UserDecisionRejectionCode.UNAUTHORIZED,
        )
        assert resp.is_rejected
        assert not resp.is_successful

    def test_is_terminal_outcome(self):
        resp = _valid_response(outcome=UserDecisionOutcome.DECISION_TERMINAL, rejection_code=None, gate_decision=None)
        assert resp.is_terminal_outcome
