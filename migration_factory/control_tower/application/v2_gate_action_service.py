"""F15 V2 Gate Action Service — centralized state-changing gate actions.

All gate actions flow through this service: continue, reanalyze, revise,
approve, reject. The service validates checksums, enforces phase-valid
decisions, records audit entries, and queues backend commands.

No command launch happens in the skeleton — the service returns action
results that callers use to queue work via existing services.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from migration_factory.control_tower.application.v2_phase_gate_service import (
    CreateGateRequest,
    ResolveGateRequest,
    ResolveGateResult,
    V2PhaseGateService,
)
from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.domain.entities import (
    ArtifactRevisionRecord,
    GateDecisionRecord,
    PhaseGateRecord,
)
from migration_factory.control_tower.domain.gate_checksum import (
    GateChecksumMismatchError,
    gate_checksum,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.application.v2_repair_flow import (
    V2RepairFlowService,
)
from migration_factory.control_tower.domain.commands import (
    NONTERMINAL_COMMAND_STATES,
)
from migration_factory.control_tower.schemas.phase_gate import (
    GateActorType,
    GateDecision,
    GatePhase,
    HUMAN_AUTHORITATIVE_ACTIONS,
    is_valid_decision_for_phase,
)
from migration_factory.control_tower.schemas.source_profile_override import (
    SourceProfileOverrideRequest,
)


# ── result types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateActionResult:
    """Result of a gate action execution.

    status values:
      - 'executed': action was applied, gate resolved, result refs set
      - 'stale_checksum': gate checksum mismatch, action rejected
      - 'invalid_decision': decision not valid for this gate phase
      - 'gate_not_open': gate is resolved/superseded, action rejected
      - 'gate_not_found': gate_id does not exist
      - 'idempotent': duplicate decision detected, existing result returned
    """

    action: str
    gate_id: str
    decision_id: str
    status: str
    result_gate_id: str | None = None
    result_command_id: str | None = None
    result_revision_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class RepairApprovalChecksumBinding:
    """Checksum set required to approve a reviewed F5 repair gate."""

    proposal_checksum: str
    context_pack_checksum: str
    reviewer_output_checksum: str
    final_reviewed_diff_checksum: str
    policy_validation_checksum: str
    base_repo_state_checksum: str
    final_reviewed_artifact_checksum: str = ""
    repair_revision_id: str = ""
    repair_revision_checksum: str = ""


def persist_approved_approval_review_revision(
    *,
    gate: PhaseGateRecord,
    revision_repo: SqliteArtifactRevisionRepository | None,
    decided_by: str,
    profile_metadata: dict[str, Any] | None = None,
) -> str | None:
    """Persist the backend-owned accepted evidence for an approval_review gate.

    The approval checkpoint validator expects an accepted revision whose
    evidence checksum matches the approval gate's source checksum.
    """
    if revision_repo is None:
        return None
    if gate.gate_phase != GatePhase.APPROVAL_REVIEW.value:
        return None

    accepted = revision_repo.find_accepted(
        gate.job_id,
        gate.stage_index,
        "approval_review",
    )
    if accepted is not None:
        if accepted.evidence_checksum != gate.source_artifact_checksum:
            raise ValueError(
                "Approval review gate already has an accepted revision with a different checksum."
            )
        if _artifact_refs_include_profile_metadata(accepted.artifact_refs_json):
            return accepted.revision_id
        if profile_metadata is None:
            return accepted.revision_id
        latest = accepted
    else:
        latest = revision_repo.find_latest_by_kind(
            gate.job_id,
            gate.stage_index,
            "approval_review",
        )

    artifact_refs = _approval_review_artifact_refs(
        gate.source_artifact_refs_json,
        profile_metadata=profile_metadata,
    )
    if accepted is not None and latest is accepted:
        latest = accepted
    now = utc_now_text()
    revision_id = uuid4().hex
    revision_repo.save(
        ArtifactRevisionRecord(
            revision_id=revision_id,
            job_id=gate.job_id,
            stage_index=gate.stage_index,
            revision_kind="approval_review",
            revision_status="accepted",
            revision_order=(latest.revision_order + 1) if latest else 1,
            evidence_checksum=gate.source_artifact_checksum,
            prior_revision_checksum=latest.evidence_checksum if latest else None,
            artifact_refs_json=json.dumps(
                artifact_refs,
                separators=(",", ":"),
                sort_keys=True,
            ),
            prior_revision_id=latest.revision_id if latest else None,
            superseded_by_revision_id=None,
            accepted_at_gate_id=gate.gate_id,
            created_at=now,
            created_by=decided_by,
            accepted_at=now,
            accepted_by=decided_by,
        )
    )
    return revision_id


def _approval_review_artifact_refs(
    raw_refs_json: str,
    *,
    profile_metadata: dict[str, Any] | None = None,
) -> list[Any]:
    try:
        parsed = json.loads(raw_refs_json or "[]")
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = []

    if isinstance(parsed, list):
        refs: list[Any] = list(parsed)
    elif isinstance(parsed, dict):
        refs = [parsed]
    else:
        refs = []

    if profile_metadata is None:
        return refs

    profile_ref = _approval_review_profile_metadata_ref(profile_metadata)
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            continue
        nested = ref.get("profile_metadata")
        if isinstance(nested, dict):
            refs[index] = {
                **ref,
                "profile_metadata": {
                    **nested,
                    **profile_metadata,
                },
            }
            return refs

    refs.append(profile_ref)
    return refs


def _approval_review_profile_metadata_ref(profile_metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "profile_route",
        "path_or_ref": "metadata:profile-route",
        "checksum": str(profile_metadata.get("route_checksum") or ""),
        "profile_metadata": dict(profile_metadata),
    }


def _artifact_refs_include_profile_metadata(raw_refs_json: str) -> bool:
    try:
        parsed = json.loads(raw_refs_json or "[]")
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return False
    for ref in parsed:
        if isinstance(ref, dict) and isinstance(ref.get("profile_metadata"), dict):
            return True
    return False


# ── service ──────────────────────────────────────────────────────────


class V2GateActionService:
    """Centralized service for F15 gate actions.

    All state-changing gate operations go through this service.
    No command launch — the caller uses result references to queue
    work via existing orchestrator/runner services.
    """

    def __init__(
        self,
        gate_repo: SqlitePhaseGateRepository,
        decision_repo: SqliteGateDecisionRepository,
        gate_service: V2PhaseGateService | None = None,
        revision_repo: SqliteArtifactRevisionRepository | None = None,
        repair_service: V2RepairFlowService | None = None,
        command_repo: SqliteV2CommandRepository | None = None,
    ) -> None:
        self._gate_repo = gate_repo
        self._decision_repo = decision_repo
        self._gate_service = gate_service or V2PhaseGateService(gate_repo)
        self._revision_repo = revision_repo
        self._repair_service = repair_service
        self._command_repo = command_repo

    # ── action: continue ────────────────────────────────────────────

    def continue_from_gate(
        self,
        *,
        gate_id: str,
        job_id: str,
        decided_by: str,
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
    ) -> GateActionResult:
        """Validate and execute a 'continue' decision at a gate.

        Requires:
        - gate exists and is OPEN
        - gate phase allows CONTINUE decision
        - gate checksum must match (validated inside resolve)
        - For analysis_review phase: an accepted analysis revision must
          exist for the stage (proves analysis was accepted before
          planning can proceed)
        - For planning_review phase: an accepted plan revision must
          exist for the stage (proves plan was accepted before
          approval/proceed)

        Args:
            expected_gate_checksum: Optional caller-supplied checksum
                for stale protection. If provided, compared against
                the current gate checksum before resolution.

        Returns:
            GateActionResult with status and optional queued references.
        """
        # Pre-validation: check for required accepted revisions
        gate = self._gate_repo.get(gate_id)
        if gate is not None and self._revision_repo is not None:
            try:
                gate_phase_val = GatePhase(gate.gate_phase)
            except ValueError:
                gate_phase_val = None

            if gate_phase_val == GatePhase.ANALYSIS_REVIEW:
                # For analysis_review, there must be an accepted analysis
                # revision (proving analysis was properly accepted)
                accepted = self._revision_repo.find_accepted(
                    gate.job_id, gate.stage_index, "analysis"
                )
                # If no accepted analysis revision exists and this is
                # the first acceptance, we still allow it. The guard
                # only blocks when there IS a revision but it isn't
                # accepted (e.g., after a reanalysis that wasn't accepted).
                all_analysis = [
                    r for r in self._revision_repo.list_by_job_and_stage(
                        gate.job_id, gate.stage_index
                    )
                    if r.revision_kind == "analysis"
                ]
                if all_analysis and accepted is None:
                    # There are analysis revisions but none accepted
                    return GateActionResult(
                        action=GateDecision.CONTINUE.value,
                        gate_id=gate_id,
                        decision_id="",
                        status="no_accepted_analysis",
                        reason=(
                            f"Analysis must be accepted before continuing "
                            f"from analysis_review gate. Found "
                            f"{len(all_analysis)} draft revision(s)"
                        ),
                    )

            elif gate_phase_val == GatePhase.PLANNING_REVIEW:
                # For planning_review, there must be an accepted plan
                accepted = self._revision_repo.find_accepted(
                    gate.job_id, gate.stage_index, "planning"
                )
                all_plans = [
                    r for r in self._revision_repo.list_by_job_and_stage(
                        gate.job_id, gate.stage_index
                    )
                    if r.revision_kind == "planning"
                ]
                if all_plans and accepted is None:
                    return GateActionResult(
                        action=GateDecision.CONTINUE.value,
                        gate_id=gate_id,
                        decision_id="",
                        status="no_accepted_plan",
                        reason=(
                            f"Plan must be accepted before continuing "
                            f"from planning_review gate. Found "
                            f"{len(all_plans)} draft revision(s)"
                        ),
                    )

        result = self._execute_action(
            gate_id=gate_id,
            job_id=job_id,
            action=GateDecision.CONTINUE,
            decided_by=decided_by,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            actor_type=GateActorType.HUMAN.value,
        )
        if (
            result.status == "executed"
            and gate is not None
            and self._revision_repo is not None
            and gate_phase_val in (GatePhase.ANALYSIS_REVIEW, GatePhase.PLANNING_REVIEW)
        ):
            revision_kind = (
                "analysis"
                if gate_phase_val == GatePhase.ANALYSIS_REVIEW
                else "planning"
            )
            if self._revision_repo.find_accepted(
                gate.job_id,
                gate.stage_index,
                revision_kind,
            ) is None:
                latest = self._revision_repo.find_latest_by_kind(
                    gate.job_id,
                    gate.stage_index,
                    revision_kind,
                )
                now = utc_now_text()
                revision_id = uuid4().hex
                self._revision_repo.save(
                    ArtifactRevisionRecord(
                        revision_id=revision_id,
                        job_id=gate.job_id,
                        stage_index=gate.stage_index,
                        revision_kind=revision_kind,
                        revision_status="accepted",
                        revision_order=(latest.revision_order + 1) if latest else 1,
                        evidence_checksum=gate.source_artifact_checksum,
                        prior_revision_checksum=latest.evidence_checksum if latest else None,
                        artifact_refs_json=gate.source_artifact_refs_json,
                        prior_revision_id=latest.revision_id if latest else None,
                        superseded_by_revision_id=None,
                        accepted_at_gate_id=gate.gate_id,
                        created_at=now,
                        created_by=decided_by,
                        accepted_at=now,
                        accepted_by=decided_by,
                    )
                )
                return GateActionResult(
                    action=result.action,
                    gate_id=result.gate_id,
                    decision_id=result.decision_id,
                    status=result.status,
                    result_gate_id=result.result_gate_id,
                    result_command_id=result.result_command_id,
                    result_revision_id=revision_id,
                    reason=result.reason,
                )
        return result

    # ── action: reanalyze ───────────────────────────────────────────

    def reanalyze_from_gate(
        self,
        *,
        gate_id: str,
        job_id: str,
        decided_by: str,
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
    ) -> GateActionResult:
        """Validate and execute a 'reanalyze' decision.

        Creates a new open analysis_review gate after resolving the
        current one (the new gate awaits updated analysis evidence).
        """
        return self._execute_action(
            gate_id=gate_id,
            job_id=job_id,
            action=GateDecision.REANALYZE,
            decided_by=decided_by,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            actor_type=GateActorType.HUMAN.value,
        )

    # ── action: request_reanalysis (with user feedback) ─────────────

    def request_reanalysis(
        self,
        *,
        gate_id: str,
        job_id: str,
        decided_by: str,
        user_feedback: str = "",
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
        ) -> GateActionResult:
        """Request reanalysis at an analysis_review gate.

        Unlike the generic ``reanalyze_from_gate``, this method:
          1. Only accepts analysis_review gates.
          2. Creates a new ArtifactRevision (kind=analysis, status=draft)
             with user feedback explaining why reanalysis is needed.
          3. Opens a fresh analysis_review gate.

        Downstream blocking (e.g. superseding planning) is handled at
        the service layer via revision lineage, not by DB-level UPDATE.

        No source writes occur.  The chatbot may explain the feedback
        to the backend for context, but the backend owns the revision.
        """
        request_checksum = sha256_canonical_json(
            {
                "gate_id": gate_id,
                "job_id": job_id,
                "action": GateDecision.REANALYZE.value,
                "decided_by": decided_by,
                "user_feedback": user_feedback,
                "expected_gate_checksum": expected_gate_checksum,
                "idempotency_key": idempotency_key,
            }
        )
        base_result = self._execute_action(
            gate_id=gate_id,
            job_id=job_id,
            action=GateDecision.REANALYZE,
            decided_by=decided_by,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            actor_type=GateActorType.HUMAN.value,
            request_checksum=request_checksum,
        )
        if base_result.status not in ("executed", "idempotent"):
            return base_result

        # Determine stage_index from the gate
        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return base_result  # unlikely, already resolved

        stage_index = gate.stage_index
        import json
        now = utc_now_text()

        # Create a draft analysis revision with user feedback
        if self._revision_repo is not None and base_result.status == "executed":
            rev_id = uuid4().hex
            self._revision_repo.save(
                ArtifactRevisionRecord(
                    revision_id=rev_id,
                    job_id=job_id,
                    stage_index=stage_index,
                    revision_kind="analysis",
                    revision_status="draft",
                    revision_order=0,
                    evidence_checksum=gate.source_artifact_checksum,
                    prior_revision_checksum=None,
                    artifact_refs_json=json.dumps(
                        [user_feedback[:256]] if user_feedback else [],
                        separators=(",", ":"),
                    ),
                    prior_revision_id=None,
                    superseded_by_revision_id=None,
                    accepted_at_gate_id=base_result.result_gate_id,
                    created_at=now,
                    created_by=decided_by,
                    accepted_at=None,
                    accepted_by=None,
                )
            )

        return base_result

    # ── action: request_plan_revision (with user feedback) ─────────

    def request_plan_revision(
        self,
        *,
        gate_id: str,
        job_id: str,
        decided_by: str,
        user_feedback: str = "",
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
    ) -> GateActionResult:
        """Request a plan revision at a planning_review gate.

        Only works on planning_review gates.  The handler:
          1. Resolves the current planning_review gate with REVISE.
          2. Creates a new ArtifactRevision (kind=planning, status=draft)
             with user feedback for the plan amendment.
          3. Opens a new open planning_review gate.
          4. Binds to the currently accepted analysis revision for
             the same stage (if available).

        No source writes occur.  The chatbot explains the feedback;
        the backend creates the revision.
        """
        request_checksum = sha256_canonical_json(
            {
                "gate_id": gate_id,
                "job_id": job_id,
                "action": GateDecision.REVISE.value,
                "decided_by": decided_by,
                "user_feedback": user_feedback,
                "expected_gate_checksum": expected_gate_checksum,
                "idempotency_key": idempotency_key,
            }
        )
        base_result = self._execute_action(
            gate_id=gate_id,
            job_id=job_id,
            action=GateDecision.REVISE,
            decided_by=decided_by,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            actor_type=GateActorType.HUMAN.value,
            request_checksum=request_checksum,
        )
        if base_result.status not in ("executed", "idempotent"):
            return base_result

        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return base_result

        stage_index = gate.stage_index
        import json
        now = utc_now_text()

        if self._revision_repo is not None and base_result.status == "executed":
            # Find the accepted analysis revision for this stage
            accepted_analysis = self._revision_repo.find_accepted(
                job_id, stage_index, "analysis"
            )
            analysis_checksum = (
                accepted_analysis.evidence_checksum
                if accepted_analysis is not None
                else gate.source_artifact_checksum
            )

            rev_id = uuid4().hex
            self._revision_repo.save(
                ArtifactRevisionRecord(
                    revision_id=rev_id,
                    job_id=job_id,
                    stage_index=stage_index,
                    revision_kind="planning",
                    revision_status="draft",
                    revision_order=0,
                    evidence_checksum=analysis_checksum,
                    prior_revision_checksum=None,
                    artifact_refs_json=json.dumps(
                        [user_feedback[:256]] if user_feedback else [],
                        separators=(",", ":"),
                    ),
                    prior_revision_id=None,
                    superseded_by_revision_id=None,
                    accepted_at_gate_id=base_result.result_gate_id,
                    created_at=now,
                    created_by=decided_by,
                    accepted_at=None,
                    accepted_by=None,
                )
            )

        return base_result

    # ── action: request_repair_revision (job104) ─────────────────────

    def request_repair_revision(
        self,
        *,
        gate_id: str,
        job_id: str,
        decided_by: str,
        proposal_id: str,
        user_feedback: str = "",
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
        open_followup_gate: bool = True,
    ) -> GateActionResult:
        """Request a repair revision at a repair_review gate.

        Only works on repair_review gates.  The handler:
          1. Resolves the current repair_review gate with REVISE.
          2. Opens a new repair_review gate with user feedback context.
          3. The caller should create the revised repair proposal
             via V2RepairFlowService.create_revision_proposal().

        No source writes occur.  The chatbot explains the feedback;
        the backend creates the revision proposal.
        """
        request_checksum = sha256_canonical_json(
            {
                "gate_id": gate_id,
                "job_id": job_id,
                "action": GateDecision.REVISE.value,
                "decided_by": decided_by,
                "proposal_id": proposal_id,
                "user_feedback": user_feedback,
                "expected_gate_checksum": expected_gate_checksum,
                "idempotency_key": idempotency_key,
            }
        )
        base_result = self._execute_action(
            gate_id=gate_id,
            job_id=job_id,
            action=GateDecision.REVISE,
            decided_by=decided_by,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            request_checksum=request_checksum,
            open_followup_gate=open_followup_gate,
        )
        if base_result.status not in ("executed", "idempotent"):
            return base_result

        # Create a revision artifact with user feedback
        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return base_result

        stage_index = gate.stage_index
        import json
        now = utc_now_text()

        if self._revision_repo is not None and base_result.status == "executed":
            feedback_lines = [user_feedback[:256]] if user_feedback else []
            if proposal_id:
                feedback_lines.append(f"source_proposal:{proposal_id}")

            rev_id = uuid4().hex
            self._revision_repo.save(
                ArtifactRevisionRecord(
                    revision_id=rev_id,
                    job_id=job_id,
                    stage_index=stage_index,
                    revision_kind="repair",
                    revision_status="draft",
                    revision_order=0,
                    evidence_checksum=gate.source_artifact_checksum,
                    prior_revision_checksum=None,
                    artifact_refs_json=json.dumps(
                        feedback_lines,
                        separators=(",", ":"),
                    ),
                    prior_revision_id=None,
                    superseded_by_revision_id=None,
                    accepted_at_gate_id=base_result.result_gate_id,
                    created_at=now,
                    created_by=decided_by,
                    accepted_at=None,
                    accepted_by=None,
                )
            )

        # Store proposal_id in result_revision_id for caller context
        base_result = GateActionResult(
            action=base_result.action,
            gate_id=base_result.gate_id,
            decision_id=base_result.decision_id,
            status=base_result.status,
            result_gate_id=base_result.result_gate_id,
            result_command_id=base_result.result_command_id,
            result_revision_id=proposal_id or base_result.result_revision_id,
            reason=base_result.reason,
        )

        return base_result

    # ── action: approve ─────────────────────────────────────────────

    def approve_from_gate(
        self,
        *,
        gate_id: str,
        job_id: str | None = None,
        decided_by: str,
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
        actor_type: str = GateActorType.HUMAN.value,
        revision_requested_active: bool = False,
        profile_metadata: dict[str, Any] | None = None,
    ) -> GateActionResult:
        """Validate and execute an 'approve' decision."""
        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return GateActionResult(
                action=GateDecision.APPROVE.value,
                gate_id=gate_id,
                decision_id="",
                status="gate_not_found",
            )
        effective_job_id = job_id or gate.job_id
        if job_id is not None and job_id != gate.job_id:
            return GateActionResult(
                action=GateDecision.APPROVE.value,
                gate_id=gate_id,
                decision_id="",
                status="invalid_decision",
                reason="Gate job does not match the requested job.",
            )
        if revision_requested_active:
            return GateActionResult(
                action=GateDecision.APPROVE.value,
                gate_id=gate_id,
                decision_id="",
                status="approval_failed",
                reason=(
                    "A revision request is pending. Transform remains blocked. "
                    "Approval is disabled until the revision is resolved or new "
                    "evidence is generated."
                ),
            )
        result = self._execute_action(
            gate_id=gate_id,
            job_id=effective_job_id,
            action=GateDecision.APPROVE,
            decided_by=decided_by,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            actor_type=actor_type,
        )
        if result.status != "executed" or gate.gate_phase != GatePhase.APPROVAL_REVIEW.value:
            return result
        try:
            revision_id = persist_approved_approval_review_revision(
                gate=gate,
                revision_repo=self._revision_repo,
                decided_by=decided_by,
                profile_metadata=profile_metadata,
            )
        except ValueError as exc:
            return GateActionResult(
                action=GateDecision.APPROVE.value,
                gate_id=gate_id,
                decision_id=result.decision_id,
                status="approval_failed",
                result_gate_id=result.result_gate_id,
                result_command_id=result.result_command_id,
                result_revision_id=None,
                reason=str(exc),
            )
        if revision_id is None:
            return result
        return GateActionResult(
            action=result.action,
            gate_id=result.gate_id,
            decision_id=result.decision_id,
            status=result.status,
            result_gate_id=result.result_gate_id,
            result_command_id=result.result_command_id,
            result_revision_id=revision_id,
            reason=result.reason,
        )

    # ── action: reject ──────────────────────────────────────────────

    def reject_from_gate(
        self,
        *,
        gate_id: str,
        job_id: str | None = None,
        decided_by: str,
        reason: str = "",
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
        actor_type: str = GateActorType.HUMAN.value,
    ) -> GateActionResult:
        """Validate and execute a 'reject' decision."""
        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return GateActionResult(
                action=GateDecision.REJECT.value,
                gate_id=gate_id,
                decision_id="",
                status="gate_not_found",
            )
        effective_job_id = job_id or gate.job_id
        if job_id is not None and job_id != gate.job_id:
            return GateActionResult(
                action=GateDecision.REJECT.value,
                gate_id=gate_id,
                decision_id="",
                status="invalid_decision",
                reason="Gate job does not match the requested job.",
            )
        return self._execute_action(
            gate_id=gate_id,
            job_id=effective_job_id,
            action=GateDecision.REJECT,
            decided_by=decided_by,
            reason=reason,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            actor_type=actor_type,
        )

    # ── action: reject_gate (with reason) ──────────────────────────

    def reject_gate(
        self,
        *,
        gate_id: str,
        job_id: str,
        decided_by: str,
        reason: str = "",
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
        actor_type: str = GateActorType.HUMAN.value,
    ) -> GateActionResult:
        """Reject a gate with an auditable reason.

        Persists the rejection reason in the decision record and
        resolves the gate with REJECT. Once rejected, the gate
        cannot continue — a new gate must be created.

        No command is queued. The rejection is fully auditable via
        the decision record.

        Requires:
        - Gate exists and is OPEN
        - Gate phase allows REJECT decision (approval_review)
        - Gate checksum must match

        Returns:
            GateActionResult with status and persisted rejection reason.
        """
        return self._execute_action(
            gate_id=gate_id,
            job_id=job_id,
            action=GateDecision.REJECT,
            decided_by=decided_by,
            reason=reason,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            actor_type=actor_type,
        )

    # ── action: approve_repair ────────────────────────────────────

    def approve_repair(
        self,
        *,
        gate_id: str,
        job_id: str,
        decided_by: str,
        proposal_id: str,
        proposal_checksum: str,
        context_pack_checksum: str,
        reviewer_output_checksum: str = "",
        final_reviewed_diff_checksum: str = "",
        policy_validation_checksum: str = "",
        base_repo_state_checksum: str = "",
        final_reviewed_artifact_checksum: str = "",
        repair_revision_id: str = "",
        repair_revision_checksum: str = "",
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
        actor_type: str = GateActorType.HUMAN.value,
    ) -> GateActionResult:
        """Approve a repair proposal and continue at a repair_review gate.

        Delegates to V2RepairFlowService to validate the proposal and
        reviewer critique before resolving the gate. After approval, the
        gate is resolved with CONTINUE so the repair can be applied in
        the sandbox.

        Requires:
        - Gate exists
        - Gate phase is repair_review
        - Gate is OPEN (unless idempotent)
        - V2RepairFlowService is configured
        - Proposal exists in draft state
        - Reviewer gate passes (accepted critique matches checksums)
        - Gate checksum must match

        Returns:
            GateActionResult with status, decision_id, and
            result_revision_id linking to the approved repair proposal.
        """
        # 1. Gate existence
        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate_id,
                decision_id="",
                status="gate_not_found",
            )

        # 2. Idempotency check — before gate status check so repeated
        #    requests with the same key return idempotent regardless
        #    of the current gate state.
        effective_idempotency_key = (
            idempotency_key
            or sha256_canonical_json(
                {
                    "gate_id": gate_id,
                    "job_id": job_id,
                    "action": GateDecision.CONTINUE.value,
                    "decided_by": decided_by,
                    "proposal_id": proposal_id,
                    "proposal_checksum": proposal_checksum,
                    "context_pack_checksum": context_pack_checksum,
                    "reviewer_output_checksum": reviewer_output_checksum,
                    "final_reviewed_diff_checksum": final_reviewed_diff_checksum,
                    "policy_validation_checksum": policy_validation_checksum,
                    "base_repo_state_checksum": base_repo_state_checksum,
                    "final_reviewed_artifact_checksum": final_reviewed_artifact_checksum,
                    "repair_revision_id": repair_revision_id,
                    "repair_revision_checksum": repair_revision_checksum,
                    "actor_type": GateActorType.HUMAN.value,
                }
            )
        )
        request_checksum = sha256_canonical_json(
            {
                "gate_id": gate_id,
                "job_id": job_id,
                "action": GateDecision.CONTINUE.value,
                "decided_by": decided_by,
                "actor_type": GateActorType.HUMAN.value,
                "expected_gate_checksum": expected_gate_checksum,
                "proposal_id": proposal_id,
                "proposal_checksum": proposal_checksum,
                "context_pack_checksum": context_pack_checksum,
                "reviewer_output_checksum": reviewer_output_checksum,
                "final_reviewed_diff_checksum": final_reviewed_diff_checksum,
                "policy_validation_checksum": policy_validation_checksum,
                "base_repo_state_checksum": base_repo_state_checksum,
                "final_reviewed_artifact_checksum": final_reviewed_artifact_checksum,
                "repair_revision_id": repair_revision_id,
                "repair_revision_checksum": repair_revision_checksum,
            }
        )
        existing = self._decision_repo.find_by_idempotency_key(
            effective_idempotency_key
        )
        if existing is not None:
            if existing.request_checksum == request_checksum:
                return GateActionResult(
                    action=GateDecision.CONTINUE.value,
                    gate_id=gate_id,
                    decision_id=existing.decision_id,
                    status="idempotent",
                    result_gate_id=existing.result_gate_id,
                    result_command_id=existing.result_command_id,
                    result_revision_id=existing.result_revision_id,
                )
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate_id,
                decision_id="",
                status="idempotency_conflict",
                reason=(
                    f"Idempotency key '{effective_idempotency_key}' was reused "
                    "for a different request payload"
                ),
            )

        if actor_type != GateActorType.HUMAN.value:
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate_id,
                decision_id="",
                status="actor_not_authoritative",
                reason=(
                    "approve_repair requires a human actor; "
                    f"received actor_type='{actor_type}'"
                ),
            )

        # 3. Gate status must be OPEN for new actions
        if gate.gate_status != "open":
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate_id,
                decision_id="",
                status="gate_not_open",
                reason=f"Gate is {gate.gate_status}",
            )

        # 4. Phase must be repair_review
        try:
            gate_phase = GatePhase(gate.gate_phase)
        except ValueError:
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate_id,
                decision_id="",
                status="invalid_decision",
                reason=f"Unknown gate phase: {gate.gate_phase}",
            )

        if gate_phase != GatePhase.REPAIR_REVIEW:
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate_id,
                decision_id="",
                status="invalid_decision",
                reason=f"approve_repair only works on repair_review gates, not {gate_phase.value}",
            )

        binding_result = self._validate_reviewed_repair_binding(
            gate=gate,
            binding=RepairApprovalChecksumBinding(
                proposal_checksum=proposal_checksum,
                context_pack_checksum=context_pack_checksum,
                reviewer_output_checksum=reviewer_output_checksum,
                final_reviewed_diff_checksum=final_reviewed_diff_checksum,
                policy_validation_checksum=policy_validation_checksum,
                base_repo_state_checksum=base_repo_state_checksum,
                final_reviewed_artifact_checksum=final_reviewed_artifact_checksum,
                repair_revision_id=repair_revision_id,
                repair_revision_checksum=repair_revision_checksum,
            ),
        )
        if binding_result is not None:
            return binding_result

        # 5. Require repair service
        if self._repair_service is None:
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate_id,
                decision_id="",
                status="no_repair_service",
                reason="V2RepairFlowService is not configured",
            )

        # 6. Approve the proposal via V2RepairFlowService
        #    This validates proposal state and reviewer critique gate
        try:
            self._repair_service.approve_proposal(
                proposal_id=proposal_id,
                approval_checksum=gate.source_artifact_checksum,
                proposal_checksum=proposal_checksum,
                context_pack_checksum=context_pack_checksum,
            )
        except ValueError as exc:
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate_id,
                decision_id="",
                status="approval_failed",
                reason=str(exc),
            )

        # 7. Execute continue action (resolves gate, persists decision)
        result = self._execute_action(
            gate_id=gate_id,
            job_id=job_id,
            action=GateDecision.CONTINUE,
            decided_by=decided_by,
            idempotency_key=effective_idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            result_revision_id=proposal_id,
            actor_type=GateActorType.HUMAN.value,
            request_checksum=request_checksum,
        )

        if result.status not in ("executed", "idempotent"):
            return result

        if (
            result.status == "executed"
            and self._revision_repo is not None
            and repair_revision_id
        ):
            accepted_revision_id = uuid4().hex
            latest = self._revision_repo.find_latest_by_kind(
                gate.job_id,
                gate.stage_index,
                "repair",
            )
            now = utc_now_text()
            self._revision_repo.save(
                ArtifactRevisionRecord(
                    revision_id=accepted_revision_id,
                    job_id=gate.job_id,
                    stage_index=gate.stage_index,
                    revision_kind="repair",
                    revision_status="accepted",
                    revision_order=(latest.revision_order + 1) if latest else 1,
                    evidence_checksum=gate.source_artifact_checksum,
                    prior_revision_checksum=repair_revision_checksum or None,
                    artifact_refs_json=gate.source_artifact_refs_json,
                    prior_revision_id=repair_revision_id,
                    superseded_by_revision_id=None,
                    accepted_at_gate_id=gate_id,
                    created_at=now,
                    created_by=decided_by,
                    accepted_at=now,
                    accepted_by=decided_by,
                )
            )
            return GateActionResult(
                action=result.action,
                gate_id=result.gate_id,
                decision_id=result.decision_id,
                status=result.status,
                result_gate_id=result.result_gate_id,
                result_command_id=result.result_command_id,
                result_revision_id=accepted_revision_id,
                reason=result.reason,
            )

        return result

    def _validate_reviewed_repair_binding(
        self,
        *,
        gate: Any,
        binding: RepairApprovalChecksumBinding,
    ) -> GateActionResult | None:
        refs = self._parse_gate_ref_checksums(gate.source_artifact_refs_json)
        reviewed_gate = any(
            key in refs
            for key in (
                "reviewer_output_checksum",
                "final_reviewed_diff_checksum",
                "policy_validation_checksum",
                "base_repo_state_checksum",
            )
        )
        if not reviewed_gate:
            return None

        required = {
            "context_pack_checksum": binding.context_pack_checksum,
            "reviewer_output_checksum": binding.reviewer_output_checksum,
            "final_reviewed_diff_checksum": binding.final_reviewed_diff_checksum,
            "policy_validation_checksum": binding.policy_validation_checksum,
            "base_repo_state_checksum": binding.base_repo_state_checksum,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate.gate_id,
                decision_id="",
                status="missing_repair_checksum",
                reason="Missing reviewed repair checksum(s): " + ", ".join(missing),
            )

        expected = {
            "context_pack_checksum": binding.context_pack_checksum,
            "reviewer_output_checksum": binding.reviewer_output_checksum,
            "final_reviewed_diff_checksum": binding.final_reviewed_diff_checksum,
            "policy_validation_checksum": binding.policy_validation_checksum,
            "base_repo_state_checksum": binding.base_repo_state_checksum,
        }
        if binding.final_reviewed_artifact_checksum:
            expected["final_artifact_checksum"] = binding.final_reviewed_artifact_checksum
        for name, supplied in expected.items():
            actual = refs.get(name)
            if actual is not None and actual != supplied:
                return GateActionResult(
                    action=GateDecision.CONTINUE.value,
                    gate_id=gate.gate_id,
                    decision_id="",
                    status="repair_checksum_mismatch",
                    reason=f"{name} does not match the reviewed repair gate",
                )

        required_binding = {
            "failure_evidence_checksum": refs.get("failure_evidence_checksum", ""),
            "context_pack_checksum": binding.context_pack_checksum,
            "primary_output_checksum": refs.get("primary_output_checksum", ""),
            "reviewer_output_checksum": binding.reviewer_output_checksum,
            "final_reviewed_diff_checksum": binding.final_reviewed_diff_checksum,
            "policy_validation_checksum": binding.policy_validation_checksum,
            "base_repo_state_checksum": binding.base_repo_state_checksum,
            "final_artifact_checksum": (
                binding.final_reviewed_artifact_checksum
                or refs.get("final_artifact_checksum", "")
            ),
        }
        expected_gate_source_checksum = sha256_canonical_json(required_binding)
        if gate.source_artifact_checksum != expected_gate_source_checksum:
            return GateActionResult(
                action=GateDecision.CONTINUE.value,
                gate_id=gate.gate_id,
                decision_id="",
                status="repair_checksum_mismatch",
                reason="Reviewed repair binding no longer matches gate source checksum",
            )
        return None

    @staticmethod
    def _parse_gate_ref_checksums(source_artifact_refs_json: str) -> dict[str, str]:
        try:
            parsed = json.loads(source_artifact_refs_json)
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(parsed, list):
            return {}
        refs: dict[str, str] = {}
        for item in parsed:
            if not isinstance(item, str) or ":" not in item:
                continue
            key, value = item.split(":", 1)
            if key.endswith("_checksum") or key == "checksum":
                refs[key] = value
        return refs

    # ── action: approve_transformation ─────────────────────────────

    def approve_transformation(
        self,
        *,
        gate_id: str,
        job_id: str,
        decided_by: str,
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
        actor_type: str = GateActorType.HUMAN.value,
        revision_requested_active: bool = False,
        profile_metadata: dict[str, Any] | None = None,
    ) -> GateActionResult:
        """Approve transformation at an approval_review gate.

        Validates that both an accepted analysis revision and an accepted
        plan revision exist for the same stage before allowing approval.
        This prevents approving a transformation on a stale plan.

        On success, returns a result_command_id that the caller can use
        to queue the backend-owned transform command.

        Requires:
        - Gate exists and is OPEN
        - Gate phase is approval_review (APPROVE is valid)
        - Accepted analysis revision exists for this stage
        - Accepted plan revision exists for this stage (no stale plan)
        - Gate checksum must match

        Returns:
            GateActionResult with status, decision_id, and
            result_command_id referencing the queued transform command.
        """
        # Pre-validate accepted analysis and plan BEFORE resolving gate
        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return GateActionResult(
                action=GateDecision.APPROVE.value,
                gate_id=gate_id,
                decision_id="",
                status="gate_not_found",
            )

        stage_index = gate.stage_index

        if revision_requested_active:
            return GateActionResult(
                action=GateDecision.APPROVE.value,
                gate_id=gate_id,
                decision_id="",
                status="approval_failed",
                reason=(
                    "A revision request is pending. Transform remains blocked. "
                    "Approval is disabled until the revision is resolved or new "
                    "evidence is generated."
                ),
            )

        if self._revision_repo is not None:
            accepted_analysis = self._revision_repo.find_accepted(
                job_id, stage_index, "analysis"
            )
            if accepted_analysis is None:
                return GateActionResult(
                    action=GateDecision.APPROVE.value,
                    gate_id=gate_id,
                    decision_id="",
                    status="no_accepted_analysis",
                    reason="No accepted analysis revision for this stage",
                )

            accepted_plan = self._revision_repo.find_accepted(
                job_id, stage_index, "planning"
            )
            if accepted_plan is None:
                return GateActionResult(
                    action=GateDecision.APPROVE.value,
                    gate_id=gate_id,
                    decision_id="",
                    status="no_accepted_plan",
                    reason="No accepted plan revision for this stage",
                )

        if actor_type not in {GateActorType.HUMAN.value, GateActorType.SYSTEM.value}:
            return GateActionResult(
                action=GateDecision.APPROVE.value,
                gate_id=gate_id,
                decision_id="",
                status="actor_not_authoritative",
                reason=(
                    "approve_transformation requires a human actor or system auto-approval; "
                    f"received actor_type='{actor_type}'"
                ),
            )

        # Generate a command ID for the queued transform
        command_id = uuid4().hex

        # Execute the approval action (validates gate open, phase, checksum)
        result = self._execute_action(
            gate_id=gate_id,
            job_id=job_id,
            action=GateDecision.APPROVE,
            decided_by=decided_by,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            result_command_id=command_id,
            actor_type=actor_type,
        )
        if result.status != "executed" or gate.gate_phase != GatePhase.APPROVAL_REVIEW.value:
            return result
        try:
            revision_id = persist_approved_approval_review_revision(
                gate=gate,
                revision_repo=self._revision_repo,
                decided_by=decided_by,
                profile_metadata=profile_metadata,
            )
        except ValueError as exc:
            return GateActionResult(
                action=GateDecision.APPROVE.value,
                gate_id=gate_id,
                decision_id=result.decision_id,
                status="approval_failed",
                result_gate_id=result.result_gate_id,
                result_command_id=result.result_command_id,
                result_revision_id=None,
                reason=str(exc),
            )
        if revision_id is None:
            return result
        return GateActionResult(
            action=result.action,
            gate_id=result.gate_id,
            decision_id=result.decision_id,
            status=result.status,
            result_gate_id=result.result_gate_id,
            result_command_id=result.result_command_id,
            result_revision_id=revision_id,
            reason=result.reason,
        )

    def reject_repair(
        self,
        *,
        gate_id: str,
        job_id: str,
        decided_by: str,
        reason: str = "",
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
        actor_type: str = GateActorType.HUMAN.value,
    ) -> GateActionResult:
        """Reject a repair proposal at a repair_review gate."""
        if self._repair_service is None:
            return GateActionResult(
                action=GateDecision.REJECT.value,
                gate_id=gate_id,
                decision_id="",
                status="no_repair_service",
                reason="V2RepairFlowService is not configured",
            )
        return self._repair_service.reject_repair(
            gate_id=gate_id,
            job_id=job_id,
            decided_by=decided_by,
            reason=reason,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            actor_type=actor_type,
        )

    # ── internal pipeline ───────────────────────────────────────────

    def override_source_profile(
        self,
        *,
        gate_id: str,
        job_id: str,
        detection_artifact_ref: str,
        detected_source_profile: str,
        requested_source_profile: str,
        target_profile: str,
        expected_gate_checksum: str,
        expected_detection_artifact_checksum: str,
        reason: str,
        comments: str,
        decided_by: str,
        actor_type: str = GateActorType.HUMAN.value,
        idempotency_key: str | None = None,
    ) -> GateActionResult:
        """Persist a governed manual source-profile override decision."""
        try:
            request = SourceProfileOverrideRequest(
                job_id=job_id,
                gate_id=gate_id,
                detection_artifact_ref=detection_artifact_ref,
                detected_source_profile=detected_source_profile,
                requested_source_profile=requested_source_profile,
                target_profile=target_profile,
                expected_gate_checksum=expected_gate_checksum,
                expected_detection_artifact_checksum=expected_detection_artifact_checksum,
                reason=reason,
                comments=comments,
                decided_by=decided_by,
                actor_type=actor_type,
            )
        except (ValidationError, ValueError) as exc:
            return GateActionResult(
                action=GateDecision.OVERRIDE_SOURCE_PROFILE.value,
                gate_id=gate_id,
                decision_id="",
                status="invalid_source_profile_override",
                reason=str(exc),
            )

        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return GateActionResult(
                action=GateDecision.OVERRIDE_SOURCE_PROFILE.value,
                gate_id=gate_id,
                decision_id="",
                status="gate_not_found",
            )
        if gate.job_id != job_id:
            return GateActionResult(
                action=GateDecision.OVERRIDE_SOURCE_PROFILE.value,
                gate_id=gate_id,
                decision_id="",
                status="gate_job_mismatch",
                reason="Gate job does not match the override request job.",
            )
        if gate.gate_phase != GatePhase.ANALYSIS_REVIEW.value:
            return GateActionResult(
                action=GateDecision.OVERRIDE_SOURCE_PROFILE.value,
                gate_id=gate_id,
                decision_id="",
                status="invalid_decision",
                reason="Source-profile overrides are only allowed at analysis_review gates.",
            )
        if gate.source_artifact_checksum != request.expected_detection_artifact_checksum:
            return GateActionResult(
                action=GateDecision.OVERRIDE_SOURCE_PROFILE.value,
                gate_id=gate_id,
                decision_id="",
                status="stale_checksum",
                reason="Detection artifact checksum is stale. Refresh the gate view and retry.",
            )

        try:
            source_artifact_refs = json.loads(gate.source_artifact_refs_json or "[]")
        except (json.JSONDecodeError, TypeError):
            source_artifact_refs = []
        if request.detection_artifact_ref not in source_artifact_refs:
            return GateActionResult(
                action=GateDecision.OVERRIDE_SOURCE_PROFILE.value,
                gate_id=gate_id,
                decision_id="",
                status="artifact_ref_mismatch",
                reason="Detection artifact reference is not bound to this gate.",
            )

        safe_artifact = request.to_safe_artifact()
        request_checksum = sha256_canonical_json(
            {
                "action": GateDecision.OVERRIDE_SOURCE_PROFILE.value,
                "override": safe_artifact,
                "idempotency_key": idempotency_key,
            }
        )

        revision_id: str | None = None
        revision_order = 1
        if self._revision_repo is not None:
            latest = self._revision_repo.find_latest_by_kind(
                job_id,
                gate.stage_index,
                "source_profile_override",
            )
            if latest is not None:
                revision_order = latest.revision_order + 1
            revision_id = uuid4().hex

        result = self._execute_action(
            gate_id=gate_id,
            job_id=job_id,
            action=GateDecision.OVERRIDE_SOURCE_PROFILE,
            decided_by=decided_by,
            idempotency_key=idempotency_key,
            expected_gate_checksum=expected_gate_checksum,
            result_revision_id=revision_id,
            reason=reason,
            actor_type=actor_type,
            request_checksum=request_checksum,
        )
        if result.status != "executed" or self._revision_repo is None or revision_id is None:
            return result

        now = utc_now_text()
        self._revision_repo.save(
            ArtifactRevisionRecord(
                revision_id=revision_id,
                job_id=job_id,
                stage_index=gate.stage_index,
                revision_kind="source_profile_override",
                revision_status="accepted",
                revision_order=revision_order,
                evidence_checksum=request_checksum,
                prior_revision_checksum=None,
                artifact_refs_json=json.dumps(
                    safe_artifact,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                prior_revision_id=None,
                superseded_by_revision_id=None,
                accepted_at_gate_id=gate_id,
                created_at=now,
                created_by=decided_by,
                accepted_at=now,
                accepted_by=decided_by,
            )
        )
        return result

    def _execute_action(
        self,
        *,
        gate_id: str,
        job_id: str,
        action: GateDecision,
        decided_by: str,
        idempotency_key: str | None = None,
        expected_gate_checksum: str | None = None,
        result_command_id: str | None = None,
        result_revision_id: str | None = None,
        reason: str = "",
        actor_type: str = "human",
        request_checksum: str | None = None,
        open_followup_gate: bool = True,
    ) -> GateActionResult:
        """Common validation and execution pipeline for all gate actions.

        Steps:
        1. Verify gate exists and is OPEN.
        2. Validate decision is allowed for the gate phase.
        3. Compute current checksum for the action decision record.
        4. Check idempotency (duplicate detection).
        5. Resolve the gate via V2PhaseGateService.
        6. Persist the decision record.
        7. Return result with result references.

        Args:
            expected_gate_checksum: Optional caller-supplied expected
                checksum for stale protection. If provided, compared
                against the current gate checksum before resolution.
                If not provided, the current checksum is used (backward
                compatible behaviour).
            result_command_id: Optional command ID to store in the decision
                record. The caller is responsible for queueing the command.
            result_revision_id: Optional revision ID to store in the decision
                record. The caller is responsible for creating the revision.
            reason: Human-readable reason for the decision.
            actor_type: Who initiated this action ("human", "assistant",
                "api", or "system"). Defaults to "human". Assistant cannot
                perform authoritative actions (approve, reject).
        """
        # 1. Gate existence
        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return GateActionResult(
                action=action.value,
                gate_id=gate_id,
                decision_id="",
                status="gate_not_found",
            )

        # 2. Gate status must be OPEN for new actions.
        #    Check before idempotency so resolved/closed gates
        #    return gate_not_open rather than idempotency_conflict.
        if gate.gate_status != "open":
            return GateActionResult(
                action=action.value,
                gate_id=gate_id,
                decision_id="",
                status="gate_not_open",
                reason=f"Gate is {gate.gate_status}",
            )

        # 3. Idempotency check (for open gates only)
        effective_idempotency_key = idempotency_key or f"{gate_id}:{action.value}"
        request_checksum = request_checksum or sha256_canonical_json(
            {
                "gate_id": gate_id,
                "job_id": job_id,
                "action": action.value,
                "decided_by": decided_by,
                "reason": reason,
                "actor_type": actor_type,
                "expected_gate_checksum": expected_gate_checksum,
            }
        )
        existing = self._decision_repo.find_by_idempotency_key(effective_idempotency_key)
        if existing is not None:
            if existing.request_checksum == request_checksum:
                return GateActionResult(
                    action=action.value,
                    gate_id=gate_id,
                    decision_id=existing.decision_id,
                    status="idempotent",
                    result_gate_id=existing.result_gate_id,
                    result_command_id=existing.result_command_id,
                    result_revision_id=existing.result_revision_id,
                )
            return GateActionResult(
                action=action.value,
                gate_id=gate_id,
                decision_id="",
                status="idempotency_conflict",
                reason=(
                    f"Idempotency key '{effective_idempotency_key}' was reused "
                    "for a different request payload"
                ),
            )

        # 3aa. Actor authority check — non-human actors cannot perform
        #      authoritative actions (approve, reject).
        system_auto_approval = (
            action == GateDecision.APPROVE
            and actor_type == GateActorType.SYSTEM.value
            and decided_by == "system:auto-approval"
        )
        if (
            action.value in HUMAN_AUTHORITATIVE_ACTIONS
            and actor_type != GateActorType.HUMAN.value
            and not system_auto_approval
        ):
            return GateActionResult(
                action=action.value,
                gate_id=gate_id,
                decision_id="",
                status="actor_not_authoritative",
                reason=(
                    f"Action '{action.value}' requires a human actor, "
                    f"but actor_type is '{actor_type}'"
                ),
            )

        # 3a. Conflicting command guard — check if there are already
        #     non-terminal (queued/running) commands for this job.
        #     This prevents queuing conflicting work when another
        #     command is already in progress.
        if self._command_repo is not None:
            existing_commands = self._command_repo.list_by_job(gate.job_id)
            for cmd in existing_commands:
                from migration_factory.control_tower.domain.commands import (
                    CommandState,
                )
                try:
                    cmd_state = CommandState(cmd.status)
                except ValueError:
                    continue
                if cmd_state in NONTERMINAL_COMMAND_STATES:
                    return GateActionResult(
                        action=action.value,
                        gate_id=gate_id,
                        decision_id="",
                        status="command_conflict",
                        reason=(
                            f"Job {gate.job_id} already has a non-terminal "
                            f"command {cmd.command_id} with status "
                            f"{cmd.status}"
                        ),
                    )

        # 4. Phase-valid decision check
        try:
            gate_phase = GatePhase(gate.gate_phase)
        except ValueError:
            return GateActionResult(
                action=action.value,
                gate_id=gate_id,
                decision_id="",
                status="invalid_decision",
                reason=f"Unknown gate phase: {gate.gate_phase}",
            )

        if not is_valid_decision_for_phase(gate_phase, action):
            return GateActionResult(
                action=action.value,
                gate_id=gate_id,
                decision_id="",
                status="invalid_decision",
                reason=f"Decision {action.value} not allowed at {gate_phase.value}",
            )

        # 6. Compute current gate checksum
        import json
        try:
            refs = json.loads(gate.source_artifact_refs_json)
        except (json.JSONDecodeError, TypeError):
            refs = []

        current_checksum = gate_checksum(
            gate_id=gate.gate_id,
            job_id=gate.job_id,
            gate_phase=gate.gate_phase,
            stage_index=gate.stage_index,
            source_artifact_checksum=gate.source_artifact_checksum,
            source_artifact_refs=refs,
        )

        # Stale checksum protection: if the caller supplied an expected
        # checksum, compare it against the current gate checksum.  A
        # mismatch means the caller's view of the gate is stale.
        if expected_gate_checksum is not None and expected_gate_checksum != current_checksum:
            return GateActionResult(
                action=action.value,
                gate_id=gate_id,
                decision_id="",
                status="stale_checksum",
                reason=(
                    f"Gate checksum mismatch: caller expected "
                    f"{expected_gate_checksum[:16]}... but current is "
                    f"{current_checksum[:16]}... "
                    f"Refresh the gate view and retry"
                ),
            )

        # 5. Resolve the gate (uses current_checksum as expected if
        #    caller did not supply one; otherwise uses the caller-supplied
        #    value which we validated above)
        resolve_expected = expected_gate_checksum or current_checksum
        resolve_result = self._gate_service.resolve_gate(ResolveGateRequest(
            gate_id=gate_id,
            job_id=job_id,
            gate_decision=action.value,
            expected_gate_checksum=resolve_expected,
            resolved_by=decided_by,
        ))

        if resolve_result.status != "resolved":
            return GateActionResult(
                action=action.value,
                gate_id=gate_id,
                decision_id="",
                status=resolve_result.status,
                reason=f"Gate resolve failed: {resolve_result.status}",
            )

        # 6. Persist the decision
        decision_id = uuid4().hex
        now = utc_now_text()

        result_gate_id: str | None = None
        # For reanalyze/revise, create a new open gate
        if open_followup_gate and action in (GateDecision.REANALYZE, GateDecision.REVISE):
            new_gate = self._gate_service.create_gate(CreateGateRequest(
                job_id=gate.job_id,
                gate_phase=gate.gate_phase,
                stage_index=gate.stage_index,
                source_artifact_checksum=gate.source_artifact_checksum,
                source_artifact_refs=tuple(refs),
                created_by=decided_by,
            ))
            result_gate_id = new_gate.gate_id if new_gate.status == "created" else None

        # For continue on gates, create the next phase gate within
        # the same stage (no migration stage skip).
        #   analysis_review + CONTINUE → queue planning command
        #   planning_review + CONTINUE → approval_review gate
        # Stage 2 migration is NOT queued from here — only
        # stage_completion_review + CONTINUE triggers migration stage
        # progression (handled by the caller).
        if action == GateDecision.CONTINUE:
            try:
                gate_phase_for_approval = GatePhase(gate.gate_phase)
            except ValueError:
                gate_phase_for_approval = None

            if gate_phase_for_approval == GatePhase.ANALYSIS_REVIEW:
                # P0: Do NOT create a synthetic planning_review gate.
                # Real planning execution must happen first and produce
                # planning artifacts (migration_plan.yaml,
                # migration_units.yaml, approval_request.json).
                # Queue a planning command that the backend run loop
                # picks up later. Once planning completes, a
                # post-planning hook creates planning_review with
                # real artifact refs/checksums.
                planning_command_id = uuid4().hex
                now = utc_now_text()
                planning_record = V2StageCommandRecord(
                    command_id=planning_command_id,
                    job_id=gate.job_id,
                    stage_index=gate.stage_index,
                    manifest_checksum="phase:planning",
                    argv_json="[]",
                    env_json="{}",
                    status="planning_pending",
                    created_at=now,
                    updated_at=now,
                    result_json=None,
                    gate_id=gate_id,
                    decision_id="",  # filled below
                )
                if self._command_repo is not None:
                    self._command_repo.save(planning_record)
                result_command_id = planning_command_id

            elif gate_phase_for_approval == GatePhase.PLANNING_REVIEW:
                # Create approval_review gate for the same stage
                approval_gate = self._gate_service.create_gate(CreateGateRequest(
                    job_id=gate.job_id,
                    gate_phase=GatePhase.APPROVAL_REVIEW.value,
                    stage_index=gate.stage_index,
                    source_artifact_checksum=current_checksum,
                    source_artifact_refs=tuple(refs),
                    created_by=decided_by,
                ))
                if approval_gate.status == "created":
                    result_gate_id = approval_gate.gate_id

        decision_record = GateDecisionRecord(
            decision_id=decision_id,
            gate_id=gate_id,
            job_id=job_id,
            action=action.value,
            expected_gate_checksum=current_checksum,
            idempotency_key=effective_idempotency_key,
            request_checksum=request_checksum,
            result_gate_id=result_gate_id,
            result_command_id=result_command_id,  # caller queues command
            result_revision_id=result_revision_id,  # caller creates revision
            decided_by=decided_by,
            decided_at=now,
            actor_type=actor_type,
            actor_id=decided_by,
            reason=reason,
        )

        try:
            self._decision_repo.save(decision_record)
        except Exception:
            # Idempotency key collision after concurrent resolve
            existing2 = self._decision_repo.find_by_idempotency_key_and_checksum(
                effective_idempotency_key, request_checksum
            )
            if existing2 is not None:
                return GateActionResult(
                    action=action.value,
                    gate_id=gate_id,
                    decision_id=existing2.decision_id,
                    status="idempotent",
                    result_gate_id=existing2.result_gate_id,
                    result_command_id=existing2.result_command_id,
                    result_revision_id=existing2.result_revision_id,
                )
            raise

        # 7. Return result
        return GateActionResult(
            action=action.value,
            gate_id=gate_id,
            decision_id=decision_id,
            status="executed",
            result_gate_id=result_gate_id,
            result_command_id=result_command_id,
            result_revision_id=result_revision_id,
        )
