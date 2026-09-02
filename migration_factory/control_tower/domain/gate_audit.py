"""F15 gate audit trail — maps gate lifecycle events to audit records.

Reuses the existing AuditRecord entity and SqliteAuditRecordRepository.
Every gate lifecycle action (create, resolve, supersede, decision
recorded) emits an append-only audit record.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from migration_factory.control_tower.domain.checksums import sha256_canonical_json


# ── audit action constants ────────────────────────────────────────────


class GateAuditAction:
    """Canonical audit action strings for F15 gate operations."""

    GATE_CREATED = "f15_gate_created"
    GATE_RESOLVED = "f15_gate_resolved"
    GATE_SUPERSEDED = "f15_gate_superseded"
    GATE_DECISION_RECORDED = "f15_gate_decision_recorded"
    GATE_DECISION_REJECTED = "f15_gate_decision_rejected"
    GATE_CONFLICT_DETECTED = "f15_gate_conflict_detected"
    REVISION_CREATED = "f15_revision_created"
    REVISION_ACCEPTED = "f15_revision_accepted"
    REVISION_SUPERSEDED = "f15_revision_superseded"


@dataclass(frozen=True, slots=True)
class GateAuditPayload:
    """Structured payload for a gate audit record.

    Stored as JSON in AuditRecord.payload_json with checksum binding.
    """

    gate_id: str | None = None
    gate_phase: str | None = None
    stage_index: int | None = None
    decision_id: str | None = None
    action: str | None = None
    revision_id: str | None = None
    revision_kind: str | None = None

    def _non_none_fields(self) -> dict[str, str | int]:
        from dataclasses import fields
        result: dict[str, str | int] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if v is not None:
                result[f.name] = v
        return result

    def to_json(self) -> str:
        return json.dumps(
            self._non_none_fields(),
            separators=(",", ":"),
            sort_keys=True,
        )

    def to_checksum(self) -> str:
        return sha256_canonical_json(self._non_none_fields())


def build_gate_audit_payload(
    *,
    gate_id: str | None = None,
    gate_phase: str | None = None,
    stage_index: int | None = None,
    decision_id: str | None = None,
    action: str | None = None,
    revision_id: str | None = None,
    revision_kind: str | None = None,
) -> GateAuditPayload:
    """Build a safe audit payload for a gate action.

    Never includes paths, commands, env, or filesystem targets.
    """
    return GateAuditPayload(
        gate_id=gate_id,
        gate_phase=gate_phase,
        stage_index=stage_index,
        decision_id=decision_id,
        action=action,
        revision_id=revision_id,
        revision_kind=revision_kind,
    )
