"""F15 gate error taxonomy — typed, safe errors for invalid gate actions.

All gate action errors are typed and carry an HTTP status code mapping.
Internal paths and implementation details are redacted from error messages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ── HTTP status code mapping for GateActionResult status values ───────

_STATUS_TO_HTTP: dict[str, int] = {
    # Success
    "executed": 200,
    "idempotent": 200,
    # Client errors
    "gate_not_found": 404,
    "gate_not_open": 409,
    "stale_checksum": 409,
    "idempotency_conflict": 409,
    "command_conflict": 409,
    "invalid_decision": 422,
    "invalid_source_profile_override": 422,
    "artifact_ref_mismatch": 422,
    "gate_job_mismatch": 400,
    "no_accepted_analysis": 422,
    "no_accepted_plan": 422,
    "approval_failed": 422,
    "actor_not_authoritative": 403,
    "no_repair_service": 500,
    "missing_repair_checksum": 422,
    "repair_checksum_mismatch": 422,
}

# Rejection statuses (non-success)
_REJECTION_STATUSES: frozenset[str] = frozenset(
    s for s, code in _STATUS_TO_HTTP.items() if code >= 400
) | frozenset({"unknown_error"})


def http_status_for_gate_status(status: str) -> int:
    """Return the HTTP status code for a GateActionResult status value."""
    return _STATUS_TO_HTTP.get(status, 500)


def is_rejection_status(status: str) -> bool:
    """Return True if the status represents a rejection (4xx/5xx)."""
    return status in _REJECTION_STATUSES


# ── path redaction ────────────────────────────────────────────────────

_INTERNAL_PATH_PATTERNS = [
    re.compile(r"/home/[^/]+/"),
    re.compile(r"/?migration_factory/[a-z_]+/"),
    re.compile(r"/tmp/[^/]+/"),
]


def redact_paths(message: str) -> str:
    """Redact internal filesystem paths from an error message.

    Replaces user home directories and internal project paths
    with a safe placeholder.
    """
    for pattern in _INTERNAL_PATH_PATTERNS:
        message = pattern.sub("/<redacted>/", message)
    return message


# ── typed error classes ──────────────────────────────────────────────


class GateError(Exception):
    """Base error for all gate action failures."""

    status: str = "gate_error"
    http_status: int = 500

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        safe_message = redact_paths(message)
        self.details = details or {}
        super().__init__(safe_message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.status,
            "message": str(self),
            "http_status": self.http_status,
            "details": self.details,
        }


class GateNotFoundError(GateError):
    """The requested gate does not exist."""

    status = "gate_not_found"
    http_status = 404


class GateNotOpenError(GateError):
    """The gate is resolved or superseded — no new actions allowed."""

    status = "gate_not_open"
    http_status = 409


class StaleChecksumError(GateError):
    """The caller's gate checksum is stale — refresh and retry."""

    status = "stale_checksum"
    http_status = 409


class IdempotencyConflictError(GateError):
    """The idempotency key was reused for a different payload."""

    status = "idempotency_conflict"
    http_status = 409


class CommandConflictError(GateError):
    """A command is already queued/running for this job."""

    status = "command_conflict"
    http_status = 409


class InvalidDecisionError(GateError):
    """The decision is not valid for this gate phase."""

    status = "invalid_decision"
    http_status = 422


class NoAcceptedAnalysisError(GateError):
    """Cannot proceed without an accepted analysis revision."""

    status = "no_accepted_analysis"
    http_status = 422


class NoAcceptedPlanError(GateError):
    """Cannot proceed without an accepted plan revision (stale plan)."""

    status = "no_accepted_plan"
    http_status = 422


class ApprovalFailedError(GateError):
    """Repair proposal approval failed (proposal state or reviewer gate)."""

    status = "approval_failed"
    http_status = 422


class ActorNotAuthoritativeError(GateError):
    """Non-human actor attempted an authoritative action (approve/reject)."""

    status = "actor_not_authoritative"
    http_status = 403


class NoRepairServiceError(GateError):
    """V2RepairFlowService is not configured."""

    status = "no_repair_service"
    http_status = 500


# ── mapper: GateActionResult status → GateError ──────────────────────

_STATUS_TO_ERROR_CLASS: dict[str, type[GateError]] = {
    "gate_not_found": GateNotFoundError,
    "gate_not_open": GateNotOpenError,
    "stale_checksum": StaleChecksumError,
    "idempotency_conflict": IdempotencyConflictError,
    "command_conflict": CommandConflictError,
    "invalid_decision": InvalidDecisionError,
    "no_accepted_analysis": NoAcceptedAnalysisError,
    "no_accepted_plan": NoAcceptedPlanError,
    "approval_failed": ApprovalFailedError,
    "actor_not_authoritative": ActorNotAuthoritativeError,
    "no_repair_service": NoRepairServiceError,
    "missing_repair_checksum": InvalidDecisionError,
    "repair_checksum_mismatch": StaleChecksumError,
}


def gate_error_from_result(
    status: str,
    message: str = "",
    *,
    details: dict[str, Any] | None = None,
) -> GateError | None:
    """Convert a GateActionResult status to the corresponding GateError.

    Returns None for success statuses (executed, idempotent).
    """
    if not is_rejection_status(status):
        return None
    error_cls = _STATUS_TO_ERROR_CLASS.get(status)
    if error_cls is None:
        return GateError(message or f"Unknown gate error: {status}", details=details)
    return error_cls(message, details=details)


# ── unsafe field check ───────────────────────────────────────────────

_UNSAFE_FIELD_PATTERNS = [
    re.compile(r"sandbox_path", re.IGNORECASE),
    re.compile(r"argv", re.IGNORECASE),
    re.compile(r"env", re.IGNORECASE),
    re.compile(r"command", re.IGNORECASE),
]


def has_unsafe_field(data: dict[str, Any]) -> bool:
    """Check if a request dict contains unsafe fields (sandbox_path, argv, etc.).

    Returns True if any unsafe field name is found as a key.
    """
    for key in data:
        for pattern in _UNSAFE_FIELD_PATTERNS:
            if pattern.search(key):
                return True
    return False
