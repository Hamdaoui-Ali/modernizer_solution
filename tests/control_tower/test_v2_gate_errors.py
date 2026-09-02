"""Focused tests for F15 job035 — gate error taxonomy."""

from __future__ import annotations

import pytest

from migration_factory.control_tower.application.v2_gate_errors import (
    ActorNotAuthoritativeError,
    ApprovalFailedError,
    CommandConflictError,
    GateError,
    GateNotFoundError,
    GateNotOpenError,
    InvalidDecisionError,
    NoAcceptedAnalysisError,
    NoAcceptedPlanError,
    NoRepairServiceError,
    StaleChecksumError,
    gate_error_from_result,
    has_unsafe_field,
    http_status_for_gate_status,
    is_rejection_status,
    redact_paths,
)


# ── HTTP status mapping ──────────────────────────────────────────────


def test_http_status_for_executed() -> None:
    assert http_status_for_gate_status("executed") == 200


def test_http_status_for_idempotent() -> None:
    assert http_status_for_gate_status("idempotent") == 200


def test_http_status_gate_not_found() -> None:
    assert http_status_for_gate_status("gate_not_found") == 404


def test_http_status_gate_not_open() -> None:
    assert http_status_for_gate_status("gate_not_open") == 409


def test_http_status_stale_checksum() -> None:
    assert http_status_for_gate_status("stale_checksum") == 409


def test_http_status_command_conflict() -> None:
    assert http_status_for_gate_status("command_conflict") == 409


def test_http_status_invalid_decision() -> None:
    assert http_status_for_gate_status("invalid_decision") == 422


def test_http_status_no_accepted_analysis() -> None:
    assert http_status_for_gate_status("no_accepted_analysis") == 422


def test_http_status_no_accepted_plan() -> None:
    assert http_status_for_gate_status("no_accepted_plan") == 422


def test_http_status_actor_not_authoritative() -> None:
    assert http_status_for_gate_status("actor_not_authoritative") == 403


def test_http_status_unknown() -> None:
    assert http_status_for_gate_status("made_up_error") == 500


# ── rejection status detection ───────────────────────────────────────


def test_rejection_statuses() -> None:
    assert is_rejection_status("gate_not_found")
    assert is_rejection_status("gate_not_open")
    assert is_rejection_status("stale_checksum")
    assert is_rejection_status("invalid_decision")
    assert is_rejection_status("command_conflict")
    assert is_rejection_status("no_accepted_analysis")
    assert is_rejection_status("no_accepted_plan")
    assert is_rejection_status("approval_failed")
    assert is_rejection_status("actor_not_authoritative")
    assert is_rejection_status("no_repair_service")
    assert not is_rejection_status("executed")
    assert not is_rejection_status("idempotent")


# ── missing gate → 404 typed error ───────────────────────────────────


def test_gate_not_found_error() -> None:
    err = GateNotFoundError("Gate abc-123 not found")
    assert err.status == "gate_not_found"
    assert err.http_status == 404
    assert "not found" in str(err)

    d = err.to_dict()
    assert d["error"] == "gate_not_found"
    assert d["http_status"] == 404


# ── closed gate → 409 ────────────────────────────────────────────────


def test_gate_not_open_error() -> None:
    err = GateNotOpenError("Gate is resolved")
    assert err.status == "gate_not_open"
    assert err.http_status == 409

    d = err.to_dict()
    assert d["http_status"] == 409


def test_stale_checksum_error() -> None:
    err = StaleChecksumError("Checksum mismatch, refresh and retry")
    assert err.http_status == 409
    assert "refresh" in str(err).lower()


def test_command_conflict_error() -> None:
    err = CommandConflictError("Command already running")
    assert err.http_status == 409


# ── unsafe fields → 422 ──────────────────────────────────────────────


def test_invalid_decision_error() -> None:
    err = InvalidDecisionError("Cannot continue at approval_review")
    assert err.http_status == 422

    d = err.to_dict()
    assert d["http_status"] == 422


def test_no_accepted_analysis_error() -> None:
    err = NoAcceptedAnalysisError("No accepted analysis for this stage")
    assert err.http_status == 422


def test_no_accepted_plan_error() -> None:
    err = NoAcceptedPlanError("No accepted plan for this stage")
    assert err.http_status == 422


def test_approval_failed_error() -> None:
    err = ApprovalFailedError("Proposal not found")
    assert err.http_status == 422


# ── forbidden ────────────────────────────────────────────────────────


def test_actor_not_authoritative_error() -> None:
    err = ActorNotAuthoritativeError("Assistant cannot approve")
    assert err.http_status == 403


# ── internal server error ────────────────────────────────────────────


def test_no_repair_service_error() -> None:
    err = NoRepairServiceError("V2RepairFlowService not configured")
    assert err.http_status == 500


# ── mapper ───────────────────────────────────────────────────────────


def test_gate_error_from_result_success() -> None:
    assert gate_error_from_result("executed", "Success") is None
    assert gate_error_from_result("idempotent", "Already done") is None


def test_gate_error_from_result_not_found() -> None:
    err = gate_error_from_result("gate_not_found", "Gate missing")
    assert err is not None
    assert isinstance(err, GateNotFoundError)


def test_gate_error_from_result_stale() -> None:
    err = gate_error_from_result("stale_checksum", "Checksum mismatch")
    assert err is not None
    assert isinstance(err, StaleChecksumError)


def test_gate_error_from_result_unknown() -> None:
    err = gate_error_from_result("unknown_error", "Something broke")
    assert err is not None
    assert isinstance(err, GateError)
    assert err.http_status == 500


# ── path redaction ───────────────────────────────────────────────────


def test_redact_home_path() -> None:
    msg = "Error accessing /home/user/some/path/file.txt"
    safe = redact_paths(msg)
    assert "<redacted>" in safe
    assert "/home/user/" not in safe


def test_redact_internal_path() -> None:
    msg = "Error in migration_factory/control_tower/application/service.py"
    safe = redact_paths(msg)
    assert "<redacted>" in safe
    assert "/migration_factory/" not in safe


def test_redact_tmp_path() -> None:
    msg = "Temp file at /tmp/xyz123/file.sql"
    safe = redact_paths(msg)
    assert "<redacted>" in safe
    assert "/tmp/" not in safe


def test_safe_message_unchanged() -> None:
    msg = "Gate abc-123 is not open"
    safe = redact_paths(msg)
    assert safe == "Gate abc-123 is not open"


# ── unsafe field detection ───────────────────────────────────────────


def test_detect_sandbox_path() -> None:
    assert has_unsafe_field({"sandbox_path": "/tmp/sandbox"})
    assert has_unsafe_field({"sandbox_path": "/some/path"})


def test_detect_argv() -> None:
    assert has_unsafe_field({"argv": ["-v"]})


def test_detect_env() -> None:
    assert has_unsafe_field({"env": {"FOO": "bar"}})


def test_detect_command() -> None:
    assert has_unsafe_field({"command": "mvn compile"})


def test_safe_fields_not_detected() -> None:
    assert not has_unsafe_field({"gate_id": "abc", "job_id": "job-1"})
    assert not has_unsafe_field({"action": "continue", "reason": "ok"})
    assert not has_unsafe_field({"decision_id": "dec-1"})


# ── details in error ────────────────────────────────────────────────


def test_error_with_details() -> None:
    err = GateNotFoundError(
        "Gate missing",
        details={"gate_id": "abc-123", "job_id": "job-1"},
    )
    d = err.to_dict()
    assert d["details"]["gate_id"] == "abc-123"
    assert d["details"]["job_id"] == "job-1"


# ── error hierarchy ──────────────────────────────────────────────────


def test_error_inheritance() -> None:
    assert issubclass(GateNotFoundError, GateError)
    assert issubclass(GateNotOpenError, GateError)
    assert issubclass(StaleChecksumError, GateError)
    assert issubclass(CommandConflictError, GateError)
    assert issubclass(InvalidDecisionError, GateError)
    assert issubclass(NoAcceptedAnalysisError, GateError)
    assert issubclass(NoAcceptedPlanError, GateError)
    assert issubclass(ApprovalFailedError, GateError)
    assert issubclass(ActorNotAuthoritativeError, GateError)
    assert issubclass(NoRepairServiceError, GateError)
