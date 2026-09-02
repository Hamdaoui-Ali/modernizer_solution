"""V2 approval mapping — interrupt to decision card to resume command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import (
    SqliteV2ApprovalRepository,
    V2ApprovalDecisionRecord,
    V2ResumeCommandRecord,
)


@dataclass(frozen=True)
class ApprovalDecisionCard:
    card_id: str
    job_id: str
    interrupt_id: str
    request_checksum: str
    stage_index: int
    summary: str
    status: str  # pending, approved, rejected
    created_at: str
    # F07 reviewer metadata
    reviewer_critique_id: str | None = None
    reviewer_decision: str | None = None
    reviewed_checksum: str | None = None


@dataclass(frozen=True)
class ResumeCommand:
    resume_id: str
    card_id: str
    decision: str
    job_id: str
    stage_index: int
    command: tuple[str, ...]
    created_at: str = ""


class V2ApprovalMappingService:
    """Maps orchestrator interrupts to decision cards and resume commands.

    - Interrupt becomes a durable decision card with checksum
    - Approval queues a resume command
    - Rejection pauses the stage
    - Stale checksum/version rejection
    """

    def __init__(
        self,
        approval_repo: SqliteV2ApprovalRepository | None = None,
    ) -> None:
        self._decisions: dict[str, ApprovalDecisionCard] = {}
        self._resumes: dict[str, ResumeCommand] = {}
        self._repo = approval_repo

    def create_decision_card(
        self,
        *,
        job_id: str,
        interrupt_id: str,
        request_checksum: str,
        stage_index: int = 1,
        summary: str = "Approval required for stage progression",
        # F07: optional reviewer critique metadata
        reviewer_critique_id: str | None = None,
        reviewer_decision: str | None = None,
        reviewed_checksum: str | None = None,
    ) -> ApprovalDecisionCard:
        """Create a durable decision card from an orchestrator interrupt."""
        card = ApprovalDecisionCard(
            card_id=uuid4().hex,
            job_id=job_id,
            interrupt_id=interrupt_id,
            request_checksum=request_checksum,
            stage_index=stage_index,
            summary=summary,
            status="pending",
            created_at=utc_now_text(),
            reviewer_critique_id=reviewer_critique_id,
            reviewer_decision=reviewer_decision,
            reviewed_checksum=reviewed_checksum,
        )
        self._decisions[card.card_id] = card
        # Persist if repo available
        if self._repo is not None:
            record = V2ApprovalDecisionRecord(
                card_id=card.card_id,
                job_id=card.job_id,
                interrupt_id=card.interrupt_id,
                request_checksum=card.request_checksum,
                stage_index=card.stage_index,
                summary=card.summary,
                status=card.status,
                created_at=card.created_at,
            )
            self._repo.save_card(record)
        return card

    def approve(
        self,
        card_id: str,
        expected_checksum: str,
        job_id: str,
        run_dir: str = "",
    ) -> ResumeCommand:
        """Approve a decision card and queue a resume command.

        Validates the expected checksum before approving.
        """
        card = self._decisions.get(card_id)
        if card is None and self._repo is not None:
            record = self._repo.get_card(card_id)
            if record is not None:
                card = ApprovalDecisionCard(
                    card_id=record.card_id,
                    job_id=record.job_id,
                    interrupt_id=record.interrupt_id,
                    request_checksum=record.request_checksum,
                    stage_index=record.stage_index,
                    summary=record.summary,
                    status=record.status,
                    created_at=record.created_at,
                )
                self._decisions[card_id] = card
        if card is None:
            raise ValueError(f"Decision card {card_id!r} not found")
        if card.job_id and card.job_id != job_id:
            raise ValueError(f"Decision card {card_id!r} does not belong to job {job_id!r}")

        if card.request_checksum != expected_checksum:
            raise ValueError(
                f"Checksum mismatch: expected {expected_checksum}, "
                f"got {card.request_checksum}"
            )

        if card.status == "approved":
            # Idempotent duplicate approve — return existing resume if available
            for resume in self._resumes.values():
                if resume.card_id == card_id:
                    return resume
            # Legacy: card was approved before resume tracking existed
            if self._repo is not None:
                rows = self._repo.list_resumes_by_job(job_id)
                for row in rows:
                    if row.card_id == card_id:
                        return ResumeCommand(
                            resume_id=row.resume_id,
                            card_id=row.card_id,
                            decision=row.decision,
                            job_id=row.job_id,
                            stage_index=row.stage_index,
                            command=tuple(json.loads(row.command_json)),
                            created_at=row.created_at,
                        )
            # Fallback: return card-only result (no resume found)
            return ResumeCommand(
                resume_id="",
                card_id=card_id,
                decision="approved",
                job_id=job_id,
                stage_index=card.stage_index,
                command=(),
            )

        if card.status != "pending":
            raise ValueError(f"Decision card {card_id!r} is already {card.status}")

        # Update card
        updated = ApprovalDecisionCard(
            card_id=card.card_id,
            job_id=card.job_id or job_id,
            interrupt_id=card.interrupt_id,
            request_checksum=card.request_checksum,
            stage_index=card.stage_index,
            summary=card.summary,
            status="approved",
            created_at=card.created_at,
        )
        self._decisions[card_id] = updated
        # Persist status if repo available
        if self._repo is not None:
            self._repo.update_card_status(card_id, "approved")

        # Create resume command
        resume_command = _resume_command_for_card(updated, decision="approved", run_dir=run_dir)
        now = utc_now_text()
        resume = ResumeCommand(
            resume_id=uuid4().hex,
            card_id=card_id,
            decision="approved",
            job_id=job_id,
            stage_index=card.stage_index,
            command=resume_command,
            created_at=now,
        )
        self._resumes[resume.resume_id] = resume
        # Persist resume if repo available
        if self._repo is not None:
            resume_record = V2ResumeCommandRecord(
                resume_id=resume.resume_id,
                card_id=resume.card_id,
                decision=resume.decision,
                job_id=resume.job_id,
                stage_index=resume.stage_index,
                command_json=json.dumps(list(resume.command), separators=(",", ":")),
                created_at=resume.created_at,
            )
            self._repo.save_resume(resume_record)
        return resume

    def reject(self, card_id: str, job_id: str) -> ApprovalDecisionCard:
        """Reject a decision card, pausing the stage."""
        card = self._decisions.get(card_id)
        if card is None and self._repo is not None:
            record = self._repo.get_card(card_id)
            if record is not None:
                card = ApprovalDecisionCard(
                    card_id=record.card_id,
                    job_id=record.job_id,
                    interrupt_id=record.interrupt_id,
                    request_checksum=record.request_checksum,
                    stage_index=record.stage_index,
                    summary=record.summary,
                    status=record.status,
                    created_at=record.created_at,
                )
                self._decisions[card_id] = card
        if card is None:
            raise ValueError(f"Decision card {card_id!r} not found")
        if card.job_id and card.job_id != job_id:
            raise ValueError(f"Decision card {card_id!r} does not belong to job {job_id!r}")
        if card.status == "rejected":
            # Idempotent duplicate reject
            return card
        if card.status != "pending":
            raise ValueError(f"Decision card {card_id!r} is already {card.status}")

        updated = ApprovalDecisionCard(
            card_id=card.card_id,
            job_id=card.job_id or job_id,
            interrupt_id=card.interrupt_id,
            request_checksum=card.request_checksum,
            stage_index=card.stage_index,
            summary=card.summary,
            status="rejected",
            created_at=card.created_at,
        )
        self._decisions[card_id] = updated
        # Persist status if repo available
        if self._repo is not None:
            self._repo.update_card_status(card_id, "rejected")
        return updated

    def get_card(self, card_id: str) -> ApprovalDecisionCard | None:
        # Try in-memory first, then repo
        card = self._decisions.get(card_id)
        if card is None and self._repo is not None:
            record = self._repo.get_card(card_id)
            if record is not None:
                card = ApprovalDecisionCard(
                    card_id=record.card_id,
                    job_id=record.job_id,
                    interrupt_id=record.interrupt_id,
                    request_checksum=record.request_checksum,
                    stage_index=record.stage_index,
                    summary=record.summary,
                    status=record.status,
                    created_at=record.created_at,
                )
                self._decisions[card_id] = card
        return card

    def card_to_dict(self, card: ApprovalDecisionCard) -> dict[str, Any]:
        result: dict[str, Any] = {
            "card_id": card.card_id,
            "job_id": card.job_id,
            "interrupt_id": card.interrupt_id,
            "request_checksum": card.request_checksum,
            "stage_index": card.stage_index,
            "summary": card.summary,
            "status": card.status,
            "created_at": card.created_at,
        }
        # Include optional reviewer metadata (F07)
        reviewer_critique_id = getattr(card, "reviewer_critique_id", None)
        reviewer_decision = getattr(card, "reviewer_decision", None)
        reviewed_checksum = getattr(card, "reviewed_checksum", None)
        if reviewer_critique_id is not None:
            result["reviewer_critique_id"] = reviewer_critique_id
        if reviewer_decision is not None:
            result["reviewer_decision"] = reviewer_decision
        if reviewed_checksum is not None:
            result["reviewed_checksum"] = reviewed_checksum
        return result

    def resume_to_dict(self, resume: ResumeCommand) -> dict[str, Any]:
        return {
            "resume_id": resume.resume_id,
            "card_id": resume.card_id,
            "decision": resume.decision,
            "job_id": resume.job_id,
            "stage_index": resume.stage_index,
            "command": list(resume.command),
        }


def _resume_command_for_card(card: ApprovalDecisionCard, *, decision: str, run_dir: str) -> tuple[str, ...]:
    return (
        "python",
        "-m",
        "migration_factory.orchestrator.resume",
        "--run-id",
        card.interrupt_id,
        "--run-dir",
        run_dir,
        "--decision",
        decision,
        "--approved-by",
        "human",
    )
