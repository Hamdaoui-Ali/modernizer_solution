"""V1-14A deterministic repair classification and fake proposal recording."""

from __future__ import annotations

import json
import re
from uuid import uuid4

from migration_factory.control_tower.application.dto import (
    FakeRepairProposalDto,
    RepairAttemptDto,
    RepairClassificationDto,
    RepairStatusDto,
)
from migration_factory.control_tower.application.redaction import redact_model_summary
from migration_factory.control_tower.domain.checksums import canonical_json_text, sha256_canonical_json, utc_now_text
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.entities import (
    V1FakeRepairProposalRecord,
    V1RepairClassificationRecord,
)
from migration_factory.control_tower.domain.errors import (
    NotFoundError,
    RepairAttemptLimitExceededError,
    RepairClassificationError,
    RepairProposalValidationError,
)


_REPAIRABLE_IMPORT_PATTERNS: tuple[str, ...] = (
    "importerror",
    "modulenotfounderror",
    "cannot find symbol",
    "package does not exist",
    "no module named",
    "unresolved import",
)
_REPAIRABLE_COMPILE_PATTERNS: tuple[str, ...] = (
    "compilation failure",
    "compile error",
    "failed to compile",
    "syntaxerror",
    "javac",
)
_REPAIRABLE_TEST_PATTERNS: tuple[str, ...] = (
    "assertionerror",
    "tests failed",
    "test failure",
    "pytest",
    "junit",
    "expected:",
    "actual:",
)
_NOT_REPAIRABLE_POLICY_PATTERNS: tuple[str, ...] = (
    "policy violation",
    "approval required",
    "forbidden",
    "not allowed",
    "unsupported",
    "boot 4",
)
_NOT_REPAIRABLE_INFRA_PATTERNS: tuple[str, ...] = (
    "timed out",
    "timeout",
    "connection reset",
    "dns",
    "network is unreachable",
    "no space left",
    "out of memory",
    "permission denied",
    "access is denied",
    "controller ownership",
    "busy_timeout",
)
_PATCH_MARKERS: tuple[str, ...] = (
    "diff --git",
    "@@",
    "*** begin patch",
    "*** update file:",
    "*** add file:",
    "```patch",
)
_COMMAND_LINE_RE = re.compile(
    r"\b(?:mvn(?:w)?|gradle|bash|sh|cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh|java)\b[^\r\n]*",
    re.IGNORECASE,
)
_STACK_TRACE_RE = re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE)


class RepairService:
    def __init__(self, unit_of_work_factory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def classify_failed_command(
        self,
        *,
        command_id: str,
        evidence_kind: str,
        failure_summary: str,
        actor_type: str,
        actor_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> RepairClassificationDto:
        cleaned_kind = evidence_kind.strip().lower() or "command_failure"
        raw_summary = failure_summary.strip()
        if not raw_summary:
            raise RepairClassificationError("failure_summary must not be empty")
        redacted_summary = redact_model_summary(raw_summary)
        public_summary = _sanitize_public_summary(redacted_summary)

        with self._unit_of_work_factory() as uow:
            command = uow.command_executions.get(command_id)
            if command is None:
                raise NotFoundError("command execution", command_id)
            _ensure_command_classifiable(command.status)
            evidence_checksum = sha256_canonical_json(
                {
                    "command_status": command.status.value,
                    "evidence_kind": cleaned_kind,
                    "failure_summary": public_summary,
                    "operation": command.operation,
                }
            )
            existing = uow.v1_repair_classifications.get_by_command_and_checksum(
                command_id,
                evidence_checksum,
            )
            if existing is not None:
                return self._to_classification_dto(existing)

            classification_code, reason_code, repairable, attempt_limit = _classify_repairability(
                command.status,
                redacted_summary,
            )
            record = V1RepairClassificationRecord(
                classification_id=f"repair-{uuid4().hex}",
                command_id=command.command_id,
                job_id=command.job_id,
                command_status=command.status.value,
                evidence_kind=cleaned_kind,
                evidence_summary=public_summary,
                evidence_checksum=evidence_checksum,
                classification_code=classification_code,
                reason_code=reason_code,
                repairable=repairable,
                attempt_limit=attempt_limit,
                actor_type=actor_type,
                actor_id=actor_id,
                created_at=utc_now_text(),
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            uow.v1_repair_classifications.insert(record)
            uow.audit_records.append_global_audit(
                audit_id=uuid4().hex,
                actor_type=actor_type,
                actor_id=actor_id,
                action="repair_classification_recorded",
                payload_json=canonical_json_text(
                    {
                        "classification_id": record.classification_id,
                        "command_id": record.command_id,
                        "job_id": record.job_id,
                        "classification_code": record.classification_code,
                        "reason_code": record.reason_code,
                        "evidence_checksum": record.evidence_checksum,
                    }
                ),
                created_at=record.created_at,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        return self._to_classification_dto(record)

    def record_fake_repair_proposal(
        self,
        *,
        command_id: str,
        proposal_summary: str,
        actor_type: str,
        actor_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> FakeRepairProposalDto:
        raw_summary = proposal_summary.strip()
        _validate_fake_proposal_summary(raw_summary)
        public_summary = _sanitize_public_summary(redact_model_summary(raw_summary))
        now = utc_now_text()

        with self._unit_of_work_factory() as uow:
            command = uow.command_executions.get(command_id)
            if command is None:
                raise NotFoundError("command execution", command_id)
            classification = uow.v1_repair_classifications.get_latest_for_command(command_id)
            if classification is None:
                raise RepairClassificationError(
                    f"Command {command_id!r} has no repair classification"
                )
            if not classification.repairable:
                raise RepairClassificationError(
                    f"Command {command_id!r} is not repairable"
                )
            proposal_checksum = sha256_canonical_json(
                {
                    "classification_id": classification.classification_id,
                    "proposal_summary": public_summary,
                }
            )
            record = self._persist_proposal_record(
                uow=uow,
                classification=classification,
                proposal_summary=public_summary,
                proposal_checksum=proposal_checksum,
                proposal_kind="manual",
                recommendation_type=None,
                confidence_label=None,
                confidence_score=None,
                warning_codes=(),
                applicable=True,
                context_checksum=None,
                actor_type=actor_type,
                actor_id=actor_id,
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
                audit_action="fake_repair_proposal_recorded",
            )
        return self._to_proposal_dto(record)

    def generate_fake_repair_proposal(
        self,
        *,
        command_id: str,
        actor_type: str,
        actor_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> FakeRepairProposalDto:
        now = utc_now_text()
        with self._unit_of_work_factory() as uow:
            command = uow.command_executions.get(command_id)
            if command is None:
                raise NotFoundError("command execution", command_id)
            classification = uow.v1_repair_classifications.get_latest_for_command(command_id)
            if classification is None:
                raise RepairClassificationError(
                    f"Command {command_id!r} has no repair classification"
                )
            if not classification.repairable:
                raise RepairClassificationError(
                    f"Command {command_id!r} is not repairable"
                )

            existing_proposals = uow.v1_fake_repair_proposals.list_for_classification(
                classification.classification_id
            )
            context_checksum = self._generated_context_checksum(classification, existing_proposals)
            existing = uow.v1_fake_repair_proposals.get_for_classification_kind_and_context(
                classification.classification_id,
                "generated",
                context_checksum,
            )
            if existing is not None:
                return self._to_proposal_dto(existing)
            if len(existing_proposals) >= classification.attempt_limit:
                raise RepairAttemptLimitExceededError(command_id, classification.attempt_limit)

            generated = _generate_fake_provider_metadata(
                classification=classification,
                existing_proposals=existing_proposals,
            )
            proposal_checksum = sha256_canonical_json(
                {
                    "classification_id": classification.classification_id,
                    "proposal_kind": "generated",
                    "context_checksum": context_checksum,
                    "recommendation_type": generated["recommendation_type"],
                    "proposal_summary": generated["proposal_summary"],
                    "confidence_label": generated["confidence_label"],
                    "confidence_score": generated["confidence_score"],
                    "warning_codes": generated["warning_codes"],
                    "applicable": True,
                }
            )
            record = self._persist_proposal_record(
                uow=uow,
                classification=classification,
                proposal_summary=generated["proposal_summary"],
                proposal_checksum=proposal_checksum,
                proposal_kind="generated",
                recommendation_type=str(generated["recommendation_type"]),
                confidence_label=str(generated["confidence_label"]),
                confidence_score=float(generated["confidence_score"]),
                warning_codes=tuple(str(item) for item in generated["warning_codes"]),
                applicable=True,
                context_checksum=context_checksum,
                actor_type=actor_type,
                actor_id=actor_id,
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
                audit_action="fake_repair_proposal_generated",
            )
        return self._to_proposal_dto(record)

    def get_repair_status(self, command_id: str) -> RepairStatusDto:
        with self._unit_of_work_factory() as uow:
            command = uow.command_executions.get(command_id)
            if command is None:
                raise NotFoundError("command execution", command_id)
            classification = uow.v1_repair_classifications.get_latest_for_command(command_id)
            if classification is None:
                return RepairStatusDto(
                    command_id=command.command_id,
                    job_id=command.job_id,
                    command_status=command.status.value,
                    classification=None,
                    attempts_used=0,
                    proposal_count=0,
                    attempt_limit=0,
                    remaining_attempts=0,
                    eligible_for_fake_repair=False,
                    proposals=(),
                    attempts=(),
                )
            proposals = tuple(
                self._to_proposal_dto(item)
                for item in uow.v1_fake_repair_proposals.list_for_classification(
                    classification.classification_id
                )
            )
        attempts = tuple(
            self._proposal_to_attempt_dto(item)
            for item in proposals
            if item.proposal_kind != "generated"
        )
        proposal_count = len(proposals)
        remaining_attempts = max(0, classification.attempt_limit - proposal_count)
        return RepairStatusDto(
            command_id=classification.command_id,
            job_id=classification.job_id,
            command_status=classification.command_status,
            classification=self._to_classification_dto(classification),
            attempts_used=proposal_count,
            proposal_count=proposal_count,
            attempt_limit=classification.attempt_limit,
            remaining_attempts=remaining_attempts,
            eligible_for_fake_repair=classification.repairable and remaining_attempts > 0,
            proposals=proposals,
            attempts=attempts,
        )

    def record_repair_attempt(
        self,
        *,
        command_id: str,
        attempt_summary: str,
        actor_type: str,
        actor_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> RepairAttemptDto:
        raw_summary = attempt_summary.strip()
        _validate_fake_proposal_summary(raw_summary)
        public_summary = _sanitize_public_summary(redact_model_summary(raw_summary))
        now = utc_now_text()
        with self._unit_of_work_factory() as uow:
            command = uow.command_executions.get(command_id)
            if command is None:
                raise NotFoundError("command execution", command_id)
            classification = uow.v1_repair_classifications.get_latest_for_command(command_id)
            if classification is None:
                raise RepairClassificationError(
                    f"Command {command_id!r} has no repair classification"
                )
            if not classification.repairable:
                raise RepairClassificationError(
                    f"Command {command_id!r} is not repairable"
                )
            proposal_checksum = sha256_canonical_json(
                {
                    "classification_id": classification.classification_id,
                    "proposal_summary": public_summary,
                }
            )
            record = self._persist_proposal_record(
                uow=uow,
                classification=classification,
                proposal_summary=public_summary,
                proposal_checksum=proposal_checksum,
                proposal_kind="repair_attempt",
                recommendation_type=None,
                confidence_label=None,
                confidence_score=None,
                warning_codes=(),
                applicable=True,
                context_checksum=None,
                actor_type=actor_type,
                actor_id=actor_id,
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
                audit_action="repair_attempt_recorded",
            )
        return self._proposal_to_attempt_dto(record)

    def list_repair_attempts(self, command_id: str) -> tuple[RepairAttemptDto, ...]:
        status = self.get_repair_status(command_id)
        return tuple(
            attempt
            for attempt in status.attempts
            if attempt.attempt_status in {"recorded", "manual"}
        )

    def list_fake_repair_proposals(self, command_id: str) -> tuple[FakeRepairProposalDto, ...]:
        status = self.get_repair_status(command_id)
        return status.proposals

    def _persist_proposal_record(
        self,
        *,
        uow,
        classification: V1RepairClassificationRecord,
        proposal_summary: str,
        proposal_checksum: str,
        proposal_kind: str,
        recommendation_type: str | None,
        confidence_label: str | None,
        confidence_score: float | None,
        warning_codes: tuple[str, ...],
        applicable: bool,
        context_checksum: str | None,
        actor_type: str,
        actor_id: str,
        created_at: str,
        correlation_id: str | None,
        causation_id: str | None,
        audit_action: str,
    ) -> V1FakeRepairProposalRecord:
        existing_proposals = uow.v1_fake_repair_proposals.list_for_classification(
            classification.classification_id
        )
        existing = uow.v1_fake_repair_proposals.get_for_classification_and_checksum(
            classification.classification_id,
            proposal_checksum,
        )
        if existing is not None:
            return existing
        if len(existing_proposals) >= classification.attempt_limit:
            raise RepairAttemptLimitExceededError(classification.command_id, classification.attempt_limit)

        record = V1FakeRepairProposalRecord(
            proposal_id=f"fpr-{uuid4().hex}",
            classification_id=classification.classification_id,
            command_id=classification.command_id,
            job_id=classification.job_id,
            proposal_order=len(existing_proposals) + 1,
            proposal_summary=proposal_summary,
            proposal_checksum=proposal_checksum,
            actor_type=actor_type,
            actor_id=actor_id,
            created_at=created_at,
            proposal_kind=proposal_kind,
            recommendation_type=recommendation_type,
            confidence_label=confidence_label,
            confidence_score=confidence_score,
            warning_codes_json=canonical_json_text(list(warning_codes)),
            applicable=applicable,
            context_checksum=context_checksum,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        uow.v1_fake_repair_proposals.insert(record)
        uow.audit_records.append_global_audit(
            audit_id=uuid4().hex,
            actor_type=actor_type,
            actor_id=actor_id,
            action=audit_action,
            payload_json=canonical_json_text(
                {
                    "proposal_id": record.proposal_id,
                    "classification_id": record.classification_id,
                    "command_id": record.command_id,
                    "proposal_order": record.proposal_order,
                    "proposal_kind": record.proposal_kind,
                    "proposal_checksum": record.proposal_checksum,
                    "recommendation_type": record.recommendation_type,
                    "warning_codes": list(warning_codes),
                    "applicable": record.applicable,
                }
            ),
            created_at=created_at,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        return record

    def _generated_context_checksum(
        self,
        classification: V1RepairClassificationRecord,
        existing_proposals: tuple[V1FakeRepairProposalRecord, ...],
    ) -> str:
        context_rows = [
            {
                "proposal_kind": proposal.proposal_kind,
                "proposal_checksum": proposal.proposal_checksum,
                "proposal_order": proposal.proposal_order,
            }
            for proposal in existing_proposals
            if proposal.proposal_kind != "generated"
        ]
        return sha256_canonical_json(
            {
                "classification_id": classification.classification_id,
                "classification_code": classification.classification_code,
                "reason_code": classification.reason_code,
                "evidence_checksum": classification.evidence_checksum,
                "context_rows": context_rows,
            }
        )

    def _to_classification_dto(
        self,
        record: V1RepairClassificationRecord,
    ) -> RepairClassificationDto:
        return RepairClassificationDto(
            classification_id=record.classification_id,
            command_id=record.command_id,
            job_id=record.job_id,
            command_status=record.command_status,
            evidence_kind=record.evidence_kind,
            evidence_summary=record.evidence_summary,
            evidence_checksum=record.evidence_checksum,
            classification_code=record.classification_code,
            reason_code=record.reason_code,
            repairable=record.repairable,
            attempt_limit=record.attempt_limit,
            actor_type=record.actor_type,
            actor_id=record.actor_id,
            created_at=record.created_at,
        )

    def _to_proposal_dto(self, record: V1FakeRepairProposalRecord) -> FakeRepairProposalDto:
        warning_codes = tuple(
            str(item)
            for item in json.loads(record.warning_codes_json)
        )
        return FakeRepairProposalDto(
            proposal_id=record.proposal_id,
            classification_id=record.classification_id,
            command_id=record.command_id,
            job_id=record.job_id,
            proposal_order=record.proposal_order,
            proposal_kind=record.proposal_kind,
            proposal_summary=record.proposal_summary,
            proposal_checksum=record.proposal_checksum,
            recommendation_type=record.recommendation_type,
            confidence_label=record.confidence_label,
            confidence_score=record.confidence_score,
            warning_codes=warning_codes,
            applicable=record.applicable,
            context_checksum=record.context_checksum,
            actor_type=record.actor_type,
            actor_id=record.actor_id,
            created_at=record.created_at,
        )

    def _proposal_to_attempt_dto(
        self,
        proposal: FakeRepairProposalDto | V1FakeRepairProposalRecord,
    ) -> RepairAttemptDto:
        proposal_id = proposal.proposal_id
        classification_id = proposal.classification_id
        command_id = proposal.command_id
        job_id = proposal.job_id
        proposal_order = proposal.proposal_order
        proposal_summary = proposal.proposal_summary
        proposal_checksum = proposal.proposal_checksum
        actor_type = proposal.actor_type
        actor_id = proposal.actor_id
        created_at = proposal.created_at
        attempt_status = "generated" if getattr(proposal, "proposal_kind", "manual") == "generated" else "recorded"
        return RepairAttemptDto(
            attempt_id=proposal_id,
            classification_id=classification_id,
            command_id=command_id,
            job_id=job_id,
            attempt_order=proposal_order,
            attempt_status=attempt_status,
            attempt_summary=proposal_summary,
            attempt_checksum=proposal_checksum,
            actor_type=actor_type,
            actor_id=actor_id,
            created_at=created_at,
        )


def _ensure_command_classifiable(status: CommandState) -> None:
    if status not in {CommandState.FAILED, CommandState.TIMED_OUT, CommandState.CANCELLED}:
        raise RepairClassificationError(
            f"Command status {status.value!r} is not classifiable for repair"
        )


def _classify_repairability(
    status: CommandState,
    summary: str,
) -> tuple[str, str, bool, int]:
    lowered = summary.lower()
    if status in {CommandState.TIMED_OUT, CommandState.CANCELLED}:
        return ("not_repairable_infrastructure", "infrastructure_timeout_or_cancelled", False, 0)
    if any(token in lowered for token in _NOT_REPAIRABLE_POLICY_PATTERNS):
        return ("not_repairable_policy", "policy_violation_detected", False, 0)
    if any(token in lowered for token in _NOT_REPAIRABLE_INFRA_PATTERNS):
        return ("not_repairable_infrastructure", "infrastructure_failure_detected", False, 0)
    if any(token in lowered for token in _REPAIRABLE_IMPORT_PATTERNS):
        return ("repairable_dependency_or_import", "dependency_import_missing", True, 2)
    if any(token in lowered for token in _REPAIRABLE_COMPILE_PATTERNS):
        return ("repairable_compile_error", "compile_error_detected", True, 2)
    if any(token in lowered for token in _REPAIRABLE_TEST_PATTERNS):
        return ("repairable_test_failure", "test_failure_detected", True, 1)
    return ("not_repairable_unknown", "unknown_failure_signature", False, 0)


def _sanitize_public_summary(summary: str) -> str:
    sanitized = _COMMAND_LINE_RE.sub("[redacted-command]", summary)
    sanitized = _STACK_TRACE_RE.sub("[redacted-stack-trace]", sanitized)
    return sanitized.strip()


def _validate_fake_proposal_summary(summary: str) -> None:
    if not summary:
        raise RepairProposalValidationError("proposal_summary must not be empty")
    lowered = summary.lower()
    if any(marker in lowered for marker in _PATCH_MARKERS):
        raise RepairProposalValidationError("proposal_summary must not contain patch content")
    if redact_model_summary(summary) != summary:
        raise RepairProposalValidationError(
            "proposal_summary contains unsafe raw content that must be redacted"
        )


def _generate_fake_provider_metadata(
    *,
    classification: V1RepairClassificationRecord,
    existing_proposals: tuple[V1FakeRepairProposalRecord, ...],
) -> dict[str, object]:
    remaining_attempts = max(0, classification.attempt_limit - len(existing_proposals) - 1)
    if classification.classification_code == "repairable_dependency_or_import":
        recommendation_type = "dependency_alignment"
        proposal_summary = (
            "Review missing dependency and import references, then prepare next repair attempt "
            "to align package declarations and resolved symbols."
        )
        confidence_label = "medium"
    elif classification.classification_code == "repairable_compile_error":
        recommendation_type = "compile_fixup"
        proposal_summary = (
            "Review compile-time type or signature mismatches, then prepare next repair attempt "
            "to align code references with current build contracts."
        )
        confidence_label = "medium"
    elif classification.classification_code == "repairable_test_failure":
        recommendation_type = "test_expectation_review"
        proposal_summary = (
            "Review failing assertions and expected outputs, then prepare next repair attempt "
            "to narrow changes to affected test behavior."
        )
        confidence_label = "low"
    else:
        raise RepairClassificationError(
            f"Command {classification.command_id!r} is not repairable"
        )

    warning_codes = ["manual_review_required", "non_authoritative"]
    if remaining_attempts == 0:
        warning_codes.append("limit_reached_after_this_proposal")
    elif remaining_attempts == 1:
        warning_codes.append("limit_nearly_reached")

    return {
        "recommendation_type": recommendation_type,
        "proposal_summary": proposal_summary,
        "confidence_label": confidence_label,
        "confidence_score": 0.6 if confidence_label == "medium" else 0.4,
        "warning_codes": tuple(warning_codes),
    }
