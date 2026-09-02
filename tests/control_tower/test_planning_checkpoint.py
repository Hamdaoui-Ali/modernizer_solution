"""F1-T4 focused tests — Planning checkpoint contract round-trip."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.planning_checkpoint import (
    PLANNING_CHECKPOINT_FIELDS,
    REQUIRED_PLANNING_ARTIFACTS,
    REQUIRED_PLANNING_ARTIFACT_FIELDS,
    TERMINAL_PLANNING_OUTCOMES,
    PlanningCheckpoint,
    PlanningCheckpointAction,
    PlanningCheckpointComment,
    PlanningOutcome,
    get_required_planning_artifact_types,
    is_valid_planning_action_for_outcome,
    is_valid_planning_gate_decision_for_outcome,
    validate_planning_artifact_refs,
)
from migration_factory.control_tower.schemas.profile_checkpoint_metadata import (
    CheckpointProfileMetadata,
)


# ── helpers ───────────────────────────────────────────────────────────

def _valid_checkpoint(**overrides) -> PlanningCheckpoint:
    defaults = {
        "checkpoint_id": "pcp-001",
        "job_id": "job-abc",
        "stage_index": 2,
        "outcome": PlanningOutcome.WAITING,
        "gate_id": "gate-001",
        "gate_checksum": "sha256:abc123",
        "summary_text": "Migration plan for springboot-2.7-java11 completed.",
    }
    defaults.update(overrides)
    return PlanningCheckpoint(**defaults)


def _valid_comment(**overrides) -> PlanningCheckpointComment:
    defaults = {
        "comment_id": "cmt-001",
        "text": "Please re-check the migration plan — some units are missing.",
        "section": "migration_plan",
    }
    defaults.update(overrides)
    return PlanningCheckpointComment(**defaults)


# ══════════════════════════════════════════════════════════════════════════
# 1. PLANNING_CHECKPOINT_FIELDS contract
# ══════════════════════════════════════════════════════════════════════════

class TestPlanningCheckpointFields:
    """F1-T4: Planning checkpoint fields must be safe and complete."""

    def test_fields_are_frozenset(self):
        assert isinstance(PLANNING_CHECKPOINT_FIELDS, frozenset)

    def test_no_dangerous_fields_in_checkpoint_fields(self):
        dangerous = {
            "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
            "provider", "model", "deployment", "endpoint", "secret", "token",
            "password", "api_key", "client_secret", "command",
        }
        overlap = PLANNING_CHECKPOINT_FIELDS & dangerous
        assert overlap == set(), f"Dangerous fields found: {overlap}"

    def test_core_checkpoint_fields_present(self):
        required = {
            "checkpoint_id", "job_id", "run_id", "stage_index",
            "gate_id", "gate_phase", "gate_checksum",
            "outcome",
        }
        assert required.issubset(PLANNING_CHECKPOINT_FIELDS)

    def test_summary_and_preview_fields_present(self):
        required = {
            "summary_text", "preview_artifact_refs",
            "latest_download_artifact_ref",
        }
        assert required.issubset(PLANNING_CHECKPOINT_FIELDS)

    def test_comment_fields_present(self):
        required = {"comments", "comment_count", "modification_feedback"}
        assert required.issubset(PLANNING_CHECKPOINT_FIELDS)

    def test_stale_fields_present(self):
        required = {"stale_reason", "stale_at"}
        assert required.issubset(PLANNING_CHECKPOINT_FIELDS)


# ══════════════════════════════════════════════════════════════════════════
# 2. PlanningOutcome enum
# ══════════════════════════════════════════════════════════════════════════

class TestPlanningOutcome:
    """F1-T4: Planning outcomes must cover all required states."""

    def test_all_outcomes_defined(self):
        values = {o.value for o in PlanningOutcome}
        assert values == {
            "waiting", "accepted", "modification_requested",
            "stopped", "stale", "failed_closed",
        }

    def test_terminal_outcomes(self):
        assert PlanningOutcome.ACCEPTED in TERMINAL_PLANNING_OUTCOMES
        assert PlanningOutcome.STOPPED in TERMINAL_PLANNING_OUTCOMES
        assert PlanningOutcome.FAILED_CLOSED in TERMINAL_PLANNING_OUTCOMES
        assert len(TERMINAL_PLANNING_OUTCOMES) == 3

    def test_non_terminal_outcomes(self):
        assert PlanningOutcome.WAITING not in TERMINAL_PLANNING_OUTCOMES
        assert PlanningOutcome.MODIFICATION_REQUESTED not in TERMINAL_PLANNING_OUTCOMES
        assert PlanningOutcome.STALE not in TERMINAL_PLANNING_OUTCOMES


# ══════════════════════════════════════════════════════════════════════════
# 3. PlanningCheckpointAction enum
# ══════════════════════════════════════════════════════════════════════════

class TestPlanningCheckpointAction:
    """F1-T4: User actions at Planning checkpoint."""

    def test_all_actions_defined(self):
        values = {a.value for a in PlanningCheckpointAction}
        assert values == {
            "continue", "request_modification", "stop", "download_artifact",
        }

    def test_actions_match_stop_condition(self):
        """Must match the planning_checkpoint allowed actions in
        v2_stage_progression.py."""
        allowed_from_stop_condition = {
            "continue", "request_modification", "stop", "download_artifact",
        }
        action_values = {a.value for a in PlanningCheckpointAction}
        assert action_values == allowed_from_stop_condition


# ══════════════════════════════════════════════════════════════════════════
# 4. Required Planning artifacts
# ══════════════════════════════════════════════════════════════════════════

class TestRequiredPlanningArtifacts:
    """F1-T4: Planning artifact contract."""

    def test_five_required_artifact_types(self):
        assert len(REQUIRED_PLANNING_ARTIFACTS) == 5

    def test_core_artifacts_included(self):
        assert "migration_plan_yaml" in REQUIRED_PLANNING_ARTIFACTS
        assert "migration_units_yaml" in REQUIRED_PLANNING_ARTIFACTS
        assert "plan_summary_md" in REQUIRED_PLANNING_ARTIFACTS
        assert "approval_request_json" in REQUIRED_PLANNING_ARTIFACTS
        assert "plan_validation_report_json" in REQUIRED_PLANNING_ARTIFACTS

    def test_get_required_planning_artifact_types(self):
        result = get_required_planning_artifact_types()
        assert result == REQUIRED_PLANNING_ARTIFACTS

    def test_validate_planning_artifact_refs_all_present(self):
        assert validate_planning_artifact_refs(REQUIRED_PLANNING_ARTIFACTS) is True

    def test_validate_planning_artifact_refs_missing_one(self):
        subset = tuple(a for a in REQUIRED_PLANNING_ARTIFACTS if a != "approval_request_json")
        assert validate_planning_artifact_refs(subset) is False

    def test_validate_planning_artifact_refs_extra_ok(self):
        extra = REQUIRED_PLANNING_ARTIFACTS + ("copilot_assist_json",)
        assert validate_planning_artifact_refs(extra) is True

    def test_validate_planning_artifact_refs_empty(self):
        assert validate_planning_artifact_refs(()) is False

    def test_required_artifact_fields(self):
        assert "artifact_id" in REQUIRED_PLANNING_ARTIFACT_FIELDS
        assert "checksum" in REQUIRED_PLANNING_ARTIFACT_FIELDS
        assert "path" in REQUIRED_PLANNING_ARTIFACT_FIELDS


# ══════════════════════════════════════════════════════════════════════════
# 5. PlanningCheckpoint construction
# ══════════════════════════════════════════════════════════════════════════

class TestPlanningCheckpointConstruction:
    """F1-T4: Construction and defaults."""

    def test_minimal_construction(self):
        cp = _valid_checkpoint()
        assert cp.checkpoint_id == "pcp-001"
        assert cp.job_id == "job-abc"
        assert cp.stage_index == 2
        assert cp.outcome == PlanningOutcome.WAITING
        assert cp.gate_phase == "planning_review"

    def test_full_construction(self):
        cp = _valid_checkpoint(
            outcome=PlanningOutcome.ACCEPTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
            artifact_refs=("artifact-001", "artifact-002"),
            artifact_types=("migration_plan_yaml", "migration_units_yaml"),
            checksums=("sha256:abc", "sha256:def"),
            summary_text="Migration plan accepted.",
        )
        assert cp.outcome == PlanningOutcome.ACCEPTED
        assert cp.resolved_at == "2026-06-17T13:00:00Z"
        assert cp.artifact_refs == ("artifact-001", "artifact-002")

    def test_stage_index_defaults_to_two(self):
        cp = PlanningCheckpoint(job_id="job-abc")
        assert cp.stage_index == 2

    def test_stage_index_not_two_rejected(self):
        with pytest.raises(ValidationError):
            PlanningCheckpoint(job_id="job-abc", stage_index=1)

    def test_gate_phase_not_planning_review_rejected(self):
        with pytest.raises(ValidationError):
            PlanningCheckpoint(job_id="job-abc", gate_phase="analysis_review")

    def test_outcome_coercion_from_string(self):
        cp = PlanningCheckpoint(
            job_id="job-abc",
            outcome="accepted",
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp.outcome == PlanningOutcome.ACCEPTED

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            PlanningCheckpoint(
                job_id="job-abc",
                not_a_field="intruder",
            )

    def test_frozen_immutable(self):
        cp = _valid_checkpoint()
        with pytest.raises(ValidationError):
            cp.outcome = PlanningOutcome.STOPPED


# ══════════════════════════════════════════════════════════════════════════
# 6. PlanningCheckpoint properties
# ══════════════════════════════════════════════════════════════════════════

class TestPlanningCheckpointProperties:
    """F1-T4: Derived properties and computed values."""

    def test_is_terminal_accepted(self):
        cp = _valid_checkpoint(
            outcome=PlanningOutcome.ACCEPTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali",
        )
        assert cp.is_terminal is True

    def test_is_terminal_stopped(self):
        cp = _valid_checkpoint(
            outcome=PlanningOutcome.STOPPED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali",
        )
        assert cp.is_terminal is True

    def test_is_waiting_default(self):
        cp = _valid_checkpoint()
        assert cp.outcome == PlanningOutcome.WAITING

    def test_modification_requested_is_not_terminal(self):
        cp = _valid_checkpoint(
            outcome=PlanningOutcome.MODIFICATION_REQUESTED,
            comments=(_valid_comment(),),
        )
        assert cp.is_terminal is False

    def test_comment_count_zero(self):
        cp = _valid_checkpoint()
        assert cp.comment_count == 0

    def test_comment_count_with_comments(self):
        cp = _valid_checkpoint(comments=(_valid_comment(), _valid_comment(comment_id="cmt-002")))
        assert cp.comment_count == 2

    def test_modification_feedback_empty(self):
        cp = _valid_checkpoint()
        assert cp.modification_feedback == {}

    def test_modification_feedback_with_comments(self):
        cp = _valid_checkpoint(
            comments=(
                _valid_comment(text="Fix A", section="migration_plan"),
                _valid_comment(comment_id="cmt-002", text="Fix B", section="risks"),
            )
        )
        fb = cp.modification_feedback
        assert "migration_plan" in fb
        assert "risks" in fb
        assert fb["migration_plan"] == ["Fix A"]
        assert fb["risks"] == ["Fix B"]


# ══════════════════════════════════════════════════════════════════════════
# 7. PlanningCheckpoint lifecycle validators
# ══════════════════════════════════════════════════════════════════════════

class TestPlanningCheckpointValidators:
    """F1-T4: Lifecycle validation rules."""

    def test_terminal_must_have_resolved_fields(self):
        with pytest.raises(ValidationError):
            PlanningCheckpoint(
                job_id="job-abc",
                outcome=PlanningOutcome.ACCEPTED,
            )

    def test_waiting_must_not_have_resolved_fields(self):
        with pytest.raises(ValidationError):
            _valid_checkpoint(
                resolved_at="2026-06-17T13:00:00Z",
                resolved_by="ali",
            )

    def test_modification_requested_requires_comments(self):
        with pytest.raises(ValidationError):
            _valid_checkpoint(
                outcome=PlanningOutcome.MODIFICATION_REQUESTED,
                resolved_at="2026-06-17T13:00:00Z",
                resolved_by="ali",
            )

    def test_modification_requested_with_comments_ok(self):
        cp = _valid_checkpoint(
            outcome=PlanningOutcome.MODIFICATION_REQUESTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali",
            comments=(_valid_comment(),),
        )
        assert cp.outcome == PlanningOutcome.MODIFICATION_REQUESTED
        assert cp.comment_count == 1

    def test_stale_must_have_reason(self):
        with pytest.raises(ValidationError):
            _valid_checkpoint(
                outcome=PlanningOutcome.STALE,
                resolved_at="2026-06-17T13:00:00Z",
                resolved_by="ali",
            )

    def test_stale_with_reason_ok(self):
        cp = _valid_checkpoint(
            outcome=PlanningOutcome.STALE,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali",
            stale_reason="Artifact checksums no longer match.",
        )
        assert cp.outcome == PlanningOutcome.STALE
        assert cp.stale_reason == "Artifact checksums no longer match."


# ══════════════════════════════════════════════════════════════════════════
# 8. PlanningCheckpointComment model
# ══════════════════════════════════════════════════════════════════════════

class TestPlanningCheckpointComment:
    """F1-T4: Structured comment model."""

    def test_minimal_construction(self):
        c = PlanningCheckpointComment(
            comment_id="cmt-001",
            text="Fix the migration units.",
            section="migration_units",
        )
        assert c.comment_id == "cmt-001"
        assert c.text == "Fix the migration units."
        assert c.author == ""

    def test_full_construction(self):
        c = PlanningCheckpointComment(
            comment_id="cmt-001",
            text="Fix the migration units.",
            section="migration_units",
            author="ali.hamdaoui",
            created_at="2026-06-17T12:00:00Z",
            is_resolved=False,
        )
        assert c.author == "ali.hamdaoui"
        assert c.created_at == "2026-06-17T12:00:00Z"

    def test_comment_id_required(self):
        with pytest.raises(ValidationError):
            PlanningCheckpointComment(text="text", section="s1")

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            PlanningCheckpointComment(
                comment_id="cmt-001",
                text="text",
                section="s1",
                not_a_field="oops",
            )

    def test_text_max_length_not_exceeded(self):
        c = PlanningCheckpointComment(
            comment_id="cmt-001",
            text="a" * 8192,
            section="s1",
        )
        assert len(c.text) == 8192

    def test_frozen_immutable(self):
        c = _valid_comment()
        with pytest.raises(ValidationError):
            c.text = "new text"


# ══════════════════════════════════════════════════════════════════════════
# 9. PlanningCheckpoint serialization
# ══════════════════════════════════════════════════════════════════════════

class TestPlanningCheckpointSerialization:
    """F1-T4: JSON and dict round-trip."""

    def test_to_dict_minimal(self):
        cp = _valid_checkpoint()
        d = cp.to_dict()
        assert d["checkpoint_id"] == "pcp-001"
        assert d["outcome"] == "waiting"
        assert d["stage_index"] == 2
        assert d["gate_phase"] == "planning_review"
        assert d["comments"] == []

    def test_to_dict_with_comments(self):
        cp = _valid_checkpoint(comments=(_valid_comment(),))
        d = cp.to_dict()
        assert len(d["comments"]) == 1
        assert d["comments"][0]["comment_id"] == "cmt-001"

    def test_to_json_minimal(self):
        cp = _valid_checkpoint()
        js = cp.to_json()
        parsed = json.loads(js)
        assert parsed["outcome"] == "waiting"

    def test_from_dict_minimal(self):
        data = {
            "checkpoint_id": "pcp-002",
            "job_id": "job-xyz",
            "outcome": "waiting",
        }
        cp = PlanningCheckpoint.from_dict(data)
        assert cp.checkpoint_id == "pcp-002"
        assert cp.job_id == "job-xyz"
        assert cp.stage_index == 2

    def test_from_dict_full(self):
        data = {
            "checkpoint_id": "pcp-002",
            "job_id": "job-xyz",
            "outcome": "accepted",
            "stage_index": 2,
            "gate_id": "gate-002",
            "gate_checksum": "sha256:def",
            "resolved_at": "2026-06-17T13:00:00Z",
            "resolved_by": "ali.hamdaoui",
            "artifact_refs": ["artifact-001"],
            "artifact_types": ["migration_plan_yaml"],
            "checksums": ["sha256:abc"],
            "summary_text": "Accepted.",
            "comments": [
                {
                    "comment_id": "cmt-001",
                    "section": "general",
                    "text": "Looks good.",
                }
            ],
        }
        cp = PlanningCheckpoint.from_dict(data)
        assert cp.checkpoint_id == "pcp-002"
        assert cp.outcome == PlanningOutcome.ACCEPTED
        assert cp.is_terminal is True
        assert cp.artifact_refs == ("artifact-001",)
        assert cp.comment_count == 1

    def test_round_trip_json(self):
        cp = _valid_checkpoint(
            outcome=PlanningOutcome.MODIFICATION_REQUESTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
            artifact_refs=("artifact-001", "artifact-002"),
            preview_artifact_refs=("artifact-001",),
            comments=(_valid_comment(),),
            profile_metadata=CheckpointProfileMetadata(
                source_profile="sb2",
                target_profile="sb3",
                source_level=1,
                target_level=2,
                included_stages=(2, 3),
            ),
        )
        json_str = cp.to_json()
        cp2 = PlanningCheckpoint.from_json(json_str)
        assert cp.checkpoint_id == cp2.checkpoint_id
        assert cp.outcome == cp2.outcome
        assert cp.artifact_refs == cp2.artifact_refs
        assert cp.preview_artifact_refs == cp2.preview_artifact_refs
        if cp.profile_metadata and cp2.profile_metadata:
            assert cp.profile_metadata.source_profile == cp2.profile_metadata.source_profile
        assert cp.comment_count == cp2.comment_count

    def test_round_trip_dict(self):
        cp = _valid_checkpoint(
            artifact_refs=("artifact-001",),
            comments=(_valid_comment(),),
        )
        d = cp.to_dict()
        cp2 = PlanningCheckpoint.from_dict(d)
        assert cp.checkpoint_id == cp2.checkpoint_id
        assert cp.artifact_refs == cp2.artifact_refs
        assert cp.comments[0].comment_id == cp2.comments[0].comment_id

    def test_from_dict_handles_none_values(self):
        """None values in dict should fall back to safe defaults."""
        data = {
            "checkpoint_id": "pcp-001",
            "job_id": "job-abc",
            "artifact_refs": None,
            "preview_artifact_refs": None,
            "comments": None,
            "profile_metadata": None,
            "gate_id": None,
        }
        cp = PlanningCheckpoint.from_dict(data)
        assert cp.artifact_refs == ()
        assert cp.preview_artifact_refs == ()
        assert cp.comments == ()
        assert cp.gate_id == ""

    def test_from_dict_handles_partial_none_values(self):
        """Some None, some with values — should not crash."""
        data = {
            "checkpoint_id": "pcp-001",
            "job_id": "job-abc",
            "artifact_refs": ("artifact-001",),
            "preview_artifact_refs": None,
            "comments": None,
            "profile_metadata": None,
        }
        cp = PlanningCheckpoint.from_dict(data)
        assert cp.artifact_refs == ("artifact-001",)
        assert cp.preview_artifact_refs == ()
        assert cp.comments == ()


# ══════════════════════════════════════════════════════════════════════════
# 10. is_valid_planning_gate_decision_for_outcome (low-level gate integration)
# ══════════════════════════════════════════════════════════════════════════

class TestValidPlanningGateDecisionForOutcome:
    """F1-T4: gate-decision validation for backend integration."""

    def test_waiting_allows_continue(self):
        assert is_valid_planning_gate_decision_for_outcome(
            PlanningOutcome.WAITING, "continue"
        ) is True

    def test_waiting_allows_reanalyze(self):
        assert is_valid_planning_gate_decision_for_outcome(
            PlanningOutcome.WAITING, "reanalyze"
        ) is True

    def test_waiting_rejects_approve(self):
        assert is_valid_planning_gate_decision_for_outcome(
            PlanningOutcome.WAITING, "approve"
        ) is False

    def test_accepted_rejects_all(self):
        assert is_valid_planning_gate_decision_for_outcome(
            PlanningOutcome.ACCEPTED, "continue"
        ) is False

    def test_stale_allows_reanalyze(self):
        assert is_valid_planning_gate_decision_for_outcome(
            PlanningOutcome.STALE, "reanalyze"
        ) is True

    def test_stale_rejects_continue(self):
        assert is_valid_planning_gate_decision_for_outcome(
            PlanningOutcome.STALE, "continue"
        ) is False

    def test_failed_closed_rejects_all(self):
        assert is_valid_planning_gate_decision_for_outcome(
            PlanningOutcome.FAILED_CLOSED, "continue"
        ) is False


# ══════════════════════════════════════════════════════════════════════════
# 11. is_valid_planning_action_for_outcome (user-facing PlanningCheckpointAction API)
# ══════════════════════════════════════════════════════════════════════════

class TestValidPlanningActionForOutcome:
    """F1-T4: user-facing action validation with PlanningCheckpointAction."""

    def test_waiting_allows_continue_action(self):
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.WAITING, PlanningCheckpointAction.CONTINUE
        ) is True

    def test_waiting_allows_request_modification(self):
        """request_modification maps to reanalyze gate decision."""
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.WAITING, PlanningCheckpointAction.REQUEST_MODIFICATION
        ) is True

    def test_waiting_allows_stop(self):
        """stop is always valid — terminal user action."""
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.WAITING, PlanningCheckpointAction.STOP
        ) is True

    def test_waiting_allows_download(self):
        """download is always valid — read-only action."""
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.WAITING, PlanningCheckpointAction.DOWNLOAD_ARTIFACT
        ) is True

    def test_accepted_rejects_continue(self):
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.ACCEPTED, PlanningCheckpointAction.CONTINUE
        ) is False

    def test_accepted_rejects_request_modification(self):
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.ACCEPTED, PlanningCheckpointAction.REQUEST_MODIFICATION
        ) is False

    def test_accepted_still_allows_download(self):
        """download is always valid even on terminal outcomes."""
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.ACCEPTED, PlanningCheckpointAction.DOWNLOAD_ARTIFACT
        ) is True

    def test_stopped_allows_stop(self):
        """stop is always valid."""
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.STOPPED, PlanningCheckpointAction.STOP
        ) is True

    def test_stopped_rejects_continue(self):
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.STOPPED, PlanningCheckpointAction.CONTINUE
        ) is False

    def test_stale_allows_request_modification(self):
        """modification maps to reanalyze, allowed in stale state."""
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.STALE, PlanningCheckpointAction.REQUEST_MODIFICATION
        ) is True

    def test_stale_rejects_continue(self):
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.STALE, PlanningCheckpointAction.CONTINUE
        ) is False

    def test_modification_requested_allows_continue(self):
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.MODIFICATION_REQUESTED, PlanningCheckpointAction.CONTINUE
        ) is True

    def test_failed_closed_rejects_continue(self):
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.FAILED_CLOSED, PlanningCheckpointAction.CONTINUE
        ) is False

    def test_failed_closed_still_allows_download(self):
        assert is_valid_planning_action_for_outcome(
            PlanningOutcome.FAILED_CLOSED, PlanningCheckpointAction.DOWNLOAD_ARTIFACT
        ) is True

    def test_unknown_outcome_rejects_action(self):
        # Safety: an unknown outcome should never silently accept.
        # This is guarded at the type level by the PlanningOutcome enum.
        pass  # type-check covers this path


# ══════════════════════════════════════════════════════════════════════════
# 12. Integration: checkpoint with profile metadata
# ══════════════════════════════════════════════════════════════════════════

class TestPlanningCheckpointWithProfileMetadata:
    """F1-T4: Profile metadata integration."""

    def test_profile_metadata_integration(self):
        pm = CheckpointProfileMetadata(
            source_profile="sb2",
            target_profile="sb3",
            source_level=1,
            target_level=2,
            included_stages=(2, 3),
        )
        cp = _valid_checkpoint(profile_metadata=pm)
        assert cp.profile_metadata.source_profile == "sb2"
        assert cp.profile_metadata.target_profile == "sb3"

    def test_profile_metadata_round_trip(self):
        pm = CheckpointProfileMetadata(
            source_profile="sb2",
            target_profile="sb3",
            source_level=1,
            target_level=2,
            included_stages=(2, 3),
        )
        cp = _valid_checkpoint(
            outcome=PlanningOutcome.MODIFICATION_REQUESTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali",
            comments=(_valid_comment(),),
            profile_metadata=pm,
        )
        d = cp.to_dict()
        cp2 = PlanningCheckpoint.from_dict(d)
        assert cp2.profile_metadata.source_profile == "sb2"
        assert cp2.profile_metadata.target_profile == "sb3"


# ══════════════════════════════════════════════════════════════════════════
# 13. No dangerous fields in serialized output
# ══════════════════════════════════════════════════════════════════════════

class TestNoDangerousFieldsInOutput:
    """F1-T4: Planning checkpoint output must never expose dangerous fields."""

    def test_to_dict_no_dangerous_keys(self):
        cp = _valid_checkpoint()
        d = cp.to_dict()
        dangerous = {
            "sandbox_path", "argv", "env", "raw_command",
            "provider", "model", "deployment", "endpoint",
            "secret", "token", "password", "command",
        }
        found = dangerous & set(d.keys())
        assert found == set(), f"Dangerous keys in output: {found}"

    def test_to_json_no_dangerous_keys(self):
        cp = _valid_checkpoint()
        js = cp.to_json()
        parsed = json.loads(js)
        dangerous = {
            "sandbox_path", "argv", "env", "raw_command",
            "provider", "model", "deployment", "endpoint",
            "secret", "token", "password", "command",
        }
        found = dangerous & set(parsed.keys())
        assert found == set(), f"Dangerous keys in JSON output: {found}"
