"""F5-T14: Backend projection for repair proposal review — safe, redacted, checksum-bound.

Exposes repair proposal data for Cockpit/API without letting UI supply diffs
or execution details. Full diff loaded by backend artifact ref endpoint only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from migration_factory.control_tower.application.redaction import redact_model_summary
from migration_factory.control_tower.application.safe_diff_preview import (
    SafeDiffFile,
    SafeDiffPreview,
    build_safe_diff_preview,
    safe_diff_preview_to_dict,
)
from migration_factory.control_tower.domain.checksums import sha256_canonical_json


FORBIDDEN_PROJECTION_KEYS: frozenset[str] = frozenset({
    "sandbox_path",
    "argv",
    "env",
    "raw_command",
    "endpoint",
    "deployment",
    "env_ref",
    "filesystem_target",
    "user_supplied_file_path",
})


@dataclass(frozen=True)
class RepairProposalProjection:
    proposal_id: str = ""
    gate_id: str = ""
    job_id: str = ""
    stage_index: int = 0
    command_id: str = ""
    failure_source: str = ""
    failure_summary: str = ""
    error_summary: str = ""
    root_cause: str = ""
    fix_strategy: str = ""
    changed_files: tuple[str, ...] = ()
    diff_preview: str = ""
    reviewed_diff_artifact_ref: str = ""
    reviewed_diff_artifact_checksum: str = ""
    risk: str = ""
    confidence: float = 0.0
    reviewer_decision: str = ""
    reviewer_notes: tuple[str, ...] = ()
    policy_status: str = ""
    policy_reason: str = ""
    policy_checksum: str = ""
    gate_checksum: str = ""
    allowed_actions: tuple[str, ...] = ()
    context_pack_checksum: str = ""
    base_repo_state_checksum: str = ""
    primary_output_checksum: str = ""
    reviewer_output_checksum: str = ""
    cycle_number: int = 0
    remaining_attempts: int = 3
    deterministic_artifact_checksum: str = ""
    model_status: dict[str, Any] = field(default_factory=dict)


def build_repair_projection_from_review_chain(
    *,
    proposal_id: str = "",
    gate_id: str = "",
    job_id: str = "",
    stage_index: int = 0,
    command_id: str = "",
    review_chain: dict[str, Any] | None = None,
    gate_checksum: str = "",
    allowed_actions: tuple[str, ...] = (),
    remaining_attempts: int = 3,
) -> RepairProposalProjection:
    chain = review_chain or {}
    return RepairProposalProjection(
        proposal_id=proposal_id,
        gate_id=gate_id,
        job_id=job_id,
        stage_index=stage_index,
        command_id=command_id,
        failure_source=str(chain.get("failure_source", "")),
        failure_summary=str(chain.get("failure_summary", "")),
        error_summary=str(chain.get("error_summary", chain.get("failure_summary", ""))),
        root_cause=str(chain.get("root_cause", "")),
        fix_strategy=str(chain.get("fix_strategy", "")),
        changed_files=tuple(chain.get("changed_files", ())),
        diff_preview=_safe_diff_preview(str(chain.get("diff_preview", ""))),
        reviewed_diff_artifact_ref=str(chain.get("final_artifact_ref", "")),
        reviewed_diff_artifact_checksum=str(chain.get("final_artifact_checksum", "")),
        risk=str(chain.get("risk", "")),
        confidence=float(chain.get("confidence", 0.0)),
        reviewer_decision=str(chain.get("reviewer_decision", "")),
        reviewer_notes=tuple(chain.get("reviewer_notes", ())),
        policy_status=str(chain.get("policy_status", chain.get("policy_validation_status", ""))),
        policy_reason=str(chain.get("policy_reason", "")),
        policy_checksum=str(chain.get("policy_validation_checksum", "")),
        gate_checksum=gate_checksum,
        allowed_actions=allowed_actions,
        context_pack_checksum=str(chain.get("context_pack_checksum", "")),
        base_repo_state_checksum=str(chain.get("base_repo_state_checksum", "")),
        primary_output_checksum=str(chain.get("primary_output_checksum", "")),
        reviewer_output_checksum=str(chain.get("reviewer_output_checksum", "")),
        cycle_number=int(chain.get("cycle_number", 0)),
        remaining_attempts=remaining_attempts,
        deterministic_artifact_checksum=str(chain.get("deterministic_artifact_checksum", "")),
        model_status=_safe_model_status(chain.get("model_roles")),
    )


def _safe_diff_preview(diff: str, max_lines: int = 20) -> str:
    lines = diff.strip().splitlines()
    return "\n".join(lines[:max_lines])


def validate_projection_safety(projection: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    projection_dict = _to_dict(projection) if not isinstance(projection, dict) else projection
    for forbidden in FORBIDDEN_PROJECTION_KEYS:
        if forbidden in projection_dict and projection_dict[forbidden]:
            failures.append(f"forbidden key {forbidden!r} found in repair projection")
    return failures


def projection_to_safe_dict(projection: RepairProposalProjection) -> dict[str, Any]:
    result: dict[str, Any] = {
        "proposal_id": projection.proposal_id,
        "gate_id": projection.gate_id,
        "job_id": projection.job_id,
        "stage_index": projection.stage_index,
        "command_id": projection.command_id,
        "failure_source": projection.failure_source,
        "failure_summary": projection.failure_summary,
        "error_summary": projection.error_summary,
        "root_cause": projection.root_cause,
        "fix_strategy": projection.fix_strategy,
        "changed_files": list(projection.changed_files),
        "diff_preview": projection.diff_preview,
        "reviewed_diff_artifact_ref": projection.reviewed_diff_artifact_ref,
        "reviewed_diff_artifact_checksum": projection.reviewed_diff_artifact_checksum,
        "risk": projection.risk,
        "confidence": projection.confidence,
        "reviewer_decision": projection.reviewer_decision,
        "reviewer_notes": list(projection.reviewer_notes),
        "policy_status": projection.policy_status,
        "policy_reason": projection.policy_reason,
        "policy_checksum": projection.policy_checksum,
        "gate_checksum": projection.gate_checksum,
        "allowed_actions": list(projection.allowed_actions),
        "context_pack_checksum": projection.context_pack_checksum,
        "base_repo_state_checksum": projection.base_repo_state_checksum,
        "primary_output_checksum": projection.primary_output_checksum,
        "reviewer_output_checksum": projection.reviewer_output_checksum,
        "cycle_number": projection.cycle_number,
        "remaining_attempts": projection.remaining_attempts,
        "deterministic_artifact_checksum": projection.deterministic_artifact_checksum,
        "model_status": _safe_model_status(projection.model_status),
    }
    # Redact any forbidden fields that may have crept in
    for forbidden in FORBIDDEN_PROJECTION_KEYS:
        result.pop(forbidden, None)
    return result


def _to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(obj)
    return {}


def _safe_model_status(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    for role in ("proposer", "reviewer", "assistant", "fallback"):
        raw = value.get(role)
        if not isinstance(raw, dict):
            continue
        safe[role] = {
            "available": bool(raw.get("available")),
            "status": str(raw.get("status") or ("available" if raw.get("available") else "blocked")),
            "fallback_used": bool(raw.get("fallback_used")),
            "primary_failure_reason": str(raw.get("primary_failure_reason") or ""),
            "fallback_failure_reason": str(raw.get("fallback_failure_reason") or ""),
            "timeout_occurred": bool(raw.get("timeout_occurred")),
            "primary_http_status": str(raw.get("primary_http_status") or ""),
            "fallback_http_status": str(raw.get("fallback_http_status") or ""),
            "schema_validation_error": str(raw.get("schema_validation_error") or ""),
        }
    return safe


READ_ONLY_REPAIR_ACTIONS: tuple[str, ...] = (
    "view_diff",
    "view_reviewer_opinion",
    "view_files_changed",
    "ask_explanation",
    "view_attempt_history",
)


@dataclass(frozen=True)
class RepairUnavailableState:
    attempted: bool
    status: str
    reason_code: str = ""
    detail: str = ""
    event_type: str = ""
    created_at: str = ""
    allowed_actions: tuple[str, ...] = ()


def repair_unavailable_state_to_dict(state: RepairUnavailableState) -> dict[str, Any]:
    result: dict[str, Any] = {
        "attempted": state.attempted,
        "status": state.status,
        "reason_code": state.reason_code,
        "detail": state.detail,
        "event_type": state.event_type,
        "created_at": state.created_at,
        "allowed_actions": list(state.allowed_actions),
    }
    if state.status == "generating":
        result["generation_status"] = "in_progress"
    return result


@dataclass(frozen=True)
class RepairGenerationProgress:
    attempted: bool = True
    status: str = "generating"
    reason_code: str = "REPAIR_GENERATION_IN_PROGRESS"
    attempt_number: int = 0
    remaining_attempts: int = 3
    detail: str = ""


def repair_generation_progress_to_dict(state: RepairGenerationProgress) -> dict[str, Any]:
    return {
        "attempted": state.attempted,
        "status": state.status,
        "reason_code": state.reason_code,
        "attempt_number": state.attempt_number,
        "remaining_attempts": state.remaining_attempts,
        "detail": state.detail,
    }


@dataclass(frozen=True)
class FilesChangedSummary:
    path: str
    change_type: str
    additions: int
    deletions: int


@dataclass(frozen=True)
class ReviewerVerdictProjection:
    reviewer_verdict_id: str | None = None
    decision: str = "unknown"
    reasoning: str | None = None
    missing_evidence: tuple[str, ...] = ()
    unsafe_assumptions: tuple[str, ...] = ()
    model_invocation_id: str | None = None
    output_checksum: str | None = None


@dataclass(frozen=True)
class ReviewedDiffProposal:
    proposal_id: str
    job_id: str | None = None
    command_id: str | None = None
    gate_id: str | None = None
    route_step_index: int | None = None
    stage_index: int | None = None
    status: str = ""
    attempt_number: int | None = None
    revision_number: int | None = None
    failure_summary: str = ""
    diagnosis_ref: str | None = None
    repair_plan_ref: str | None = None
    diff_ref: str | None = None
    diff_checksum: str = ""
    safe_diff_preview: SafeDiffPreview | None = None
    reviewer_verdict: ReviewerVerdictProjection | None = None
    files_changed: list[FilesChangedSummary] = field(default_factory=list)
    risk: str | None = None
    required_validation: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = READ_ONLY_REPAIR_ACTIONS
    redactions: tuple[str, ...] = ()
    apply_status: str | None = None
    rerun_status: str | None = None
    validation_proof_status: str | None = None
    final_diff_source: str | None = None
    generation_status: str | None = None
    generation_reason: str | None = None
    root_cause: str | None = None
    fix_strategy: str | None = None


def build_reviewed_diff_proposal_projection(
    *,
    proposal_id: str,
    status: str,
    failure_summary: str,
    review_chain: dict[str, Any] | None = None,
    reviewer_verdict: dict[str, Any] | None = None,
    job_id: str | None = None,
    command_id: str | None = None,
    gate_id: str | None = None,
    route_step_index: int | None = None,
    stage_index: int | None = None,
    attempt_number: int | None = None,
    revision_number: int | None = None,
    diagnosis_ref: str | None = None,
    repair_plan_ref: str | None = None,
    required_validation: tuple[str, ...] = (),
    allowed_actions: tuple[str, ...] = READ_ONLY_REPAIR_ACTIONS,
    risk: str | None = None,
    final_diff_text: str | None = None,
) -> ReviewedDiffProposal:
    chain = review_chain or {}
    diff_ref = _reviewed_diff_ref_from_chain(chain)
    if diff_ref is None:
        raise ValueError("reviewed diff ref is required for projection")

    safe_diff_preview = build_safe_diff_preview(
        proposal_id=proposal_id,
        diff_ref=diff_ref,
        diff_text=final_diff_text,
    )
    verdict = _build_reviewer_verdict_projection(
        reviewer_verdict=reviewer_verdict,
        review_chain=chain,
    )
    files_changed = [
        FilesChangedSummary(
            path=file.path,
            change_type=file.change_type,
            additions=file.additions,
            deletions=file.deletions,
        )
        for file in safe_diff_preview.files
    ]
    redactions = list(safe_diff_preview.redactions)
    if verdict.reasoning:
        redactions.append("reviewer reasoning redacted or bounded")
    return ReviewedDiffProposal(
        proposal_id=proposal_id,
        job_id=_maybe_str(job_id or chain.get("job_id")),
        command_id=_maybe_str(command_id or chain.get("command_id")),
        gate_id=_maybe_str(gate_id or chain.get("gate_id")),
        route_step_index=_maybe_int(route_step_index if route_step_index is not None else chain.get("route_step_index")),
        stage_index=_maybe_int(stage_index if stage_index is not None else chain.get("stage_index")),
        status=status,
        attempt_number=_maybe_int(attempt_number if attempt_number is not None else chain.get("attempt_number")),
        revision_number=_maybe_int(revision_number if revision_number is not None else chain.get("revision_number")),
        failure_summary=failure_summary,
        diagnosis_ref=_maybe_str(diagnosis_ref or chain.get("diagnosis_ref")),
        repair_plan_ref=_maybe_str(repair_plan_ref or chain.get("repair_plan_ref")),
        diff_ref=safe_diff_preview.diff_ref,
        diff_checksum=safe_diff_preview.diff_checksum,
        safe_diff_preview=safe_diff_preview,
        reviewer_verdict=verdict,
        files_changed=files_changed,
        risk=_maybe_str(risk or chain.get("risk")),
        required_validation=required_validation,
        allowed_actions=allowed_actions,
        redactions=tuple(dict.fromkeys(redactions)),
        final_diff_source=_maybe_str(chain.get("final_diff_source")),
        generation_status=_maybe_str(chain.get("generation_status")),
        generation_reason=_bounded_redacted_text(str(chain.get("generation_failure_reason") or "")) if chain.get("generation_failure_reason") else None,
        root_cause=_maybe_str(chain.get("root_cause")),
        fix_strategy=_maybe_str(chain.get("fix_strategy")),
    )


def reviewed_diff_proposal_to_safe_dict(proposal: ReviewedDiffProposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "job_id": proposal.job_id,
        "command_id": proposal.command_id,
        "gate_id": proposal.gate_id,
        "route_step_index": proposal.route_step_index,
        "stage_index": proposal.stage_index,
        "status": proposal.status,
        "attempt_number": proposal.attempt_number,
        "revision_number": proposal.revision_number,
        "failure_summary": proposal.failure_summary,
        "diagnosis_ref": proposal.diagnosis_ref,
        "repair_plan_ref": proposal.repair_plan_ref,
        # Raw authoritative artifact paths remain server-side. The dedicated
        # diff endpoint exposes only the safe preview.
        "diff_ref": None,
        "diff_checksum": proposal.diff_checksum,
        "safe_diff_preview": safe_diff_preview_to_dict(proposal.safe_diff_preview) if proposal.safe_diff_preview is not None else None,
        "reviewer_verdict": reviewer_verdict_projection_to_safe_dict(proposal.reviewer_verdict) if proposal.reviewer_verdict is not None else None,
        "files_changed": [files_changed_summary_to_dict(file) for file in proposal.files_changed],
        "risk": proposal.risk,
        "required_validation": list(proposal.required_validation),
        "allowed_actions": list(proposal.allowed_actions),
        "redactions": list(proposal.redactions),
        "apply_status": proposal.apply_status,
        "rerun_status": proposal.rerun_status,
        "validation_proof_status": proposal.validation_proof_status,
        "final_diff_source": proposal.final_diff_source,
        "generation_status": proposal.generation_status,
        "generation_reason": proposal.generation_reason,
        "root_cause": proposal.root_cause,
        "fix_strategy": proposal.fix_strategy,
    }


def reviewer_verdict_projection_to_safe_dict(verdict: ReviewerVerdictProjection) -> dict[str, Any]:
    return {
        "reviewer_verdict_id": verdict.reviewer_verdict_id,
        "decision": verdict.decision,
        "reasoning": verdict.reasoning,
        "missing_evidence": list(verdict.missing_evidence),
        "unsafe_assumptions": list(verdict.unsafe_assumptions),
        "model_invocation_id": verdict.model_invocation_id,
        "output_checksum": verdict.output_checksum,
    }


def files_changed_summary_to_dict(summary: FilesChangedSummary) -> dict[str, Any]:
    return {
        "path": summary.path,
        "change_type": summary.change_type,
        "additions": summary.additions,
        "deletions": summary.deletions,
    }


def _build_reviewer_verdict_projection(
    *,
    reviewer_verdict: dict[str, Any] | None,
    review_chain: dict[str, Any],
) -> ReviewerVerdictProjection:
    payload = reviewer_verdict or {}
    decision = str(
        payload.get("decision")
        or review_chain.get("reviewer_decision")
        or "unknown"
    ).strip().lower()
    if decision not in {"accept", "revise", "reject"}:
        decision = "unknown"

    reasoning_source = (
        payload.get("reasoning")
        or payload.get("notes")
        or review_chain.get("reviewer_reasoning")
        or review_chain.get("reviewer_notes")
        or ""
    )
    reasoning = _bounded_redacted_text(_stringify_reasoning(reasoning_source)) if reasoning_source else None
    missing_evidence = _normalize_text_list(
        payload.get("missing_evidence")
        or review_chain.get("missing_evidence")
        or (),
    )
    unsafe_assumptions = _normalize_text_list(
        payload.get("unsafe_assumptions")
        or review_chain.get("unsafe_assumptions")
        or (),
    )
    reviewer_verdict_id = _maybe_str(
        payload.get("reviewer_verdict_id")
        or review_chain.get("reviewer_verdict_id")
        or review_chain.get("reviewer_critique_id")
    )
    model_invocation_id = _maybe_str(
        payload.get("model_invocation_id")
        or review_chain.get("model_invocation_id")
    )
    output_checksum = _maybe_str(
        payload.get("output_checksum")
        or review_chain.get("reviewer_output_checksum")
        or review_chain.get("reviewer_verdict_checksum")
    )
    return ReviewerVerdictProjection(
        reviewer_verdict_id=reviewer_verdict_id,
        decision=decision,
        reasoning=reasoning,
        missing_evidence=tuple(missing_evidence),
        unsafe_assumptions=tuple(unsafe_assumptions),
        model_invocation_id=model_invocation_id,
        output_checksum=output_checksum,
    )


def _reviewed_diff_ref_from_chain(review_chain: dict[str, Any]) -> str | None:
    candidates = (
        review_chain.get("final_diff_ref"),
        review_chain.get("final_reviewed_diff_ref"),
    )
    artifact_refs = review_chain.get("artifact_refs")
    if isinstance(artifact_refs, dict):
        candidates = candidates + (
            artifact_refs.get("final_reviewed_diff"),
            artifact_refs.get("final_diff"),
        )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def _normalize_text_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (_bounded_redacted_text(value),)
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            item = str(item)
        result.append(_bounded_redacted_text(item))
    return tuple(result)


def _bounded_redacted_text(value: str, *, limit: int = 1000) -> str:
    cleaned = redact_model_summary(value)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - len("...[truncated]")] + "...[truncated]"


def build_reviewed_diff_proposal_from_record(
    *,
    proposal_id: str,
    status: str,
    failure_summary: str,
    job_id: str | None = None,
    command_id: str | None = None,
    gate_id: str | None = None,
    route_step_index: int | None = None,
    stage_index: int | None = None,
    attempt_number: int | None = None,
    revision_number: int | None = None,
    diagnosis_ref: str | None = None,
    repair_plan_ref: str | None = None,
    diff_ref: str | None = None,
    diff_checksum: str | None = None,
    reviewer_verdict_id: str | None = None,
    reviewer_output_checksum: str | None = None,
    policy_validation_checksum: str | None = None,
    status_reason: str | None = None,
    required_validation: tuple[str, ...] = (),
    allowed_actions: tuple[str, ...] = READ_ONLY_REPAIR_ACTIONS,
    risk: str | None = None,
    final_diff_text: str | None = None,
    reviewer_decision: str | None = None,
    reviewer_reasoning: str | None = None,
    apply_status: str | None = None,
    rerun_status: str | None = None,
    validation_proof_status: str | None = None,
    final_diff_source: str | None = None,
    generation_status: str | None = None,
    generation_reason: str | None = None,
) -> ReviewedDiffProposal:
    """Build a ReviewedDiffProposal from persisted V2RepairProposalRecord fields.

    This is the durable-persistence path (PR-B). Unlike
    build_reviewed_diff_proposal_projection which builds from a
    review_chain dict, this function reads from persisted column values.
    """
    if status == "generating":
        generation_status = "in_progress"
    if diff_ref is None:
        raise ValueError("reviewed diff ref is required for projection")

    safe_diff_preview = build_safe_diff_preview(
        proposal_id=proposal_id,
        diff_ref=diff_ref,
        diff_text=final_diff_text,
    )
    persisted_reviewer_reasoning = reviewer_reasoning
    artifact_path = Path(diff_ref).with_name("final_reviewed_repair_artifact.json")
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        reviewer_notes = artifact.get("reviewer_notes") if isinstance(artifact, dict) else None
        if isinstance(reviewer_notes, list) and reviewer_notes:
            persisted_reviewer_reasoning = "\n".join(str(note) for note in reviewer_notes if str(note).strip())
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    verdict = ReviewerVerdictProjection(
        reviewer_verdict_id=reviewer_verdict_id,
        decision=reviewer_decision or "unknown",
        reasoning=(
            _bounded_redacted_text(persisted_reviewer_reasoning)
            if persisted_reviewer_reasoning
            else None
        ),
        output_checksum=reviewer_output_checksum,
    )
    files_changed = [
        FilesChangedSummary(
            path=file.path,
            change_type=file.change_type,
            additions=file.additions,
            deletions=file.deletions,
        )
        for file in safe_diff_preview.files
    ]
    redactions = list(safe_diff_preview.redactions)

    return ReviewedDiffProposal(
        proposal_id=proposal_id,
        job_id=_maybe_str(job_id),
        command_id=_maybe_str(command_id),
        gate_id=_maybe_str(gate_id),
        route_step_index=_maybe_int(route_step_index),
        stage_index=_maybe_int(stage_index),
        status=status,
        attempt_number=_maybe_int(attempt_number),
        revision_number=_maybe_int(revision_number),
        failure_summary=failure_summary,
        diagnosis_ref=_maybe_str(diagnosis_ref),
        repair_plan_ref=_maybe_str(repair_plan_ref),
        diff_ref=safe_diff_preview.diff_ref,
        diff_checksum=safe_diff_preview.diff_checksum,
        safe_diff_preview=safe_diff_preview,
        reviewer_verdict=verdict,
        files_changed=files_changed,
        risk=_maybe_str(risk),
        required_validation=required_validation,
        allowed_actions=allowed_actions,
        redactions=tuple(dict.fromkeys(redactions)),
        apply_status=_maybe_str(apply_status),
        rerun_status=_maybe_str(rerun_status),
        validation_proof_status=_maybe_str(validation_proof_status),
        final_diff_source=_maybe_str(final_diff_source),
        generation_status=_maybe_str(generation_status),
        generation_reason=_bounded_redacted_text(generation_reason) if generation_reason else None,
    )


def record_to_attempt_summary(record: Any) -> dict[str, Any]:
    """Convert a V2RepairProposalRecord to a safe attempt summary dict.

    PR-F: Extended with apply/rerun/rollback/validation/attempt fields.
    No raw patch, path, env, argv, command, or secrets exposed.
    """
    return {
        "proposal_id": record.proposal_id,
        "command_id": record.command_id if hasattr(record, "command_id") else None,
        "job_id": getattr(record, "job_id", None),
        "gate_id": getattr(record, "gate_id", None),
        "attempt_number": getattr(record, "attempt_number", None),
        "revision_number": getattr(record, "revision_number", None),
        "status": record.status,
        "apply_status": getattr(record, "apply_status", None),
        "rerun_status": getattr(record, "rerun_status", None),
        "rollback_status": getattr(record, "rollback_status", None),
        "reviewer_decision": getattr(record, "reviewer_decision", None),
        "diff_checksum": getattr(record, "diff_checksum", None),
        "policy_validation_checksum": getattr(record, "policy_validation_checksum", None),
        "validation_proof_status": getattr(record, "validation_proof_status", None),
        "final_diff_source": getattr(record, "final_diff_source", None),
        "validation_result_ref": getattr(record, "validation_result_ref", None),
        "next_gate_id": getattr(record, "next_gate_id", None),
        "next_gate_status": getattr(record, "next_gate_status", None),
        "remaining_attempts": getattr(record, "remaining_attempts", None),
        "status_reason": getattr(record, "status_reason", None),
        "created_at": record.created_at,
        "completed_at": getattr(record, "completed_at", None),
    }


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stringify_reasoning(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return str(value)
