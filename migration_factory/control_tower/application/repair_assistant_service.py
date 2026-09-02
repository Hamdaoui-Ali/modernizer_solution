"""Dedicated Repair Assistant — proposal-scoped chat and revision intent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from migration_factory.control_tower.application.redaction import (
    redact_model_summary,
    redact_patch_preview,
)
from migration_factory.control_tower.application.v2_assistant_model_client import (
    V2AssistantModelClient,
    V2AssistantModelResult,
)
from migration_factory.control_tower.application.v2_model_role_router import (
    V2ModelRole,
)
from migration_factory.control_tower.application.v2_llm_invocation_ledger import (
    V2LLMInvocationLedger,
    compute_content_checksum,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.repair_assistant_repository import (
    ClaimOutcome,
    LeaseState,
    RepairAssistantMessageRecord,
    SqliteRepairAssistantRepository,
    _REPAIR_ASSISTANT_LEASE_SECONDS,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
    V2RepairProposalRecord,
)


# ── Domain dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class RepairAssistantMessage:
    message_id: str
    job_id: str
    proposal_id: str
    attempt_number: int | None
    role: str
    message_text: str
    action: str | None
    revision_intent_json: str | None
    base_diff_checksum: str
    generated_proposal_id: str | None
    status: str
    created_at: str
    idempotency_key: str | None
    failure_stage: str | None = None
    failure_code: str | None = None
    safe_failure_message: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class RepairAssistantContext:
    job_id: str
    proposal_id: str
    proposal_status: str
    attempt_number: int | None
    base_diff_checksum: str
    diff_content: str | None
    failure_summary: str | None
    reviewer_decision: str | None
    reviewer_notes: list[str]
    prior_attempts: list[dict]
    prior_revision_instructions: list[str]
    previous_validation_result: str | None
    available_versions: list[str]
    pom_intelligence: dict | None
    user_comments: str
    prior_reviewer_notes: list[str]


@dataclass(frozen=True)
class RepairAssistantIntent:
    action: str  # ANSWER_ONLY | REQUEST_REVISION | CLARIFICATION_REQUIRED
    assistant_message: str
    user_instruction: str
    resolved_instruction: str
    tool: str | None
    constraints: list[str]
    target_files: list[str]
    requires_clarification: bool

    @property
    def revision_instruction(self) -> str:
        return self.user_instruction


@dataclass(frozen=True)
class RepairAssistantResult:
    message_id: str
    assistant_message: str
    action: str
    revision_intent: RepairAssistantIntent | None
    revision_started: bool
    new_proposal_id: str | None
    new_attempt_number: int | None
    status: str
    failure_stage: str | None = None
    failure_code: str | None = None
    correlation_id: str | None = None


# ── Failure diagnostics constants ────────────────────────────────────

CONTEXT_RESOLUTION_FAILED = "CONTEXT_RESOLUTION_FAILED"
PROPOSER_OUTPUT_INVALID = "PROPOSER_OUTPUT_INVALID"
REVIEWER_UNAVAILABLE = "REVIEWER_UNAVAILABLE"
PROPOSAL_PERSIST_FAILED = "PROPOSAL_PERSIST_FAILED"
LEASE_STATE_UNAVAILABLE = "LEASE_STATE_UNAVAILABLE"

FAILURE_STAGE_CONTEXT_RESOLUTION = "context_resolution"
FAILURE_STAGE_PROPOSER = "proposer_generation"
FAILURE_STAGE_REVIEWER = "reviewer_evaluation"
FAILURE_STAGE_PROPOSAL_PERSIST = "proposal_persist"
FAILURE_STAGE_LEASE = "lease_state"

FAILURE_CODE_MAP = {
    CONTEXT_RESOLUTION_FAILED: "CONTEXT_RESOLUTION_FAILED",
    PROPOSER_OUTPUT_INVALID: "PROPOSER_OUTPUT_INVALID",
    REVIEWER_UNAVAILABLE: "REVIEWER_UNAVAILABLE",
    PROPOSAL_PERSIST_FAILED: "PROPOSAL_PERSIST_FAILED",
    LEASE_STATE_UNAVAILABLE: "LEASE_STATE_UNAVAILABLE",
}

# ── Phase helpers for transaction-boundary fix ───────────────────────


_PRE_PROCESSING_EXPIRY_SECONDS = 300

_STALE_REASON = "STALE_BASE_PROPOSAL"


class _StaleProposalError(ValueError):
    pass


def _snapshot_from_record(record: V2RepairProposalRecord) -> dict:
    return {
        "proposal_id": record.proposal_id,
        "command_id": record.command_id,
        "job_id": record.job_id,
        "status": record.status,
        "attempt_number": record.attempt_number,
        "diff_checksum": record.diff_checksum,
        "failure_evidence_ref": record.failure_evidence_ref,
        "repair_context_ref": record.repair_context_ref,
        "diff_ref": record.diff_ref,
        "reviewer_decision": record.reviewer_decision,
        "reviewer_verdict_ref": record.reviewer_verdict_ref,
        "failure_summary": record.failure_summary,
        "validation_result_ref": record.validation_result_ref,
        "gate_id": record.gate_id,
        "route_step_index": record.route_step_index,
        "source_proposal_id": record.source_proposal_id,
        "revision_of": record.revision_of,
        "revision_number": record.revision_number,
        "context_pack_checksum": record.context_pack_checksum,
        "source_profile": record.source_profile,
        "target_profile": record.target_profile,
        "validation_context_ref": record.validation_context_ref,
        "validation_context_checksum": record.validation_context_checksum,
    }


def _record_from_snapshot(
    snapshot: dict,
    *,
    message_id: str,
    role: str,
    message_text: str,
    action: str | None,
    revision_intent_json: str | None,
    base_diff_checksum: str,
    generated_proposal_id: str | None,
    status: str,
    created_at: str,
    idempotency_key: str | None,
) -> RepairAssistantMessageRecord:
    return RepairAssistantMessageRecord(
        message_id=message_id,
        job_id=str(snapshot["job_id"]),
        proposal_id=str(snapshot["proposal_id"]),
        attempt_number=snapshot.get("attempt_number"),
        role=role,
        message_text=message_text,
        action=action,
        revision_intent_json=revision_intent_json,
        base_diff_checksum=base_diff_checksum,
        generated_proposal_id=generated_proposal_id,
        status=status,
        created_at=created_at,
        idempotency_key=idempotency_key,
    )


# ── Service ───────────────────────────────────────────────────────────


_REPAIR_ASSISTANT_SYSTEM_PROMPT = """\
You are the AMF-252 Repair Assistant. Your role is to help operators
understand and resolve repair proposal failures by analyzing context
and producing structured responses.

You have three possible actions:

1. ANSWER_ONLY — The user's question can be answered directly from the
   context. Provide a clear, concise answer without requesting any code
   changes. Set action="ANSWER_ONLY", assistant_message to your answer,
   and revision_instruction to empty.

2. REQUEST_REVISION — The user has identified a problem with the
   current repair proposal that requires a code/diff change. You must:
   - Set action="REQUEST_REVISION"
   - Set tool="request_repair_revision"
   - Preserve the exact user request in arguments.user_instruction
   - Use resolved_instruction, constraints and target_files only as
     optional contextual hints
   - Set requires_clarification=False

3. CLARIFICATION_REQUIRED — The user's request is ambiguous or
   insufficient context is available. Set action="CLARIFICATION_REQUIRED",
   describe what additional information is needed, and set
   requires_clarification=True. Never guess or hallucinate context.

CRITICAL RULES:
- Never execute commands or modify files yourself.
- Never fabricate evidence, checksums, or validation results.
- Always base your analysis on the provided context only.
- For REQUEST_REVISION, be specific about what code should change.
- For ANSWER_ONLY, do not suggest code changes.

Respond ONLY with valid JSON matching this schema:
{
  "action": "ANSWER_ONLY|REQUEST_REVISION|CLARIFICATION_REQUIRED",
  "assistant_message": "...",
  "tool": null,
  "arguments": {"user_instruction":"", "resolved_instruction":"", "constraints":[], "target_files":[]},
  "requires_clarification": false
}"""


class RepairAssistantService:
    def __init__(
        self,
        repair_assistant_repo: SqliteRepairAssistantRepository | None = None,
        repair_repo: SqliteV2RepairRepository | None = None,
        model_client: V2AssistantModelClient | None = None,
        uow_factory: Callable[[], Any] | None = None,
        invocation_ledger: V2LLMInvocationLedger | None = None,
    ) -> None:
        self._repo = repair_assistant_repo
        self._repair_repo = repair_repo
        self._model_client = model_client or V2AssistantModelClient()
        self._uow_factory = uow_factory
        self._invocation_ledger = invocation_ledger

    def _ledger_start(self, *, job_id: str, proposal_id: str, prompt: str) -> str | None:
        if self._invocation_ledger is None:
            return None
        try:
            return self._invocation_ledger.start_invocation(
                job_id=job_id,
                proposal_id=proposal_id,
                role="assistant",
                responsibility="assistant_answer",
                transport="azure_openai",
                input_checksum=compute_content_checksum(prompt),
            )
        except Exception:
            import logging
            logging.getLogger("repair_assistant").exception(
                "Repair Assistant invocation ledger start failed; decision unchanged",
                extra={"job_id": job_id, "proposal_id": proposal_id},
            )
            return None

    def _ledger_finish(self, invocation_id: str | None, result: V2AssistantModelResult) -> None:
        if invocation_id is None or self._invocation_ledger is None:
            return
        try:
            common = {
                "transport": "azure_openai",
                "http_status": result.primary_http_status or result.fallback_http_status or None,
                "azure_request_id": getattr(result, "azure_request_id", "") or None,
                "retry_count": getattr(result, "retry_count", 0),
                "retry_after": getattr(result, "retry_after", "") or None,
                "response_format": result.response_format_used or None,
                "parse_result": "accepted" if result.success else "rejected",
            }
            if result.success:
                self._invocation_ledger.complete_invocation(
                    invocation_id, output=result.content, redacted_summary=result.redacted_summary, **common,
                )
            else:
                self._invocation_ledger.fail_invocation(
                    invocation_id, redacted_error=result.failure_reason,
                    redacted_summary=result.redacted_summary, **common,
                )
        except Exception:
            import logging
            logging.getLogger("repair_assistant").exception(
                "Repair Assistant invocation ledger completion failed; decision unchanged",
                extra={"invocation_id": invocation_id},
            )

    # ── Public API ────────────────────────────────────────────────────

    def get_messages(
        self,
        *,
        job_id: str,
        proposal_id: str,
    ) -> list[RepairAssistantMessage]:
        if self._repo is None:
            return []
        records = self._repo.list_messages(job_id, proposal_id)
        return [self._record_to_message(r) for r in records]

    def build_repair_assistant_context(
        self,
        *,
        job_id: str,
        proposal_id: str,
    ) -> RepairAssistantContext:
        proposal = self._get_proposal_or_raise(job_id, proposal_id)
        diff_content = self._read_ref(proposal.diff_ref) if proposal.diff_ref else None
        failure_evidence = self._read_ref(proposal.failure_evidence_ref) if proposal.failure_evidence_ref else None
        repair_context = self._read_ref(proposal.repair_context_ref) if proposal.repair_context_ref else None
        reviewer_decision = proposal.reviewer_decision
        reviewer_notes = self._load_reviewer_notes(proposal)
        prior_attempts = self._load_prior_attempts(job_id, proposal)
        prior_revision_instructions = self._load_prior_revision_instructions(job_id, proposal_id)
        previous_validation = self._read_ref(proposal.validation_result_ref) if proposal.validation_result_ref else None
        latest_apply_validation = (
            previous_validation
            if str(proposal.apply_status or "").upper() == "APPLIED"
            else None
        )
        available_versions = self._load_available_versions(job_id)
        pom_intelligence = self._load_pom_intelligence(job_id)
        prior_reviewer_notes = self._load_prior_reviewer_notes(job_id, proposal_id)

        return RepairAssistantContext(
            job_id=job_id,
            proposal_id=proposal_id,
            proposal_status=proposal.status,
            attempt_number=proposal.attempt_number,
            base_diff_checksum=proposal.diff_checksum or "",
            diff_content=diff_content,
            failure_summary=failure_evidence or proposal.failure_summary,
            reviewer_decision=reviewer_decision,
            reviewer_notes=reviewer_notes,
            prior_attempts=prior_attempts,
            prior_revision_instructions=prior_revision_instructions,
            previous_validation_result=latest_apply_validation,
            available_versions=available_versions,
            pom_intelligence=pom_intelligence,
            user_comments="",
            prior_reviewer_notes=prior_reviewer_notes,
        )

    def verify_proposal_for_message(
        self,
        *,
        job_id: str,
        proposal_id: str,
        base_diff_checksum: str,
    ) -> dict:
        proposal = self._get_proposal_or_raise(job_id, proposal_id)
        if not proposal.diff_checksum:
            raise ValueError("Proposal has no diff_checksum.")
        if str(base_diff_checksum) != str(proposal.diff_checksum):
            raise _StaleProposalError(_STALE_REASON)
        return _snapshot_from_record(proposal)

    def claim_and_save_user_message(
        self,
        *,
        snapshot: dict,
        message: str,
        idempotency_key: str | None,
        base_diff_checksum: str,
        owner: str,
        user_message_id: str,
    ) -> tuple[str, RepairAssistantMessageRecord | None]:
        """SHORT ATOMIC WRITE: Atomically claim an idempotency lease.
        
        Returns (ClaimOutcome, record_or_None).

        If CLAIMED: a new user message with status='processing' has been persisted.
        If EXPIRED_TAKEOVER: the expired lease was taken over.
        If COMPLETED: the existing completed record is returned.
        If ALREADY_PROCESSING: another worker holds the active lease.
        """
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        if not idempotency_key:
            raise ValueError("idempotency_key is required; missing key must fail before persistence")
        now = utc_now_text()
        from datetime import datetime, timedelta, timezone
        expiry_dt = datetime.now(timezone.utc) + timedelta(seconds=_REPAIR_ASSISTANT_LEASE_SECONDS)
        lease_expiry = expiry_dt.isoformat()
        message_payload = (
            user_message_id,
            str(snapshot["job_id"]),
            str(snapshot["proposal_id"]),
            snapshot.get("attempt_number"),
            "user",
            message,
            None,
            None,
            base_diff_checksum,
            None,
            "processing",
            now,
            idempotency_key,
        )
        outcome, existing = self._repo.claim_idempotency_lease(
            job_id=str(snapshot["job_id"]),
            proposal_id=str(snapshot["proposal_id"]),
            idempotency_key=idempotency_key,
            owner=owner,
            now=now,
            lease_expiry=lease_expiry,
            message_payload=message_payload,
        )
        return (outcome, existing)

    def renew_message_lease(
        self,
        *,
        message_id: str,
        job_id: str,
        proposal_id: str,
        idempotency_key: str,
        processing_owner: str,
        new_lease_expires_at: str,
    ) -> bool:
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        return self._repo.renew_lease(
            message_id=message_id,
            job_id=job_id,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            processing_owner=processing_owner,
            new_lease_expires_at=new_lease_expires_at,
        )

    def verify_message_ownership(
        self,
        *,
        message_id: str,
        job_id: str,
        proposal_id: str,
        idempotency_key: str,
        processing_owner: str,
    ) -> bool:
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        return self._repo.verify_ownership(
            message_id=message_id,
            job_id=job_id,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            processing_owner=processing_owner,
        )

    def finalize_message_lease(
        self,
        *,
        message_id: str,
        owner: str,
        status: str,
        generated_proposal_id: str | None = None,
        response_message_id: str | None = None,
    ) -> str:
        """Finalize lease with atomic CAS.

        Returns one of LeaseState.*
        """
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        return self._repo.finalize_lease(
            message_id=message_id,
            owner=owner,
            status=status,
            generated_proposal_id=generated_proposal_id,
            response_message_id=response_message_id,
        )

    def finalize_message_lease_with_failure(
        self,
        *,
        message_id: str,
        owner: str,
        status: str,
        failure_stage: str,
        failure_code: str,
        safe_failure_message: str,
        correlation_id: str,
        generated_proposal_id: str | None = None,
        response_message_id: str | None = None,
    ) -> str:
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        return self._repo.finalize_lease_with_failure(
            message_id=message_id,
            owner=owner,
            status=status,
            failure_stage=failure_stage,
            failure_code=failure_code,
            safe_failure_message=safe_failure_message,
            correlation_id=correlation_id,
            generated_proposal_id=generated_proposal_id,
            response_message_id=response_message_id,
        )

    def save_assistant_message_record(
        self,
        *,
        snapshot: dict,
        base_diff_checksum: str,
        intent: RepairAssistantIntent,
    ) -> RepairAssistantMessageRecord:
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        revision_intent_json = json.dumps({
            "action": intent.action,
            "assistant_message": intent.assistant_message,
            "tool": intent.tool,
            "user_instruction": intent.user_instruction,
            "resolved_instruction": intent.resolved_instruction,
            "revision_instruction": intent.user_instruction,
            "constraints": intent.constraints,
            "target_files": intent.target_files,
            "requires_clarification": intent.requires_clarification,
        }, separators=(",", ":"), sort_keys=True) if intent.action == "REQUEST_REVISION" else None

        if intent.action == "REQUEST_REVISION":
            status = "revision_generating"
        elif intent.action == "CLARIFICATION_REQUIRED":
            status = "clarification_required"
        else:
            status = "answered"

        assistant_message_id = uuid4().hex
        record = _record_from_snapshot(
            snapshot=snapshot,
            message_id=assistant_message_id,
            role="assistant",
            message_text=intent.assistant_message,
            action=intent.action,
            revision_intent_json=revision_intent_json,
            base_diff_checksum=base_diff_checksum,
            generated_proposal_id=None,
            status=status,
            created_at=utc_now_text(),
            idempotency_key=None,
        )
        self._repo.save_message(record)
        return record

    def save_error_message_record(
        self,
        *,
        snapshot: dict,
        base_diff_checksum: str,
        error_text: str,
    ) -> RepairAssistantMessageRecord:
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        record = _record_from_snapshot(
            snapshot=snapshot,
            message_id=uuid4().hex,
            role="assistant",
            message_text=error_text,
            action=None,
            revision_intent_json=None,
            base_diff_checksum=base_diff_checksum,
            generated_proposal_id=None,
            status="error",
            created_at=utc_now_text(),
            idempotency_key=None,
        )
        self._repo.save_message(record)
        return record

    def save_failure_message_record(
        self,
        *,
        snapshot: dict,
        base_diff_checksum: str,
        failure_stage: str,
        failure_code: str,
        safe_failure_message: str,
        correlation_id: str,
    ) -> RepairAssistantMessageRecord:
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        record = RepairAssistantMessageRecord(
            message_id=uuid4().hex,
            job_id=str(snapshot["job_id"]),
            proposal_id=str(snapshot["proposal_id"]),
            attempt_number=snapshot.get("attempt_number"),
            role="assistant",
            message_text=safe_failure_message,
            action=None,
            revision_intent_json=None,
            base_diff_checksum=base_diff_checksum,
            generated_proposal_id=None,
            status="revision_failed",
            created_at=utc_now_text(),
            idempotency_key=None,
            failure_stage=failure_stage,
            failure_code=failure_code,
            safe_failure_message=safe_failure_message,
            correlation_id=correlation_id,
        )
        self._repo.save_message(record)
        return record

    def update_message_for_revision(
        self,
        *,
        message_id: str,
        status: str,
        generated_proposal_id: str | None,
    ) -> None:
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        self._repo.update_message_status(
            message_id, status, generated_proposal_id=generated_proposal_id,
        )

    def update_message_outcome(self, *, message_id: str, status: str, message_text: str) -> None:
        if self._repo is None:
            raise RuntimeError("repair_assistant_repo is not configured")
        self._repo.update_message_outcome(message_id, status=status, message_text=message_text)

    def recheck_proposal_staleness(
        self,
        *,
        job_id: str,
        proposal_id: str,
        base_diff_checksum: str,
    ) -> dict:
        proposal = self._get_proposal_or_raise(job_id, proposal_id)
        if str(base_diff_checksum) != str(proposal.diff_checksum or ""):
            raise _StaleProposalError(_STALE_REASON)
        return _snapshot_from_record(proposal)

    def process_message(
        self,
        *,
        job_id: str,
        proposal_id: str,
        message: str,
        idempotency_key: str | None = None,
        base_diff_checksum: str,
    ) -> RepairAssistantResult:
        # ── Idempotency check ─────────────────────────────────────────
        if idempotency_key and self._repo is not None:
            existing = self._repo.get_message_by_idempotency_key(idempotency_key)
            if existing is not None:
                return self._record_to_result(existing)

        # ── Checksum verification ─────────────────────────────────────
        proposal = self._get_proposal_or_raise(job_id, proposal_id)
        if proposal.diff_checksum and proposal.diff_checksum != base_diff_checksum:
            return RepairAssistantResult(
                message_id="",
                assistant_message="Checksum mismatch: the proposal diff has changed since this context was loaded. Please refresh and try again.",
                action="blocked",
                revision_intent=None,
                revision_started=False,
                new_proposal_id=None,
                new_attempt_number=None,
                status="blocked",
            )

        # ── Build context ─────────────────────────────────────────────
        context = self.build_repair_assistant_context(job_id=job_id, proposal_id=proposal_id)
        context_with_message = RepairAssistantContext(
            job_id=context.job_id,
            proposal_id=context.proposal_id,
            proposal_status=context.proposal_status,
            attempt_number=context.attempt_number,
            base_diff_checksum=context.base_diff_checksum,
            diff_content=context.diff_content,
            failure_summary=context.failure_summary,
            reviewer_decision=context.reviewer_decision,
            reviewer_notes=context.reviewer_notes,
            prior_attempts=context.prior_attempts,
            prior_revision_instructions=context.prior_revision_instructions,
            previous_validation_result=context.previous_validation_result,
            available_versions=context.available_versions,
            pom_intelligence=context.pom_intelligence,
            user_comments=message,
            prior_reviewer_notes=context.prior_reviewer_notes,
        )

        # ── Persist user message ──────────────────────────────────────
        now = utc_now_text()
        user_message_id = uuid4().hex
        user_record = RepairAssistantMessageRecord(
            message_id=user_message_id,
            job_id=job_id,
            proposal_id=proposal_id,
            attempt_number=proposal.attempt_number,
            role="user",
            message_text=message,
            action=None,
            revision_intent_json=None,
            base_diff_checksum=base_diff_checksum,
            generated_proposal_id=None,
            status="answered",
            created_at=now,
            idempotency_key=idempotency_key,
        )
        if self._repo is not None:
            self._repo.save_message(user_record)

        # ── Build prompt and call model ───────────────────────────────
        prompt = self._build_model_prompt(context_with_message)
        model_result = self.call_assistant_model_with_ledger(
            job_id=job_id, proposal_id=proposal_id, prompt=prompt,
        )

        if not model_result.success:
            assistant_message_id = uuid4().hex
            error_record = RepairAssistantMessageRecord(
                message_id=assistant_message_id,
                job_id=job_id,
                proposal_id=proposal_id,
                attempt_number=proposal.attempt_number,
                role="assistant",
                message_text=f"Model call failed: {redact_model_summary(model_result.failure_reason)}",
                action=None,
                revision_intent_json=None,
                base_diff_checksum=base_diff_checksum,
                generated_proposal_id=None,
                status="error",
                created_at=utc_now_text(),
                idempotency_key=None,
            )
            if self._repo is not None:
                self._repo.save_message(error_record)
            return RepairAssistantResult(
                message_id=assistant_message_id,
                assistant_message="I'm sorry, the repair assistant model is currently unavailable.",
                action="error",
                revision_intent=None,
                revision_started=False,
                new_proposal_id=None,
                new_attempt_number=None,
                status="error",
            )

        # ── Parse intent ──────────────────────────────────────────────
        intent = self._parse_intent(model_result.content, user_message=message)
        if intent is None:
            assistant_message_id = uuid4().hex
            fallback_record = RepairAssistantMessageRecord(
                message_id=assistant_message_id,
                job_id=job_id,
                proposal_id=proposal_id,
                attempt_number=proposal.attempt_number,
                role="assistant",
                message_text="I analyzed the context but could not structure the response. Please try rephrasing.",
                action=None,
                revision_intent_json=None,
                base_diff_checksum=base_diff_checksum,
                generated_proposal_id=None,
                status="error",
                created_at=utc_now_text(),
                idempotency_key=None,
            )
            if self._repo is not None:
                self._repo.save_message(fallback_record)
            return RepairAssistantResult(
                message_id=assistant_message_id,
                assistant_message="I analyzed the context but could not structure the response. Please try rephrasing.",
                action="error",
                revision_intent=None,
                revision_started=False,
                new_proposal_id=None,
                new_attempt_number=None,
                status="error",
            )

        # ── Persist assistant message ─────────────────────────────────
        revision_intent_json = json.dumps({
            "action": intent.action,
            "assistant_message": intent.assistant_message,
            "tool": intent.tool,
            "user_instruction": intent.user_instruction,
            "resolved_instruction": intent.resolved_instruction,
            "revision_instruction": intent.user_instruction,
            "constraints": intent.constraints,
            "target_files": intent.target_files,
            "requires_clarification": intent.requires_clarification,
        }, separators=(",", ":"), sort_keys=True) if intent.action == "REQUEST_REVISION" else None

        if intent.action == "REQUEST_REVISION":
            status = "revision_generating"
        elif intent.action == "CLARIFICATION_REQUIRED":
            status = "clarification_required"
        else:
            status = "answered"

        assistant_message_id = uuid4().hex
        assistant_record = RepairAssistantMessageRecord(
            message_id=assistant_message_id,
            job_id=job_id,
            proposal_id=proposal_id,
            attempt_number=proposal.attempt_number,
            role="assistant",
            message_text=intent.assistant_message,
            action=intent.action,
            revision_intent_json=revision_intent_json,
            base_diff_checksum=base_diff_checksum,
            generated_proposal_id=None,
            status=status,
            created_at=utc_now_text(),
            idempotency_key=None,
        )
        if self._repo is not None:
            self._repo.save_message(assistant_record)

        # ── Return result ─────────────────────────────────────────────
        revision_started = False  # API layer handles regeneration
        return RepairAssistantResult(
            message_id=assistant_message_id,
            assistant_message=intent.assistant_message,
            action=intent.action,
            revision_intent=intent if intent.action == "REQUEST_REVISION" else None,
            revision_started=revision_started,
            new_proposal_id=None,
            new_attempt_number=None,
            status=status,
        )

    # ── Model call ────────────────────────────────────────────────────

    def _call_assistant_model(self, prompt: str) -> V2AssistantModelResult:
        return self._model_client.answer_with_role(
            role=V2ModelRole.ASSISTANT,
            prompt=prompt,
            fallback="I'm sorry, the repair assistant model is currently unavailable. Please try again later.",
            output_schema_name="RepairAssistantIntent",
            require_schema=True,
        )

    def call_assistant_model_with_ledger(
        self, *, job_id: str, proposal_id: str, prompt: str,
    ) -> V2AssistantModelResult:
        invocation_id = self._ledger_start(job_id=job_id, proposal_id=proposal_id, prompt=prompt)
        result = self._call_assistant_model(prompt)
        self._ledger_finish(invocation_id, result)
        return result

    # ── Prompt construction ───────────────────────────────────────────

    def _build_model_prompt(self, context: RepairAssistantContext) -> str:
        sections: list[str] = [
            _REPAIR_ASSISTANT_SYSTEM_PROMPT,
            "",
            "=== CONTEXT ===",
            f"Job ID: {context.job_id}",
            f"Proposal ID: {context.proposal_id}",
            f"Proposal Status: {context.proposal_status}",
            f"Attempt Number: {context.attempt_number or 'N/A'}",
            f"Base Diff Checksum: {context.base_diff_checksum}",
            "",
        ]

        if context.failure_summary:
            safe_summary = redact_model_summary(context.failure_summary)
            sections.append(f"=== ORIGINAL_FAILURE_EVIDENCE ===\n{safe_summary}\n")

        if context.diff_content:
            safe_diff = redact_patch_preview(context.diff_content, max_chars=8000)
            sections.append(f"=== CURRENT_UNAPPLIED_PROPOSAL ===\n{safe_diff}\n")
        else:
            sections.append("=== CURRENT_UNAPPLIED_PROPOSAL ===\nNONE\n")

        if context.reviewer_decision:
            sections.append(f"Reviewer Decision: {context.reviewer_decision}")
        if context.reviewer_notes:
            sections.append("Reviewer Notes:")
            for note in context.reviewer_notes:
                sections.append(f"  - {redact_model_summary(note)}")

        if context.prior_attempts:
            sections.append(f"=== PRIOR ATTEMPTS ({len(context.prior_attempts)}) ===")
            for i, attempt in enumerate(context.prior_attempts):
                sections.append(f"  Attempt {i+1}: {redact_model_summary(json.dumps(attempt, default=str))}")

        if context.prior_revision_instructions:
            sections.append("=== PRIOR REVISION INSTRUCTIONS ===")
            for instr in context.prior_revision_instructions:
                sections.append(f"  - {redact_model_summary(instr)}")

        if context.prior_reviewer_notes:
            sections.append("=== PRIOR REVIEWER NOTES ===")
            for note in context.prior_reviewer_notes:
                sections.append(f"  - {redact_model_summary(note)}")

        if context.previous_validation_result:
            safe_validation = redact_model_summary(context.previous_validation_result[:2000])
            sections.append(f"=== LATEST_APPLY_VALIDATION_RESULT ===\n{safe_validation}\n")
        else:
            sections.append("=== LATEST_APPLY_VALIDATION_RESULT ===\nNONE\n")

        if context.available_versions:
            sections.append(f"Available Target Versions: {', '.join(context.available_versions)}")

        if context.pom_intelligence:
            safe_pom = redact_model_summary(json.dumps(context.pom_intelligence, default=str)[:3000])
            sections.append(f"=== POM INTELLIGENCE ===\n{safe_pom}\n")

        sections.append(f"=== USER MESSAGE ===\n{context.user_comments}\n")
        sections.append("Respond with valid JSON only.")

        return "\n".join(sections)

    # ── Intent parsing ────────────────────────────────────────────────

    @staticmethod
    def _parse_intent(raw: str, user_message: str = "") -> RepairAssistantIntent | None:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            start = cleaned.find("\n")
            if start != -1:
                cleaned = cleaned[start:].strip()
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            brace = cleaned.find("{")
            if brace == -1:
                return None
            try:
                data = json.loads(cleaned[brace:])
            except json.JSONDecodeError:
                return None

        if not isinstance(data, dict):
            return None

        action = str(data.get("action", "ANSWER_ONLY"))
        if action not in ("ANSWER_ONLY", "REQUEST_REVISION", "CLARIFICATION_REQUIRED"):
            action = "ANSWER_ONLY"

        arguments = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
        model_instruction = str(arguments.get("user_instruction", data.get("revision_instruction", "")))
        return RepairAssistantIntent(
            action=action,
            assistant_message=str(data.get("assistant_message", "")),
            user_instruction=user_message if action == "REQUEST_REVISION" else model_instruction,
            resolved_instruction=str(arguments.get("resolved_instruction", "")),
            tool=(str(data.get("tool") or "") or "request_repair_revision") if action == "REQUEST_REVISION" else None,
            constraints=list(arguments.get("constraints", data.get("constraints", []))),
            target_files=list(arguments.get("target_files", data.get("target_files", []))),
            requires_clarification=bool(data.get("requires_clarification", False)),
        )

    # ── Context loaders ───────────────────────────────────────────────

    def _get_proposal_or_raise(self, job_id: str, proposal_id: str) -> V2RepairProposalRecord:
        if self._repair_repo is None:
            raise RuntimeError("repair_repo is not configured")
        proposal = self._repair_repo.get_proposal_for_job(job_id, proposal_id)
        if proposal is None:
            raise ValueError(f"Repair proposal {proposal_id!r} not found for job {job_id!r}")
        return proposal

    def _load_reviewer_notes(self, proposal: V2RepairProposalRecord) -> list[str]:
        notes: list[str] = []
        if proposal.reviewer_verdict_ref:
            verdict = self._read_ref(proposal.reviewer_verdict_ref)
            if verdict:
                notes.append(verdict)
        return notes

    def _load_prior_attempts(self, job_id: str, current: V2RepairProposalRecord) -> list[dict]:
        if self._repair_repo is None:
            return []
        proposals = self._repair_repo.list_proposals_by_job(job_id)
        prior: list[dict] = []
        for prop in proposals:
            if prop.proposal_id == current.proposal_id:
                continue
            prior.append({
                "proposal_id": prop.proposal_id,
                "attempt_number": prop.attempt_number,
                "status": prop.status,
                "failure_summary": prop.failure_summary,
                "reviewer_decision": prop.reviewer_decision,
                "created_at": prop.created_at,
            })
        return prior

    def _load_prior_revision_instructions(self, job_id: str, proposal_id: str) -> list[str]:
        if self._repo is None:
            return []
        messages = self._repo.list_messages(job_id, proposal_id)
        instructions: list[str] = []
        for msg in messages:
            if msg.action == "REQUEST_REVISION" and msg.revision_intent_json:
                try:
                    parsed = json.loads(msg.revision_intent_json)
                    instr = parsed.get("revision_instruction", "")
                    if instr:
                        instructions.append(instr)
                except (json.JSONDecodeError, TypeError):
                    pass
        return instructions

    def _load_available_versions(self, job_id: str) -> list[str]:
        _ = job_id
        return []

    def _load_pom_intelligence(self, job_id: str) -> dict | None:
        _ = job_id
        return None

    def _load_prior_reviewer_notes(self, job_id: str, proposal_id: str) -> list[str]:
        if self._repair_repo is None:
            return []
        proposals = self._repair_repo.list_proposals_by_job(job_id)
        notes: list[str] = []
        for prop in proposals:
            if prop.proposal_id == proposal_id:
                continue
            if prop.reviewer_decision:
                notes.append(f"[{prop.proposal_id}] {prop.reviewer_decision}")
                if prop.reviewer_verdict_ref:
                    verdict = self._read_ref(prop.reviewer_verdict_ref)
                    if verdict:
                        notes.append(verdict)
        return notes

    # ── File helpers ──────────────────────────────────────────────────

    @staticmethod
    def _read_ref(ref_path: str) -> str | None:
        path = Path(ref_path)
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None
        return None

    # ── Mapping helpers ───────────────────────────────────────────────

    @staticmethod
    def _record_to_message(record: RepairAssistantMessageRecord) -> RepairAssistantMessage:
        return RepairAssistantMessage(
            message_id=record.message_id,
            job_id=record.job_id,
            proposal_id=record.proposal_id,
            attempt_number=record.attempt_number,
            role=record.role,
            message_text=record.message_text,
            action=record.action,
            revision_intent_json=record.revision_intent_json,
            base_diff_checksum=record.base_diff_checksum,
            generated_proposal_id=record.generated_proposal_id,
            status=record.status,
            created_at=record.created_at,
            idempotency_key=record.idempotency_key,
            failure_stage=record.failure_stage,
            failure_code=record.failure_code,
            safe_failure_message=record.safe_failure_message,
            correlation_id=record.correlation_id,
        )

    @staticmethod
    def _record_to_result(record: RepairAssistantMessageRecord) -> RepairAssistantResult:
        intent: RepairAssistantIntent | None = None
        if record.action == "REQUEST_REVISION" and record.revision_intent_json:
            try:
                data = json.loads(record.revision_intent_json)
                intent = RepairAssistantIntent(
                    action="REQUEST_REVISION",
                    assistant_message=str(data.get("assistant_message", "")),
                    user_instruction=str(data.get("user_instruction", data.get("revision_instruction", ""))),
                    resolved_instruction=str(data.get("resolved_instruction", "")),
                    tool=str(data.get("tool") or "") or "request_repair_revision",
                    constraints=list(data.get("constraints", [])),
                    target_files=list(data.get("target_files", [])),
                    requires_clarification=bool(data.get("requires_clarification", False)),
                )
            except (json.JSONDecodeError, TypeError):
                pass
        return RepairAssistantResult(
            message_id=record.message_id,
            assistant_message=record.message_text,
            action=record.action or "ANSWER_ONLY",
            revision_intent=intent,
            revision_started=False,
            new_proposal_id=record.generated_proposal_id,
            new_attempt_number=record.attempt_number,
            status=record.status,
            failure_stage=record.failure_stage,
            failure_code=record.failure_code,
            correlation_id=record.correlation_id,
        )
