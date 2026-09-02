"""F15 gate checksum contract — canonical checksums for gate-bound artifacts.

Gate checksums bind artifact refs, phase, stage, and gate identity into
a single deterministic hash.  Decisions made against a stale gate (where
the checksum no longer matches) are rejected by the service layer.
"""

from __future__ import annotations

from typing import Iterable

from migration_factory.control_tower.domain.checksums import sha256_canonical_json


def gate_checksum(
    *,
    gate_id: str,
    job_id: str,
    gate_phase: str,
    stage_index: int,
    source_artifact_checksum: str,
    source_artifact_refs: Iterable[str],
    extra: dict[str, str] | None = None,
) -> str:
    """Compute the canonical checksum for a gate.

    Includes all fields that could change between gate creation and
    decision time.  If any of these change, the checksum changes,
    and a decision made against a stale checksum is rejected.

    Args:
        gate_id: Unique gate identifier.
        job_id: Migration job id.
        gate_phase: GatePhase value (e.g. 'analysis_review').
        stage_index: Stage 1, 2, or 3.
        source_artifact_checksum: Checksum of the primary evidence artifact.
        source_artifact_refs: Ordered artifact ids under review.
        extra: Optional extra key/value pairs (reserved for future use).

    Returns:
        hex-encoded SHA-256 checksum.
    """
    payload: dict = {
        "gate_id": gate_id,
        "job_id": job_id,
        "gate_phase": gate_phase,
        "stage_index": stage_index,
        "source_artifact_checksum": source_artifact_checksum,
        "source_artifact_refs": sorted(source_artifact_refs),
    }
    if extra:
        payload["extra"] = dict(sorted(extra.items()))
    return sha256_canonical_json(payload)


# ── error messages ────────────────────────────────────────────────────


STALE_CHECKSUM_MESSAGE = (
    "Gate checksum mismatch: the gate's evidence has changed since this "
    "decision was prepared. Please review the latest evidence and try again."
)


class GateChecksumMismatchError(ValueError):
    """Raised when a decision is submitted against a stale gate checksum."""

    def __init__(
        self,
        expected_checksum: str,
        actual_checksum: str,
        gate_id: str | None = None,
    ) -> None:
        self.expected_checksum = expected_checksum
        self.actual_checksum = actual_checksum
        self.gate_id = gate_id
        detail = (
            f"Gate {gate_id or 'unknown'}: expected checksum "
            f"{expected_checksum[:16]}..., actual {actual_checksum[:16]}..."
        )
        super().__init__(f"{STALE_CHECKSUM_MESSAGE}\n{detail}")
