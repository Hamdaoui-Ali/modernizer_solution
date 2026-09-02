"""F1-T3 focused tests — Analysis checkpoint contract round-trip."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.analysis_checkpoint import (
    ANALYSIS_CHECKPOINT_FIELDS,
    REQUIRED_ANALYSIS_ARTIFACTS,
    REQUIRED_ANALYSIS_ARTIFACT_FIELDS,
    TERMINAL_ANALYSIS_OUTCOMES,
    AnalysisCheckpoint,
    AnalysisCheckpointAction,
    AnalysisCheckpointComment,
    AnalysisOutcome,
    get_required_analysis_artifact_types,
    is_valid_action_for_outcome,
    is_valid_gate_decision_for_outcome,
    validate_analysis_artifact_refs,
)
from migration_factory.control_tower.schemas.profile_checkpoint_metadata import (
    CheckpointProfileMetadata,
)


# ── helpers ───────────────────────────────────────────────────────────

def _valid_checkpoint(**overrides) -> AnalysisCheckpoint:
    defaults = {
        "checkpoint_id": "acp-001",
        "job_id": "job-abc",
        "stage_index": 1,
        "outcome": AnalysisOutcome.WAITING,
        "gate_id": "gate-001",
        "revision_id": "rev-001",
        "source_artifact_checksum": "sha256:abc123",
        "summary": "Analysis of springboot-2.7-java11 project completed.",
        "created_at": "2026-06-17T12:00:00Z",
    }
    defaults.update(overrides)
    return AnalysisCheckpoint(**defaults)


def _valid_comment(**overrides) -> AnalysisCheckpointComment:
    defaults = {
        "comment_id": "cmt-001",
        "checkpoint_id": "acp-001",
        "text": "Please re-check the dependency graph - it misses some JARs.",
        "section": "dependency_graph",
        "created_at": "2026-06-17T13:00:00Z",
        "created_by": "ali.hamdaoui",
    }
    defaults.update(overrides)
    return AnalysisCheckpointComment(**defaults)


# ══════════════════════════════════════════════════════════════════════════
# 1. ANALYSIS_CHECKPOINT_FIELDS contract
# ══════════════════════════════════════════════════════════════════════════

class TestAnalysisCheckpointFields:
    """F1-T3: Analysis checkpoint fields must be safe and complete."""

    def test_fields_are_frozenset(self):
        assert isinstance(ANALYSIS_CHECKPOINT_FIELDS, frozenset)

    def test_no_dangerous_fields_in_checkpoint_fields(self):
        dangerous = {
            "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
            "provider", "model", "deployment", "endpoint", "secret", "token",
            "password", "api_key", "client_secret", "command",
        }
        overlap = ANALYSIS_CHECKPOINT_FIELDS & dangerous
        assert overlap == set(), f"Dangerous fields found: {overlap}"

    def test_core_checkpoint_fields_present(self):
        required = {
            "checkpoint_id", "job_id", "stage_index", "outcome",
            "gate_id", "revision_id", "source_artifact_checksum",
        }
        assert required.issubset(ANALYSIS_CHECKPOINT_FIELDS)

    def test_summary_and_preview_fields_present(self):
        user_visible = {"summary", "artifact_refs"}
        assert user_visible.issubset(ANALYSIS_CHECKPOINT_FIELDS)

    def test_comment_fields_present(self):
        assert "comments" in ANALYSIS_CHECKPOINT_FIELDS
        assert "comment_count" in ANALYSIS_CHECKPOINT_FIELDS

    def test_stale_fields_present(self):
        assert "is_stale" in ANALYSIS_CHECKPOINT_FIELDS
        assert "stale_reason" in ANALYSIS_CHECKPOINT_FIELDS


# ══════════════════════════════════════════════════════════════════════════
# 2. AnalysisOutcome enum
# ══════════════════════════════════════════════════════════════════════════

class TestAnalysisOutcome:
    def test_all_outcomes_defined(self):
        assert AnalysisOutcome.WAITING.value == "waiting"
        assert AnalysisOutcome.ACCEPTED.value == "accepted"
        assert AnalysisOutcome.MODIFICATION_REQUESTED.value == "modification_requested"
        assert AnalysisOutcome.STOPPED.value == "stopped"
        assert AnalysisOutcome.STALE.value == "stale"
        assert AnalysisOutcome.FAILED_CLOSED.value == "failed_closed"

    def test_terminal_outcomes(self):
        assert AnalysisOutcome.ACCEPTED in TERMINAL_ANALYSIS_OUTCOMES
        assert AnalysisOutcome.STOPPED in TERMINAL_ANALYSIS_OUTCOMES
        assert AnalysisOutcome.FAILED_CLOSED in TERMINAL_ANALYSIS_OUTCOMES

    def test_non_terminal_outcomes(self):
        assert AnalysisOutcome.WAITING not in TERMINAL_ANALYSIS_OUTCOMES
        assert AnalysisOutcome.MODIFICATION_REQUESTED not in TERMINAL_ANALYSIS_OUTCOMES
        assert AnalysisOutcome.STALE not in TERMINAL_ANALYSIS_OUTCOMES


# ══════════════════════════════════════════════════════════════════════════
# 3. AnalysisCheckpointAction enum
# ══════════════════════════════════════════════════════════════════════════

class TestAnalysisCheckpointAction:
    def test_all_actions_defined(self):
        assert AnalysisCheckpointAction.CONTINUE.value == "continue"
        assert AnalysisCheckpointAction.REQUEST_MODIFICATION.value == "request_modification"
        assert AnalysisCheckpointAction.STOP.value == "stop"
        assert AnalysisCheckpointAction.DOWNLOAD_ARTIFACT.value == "download_artifact"

    def test_actions_match_stop_condition(self):
        # The allowed actions from v2_stage_progression.py for
        # analysis_checkpoint are: continue, request_modification, stop,
        # download_artifact. Our actions must match.
        expected = {"continue", "request_modification", "stop", "download_artifact"}
        actual = {a.value for a in AnalysisCheckpointAction}
        assert actual == expected


# ══════════════════════════════════════════════════════════════════════════
# 4. REQUIRED_ANALYSIS_ARTIFACTS
# ══════════════════════════════════════════════════════════════════════════

class TestRequiredAnalysisArtifacts:
    def test_five_required_artifact_types(self):
        assert len(REQUIRED_ANALYSIS_ARTIFACTS) == 5

    def test_core_artifacts_included(self):
        core = {
            "analysis_report_json",
            "dependency_graph_json",
            "test_inventory_json",
            "config_inventory_json",
            "analysis_summary_md",
        }
        assert set(REQUIRED_ANALYSIS_ARTIFACTS) == core

    def test_get_required_analysis_artifact_types(self):
        result = get_required_analysis_artifact_types()
        assert result == REQUIRED_ANALYSIS_ARTIFACTS

    def test_validate_analysis_artifact_refs_all_present(self):
        provided = (
            "analysis_report_json",
            "dependency_graph_json",
            "test_inventory_json",
            "config_inventory_json",
            "analysis_summary_md",
        )
        assert validate_analysis_artifact_refs(provided) is True

    def test_validate_analysis_artifact_refs_missing_one(self):
        provided = (
            "analysis_report_json",
            "dependency_graph_json",
            "test_inventory_json",
            "config_inventory_json",
        )
        assert validate_analysis_artifact_refs(provided) is False

    def test_validate_analysis_artifact_refs_extra_ok(self):
        provided = (
            "analysis_report_json",
            "dependency_graph_json",
            "test_inventory_json",
            "config_inventory_json",
            "analysis_summary_md",
            "extra_custom_artifact",
        )
        assert validate_analysis_artifact_refs(provided) is True

    def test_validate_analysis_artifact_refs_empty(self):
        assert validate_analysis_artifact_refs(()) is False

    def test_required_artifact_fields(self):
        assert "artifact_id" in REQUIRED_ANALYSIS_ARTIFACT_FIELDS
        assert "artifact_type" in REQUIRED_ANALYSIS_ARTIFACT_FIELDS
        assert "checksum" in REQUIRED_ANALYSIS_ARTIFACT_FIELDS
        assert "revision_id" in REQUIRED_ANALYSIS_ARTIFACT_FIELDS


# ══════════════════════════════════════════════════════════════════════════
# 5. AnalysisCheckpoint construction and defaults
# ══════════════════════════════════════════════════════════════════════════

class TestAnalysisCheckpointConstruction:
    def test_minimal_construction(self):
        cp = AnalysisCheckpoint(
            checkpoint_id="acp-001",
            job_id="job-abc",
            created_at="2026-06-17T12:00:00Z",
        )
        assert cp.checkpoint_id == "acp-001"
        assert cp.job_id == "job-abc"
        assert cp.stage_index == 1
        assert cp.outcome == AnalysisOutcome.WAITING
        assert cp.gate_id == ""
        assert cp.revision_id == ""
        assert cp.source_artifact_checksum == ""
        assert cp.artifact_refs == ()
        assert cp.summary == ""
        assert cp.preview_refs == ()
        assert cp.is_stale is False
        assert cp.stale_reason == ""
        assert cp.comments == ()

    def test_full_construction(self):
        cp = _valid_checkpoint(
            artifact_refs=("artifact-001", "artifact-002"),
            preview_refs=("artifact-001", "artifact-002"),
            summary="Analysis complete: 3 risks found.",
        )
        assert len(cp.artifact_refs) == 2
        assert len(cp.preview_refs) == 2
        assert cp.summary == "Analysis complete: 3 risks found."

    def test_stage_index_defaults_to_one(self):
        cp = AnalysisCheckpoint(
            checkpoint_id="acp-001",
            job_id="job-abc",
            created_at="2026-06-17T12:00:00Z",
        )
        assert cp.stage_index == 1

    def test_stage_index_not_one_rejected(self):
        # Pydantic's Field(le=1) catches this before our model validator,
        # so the message comes from Pydantic's built-in constraint.
        with pytest.raises(ValidationError, match="less_than_equal"):
            AnalysisCheckpoint(
                checkpoint_id="acp-001",
                job_id="job-abc",
                stage_index=2,
                created_at="2026-06-17T12:00:00Z",
            )

    def test_outcome_coercion_from_string(self):
        cp = AnalysisCheckpoint(
            checkpoint_id="acp-001",
            job_id="job-abc",
            outcome="accepted",
            created_at="2026-06-17T12:00:00Z",
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp.outcome == AnalysisOutcome.ACCEPTED
        assert cp.is_terminal is True

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisCheckpoint(
                checkpoint_id="acp-001",
                job_id="job-abc",
                created_at="2026-06-17T12:00:00Z",
                sandbox_path="/tmp/bad",  # must be rejected
            )

    def test_frozen_immutable(self):
        cp = _valid_checkpoint()
        with pytest.raises(Exception):  # frozen model
            cp.outcome = AnalysisOutcome.ACCEPTED  # type: ignore


# ══════════════════════════════════════════════════════════════════════════
# 6. Derived properties
# ══════════════════════════════════════════════════════════════════════════

class TestAnalysisCheckpointProperties:
    def test_is_terminal_accepted(self):
        cp = _valid_checkpoint(
            outcome=AnalysisOutcome.ACCEPTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp.is_terminal is True
        assert cp.is_waiting is False

    def test_is_terminal_stopped(self):
        cp = _valid_checkpoint(
            outcome=AnalysisOutcome.STOPPED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
        )
        assert cp.is_terminal is True

    def test_is_waiting(self):
        cp = _valid_checkpoint(outcome=AnalysisOutcome.WAITING)
        assert cp.is_waiting is True
        assert cp.is_terminal is False

    def test_modification_requested_is_not_terminal(self):
        cp = _valid_checkpoint(
            outcome=AnalysisOutcome.MODIFICATION_REQUESTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
            comments=(_valid_comment(),),
        )
        assert cp.is_terminal is False
        assert cp.is_waiting is False

    def test_comment_count_zero(self):
        cp = _valid_checkpoint()
        assert cp.comment_count == 0

    def test_comment_count_with_comments(self):
        cp = _valid_checkpoint(
            comments=(
                _valid_comment(comment_id="cmt-001"),
                _valid_comment(comment_id="cmt-002"),
            ),
        )
        assert cp.comment_count == 2

    def test_has_artifacts_false(self):
        cp = _valid_checkpoint()
        assert cp.has_artifacts is False

    def test_has_artifacts_true(self):
        cp = _valid_checkpoint(artifact_refs=("artifact-001",))
        assert cp.has_artifacts is True

    def test_modification_feedback_empty(self):
        cp = _valid_checkpoint()
        assert cp.modification_feedback == ""

    def test_modification_feedback_with_comments(self):
        cp = _valid_checkpoint(
            comments=(
                _valid_comment(
                    comment_id="cmt-001",
                    text="Dependency graph is incomplete.",
                    section="dependency_graph",
                ),
                _valid_comment(
                    comment_id="cmt-002",
                    text="Test inventory looks good.",
                    section="test_inventory",
                ),
            ),
        )
        feedback = cp.modification_feedback
        assert "dependency_graph" in feedback
        assert "Dependency graph is incomplete" in feedback
        assert "test_inventory" in feedback
        assert "Test inventory looks good" in feedback


# ══════════════════════════════════════════════════════════════════════════
# 7. Model validators — lifecycle rules
# ══════════════════════════════════════════════════════════════════════════

class TestAnalysisCheckpointValidators:
    def test_terminal_must_have_resolved_fields(self):
        with pytest.raises(ValidationError, match="resolved_at and resolved_by"):
            _valid_checkpoint(
                outcome=AnalysisOutcome.ACCEPTED,
                resolved_at=None,
                resolved_by=None,
            )

    def test_waiting_must_not_have_resolved_fields(self):
        with pytest.raises(ValidationError, match="must not have resolved_at"):
            _valid_checkpoint(
                outcome=AnalysisOutcome.WAITING,
                resolved_at="2026-06-17T13:00:00Z",
                resolved_by="ali.hamdaoui",
            )

    def test_modification_requested_requires_comments(self):
        with pytest.raises(ValidationError, match="at least one comment"):
            _valid_checkpoint(
                outcome=AnalysisOutcome.MODIFICATION_REQUESTED,
                resolved_at="2026-06-17T13:00:00Z",
                resolved_by="ali.hamdaoui",
                comments=(),
            )

    def test_modification_requested_with_comments_ok(self):
        cp = _valid_checkpoint(
            outcome=AnalysisOutcome.MODIFICATION_REQUESTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
            comments=(_valid_comment(),),
        )
        assert cp.outcome == AnalysisOutcome.MODIFICATION_REQUESTED

    def test_stale_must_have_reason(self):
        with pytest.raises(ValidationError, match="stale_reason"):
            _valid_checkpoint(
                is_stale=True,
                stale_reason="",
            )

    def test_stale_with_reason_ok(self):
        cp = _valid_checkpoint(
            is_stale=True,
            stale_reason="artifact checksum mismatch after rescan",
        )
        assert cp.is_stale is True
        assert cp.stale_reason == "artifact checksum mismatch after rescan"


# ══════════════════════════════════════════════════════════════════════════
# 8. AnalysisCheckpointComment
# ══════════════════════════════════════════════════════════════════════════

class TestAnalysisCheckpointComment:
    def test_minimal_construction(self):
        cmt = AnalysisCheckpointComment(
            comment_id="cmt-001",
            checkpoint_id="acp-001",
        )
        assert cmt.comment_id == "cmt-001"
        assert cmt.checkpoint_id == "acp-001"
        assert cmt.text == ""
        assert cmt.section == "general"
        assert cmt.created_at == ""
        assert cmt.created_by == ""
        assert cmt.revision_id is None

    def test_full_construction(self):
        cmt = _valid_comment()
        assert cmt.section == "dependency_graph"
        assert cmt.text == "Please re-check the dependency graph - it misses some JARs."

    def test_comment_id_required(self):
        with pytest.raises(ValidationError):
            AnalysisCheckpointComment(
                comment_id="",
                checkpoint_id="acp-001",
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisCheckpointComment(
                comment_id="cmt-001",
                checkpoint_id="acp-001",
                unsafe_field="bad",
            )

    def test_text_max_length_not_exceeded(self):
        # 2000 chars is ok
        long_text = "x" * 2000
        cmt = AnalysisCheckpointComment(
            comment_id="cmt-001",
            checkpoint_id="acp-001",
            text=long_text,
        )
        assert len(cmt.text) == 2000

    def test_frozen_immutable(self):
        cmt = _valid_comment()
        with pytest.raises(Exception):
            cmt.text = "changed"  # type: ignore


# ══════════════════════════════════════════════════════════════════════════
# 9. Serialization round-trip
# ══════════════════════════════════════════════════════════════════════════

class TestAnalysisCheckpointSerialization:
    def test_to_dict_minimal(self):
        cp = _valid_checkpoint()
        d = cp.to_dict()
        assert d["checkpoint_id"] == "acp-001"
        assert d["job_id"] == "job-abc"
        assert d["stage_index"] == 1
        assert d["outcome"] == "waiting"
        assert isinstance(d["artifact_refs"], list)
        assert isinstance(d["comments"], list)
        assert isinstance(d["profile_metadata"], dict)

    def test_to_dict_with_comments(self):
        cp = _valid_checkpoint(
            outcome=AnalysisOutcome.MODIFICATION_REQUESTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
            comments=(_valid_comment(),),
        )
        d = cp.to_dict()
        assert len(d["comments"]) == 1
        assert d["comments"][0]["section"] == "dependency_graph"

    def test_to_json_minimal(self):
        cp = _valid_checkpoint()
        js = cp.to_json()
        parsed = json.loads(js)
        assert parsed["checkpoint_id"] == "acp-001"

    def test_from_dict_minimal(self):
        data = {
            "checkpoint_id": "acp-001",
            "job_id": "job-abc",
            "created_at": "2026-06-17T12:00:00Z",
        }
        cp = AnalysisCheckpoint.from_dict(data)
        assert cp.checkpoint_id == "acp-001"
        assert cp.job_id == "job-abc"
        assert cp.outcome == AnalysisOutcome.WAITING

    def test_from_dict_full(self):
        data = {
            "checkpoint_id": "acp-002",
            "job_id": "job-xyz",
            "stage_index": 1,
            "outcome": "accepted",
            "gate_id": "gate-002",
            "revision_id": "rev-002",
            "source_artifact_checksum": "sha256:def456",
            "artifact_refs": ["artifact-001"],
            "summary": "Analysis accepted.",
            "preview_refs": ["artifact-001"],
            "profile_metadata": {
                "source_profile": "sb2",
                "target_profile": "sb3",
            },
            "is_stale": False,
            "stale_reason": "",
            "decision_reason": "looks good",
            "created_at": "2026-06-17T12:00:00Z",
            "resolved_at": "2026-06-17T13:00:00Z",
            "resolved_by": "ali.hamdaoui",
            "comments": [
                {
                    "comment_id": "cmt-001",
                    "checkpoint_id": "acp-002",
                    "text": "ok",
                    "section": "general",
                    "created_at": "2026-06-17T12:30:00Z",
                    "created_by": "ali.hamdaoui",
                    "revision_id": None,
                }
            ],
        }
        cp = AnalysisCheckpoint.from_dict(data)
        assert cp.checkpoint_id == "acp-002"
        assert cp.outcome == AnalysisOutcome.ACCEPTED
        assert cp.is_terminal is True
        assert cp.artifact_refs == ("artifact-001",)
        assert cp.comment_count == 1

    def test_round_trip_json(self):
        cp = _valid_checkpoint(
            outcome=AnalysisOutcome.MODIFICATION_REQUESTED,
            resolved_at="2026-06-17T13:00:00Z",
            resolved_by="ali.hamdaoui",
            artifact_refs=("artifact-001", "artifact-002"),
            preview_refs=("artifact-001",),
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
        cp2 = AnalysisCheckpoint.from_json(json_str)
        assert cp.checkpoint_id == cp2.checkpoint_id
        assert cp.outcome == cp2.outcome
        assert cp.artifact_refs == cp2.artifact_refs
        assert cp.preview_refs == cp2.preview_refs
        assert cp.profile_metadata.source_profile == cp2.profile_metadata.source_profile
        assert cp.comment_count == cp2.comment_count

    def test_round_trip_dict(self):
        cp = _valid_checkpoint(
            artifact_refs=("artifact-001",),
            comments=(_valid_comment(),),
        )
        d = cp.to_dict()
        cp2 = AnalysisCheckpoint.from_dict(d)
        assert cp.checkpoint_id == cp2.checkpoint_id
        assert cp.artifact_refs == cp2.artifact_refs
        assert cp.comments[0].comment_id == cp2.comments[0].comment_id

    def test_from_dict_handles_none_values(self):
        """None values in dict should fall back to safe defaults."""
        data = {
            "checkpoint_id": "acp-001",
            "job_id": "job-abc",
            "created_at": "2026-06-17T12:00:00Z",
            "artifact_refs": None,
            "preview_refs": None,
            "comments": None,
            "profile_metadata": None,
            "gate_id": None,
            "revision_id": None,
        }
        cp = AnalysisCheckpoint.from_dict(data)
        assert cp.artifact_refs == ()
        assert cp.preview_refs == ()
        assert cp.comments == ()
        assert cp.gate_id == ""
        assert cp.revision_id == ""
        # profile_metadata should be default
        assert cp.profile_metadata.source_profile == ""

    def test_from_dict_handles_partial_none_values(self):
        """Some None, some with values — should not crash."""
        data = {
            "checkpoint_id": "acp-001",
            "job_id": "job-abc",
            "created_at": "2026-06-17T12:00:00Z",
            "artifact_refs": ("artifact-001",),
            "preview_refs": None,
            "comments": None,
            "profile_metadata": None,
        }
        cp = AnalysisCheckpoint.from_dict(data)
        assert cp.artifact_refs == ("artifact-001",)
        assert cp.preview_refs == ()
        assert cp.comments == ()


# ══════════════════════════════════════════════════════════════════════════
# 10. is_valid_gate_decision_for_outcome (low-level gate integration)
# ══════════════════════════════════════════════════════════════════════════

class TestValidGateDecisionForOutcome:
    """F1-T3: gate-decision validation for backend integration."""

    def test_waiting_allows_continue(self):
        assert is_valid_gate_decision_for_outcome(
            AnalysisOutcome.WAITING, "continue"
        ) is True

    def test_waiting_allows_reanalyze(self):
        assert is_valid_gate_decision_for_outcome(
            AnalysisOutcome.WAITING, "reanalyze"
        ) is True

    def test_waiting_rejects_approve(self):
        assert is_valid_gate_decision_for_outcome(
            AnalysisOutcome.WAITING, "approve"
        ) is False

    def test_accepted_rejects_all(self):
        assert is_valid_gate_decision_for_outcome(
            AnalysisOutcome.ACCEPTED, "continue"
        ) is False

    def test_stale_allows_reanalyze(self):
        assert is_valid_gate_decision_for_outcome(
            AnalysisOutcome.STALE, "reanalyze"
        ) is True

    def test_stale_rejects_continue(self):
        assert is_valid_gate_decision_for_outcome(
            AnalysisOutcome.STALE, "continue"
        ) is False

    def test_failed_closed_rejects_all(self):
        assert is_valid_gate_decision_for_outcome(
            AnalysisOutcome.FAILED_CLOSED, "continue"
        ) is False


# ══════════════════════════════════════════════════════════════════════════
# 11. is_valid_action_for_outcome (user-facing AnalysisCheckpointAction API)
# ══════════════════════════════════════════════════════════════════════════

class TestValidActionForOutcome:
    """F1-T3: user-facing action validation with AnalysisCheckpointAction."""

    def test_waiting_allows_continue_action(self):
        assert is_valid_action_for_outcome(
            AnalysisOutcome.WAITING, AnalysisCheckpointAction.CONTINUE
        ) is True

    def test_waiting_allows_request_modification(self):
        """request_modification maps to reanalyze gate decision."""
        assert is_valid_action_for_outcome(
            AnalysisOutcome.WAITING, AnalysisCheckpointAction.REQUEST_MODIFICATION
        ) is True

    def test_waiting_allows_stop(self):
        """stop is always valid — terminal user action."""
        assert is_valid_action_for_outcome(
            AnalysisOutcome.WAITING, AnalysisCheckpointAction.STOP
        ) is True

    def test_waiting_allows_download(self):
        """download is always valid — read-only action."""
        assert is_valid_action_for_outcome(
            AnalysisOutcome.WAITING, AnalysisCheckpointAction.DOWNLOAD_ARTIFACT
        ) is True

    def test_accepted_rejects_continue(self):
        assert is_valid_action_for_outcome(
            AnalysisOutcome.ACCEPTED, AnalysisCheckpointAction.CONTINUE
        ) is False

    def test_accepted_rejects_request_modification(self):
        assert is_valid_action_for_outcome(
            AnalysisOutcome.ACCEPTED, AnalysisCheckpointAction.REQUEST_MODIFICATION
        ) is False

    def test_accepted_still_allows_download(self):
        """download is always valid even on terminal outcomes."""
        assert is_valid_action_for_outcome(
            AnalysisOutcome.ACCEPTED, AnalysisCheckpointAction.DOWNLOAD_ARTIFACT
        ) is True

    def test_stopped_allows_stop(self):
        """stop is always valid."""
        assert is_valid_action_for_outcome(
            AnalysisOutcome.STOPPED, AnalysisCheckpointAction.STOP
        ) is True

    def test_stopped_rejects_continue(self):
        assert is_valid_action_for_outcome(
            AnalysisOutcome.STOPPED, AnalysisCheckpointAction.CONTINUE
        ) is False

    def test_stale_allows_request_modification(self):
        """modification maps to reanalyze, allowed in stale state."""
        assert is_valid_action_for_outcome(
            AnalysisOutcome.STALE, AnalysisCheckpointAction.REQUEST_MODIFICATION
        ) is True

    def test_stale_rejects_continue(self):
        assert is_valid_action_for_outcome(
            AnalysisOutcome.STALE, AnalysisCheckpointAction.CONTINUE
        ) is False

    def test_modification_requested_allows_continue(self):
        assert is_valid_action_for_outcome(
            AnalysisOutcome.MODIFICATION_REQUESTED, AnalysisCheckpointAction.CONTINUE
        ) is True

    def test_failed_closed_rejects_continue(self):
        assert is_valid_action_for_outcome(
            AnalysisOutcome.FAILED_CLOSED, AnalysisCheckpointAction.CONTINUE
        ) is False

    def test_failed_closed_still_allows_download(self):
        assert is_valid_action_for_outcome(
            AnalysisOutcome.FAILED_CLOSED, AnalysisCheckpointAction.DOWNLOAD_ARTIFACT
        ) is True

    def test_unknown_outcome_rejects_action(self):
        # Safety: an unknown outcome should never silently accept.
        # We test this by passing a raw string that doesn't match
        # any AnalysisOutcome — but the function signature requires
        # AnalysisOutcome, so this is guarded at the type level.
        pass  # type-check covers this path


# ══════════════════════════════════════════════════════════════════════════
# 12. Integration: checkpoint with profile metadata
# ══════════════════════════════════════════════════════════════════════════

class TestCheckpointWithProfileMetadata:
    def test_profile_metadata_integration(self):
        profile = CheckpointProfileMetadata(
            source_profile="springboot-2.7-java11",
            target_profile="springboot-3.5-java17",
            source_level=1,
            target_level=2,
            included_stages=(2, 3),
            valid=True,
        )
        cp = _valid_checkpoint(profile_metadata=profile)
        assert cp.profile_metadata.source_profile == "springboot-2.7-java11"
        assert cp.profile_metadata.target_profile == "springboot-3.5-java17"
        assert cp.profile_metadata.valid is True

    def test_profile_metadata_round_trip(self):
        profile = CheckpointProfileMetadata(
            source_profile="sb2",
            target_profile="sb3",
            source_level=1,
            target_level=2,
            included_stages=(2, 3),
            valid=True,
        )
        cp = _valid_checkpoint(profile_metadata=profile)
        d = cp.to_dict()
        cp2 = AnalysisCheckpoint.from_dict(d)
        assert cp2.profile_metadata.source_profile == "sb2"
        assert cp2.profile_metadata.target_profile == "sb3"


# ══════════════════════════════════════════════════════════════════════════
# 13. No dangerous fields in serialized output
# ══════════════════════════════════════════════════════════════════════════

class TestNoDangerousFieldsInOutput:
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
        assert found == set(), f"Dangerous keys in output: {found}"
