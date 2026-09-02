"""Focused tests for F15-JOB-061 — Extend ActionRequest with gate actions.

Proves:
  - Gate action types are added to allowed action types
  - Gate action request schema validates correctly
  - Blocked action types still rejected
  - AssistantGateAnswer schema validates
"""

import pytest

from migration_factory.control_tower.application.v2_model_schemas import (
    F05_ALLOWED_ACTION_TYPES,
    F15_GATE_ALLOWED_ACTION_TYPES,
    SchemaValidator,
    SchemaValidationError,
    ACTION_REQUEST_SCHEMA,
    GATE_ACTION_REQUEST_SCHEMA,
    ASSISTANT_GATE_ANSWER_SCHEMA,
)


class TestGateAllowedActions:
    """F15 gate action types are properly registered."""

    def test_gate_actions_in_allowed_list(self):
        """Gate action types are in F05_ALLOWED_ACTION_TYPES."""
        for action in F15_GATE_ALLOWED_ACTION_TYPES:
            assert action in F05_ALLOWED_ACTION_TYPES, (
                f"Gate action {action!r} missing from F05_ALLOWED_ACTION_TYPES"
            )

    def test_continue_from_gate_present(self):
        """continue_from_gate is in allowed types."""
        assert "continue_from_gate" in F05_ALLOWED_ACTION_TYPES

    def test_request_reanalysis_present(self):
        """request_reanalysis is in allowed types."""
        assert "request_reanalysis" in F05_ALLOWED_ACTION_TYPES

    def test_request_plan_revision_present(self):
        """request_plan_revision is in allowed types."""
        assert "request_plan_revision" in F05_ALLOWED_ACTION_TYPES

    def test_approve_from_gate_present(self):
        """approve_from_gate is in allowed types."""
        assert "approve_from_gate" in F05_ALLOWED_ACTION_TYPES

    def test_reject_from_gate_present(self):
        """reject_from_gate is in allowed types."""
        assert "reject_from_gate" in F05_ALLOWED_ACTION_TYPES

    def test_explain_gate_evidence_present(self):
        """explain_gate_evidence is in allowed types."""
        assert "explain_gate_evidence" in F05_ALLOWED_ACTION_TYPES

    def test_show_gate_available_actions_present(self):
        """show_gate_available_actions is in allowed types."""
        assert "show_gate_available_actions" in F05_ALLOWED_ACTION_TYPES

    def test_all_gate_actions_count(self):
        """All expected gate actions are present."""
        expected = frozenset({
            "continue_from_gate",
            "request_reanalysis",
            "request_plan_revision",
            "approve_from_gate",
            "reject_from_gate",
            "explain_gate_evidence",
            "show_gate_available_actions",
        })
        assert F15_GATE_ALLOWED_ACTION_TYPES == expected


class TestGateActionRequestSchema:
    """GATE_ACTION_REQUEST_SCHEMA validation."""

    def test_valid_continue_request(self):
        """Valid continue_from_gate request passes validation."""
        data = {
            "action_type": "continue_from_gate",
            "gate_id": "gate-123",
            "expected_gate_checksum": "abc123def456",
            "idempotency_key": "idem-001",
            "request_checksum": "req-chk-001",
            "reason": "Analysis looks good, proceed to planning",
        }
        SchemaValidator.validate("GateActionRequest", data)

    def test_valid_reanalysis_request(self):
        """Valid request_reanalysis with user feedback passes."""
        data = {
            "action_type": "request_reanalysis",
            "gate_id": "gate-456",
            "expected_gate_checksum": "def789abc012",
            "idempotency_key": "idem-002",
            "request_checksum": "req-chk-002",
            "reason": "Found additional XML configs to scan",
            "user_feedback": "Please scan the XML config files in src/main/resources",
        }
        SchemaValidator.validate("GateActionRequest", data)

    def test_valid_approve_request(self):
        """Valid approve_from_gate passes."""
        data = {
            "action_type": "approve_from_gate",
            "gate_id": "gate-789",
            "expected_gate_checksum": "chk7890123456",
            "idempotency_key": "idem-003",
            "request_checksum": "req-chk-003",
            "reason": "Plan looks safe, approving transformation",
        }
        SchemaValidator.validate("GateActionRequest", data)

    def test_missing_required_field(self):
        """Missing required field is rejected."""
        data = {
            "action_type": "continue_from_gate",
            "gate_id": "gate-123",
            # missing expected_gate_checksum, idempotency_key, etc.
        }
        with pytest.raises(SchemaValidationError):
            SchemaValidator.validate("GateActionRequest", data)

    def test_invalid_action_type(self):
        """Invalid action type is rejected."""
        data = {
            "action_type": "invalid_action",
            "gate_id": "gate-123",
            "expected_gate_checksum": "chk",
            "idempotency_key": "idem",
            "request_checksum": "req",
            "reason": "test",
        }
        with pytest.raises(SchemaValidationError):
            SchemaValidator.validate("GateActionRequest", data)

    def test_extra_fields_rejected(self):
        """Additional properties are rejected (fail closed)."""
        data = {
            "action_type": "continue_from_gate",
            "gate_id": "gate-123",
            "expected_gate_checksum": "chk",
            "idempotency_key": "idem",
            "request_checksum": "req",
            "reason": "test",
            "sandbox_path": "/tmp/evil-path",  # should be rejected
        }
        with pytest.raises(SchemaValidationError):
            SchemaValidator.validate("GateActionRequest", data)

    def test_stage_index_valid(self):
        """Valid stage_index passes, invalid is rejected."""
        valid = {
            "action_type": "continue_from_gate",
            "gate_id": "gate-123",
            "expected_gate_checksum": "chk",
            "idempotency_key": "idem",
            "request_checksum": "req",
            "reason": "test",
            "stage_index": 1,
        }
        SchemaValidator.validate("GateActionRequest", valid)

        invalid = dict(valid)
        invalid["stage_index"] = 5
        with pytest.raises(SchemaValidationError):
            SchemaValidator.validate("GateActionRequest", invalid)


class TestAssistantGateAnswerSchema:
    """ASSISTANT_GATE_ANSWER_SCHEMA validation."""

    def test_valid_answer(self):
        """Valid gate answer passes validation."""
        data = {
            "gate_id": "gate-123",
            "gate_phase": "analysis_review",
            "answer": "The analysis found 3 high-risk dependencies.",
            "evidence_summary": "3 high, 5 medium, 2 low risks detected.",
            "decision_required": True,
            "stage_index": 1,
        }
        SchemaValidator.validate("AssistantGateAnswer", data)

    def test_with_available_actions(self):
        """Answer with available actions passes."""
        data = {
            "gate_id": "gate-123",
            "gate_phase": "analysis_review",
            "answer": "You can continue to planning or request reanalysis.",
            "available_actions": [
                {"action": "continue", "label": "Continue to Planning",
                 "description": "Accept analysis and proceed to Stage 2"},
                {"action": "reanalyze", "label": "Request Reanalysis",
                 "description": "Re-run analysis with additional configs"},
            ],
            "decision_required": True,
            "gate_checksum": "abc123",
            "stage_index": 1,
        }
        SchemaValidator.validate("AssistantGateAnswer", data)

    def test_missing_required_field(self):
        """Missing required gate_id is rejected."""
        data = {
            "gate_phase": "analysis_review",
            "answer": "No gate ID",
        }
        with pytest.raises(SchemaValidationError):
            SchemaValidator.validate("AssistantGateAnswer", data)

    def test_invalid_gate_phase(self):
        """Invalid gate phase is rejected."""
        data = {
            "gate_id": "gate-123",
            "gate_phase": "invalid_phase",
            "answer": "test",
        }
        with pytest.raises(SchemaValidationError):
            SchemaValidator.validate("AssistantGateAnswer", data)

    def test_extra_fields_rejected(self):
        """Extra properties are rejected (fail closed)."""
        data = {
            "gate_id": "gate-123",
            "gate_phase": "analysis_review",
            "answer": "test",
            "raw_command": "rm -rf /",  # should be rejected
        }
        with pytest.raises(SchemaValidationError):
            SchemaValidator.validate("AssistantGateAnswer", data)

    def test_all_gate_phases_accepted(self):
        """All valid gate phases are accepted."""
        for phase in ["analysis_review", "planning_review",
                       "approval_review", "repair_review",
                       "stage_completion_review"]:
            data = {
                "gate_id": "gate-123",
                "gate_phase": phase,
                "answer": f"Explanation for {phase}",
            }
            SchemaValidator.validate("AssistantGateAnswer", data)


class TestBlockedActions:
    """Blocked action types are still rejected."""

    def test_execute_command_blocked(self):
        """execute_command_directly is blocked."""
        from migration_factory.control_tower.application.v2_model_schemas import (
            F05_EXPLICITLY_BLOCKED_ACTION_TYPES,
        )
        assert "execute_command_directly" in F05_EXPLICITLY_BLOCKED_ACTION_TYPES

    def test_approve_decision_blocked(self):
        """approve_decision is blocked."""
        from migration_factory.control_tower.application.v2_model_schemas import (
            F05_EXPLICITLY_BLOCKED_ACTION_TYPES,
        )
        assert "approve_decision" in F05_EXPLICITLY_BLOCKED_ACTION_TYPES

    def test_blocked_not_in_allowed(self):
        """Blocked action types are not in allowed types."""
        from migration_factory.control_tower.application.v2_model_schemas import (
            F05_EXPLICITLY_BLOCKED_ACTION_TYPES,
            F05_ALLOWED_ACTION_TYPES,
        )
        for blocked in F05_EXPLICITLY_BLOCKED_ACTION_TYPES:
            assert blocked not in F05_ALLOWED_ACTION_TYPES
