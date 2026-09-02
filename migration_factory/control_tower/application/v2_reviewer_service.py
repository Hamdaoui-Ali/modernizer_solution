"""V2 reviewer service — reviewer critique gate before apply (F07).

The reviewer creates critiques for repair and POM proposals. A reviewer
``accept`` is NOT human approval — it only enables approval-card eligibility.
The human must still explicitly approve via a checksum-gated decision card.

The service enforces:
- Reviewer accept never changes proposal status to approved/applied.
- Latest accepted critique must match current proposal_checksum AND
  context_pack_checksum before approval can proceed.
- Revised proposal needs a fresh critique.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.v2_reviewer_repository import (
    SqliteV2ReviewerRepository,
    V2ReviewerCritiqueRecord,
)


@dataclass(frozen=True)
class ReviewerCritique:
    critique_id: str
    proposal_id: str
    proposal_type: str  # repair, pom_patch
    proposal_checksum: str
    context_pack_checksum: str
    decision: str  # accept, revise, reject
    reasoning: str
    missing_evidence: tuple[str, ...]
    unsafe_assumptions: tuple[str, ...]
    model_invocation_id: str | None
    created_at: str


class V2ReviewerService:
    """Service for recording and querying reviewer critiques.

    The reviewer is a backend policy gate. It does not approve, execute,
    or change proposal status. Its accept decision enables approval-card
    preparation; revise and reject route back through the proposal pipeline.
    """

    def __init__(
        self,
        reviewer_repo: SqliteV2ReviewerRepository | None = None,
    ) -> None:
        self._critiques: dict[str, ReviewerCritique] = {}
        self._repo = reviewer_repo

    def record_critique(
        self,
        *,
        proposal_id: str,
        proposal_type: str = "repair",
        proposal_checksum: str,
        context_pack_checksum: str,
        decision: str,
        reasoning: str,
        missing_evidence: tuple[str, ...] = (),
        unsafe_assumptions: tuple[str, ...] = (),
        model_invocation_id: str | None = None,
    ) -> ReviewerCritique:
        """Record a reviewer critique for a proposal.

        This is a persistence operation only. It does NOT change proposal
        status. The decision is used downstream by the approval gate.

        Raises:
            ValueError: If decision is not one of accept/revise/reject.
        """
        if decision not in ("accept", "revise", "reject"):
            raise ValueError(
                f"Invalid reviewer decision {decision!r}. Must be accept, revise, or reject."
            )

        critique = ReviewerCritique(
            critique_id=uuid4().hex,
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            proposal_checksum=proposal_checksum,
            context_pack_checksum=context_pack_checksum,
            decision=decision,
            reasoning=reasoning,
            missing_evidence=missing_evidence,
            unsafe_assumptions=unsafe_assumptions,
            model_invocation_id=model_invocation_id,
            created_at=utc_now_text(),
        )
        self._critiques[critique.critique_id] = critique

        if self._repo is not None:
            record = V2ReviewerCritiqueRecord(
                critique_id=critique.critique_id,
                proposal_id=critique.proposal_id,
                proposal_type=critique.proposal_type,
                proposal_checksum=critique.proposal_checksum,
                context_pack_checksum=critique.context_pack_checksum,
                decision=critique.decision,
                reasoning=critique.reasoning,
                missing_evidence_json=json.dumps(list(critique.missing_evidence), separators=(",", ":")),
                unsafe_assumptions_json=json.dumps(list(critique.unsafe_assumptions), separators=(",", ":")),
                model_invocation_id=critique.model_invocation_id,
                created_at=critique.created_at,
            )
            self._repo.save_critique(record)

        return critique

    def get_critique(self, critique_id: str) -> ReviewerCritique | None:
        """Get a single critique by ID."""
        critique = self._critiques.get(critique_id)
        if critique is None and self._repo is not None:
            record = self._repo.get_critique(critique_id)
            if record is not None:
                critique = self._record_to_critique(record)
                self._critiques[critique_id] = critique
        return critique

    def list_critiques(self, proposal_id: str) -> tuple[ReviewerCritique, ...]:
        """List all critiques for a proposal, newest first."""
        if self._repo is not None:
            records = self._repo.list_critiques_by_proposal(proposal_id)
            return tuple(self._record_to_critique(r) for r in records)
        return tuple(
            c for c in self._critiques.values()
            if c.proposal_id == proposal_id
        )

    def check_reviewer_gate(
        self,
        proposal_id: str,
        proposal_checksum: str,
        context_pack_checksum: str,
    ) -> ReviewerCritique | None:
        """Check if a latest accepted critique matches the current checksums.

        This is the F07 fail-closed gate. Returns the critique if an
        accepted one matches, or None if no match exists.

        Args:
            proposal_id: The proposal being checked.
            proposal_checksum: Current proposal checksum.
            context_pack_checksum: Current context pack checksum.

        Returns:
            The matching accepted critique, or None if the gate is not satisfied.
        """
        if self._repo is not None:
            record = self._repo.get_latest_accepted(
                proposal_id=proposal_id,
                proposal_checksum=proposal_checksum,
                context_pack_checksum=context_pack_checksum,
            )
            if record is not None:
                return self._record_to_critique(record)
            return None

        # In-memory fallback
        matching = [
            c for c in self._critiques.values()
            if c.proposal_id == proposal_id
            and c.decision == "accept"
            and c.proposal_checksum == proposal_checksum
            and c.context_pack_checksum == context_pack_checksum
        ]
        if not matching:
            return None
        # Latest first
        matching.sort(key=lambda c: c.created_at, reverse=True)
        return matching[0]

    def critique_to_dict(self, critique: ReviewerCritique) -> dict[str, Any]:
        return {
            "critique_id": critique.critique_id,
            "proposal_id": critique.proposal_id,
            "proposal_type": critique.proposal_type,
            "proposal_checksum": critique.proposal_checksum,
            "context_pack_checksum": critique.context_pack_checksum,
            "decision": critique.decision,
            "reasoning": critique.reasoning,
            "missing_evidence": list(critique.missing_evidence),
            "unsafe_assumptions": list(critique.unsafe_assumptions),
            "model_invocation_id": critique.model_invocation_id,
            "created_at": critique.created_at,
        }

    def _record_to_critique(self, record: V2ReviewerCritiqueRecord) -> ReviewerCritique:
        return ReviewerCritique(
            critique_id=record.critique_id,
            proposal_id=record.proposal_id,
            proposal_type=record.proposal_type,
            proposal_checksum=record.proposal_checksum,
            context_pack_checksum=record.context_pack_checksum,
            decision=record.decision,
            reasoning=record.reasoning,
            missing_evidence=tuple(json.loads(record.missing_evidence_json)),
            unsafe_assumptions=tuple(json.loads(record.unsafe_assumptions_json)),
            model_invocation_id=record.model_invocation_id,
            created_at=record.created_at,
        )
