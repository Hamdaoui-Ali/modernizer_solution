"""F1-T8: Tests for resume contract — resume behavior schema."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.resume_contract import (
    RESUME_FIELDS,
    ResumeOutcome,
    ResumeRejectionCode,
    ResumeRequest,
    ResumeResponse,
    _SUCCESSFUL_RESUME_OUTCOMES,
    _TERMINAL_RESUME_OUTCOMES,
    is_successful_resume,
    is_terminal_rejection,
    is_terminal_resume,
    is_valid_idempotency_key_format,
    is_valid_rejection_code,
    is_valid_resume_outcome,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _valid_request(**overrides) -> ResumeRequest:
    defaults: dict = {
        "checkpoint_id": "acp-001",
        "job_id": "job-abc",
        "decision": "continue",
        "artifact_refs": (
            "artifact:analysis_report_json:1",
            "artifact:dependency_graph_json:1",
        ),
        "artifact_checksums": {
            "artifact:analysis_report_json:1": "sha256:aaa",
            "artifact:dependency_graph_json:1": "sha256:bbb",
        },
        "comment_text": "",
        "idempotency_key": "idem-001",
    }
    defaults.update(overrides)
    return ResumeRequest(**defaults)


def _valid_response(**overrides) -> ResumeResponse:
    defaults: dict = {
        "request_id": "req-001",
        "checkpoint_id": "acp-001",
        "job_id": "job-abc",
        "outcome": ResumeOutcome.RESUMED,
        "next_stage": "planning",
        "next_gate_id": "gate-planning-001",
        "idempotency_key": "idem-001",
    }
    defaults.update(overrides)
    return ResumeResponse(**defaults)


# ══════════════════════════════════════════════════════════════════════════
# 1. ResumeOutcome enum
# ══════════════════════════════════════════════════════════════════════════

class TestResumeOutcome:
    """F1-T8: Resume outcomes contract."""

    def test_all_outcomes_defined(self):
        expected = {
            "resumed",
            "rejected_stale",
            "rejected_foreign",
            "rejected_incompatible",
            "rejected_terminal",
            "idempotent",
            "failed_closed",
        }
        actual = {o.value for o in ResumeOutcome}
        assert actual == expected

    def test_terminal_outcomes(self):
        assert ResumeOutcome.REJECTED_TERMINAL in _TERMINAL_RESUME_OUTCOMES
        assert ResumeOutcome.FAILED_CLOSED in _TERMINAL_RESUME_OUTCOMES
        # RESUMED is not terminal
        assert ResumeOutcome.RESUMED not in _TERMINAL_RESUME_OUTCOMES

    def test_successful_outcomes(self):
        assert ResumeOutcome.RESUMED in _SUCCESSFUL_RESUME_OUTCOMES
        assert ResumeOutcome.IDEMPOTENT in _SUCCESSFUL_RESUME_OUTCOMES
        # Rejected outcomes are not successful
        assert ResumeOutcome.REJECTED_STALE not in _SUCCESSFUL_RESUME_OUTCOMES

    def test_is_terminal_resume_helper(self):
        assert is_terminal_resume(ResumeOutcome.REJECTED_TERMINAL) is True
        assert is_terminal_resume(ResumeOutcome.FAILED_CLOSED) is True
        assert is_terminal_resume(ResumeOutcome.RESUMED) is False
        assert is_terminal_resume(ResumeOutcome.REJECTED_STALE) is False

    def test_is_successful_resume_helper(self):
        assert is_successful_resume(ResumeOutcome.RESUMED) is True
        assert is_successful_resume(ResumeOutcome.IDEMPOTENT) is True
        assert is_successful_resume(ResumeOutcome.REJECTED_FOREIGN) is False

    def test_is_valid_resume_outcome(self):
        assert is_valid_resume_outcome("resumed") is True
        assert is_valid_resume_outcome("rejected_stale") is True
        assert is_valid_resume_outcome("bogus") is False
        assert is_valid_resume_outcome("") is False


# ══════════════════════════════════════════════════════════════════════════
# 2. ResumeRejectionCode enum
# ══════════════════════════════════════════════════════════════════════════

class TestResumeRejectionCode:
    """F1-T8: Rejection codes contract."""

    def test_all_codes_defined(self):
        actual = {c.value for c in ResumeRejectionCode}
        assert "checksum_mismatch" in actual
        assert "foreign_job" in actual
        assert "foreign_profile" in actual
        assert "already_terminal" in actual
        assert "gate_not_open" in actual
        assert "invalid_decision" in actual
        assert "backend_failure" in actual

    def test_terminal_rejection_codes(self):
        assert is_terminal_rejection(ResumeRejectionCode.ALREADY_TERMINAL) is True
        assert is_terminal_rejection(ResumeRejectionCode.BACKEND_FAILURE) is True
        assert is_terminal_rejection(ResumeRejectionCode.CHECKSUM_MISMATCH) is False

    def test_is_valid_rejection_code(self):
        assert is_valid_rejection_code("checksum_mismatch") is True
        assert is_valid_rejection_code("backend_failure") is True
        assert is_valid_rejection_code("bogus_code") is False
        assert is_valid_rejection_code("") is False

    def test_rejection_codes_are_sanitized(self):
        """No rejection code value should contain paths, secrets, or sandbox references."""
        for code in ResumeRejectionCode:
            v = code.value
            assert "/" not in v
            assert "sandbox" not in v
            assert "secret" not in v
            assert "password" not in v
            assert "token" not in v


# ══════════════════════════════════════════════════════════════════════════
# 3. ResumeRequest construction
# ══════════════════════════════════════════════════════════════════════════

class TestResumeRequestConstruction:
    """F1-T8: Resume request model."""

    def test_minimal_construction(self):
        req = _valid_request()
        assert req.checkpoint_id == "acp-001"
        assert req.job_id == "job-abc"
        assert req.decision == "continue"
        assert req.idempotency_key == "idem-001"

    def test_empty_checkpoint_id_rejected(self):
        with pytest.raises(ValidationError):
            _valid_request(checkpoint_id="")

    def test_empty_job_id_rejected(self):
        with pytest.raises(ValidationError):
            _valid_request(job_id="")

    def test_empty_idempotency_key_rejected(self):
        with pytest.raises(ValidationError):
            _valid_request(idempotency_key="")

    def test_empty_decision_rejected(self):
        with pytest.raises(ValidationError):
            _valid_request(decision="")

    def test_decision_too_long_rejected(self):
        with pytest.raises(ValidationError, match="String should have at most"):
            _valid_request(decision="x" * 65)

    def test_comment_text_too_long_rejected(self):
        with pytest.raises(ValidationError, match="String should have at most"):
            _valid_request(comment_text="x" * 2001)

    def test_empty_artifact_refs_allowed(self):
        req = _valid_request(artifact_refs=(), artifact_checksums={})
        assert len(req.artifact_refs) == 0

    def test_artifact_ref_without_checksum_rejected(self):
        with pytest.raises(ValidationError, match="missing a checksum"):
            _valid_request(
                artifact_refs=("artifact:extra:1",),
                artifact_checksums={},
            )

    def test_checksums_without_ref_is_ok(self):
        """Extra checksums without corresponding refs are allowed (forward-compat)."""
        req = _valid_request(
            artifact_refs=("artifact:a:1",),
            artifact_checksums={
                "artifact:a:1": "sha256:aaa",
                "artifact:extra:1": "sha256:bbb",  # no ref, but allowed
            },
        )
        assert "artifact:a:1" in req.artifact_checksums

    def test_too_many_artifact_refs_rejected(self):
        with pytest.raises(ValidationError, match="should have at most 32"):
            _valid_request(artifact_refs=tuple(f"a-{i}" for i in range(33)))

    def test_default_decision_is_continue(self):
        req = ResumeRequest(
            checkpoint_id="acp-001",
            job_id="job-abc",
            idempotency_key="idem-001",
        )
        assert req.decision == "continue"

    def test_default_comment_is_empty(self):
        req = ResumeRequest(
            checkpoint_id="acp-001",
            job_id="job-abc",
            idempotency_key="idem-001",
        )
        assert req.comment_text == ""


# ══════════════════════════════════════════════════════════════════════════
# 4. ResumeResponse construction
# ══════════════════════════════════════════════════════════════════════════

class TestResumeResponseConstruction:
    """F1-T8: Resume response model."""

    def test_minimal_construction(self):
        resp = _valid_response()
        assert resp.outcome == ResumeOutcome.RESUMED
        assert resp.next_stage == "planning"
        assert resp.is_resumed is True
        assert resp.is_rejected is False

    def test_empty_request_id_rejected(self):
        with pytest.raises(ValidationError):
            _valid_response(request_id="")

    def test_empty_checkpoint_id_rejected(self):
        with pytest.raises(ValidationError):
            _valid_response(checkpoint_id="")

    def test_empty_job_id_rejected(self):
        with pytest.raises(ValidationError):
            _valid_response(job_id="")

    def test_resumed_without_next_stage_rejected(self):
        with pytest.raises(ValidationError, match="requires a non-empty next_stage"):
            _valid_response(next_stage="")

    def test_resumed_with_whitespace_next_stage_rejected(self):
        with pytest.raises(ValidationError, match="requires a non-empty next_stage"):
            _valid_response(next_stage="   ")

    def test_rejected_without_rejection_code_rejected(self):
        with pytest.raises(ValidationError, match="requires a rejection_code"):
            _valid_response(
                outcome=ResumeOutcome.REJECTED_STALE,
                next_stage="",
                rejection_code=None,
            )

    def test_rejected_with_rejection_code_ok(self):
        resp = _valid_response(
            outcome=ResumeOutcome.REJECTED_STALE,
            rejection_code=ResumeRejectionCode.CHECKSUM_MISMATCH,
            next_stage="",
        )
        assert resp.is_rejected is True
        assert resp.rejection_code == ResumeRejectionCode.CHECKSUM_MISMATCH

    def test_idempotent_outcome_is_successful(self):
        resp = _valid_response(
            outcome=ResumeOutcome.IDEMPOTENT,
            is_cached=True,
            next_stage="planning",
            rejection_code=None,
        )
        assert resp.is_resumed is False
        assert resp.is_rejected is False
        # IDEMPOTENT is not RESUMED but is successful
        assert is_successful_resume(resp.outcome) is True

    def test_rejection_detail_max_length(self):
        with pytest.raises(ValidationError, match="String should have at most"):
            _valid_response(
                outcome=ResumeOutcome.REJECTED_STALE,
                rejection_code=ResumeRejectionCode.CHECKSUM_MISMATCH,
                rejection_detail="x" * 501,
                next_stage="",
            )

    def test_default_resolved_at_is_set(self):
        resp = _valid_response()
        assert resp.resolved_at
        assert "T" in resp.resolved_at  # ISO 8601

    def test_is_cached_defaults_to_false(self):
        resp = _valid_response()
        assert resp.is_cached is False


# ══════════════════════════════════════════════════════════════════════════
# 5. ResumeResponse factory methods
# ══════════════════════════════════════════════════════════════════════════

class TestResumeResponseFactories:
    """F1-T8: Idempotency and rejected factories."""

    def test_idempotent_factory_returns_cached(self):
        prior = _valid_response(
            outcome=ResumeOutcome.RESUMED,
            next_stage="build",
            next_gate_id="gate-build-001",
        )
        cached = ResumeResponse.idempotent(
            request_id="req-002",
            checkpoint_id="acp-001",
            job_id="job-abc",
            idempotency_key="idem-001",
            prior_response=prior,
        )
        assert cached.outcome == ResumeOutcome.IDEMPOTENT
        assert cached.is_cached is True
        assert cached.is_rejected is False
        assert cached.next_stage == "build"

    def test_idempotent_factory_preserves_rejection(self):
        prior = _valid_response(
            outcome=ResumeOutcome.REJECTED_FOREIGN,
            rejection_code=ResumeRejectionCode.FOREIGN_JOB,
            next_stage="",
        )
        cached = ResumeResponse.idempotent(
            request_id="req-002",
            checkpoint_id="acp-001",
            job_id="job-abc",
            idempotency_key="idem-001",
            prior_response=prior,
        )
        assert cached.outcome == ResumeOutcome.IDEMPOTENT
        assert cached.rejection_code == ResumeRejectionCode.FOREIGN_JOB

    def test_rejected_factory(self):
        resp = ResumeResponse.rejected(
            request_id="req-003",
            checkpoint_id="acp-001",
            job_id="job-abc",
            outcome=ResumeOutcome.REJECTED_STALE,
            rejection_code=ResumeRejectionCode.CHECKSUM_MISMATCH,
            rejection_detail="Checksums do not match stored checkpoint",
        )
        assert resp.outcome == ResumeOutcome.REJECTED_STALE
        assert resp.is_rejected is True
        assert resp.rejection_detail == "Checksums do not match stored checkpoint"
        assert resp.next_stage == ""


# ══════════════════════════════════════════════════════════════════════════
# 6. Serialization round-trip
# ══════════════════════════════════════════════════════════════════════════

class TestSerialization:
    """F1-T8: Serialization round-trip for request and response."""

    # ── ResumeRequest ──

    def test_request_to_dict(self):
        req = _valid_request()
        d = req.to_dict()
        assert d["checkpoint_id"] == "acp-001"
        assert d["job_id"] == "job-abc"
        assert d["decision"] == "continue"
        assert d["idempotency_key"] == "idem-001"
        assert isinstance(d["artifact_refs"], list)

    def test_request_to_json(self):
        req = _valid_request()
        j = req.to_json()
        assert "acp-001" in j
        parsed = json.loads(j)
        assert parsed["checkpoint_id"] == "acp-001"

    def test_request_from_dict_minimal(self):
        req = ResumeRequest.from_dict({
            "checkpoint_id": "acp-002",
            "job_id": "job-xyz",
            "idempotency_key": "idem-002",
        })
        assert req.checkpoint_id == "acp-002"
        assert req.decision == "continue"

    def test_request_from_dict_full(self):
        d = {
            "checkpoint_id": "acp-003",
            "job_id": "job-full",
            "decision": "request_modification",
            "artifact_refs": ["artifact:a:1"],
            "artifact_checksums": {"artifact:a:1": "sha256:ccc"},
            "comment_text": "Please revise section 3",
            "idempotency_key": "idem-003",
        }
        req = ResumeRequest.from_dict(d)
        assert req.decision == "request_modification"
        assert "artifact:a:1" in req.artifact_refs
        assert req.comment_text == "Please revise section 3"

    def test_request_round_trip(self):
        req = _valid_request(
            decision="request_modification",
            comment_text="Fix the dependency graph",
            artifact_refs=("artifact:a:1",),
            artifact_checksums={"artifact:a:1": "sha256:xxx"},
        )
        d = req.to_dict()
        req2 = ResumeRequest.from_dict(d)
        assert req2.decision == req.decision
        assert req2.comment_text == req.comment_text
        assert req2.idempotency_key == req.idempotency_key

    def test_request_json_round_trip(self):
        req = _valid_request()
        j = req.to_json()
        d = json.loads(j)
        req2 = ResumeRequest.from_dict(d)
        assert req2.checkpoint_id == req.checkpoint_id
        assert req2.idempotency_key == req.idempotency_key

    def test_request_from_dict_none_fields_guarded(self):
        """Database NULL columns must produce empty strings, not literal 'None'."""
        with pytest.raises(ValidationError):
            ResumeRequest.from_dict({
                "checkpoint_id": None,
                "job_id": "job-abc",
                "idempotency_key": "idem-001",
            })

    # ── ResumeResponse ──

    def test_response_to_dict(self):
        resp = _valid_response()
        d = resp.to_dict()
        assert d["outcome"] == "resumed"
        assert d["next_stage"] == "planning"
        assert d["is_cached"] is False

    def test_response_to_json(self):
        resp = _valid_response()
        j = resp.to_json()
        assert "resumed" in j

    def test_response_from_dict_minimal(self):
        resp = ResumeResponse.from_dict({
            "request_id": "req-min",
            "checkpoint_id": "acp-min",
            "job_id": "job-min",
            "outcome": "resumed",
            "next_stage": "planning",
        })
        assert resp.outcome == ResumeOutcome.RESUMED
        assert resp.next_stage == "planning"

    def test_response_from_dict_rejected(self):
        resp = ResumeResponse.from_dict({
            "request_id": "req-rej",
            "checkpoint_id": "acp-rej",
            "job_id": "job-rej",
            "outcome": "rejected_stale",
            "rejection_code": "checksum_mismatch",
            "rejection_detail": "Stale artifacts detected",
        })
        assert resp.outcome == ResumeOutcome.REJECTED_STALE
        assert resp.rejection_code == ResumeRejectionCode.CHECKSUM_MISMATCH
        assert resp.is_rejected is True

    def test_response_round_trip(self):
        resp = _valid_response(
            outcome=ResumeOutcome.REJECTED_FOREIGN,
            rejection_code=ResumeRejectionCode.FOREIGN_JOB,
            rejection_detail="Checkpoint belongs to a different job",
            next_stage="",
        )
        d = resp.to_dict()
        resp2 = ResumeResponse.from_dict(d)
        assert resp2.outcome == resp.outcome
        assert resp2.rejection_code == resp.rejection_code
        assert resp2.is_rejected is True

    def test_response_json_round_trip(self):
        resp = _valid_response()
        j = resp.to_json()
        d = json.loads(j)
        resp2 = ResumeResponse.from_dict(d)
        assert resp2.outcome == resp.outcome
        assert resp2.next_stage == resp.next_stage

    def test_response_from_dict_none_fields_guarded(self):
        """Database NULL columns must produce empty strings, not literal 'None'."""
        with pytest.raises(ValidationError):
            ResumeResponse.from_dict({
                "request_id": None,
                "checkpoint_id": "acp-null",
                "job_id": "job-null",
            })

    def test_response_from_dict_none_outcome_defaults(self):
        resp = ResumeResponse.from_dict({
            "request_id": "req-null",
            "checkpoint_id": "acp-null",
            "job_id": "job-null",
            "outcome": None,
        })
        assert resp.outcome == ResumeOutcome.FAILED_CLOSED

    def test_response_from_dict_unknown_rejection_code_ignored(self):
        """Unknown rejection codes should be ignored; fallback to BACKEND_FAILURE."""
        resp = ResumeResponse.from_dict({
            "request_id": "req-unk",
            "checkpoint_id": "acp-unk",
            "job_id": "job-unk",
            "outcome": "rejected_stale",
            "rejection_code": "some_unknown_code",
            "next_stage": "",
        })
        assert resp.rejection_code == ResumeRejectionCode.BACKEND_FAILURE
        assert resp.is_rejected is True


# ══════════════════════════════════════════════════════════════════════════
# 7. Resume fields contract
# ══════════════════════════════════════════════════════════════════════════

class TestResumeFields:
    """F1-T8: Safe fields contract."""

    def test_fields_are_frozenset(self):
        assert isinstance(RESUME_FIELDS, frozenset)

    def test_no_dangerous_fields(self):
        dangerous = {
            "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
            "provider", "model", "deployment", "endpoint", "secret", "token",
            "password", "api_key", "client_secret", "command",
        }
        overlap = RESUME_FIELDS & dangerous
        assert not overlap, f"Dangerous fields in RESUME_FIELDS: {overlap}"

    def test_request_fields_present(self):
        assert "checkpoint_id" in RESUME_FIELDS
        assert "job_id" in RESUME_FIELDS
        assert "decision" in RESUME_FIELDS
        assert "artifact_refs" in RESUME_FIELDS
        assert "artifact_checksums" in RESUME_FIELDS
        assert "comment_text" in RESUME_FIELDS
        assert "idempotency_key" in RESUME_FIELDS

    def test_response_fields_present(self):
        assert "request_id" in RESUME_FIELDS
        assert "outcome" in RESUME_FIELDS
        assert "rejection_code" in RESUME_FIELDS
        assert "next_stage" in RESUME_FIELDS
        assert "next_gate_id" in RESUME_FIELDS
        assert "resolved_at" in RESUME_FIELDS

    def test_no_user_supplied_path_or_command_fields(self):
        """Resume must not accept filesystem paths or shell commands from the user."""
        assert "target_path" not in RESUME_FIELDS
        assert "command" not in RESUME_FIELDS
        assert "argv" not in RESUME_FIELDS
        assert "env" not in RESUME_FIELDS


# ══════════════════════════════════════════════════════════════════════════
# 8. No dangerous fields in serialized output
# ══════════════════════════════════════════════════════════════════════════

class TestNoDangerousFieldsInOutput:
    """F1-T8: Serialized outputs must never contain dangerous keys."""

    DANGEROUS_SUBSTRINGS = [
        "sandbox", "secret", "password", "token", "api_key",
        "client_secret", "command", "argv", "endpoint",
        "provider", "deployment",
    ]

    def test_request_to_dict_no_dangerous_keys(self):
        req = _valid_request()
        d = req.to_dict()
        keys = set(d.keys())
        for ds in self.DANGEROUS_SUBSTRINGS:
            assert not any(ds in k.lower() for k in keys), f"Dangerous: {ds}"

    def test_request_to_json_no_dangerous_keys(self):
        req = _valid_request()
        j = req.to_json()
        for ds in self.DANGEROUS_SUBSTRINGS:
            assert ds not in j.lower(), f"Dangerous: {ds}"

    def test_response_to_dict_no_dangerous_keys(self):
        resp = _valid_response()
        d = resp.to_dict()
        keys = set(d.keys())
        for ds in self.DANGEROUS_SUBSTRINGS:
            assert not any(ds in k.lower() for k in keys), f"Dangerous: {ds}"

    def test_response_to_json_no_dangerous_keys(self):
        resp = _valid_response()
        j = resp.to_json()
        for ds in self.DANGEROUS_SUBSTRINGS:
            assert ds not in j.lower(), f"Dangerous: {ds}"

    def test_request_from_dict_ignores_dangerous_keys_in_input(self):
        """Even if an attacker includes dangerous keys, from_dict must ignore them."""
        req = ResumeRequest.from_dict({
            "checkpoint_id": "acp-001",
            "job_id": "job-abc",
            "idempotency_key": "idem-001",
            "sandbox_path": "/etc/passwd",
            "command": "rm -rf /",
            "env": {"SECRET": "leaked"},
        })
        assert req.checkpoint_id == "acp-001"

    def test_response_from_dict_ignores_dangerous_keys_in_input(self):
        resp = ResumeResponse.from_dict({
            "request_id": "req-safe",
            "checkpoint_id": "acp-safe",
            "job_id": "job-safe",
            "outcome": "resumed",
            "next_stage": "planning",
            "sandbox_path": "/tmp/leak",
            "command": "evil",
        })
        assert resp.request_id == "req-safe"
        assert resp.next_stage == "planning"


# ══════════════════════════════════════════════════════════════════════════
# 9. Idempotency key validation
# ══════════════════════════════════════════════════════════════════════════

class TestIdempotencyKey:
    """F1-T8: Idempotency key contract."""

    def test_valid_key_format(self):
        assert is_valid_idempotency_key_format("idem-001") is True
        assert is_valid_idempotency_key_format("a") is True
        assert is_valid_idempotency_key_format("uuid-like-key-12345") is True

    def test_invalid_key_format(self):
        assert is_valid_idempotency_key_format("") is False
        assert is_valid_idempotency_key_format("   ") is False

    def test_request_rejects_empty_idempotency_key(self):
        with pytest.raises(ValidationError):
            _valid_request(idempotency_key="")

    def test_different_keys_produce_different_requests(self):
        req1 = _valid_request(idempotency_key="idem-001")
        req2 = _valid_request(idempotency_key="idem-002")
        assert req1.idempotency_key != req2.idempotency_key


# ══════════════════════════════════════════════════════════════════════════
# 10. Rejection detail sanitization
# ══════════════════════════════════════════════════════════════════════════

class TestRejectionDetailSanitization:
    """F1-T8: Rejection detail must not expose secrets."""

    def test_rejected_response_rejection_detail_present(self):
        resp = ResumeResponse.rejected(
            request_id="req-r",
            checkpoint_id="acp-r",
            job_id="job-r",
            outcome=ResumeOutcome.REJECTED_STALE,
            rejection_code=ResumeRejectionCode.CHECKSUM_MISMATCH,
            rejection_detail="Checksum mismatch for required artifacts",
        )
        d = resp.to_dict()
        assert d["rejection_detail"] == "Checksum mismatch for required artifacts"

    def test_rejected_response_can_have_empty_detail(self):
        resp = ResumeResponse.rejected(
            request_id="req-r2",
            checkpoint_id="acp-r2",
            job_id="job-r2",
            outcome=ResumeOutcome.REJECTED_FOREIGN,
            rejection_code=ResumeRejectionCode.FOREIGN_JOB,
        )
        assert resp.rejection_detail == ""
