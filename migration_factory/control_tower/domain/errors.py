"""Typed domain errors for the AI Migration Control Tower."""

from __future__ import annotations

from migration_factory.control_tower.domain.states import JobState


class ControlTowerError(Exception):
    """Base exception for Control Tower failures."""


class ControlTowerDomainError(ControlTowerError):
    """Base exception for Control Tower domain failures."""


class NotFoundError(ControlTowerDomainError):
    """Raised when a required Control Tower record is missing."""

    def __init__(self, entity_name: str, identifier: str | None = None) -> None:
        self.entity_name = entity_name
        self.identifier = identifier
        message = entity_name if identifier is None else f"{entity_name} not found: {identifier}"
        super().__init__(message)


class RegistrationConflictError(ControlTowerDomainError):
    """Raised when an immutable configuration version is registered with changed content."""

    def __init__(self, entity_type: str, entity_id: str, version: str) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.version = version
        super().__init__(
            f"{entity_type} {entity_id!r} version {version!r} is already registered with different content"
        )


class ExpectedVersionRequiredError(ControlTowerDomainError):
    """Raised when an optimistic transition command omits expected_version."""

    def __init__(self) -> None:
        super().__init__("expected_version is required")


class StaleVersionError(ControlTowerDomainError):
    """Raised when a transition uses a job version that is no longer current."""

    def __init__(self, job_id: str, expected_version: int, actual_version: int | None) -> None:
        self.job_id = job_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        actual = "missing" if actual_version is None else str(actual_version)
        super().__init__(
            f"Stale version for job {job_id!r}: expected {expected_version}, actual {actual}"
        )


class ConcurrencyConflictError(ControlTowerDomainError):
    """Raised when database-enforced single-writer invariants reject a change."""


class InvalidJobStateTransitionError(ControlTowerDomainError):
    """Raised when a requested job state transition is not allowed."""

    def __init__(self, current_state: JobState, requested_state: JobState) -> None:
        self.current_state = current_state
        self.requested_state = requested_state
        super().__init__(
            "Invalid job state transition: "
            f"{current_state.value} -> {requested_state.value}"
        )


class CompatibilityError(ControlTowerError):
    """Raised when loaded configuration objects cannot be combined safely."""


class ArtifactPathError(ControlTowerError):
    """Raised when an artifact path is not trusted."""


class ArtifactHashError(ControlTowerError):
    """Raised when artifact hashing detects a race or mismatch."""


class StorageIntegrityError(ControlTowerError):
    """Raised when a persistence layer integrity violation cannot be classified."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class IdempotencyConflictError(ControlTowerError):
    """Raised when an idempotency key is reused with different request content."""

    def __init__(self, operation: str, idempotency_key: str) -> None:
        self.operation = operation
        self.idempotency_key = idempotency_key
        super().__init__(
            f"Idempotency key {idempotency_key!r} for {operation!r} was already used with a different request"
        )


class ActiveCommandConflictError(ControlTowerError):
    """Raised when a job already owns a nonterminal command."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Migration job {job_id!r} already has a nonterminal command")


class WorkspacePathError(ControlTowerError):
    """Raised when a workspace path fails security validation."""


class ManifestIntegrityError(ControlTowerError):
    """Raised when a manifest checksum does not match."""


class WorkspaceConflictError(ControlTowerError):
    """Raised when workspace preparation conflicts with existing state."""


class UnsupportedPlatformError(ControlTowerError):
    """Raised when the current platform does not support worker launch."""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        super().__init__(f"Worker launch is not supported on this platform: {platform}")


class InvalidEventCursorError(ControlTowerError):
    """Raised when a public event replay cursor is malformed or outside the valid range."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class EventCursorConflictError(ControlTowerError):
    """Raised when Last-Event-ID and after_sequence disagree."""

    def __init__(self, header_sequence: int, query_sequence: int) -> None:
        self.header_sequence = header_sequence
        self.query_sequence = query_sequence
        super().__init__(
            "Last-Event-ID and after_sequence must match when both are provided: "
            f"{header_sequence} != {query_sequence}"
        )


class ControllerOwnershipConflictError(ControlTowerError):
    """Raised when another local Control Tower controller already owns the singleton."""

    def __init__(self) -> None:
        super().__init__("Another local Control Tower controller is already active")


class ControllerOwnershipUnavailableError(ControlTowerError):
    """Raised when singleton/controller ownership cannot be established."""

    def __init__(self) -> None:
        super().__init__("Local Control Tower controller ownership is unavailable")


class ControllerOwnershipReleaseError(ControlTowerError):
    """Raised when singleton/controller ownership cannot be released cleanly."""

    def __init__(self) -> None:
        super().__init__("Local Control Tower controller ownership could not be released cleanly")


class ContinuationPolicyViolationError(ControlTowerError):
    """Raised when a stage continuation policy check fails.

    The stage cannot proceed because its input source does not match
    the expected prior-stage sandbox output.
    """

    def __init__(
        self,
        job_id: str,
        stage_index: int,
        expected_prior_stage_index: int,
        reason: str,
    ) -> None:
        self.job_id = job_id
        self.stage_index = stage_index
        self.expected_prior_stage_index = expected_prior_stage_index
        self.reason = reason
        super().__init__(
            f"Stage {stage_index} continuation policy violation for job {job_id!r}: "
            f"expected prior stage {expected_prior_stage_index} sandbox - {reason}"
        )


class ContinuationPolicyNotFoundError(ControlTowerError):
    """Raised when no continuation policy entry exists for a job/stage."""

    def __init__(self, job_id: str, stage_index: int) -> None:
        self.job_id = job_id
        self.stage_index = stage_index
        super().__init__(
            f"No continuation policy entry found for job {job_id!r} stage {stage_index}"
        )


class PlanAmendmentValidationError(ControlTowerError):
    """Raised when a plan amendment or revision payload is invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PlanRevisionConflictError(ControlTowerError):
    """Raised when ordered or terminal revision rules are violated."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PlanAdvisoryValidationError(ControlTowerError):
    """Raised when an advisory validation report cannot be projected safely."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PlanReviewConflictError(ControlTowerError):
    """Raised when a revision receives a conflicting second review decision."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PlanReviewChecksumMismatchError(ControlTowerError):
    """Raised when reviewer approval targets a stale revision checksum."""

    def __init__(self, revision_id: str) -> None:
        super().__init__(f"Review checksum does not match current revision payload for {revision_id!r}")


class RepairClassificationError(ControlTowerError):
    """Raised when repair classification cannot be created safely."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class RepairAttemptLimitExceededError(ControlTowerError):
    """Raised when deterministic fake repair attempt limits are exhausted."""

    def __init__(self, command_id: str, attempt_limit: int) -> None:
        super().__init__(
            f"Fake repair attempt limit reached for command {command_id!r}: {attempt_limit}"
        )


class RepairProposalValidationError(ControlTowerError):
    """Raised when fake repair proposal input is unsafe or malformed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PatchPolicyValidationError(ControlTowerError):
    """Raised when patch content fails policy validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PatchContentEscapeError(PatchPolicyValidationError):
    """Raised when patch content contains escape or shell metacharacters."""


class PatchContentMismatchError(PatchPolicyValidationError):
    """Raised when patch content does not match the expected target path or context."""


class PatchContentOversizeError(PatchPolicyValidationError):
    """Raised when patch content exceeds allowed size limits."""


class PatchNotApprovedError(PatchPolicyValidationError):
    """Raised when an unapproved patch is submitted for application."""


class PatchSnapshotNotFoundError(PatchPolicyValidationError):
    """Raised when no snapshot exists before patch application."""


class PatchRollbackError(ControlTowerError):
    """Raised when sandbox rollback fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
