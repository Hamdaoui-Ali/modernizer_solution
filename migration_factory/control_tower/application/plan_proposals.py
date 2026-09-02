"""V1-12B fake-provider advisory plan proposal validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

from migration_factory.control_tower.application.dto import AdvisoryValidationReportDto
from migration_factory.control_tower.application.plan_amendments import (
    PlanChange,
    _payload_dict,
    _redacted_summary,
)
from migration_factory.control_tower.domain.checksums import (
    canonical_json_text,
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.domain.entities import V1PlanRevisionRecord
from migration_factory.control_tower.domain.errors import (
    NotFoundError,
    PlanAdvisoryValidationError,
    PlanRevisionConflictError,
)
from migration_factory.control_tower.application.redaction import (
    redact_absolute_paths,
    redact_deployment_identifiers,
    redact_env_assignments,
    redact_raw_prompts,
    redact_sensitive_env_vars,
    redact_public_value,
)


_ALLOWED_PROPOSAL_FIELDS = frozenset(
    {
        "title",
        "summary",
        "notes",
        "changes",
        "confidence_label",
        "confidence_score",
    }
)
_FORBIDDEN_AUTHORITY_FIELDS = frozenset(
    {
        "route",
        "pipeline_id",
        "pipeline_version",
        "stage_route",
        "graph_route",
        "ledger_id",
        "worker_command",
        "command_id",
        "command",
        "arguments",
        "argv",
        "shell_command",
        "working_directory",
        "working_dir",
        "maven_goal",
        "maven_goals",
        "approval_id",
        "approval_decision",
        "approval_checksum",
        "artifact_id",
        "artifact_path",
        "artifact_metadata",
        "run_configuration",
        "target_proof",
        "target_proof_level",
        "achieved_proof",
        "achieved_proof_level",
        "source_path",
        "sandbox_path",
        "deployment_id",
        "provider_response",
        "raw_model_output",
        "prompt",
        "stack_trace",
    }
)
_ALLOWED_CONFIDENCE_LABELS = frozenset({"low", "medium", "high"})


@dataclass(frozen=True, slots=True)
class _ValidatedProposal:
    validation_status: str
    warning_codes: tuple[str, ...]
    rejection_codes: tuple[str, ...]
    title: str | None
    summary: str | None
    notes: tuple[str, ...]
    changes: tuple[PlanChange, ...]
    confidence_label: str | None
    confidence_score: float | None
    payload_json: str | None
    payload_checksum: str | None
    redacted_summary: dict[str, Any]


class FakeProviderPlanProposalService:
    """Validate and persist non-authoritative fake-provider plan proposals."""

    def __init__(self, unit_of_work_factory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def validate_output(
        self,
        raw_output: object,
        *,
        model_invocation_id: str | None = None,
        context_pack_manifest_id: str | None = None,
    ) -> AdvisoryValidationReportDto:
        validated = self._validate_output(
            raw_output,
            model_invocation_id=model_invocation_id,
            context_pack_manifest_id=context_pack_manifest_id,
        )
        return AdvisoryValidationReportDto(
            amendment_id="",
            job_id="",
            validation_status=validated.validation_status,
            source_kind="fake_provider",
            revision_persisted=False,
            non_authoritative=True,
            warning_codes=validated.warning_codes,
            rejection_codes=validated.rejection_codes,
            confidence_label=validated.confidence_label,
            confidence_score=validated.confidence_score,
            payload_checksum=validated.payload_checksum,
            model_invocation_id=model_invocation_id,
            context_pack_manifest_id=context_pack_manifest_id,
            redacted_summary=validated.redacted_summary,
        )

    def create_revision_from_fake_provider(
        self,
        *,
        amendment_id: str,
        raw_output: object,
        created_by: str,
        model_invocation_id: str | None = None,
        context_pack_manifest_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AdvisoryValidationReportDto:
        validated = self._validate_output(
            raw_output,
            model_invocation_id=model_invocation_id,
            context_pack_manifest_id=context_pack_manifest_id,
        )
        now = utc_now_text()

        with self._unit_of_work_factory() as uow:
            amendment = uow.v1_plan_amendments.get(amendment_id)
            if amendment is None:
                raise NotFoundError("plan amendment", amendment_id)
            if model_invocation_id is not None and uow.v1_model_invocations.get(model_invocation_id) is None:
                raise NotFoundError("model invocation", model_invocation_id)
            if context_pack_manifest_id is not None and uow.v1_context_pack_manifests.get(context_pack_manifest_id) is None:
                raise NotFoundError("context pack manifest", context_pack_manifest_id)

            if validated.validation_status != "PASS":
                self._append_validation_audit(
                    uow=uow,
                    action="fake_provider_plan_proposal_rejected",
                    job_id=amendment.job_id,
                    actor_id=created_by,
                    created_at=now,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    payload={
                        "amendment_id": amendment_id,
                        "validation_status": validated.validation_status,
                        "warning_codes": list(validated.warning_codes),
                        "rejection_codes": list(validated.rejection_codes),
                        "model_invocation_id": model_invocation_id,
                        "context_pack_manifest_id": context_pack_manifest_id,
                    },
                )
                return AdvisoryValidationReportDto(
                    amendment_id=amendment_id,
                    job_id=amendment.job_id,
                    validation_status=validated.validation_status,
                    source_kind="fake_provider",
                    revision_persisted=False,
                    non_authoritative=True,
                    warning_codes=validated.warning_codes,
                    rejection_codes=validated.rejection_codes,
                    confidence_label=validated.confidence_label,
                    confidence_score=validated.confidence_score,
                    payload_checksum=validated.payload_checksum,
                    model_invocation_id=model_invocation_id,
                    context_pack_manifest_id=context_pack_manifest_id,
                    redacted_summary=validated.redacted_summary,
                )

            if uow.v1_plan_revisions.has_terminal_revision(amendment_id):
                raise PlanRevisionConflictError(
                    f"Plan amendment {amendment_id!r} already has a terminal accepted/finalized revision"
                )

            revision_order = uow.v1_plan_revisions.next_revision_order(amendment_id)
            persisted_summary = dict(validated.redacted_summary)
            persisted_summary["validation_status"] = "PASS"
            persisted_summary["warning_codes"] = list(validated.warning_codes)
            persisted_summary["rejection_codes"] = []
            persisted_summary["confidence_label"] = validated.confidence_label
            persisted_summary["confidence_score"] = validated.confidence_score
            persisted_summary["model_invocation_id"] = model_invocation_id
            persisted_summary["context_pack_manifest_id"] = context_pack_manifest_id

            record = V1PlanRevisionRecord(
                revision_id=f"rev-{uuid4().hex}",
                amendment_id=amendment_id,
                job_id=amendment.job_id,
                revision_order=revision_order,
                revision_state="draft",
                source_kind="fake_provider",
                payload_json=validated.payload_json or canonical_json_text({}),
                payload_checksum=validated.payload_checksum or sha256_canonical_json({}),
                redacted_summary_json=canonical_json_text(persisted_summary),
                created_at=now,
                created_by=created_by,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            uow.v1_plan_revisions.insert(record)
            self._append_validation_audit(
                uow=uow,
                action="fake_provider_plan_proposal_persisted",
                job_id=amendment.job_id,
                actor_id=created_by,
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
                payload={
                    "amendment_id": amendment_id,
                    "revision_id": record.revision_id,
                    "revision_order": revision_order,
                    "payload_checksum": record.payload_checksum,
                    "validation_status": "PASS",
                    "warning_codes": list(validated.warning_codes),
                    "model_invocation_id": model_invocation_id,
                    "context_pack_manifest_id": context_pack_manifest_id,
                },
            )
        return self.get_validation_report(record.revision_id)

    def get_validation_report(self, revision_id: str) -> AdvisoryValidationReportDto:
        with self._unit_of_work_factory() as uow:
            revision = uow.v1_plan_revisions.get(revision_id)
        if revision is None:
            raise NotFoundError("plan revision", revision_id)
        if revision.source_kind != "fake_provider":
            raise PlanAdvisoryValidationError(
                f"Plan revision {revision_id!r} is not a fake-provider advisory proposal"
            )
        summary = json.loads(revision.redacted_summary_json)
        return AdvisoryValidationReportDto(
            amendment_id=revision.amendment_id,
            job_id=revision.job_id,
            validation_status=str(summary.get("validation_status", "PASS")),
            source_kind=revision.source_kind,
            revision_persisted=True,
            non_authoritative=bool(summary.get("non_authoritative", True)),
            warning_codes=tuple(str(code) for code in summary.get("warning_codes", [])),
            rejection_codes=tuple(str(code) for code in summary.get("rejection_codes", [])),
            confidence_label=_optional_string(summary.get("confidence_label")),
            confidence_score=_optional_float(summary.get("confidence_score")),
            payload_checksum=revision.payload_checksum,
            model_invocation_id=_optional_string(summary.get("model_invocation_id")),
            context_pack_manifest_id=_optional_string(summary.get("context_pack_manifest_id")),
            revision_id=revision.revision_id,
            revision_order=revision.revision_order,
            revision_state=revision.revision_state,
            redacted_summary=redact_public_value(summary),
        )

    def _validate_output(
        self,
        raw_output: object,
        *,
        model_invocation_id: str | None,
        context_pack_manifest_id: str | None,
    ) -> _ValidatedProposal:
        warning_codes: list[str] = []
        if model_invocation_id is None:
            warning_codes.append("MISSING_MODEL_INVOCATION_REF")
        if context_pack_manifest_id is None:
            warning_codes.append("MISSING_CONTEXT_PACK_REF")

        if not isinstance(raw_output, Mapping):
            return _failed_report(
                warning_codes=warning_codes,
                rejection_codes=("MALFORMED_PAYLOAD",),
            )

        forbidden_keys = sorted(_find_forbidden_keys(raw_output))
        if forbidden_keys:
            return _failed_report(
                warning_codes=warning_codes,
                rejection_codes=tuple(f"FORBIDDEN_FIELD:{key}" for key in forbidden_keys),
            )

        unknown_fields = sorted(set(str(key) for key in raw_output.keys()) - _ALLOWED_PROPOSAL_FIELDS)
        if unknown_fields:
            return _failed_report(
                warning_codes=warning_codes,
                rejection_codes=tuple(f"UNSUPPORTED_FIELD:{field}" for field in unknown_fields),
            )

        try:
            title = _require_string(raw_output.get("title"), field_name="title")
            summary = _require_string(raw_output.get("summary"), field_name="summary")
            notes = _normalize_notes(raw_output.get("notes", ()))
            changes = _normalize_raw_changes(raw_output.get("changes"))
            confidence_label = _normalize_confidence_label(raw_output.get("confidence_label"))
            confidence_score = _normalize_confidence_score(raw_output.get("confidence_score"))
        except ValueError as exc:
            return _failed_report(
                warning_codes=warning_codes,
                rejection_codes=(str(exc),),
            )

        unsafe_text_codes = _unsafe_text_reasons(title, summary, notes, changes)
        if unsafe_text_codes:
            return _failed_report(
                warning_codes=warning_codes,
                rejection_codes=tuple(sorted(unsafe_text_codes)),
            )

        payload = _payload_dict(title=title, summary=summary, notes=notes, changes=changes)
        payload_json = canonical_json_text(payload)
        payload_checksum = sha256_canonical_json(payload)
        redacted_summary = _redacted_summary(
            source_kind="fake_provider",
            title=title,
            summary=summary,
            changes=changes,
        )
        return _ValidatedProposal(
            validation_status="PASS",
            warning_codes=tuple(sorted(warning_codes)),
            rejection_codes=(),
            title=title,
            summary=summary,
            notes=notes,
            changes=changes,
            confidence_label=confidence_label,
            confidence_score=confidence_score,
            payload_json=payload_json,
            payload_checksum=payload_checksum,
            redacted_summary=redact_public_value(redacted_summary),
        )

    def _append_validation_audit(
        self,
        *,
        uow,
        action: str,
        job_id: str,
        actor_id: str,
        created_at: str,
        correlation_id: str | None,
        causation_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        uow.audit_records.append_global_audit(
            audit_id=uuid4().hex,
            actor_type="system",
            actor_id=actor_id,
            action=action,
            payload_json=canonical_json_text(redact_public_value(payload)),
            created_at=created_at,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )


def _failed_report(
    *,
    warning_codes: list[str],
    rejection_codes: tuple[str, ...],
) -> _ValidatedProposal:
    return _ValidatedProposal(
        validation_status="FAILED",
        warning_codes=tuple(sorted(warning_codes)),
        rejection_codes=rejection_codes,
        title=None,
        summary=None,
        notes=(),
        changes=(),
        confidence_label=None,
        confidence_score=None,
        payload_json=None,
        payload_checksum=None,
        redacted_summary={
            "non_authoritative": True,
            "validation_status": "FAILED",
            "warning_codes": list(sorted(warning_codes)),
            "rejection_codes": list(rejection_codes),
        },
    )


def _find_forbidden_keys(value: object, *, prefix: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            dotted = f"{prefix}.{key_text}" if prefix else key_text
            lowered = key_text.lower()
            if lowered in _FORBIDDEN_AUTHORITY_FIELDS:
                found.add(dotted)
            found.update(_find_forbidden_keys(nested, prefix=dotted))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found.update(_find_forbidden_keys(nested, prefix=f"{prefix}[{index}]"))
    return found


def _require_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"MALFORMED_{field_name.upper()}")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"MALFORMED_{field_name.upper()}")
    return cleaned


def _normalize_notes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("MALFORMED_NOTES")
    normalized: list[str] = []
    for item in value:
        normalized.append(_require_string(item, field_name="note"))
    return tuple(normalized)


def _normalize_raw_changes(value: object) -> tuple[PlanChange, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("MALFORMED_CHANGES")
    normalized: list[PlanChange] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("MALFORMED_CHANGES")
        stage_index = item.get("stage_index")
        if not isinstance(stage_index, int):
            raise ValueError("MALFORMED_CHANGES")
        normalized.append(
            PlanChange(
                stage_index=stage_index,
                change_type=_require_string(item.get("change_type"), field_name="change_type"),
                description=_require_string(item.get("description"), field_name="description"),
                rationale=None
                if item.get("rationale") is None
                else _require_string(item.get("rationale"), field_name="rationale"),
            )
        )
    # Reuse V1-12A canonical validation.
    _payload_dict(title="placeholder", summary="placeholder", notes=(), changes=tuple(normalized))
    return tuple(normalized)


def _normalize_confidence_label(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("MALFORMED_CONFIDENCE_LABEL")
    normalized = value.strip().lower()
    if normalized not in _ALLOWED_CONFIDENCE_LABELS:
        raise ValueError("MALFORMED_CONFIDENCE_LABEL")
    return normalized


def _normalize_confidence_score(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError("MALFORMED_CONFIDENCE_SCORE")
    score = float(value)
    if score < 0.0 or score > 1.0:
        raise ValueError("MALFORMED_CONFIDENCE_SCORE")
    return round(score, 4)


def _unsafe_text_reasons(
    title: str,
    summary: str,
    notes: tuple[str, ...],
    changes: tuple[PlanChange, ...],
) -> set[str]:
    reasons: set[str] = set()
    for text in (
        title,
        summary,
        *notes,
        *(change.description for change in changes),
        *(change.rationale for change in changes if change.rationale is not None),
    ):
        if redact_absolute_paths(text) != text:
            reasons.add("UNSAFE_ABSOLUTE_PATH_CONTENT")
        if redact_env_assignments(text) != text or redact_sensitive_env_vars(text) != text:
            reasons.add("UNSAFE_ENV_CONTENT")
        if redact_deployment_identifiers(text) != text:
            reasons.add("UNSAFE_DEPLOYMENT_CONTENT")
        if redact_raw_prompts(text) != text:
            reasons.add("UNSAFE_PROMPT_CONTENT")
    return reasons


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, (float, int)):
        return float(value)
    return None
