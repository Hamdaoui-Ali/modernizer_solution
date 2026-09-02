"""F15 V2 Phase Gate Service — governed stage gate lifecycle.

Creates and resolves gates for analysis, planning, approval, repair,
and stage-completion review points. All state changes go through
persisted gates with checksum binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import sha256_canonical_json, utc_now_text
from migration_factory.control_tower.domain.entities import PhaseGateRecord
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_event_repository import (
    SqliteV2JobEventRepository,
)
from migration_factory.control_tower.domain.gate_checksum import gate_checksum
from migration_factory.control_tower.schemas.phase_gate import (
    GateDecision,
    GatePhase,
    GateStatus,
    is_valid_decision_for_phase,
)


# ── request/result types ──────────────────────────────────────────────


@dataclass(frozen=True)
class CreateGateRequest:
    """Validated request to create a new gate.

    All fields are backend-validated; none come directly from the
    frontend or chatbot.
    """

    job_id: str
    gate_phase: str
    stage_index: int
    source_artifact_checksum: str
    source_artifact_refs: tuple[str, ...]
    created_by: str = "system"


@dataclass(frozen=True)
class CreateGateResult:
    gate_id: str
    gate_checksum: str
    status: str  # 'created' or 'conflict'
    existing_gate_id: str | None = None


@dataclass(frozen=True)
class ResolveGateRequest:
    """Validated request to resolve an open gate.

    The decision must match a valid GateDecision for the gate's phase.
    Stale checksums are rejected.
    """

    gate_id: str
    job_id: str
    gate_decision: str
    expected_gate_checksum: str
    resolved_by: str
    resolved_artifact_checksum: str | None = None


@dataclass(frozen=True)
class ResolveGateResult:
    gate_id: str
    status: str  # 'resolved', 'stale_checksum', 'already_resolved', 'not_found'


# ── available action types ────────────────────────────────────────


@dataclass(frozen=True)
class AvailableAction:
    """A possible action at a gate, with UI-friendly label and metadata."""

    action: str          # GateDecision value (e.g. "continue", "reanalyze")
    label: str           # UI-readable label (e.g. "Accept", "Request Reanalysis")
    description: str     # Short explanation for UI tooltips
    blocked: bool = False  # True if blocked by an open/running command conflict
    block_reason: str = ""


# Map GateDecision values to UI-friendly labels and descriptions
_ACTION_LABELS: dict[GateDecision, tuple[str, str]] = {
    GateDecision.CONTINUE: (
        "Accept",
        "Accept the current state and proceed to the next phase",
    ),
    GateDecision.REANALYZE: (
        "Request Reanalysis",
        "Request updated analysis with user feedback",
    ),
    GateDecision.REVISE: (
        "Request Revision",
        "Request a plan revision with user feedback",
    ),
    GateDecision.APPROVE: (
        "Approve",
        "Approve the transformation to proceed",
    ),
    GateDecision.REJECT: (
        "Reject",
        "Reject the current state — gate cannot continue",
    ),
    GateDecision.OVERRIDE_SOURCE_PROFILE: (
        "Override Source Profile",
        "Override detected source profile with required operator comments",
    ),
}


# ── service ──────────────────────────────────────────────────────────


class V2PhaseGateService:
    """Application service for F15 governed-stage gate lifecycle."""

    def __init__(
        self,
        gate_repo: SqlitePhaseGateRepository,
        event_repo: SqliteV2JobEventRepository | None = None,
    ) -> None:
        self._gate_repo = gate_repo
        self._event_repo = event_repo

    # ── create gate ─────────────────────────────────────────────────

    def create_gate(self, request: CreateGateRequest) -> CreateGateResult:
        """Create a new governed-stage gate.

        If an open gate already exists for the same (job_id, gate_phase,
        stage_index), the DB unique index will block the INSERT. We
        catch this and return a conflict result.

        Returns:
            CreateGateResult with the new gate_id and checksum.
        """
        # Check for existing open gate
        existing = self._gate_repo.find_open(
            request.job_id,
            request.gate_phase,
            request.stage_index,
        )
        if existing is not None:
            return CreateGateResult(
                gate_id="",
                gate_checksum="",
                status="conflict",
                existing_gate_id=existing.gate_id,
            )

        gate_id = uuid4().hex
        now = utc_now_text()

        # Compute canonical checksum
        chk = gate_checksum(
            gate_id=gate_id,
            job_id=request.job_id,
            gate_phase=request.gate_phase,
            stage_index=request.stage_index,
            source_artifact_checksum=request.source_artifact_checksum,
            source_artifact_refs=request.source_artifact_refs,
        )

        import json
        record = PhaseGateRecord(
            gate_id=gate_id,
            job_id=request.job_id,
            gate_phase=request.gate_phase,
            stage_index=request.stage_index,
            gate_status=GateStatus.OPEN.value,
            gate_decision="pending",
            source_artifact_checksum=request.source_artifact_checksum,
            resolved_artifact_checksum=None,
            source_artifact_refs_json=json.dumps(
                sorted(request.source_artifact_refs), separators=(",", ":")
            ),
            created_at=now,
            resolved_at=None,
            resolved_by=None,
        )

        self._gate_repo.save(record)

        if self._event_repo is not None:
            self._event_repo.insert(
                job_id=request.job_id,
                sequence=0,  # will be assigned by repo
                event_type="f15_gate_opened",
                actor_type="backend",
                actor_id="v2_phase_gate_service",
                correlation_id=gate_id,
                causation_id=None,
                payload_json=json.dumps({
                    "gate_id": gate_id,
                    "gate_phase": request.gate_phase,
                    "stage_index": request.stage_index,
                }, separators=(",", ":")),
            )

        return CreateGateResult(
            gate_id=gate_id,
            gate_checksum=chk,
            status="created",
        )

    # ── resolve gate ────────────────────────────────────────────────

    def resolve_gate(self, request: ResolveGateRequest) -> ResolveGateResult:
        """Resolve an open gate with a decision.

        Validates that the gate is still open and the checksum matches.
        Stale checksums are rejected with a 'stale_checksum' status.

        Returns:
            ResolveGateResult with the outcome status.
        """
        gate = self._gate_repo.get(request.gate_id)
        if gate is None:
            return ResolveGateResult(
                gate_id=request.gate_id,
                status="not_found",
            )

        if gate.gate_status != GateStatus.OPEN.value:
            return ResolveGateResult(
                gate_id=request.gate_id,
                status="already_resolved" if gate.gate_status == "resolved" else "not_open",
            )

        # Recompute current checksum
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

        if current_checksum != request.expected_gate_checksum:
            return ResolveGateResult(
                gate_id=request.gate_id,
                status="stale_checksum",
            )

        now = utc_now_text()
        self._gate_repo.resolve(
            gate_id=request.gate_id,
            gate_decision=request.gate_decision,
            resolved_by=request.resolved_by,
            resolved_at=now,
            resolved_artifact_checksum=request.resolved_artifact_checksum,
        )

        if self._event_repo is not None:
            self._event_repo.insert(
                job_id=gate.job_id,
                sequence=0,
                event_type="f15_gate_resolved",
                actor_type="human",
                actor_id=request.resolved_by,
                correlation_id=request.gate_id,
                causation_id=None,
                payload_json=json.dumps({
                    "gate_id": request.gate_id,
                    "gate_phase": gate.gate_phase,
                    "decision": request.gate_decision,
                }, separators=(",", ":")),
            )

        return ResolveGateResult(
            gate_id=request.gate_id,
            status="resolved",
        )

    # ── available actions ─────────────────────────────────────────

    def get_available_actions(
        self,
        gate_id: str,
        *,
        blocked_actions: set[str] | None = None,
    ) -> list[AvailableAction]:
        """Return the list of valid actions for an open gate.

        Only open gates return available actions. Resolved, superseded,
        or nonexistent gates return an empty list.

        Args:
            gate_id: The gate to query.
            blocked_actions: Optional set of action values that are
                temporarily blocked (e.g. due to open/running commands).

        Returns:
            List of AvailableAction with UI-friendly labels and
            block status.
        """
        gate = self._gate_repo.get(gate_id)
        if gate is None or gate.gate_status != GateStatus.OPEN.value:
            return []

        try:
            gate_phase = GatePhase(gate.gate_phase)
        except ValueError:
            return []

        blocked = blocked_actions or set()
        result: list[AvailableAction] = []

        for decision in GateDecision:
            if decision == GateDecision.PENDING:
                continue  # not a user-selectable action
            if not is_valid_decision_for_phase(gate_phase, decision):
                continue

            label_info = _ACTION_LABELS.get(decision, (decision.value, ""))
            is_blocked = decision.value in blocked
            block_reason = ""
            if is_blocked:
                block_reason = f"Action '{decision.value}' is blocked by an open or running command"

            result.append(AvailableAction(
                action=decision.value,
                label=label_info[0],
                description=label_info[1],
                blocked=is_blocked,
                block_reason=block_reason,
            ))

        return result

    def supersede_gate(self, gate_id: str) -> bool:
        """Supersede an open gate with a newer one.

        Returns True if the gate was superseded, False if it was
        already resolved or not found.
        """
        gate = self._gate_repo.get(gate_id)
        if gate is None or gate.gate_status != GateStatus.OPEN.value:
            return False

        self._gate_repo.supersede(gate_id)
        return True
