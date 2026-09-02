"""F3-T6 Profile checkpoint metadata — defines how source/target profile
choices are persisted on artifacts and checkpoints.

This module provides the canonical metadata structure that must appear
in checkpoint artifacts, artifact revisions, and any persistence layer
that records profile-based migration routing decisions.

The metadata captures:
  - Selected source and target profiles
  - Derived stage route (included, excluded, skipped stages)
  - Validation outcome and reason
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from .common import StrictModel


# ── Safe checkpoint fields ──────────────────────────────────────────

# Fields that are safe to include in checkpoint/artifact metadata.
# These are profile-routing metadata fields — NOT provider, model,
# env, sandbox, or command fields.
PROFILE_CHECKPOINT_FIELDS: frozenset[str] = frozenset({
    "source_profile",
    "target_profile",
    "source_level",
    "target_level",
    "included_stages",
    "excluded_stages",
    "skipped_stages",
    "skipped_stage_ledger",
    "route_steps",
    "route_step_index",
    "runtime_profile",
    "catalog",
    "execution_jdk",
    "approval_gate_id",
    "artifact_refs",
    "evidence_refs",
    "valid",
    "reason",
    "source_profile_detection_ref",
    "source_profile_detection_checksum",
    "source_profile_detection_confidence",
    "source_profile_detection_uncertainty_notes",
})


# ── CheckpointProfileMetadata ─────────────────────────────────────────

class SkippedStageLedgerEntry(StrictModel):
    """Safe audit entry for a stage skipped by source-profile routing."""

    job_id: str = ""
    source_profile: str = ""
    target_profile: str = ""
    skipped_stage_index: int = Field(ge=1)
    skipped_stage_name: str = ""
    skipped_stage_profile: str = ""
    reason: str = ""
    evidence_ref: str = ""
    evidence_checksum: str = ""
    route_checksum: str = ""
    artifact_checksum: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_profile": self.source_profile,
            "target_profile": self.target_profile,
            "skipped_stage_index": self.skipped_stage_index,
            "skipped_stage_name": self.skipped_stage_name,
            "skipped_stage_profile": self.skipped_stage_profile,
            "reason": self.reason,
            "evidence_ref": self.evidence_ref,
            "evidence_checksum": self.evidence_checksum,
            "route_checksum": self.route_checksum,
            "artifact_checksum": self.artifact_checksum,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkippedStageLedgerEntry":
        return cls(
            job_id=str(data.get("job_id") or ""),
            source_profile=str(data.get("source_profile") or ""),
            target_profile=str(data.get("target_profile") or ""),
            skipped_stage_index=int(data.get("skipped_stage_index") or 1),
            skipped_stage_name=str(data.get("skipped_stage_name") or ""),
            skipped_stage_profile=str(data.get("skipped_stage_profile") or ""),
            reason=str(data.get("reason") or ""),
            evidence_ref=str(data.get("evidence_ref") or ""),
            evidence_checksum=str(data.get("evidence_checksum") or ""),
            route_checksum=str(data.get("route_checksum") or ""),
            artifact_checksum=str(data.get("artifact_checksum") or ""),
            created_at=str(data.get("created_at") or ""),
        )


class RouteStepCheckpointMetadata(StrictModel):
    """Safe route-step checkpoint metadata for cockpit and audit projections."""

    route_step_index: int = Field(ge=1)
    stage_index: int = Field(ge=1)
    source_profile: str = ""
    target_profile: str = ""
    runtime_profile: str = ""
    catalog: str = ""
    execution_jdk: str = ""
    status: str = "pending"
    approval_gate_id: str = ""
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_step_index": self.route_step_index,
            "stage_index": self.stage_index,
            "source_profile": self.source_profile,
            "target_profile": self.target_profile,
            "runtime_profile": self.runtime_profile,
            "catalog": self.catalog,
            "execution_jdk": self.execution_jdk,
            "status": self.status,
            "approval_gate_id": self.approval_gate_id,
            "artifact_refs": list(self.artifact_refs),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouteStepCheckpointMetadata":
        return cls(
            route_step_index=int(data.get("route_step_index") or 1),
            stage_index=int(data.get("stage_index") or 1),
            source_profile=str(data.get("source_profile") or ""),
            target_profile=str(data.get("target_profile") or ""),
            runtime_profile=str(data.get("runtime_profile") or ""),
            catalog=str(data.get("catalog") or ""),
            execution_jdk=str(data.get("execution_jdk") or ""),
            status=str(data.get("status") or "pending"),
            approval_gate_id=str(data.get("approval_gate_id") or ""),
            artifact_refs=tuple(
                str(ref) for ref in (data.get("artifact_refs") or ()) if str(ref).strip()
            ) if isinstance(data.get("artifact_refs"), (list, tuple)) else (),
            evidence_refs=tuple(
                str(ref) for ref in (data.get("evidence_refs") or ()) if str(ref).strip()
            ) if isinstance(data.get("evidence_refs"), (list, tuple)) else (),
        )


class CheckpointProfileMetadata(StrictModel):
    """Profile routing metadata persisted on artifacts and checkpoints.

    Captures the source/target profile selection and the resulting
    stage route decisions. This metadata is stored alongside artifact
    revisions so that downstream consumers (audit, resume, reporting)
    can determine which profile route was in effect.

    Design invariants:
      - All fields have safe defaults (empty strings, -1, empty tuples).
      - Never exposes provider, model, deployment, sandbox_path, argv,
        env, or raw command fields.
      - JSON-serializable via to_dict()/to_json() for artifact storage.
    """

    source_profile: str = ""
    target_profile: str = ""
    source_level: int = Field(default=-1, ge=-1)
    target_level: int = Field(default=-1, ge=-1)
    included_stages: tuple[int, ...] = Field(default_factory=tuple)
    excluded_stages: tuple[int, ...] = Field(default_factory=tuple)
    skipped_stages: tuple[int, ...] = Field(default_factory=tuple)
    skipped_stage_ledger: tuple[SkippedStageLedgerEntry, ...] = Field(default_factory=tuple)
    route_steps: tuple[RouteStepCheckpointMetadata, ...] = Field(default_factory=tuple)
    valid: bool = False
    reason: str = ""
    source_profile_detection_ref: str = ""
    source_profile_detection_checksum: str = ""
    source_profile_detection_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_profile_detection_uncertainty_notes: tuple[str, ...] = Field(default_factory=tuple)

    # ── Serialization ───────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON storage or API responses."""
        return {
            "source_profile": self.source_profile,
            "target_profile": self.target_profile,
            "source_level": self.source_level,
            "target_level": self.target_level,
            "included_stages": list(self.included_stages),
            "excluded_stages": list(self.excluded_stages),
            "skipped_stages": list(self.skipped_stages),
            "skipped_stage_ledger": [
                entry.to_dict() for entry in self.skipped_stage_ledger
            ],
            "route_steps": [entry.to_dict() for entry in self.route_steps],
            "valid": self.valid,
            "reason": self.reason,
            "source_profile_detection_ref": self.source_profile_detection_ref,
            "source_profile_detection_checksum": self.source_profile_detection_checksum,
            "source_profile_detection_confidence": self.source_profile_detection_confidence,
            "source_profile_detection_uncertainty_notes": list(
                self.source_profile_detection_uncertainty_notes
            ),
        }

    def to_json(self) -> str:
        """Serialize to a JSON string for artifact_refs_json storage."""
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CheckpointProfileMetadata":
        """Deserialize from a plain dict.

        Guards against None values because dict[str, Any] callers may
        pass keys with None values (e.g. from database NULL columns).
        absent keys fall back to safe defaults; present-but-None keys
        are also treated as absent for safety.
        """
        sp = data.get("source_profile")
        tp = data.get("target_profile")
        sl = data.get("source_level")
        tl = data.get("target_level")
        inc = data.get("included_stages")
        exc = data.get("excluded_stages")
        skp = data.get("skipped_stages")
        ledger = data.get("skipped_stage_ledger")
        route_steps = data.get("route_steps")
        v = data.get("valid")
        r = data.get("reason")
        detection_ref = data.get("source_profile_detection_ref")
        detection_checksum = data.get("source_profile_detection_checksum")
        detection_confidence = data.get("source_profile_detection_confidence")
        detection_notes = data.get("source_profile_detection_uncertainty_notes")

        return cls(
            source_profile=str(sp) if sp is not None else "",
            target_profile=str(tp) if tp is not None else "",
            source_level=int(sl) if sl is not None else -1,
            target_level=int(tl) if tl is not None else -1,
            included_stages=(
                tuple(int(s) for s in inc) if inc is not None else ()
            ),
            excluded_stages=(
                tuple(int(s) for s in exc) if exc is not None else ()
            ),
            skipped_stages=(
                tuple(int(s) for s in skp) if skp is not None else ()
            ),
            skipped_stage_ledger=(
                tuple(
                    item
                    if isinstance(item, SkippedStageLedgerEntry)
                    else SkippedStageLedgerEntry.from_dict(item)
                    for item in ledger
                    if isinstance(item, (dict, SkippedStageLedgerEntry))
                )
                if ledger is not None
                else ()
            ),
            route_steps=(
                tuple(
                    item
                    if isinstance(item, RouteStepCheckpointMetadata)
                    else RouteStepCheckpointMetadata.from_dict(item)
                    for item in route_steps
                    if isinstance(item, (dict, RouteStepCheckpointMetadata))
                )
                if route_steps is not None
                else ()
            ),
            valid=bool(v) if v is not None else False,
            reason=str(r) if r is not None else "",
            source_profile_detection_ref=(
                str(detection_ref) if detection_ref is not None else ""
            ),
            source_profile_detection_checksum=(
                str(detection_checksum) if detection_checksum is not None else ""
            ),
            source_profile_detection_confidence=(
                float(detection_confidence)
                if detection_confidence is not None
                else None
            ),
            source_profile_detection_uncertainty_notes=(
                tuple(str(note) for note in detection_notes)
                if detection_notes is not None
                else ()
            ),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "CheckpointProfileMetadata":
        """Deserialize from a JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    @classmethod
    def from_profile_route(
        cls,
        route: Any,
        *,
        skipped_stage_ledger: tuple[SkippedStageLedgerEntry, ...] = (),
    ) -> "CheckpointProfileMetadata":
        """Create from a ProfileRoute (from v2_stage_progression).

        Uses getattr to avoid a hard import dependency on the
        application-layer ProfileRoute dataclass.
        """
        return cls(
            source_profile=getattr(route, "source_profile", ""),
            target_profile=getattr(route, "target_profile", ""),
            source_level=getattr(route, "source_level", -1),
            target_level=getattr(route, "target_level", -1),
            included_stages=getattr(route, "included_stages", ()),
            excluded_stages=getattr(route, "excluded_stages", ()),
            skipped_stages=getattr(route, "skipped_stages", ()),
            skipped_stage_ledger=skipped_stage_ledger,
            route_steps=tuple(
                RouteStepCheckpointMetadata.from_dict(step)
                if isinstance(step, dict)
                else RouteStepCheckpointMetadata(
                    route_step_index=getattr(step, "route_step_index", 1),
                    stage_index=getattr(step, "stage_index", 1),
                    source_profile=getattr(step, "source_profile", ""),
                    target_profile=getattr(step, "target_profile", ""),
                    runtime_profile=getattr(step, "runtime_profile", ""),
                    catalog=getattr(step, "catalog", ""),
                    execution_jdk=getattr(step, "execution_jdk", ""),
                    status=getattr(step, "status", "pending"),
                    approval_gate_id=getattr(step, "approval_gate_id", ""),
                    artifact_refs=tuple(getattr(step, "artifact_refs", ()) or ()),
                    evidence_refs=tuple(getattr(step, "evidence_refs", ()) or ()),
                )
                for step in getattr(route, "route_steps", ())
            ),
            valid=getattr(route, "valid", False),
            reason=getattr(route, "reason", ""),
        )

    def with_source_profile_detection(
        self,
        detection: Any,
    ) -> "CheckpointProfileMetadata":
        """Attach safe source-profile detection metadata."""

        return self.model_copy(update={
            "source_profile_detection_ref": getattr(detection, "artifact_ref", ""),
            "source_profile_detection_checksum": getattr(detection, "artifact_checksum", ""),
            "source_profile_detection_confidence": getattr(detection, "confidence", None),
            "source_profile_detection_uncertainty_notes": tuple(
                getattr(detection, "uncertainty_notes", ())
            ),
        })

    # ── Derived properties ──────────────────────────────────────────

    @property
    def has_profiles(self) -> bool:
        """True if both source and target profiles are specified."""
        return bool(self.source_profile and self.target_profile)

    @property
    def stage_count(self) -> int:
        """Number of included stages in the route."""
        return len(self.included_stages)

    @property
    def is_no_op(self) -> bool:
        """True when source equals target (no migration stages needed)."""
        return (
            self.has_profiles
            and self.source_profile == self.target_profile
        )
