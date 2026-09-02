"""Migration-grounded, strictly read-only Assistant V2 conversation flow.

The module deliberately separates database work from the model invocation:

1. retrieve and reduce current migration truth in a short read UoW;
2. close the read UoW;
3. make exactly one model-client call;
4. validate/redact the result;
5. persist both messages and the invocation ledger in one short write UoW.

No function in this module executes a gate action, approval, resume, command,
POM edit, rollback, stage transition, or filesystem write.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Iterable, Mapping, Sequence

from migration_factory.control_tower.application.redaction import redact_model_summary
from migration_factory.control_tower.application.v2_assistant_model_client import (
    V2AssistantModelResult,
)
from migration_factory.control_tower.application.v2_assistant_response_composer import (
    AssistantResponseCard,
    V2AssistantResponseComposer,
)
from migration_factory.control_tower.application.v2_assistant_service import (
    AssistantMessage,
    V2AssistantService,
)
from migration_factory.control_tower.application.v2_llm_invocation_ledger import (
    V2LLMInvocationLedger,
    compute_content_checksum,
)
from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole


UnitOfWorkFactory = Callable[[], Any]

_RAW_EVENT_TYPES = frozenset({
    "stdout",
    "stderr",
    "command_stdout",
    "command_stderr",
    "runner_stdout",
    "runner_stderr",
})
_MODEL_TELEMETRY_TYPES = frozenset({
    "model_invocation_started",
    "model_invocation_completed",
    "model_invocation_failed",
    "assistant_model_invocation_started",
    "assistant_model_invocation_completed",
    "assistant_model_invocation_failed",
})
_BLOCK_EVENT_TYPES = frozenset({
    "approval_required",
    "stage_blocked_for_approval",
    "resume_rejected",
    "stage_blocked",
    "gate_blocked",
})
_RECOVERY_EVENT_TYPES = frozenset({
    "approval_completed",
    "approval_resume_queued",
    "resume_started",
    "resume_completed",
    "sandbox_transform_started",
    "sandbox_transform_completed",
    "repair_patch_applied",
    "repair_validation_passed",
    "stage_started",
    "stage_completed",
    "next_stage_queued",
})
_ACTION_INTENTS = frozenset({
    "apply_dependency_change",
    "rollback_pom_change",
    "continue_from_gate",
    "request_revision",
    "confirm",
    "approve",
    "reject",
    "capability_boundary",
})

ASSISTANT_FOCUSES = frozenset({
    "executive_status",
    "current_activity",
    "current_status",
    "current_blockers",
    "current_approval",
    "approval_decision_brief",
    "latest_event",
    "recent_progress",
    "gate_evidence_review",
    "artifact_review",
    "application_change_summary",
    "dependency_change_summary",
    "validation_summary",
    "risk_summary",
    "failure_explanation",
    "evidence_support",
    "mutation_attempt",
    "general",
})
ASSISTANT_STYLES = frozenset({
    "one_sentence",
    "concise",
    "standard",
    "detailed",
    "executive",
    "technical",
    "list",
})
_EVIDENCE_FOCUSES = frozenset({
    "approval_decision_brief",
    "gate_evidence_review",
    "artifact_review",
    "application_change_summary",
    "dependency_change_summary",
    "validation_summary",
    "risk_summary",
    "failure_explanation",
    "evidence_support",
})
_INTERNAL_OUTPUT_TERMS = (
    "event_id",
    "gate_id",
    "card_id",
    "invocation_id",
    "command_id",
    "context_checksum",
    "source_artifact_checksum",
    "pipeline_rows",
)
_MONITORING_PROMISES = (
    "i'll monitor",
    "i will monitor",
    "i'll keep watching",
    "i will keep watching",
    "i'll let you know",
    "i will let you know",
    "check back later",
    "in the background",
)


@dataclass(frozen=True, slots=True)
class AssistantGroundingEnvelope:
    """Bounded, redacted input prepared before the model call."""

    job_id: str
    question: str
    assistant_intent: str
    focus: str
    style: str
    prompt: str
    fallback: str
    conversation_history: tuple[dict[str, str], ...]
    context_checksum: str
    current_state: Mapping[str, Any]
    open_gate_id: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id must not be empty")
        if not self.question.strip():
            raise ValueError("question must not be empty")
        if not self.prompt.strip():
            raise ValueError("assistant prompt must not be empty")
        if not self.fallback.strip():
            raise ValueError("assistant fallback must not be empty")
        if self.focus not in ASSISTANT_FOCUSES:
            raise ValueError(f"unsupported assistant focus: {self.focus}")
        if self.style not in ASSISTANT_STYLES:
            raise ValueError(f"unsupported assistant style: {self.style}")
        if len(self.prompt) > 16_000:
            raise ValueError("assistant prompt exceeded the 16000 character bound")
        if len(self.fallback) > 16_000:
            raise ValueError("assistant fallback exceeded the 16000 character bound")
        if len(self.conversation_history) > 8:
            raise ValueError("conversation history exceeded the 8 message bound")


@dataclass(frozen=True, slots=True)
class V2AssistantConversationResult:
    user_message: AssistantMessage
    assistant_message: AssistantMessage
    model_result: V2AssistantModelResult
    context_checksum: str
    invocation_id: str
    current_state: Mapping[str, Any]


def resolve_response_style(question: str) -> str:
    """Resolve requested answer shape with a small deterministic vocabulary."""

    lowered = _text(question).strip().lower()
    if any(term in lowered for term in ("one sentence", "one-sentence", "single sentence")):
        return "one_sentence"
    if any(term in lowered for term in ("list ", "list them", "bullet", "checklist")):
        return "list"
    if any(term in lowered for term in ("executive", "management", "manager", "leadership")):
        return "executive"
    if any(term in lowered for term in ("technical", "implementation detail", "deep dive")):
        return "technical"
    if any(term in lowered for term in ("detailed", "in detail", "comprehensive")):
        return "detailed"
    if any(term in lowered for term in ("concise", "briefly", "short answer", "quick update")):
        return "concise"
    return "standard"


def resolve_request_focus(
    question: str,
    conversation_reference: Sequence[Mapping[str, str]] = (),
) -> str:
    """Resolve the evidence scope without becoming a large phrase router."""

    lowered = " ".join(_text(question).strip().lower().split())
    prior_assistant = " ".join(
        _text(turn.get("content", "")).lower()
        for turn in conversation_reference
        if _text(turn.get("role", "")).lower() == "assistant"
    )

    if any(term in lowered for term in ("do i need to approve", "should i approve", "is approval required", "do you recommend approval")):
        return "current_approval"
    if _looks_like_mutation_attempt(lowered):
        return "mutation_attempt"
    if _is_reference_only_follow_up(lowered):
        if any(term in prior_assistant for term in ("gate artifact", "approval evidence", "gate evidence")):
            return "gate_evidence_review"
        if any(term in prior_assistant for term in ("approv", "decision", "gate")):
            return "approval_decision_brief"
        if any(term in prior_assistant for term in ("test", "validation", "passed", "failed")):
            return "validation_summary"
        if any(term in prior_assistant for term in ("dependency", "dependencies", "pom")):
            return "dependency_change_summary"
        if any(term in prior_assistant for term in ("application", "files", "transformation")):
            return "application_change_summary"
        if any(term in prior_assistant for term in ("artifacts", "evidence")):
            return "artifact_review"
    if any(term in lowered for term in ("one-sentence executive", "executive update", "tell management", "tell leadership")):
        return "executive_status"
    if any(term in lowered for term in ("what exactly am i approving", "what am i approving", "what should i approve", "should i approve", "approval decision brief")):
        return "approval_decision_brief"
    if "artifact" in lowered and any(term in lowered for term in ("should review", "to review", "review them", "list them")):
        return "gate_evidence_review"
    if any(term in lowered for term in ("biggest migration risk", "highest risk", "risk increased", "current risk")):
        return "risk_summary"
    if any(term in lowered for term in ("what is happening right now", "what's happening right now", "what is happening now", "what's happening now", "happening now", "right now")):
        return "current_activity"
    if any(term in lowered for term in ("what happened most recently", "latest event", "most recent event")):
        return "latest_event"
    if any(term in lowered for term in ("what changed recently", "recent progress", "progressed recently", "last stage")):
        return "recent_progress"
    if any(term in lowered for term in ("anything blocking", "what is blocking", "current blocker", "is it stuck", "are we stuck")):
        return "current_blockers"
    if any(term in lowered for term in ("do i need to make a decision", "approval pending", "need my approval", "decision required")):
        return "current_approval"
    if any(term in lowered for term in ("what changed in the application", "which files were affected", "files changed", "transformations were")):
        return "application_change_summary"
    if any(term in lowered for term in ("dependencies changed", "dependency changed", "dependency changes", "pom dependencies", "libraries are declared", "build descriptor")):
        return "dependency_change_summary"
    if any(term in lowered for term in ("what passed", "what warned", "what failed", "validation summary", "build and test")):
        return "validation_summary"
    if any(term in lowered for term in ("why has the next stage not started", "why hasn't the next stage", "why is it still pending", "failure explanation")):
        return "failure_explanation"
    if any(term in lowered for term in ("what evidence supports", "evidence for that", "support that conclusion")):
        return "evidence_support"
    if "artifact" in lowered or "evidence" in lowered:
        return "artifact_review"
    if any(term in lowered for term in ("what is the current status", "current status", "migration status", "where are we")):
        return "current_status"
    if any(term in lowered for term in ("executive", "management", "leadership")):
        return "executive_status"
    return "general"


def _is_reference_only_follow_up(lowered: str) -> bool:
    words = set(re.findall(r"[a-z]+", lowered))
    references = {"yes", "them", "those", "that", "it", "plan", "artifacts", "list", "review"}
    return bool(words & {"yes", "them", "those", "that", "it"}) and words <= references | {"so", "can", "i", "please", "and", "the", "me"}


def _looks_like_mutation_attempt(lowered: str) -> bool:
    if any(phrase in lowered for phrase in (
        "do not apply", "don't apply", "do not execute", "don't execute",
        "do not write", "don't write", "do not change", "don't change",
        "just propose", "only propose",
    )):
        return False
    action = any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in (
        "approve", "reject", "continue", "resume", "execute", "apply", "write",
        "modify", "change the pom", "rollback", "roll back", "confirm checksum",
        "advance the stage", "start the next stage",
    ))
    advisory = any(term in lowered for term in (
        "what am i approving", "should i approve", "explain", "summarize", "what changed",
        "what will change", "draft", "propose", "review",
    ))
    return action and not advisory


def build_current_state_snapshot(
    *,
    job: Any,
    pipeline: Mapping[str, Any],
    open_gates: Sequence[Any],
    approvals: Sequence[Any],
    events: Sequence[Any],
    commands: Sequence[Any] = (),
    run_configuration: Any | None = None,
    artifact_previews: Sequence[Mapping[str, Any]] = (),
    gate_grounding: Mapping[str, Mapping[str, Any]] | None = None,
    current_repair: Any | None = None,
) -> dict[str, Any]:
    """Purely reduce persisted records into bounded current-state semantics.

    Event ordering is always derived from ``sequence``. Historical blocked or
    failed events are retained as audit evidence but are never promoted to a
    current blocker without a current gate, approval, blocked projection row,
    terminal job state, or unresolved current failure/repair condition.
    """

    ordered_events = tuple(sorted(events, key=_event_sequence))
    operational_events = tuple(
        event for event in ordered_events if _is_operational_event(event)
    )
    latest_operational = operational_events[-1] if operational_events else None

    pending_approvals = tuple(
        card
        for card in approvals
        if _text(getattr(card, "status", "")).lower() == "pending"
    )
    approved_approvals = tuple(
        card
        for card in approvals
        if _text(getattr(card, "status", "")).lower()
        in {"approved", "auto_approved"}
    )
    ordered_gates = tuple(
        sorted(
            (
                gate
                for gate in open_gates
                if _text(getattr(gate, "gate_status", "open")).lower() == "open"
            ),
            key=lambda gate: (
                _as_int(getattr(gate, "stage_index", 0)),
                _text(getattr(gate, "gate_phase", "")),
                _text(getattr(gate, "created_at", "")),
                _text(getattr(gate, "gate_id", "")),
            ),
        )
    )

    projected_rows = tuple(
        _safe_pipeline_row(row)
        for row in pipeline.get("rows", ())
        if isinstance(row, Mapping)
    )

    historical_blocks = tuple(
        _safe_event(event)
        for event in ordered_events
        if _is_block_event(event)
    )[-12:]
    latest_failure = _latest_failure_event(operational_events)
    unresolved_failure = _failure_is_current(
        job=job,
        latest_failure=latest_failure,
        operational_events=operational_events,
    )
    repair = _safe_repair_projection(current_repair)
    unresolved_repair = bool(repair and repair.get("unresolved"))

    # The shared UI projection intentionally preserves a phase's historical
    # failure marker. For current assistant semantics, a later authoritative
    # recovery/completion event makes that marker audit evidence, not a current
    # failed row. Expose it as recovered so the model cannot revive the failure.
    job_status = _text(getattr(job, "status", "")).lower() or "pending"
    rows = tuple(
        {
            **row,
            "status": "recovered",
            "latest_message": "A later operational event records recovery or progress.",
        }
        if _text(row.get("status", "")).lower() == "failed"
        and not unresolved_failure
        and job_status != "failed"
        else row
        for row in projected_rows
    )
    blocked_rows = tuple(
        row for row in rows if _text(row.get("status", "")).lower() == "blocked"
    )
    failed_rows = tuple(
        row for row in rows if _text(row.get("status", "")).lower() == "failed"
    )
    running_rows = tuple(
        row for row in rows if _text(row.get("status", "")).lower() == "running"
    )

    gate_grounding = gate_grounding or {}
    gate_snapshots = tuple(
        _safe_gate_snapshot(
            gate,
            gate_grounding.get(_text(getattr(gate, "gate_id", "")), {}),
        )
        for gate in ordered_gates
    )

    approval_required_now = bool(pending_approvals or ordered_gates)
    is_failed = bool(job_status == "failed" or failed_rows or unresolved_failure)

    current_block_reasons: list[dict[str, Any]] = []
    for gate in gate_snapshots:
        current_block_reasons.append({
            "kind": "open_gate",
            "stage_index": gate.get("stage_index"),
            "summary": (
                f"An open {gate.get('gate_phase') or 'phase'} gate requires an "
                "explicit decision in the Decisions controls."
            ),
        })
    for card in pending_approvals:
        current_block_reasons.append({
            "kind": "unresolved_approval",
            "stage_index": getattr(card, "stage_index", None),
            "summary": _bounded_text(getattr(card, "summary", "Approval is pending."), 240),
        })
    for row in blocked_rows:
        current_block_reasons.append({
            "kind": "blocked_pipeline_row",
            "stage_index": None,
            "summary": _bounded_text(
                row.get("latest_message")
                or row.get("label")
                or row.get("key")
                or "A current pipeline row is blocked.",
                240,
            ),
        })
    for row in failed_rows:
        current_block_reasons.append({
            "kind": "failed_pipeline_row",
            "stage_index": None,
            "summary": _bounded_text(
                row.get("latest_message")
                or row.get("label")
                or row.get("key")
                or "A current pipeline row has failed.",
                240,
            ),
        })
    if job_status in {"blocked", "failed"}:
        current_block_reasons.append({
            "kind": "terminal_job_state",
            "stage_index": None,
            "summary": f"The current job state is {job_status}.",
        })
    if unresolved_failure and latest_failure is not None:
        current_block_reasons.append({
            "kind": "unresolved_failure",
            "stage_index": getattr(latest_failure, "stage", None),
            "summary": _bounded_text(getattr(latest_failure, "message", "A current failure is unresolved."), 240),
        })
    if unresolved_repair:
        current_block_reasons.append({
            "kind": "unresolved_repair",
            "stage_index": repair.get("stage_index"),
            "summary": _bounded_text(
                repair.get("status_reason") or repair.get("failure_summary") or "A current repair condition is unresolved.",
                240,
            ),
        })

    # Failed is a current blocking condition. Historical blocked/failed events
    # alone are intentionally absent from this expression.
    is_blocked = bool(
        ordered_gates
        or pending_approvals
        or blocked_rows
        or failed_rows
        or job_status in {"blocked", "failed"}
        or unresolved_failure
        or unresolved_repair
    )
    completed = _current_state_is_completed(job_status, rows, operational_events)
    is_running = bool(
        not is_blocked
        and not is_failed
        and not completed
        and (
            job_status == "running"
            or running_rows
            or (
                latest_operational is not None
                and (
                    _text(getattr(latest_operational, "status", "")).lower() == "running"
                    or _text(getattr(latest_operational, "type", "")).lower().endswith("_started")
                )
            )
        )
    )

    if is_failed:
        overall_state = "failed"
    elif approval_required_now:
        overall_state = "awaiting_approval"
    elif is_blocked:
        overall_state = "blocked"
    elif completed:
        overall_state = "completed"
    elif is_running:
        overall_state = "running"
    else:
        overall_state = job_status

    artifact_kinds: list[str] = []
    for event in ordered_events:
        if _text(getattr(event, "type", "")).lower() != "artifact_written":
            continue
        payload = _event_payload(event)
        kind = _bounded_text(payload.get("artifact_kind", ""), 120)
        if kind and kind not in artifact_kinds:
            artifact_kinds.append(kind)

    safe_previews = tuple(_safe_artifact_preview(item) for item in artifact_previews)[:8]
    recent_audit_events = tuple(_safe_event(event) for event in operational_events[-12:])
    safe_latest_failure = _safe_event(latest_failure) if latest_failure is not None else None
    stage_snapshot = _build_stage_snapshot(
        job=job,
        pipeline=pipeline,
        run_configuration=run_configuration,
        commands=commands,
        operational_events=operational_events,
        current_block_reasons=current_block_reasons,
        approval_required_now=approval_required_now,
        artifact_previews=safe_previews,
    )

    artifact_ownership: list[dict[str, Any]] = []
    for event in ordered_events:
        if _text(getattr(event, "type", "")).lower() != "artifact_written":
            continue
        payload = _event_payload(event)
        kind = _bounded_text(payload.get("artifact_kind", ""), 120)
        if not kind:
            continue
        owned = {"kind": kind, "stage_index": getattr(event, "stage", None)}
        if owned not in artifact_ownership:
            artifact_ownership.append(owned)

    return {
        "overall_state": overall_state,
        "is_running": is_running,
        "is_blocked": is_blocked,
        "is_failed": is_failed,
        "approval_required_now": approval_required_now,
        "open_gate": gate_snapshots[0] if gate_snapshots else None,
        "open_gates": list(gate_snapshots),
        "current_block_reasons": current_block_reasons[:8],
        "latest_operational_event": (
            _safe_event(latest_operational) if latest_operational is not None else None
        ),
        "historical_block_events": list(historical_blocks),
        "job": {
            "job_id": _text(getattr(job, "job_id", "")),
            "pipeline_id": _text(getattr(job, "pipeline_id", "")),
            "status": job_status,
        },
        "pipeline_rows": list(rows),
        "pending_approvals": [_safe_approval(card) for card in pending_approvals[:8]],
        "approved_approvals": [_safe_approval(card) for card in approved_approvals[:8]],
        "current_failure_repair": {
            "unresolved": bool(unresolved_failure or unresolved_repair),
            "latest_failure": safe_latest_failure,
            "repair": repair,
        },
        "artifacts": {
            "kinds": artifact_kinds[-20:],
            "owned": artifact_ownership[-24:],
            "previews": list(safe_previews),
        },
        "migration_snapshot": stage_snapshot,
        "recent_audit_events": list(recent_audit_events),
        "semantics": {
            "pending_stages_are_blockers": False,
            "missing_artifacts_are_blockers": False,
            "generic_guardrails_are_blockers": False,
            "historical_block_events_are_current": False,
        },
    }


def _build_stage_snapshot(
    *,
    job: Any,
    pipeline: Mapping[str, Any],
    run_configuration: Any | None,
    commands: Sequence[Any],
    operational_events: Sequence[Any],
    current_block_reasons: Sequence[Mapping[str, Any]],
    approval_required_now: bool,
    artifact_previews: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    route_steps = _resolve_route_steps(job=job, run_configuration=run_configuration)
    active_stage = _resolve_active_stage(pipeline, operational_events, commands)
    completed_stages = _completed_stage_indices(operational_events)
    active_position = next(
        (index for index, step in enumerate(route_steps) if _as_int(step.get("stage_index")) == active_stage),
        None,
    )
    if active_position is None and route_steps:
        completed_positions = [
            index
            for index, step in enumerate(route_steps)
            if _as_int(step.get("stage_index")) in completed_stages
        ]
        active_position = min(max(completed_positions, default=-1) + 1, len(route_steps) - 1)
        active_stage = _as_int(route_steps[active_position].get("stage_index"))

    active_step = dict(route_steps[active_position]) if active_position is not None else None
    previous_step = (
        dict(route_steps[active_position - 1])
        if active_position is not None and active_position > 0
        else None
    )
    next_step = (
        dict(route_steps[active_position + 1])
        if active_position is not None and active_position + 1 < len(route_steps)
        else None
    )
    latest_operational = operational_events[-1] if operational_events else None
    active_phase_event = next(
        (
            event
            for event in reversed(operational_events)
            if _as_int(getattr(event, "stage", 0)) == active_stage
            and _text(getattr(event, "type", "")).lower() != "artifact_written"
        ),
        latest_operational,
    )
    active_phase = _phase_for_event(active_phase_event)
    latest_result = next(
        (event for event in reversed(operational_events) if _is_meaningful_result_event(event)),
        None,
    )
    recent_transitions = [
        _safe_event(event)
        for event in operational_events
        if _is_meaningful_transition(event)
    ][-8:]
    previous_stage = _as_int(previous_step.get("stage_index")) if previous_step else 0
    previous_result = _stage_result_summary(operational_events, previous_stage) if previous_stage else None
    validation_stage = active_stage
    if not any(
        getattr(event, "stage", None) == active_stage
        and _text(getattr(event, "type", "")).lower().startswith(("build_", "test_"))
        for event in operational_events
    ):
        validation_stage = max(completed_stages, default=active_stage)
    build_test = _build_test_summary(operational_events, validation_stage)
    highest_risk = _highest_supported_risk(
        current_block_reasons=current_block_reasons,
        artifact_previews=artifact_previews,
        build_test=build_test,
    )
    current_blocker = (
        _bounded_text(current_block_reasons[0].get("summary", ""), 240)
        if current_block_reasons
        else None
    )
    completed_route_steps = sum(
        1 for step in route_steps if _as_int(step.get("stage_index")) in completed_stages
    )
    return {
        "total_route_steps": len(route_steps),
        "completed_route_steps": completed_route_steps,
        "active_stage_index": active_stage,
        "active_route_step": active_step,
        "active_step_source_profile": active_step.get("source_profile") if active_step else None,
        "active_step_target_profile": active_step.get("target_profile") if active_step else None,
        "active_phase": active_phase,
        "previous_step_result": previous_result,
        "next_route_step": next_step,
        "current_blocker": current_blocker,
        "current_approval_requirement": (
            "An explicit approval decision is required." if approval_required_now else None
        ),
        "latest_meaningful_result": _safe_event(latest_result) if latest_result is not None else None,
        "latest_operational_event": _safe_event(latest_operational) if latest_operational is not None else None,
        "recent_meaningful_transitions": recent_transitions,
        "immediate_next_expected_backend_milestone": _next_expected_milestone(
            active_stage=active_stage,
            active_phase=active_phase,
            next_step=next_step,
            approval_required_now=approval_required_now,
        ),
        "current_highest_supported_risk": highest_risk,
        "current_build_test_result": build_test,
    }


def _resolve_route_steps(*, job: Any, run_configuration: Any | None) -> list[dict[str, Any]]:
    payload = _json_mapping(getattr(run_configuration, "payload_json", ""))
    source_profile = _text(payload.get("source_profile", ""))
    target_profile = _text(payload.get("target_profile", ""))
    if source_profile and target_profile:
        try:
            from migration_factory.control_tower.application.v2_stage_progression import (
                compute_profile_route,
                route_step_to_dict,
            )

            route = compute_profile_route(source_profile, target_profile)
            return [
                route_step_to_dict(step, include_execution_stage=True)
                for step in route.route_steps
            ]
        except (ImportError, TypeError, ValueError):
            pass
    raw_stages = _json_list(getattr(job, "stage_chain_json", ""))
    return [
        {
            "route_step_index": index,
            "stage_index": _as_int(stage.get("stage_index")) or index,
            "source_profile": _bounded_text(stage.get("source_profile", ""), 120),
            "target_profile": _bounded_text(stage.get("target_profile", ""), 120),
        }
        for index, stage in enumerate(raw_stages, start=1)
        if isinstance(stage, Mapping)
    ]


def _resolve_active_stage(
    pipeline: Mapping[str, Any],
    events: Sequence[Any],
    commands: Sequence[Any],
) -> int:
    for event in reversed(events):
        event_type = _text(getattr(event, "type", "")).lower()
        if event_type == "artifact_written":
            continue
        if event_type == "next_stage_queued":
            payload = _event_payload(event)
            return _as_int(payload.get("to_stage") or getattr(event, "stage", 0))
        if (
            event_type.endswith(("_started", "_completed", "_failed"))
            or event_type in {"stage_completed", "approval_required", "stage_blocked_for_approval"}
        ) and getattr(event, "stage", None) is not None:
            return _as_int(getattr(event, "stage", 0))
    projected = _as_int(pipeline.get("active_stage_index"))
    if projected:
        return projected
    if commands:
        return max(_as_int(getattr(command, "stage_index", 0)) for command in commands)
    staged = [_as_int(getattr(event, "stage", 0)) for event in events if getattr(event, "stage", None) is not None]
    return max(staged, default=1)


def _completed_stage_indices(events: Sequence[Any]) -> set[int]:
    completed: set[int] = set()
    for event in events:
        event_type = _text(getattr(event, "type", "")).lower()
        payload = _event_payload(event)
        if event_type == "stage_completed":
            completed.add(_as_int(getattr(event, "stage", 0)))
        elif event_type == "next_stage_queued":
            completed.add(_as_int(payload.get("from_stage")))
    completed.discard(0)
    return completed


def _phase_for_event(event: Any | None) -> str | None:
    if event is None:
        return None
    event_type = _text(getattr(event, "type", "")).lower()
    if event_type.endswith("_queued"):
        return "queued"
    for token, phase in (
        ("analysis", "analysis"),
        ("approval", "approval"),
        ("gate", "approval"),
        ("transform", "transformation"),
        ("rewrite", "transformation"),
        ("build", "build"),
        ("test", "test"),
        ("repair", "repair"),
        ("stage", "stage_transition"),
        ("migration", "migration_completion"),
    ):
        if token in event_type:
            return phase
    return "operational"


def _is_meaningful_result_event(event: Any) -> bool:
    event_type = _text(getattr(event, "type", "")).lower()
    status = _text(getattr(event, "status", "")).lower()
    return status in {"completed", "passed", "pass", "failed", "warning", "warned"} and (
        event_type.endswith(("_completed", "_failed", "_passed"))
        or event_type in {"stage_completed", "approval_completed"}
    )


def _is_meaningful_transition(event: Any) -> bool:
    event_type = _text(getattr(event, "type", "")).lower()
    return event_type.endswith(("_started", "_completed", "_failed", "_queued")) or event_type in {
        "approval_required", "stage_blocked_for_approval", "repair_validation_passed",
    }


def _stage_result_summary(events: Sequence[Any], stage_index: int) -> dict[str, Any] | None:
    relevant = [event for event in events if _as_int(getattr(event, "stage", 0)) == stage_index]
    if not relevant:
        return None
    validation = _build_test_summary(relevant, stage_index)
    latest = next((event for event in reversed(relevant) if _is_meaningful_result_event(event)), relevant[-1])
    return {
        "stage_index": stage_index,
        "summary": _bounded_text(getattr(latest, "message", ""), 320),
        "build_test": validation,
    }


def _build_test_summary(events: Sequence[Any], stage_index: int) -> dict[str, Any]:
    build = None
    test = None
    for event in events:
        if _as_int(getattr(event, "stage", 0)) != stage_index:
            continue
        event_type = _text(getattr(event, "type", "")).lower()
        payload = _event_payload(event)
        item = {
            "status": _bounded_text(
                payload.get("build_status") or payload.get("test_status") or getattr(event, "status", ""),
                120,
            ),
            "message": _bounded_text(getattr(event, "message", ""), 280),
        }
        if event_type.startswith("build_"):
            build = item
        elif event_type.startswith("test_"):
            test = item
    return {"stage_index": stage_index, "build": build, "test": test}


def _highest_supported_risk(
    *,
    current_block_reasons: Sequence[Mapping[str, Any]],
    artifact_previews: Sequence[Mapping[str, Any]],
    build_test: Mapping[str, Any],
) -> dict[str, Any] | None:
    risk_blocks = [
        item
        for item in current_block_reasons
        if _text(item.get("kind", "")) not in {"open_gate", "unresolved_approval"}
    ]
    if risk_blocks:
        return {"level": "high", "summary": _bounded_text(risk_blocks[0].get("summary", ""), 240), "basis": "current persisted failure or blocker"}
    for kind in ("test", "build"):
        result = build_test.get(kind)
        if isinstance(result, Mapping) and "warning" in _text(result.get("status", "")).lower():
            return {"level": "medium", "summary": _bounded_text(result.get("message") or f"{kind.title()} completed with warnings.", 240), "basis": f"{kind} validation evidence"}
    for preview in artifact_previews:
        preview_kind = _text(preview.get("kind", "")).lower()
        if not any(term in preview_kind for term in ("risk", "policy", "validation")):
            continue
        text = _text(preview.get("preview", ""))
        match = re.search(r"(?im)^.{0,80}(?:high[- ]risk|critical risk).{0,140}$", text)
        if match:
            return {"level": "supported", "summary": _bounded_text(match.group(0), 240), "basis": _bounded_text(preview.get("kind", "artifact evidence"), 120)}
    return None


def _next_expected_milestone(
    *,
    active_stage: int,
    active_phase: str | None,
    next_step: Mapping[str, Any] | None,
    approval_required_now: bool,
) -> str | None:
    if approval_required_now:
        return f"Stage {active_stage} approval decision"
    labels = {
        "analysis": "analysis completion",
        "transformation": "transformation completion",
        "build": "build completion",
        "test": "test completion",
        "repair": "repair validation completion",
    }
    if active_phase in labels:
        return f"Stage {active_stage} {labels[active_phase]}"
    if active_phase == "queued":
        return f"Stage {active_stage} analysis start"
    if next_step:
        return f"Stage {_as_int(next_step.get('stage_index'))} analysis start"
    if active_stage:
        return f"Stage {active_stage} completion"
    return None


def _json_mapping(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _json_list(raw: Any) -> list[Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return list(value) if isinstance(value, list) else []


def _focus_grounding(current_state: Mapping[str, Any], focus: str) -> dict[str, Any]:
    """Select only evidence relevant to the current question's focus."""

    snapshot = dict(current_state.get("migration_snapshot", {}))
    base: dict[str, Any] = {
        "job": {"job_id": current_state.get("job", {}).get("job_id")},
        "overall_state": current_state.get("overall_state"),
        "is_running": current_state.get("is_running"),
        "is_blocked": current_state.get("is_blocked"),
        "is_failed": current_state.get("is_failed"),
        "approval_required_now": current_state.get("approval_required_now"),
        "migration_snapshot": snapshot,
        "semantics": current_state.get("semantics", {}),
    }
    if focus in {"current_blockers", "failure_explanation", "risk_summary"}:
        base["current_block_reasons"] = current_state.get("current_block_reasons", [])
        base["current_failure_repair"] = current_state.get("current_failure_repair", {})
    if focus in {"current_approval", "approval_decision_brief", "gate_evidence_review"}:
        base["open_gate"] = current_state.get("open_gate")
        base["pending_approvals"] = current_state.get("pending_approvals", [])
        base["approved_approvals"] = current_state.get("approved_approvals", [])
    if focus in {"latest_event", "recent_progress", "evidence_support", "validation_summary"}:
        base["recent_audit_events"] = current_state.get("recent_audit_events", [])
    if focus in _EVIDENCE_FOCUSES:
        base["artifacts"] = current_state.get("artifacts", {})
    if focus in {"current_status", "general"}:
        base["pipeline_rows"] = current_state.get("pipeline_rows", [])
    return base


def _build_evidence_ref_catalog(state: Mapping[str, Any]) -> list[dict[str, str]]:
    catalog: list[dict[str, str]] = [{
        "ref": "state:current",
        "summary": _bounded_text(json.dumps({
            "overall_state": state.get("overall_state"),
            "is_running": state.get("is_running"),
            "is_blocked": state.get("is_blocked"),
            "is_failed": state.get("is_failed"),
            "approval_required_now": state.get("approval_required_now"),
            "migration_snapshot": state.get("migration_snapshot", {}),
        }, default=str), 1_600),
    }]
    snapshot = state.get("migration_snapshot", {})
    if isinstance(snapshot, Mapping):
        for index, item in enumerate(snapshot.get("recent_meaningful_transitions", [])[-8:], start=1):
            if isinstance(item, Mapping):
                catalog.append({
                    "ref": f"transition:{index}",
                    "summary": _bounded_text(item.get("message") or item.get("type"), 260),
                })
    for item in state.get("artifacts", {}).get("owned", [])[-12:]:
        if not isinstance(item, Mapping):
            continue
        ref = f"artifact:stage{_as_int(item.get('stage_index'))}:{_bounded_text(item.get('kind'), 80)}"
        if not any(entry["ref"] == ref for entry in catalog):
            catalog.append({"ref": ref, "summary": ref})
    gate = state.get("open_gate")
    if isinstance(gate, Mapping):
        for item in gate.get("evidence", [])[:8]:
            if not isinstance(item, Mapping):
                continue
            ref = f"gate:stage{_as_int(gate.get('stage_index'))}:{_bounded_text(item.get('kind'), 80)}"
            catalog.append({
                "ref": ref,
                "summary": _bounded_text(item.get("content") or item.get("kind"), 320),
            })
    return catalog[:24]


def build_assistant_prompt(
    *,
    question: str,
    assistant_intent: str,
    focus: str = "general",
    style: str = "standard",
    current_state: Mapping[str, Any],
    user_context: Sequence[Mapping[str, str]],
    model_context: Mapping[str, Any] | None = None,
    max_chars: int = 8_000,
) -> str:
    """Serialize bounded authoritative grounding for one natural-language ask."""
    relevant_state = _focus_grounding(current_state, focus)
    if assistant_intent in {
        "pom_or_dependency_explanation",
        "artifact_content",
        "stage3_dependency_review",
        "pom_change_proposal",
        "pom_dependency_change_request",
    }:
        relevant_state["artifacts"] = current_state.get("artifacts", {})
    conversation_reference = {
        "authority": "non_authoritative",
        "purpose": "reference_resolution_only",
        "recent_turns": [
            {
                "role": _bounded_text(item.get("role", "user"), 20),
                "content": _bounded_text(item.get("content", ""), 400),
            }
            for item in list(user_context)[-6:]
            if isinstance(item, Mapping)
            and _text(item.get("role", "")).lower() in {"user", "assistant"}
            and _text(item.get("content", "")).strip()
        ],
    }
    evidence_catalog = _build_evidence_ref_catalog(relevant_state)
    prompt: dict[str, Any] = {
        "question": question,
        "request_focus": focus,
        "response_style": style,
        "assistant_intent_hint": assistant_intent,
        "current_state": relevant_state,
        "state_semantics": {
            "overall_state": current_state.get("overall_state"),
            "is_running": current_state.get("is_running"),
            "is_blocked": current_state.get("is_blocked"),
            "is_failed": current_state.get("is_failed"),
            "approval_required_now": current_state.get("approval_required_now"),
            "missing_artifacts_mean_blocked": False,
        },
        "pipeline_rows": relevant_state.get("pipeline_rows", []),
        "latest_events": relevant_state.get("recent_audit_events", []),
        "pending_approvals": relevant_state.get("pending_approvals", []),
        "approved_approvals": relevant_state.get("approved_approvals", []),
        "failure_summary": relevant_state.get("current_failure_repair", {}),
        "artifact_kinds": relevant_state.get("artifacts", {}).get("kinds", []),
        "artifact_previews": relevant_state.get("artifacts", {}).get("previews", []),
        "conversation_reference": conversation_reference,
        "evidence_ref_catalog": evidence_catalog,
        "answer_contract": {
            "format": "json_object",
            "fields": [
                "answer", "focus", "observed_claims", "technical_explanation",
                "evidence_refs", "uncertainty", "requested_style_satisfied",
            ],
            "requested_focus": focus,
            "requested_style": style,
            "allowed_evidence_refs": [item["ref"] for item in evidence_catalog],
            "direct_answer_first": True,
            "fixed_status_template": False,
            "current_state_is_authoritative": True,
            "conversation_reference_is_non_authoritative": True,
            "do_not_answer_unrelated_previous_questions": True,
            "chat_is_strictly_read_only": True,
            "no_monitoring_or_background_promises": True,
            "no_internal_ids_or_provider_details_unless_requested": True,
        },
    }
    if assistant_intent == "model_status" and model_context:
        prompt["model"] = dict(model_context)

    safe = _serialize_redacted(prompt)
    if len(safe) <= max_chars:
        return safe

    compact_state = dict(relevant_state)
    compact_state["recent_audit_events"] = list(
        relevant_state.get("recent_audit_events", [])[-4:]
    )
    compact_state["historical_block_events"] = list(
        relevant_state.get("historical_block_events", [])[-4:]
    )
    compact_state["pipeline_rows"] = [
        {"key": row.get("key"), "status": row.get("status")}
        for row in relevant_state.get("pipeline_rows", [])
        if isinstance(row, Mapping)
    ]
    compact_state["open_gates"] = []
    compact_state["artifacts"] = {
        "kinds": list(relevant_state.get("artifacts", {}).get("kinds", []))[-12:],
        "owned": list(relevant_state.get("artifacts", {}).get("owned", []))[-12:],
        # The top-level compatibility field below carries the bounded preview
        # once; do not duplicate several kilobytes inside current_state.
        "previews": [],
    }
    prompt["current_state"] = compact_state
    prompt["latest_events"] = compact_state["recent_audit_events"]
    prompt["pipeline_rows"] = compact_state["pipeline_rows"]
    safe = _serialize_redacted(prompt)
    if len(safe) <= max_chars:
        return safe

    prompt["conversation_reference"]["recent_turns"] = conversation_reference["recent_turns"][-4:]
    prompt["artifact_previews"] = [
        {
            **dict(item),
            "preview": _bounded_text(item.get("preview", ""), 1_200),
        }
        for item in list(relevant_state.get("artifacts", {}).get("previews", []))[:3]
        if isinstance(item, Mapping)
    ]
    compact_gate = compact_state.get("open_gate")
    if isinstance(compact_gate, Mapping):
        compact_gate = dict(compact_gate)
        compact_gate["evidence"] = [
            {
                **dict(item),
                "content": _bounded_text(item.get("content", ""), 320),
            }
            for item in list(compact_gate.get("evidence", []))[:8]
            if isinstance(item, Mapping)
        ]
        compact_state["open_gate"] = compact_gate
    safe = _serialize_redacted(prompt)
    if len(safe) <= max_chars:
        return safe

    # Last-resort structured reduction. Always return valid JSON; raw string
    # slicing can cut inside an escape or value and make grounding unusable.
    minimal_state = {
        key: compact_state.get(key)
        for key in (
            "overall_state",
            "is_running",
            "is_blocked",
            "is_failed",
            "approval_required_now",
            "open_gate",
            "current_block_reasons",
            "latest_operational_event",
            "current_failure_repair",
            "migration_snapshot",
            "semantics",
        )
    }
    minimal_prompt = {
        "question": _bounded_text(question, 1_200),
        "assistant_intent_hint": assistant_intent,
        "current_state": minimal_state,
        "state_semantics": prompt["state_semantics"],
        "pipeline_rows": compact_state["pipeline_rows"][:12],
        "latest_events": compact_state["recent_audit_events"][-2:],
        "pending_approvals": list(relevant_state.get("pending_approvals", []))[:3],
        "approved_approvals": list(relevant_state.get("approved_approvals", []))[-3:],
        "failure_summary": relevant_state.get("current_failure_repair", {}),
        "artifact_kinds": list(relevant_state.get("artifacts", {}).get("kinds", []))[-12:],
        "artifact_previews": prompt["artifact_previews"][:1],
        "conversation_reference": {
            **prompt["conversation_reference"],
            "recent_turns": prompt["conversation_reference"]["recent_turns"][-2:],
        },
        "answer_contract": prompt["answer_contract"],
        "request_focus": focus,
        "response_style": style,
    }
    if assistant_intent == "model_status" and model_context:
        minimal_prompt["model"] = dict(model_context)
    safe = _serialize_redacted(minimal_prompt)
    if len(safe) <= max_chars:
        return safe

    minimal_prompt["question"] = _bounded_text(question, 400)
    minimal_prompt["artifact_previews"] = [
        {
            **dict(item),
            "preview": _bounded_text(item.get("preview", ""), 400),
        }
        for item in minimal_prompt["artifact_previews"]
    ]
    minimal_prompt["current_state"]["open_gate"] = _minimal_gate_snapshot(
        minimal_prompt["current_state"].get("open_gate")
    )
    safe = _serialize_redacted(minimal_prompt)
    if len(safe) > max_chars:
        raise ValueError("assistant grounding could not fit the configured JSON bound")
    return safe


def build_read_only_fallback(
    *,
    question: str,
    assistant_intent: str,
    focus: str,
    style: str,
    current_state: Mapping[str, Any],
    model_context: Mapping[str, Any] | None = None,
) -> str:
    """Return deterministic, non-executing guidance after one model failure."""

    state = _text(current_state.get("overall_state", "pending")) or "pending"
    latest = current_state.get("latest_operational_event")
    latest_summary = ""
    if isinstance(latest, Mapping):
        latest_summary = _bounded_text(
            latest.get("message") or latest.get("type") or "",
            240,
        )

    if assistant_intent == "model_status":
        model_status = _text((model_context or {}).get("status", "unknown")) or "unknown"
        return (
            f"The configured assistant model status is {model_status}. "
            "No migration action was taken."
        )

    if focus == "mutation_attempt" or assistant_intent == "capability_boundary":
        return (
            "I cannot approve, execute, write files, or change migration stages from chat. "
            "I can explain current evidence, compare artifacts, and draft a non-executing "
            "recommendation; use the explicit Decisions controls or dedicated UI or API control for actions."
        )

    focus_answer = _build_focus_fallback(
        focus=focus,
        style=style,
        current_state=current_state,
    )
    if focus_answer is not None:
        return focus_answer

    if assistant_intent == "pom_or_dependency_explanation":
        root_pom = _root_pom_preview(current_state)
        if isinstance(root_pom, Mapping) and root_pom.get("exists"):
            preview_text = _bounded_text(root_pom.get("preview", ""), 2_000)
            return (
                "The backend-resolved root POM is available for read-only review:\n\n"
                f"{preview_text}"
            ).strip()
        reason = (
            _bounded_text(root_pom.get("reason", ""), 160)
            if isinstance(root_pom, Mapping)
            else "no persisted root POM preview is available"
        )
        available = [
            _bounded_text(kind, 120)
            for kind in current_state.get("artifacts", {}).get("kinds", [])
            if _bounded_text(kind, 120)
        ]
        available_note = (
            f" Available persisted artifacts include: {', '.join(available[-10:])}."
            if available
            else ""
        )
        return (
            f"The requested root POM is not available yet ({reason}). "
            "That missing artifact does not by itself mean the migration is blocked."
            f"{available_note}"
        )

    if assistant_intent == "artifact_content":
        available = [
            _bounded_text(kind, 120)
            for kind in current_state.get("artifacts", {}).get("kinds", [])
            if _bounded_text(kind, 120)
        ]
        if available:
            return f"Available persisted artifact kinds: {', '.join(available[-20:])}."
        return "No persisted artifact kinds are recorded for the current migration yet."

    if assistant_intent == "stage3_dependency_review":
        return _dependency_review_fallback(
            question=question,
            current_state=current_state,
        )

    if assistant_intent in {"pom_change_proposal", "pom_dependency_change_request"}:
        return _pom_proposal_fallback(
            question=question,
            current_state=current_state,
        )

    if assistant_intent in _ACTION_INTENTS:
        gate = current_state.get("open_gate")
        if isinstance(gate, Mapping):
            decision = (
                f"The migration is {state} at the {gate.get('gate_phase') or 'current'} gate. "
                "Chat is read-only and did not execute that request; use the explicit "
                "Decisions controls or the dedicated API action."
            )
        else:
            decision = (
                f"The migration is {state}. Chat is read-only and did not execute that "
                "request; use the dedicated UI or API control for state-changing actions."
            )
        return decision

    if current_state.get("approval_required_now"):
        gate = current_state.get("open_gate")
        phase = gate.get("gate_phase") if isinstance(gate, Mapping) else "approval"
        return (
            f"The migration is awaiting an explicit {phase} decision. "
            "Review the current evidence and use the Decisions controls; chat did not take action."
        )
    if latest_summary:
        return f"The migration is currently {state}. Most recently: {latest_summary}"
    return f"The migration is currently {state}; no newer operational event is recorded."


def _build_focus_fallback(
    *,
    focus: str,
    style: str,
    current_state: Mapping[str, Any],
) -> str | None:
    snapshot = current_state.get("migration_snapshot", {})
    if not isinstance(snapshot, Mapping):
        snapshot = {}
    active = snapshot.get("active_route_step")
    active_stage = _as_int(active.get("stage_index")) if isinstance(active, Mapping) else 0
    active_number = _as_int(active.get("route_step_index")) if isinstance(active, Mapping) else active_stage
    if not active_number:
        active_number = _as_int(snapshot.get("active_stage_index"))
    completed = _as_int(snapshot.get("completed_route_steps"))
    total = _as_int(snapshot.get("total_route_steps"))
    phase = _text(snapshot.get("active_phase", "")).replace("_", " ") or "work"
    next_milestone = _text(snapshot.get("immediate_next_expected_backend_milestone", ""))
    latest_result = snapshot.get("latest_meaningful_result")
    latest_result_text = (
        _bounded_text(latest_result.get("message", ""), 240)
        if isinstance(latest_result, Mapping)
        else ""
    )
    blocker = _text(snapshot.get("current_blocker", ""))
    approval = bool(current_state.get("approval_required_now"))
    risk = snapshot.get("current_highest_supported_risk")
    risk_text = _bounded_text(risk.get("summary", ""), 240) if isinstance(risk, Mapping) else ""

    if focus == "executive_status":
        progress = (
            f"Stage {completed} of {total} is complete"
            if total
            else "The migration route is in progress"
        )
        activity = (
            f"Stage {active_number} {phase} is running"
            if active_number
            else f"the current {phase} is active"
        )
        blocking = blocker or "nothing is currently blocked"
        decision = (
            "an approval decision is required"
            if approval
            else "no approval is currently required"
        )
        result = f"; the latest supported result is {latest_result_text}" if latest_result_text else ""
        milestone = f"; the next milestone is {next_milestone}" if next_milestone else ""
        risk_note = f"; the highest supported risk is {risk_text}" if risk_text else ""
        return _one_sentence(f"{progress} and {activity}; {blocking}, and {decision}{result}{milestone}{risk_note}")

    if focus == "current_activity":
        overall = _text(current_state.get("overall_state", "pending"))
        if active_number:
            profile = ""
            if isinstance(active, Mapping):
                source = _text(active.get("source_profile", ""))
                target = _text(active.get("target_profile", ""))
                if source and target:
                    profile = f" from {source} to {target}"
            if phase == "queued":
                answer = f"Stage {active_number} is queued{profile}"
            elif overall == "completed":
                answer = f"Stage {active_number} is complete; no migration phase is currently running"
            elif overall == "awaiting_approval":
                answer = f"Stage {active_number} is awaiting an approval decision"
            elif current_state.get("is_running"):
                answer = f"Stage {active_number} {phase} is happening right now{profile}"
            else:
                answer = f"Stage {active_number} is currently {overall}{profile}"
            if next_milestone:
                answer += f"; the immediate next backend milestone is {next_milestone}"
            return _one_sentence(answer)
        return _one_sentence(f"The migration is currently {_text(current_state.get('overall_state', 'pending'))}")

    if focus == "current_status":
        return _one_sentence(
            f"The migration is {_text(current_state.get('overall_state', 'pending'))}"
            + (f" in Stage {active_number} {phase}" if active_number else "")
            + (f"; next milestone: {next_milestone}" if next_milestone else "")
        )

    if focus == "current_blockers":
        if blocker:
            return _one_sentence(f"The current blocker is {blocker}")
        return "Nothing is currently blocked."

    if focus == "current_approval":
        if approval:
            gate = current_state.get("open_gate")
            gate_phase = _text(gate.get("gate_phase", "current gate")) if isinstance(gate, Mapping) else "current gate"
            return _one_sentence(f"An explicit decision is required for the {gate_phase}; chat remains read-only")
        return "No approval decision is currently pending."

    if focus == "approval_decision_brief":
        return _approval_decision_fallback(current_state)

    if focus == "latest_event":
        latest = snapshot.get("latest_operational_event")
        if isinstance(latest, Mapping):
            return _one_sentence(
                f"Most recently, Stage {_as_int(latest.get('stage_index'))} recorded {_bounded_text(latest.get('message') or latest.get('type'), 260)}"
            )
        return "No meaningful operational event is currently recorded."

    if focus == "recent_progress":
        transitions = snapshot.get("recent_meaningful_transitions", [])
        lines = [
            f"- Stage {_as_int(item.get('stage_index'))}: {_bounded_text(item.get('message') or item.get('type'), 240)}"
            for item in transitions[-5:]
            if isinstance(item, Mapping)
        ]
        if not lines:
            return "No recent meaningful progression is recorded."
        return "\n".join(lines) if style == "list" else " Recent progression: " + " ".join(line[2:] + "." for line in lines)

    if focus in {"gate_evidence_review", "artifact_review"}:
        return _artifact_review_fallback(current_state, gate_only=focus == "gate_evidence_review")

    if focus == "application_change_summary":
        return _application_change_fallback(current_state)

    if focus == "dependency_change_summary":
        return _dependency_change_fallback(current_state)

    if focus == "validation_summary":
        return _validation_fallback(snapshot)

    if focus == "risk_summary":
        if risk_text:
            return _one_sentence(f"The highest currently supported migration risk is {risk_text}")
        return "Current persisted evidence does not support a specific elevated migration risk."

    if focus == "failure_explanation":
        if blocker:
            return _one_sentence(f"The next stage has not started because {blocker}")
        if next_milestone:
            return _one_sentence(f"The next stage is not recorded as blocked; the current expected milestone is {next_milestone}")
        return "Current persisted evidence does not identify a blocker or a reason the next stage has not started."

    if focus == "evidence_support":
        return _evidence_support_fallback(current_state)

    return None


def _approval_decision_fallback(current_state: Mapping[str, Any]) -> str:
    gate = current_state.get("open_gate")
    if not isinstance(gate, Mapping):
        return "There is no open approval gate, so there is no current approval decision brief."
    stage = _as_int(gate.get("stage_index"))
    phase = _text(gate.get("gate_phase", "migration"))
    artifacts = [
        _bounded_text(kind, 120)
        for kind in gate.get("bound_artifact_kinds", [])
        if _text(kind)
    ]
    evidence = [item for item in gate.get("evidence", []) if isinstance(item, Mapping)]
    files = _extract_safe_file_labels(evidence)
    risk = current_state.get("migration_snapshot", {}).get("current_highest_supported_risk")
    risk_text = _bounded_text(risk.get("summary", ""), 240) if isinstance(risk, Mapping) else "No elevated risk is supported by the available bounded evidence."
    validation = current_state.get("migration_snapshot", {}).get("current_build_test_result", {})
    actions = [
        _bounded_text(item.get("label") or item.get("action"), 120)
        for item in gate.get("available_actions", [])
        if isinstance(item, Mapping) and not item.get("blocked")
    ]
    review_order = artifacts[:4] or [_bounded_text(item.get("kind", "evidence"), 120) for item in evidence[:4]]
    return "\n".join([
        f"- Approval: Stage {stage} {phase} evidence and its checksum-bound planned transition.",
        f"- Planned changes: {_evidence_content_summary(evidence) or 'Not available in the bounded gate evidence.'}",
        f"- Highest supported risk: {risk_text}",
        f"- Affected/high-risk files: {', '.join(files) if files else 'Not identified in available evidence.'}",
        f"- Completed validation: {_validation_text(validation)}",
        f"- Warnings/unresolved concerns: {risk_text}",
        f"- Evidence available: {', '.join(artifacts) if artifacts else 'No bound artifact labels are available.'}",
        f"- Decision options: {', '.join(actions) if actions else 'Use the explicit Decisions controls for the available gate actions.'}",
        f"- Recommended review order: {' → '.join(review_order) if review_order else 'Review the approval request, plan, diff/impact, dependencies, tests, then risks.'}",
    ])


def _artifact_review_fallback(current_state: Mapping[str, Any], *, gate_only: bool) -> str:
    gate = current_state.get("open_gate")
    if gate_only and isinstance(gate, Mapping):
        owned = [
            {"kind": kind, "stage_index": gate.get("stage_index")}
            for kind in gate.get("bound_artifact_kinds", [])
        ]
    else:
        owned = current_state.get("artifacts", {}).get("owned", [])
    lines = [
        f"- Stage {_as_int(item.get('stage_index'))}: {_bounded_text(item.get('kind'), 120)}"
        for item in owned
        if isinstance(item, Mapping) and _text(item.get("kind"))
    ]
    if lines:
        return "\n".join(lines)
    return "No persisted artifacts are available in the requested review scope."


def _application_change_fallback(current_state: Mapping[str, Any]) -> str:
    previews = current_state.get("artifacts", {}).get("previews", [])
    files = _extract_safe_file_labels(previews)
    summaries = [
        _bounded_text(item.get("preview", ""), 260)
        for item in previews
        if isinstance(item, Mapping)
        and any(term in _text(item.get("kind", "")).lower() for term in ("rewrite", "impact", "diff", "plan"))
        and _text(item.get("preview", "")).strip()
    ]
    if not files and not summaries:
        return "No bounded rewrite, SafeDiff, or application-change evidence is available for the current scope."
    lines = [
        f"- Affected files: {', '.join(files) if files else 'Not enumerated in available evidence.'}",
        f"- Observed transformation evidence: {summaries[0] if summaries else 'File-level evidence is available, but no bounded transformation summary was recorded.'}",
    ]
    return "\n".join(lines)


def _dependency_change_fallback(current_state: Mapping[str, Any]) -> str:
    previews = [
        item for item in current_state.get("artifacts", {}).get("previews", [])
        if isinstance(item, Mapping)
        and any(term in _text(item.get("kind", "")).lower() for term in ("dependency", "pom", "policy"))
    ]
    if not previews:
        return "No bounded dependency plan, dependency policy, or root POM evidence is available in the current scope."
    return "\n".join(
        f"- Stage {_as_int(item.get('stage_index'))}: {_bounded_text(item.get('kind'), 120)} — {_bounded_text(item.get('preview') or item.get('reason'), 360)}"
        for item in previews[:5]
    )


def _validation_fallback(snapshot: Mapping[str, Any]) -> str:
    validation = snapshot.get("current_build_test_result", {})
    if not isinstance(validation, Mapping):
        return "No current build or test result is available."
    stage = _as_int(validation.get("stage_index"))
    build = validation.get("build")
    test = validation.get("test")
    parts = [
        f"- Build: {_validation_item_text(build)}",
        f"- Tests: {_validation_item_text(test)}",
    ]
    failed = any(
        isinstance(item, Mapping) and "fail" in _text(item.get("status", "")).lower()
        for item in (build, test)
    )
    parts.append(f"- Failed: {'Yes' if failed else 'No failure is recorded in the available build/test evidence.'}")
    return f"Stage {stage} validation:\n" + "\n".join(parts)


def _validation_item_text(item: Any) -> str:
    if not isinstance(item, Mapping):
        return "not available"
    status = _bounded_text(item.get("status", "unknown"), 120)
    message = _bounded_text(item.get("message", ""), 220)
    return f"{status}{f' — {message}' if message else ''}"


def _validation_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "No build/test validation evidence is available."
    return f"Stage {_as_int(value.get('stage_index'))}: build {_validation_item_text(value.get('build'))}; tests {_validation_item_text(value.get('test'))}."


def _evidence_support_fallback(current_state: Mapping[str, Any]) -> str:
    snapshot = current_state.get("migration_snapshot", {})
    refs: list[str] = []
    for key in ("latest_meaningful_result", "latest_operational_event"):
        item = snapshot.get(key) if isinstance(snapshot, Mapping) else None
        if isinstance(item, Mapping):
            refs.append(f"Stage {_as_int(item.get('stage_index'))} {_bounded_text(item.get('type'), 120)}: {_bounded_text(item.get('message'), 220)}")
    for item in current_state.get("artifacts", {}).get("owned", [])[-5:]:
        if isinstance(item, Mapping):
            refs.append(f"Stage {_as_int(item.get('stage_index'))} artifact: {_bounded_text(item.get('kind'), 120)}")
    return "\n".join(f"- {ref}" for ref in refs) if refs else "No persisted evidence is available to support that conclusion."


def _extract_safe_file_labels(values: Sequence[Any]) -> list[str]:
    labels: list[str] = []
    for value in values:
        text = json.dumps(value, default=str) if isinstance(value, Mapping) else _text(value)
        for candidate in re.findall(r"(?<![A-Za-z]:)(?<!/)(?:[\w.-]+/)*[\w.-]+\.(?:java|xml|properties|yml|yaml|json|md)", text):
            label = candidate.replace("\\", "/").lstrip("/")
            if label and label not in labels and ".." not in label:
                labels.append(label)
    return labels[:12]


def _evidence_content_summary(evidence: Sequence[Mapping[str, Any]]) -> str:
    for item in evidence:
        content = _bounded_text(item.get("content", ""), 480)
        if content:
            return " ".join(content.split())
    return ""


def _one_sentence(text: str) -> str:
    cleaned = " ".join(_text(text).split()).strip().rstrip(".!?")
    cleaned = re.sub(r"[.!?]+\s+", "; ", cleaned)
    return f"{cleaned}."


def _shape_fallback(text: str, *, style: str) -> str:
    safe = _text(text).strip()
    if style == "one_sentence":
        return _one_sentence(safe)
    if style == "list":
        existing = [line.strip() for line in safe.splitlines() if line.strip()]
        if len(existing) >= 2 and all(line.startswith(("-", "*")) for line in existing):
            return "\n".join(existing)
        parts = [
            part.strip(" -.")
            for part in re.split(r"(?:[.!?]+\s+|;\s*)", " ".join(existing))
            if part.strip(" -.")
        ]
        if len(parts) < 2:
            parts.append("Scope: current persisted migration evidence")
        return "\n".join(f"- {part}" for part in parts[:8])
    if style in {"concise", "executive"} and len(safe) > 720:
        return _bounded_text(safe, 717).rstrip() + "..."
    return safe


class V2AssistantContextResolver:
    """Load current backend truth and close the read UoW before returning."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        job_loader: Callable[[Any, str], Any],
        pipeline_projector: Callable[[str, tuple[Any, ...]], dict[str, Any]],
        intent_classifier: Callable[[str], str],
        artifact_preview_resolver: Callable[..., list[dict[str, Any]]],
        conversation_history_builder: Callable[[tuple[Any, ...]], list[dict[str, str]]],
        model_context: Mapping[str, Any] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._job_loader = job_loader
        self._pipeline_projector = pipeline_projector
        self._intent_classifier = intent_classifier
        self._artifact_preview_resolver = artifact_preview_resolver
        self._conversation_history_builder = conversation_history_builder
        self._model_context = dict(model_context or {})

    def resolve(self, *, job_id: str, question: str) -> AssistantGroundingEnvelope:
        uow = self._unit_of_work_factory()
        if hasattr(uow, "transaction_mode"):
            uow.transaction_mode = "read"

        with uow as entered:
            job = self._job_loader(entered, job_id)
            events = tuple(sorted(entered.v2_events.list_by_job(job_id), key=_event_sequence))
            approvals = tuple(entered.v2_approvals.list_cards_by_job(job_id))
            commands = tuple(entered.v2_commands.list_by_job(job_id))
            pipeline = self._pipeline_projector(job_id, events)
            assistant_intent = self._intent_classifier(question.strip().lower())

            assistant_service = V2AssistantService(assistant_repo=entered.v2_assistant)
            prior_messages = assistant_service.get_messages(job_id)
            conversation_history = tuple(
                self._conversation_history_builder(prior_messages)
            )
            focus = resolve_request_focus(question, conversation_history)
            style = resolve_response_style(question)

            setup = (
                entered.v2_setups.get(job.setup_id)
                if getattr(job, "setup_id", "")
                else None
            )
            approval_focus = focus in {"approval_decision_brief", "gate_evidence_review"}
            needs_artifacts = (
                focus in _EVIDENCE_FOCUSES
                and not approval_focus
            ) or (not approval_focus and assistant_intent in {
                "pom_or_dependency_explanation",
                "artifact_content",
                "stage3_dependency_review",
                "pom_change_proposal",
                "pom_dependency_change_request",
            })
            artifact_previews = (
                tuple(
                    self._artifact_preview_resolver(
                        question=question,
                        events=events,
                        commands=commands,
                        setup=setup,
                        assistant_intent=assistant_intent,
                    )
                )
                if needs_artifacts
                else ()
            )

            phase_gates = getattr(entered, "phase_gates", None)
            open_gates = tuple(phase_gates.list_open(job_id)) if phase_gates is not None else ()
            if open_gates and focus == "general" and any(
                term in question.lower()
                for term in ("approv", "what will change", "decision", "gate", "review", "plan evidence")
            ):
                focus = "approval_decision_brief"
            gate_grounding = (
                _resolve_gate_grounding(
                    gate_repo=phase_gates,
                    open_gates=open_gates,
                    storage_root=getattr(setup, "output_parent_path", None),
                )
                if focus in {"approval_decision_brief", "gate_evidence_review"}
                else {}
            )

            repair_repo = getattr(entered, "v2_repairs", None)
            current_repair = (
                repair_repo.get_current_proposal_for_job(job_id)
                if repair_repo is not None
                and hasattr(repair_repo, "get_current_proposal_for_job")
                else None
            )
            run_config_repo = getattr(entered, "run_configurations", None)
            run_configuration = (
                run_config_repo.get_for_job(job_id)
                if run_config_repo is not None and hasattr(run_config_repo, "get_for_job")
                else None
            )
            current_state = build_current_state_snapshot(
                job=job,
                pipeline=pipeline,
                open_gates=open_gates,
                approvals=approvals,
                events=events,
                commands=commands,
                run_configuration=run_configuration,
                artifact_previews=artifact_previews,
                gate_grounding=gate_grounding,
                current_repair=current_repair,
            )

        # No repository record or live UoW escapes this point.
        prompt = build_assistant_prompt(
            question=question,
            assistant_intent=assistant_intent,
            focus=focus,
            style=style,
            current_state=current_state,
            user_context=conversation_history,
            model_context=self._model_context,
        )
        fallback = build_read_only_fallback(
            question=question,
            assistant_intent=assistant_intent,
            focus=focus,
            style=style,
            current_state=current_state,
            model_context=self._model_context,
        )
        # build_assistant_prompt redacts values before JSON serialization so
        # path redaction cannot corrupt JSON escaping.
        safe_prompt = _bounded_text(prompt, 16_000)
        safe_fallback = _bounded_text(
            _shape_fallback(redact_model_summary(fallback), style=style),
            16_000,
        )

        first_gate = open_gates[0] if open_gates else None
        open_gate_id = (
            _text(getattr(first_gate, "gate_id", "")) or None
            if first_gate is not None
            else None
        )
        return AssistantGroundingEnvelope(
            job_id=job_id,
            question=question,
            assistant_intent=assistant_intent,
            focus=focus,
            style=style,
            prompt=safe_prompt,
            fallback=safe_fallback,
            conversation_history=conversation_history,
            context_checksum=compute_content_checksum(safe_prompt),
            current_state=current_state,
            open_gate_id=open_gate_id,
        )


class V2AssistantConversationService:
    """Execute one grounded, read-only Assistant V2 exchange."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        context_resolver: V2AssistantContextResolver,
        model_client: Any,
        response_composer: V2AssistantResponseComposer,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._context_resolver = context_resolver
        self._model_client = model_client
        self._response_composer = response_composer

    def ask(
        self,
        *,
        job_id: str,
        question: str,
        correlation_id: str | None = None,
    ) -> V2AssistantConversationResult:
        envelope = self._context_resolver.resolve(job_id=job_id, question=question)

        started = perf_counter()
        model_result = self._invoke_model_once(envelope)
        latency_ms = max(0, int((perf_counter() - started) * 1000))
        model_result = self._validated_result(model_result, envelope)

        write_uow = self._unit_of_work_factory()
        if hasattr(write_uow, "transaction_mode"):
            write_uow.transaction_mode = "write"
        with write_uow as entered:
            assistant_service = V2AssistantService(assistant_repo=entered.v2_assistant)
            user_message = assistant_service.add_message(
                job_id=job_id,
                role="user",
                content=question,
                correlation_id=correlation_id,
            )
            assistant_message = assistant_service.add_message(
                job_id=job_id,
                role="assistant",
                content=model_result.content,
                correlation_id=user_message.message_id,
            )

            ledger = V2LLMInvocationLedger(entered.v2_llm_invocations)
            is_fallback = not model_result.success and model_result.source == "deterministic"
            invocation_id = ledger.start_invocation(
                job_id=job_id,
                role="fallback" if is_fallback else "main",
                responsibility="explanation",
                gate_id=envelope.open_gate_id,
                context_checksum=envelope.context_checksum,
                input_checksum=compute_content_checksum(question),
            )
            if model_result.success:
                ledger.complete_invocation(
                    invocation_id,
                    output=model_result.content,
                    redacted_summary=model_result.redacted_summary,
                    latency_ms=latency_ms,
                    fallback_used=False,
                )
            else:
                ledger.fail_invocation(
                    invocation_id,
                    redacted_error=model_result.failure_reason,
                    redacted_summary=model_result.redacted_summary,
                    latency_ms=latency_ms,
                    fallback_used=is_fallback,
                )

        return V2AssistantConversationResult(
            user_message=user_message,
            assistant_message=assistant_message,
            model_result=model_result,
            context_checksum=envelope.context_checksum,
            invocation_id=invocation_id,
            current_state=envelope.current_state,
        )

    def _invoke_model_once(self, envelope: AssistantGroundingEnvelope) -> V2AssistantModelResult:
        try:
            if hasattr(self._model_client, "answer_once"):
                return self._model_client.answer_once(
                    prompt=envelope.prompt,
                    fallback=envelope.fallback,
                    conversation_history=[],
                )
            if hasattr(self._model_client, "answer_with_role"):
                return self._model_client.answer_with_role(
                    role=V2ModelRole.ASSISTANT,
                    prompt=envelope.prompt,
                    fallback=envelope.fallback,
                    conversation_history=[],
                )
            return self._model_client.answer(
                prompt=envelope.prompt,
                fallback=envelope.fallback,
                conversation_history=[],
            )
        except Exception as exc:  # fail closed after the single attempted call
            return V2AssistantModelResult(
                content=envelope.fallback,
                source="deterministic",
                model_status="fallback",
                provider="deterministic",
                role="assistant",
                success=False,
                redacted_summary=(
                    "Assistant model invocation failed safely "
                    f"({type(exc).__name__})."
                ),
                failure_reason="model_exception",
            )

    def _validated_result(
        self,
        result: V2AssistantModelResult,
        envelope: AssistantGroundingEnvelope,
    ) -> V2AssistantModelResult:
        safe_content = _bounded_text(redact_model_summary(result.content or ""), 16_000)
        if (
            not result.success
            and result.source == "deterministic"
            and envelope.assistant_intent != "model_status"
        ):
            # The shared model client appends provider diagnostics to fallback
            # text. Operational questions should receive only the grounded,
            # deterministic answer; provider state is available in response
            # metadata and is discussed only when explicitly requested.
            safe_content = envelope.fallback
        elif result.success:
            validated = _validate_model_answer(safe_content, envelope)
            if validated is None:
                return V2AssistantModelResult(
                    content=envelope.fallback,
                    source="deterministic",
                    model_status="fallback",
                    provider="deterministic",
                    role="assistant",
                    success=False,
                    redacted_summary="Assistant response validation failed safely.",
                    failure_reason="response_validation_failed",
                )
            safe_content = validated
        if not safe_content:
            safe_content = self._response_composer.render(
                AssistantResponseCard(
                    headline="Migration assistant",
                    status="warning",
                    summary=envelope.fallback,
                    safety_note=(
                        "This response is read-only. Any migration decision "
                        "must use an explicit backend-validated control."
                    ),
                )
            )
        return V2AssistantModelResult(
            content=safe_content[:16_000],
            source=result.source,
            model_status=result.model_status,
            provider=result.provider,
            role=result.role,
            success=result.success,
            redacted_summary=_bounded_text(
                redact_model_summary(result.redacted_summary or ""), 500
            ),
            failure_reason=_bounded_text(
                redact_model_summary(result.failure_reason or ""), 200
            ),
        )


def _validate_model_answer(
    content: str,
    envelope: AssistantGroundingEnvelope,
) -> str | None:
    """Validate focus, style, authority, and read-only safety without a retry."""

    answer = content.strip()
    structured: Mapping[str, Any] | None = None
    try:
        candidate = json.loads(answer)
        if isinstance(candidate, Mapping):
            structured = candidate
    except (json.JSONDecodeError, TypeError, ValueError):
        structured = None

    if structured is None:
        return None
    if _text(structured.get("focus", "")) != envelope.focus:
        return None
    if structured.get("requested_style_satisfied") is not True:
        return None
    answer = _bounded_text(structured.get("answer", ""), 16_000)
    if not answer:
        return None
    claims = structured.get("observed_claims", [])
    evidence_refs = structured.get("evidence_refs", [])
    if not isinstance(claims, list) or not isinstance(evidence_refs, list):
        return None
    uncertainty = _text(structured.get("uncertainty", "")).strip()
    if not claims and not uncertainty:
        return None
    if claims and not evidence_refs:
        return None
    try:
        prompt_data = json.loads(envelope.prompt)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    allowed_refs = set(prompt_data.get("answer_contract", {}).get("allowed_evidence_refs", []))
    if any(_text(ref) not in allowed_refs for ref in evidence_refs):
        return None
    if any(not _text(claim).strip() for claim in claims):
        return None

    lowered = answer.lower()
    if envelope.focus == "mutation_attempt":
        return None
    if any(term in lowered for term in _MONITORING_PROMISES):
        return None
    if envelope.assistant_intent != "model_status" and any(
        term in lowered for term in ("azure openai", "model provider", "deployment name")
    ):
        return None
    if not any(term in envelope.question.lower() for term in (" id", "identifier", "checksum")) and any(
        term in lowered for term in _INTERNAL_OUTPUT_TERMS
    ):
        return None
    if re.search(r"\byou should (?:run|execute|apply|approve|reject|resume|write|modify)\b", lowered):
        return None
    if re.search(r"\bi (?:have|will|can) (?:run|executed|applied|approved|rejected|resumed|written|modified)\b", lowered):
        return None

    state = envelope.current_state
    if not state.get("is_blocked") and envelope.focus in {
        "current_activity", "current_status", "current_blockers", "executive_status"
    } and re.search(r"\b(?:is|currently|still) (?:blocked|stuck)\b", lowered):
        return None
    if not state.get("approval_required_now") and envelope.focus in {
        "current_activity", "current_approval", "executive_status"
    } and re.search(r"\bapproval (?:is )?(?:required|pending)\b", lowered):
        return None
    if state.get("is_running") and envelope.focus == "current_activity" and re.search(
        r"\b(?:failed|completed|stopped)\b", lowered
    ):
        return None

    if envelope.style == "one_sentence" and _sentence_count(answer) != 1:
        return None
    if envelope.style == "list":
        useful_items = [
            line for line in answer.splitlines()
            if re.match(r"^\s*(?:[-*]|\d+[.)])\s+\S", line)
        ]
        if len(useful_items) < 2:
            return None
    return answer


def _sentence_count(text: str) -> int:
    normalized = " ".join(_text(text).split())
    endings = re.findall(r"[.!?]+(?:\s|$)", normalized)
    return len(endings) if endings else (1 if normalized else 0)


def _resolve_gate_grounding(
    *,
    gate_repo: Any,
    open_gates: Sequence[Any],
    storage_root: Any,
) -> dict[str, dict[str, Any]]:
    """Read bounded gate actions/evidence through existing read-only services."""

    if gate_repo is None or not open_gates:
        return {}
    try:
        from migration_factory.control_tower.application.v2_gate_artifact_resolver import (
            V2GateArtifactResolver,
        )
        from migration_factory.control_tower.application.v2_phase_gate_service import (
            V2PhaseGateService,
        )

        gate_service = V2PhaseGateService(gate_repo)
        artifact_resolver = V2GateArtifactResolver(
            gate_repo,
            storage_root=storage_root,
            max_content_chars=640,
        )
    except (AttributeError, TypeError, ValueError):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for gate in open_gates[:3]:
        gate_id = _text(getattr(gate, "gate_id", ""))
        if not gate_id:
            continue
        try:
            actions = gate_service.get_available_actions(gate_id)
        except (AttributeError, TypeError, ValueError):
            actions = ()
        try:
            evidence = artifact_resolver.resolve_gate_artifacts(gate_id)
        except (AttributeError, OSError, TypeError, ValueError):
            evidence = None

        result[gate_id] = {
            "available_actions": [
                {
                    "action": _bounded_text(getattr(action, "action", ""), 80),
                    "label": _bounded_text(getattr(action, "label", ""), 120),
                    "description": _bounded_text(getattr(action, "description", ""), 240),
                    "blocked": bool(getattr(action, "blocked", False)),
                    "block_reason": _bounded_text(getattr(action, "block_reason", ""), 240),
                }
                for action in actions
            ][:8],
            "evidence": [
                {
                    "kind": _bounded_text(getattr(artifact, "kind", ""), 120),
                    "stage_index": getattr(gate, "stage_index", None),
                    "checksum_verified": bool(getattr(artifact, "checksum_verified", False)),
                    "content": _bounded_text(getattr(artifact, "content", ""), 640),
                    "truncated": bool(getattr(artifact, "truncated", False)),
                }
                for artifact in _select_gate_evidence(
                    getattr(evidence, "artifacts", ()),
                    limit=8,
                )
            ]
            if evidence is not None
            else [],
            "evidence_status": _bounded_text(
                getattr(evidence, "failure_message", "") if evidence is not None else "",
                240,
            ),
        }
    return result


def _select_gate_evidence(artifacts: Sequence[Any], *, limit: int) -> list[Any]:
    """Keep a representative decision set instead of the first N filenames."""

    priorities = (
        ("approval",),
        ("analysis",),
        ("assessment",),
        ("migration_plan", "plan_summary"),
        ("rewrite", "diff", "impact"),
        ("dependency",),
        ("test", "validation"),
        ("risk", "warning"),
    )
    remaining = list(artifacts)
    selected: list[Any] = []
    for terms in priorities:
        match = next(
            (
                artifact
                for artifact in remaining
                if any(term in _text(getattr(artifact, "kind", "")).lower() for term in terms)
            ),
            None,
        )
        if match is not None:
            selected.append(match)
            remaining.remove(match)
    selected.extend(remaining[: max(0, limit - len(selected))])
    return selected[:limit]


def _safe_gate_snapshot(gate: Any, grounding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "gate_phase": _bounded_text(getattr(gate, "gate_phase", ""), 80),
        "stage_index": getattr(gate, "stage_index", None),
        "gate_status": "open",
        "decision_required": True,
        "source_artifact_checksum": _bounded_text(
            getattr(gate, "source_artifact_checksum", ""), 128
        ),
        "bound_artifact_kinds": _bound_artifact_labels(gate),
        "available_actions": list(grounding.get("available_actions", []))[:8],
        "evidence": list(grounding.get("evidence", []))[:8],
        "evidence_status": _bounded_text(grounding.get("evidence_status", ""), 240),
        "chat_can_execute": False,
        "action_surface": "Use the explicit Decisions controls or dedicated API endpoint.",
    }


def _minimal_gate_snapshot(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "gate_phase": _bounded_text(value.get("gate_phase", ""), 80),
        "stage_index": value.get("stage_index"),
        "gate_status": "open",
        "decision_required": True,
        "bound_artifact_kinds": list(value.get("bound_artifact_kinds", []))[:8],
        "available_actions": [
            {
                "action": _bounded_text(item.get("action", ""), 80),
                "label": _bounded_text(item.get("label", ""), 120),
                "blocked": bool(item.get("blocked", False)),
            }
            for item in list(value.get("available_actions", []))[:4]
            if isinstance(item, Mapping)
        ],
        "chat_can_execute": False,
        "action_surface": "Use the explicit Decisions controls or dedicated API endpoint.",
    }


def _bound_artifact_labels(gate: Any) -> list[str]:
    raw = getattr(gate, "source_artifact_refs_json", "[]") or "[]"
    try:
        refs = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(refs, list):
        return []
    labels: list[str] = []
    for ref in refs:
        if isinstance(ref, Mapping):
            candidate = ref.get("kind") or ref.get("artifact_kind") or ref.get("file_alias")
        else:
            candidate = ref
        text = _bounded_text(candidate, 240).replace("\\", "/").rstrip("/")
        if not text:
            continue
        label = text.rsplit("/", 1)[-1]
        if ":" in label and "." not in label:
            label = label.split(":", 1)[0]
        label = _bounded_text(label, 120)
        if label and label not in labels:
            labels.append(label)
    return labels[:12]


def _safe_pipeline_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "key": _bounded_text(row.get("key", ""), 80),
        "label": _bounded_text(row.get("label", ""), 120),
        "status": _bounded_text(row.get("status", ""), 40).lower(),
        "latest_message": _bounded_text(row.get("latest_message", ""), 240),
        "artifact_count": _as_int(row.get("artifact_count", 0)),
    }


def _safe_approval(card: Any) -> dict[str, Any]:
    return {
        "stage_index": getattr(card, "stage_index", None),
        "status": _bounded_text(getattr(card, "status", ""), 40).lower(),
        "summary": _bounded_text(getattr(card, "summary", ""), 240),
        "request_checksum": _bounded_text(getattr(card, "request_checksum", ""), 128),
    }


def _safe_repair_projection(repair: Any | None) -> dict[str, Any] | None:
    if repair is None:
        return None
    status = _bounded_text(getattr(repair, "status", ""), 80).lower()
    return {
        "status": status,
        "status_reason": _bounded_text(getattr(repair, "status_reason", ""), 240),
        "failure_summary": _bounded_text(getattr(repair, "failure_summary", ""), 240),
        "hypothesis": _bounded_text(getattr(repair, "hypothesis", ""), 240),
        "patch_summary": _bounded_text(getattr(repair, "patch_summary", ""), 240),
        "stage_index": getattr(repair, "route_step_index", None),
        "attempt_number": getattr(repair, "attempt_number", None),
        "apply_status": _bounded_text(getattr(repair, "apply_status", ""), 80),
        "rerun_status": _bounded_text(getattr(repair, "rerun_status", ""), 80),
        "rollback_status": _bounded_text(getattr(repair, "rollback_status", ""), 80),
        "remaining_attempts": getattr(repair, "remaining_attempts", None),
        "unresolved": status in {
            "user_review_required",
            "reviewer_accepted",
            "diff_materialized",
            "pending",
            "failed",
            "blocked",
        },
    }


def _safe_artifact_preview(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": _bounded_text(item.get("artifact_kind") or item.get("kind") or "", 120),
        "source_type": _bounded_text(item.get("source_type", ""), 80),
        "file_alias": _bounded_text(item.get("file_alias", ""), 120),
        "stage_index": item.get("stage_index"),
        "exists": bool(item.get("exists", False)),
        "reason": _bounded_text(item.get("reason", ""), 160),
        "preview": _bounded_text(item.get("preview", ""), 2_500),
        "truncated": bool(item.get("truncated", False)),
        "download_url": _bounded_text(item.get("download_url", ""), 240),
    }


def _safe_event(event: Any) -> dict[str, Any]:
    payload = _event_payload(event)
    safe_payload: dict[str, Any] = {}
    for key in (
        "artifact_kind",
        "build_status",
        "test_status",
        "final_status",
        "result_kind",
        "repair_loop_status",
        "gate_phase",
        "decision",
        "reason",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            safe_payload[key] = _bounded_text(value, 240)
    return {
        "sequence": _event_sequence(event),
        "stage_index": getattr(event, "stage", None),
        "type": _bounded_text(getattr(event, "type", ""), 120),
        "status": _bounded_text(getattr(event, "status", ""), 40).lower(),
        "message": _bounded_text(getattr(event, "message", ""), 320),
        "details": safe_payload,
    }


def _event_payload(event: Any) -> dict[str, Any]:
    raw = getattr(event, "payload_json", "{}") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _event_sequence(event: Any) -> int:
    return _as_int(getattr(event, "sequence", 0))


def _is_operational_event(event: Any) -> bool:
    event_type = _text(getattr(event, "type", "")).lower()
    if event_type in _RAW_EVENT_TYPES or event_type in _MODEL_TELEMETRY_TYPES:
        return False
    if event_type.startswith(("model_invocation_", "assistant_model_invocation_")):
        return False
    return True


def _is_block_event(event: Any) -> bool:
    event_type = _text(getattr(event, "type", "")).lower()
    status = _text(getattr(event, "status", "")).lower()
    return status == "blocked" or event_type in _BLOCK_EVENT_TYPES


def _latest_failure_event(events: Iterable[Any]) -> Any | None:
    candidates = [
        event
        for event in events
        if (
            _text(getattr(event, "status", "")).lower() == "failed"
            or _text(getattr(event, "type", "")).lower().endswith("_failed")
        )
        and _text(getattr(event, "type", "")).lower() not in _MODEL_TELEMETRY_TYPES
    ]
    return max(candidates, key=_event_sequence) if candidates else None


def _failure_is_current(
    *,
    job: Any,
    latest_failure: Any | None,
    operational_events: Sequence[Any],
) -> bool:
    if _text(getattr(job, "status", "")).lower() == "failed":
        return True
    if latest_failure is None:
        return False
    failure_sequence = _event_sequence(latest_failure)
    failure_stage = getattr(latest_failure, "stage", None)
    for event in operational_events:
        if _event_sequence(event) <= failure_sequence:
            continue
        event_type = _text(getattr(event, "type", "")).lower()
        same_or_later_stage = (
            failure_stage is None
            or getattr(event, "stage", None) is None
            or _as_int(getattr(event, "stage", 0)) >= _as_int(failure_stage)
        )
        if same_or_later_stage and (
            event_type in _RECOVERY_EVENT_TYPES
            or (
                _text(getattr(event, "status", "")).lower() == "running"
                and event_type.endswith("_started")
            )
        ):
            return False
        if event_type == "repair_validation_completed" and bool(
            _event_payload(event).get("passed")
        ):
            return False
    return True


def _current_state_is_completed(
    job_status: str,
    rows: Sequence[Mapping[str, Any]],
    operational_events: Sequence[Any],
) -> bool:
    if job_status == "completed":
        return True
    if any(
        _text(getattr(event, "type", "")).lower() == "final_report_completed"
        for event in operational_events[-6:]
    ):
        return True
    final_report_rows = [
        row
        for row in rows
        if _text(row.get("key", "")).lower() == "final_report"
    ]
    return bool(
        final_report_rows
        and _text(final_report_rows[-1].get("status", "")).lower()
        in {"pass", "completed"}
    )


def _serialize_redacted(value: Mapping[str, Any]) -> str:
    safe_value = _redact_json_value(value)
    return json.dumps(safe_value, separators=(",", ":"), sort_keys=True, default=str)


def _redact_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redact_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_json_value(item) for item in value]
    if isinstance(value, str):
        return _text(redact_model_summary(value))
    return value


def _root_pom_preview(current_state: Mapping[str, Any]) -> Mapping[str, Any] | None:
    previews = current_state.get("artifacts", {}).get("previews", [])
    return next(
        (
            preview
            for preview in previews
            if isinstance(preview, Mapping)
            and _text(preview.get("kind", "")).lower() == "root_pom"
        ),
        None,
    )


def _dependency_review_fallback(
    *,
    question: str,
    current_state: Mapping[str, Any],
) -> str:
    """Build a bounded evidence summary without proposing or applying edits."""

    stage = 3
    lowered = question.lower()
    for candidate in (1, 2, 3):
        if f"stage {candidate}" in lowered or f"stage{candidate}" in lowered:
            stage = candidate
            break
    if stage < 3:
        return (
            f"Stage {stage} is transitional and not at the final target baseline. "
            "Final app-specific dependency recommendations should wait for Stage 3; "
            "no dependency change was applied."
        )

    latest_event = current_state.get("latest_operational_event")
    if (
        current_state.get("is_running")
        and isinstance(latest_event, Mapping)
        and _as_int(latest_event.get("stage_index")) == 3
        and _text(latest_event.get("type", "")).lower().endswith("_started")
    ):
        return (
            "Stage 3 is still running, so the root POM is not yet a stable completed "
            "baseline and final dependency recommendations cannot be confirmed. "
            "No dependency change was applied."
        )

    root_pom = _root_pom_preview(current_state)
    if not isinstance(root_pom, Mapping) or not root_pom.get("exists"):
        reason = (
            _bounded_text(root_pom.get("reason", ""), 120)
            if isinstance(root_pom, Mapping)
            else "root_pom_unavailable"
        )
        return (
            f"The Stage 3 dependency review is not ready ({reason}): the target baseline "
            "cannot be confirmed because no stable current root POM preview is available. "
            "No versions were guessed and no change was applied."
        )
    preview = _bounded_text(root_pom.get("preview", ""), 2_500)
    java_match = _search_xml_value(preview, "java.version")
    boot_match = (
        _search_xml_value(preview, "spring-boot.version")
        or _search_xml_value(preview, "version", parent_hint="spring-boot")
    )
    if not java_match or not boot_match:
        baseline = (
            "The Java/Spring Boot target baseline cannot be confirmed from the current "
            "root POM, so no target version is guessed."
        )
    else:
        baseline = (
            f"Detected target baseline from the root POM: Java {java_match}, "
            f"Spring Boot {boot_match}."
        )
    javax_note = (
        " Remaining javax.* coordinates need evidence-backed review before replacing "
        "javax dependencies with their Jakarta equivalents."
        if "javax." in preview.lower()
        else " No remaining javax.* coordinate appears in the bounded preview."
    )
    coordinates = _dependency_coordinates(preview)
    policy_candidates = [
        coordinate.split(":", 1)[-1]
        for coordinate in coordinates
        if not coordinate.startswith(("org.springframework.boot:", "javax."))
    ][:8]
    candidate_note = (
        f" Policy candidates visible in the preview: {', '.join(policy_candidates)}."
        if policy_candidates
        else ""
    )
    coordinate_note = (
        f" Coordinates visible in the preview: {', '.join(coordinates[:8])}."
        if coordinates
        else ""
    )
    return (
        f"{baseline}\n\n"
        "Review these non-executing dependency buckets: Boot-Managed, Jakarta/platform, "
        "App-Specific third-party, Build Plugins, and Transitive/BOM-Managed risk."
        f"{javax_note}{coordinate_note}{candidate_note} "
        "App-specific versions remain human policy decisions. Not applied: no file was written."
    )


def _pom_proposal_fallback(
    *,
    question: str,
    current_state: Mapping[str, Any],
) -> str:
    root_pom = _root_pom_preview(current_state)
    evidence = (
        "the current root POM preview"
        if isinstance(root_pom, Mapping) and root_pom.get("exists")
        else "current persisted migration evidence (the root POM is not available)"
    )
    preview = (
        _bounded_text(root_pom.get("preview", ""), 2_500)
        if isinstance(root_pom, Mapping)
        else ""
    )
    javax_coordinates = [
        coordinate
        for coordinate in _dependency_coordinates(preview)
        if coordinate.startswith("javax.")
    ]
    evidence_note = (
        f" Current Jakarta-review candidates: {', '.join(javax_coordinates)}."
        if javax_coordinates
        else ""
    )
    return (
        f"Draft recommendation only, based on {evidence}: {question.strip()} "
        "Review the exact before/after XML, compatibility risk, and target-version evidence "
        f"in the dedicated POM/Decisions controls.{evidence_note} Not applied: "
        "no file was written and no command was executed."
    )


def _search_xml_value(
    text: str,
    tag: str,
    *,
    parent_hint: str = "",
) -> str:
    import re

    if parent_hint and parent_hint not in text.lower():
        return ""
    match = re.search(
        rf"<{re.escape(tag)}>([^<]{{1,80}})</{re.escape(tag)}>",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _dependency_coordinates(text: str) -> list[str]:
    import re

    coordinates: list[str] = []
    for block in re.findall(
        r"<dependency>(.*?)</dependency>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        group = _search_xml_value(block, "groupId")
        artifact = _search_xml_value(block, "artifactId")
        if group and artifact:
            coordinate = f"{group}:{artifact}"
            if coordinate not in coordinates:
                coordinates.append(coordinate)
    return coordinates


def _bounded_text(value: Any, limit: int) -> str:
    return _text(value).strip()[:limit]


def _text(value: Any) -> str:
    return str(value or "")


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
