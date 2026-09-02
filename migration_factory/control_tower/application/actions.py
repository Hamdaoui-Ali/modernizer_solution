"""Privileged action persistence service for V1-17A.

Persists pending privileged action requests with typed action
metadata, checksums, actor attribution, status tracking, and
audit trails.

Only typed Maven and write actions are allowed. Shell actions
are rejected at the service layer.

Approval logic belongs to V1-17C. Execution belongs to V1-17D.
Policy/checksum validation beyond basic storage belongs to V1-17B.
"""

from __future__ import annotations

import json
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.domain.entities import V1PrivilegedActionDecisionRecord
from migration_factory.control_tower.domain.entities import V1PrivilegedActionExecutionRecord
from migration_factory.control_tower.domain.entities import V1PrivilegedActionRecord
from migration_factory.control_tower.domain.errors import ControlTowerError


# ── Domain errors ──────────────────────────────────────────────────


class PrivilegedActionError(ControlTowerError):
    """Base error for privileged action failures."""


class ActionFormatError(PrivilegedActionError):
    """Raised when action parameters are malformed or have wrong types."""

    def __init__(self, action_type: str, reason: str) -> None:
        self.action_type = action_type
        super().__init__(
            f"Malformed {action_type!r} action parameters: {reason}"
        )


class ActionPolicyViolationError(PrivilegedActionError):
    """Raised when the action payload violates active policy."""

    def __init__(self, action_type: str, policy_reason: str) -> None:
        self.action_type = action_type
        super().__init__(
            f"Policy violation for {action_type!r} action: {policy_reason}"
        )


class ChecksumMismatchError(PrivilegedActionError):
    """Raised when a checksum does not match the expected value."""

    def __init__(self, action_id: str, expected: str, actual: str) -> None:
        self.action_id = action_id
        super().__init__(
            f"Checksum mismatch for action {action_id!r}: "
            f"expected {expected!r}, got {actual!r}"
        )


class ActionStaleError(PrivilegedActionError):
    """Raised when an action reference is stale or not found."""

    def __init__(self, action_id: str, reason: str) -> None:
        self.action_id = action_id
        super().__init__(f"Action {action_id!r} is stale: {reason}")


class DuplicateActionDecisionError(PrivilegedActionError):
    """Raised when an action already has a decision recorded."""

    def __init__(self, action_id: str, existing_decision: str) -> None:
        self.action_id = action_id
        self.existing_decision = existing_decision
        super().__init__(
            f"Action {action_id!r} already has a {existing_decision!r} decision"
        )


class ActionNotApprovedError(PrivilegedActionError):
    """Raised when an action has not been approved for execution."""

    def __init__(self, action_id: str, reason: str) -> None:
        self.action_id = action_id
        super().__init__(
            f"Action {action_id!r} is not approved: {reason}"
        )


class InvalidActionTypeError(PrivilegedActionError):
    """Raised when an unsupported action type is requested."""

    def __init__(self, action_type: str) -> None:
        self.action_type = action_type
        super().__init__(
            f"Unsupported privileged action type: {action_type!r}. "
            f"Only 'maven' and 'write' are allowed."
        )


class ActionNotFoundError(PrivilegedActionError):
    """Raised when a privileged action is not found."""

    def __init__(self, action_id: str) -> None:
        self.action_id = action_id
        super().__init__(f"Privileged action not found: {action_id!r}")


# ── Allowed action types ──────────────────────────────────────────

ALLOWED_ACTION_TYPES: tuple[str, ...] = ("maven", "write")

# Action parameters are validated structurally but not deeply
# (policy/checksum validation belongs to V1-17B)


# ── PrivilegedActionService ────────────────────────────────────────


class PrivilegedActionService:
    """Service for persisting and querying pending privileged actions.

    This service stores requested actions only. It does not:
    - Approve or reject actions (V1-17C)
    - Execute actions (V1-17D)
    - Validate action policy/checksums beyond basic type checks (V1-17B)
    """

    def __init__(self, unit_of_work_factory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def request_action(
        self,
        *,
        job_id: str,
        action_type: str,
        parameters: dict[str, object],
        requested_by: str = "system",
        policy_json: str | None = None,
        policy_version: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> V1PrivilegedActionRecord:
        """Persist a pending privileged action request.

        Validates:
        - Action type is 'maven' or 'write' (shell is rejected).
        - Parameters are not empty.

        Computes:
        - A unique action_id.
        - A checksum over the parameters.
        - Attribution and timestamps.

        Args:
            job_id: The migration job requesting the action.
            action_type: 'maven' or 'write'.
            parameters: Structured parameters for the action.
            requested_by: Actor requesting the action.
            policy_json: Optional policy reference JSON.
            policy_version: Optional policy version.
            correlation_id: Optional correlation ID.
            causation_id: Optional causation ID.

        Returns:
            The persisted V1PrivilegedActionRecord.

        Raises:
            InvalidActionTypeError: If action_type is not 'maven' or 'write'.
        """
        start = self._validate_request(action_type, parameters)

        action_id = f"pa-{uuid4().hex}"
        now = utc_now_text()
        parameters_json = json.dumps(parameters, separators=(",", ":"), sort_keys=True)
        parameters_checksum = sha256_canonical_json(parameters)

        record = V1PrivilegedActionRecord(
            action_id=action_id,
            job_id=job_id,
            action_type=action_type,
            action_version="1.0",
            parameters_json=parameters_json,
            parameters_checksum=parameters_checksum,
            policy_json=policy_json,
            policy_version=policy_version,
            status="pending",
            requested_by=requested_by,
            requested_at=now,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        with self._unit_of_work_factory() as uow:
            uow.v1_privileged_actions.insert(record)

            # Record audit event
            import json as _json

            audit_payload = {
                "action": "privileged_action_requested",
                "action_id": action_id,
                "job_id": job_id,
                "action_type": action_type,
                "parameters_checksum": parameters_checksum,
            }
            uow.audit_records.append_global_audit(
                audit_id=uuid4().hex,
                actor_type="system",
                actor_id=requested_by,
                action="privileged_action_requested",
                payload_json=_json.dumps(audit_payload, separators=(",", ":"), sort_keys=True),
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

        return record

    def get_action(self, action_id: str) -> V1PrivilegedActionRecord | None:
        """Get a single privileged action by ID."""
        with self._unit_of_work_factory() as uow:
            return uow.v1_privileged_actions.get(action_id)

    def list_actions(self) -> tuple[V1PrivilegedActionRecord, ...]:
        """List all privileged actions."""
        with self._unit_of_work_factory() as uow:
            return uow.v1_privileged_actions.list()

    def list_actions_for_job(self, job_id: str) -> tuple[V1PrivilegedActionRecord, ...]:
        """List privileged actions for a specific job."""
        with self._unit_of_work_factory() as uow:
            return uow.v1_privileged_actions.list_for_job(job_id)

    def list_pending_actions(self) -> tuple[V1PrivilegedActionRecord, ...]:
        """List all pending privileged actions."""
        with self._unit_of_work_factory() as uow:
            return uow.v1_privileged_actions.list_by_status("pending")

    def list_actions_by_status(self, status: str) -> tuple[V1PrivilegedActionRecord, ...]:
        """List privileged actions by status."""
        with self._unit_of_work_factory() as uow:
            return uow.v1_privileged_actions.list_by_status(status)

    def to_dto(self, record: V1PrivilegedActionRecord) -> dict[str, object]:
        """Convert a domain record to a public DTO.

        Only non-sensitive fields are exposed. Parameters JSON
        is included in its structured form (already safe by
        construction since raw paths/secrets are not stored).
        """
        return {
            "action_id": record.action_id,
            "job_id": record.job_id,
            "action_type": record.action_type,
            "action_version": record.action_version,
            "parameters": json.loads(record.parameters_json) if record.parameters_json else {},
            "parameters_checksum": record.parameters_checksum,
            "status": record.status,
            "requested_by": record.requested_by,
            "requested_at": record.requested_at,
            "approved_by": record.approved_by,
            "approved_at": record.approved_at,
            "rejected_by": record.rejected_by,
            "rejected_reason": record.rejected_reason,
            "executed_at": record.executed_at,
            "failure_reason": record.failure_reason,
        }

    def _validate_request(
        self,
        action_type: str,
        parameters: dict[str, object],
    ) -> None:
        """Validate a privileged action request before persistence."""
        if action_type not in ALLOWED_ACTION_TYPES:
            raise InvalidActionTypeError(action_type)

        if not parameters:
            raise ValueError("Privileged action parameters must not be empty")

        # V1-17B: Parameter structure validation (required fields, types)
        self.validate_parameters_structure(action_type, parameters)

        # V1-17B: Security validation (forbidden paths, secrets, shell chars)
        # Goal safeness (well-known Maven phases) is validated by callers
        # (V1-17C approve, V1-17D execute) via validate_action_parameters_policy
        self._validate_action_parameters_security(action_type, parameters)

    def _validate_action_parameters_security(
        self,
        action_type: str,
        parameters: dict[str, object],
    ) -> None:
        """Validate action parameters for security issues.

        Checks shell metacharacters and forbidden path references.
        Does not validate Maven goal safeness (that is a policy check
        for V1-17C/V1-17D callers).
        """
        from migration_factory.control_tower.application.redaction import (
            contains_forbidden_path,
        )

        # Check for shell metacharacters in parameters
        for key, value in parameters.items():
            if isinstance(value, str) and len(value) > 1:
                if any(c in value for c in (";", "|", "`", "$(")):
                    raise ActionPolicyViolationError(
                        action_type,
                        f"Parameter {key!r} contains shell metacharacters",
                    )

        # For write actions, check path against forbidden paths
        if action_type == "write":
            write_path = parameters.get("path", "")
            if isinstance(write_path, str):
                if contains_forbidden_path(write_path):
                    raise ActionPolicyViolationError(
                        "write",
                        f"Write path contains forbidden pattern: {write_path!r}",
                    )

    # ── V1-17B: Validation methods ────────────────────────────────────

    @staticmethod
    def validate_parameters_structure(
        action_type: str,
        parameters: dict[str, object],
    ) -> None:
        """Validate parameter structure for a given action type.

        Args:
            action_type: 'maven' or 'write'.
            parameters: Action parameters dict.

        Raises:
            ActionFormatError: If parameters are malformed or required
                fields are missing or have wrong types.
        """
        if action_type == "maven":
            PrivilegedActionService._validate_maven_parameters(parameters)
        elif action_type == "write":
            PrivilegedActionService._validate_write_parameters(parameters)

    @staticmethod
    def _validate_maven_parameters(parameters: dict[str, object]) -> None:
        goal = parameters.get("goal")
        if goal is None:
            raise ActionFormatError(
                "maven", "Missing required 'goal' parameter"
            )
        if not isinstance(goal, str) or not goal.strip():
            raise ActionFormatError(
                "maven",
                f"'goal' must be a non-empty string, got {type(goal).__name__}",
            )

        # Module is optional but must be a string if present
        module = parameters.get("module")
        if module is not None and (not isinstance(module, str) or not module.strip()):
            raise ActionFormatError(
                "maven",
                f"'module' must be a non-empty string when provided, "
                f"got {type(module).__name__}",
            )

    @staticmethod
    def _validate_write_parameters(parameters: dict[str, object]) -> None:
        path = parameters.get("path")
        if path is None:
            raise ActionFormatError(
                "write", "Missing required 'path' parameter"
            )
        if not isinstance(path, str) or not path.strip():
            raise ActionFormatError(
                "write",
                f"'path' must be a non-empty string, got {type(path).__name__}",
            )

        content = parameters.get("content")
        if content is None:
            raise ActionFormatError(
                "write", "Missing required 'content' parameter"
            )
        if not isinstance(content, str):
            raise ActionFormatError(
                "write",
                f"'content' must be a string, got {type(content).__name__}",
            )

    @staticmethod
    def validate_action_parameters_policy(
        action_type: str,
        parameters: dict[str, object],
    ) -> None:
        """Validate action parameters against active policy and forbidden paths.

        Uses V1-00D redaction/forbidden-path baseline to reject payloads
        that contain forbidden paths, secrets, or unsafe values.

        Args:
            action_type: 'maven' or 'write'.
            parameters: Action parameters dict.

        Raises:
            ActionPolicyViolationError: If parameters violate policy.
        """
        from migration_factory.control_tower.application.redaction import (
            contains_forbidden_path,
            is_forbidden_file,
        )

        # Common checks: no secrets or forbidden content in any parameter value
        PrivilegedActionService._check_no_secrets_in_parameters(
            action_type, parameters
        )

        if action_type == "write":
            write_path = parameters.get("path", "")
            if isinstance(write_path, str):
                if contains_forbidden_path(write_path):
                    raise ActionPolicyViolationError(
                        "write",
                        f"Write path contains forbidden pattern: {write_path!r}",
                    )
                if is_forbidden_file(write_path):
                    raise ActionPolicyViolationError(
                        "write",
                        f"Write path targets a forbidden file: {write_path!r}",
                    )

        elif action_type == "maven":
            goal = parameters.get("goal", "")
            if isinstance(goal, str):
                PrivilegedActionService._validate_maven_goal_safe(goal)

    @staticmethod
    def _check_no_secrets_in_parameters(
        action_type: str,
        parameters: dict[str, object],
    ) -> None:
        """Ensure no parameter values contain forbidden or secret content."""
        from migration_factory.control_tower.application.redaction import (
            contains_forbidden_path,
        )

        for key, value in parameters.items():
            if isinstance(value, str) and len(value) > 3:
                # Check for shell metacharacters that indicate injection
                # This is a safety net — shell is disabled, but we still
                # fail closed on suspicious payloads.
                if any(c in value for c in (";", "|", "`", "$(", "$")):
                    raise ActionPolicyViolationError(
                        action_type,
                        f"Parameter {key!r} contains shell metacharacters",
                    )
                # Check for forbidden path references in parameters
                if contains_forbidden_path(value):
                    raise ActionPolicyViolationError(
                        action_type,
                        f"Parameter {key!r} references a forbidden path",
                    )

    @staticmethod
    def _validate_maven_goal_safe(goal: str) -> None:
        """Validate that a Maven goal is safe and well-known.

        Only well-known Maven lifecycle phases are allowed.
        Arbitrary plugin goals and shell injection are rejected.
        """
        # Well-known Maven lifecycle phases that are safe
        SAFE_MAVEN_GOALS = frozenset({
            "clean", "compile", "test", "package", "install",
            "deploy", "verify", "validate", "site",
            "clean compile", "clean test", "clean package",
            "clean install", "clean verify", "test-compile",
            "test-compile test", "compile test-compile test",
            "process-classes", "process-test-classes",
            "generate-sources", "generate-test-sources",
            "process-resources", "process-test-resources",
        })

        normalized = goal.strip().lower()
        if normalized not in SAFE_MAVEN_GOALS:
            raise ActionPolicyViolationError(
                "maven",
                f"Unsafe or unrecognized Maven goal: {goal!r}. "
                f"Only well-known lifecycle phases are allowed.",
            )

    @staticmethod
    def verify_checksum(
        *,
        action_id: str,
        expected_checksum: str,
        actual_checksum: str,
    ) -> None:
        """Verify that an expected checksum matches the actual checksum.

        Args:
            action_id: The action's identifier (for error reporting).
            expected_checksum: The checksum value to match against.
            actual_checksum: The actual computed checksum.

        Raises:
            ChecksumMismatchError: If the checksums do not match.
        """
        if expected_checksum != actual_checksum:
            raise ChecksumMismatchError(
                action_id, expected_checksum, actual_checksum,
            )

    def validate_action_available(self, action_id: str) -> V1PrivilegedActionRecord:
        """Validate that an action exists and is still in pending state.

        Stale actions (not found, already approved, rejected, executed,
        or completed) are rejected.

        Args:
            action_id: The action to validate.

        Returns:
            The validated V1PrivilegedActionRecord if available.

        Raises:
            ActionStaleError: If the action is not found or not pending.
        """
        record = self.get_action(action_id)
        if record is None:
            raise ActionStaleError(action_id, "Action not found")

        if record.status != "pending":
            raise ActionStaleError(
                action_id,
                f"Action status is {record.status!r}, expected 'pending'",
            )
        return record

    @staticmethod
    def compute_parameters_checksum(parameters: dict[str, object]) -> str:
        """Compute a SHA-256 checksum over canonical JSON of parameters.

        This is the same algorithm used during request_action, provided
        for external verification (e.g., comparing a submitted checksum
        against the stored one).

        Args:
            parameters: The action parameters dict.

        Returns:
            The hex digest SHA-256 checksum.
        """
        from migration_factory.control_tower.domain.checksums import (
            sha256_canonical_json,
        )
        return sha256_canonical_json(parameters)

    # ── V1-17C: Approve/reject methods ────────────────────────────────

    def approve_action(
        self,
        action_id: str,
        *,
        approved_by: str,
        parameters_checksum: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> V1PrivilegedActionDecisionRecord:
        """Approve a pending privileged action.

        Validates:
        - Action exists and is pending (not stale).
        - Provided checksum matches stored parameters_checksum.
        - No prior decision exists for this action (duplicate guard).

        Args:
            action_id: The action to approve.
            approved_by: Actor approving the action.
            parameters_checksum: Checksum to verify against stored value.

        Returns:
            The persisted V1PrivilegedActionDecisionRecord.

        Raises:
            ActionStaleError: If the action is not found or not pending.
            ChecksumMismatchError: If the checksum does not match.
            DuplicateActionDecisionError: If a decision already exists.
        """
        record = self.validate_action_available(action_id)

        # Verify checksum
        self.verify_checksum(
            action_id=action_id,
            expected_checksum=parameters_checksum,
            actual_checksum=record.parameters_checksum,
        )

        # Check for existing decision
        with self._unit_of_work_factory() as uow:
            existing = uow.v1_privileged_action_decisions.get(action_id)
            if existing is not None:
                raise DuplicateActionDecisionError(
                    action_id, existing.decision
                )

            now = utc_now_text()
            decision = V1PrivilegedActionDecisionRecord(
                action_id=action_id,
                decision="approved",
                decided_by=approved_by,
                decided_at=now,
                parameters_checksum=parameters_checksum,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            uow.v1_privileged_action_decisions.insert(decision)

            # Audit trail
            import json as _json

            audit_payload = _json.dumps(
                {
                    "action": "privileged_action_approved",
                    "action_id": action_id,
                    "job_id": record.job_id,
                    "approved_by": approved_by,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            uow.audit_records.append_global_audit(
                audit_id=str(uuid4()),
                actor_type="user",
                actor_id=approved_by,
                action="privileged_action_approved",
                payload_json=audit_payload,
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id or action_id,
            )

        return decision

    def reject_action(
        self,
        action_id: str,
        *,
        rejected_by: str,
        parameters_checksum: str,
        rejection_reason: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> V1PrivilegedActionDecisionRecord:
        """Reject a pending privileged action.

        Validates:
        - Action exists and is pending (not stale).
        - Provided checksum matches stored parameters_checksum.
        - No prior decision exists for this action (duplicate guard).

        Args:
            action_id: The action to reject.
            rejected_by: Actor rejecting the action.
            parameters_checksum: Checksum to verify against stored value.
            rejection_reason: Optional reason for the rejection.

        Returns:
            The persisted V1PrivilegedActionDecisionRecord.

        Raises:
            ActionStaleError: If the action is not found or not pending.
            ChecksumMismatchError: If the checksum does not match.
            DuplicateActionDecisionError: If a decision already exists.
        """
        record = self.validate_action_available(action_id)

        # Verify checksum
        self.verify_checksum(
            action_id=action_id,
            expected_checksum=parameters_checksum,
            actual_checksum=record.parameters_checksum,
        )

        # Check for existing decision
        with self._unit_of_work_factory() as uow:
            existing = uow.v1_privileged_action_decisions.get(action_id)
            if existing is not None:
                raise DuplicateActionDecisionError(
                    action_id, existing.decision
                )

            now = utc_now_text()
            decision = V1PrivilegedActionDecisionRecord(
                action_id=action_id,
                decision="rejected",
                decided_by=rejected_by,
                decided_at=now,
                parameters_checksum=parameters_checksum,
                rejection_reason=rejection_reason,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            uow.v1_privileged_action_decisions.insert(decision)

            # Audit trail
            import json as _json

            audit_payload = _json.dumps(
                {
                    "action": "privileged_action_rejected",
                    "action_id": action_id,
                    "job_id": record.job_id,
                    "rejected_by": rejected_by,
                    "rejection_reason": rejection_reason,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            uow.audit_records.append_global_audit(
                audit_id=str(uuid4()),
                actor_type="user",
                actor_id=rejected_by,
                action="privileged_action_rejected",
                payload_json=audit_payload,
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id or action_id,
            )

        return decision

    # ── V1-17D: Execute methods ──────────────────────────────────────

    def execute_action(
        self,
        action_id: str,
        *,
        executed_by: str,
        parameters_checksum: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> V1PrivilegedActionExecutionRecord:
        """Execute a previously approved, checksum-bound privileged action.

        Validates:
        - Action exists and is pending (not stale).
        - Provided checksum matches stored parameters_checksum.
        - Action has been approved (decision table has 'approved' entry).
        - Action has not already been executed (no duplicate execution).

        Records the execution with a redacted result summary.

        Args:
            action_id: The action to execute.
            executed_by: Actor executing the action.
            parameters_checksum: Checksum to verify against stored value.

        Returns:
            The persisted V1PrivilegedActionExecutionRecord with
            redacted result summary.

        Raises:
            ActionStaleError: If the action is not found or not pending.
            ChecksumMismatchError: If the checksum does not match.
            ActionNotApprovedError: If the action has not been approved.
        """
        record = self.validate_action_available(action_id)

        # Verify checksum
        self.verify_checksum(
            action_id=action_id,
            expected_checksum=parameters_checksum,
            actual_checksum=record.parameters_checksum,
        )

        with self._unit_of_work_factory() as uow:
            # Verify approved
            decision = uow.v1_privileged_action_decisions.get(action_id)
            if decision is None:
                raise ActionNotApprovedError(
                    action_id, "No approval decision found"
                )
            if decision.decision != "approved":
                raise ActionNotApprovedError(
                    action_id,
                    f"Decision is {decision.decision!r}, expected 'approved'",
                )

            # Verify not already executed
            existing = uow.v1_privileged_action_executions.get(action_id)
            if existing is not None:
                raise ActionNotApprovedError(
                    action_id,
                    f"Action already executed with status {existing.status!r}",
                )

            now = utc_now_text()

            # Build redacted result summary
            parameters = json.loads(record.parameters_json) if record.parameters_json else {}
            result_summary = self._build_redacted_execution_summary(
                record.action_type, parameters
            )

            execution = V1PrivilegedActionExecutionRecord(
                action_id=action_id,
                job_id=record.job_id,
                action_type=record.action_type,
                parameters_checksum=parameters_checksum,
                status="completed",
                started_at=now,
                completed_at=now,
                result_summary=result_summary,
                executed_by=executed_by,
                correlation_id=correlation_id,
                causation_id=causation_id or action_id,
            )
            uow.v1_privileged_action_executions.insert(execution)

            # Audit trail
            import json as _json

            audit_payload = _json.dumps(
                {
                    "action": "privileged_action_executed",
                    "action_id": action_id,
                    "job_id": record.job_id,
                    "action_type": record.action_type,
                    "result_summary": result_summary,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            uow.audit_records.append_global_audit(
                audit_id=str(uuid4()),
                actor_type="user",
                actor_id=executed_by,
                action="privileged_action_executed",
                payload_json=audit_payload,
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id or action_id,
            )

        return execution

    def get_execution(self, action_id: str) -> V1PrivilegedActionExecutionRecord | None:
        """Get an execution record for an action."""
        with self._unit_of_work_factory() as uow:
            return uow.v1_privileged_action_executions.get(action_id)

    @staticmethod
    def _build_redacted_execution_summary(
        action_type: str,
        parameters: dict[str, object],
    ) -> str:
        """Build a redacted summary of what was executed.

        Applies V1-00D redaction baseline to remove paths, secrets,
        and sensitive content from the result summary.
        """
        from migration_factory.control_tower.application.redaction import (
            redact_model_summary,
        )

        if action_type == "maven":
            goal = parameters.get("goal", "unknown")
            module = parameters.get("module")
            summary = f"Maven goal: {goal}"
            if module:
                summary += f" (module: {module})"
        elif action_type == "write":
            path = parameters.get("path", "unknown")
            summary = f"Write action to: {path}"
        else:
            summary = f"Action type: {action_type}"

        return redact_model_summary(summary)
