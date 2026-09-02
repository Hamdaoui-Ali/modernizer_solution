"""V1-12A plan amendment and revision persistence services."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from migration_factory.control_tower.application.dto import (
    PlanAmendmentDto,
    PlanPreviewDto,
    PlanRevisionDto,
)
from migration_factory.control_tower.domain.checksums import (
    canonical_json_text,
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.domain.entities import (
    V1PlanAmendmentRecord,
    V1PlanRevisionRecord,
)
from migration_factory.control_tower.domain.errors import (
    NotFoundError,
    PlanAmendmentValidationError,
    PlanRevisionConflictError,
)
from migration_factory.control_tower.application.redaction import redact_public_value


_ALLOWED_SOURCE_KINDS = {"manual", "fake_provider"}
_ALLOWED_CHANGE_TYPES = {"instruction", "validation", "documentation", "metadata"}
_ALLOWED_REVISION_STATES = {"draft", "accepted", "rejected", "finalized"}


@dataclass(frozen=True, slots=True)
class PlanChange:
    stage_index: int
    change_type: str
    description: str
    rationale: str | None = None


def _clean_text(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise PlanAmendmentValidationError(f"{field_name} must not be empty")
    return cleaned


def _normalize_source_kind(value: str) -> str:
    if value not in _ALLOWED_SOURCE_KINDS:
        raise PlanAmendmentValidationError(
            f"Unsupported source_kind {value!r}; expected one of {_ALLOWED_SOURCE_KINDS!r}"
        )
    return value


def _normalize_revision_state(value: str) -> str:
    if value not in _ALLOWED_REVISION_STATES:
        raise PlanAmendmentValidationError(
            f"Unsupported revision_state {value!r}; expected one of {_ALLOWED_REVISION_STATES!r}"
        )
    return value


def _normalize_changes(changes: tuple[PlanChange, ...]) -> tuple[PlanChange, ...]:
    if not changes:
        raise PlanAmendmentValidationError("changes must contain at least one stage change")

    normalized: list[PlanChange] = []
    for change in changes:
        if change.stage_index < 1 or change.stage_index > 3:
            raise PlanAmendmentValidationError("stage_index must stay within locked V1 stages 1..3")
        if change.change_type not in _ALLOWED_CHANGE_TYPES:
            raise PlanAmendmentValidationError(
                f"Unsupported change_type {change.change_type!r}; expected one of {_ALLOWED_CHANGE_TYPES!r}"
            )
        normalized.append(
            PlanChange(
                stage_index=change.stage_index,
                change_type=change.change_type,
                description=_clean_text(change.description, field_name="description"),
                rationale=None if change.rationale is None else _clean_text(change.rationale, field_name="rationale"),
            )
        )
    return tuple(normalized)


def _payload_dict(
    *,
    title: str,
    summary: str,
    notes: tuple[str, ...],
    changes: tuple[PlanChange, ...],
) -> dict[str, object]:
    return {
        "title": _clean_text(title, field_name="title"),
        "summary": _clean_text(summary, field_name="summary"),
        "notes": tuple(_clean_text(note, field_name="note") for note in notes),
        "changes": tuple(
            {
                "stage_index": change.stage_index,
                "change_type": change.change_type,
                "description": change.description,
                "rationale": change.rationale,
            }
            for change in _normalize_changes(changes)
        ),
    }


def _redacted_summary(
    *,
    source_kind: str,
    title: str,
    summary: str,
    changes: tuple[PlanChange, ...],
) -> dict[str, object]:
    affected = tuple(sorted({change.stage_index for change in changes}))
    change_types = tuple(sorted({change.change_type for change in changes}))
    return {
        "source_kind": source_kind,
        "title": title,
        "summary": summary,
        "change_count": len(changes),
        "affected_stage_indexes": affected,
        "change_types": change_types,
        "non_authoritative": True,
    }


class PlanAmendmentService:
    """Persist non-authoritative amendments and immutable revisions."""

    def __init__(self, unit_of_work_factory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create_amendment(
        self,
        *,
        job_id: str,
        source_kind: str,
        title: str,
        summary: str,
        notes: tuple[str, ...] = (),
        changes: tuple[PlanChange, ...],
        created_by: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> V1PlanAmendmentRecord:
        normalized_source = _normalize_source_kind(source_kind)
        payload = _payload_dict(title=title, summary=summary, notes=notes, changes=changes)
        payload_json = canonical_json_text(payload)
        payload_checksum = sha256_canonical_json(payload)
        summary_dict = _redacted_summary(
            source_kind=normalized_source,
            title=str(payload["title"]),
            summary=str(payload["summary"]),
            changes=_normalize_changes(changes),
        )
        now = utc_now_text()
        record = V1PlanAmendmentRecord(
            amendment_id=f"am-{uuid4().hex}",
            job_id=job_id,
            source_kind=normalized_source,
            title=str(payload["title"]),
            summary=str(payload["summary"]),
            payload_json=payload_json,
            payload_checksum=payload_checksum,
            redacted_summary_json=canonical_json_text(summary_dict),
            created_at=now,
            created_by=_clean_text(created_by, field_name="created_by"),
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        with self._unit_of_work_factory() as uow:
            if uow.migration_jobs.get(job_id) is None:
                raise NotFoundError("migration job", job_id)
            uow.v1_plan_amendments.insert(record)
            uow.audit_records.append_global_audit(
                audit_id=uuid4().hex,
                actor_type="system",
                actor_id=record.created_by,
                action="plan_amendment_persisted",
                payload_json=canonical_json_text(
                    {
                        "amendment_id": record.amendment_id,
                        "job_id": job_id,
                        "payload_checksum": payload_checksum,
                        "source_kind": normalized_source,
                    }
                ),
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        return record

    def create_revision(
        self,
        *,
        amendment_id: str,
        source_kind: str,
        title: str,
        summary: str,
        notes: tuple[str, ...] = (),
        changes: tuple[PlanChange, ...],
        created_by: str,
        revision_order: int | None = None,
        revision_state: str = "draft",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> V1PlanRevisionRecord:
        normalized_source = _normalize_source_kind(source_kind)
        normalized_state = _normalize_revision_state(revision_state)
        payload = _payload_dict(title=title, summary=summary, notes=notes, changes=changes)
        payload_json = canonical_json_text(payload)
        payload_checksum = sha256_canonical_json(payload)
        normalized_changes = _normalize_changes(changes)
        summary_dict = _redacted_summary(
            source_kind=normalized_source,
            title=str(payload["title"]),
            summary=str(payload["summary"]),
            changes=normalized_changes,
        )
        now = utc_now_text()

        with self._unit_of_work_factory() as uow:
            amendment = uow.v1_plan_amendments.get(amendment_id)
            if amendment is None:
                raise NotFoundError("plan amendment", amendment_id)
            if uow.v1_plan_revisions.has_terminal_revision(amendment_id):
                raise PlanRevisionConflictError(
                    f"Plan amendment {amendment_id!r} already has a terminal accepted/finalized revision"
                )
            resolved_order = revision_order if revision_order is not None else uow.v1_plan_revisions.next_revision_order(amendment_id)
            if resolved_order < 1:
                raise PlanAmendmentValidationError("revision_order must be >= 1")
            decided_at = now if normalized_state != "draft" else None
            decided_by = _clean_text(created_by, field_name="created_by") if normalized_state != "draft" else None
            record = V1PlanRevisionRecord(
                revision_id=f"rev-{uuid4().hex}",
                amendment_id=amendment_id,
                job_id=amendment.job_id,
                revision_order=resolved_order,
                revision_state=normalized_state,
                source_kind=normalized_source,
                payload_json=payload_json,
                payload_checksum=payload_checksum,
                redacted_summary_json=canonical_json_text(summary_dict),
                created_at=now,
                created_by=_clean_text(created_by, field_name="created_by"),
                decided_at=decided_at,
                decided_by=decided_by,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            uow.v1_plan_revisions.insert(record)
            uow.audit_records.append_global_audit(
                audit_id=uuid4().hex,
                actor_type="system",
                actor_id=record.created_by,
                action="plan_revision_persisted",
                payload_json=canonical_json_text(
                    {
                        "revision_id": record.revision_id,
                        "amendment_id": amendment_id,
                        "job_id": amendment.job_id,
                        "revision_order": resolved_order,
                        "revision_state": normalized_state,
                        "payload_checksum": payload_checksum,
                    }
                ),
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        return record

    def preview_amendment(
        self,
        *,
        job_id: str,
        source_kind: str,
        title: str,
        summary: str,
        notes: tuple[str, ...] = (),
        changes: tuple[PlanChange, ...],
    ) -> PlanPreviewDto:
        normalized_source = _normalize_source_kind(source_kind)
        payload = _payload_dict(title=title, summary=summary, notes=notes, changes=changes)
        normalized_changes = _normalize_changes(changes)
        with self._unit_of_work_factory() as uow:
            if uow.migration_jobs.get(job_id) is None:
                raise NotFoundError("migration job", job_id)
        summary_dict = _redacted_summary(
            source_kind=normalized_source,
            title=str(payload["title"]),
            summary=str(payload["summary"]),
            changes=normalized_changes,
        )
        redacted_summary = redact_public_value(summary_dict)
        return PlanPreviewDto(
            job_id=job_id,
            source_kind=normalized_source,
            title=str(redacted_summary["title"]),
            summary=str(redacted_summary["summary"]),
            payload_checksum=sha256_canonical_json(payload),
            change_count=int(redacted_summary["change_count"]),
            affected_stage_indexes=tuple(redacted_summary["affected_stage_indexes"]),
            change_types=tuple(redacted_summary["change_types"]),
            redacted_summary=redacted_summary,
            validation_status="PASS",
            warning_codes=(),
            preview_persisted=False,
            preview_applied=False,
        )

    def to_amendment_dto(self, record: V1PlanAmendmentRecord) -> PlanAmendmentDto:
        return PlanAmendmentDto(
            amendment_id=record.amendment_id,
            job_id=record.job_id,
            source_kind=record.source_kind,
            title=record.title,
            summary=record.summary,
            payload_checksum=record.payload_checksum,
            redacted_summary=json.loads(record.redacted_summary_json),
            created_at=record.created_at,
            created_by=record.created_by,
        )

    def to_revision_dto(self, record: V1PlanRevisionRecord) -> PlanRevisionDto:
        return PlanRevisionDto(
            revision_id=record.revision_id,
            amendment_id=record.amendment_id,
            job_id=record.job_id,
            revision_order=record.revision_order,
            revision_state=record.revision_state,
            source_kind=record.source_kind,
            payload_checksum=record.payload_checksum,
            redacted_summary=json.loads(record.redacted_summary_json),
            created_at=record.created_at,
            created_by=record.created_by,
            decided_at=record.decided_at,
            decided_by=record.decided_by,
        )
