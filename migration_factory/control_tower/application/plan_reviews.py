"""V1-13 reviewer gate for plan revisions."""

from __future__ import annotations

from uuid import uuid4

from migration_factory.control_tower.application.dto import (
    PlanReviewDecisionDto,
    PlanReviewStatusDto,
)
from migration_factory.control_tower.application.redaction import redact_public_value
from migration_factory.control_tower.domain.checksums import canonical_json_text, utc_now_text
from migration_factory.control_tower.domain.entities import V1PlanReviewDecisionRecord
from migration_factory.control_tower.domain.errors import (
    NotFoundError,
    PlanReviewChecksumMismatchError,
    PlanReviewConflictError,
)


_ALLOWED_REVIEW_DECISIONS = {"approved", "rejected"}


class PlanReviewService:
    def __init__(self, unit_of_work_factory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def record_review_decision(
        self,
        *,
        revision_id: str,
        expected_checksum: str,
        decision: str,
        review_summary: str,
        actor_type: str,
        actor_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> PlanReviewDecisionDto:
        normalized_decision = decision.strip().lower()
        if normalized_decision not in _ALLOWED_REVIEW_DECISIONS:
            raise PlanReviewConflictError(
                f"Unsupported review decision {decision!r}; expected approved or rejected"
            )
        cleaned_checksum = expected_checksum.strip()
        if not cleaned_checksum:
            raise PlanReviewConflictError("expected_checksum must not be empty")
        cleaned_summary = review_summary.strip() or f"Reviewer {normalized_decision}"
        redacted_summary = str(redact_public_value(cleaned_summary))
        now = utc_now_text()

        with self._unit_of_work_factory() as uow:
            revision = uow.v1_plan_revisions.get(revision_id)
            if revision is None:
                raise NotFoundError("plan revision", revision_id)
            if cleaned_checksum != revision.payload_checksum:
                raise PlanReviewChecksumMismatchError(revision_id)

            existing = uow.v1_plan_review_decisions.get_for_revision(revision_id)
            if existing is not None:
                if (
                    existing.reviewed_checksum == cleaned_checksum
                    and existing.decision == normalized_decision
                    and existing.review_summary == redacted_summary
                ):
                    return self.to_decision_dto(existing)
                raise PlanReviewConflictError(
                    f"Plan revision {revision_id!r} already has a recorded review decision"
                )

            record = V1PlanReviewDecisionRecord(
                review_decision_id=f"revw-{uuid4().hex}",
                revision_id=revision.revision_id,
                amendment_id=revision.amendment_id,
                job_id=revision.job_id,
                decision=normalized_decision,
                reviewed_checksum=cleaned_checksum,
                review_summary=redacted_summary,
                actor_type=actor_type,
                actor_id=actor_id,
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            uow.v1_plan_review_decisions.insert(record)
            uow.audit_records.append_global_audit(
                audit_id=uuid4().hex,
                actor_type=actor_type,
                actor_id=actor_id,
                action="plan_review_decision_recorded",
                payload_json=canonical_json_text(
                    {
                        "review_decision_id": record.review_decision_id,
                        "revision_id": revision.revision_id,
                        "amendment_id": revision.amendment_id,
                        "job_id": revision.job_id,
                        "decision": normalized_decision,
                        "reviewed_checksum": cleaned_checksum,
                    }
                ),
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        return self.to_decision_dto(record)

    def get_review_status(self, revision_id: str) -> PlanReviewStatusDto:
        with self._unit_of_work_factory() as uow:
            revision = uow.v1_plan_revisions.get(revision_id)
            if revision is None:
                raise NotFoundError("plan revision", revision_id)
            review = uow.v1_plan_review_decisions.get_for_revision(revision_id)

        if review is None:
            return PlanReviewStatusDto(
                revision_id=revision.revision_id,
                amendment_id=revision.amendment_id,
                job_id=revision.job_id,
                payload_checksum=revision.payload_checksum,
                review_required=True,
                eligible_for_downstream=False,
                status="pending_review",
            )

        eligible = review.decision == "approved" and review.reviewed_checksum == revision.payload_checksum
        return PlanReviewStatusDto(
            revision_id=revision.revision_id,
            amendment_id=revision.amendment_id,
            job_id=revision.job_id,
            payload_checksum=revision.payload_checksum,
            review_required=True,
            eligible_for_downstream=eligible,
            status="approved" if eligible else "rejected",
            decision=review.decision,
            review_summary=review.review_summary,
            review_decision_id=review.review_decision_id,
            reviewed_checksum=review.reviewed_checksum,
            created_at=review.created_at,
        )

    def to_decision_dto(self, record: V1PlanReviewDecisionRecord) -> PlanReviewDecisionDto:
        return PlanReviewDecisionDto(
            review_decision_id=record.review_decision_id,
            revision_id=record.revision_id,
            amendment_id=record.amendment_id,
            job_id=record.job_id,
            decision=record.decision,
            reviewed_checksum=record.reviewed_checksum,
            review_summary=record.review_summary,
            actor_type=record.actor_type,
            actor_id=record.actor_id,
            created_at=record.created_at,
        )
