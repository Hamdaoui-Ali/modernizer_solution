"""Evidence pack builders for governed-stage gates.

Builds bounded, redacted evidence packs from gate-bound artifacts
for assistant explanation in each gate phase:

  - Analysis review (analysis_report, dependency_graph, etc.)
  - Planning review (migration_plan, migration_units, etc.)
  - Approval review (approved analysis + plan refs)
  - Failure/repair review (failed command logs, classification, etc.)

All evidence packs are bounded in size, redacted, and use
gate-bound artifact refs (never stale previews).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.application.redaction import (
    redact_absolute_paths,
    redact_model_summary,
)
from migration_factory.control_tower.application.v2_gate_artifact_resolver import (
    V2GateArtifactResolver,
    ArtifactResolutionResult,
    ResolvedArtifact,
    ResolutionFailureReason,
)
from migration_factory.control_tower.domain.checksums import sha256_canonical_json, utc_now_text
from migration_factory.control_tower.domain.gate_artifact_ref import (
    ArtifactKind,
    GateArtifactRef,
    build_artifact_refs,
    parse_artifact_refs,
)


# ── Evidence pack schema ──────────────────────────────────────────────


@dataclass(frozen=True)
class EvidencePack:
    """A bounded, redacted evidence pack for assistant explanation.

    Fields:
        pack_id: Unique identifier for this pack.
        pack_type: One of 'analysis', 'planning', 'approval', 'failure'.
        gate_id: The gate this pack is bound to.
        gate_phase: The gate phase this evidence was built for.
        summary: Human-readable summary of the evidence.
        artifacts: Resolved and redacted artifact content.
        missing_refs: Refs that could not be resolved.
        checksum_mismatches: Refs with checksum mismatch.
        failure_message: Overall failure message if evidence is incomplete.
        resolved_artifact_count: Number of successfully resolved artifacts.
        total_artifact_count: Total number of artifact refs.
        redaction_status: Status of redaction ('applied', 'not_needed').
        created_at: ISO timestamp of pack creation.
    """

    pack_id: str
    pack_type: str
    gate_id: str
    gate_phase: str
    summary: str
    artifacts: tuple[ResolvedArtifact, ...] = ()
    missing_refs: tuple[str, ...] = ()
    checksum_mismatches: tuple[str, ...] = ()
    failure_message: str | None = None
    resolved_artifact_count: int = 0
    total_artifact_count: int = 0
    redaction_status: str = "applied"
    created_at: str = ""


# ── Budget constants ──────────────────────────────────────────────────

# Max characters per artifact in evidence packs
DEFAULT_EVIDENCE_CHAR_BUDGET = 20_000

# Max characters for the pack summary
DEFAULT_SUMMARY_CHAR_BUDGET = 2_000

# Max characters for the full pack (sum of all artifact contents)
DEFAULT_PACK_CHAR_BUDGET = 100_000


# ── Base builder ─────────────────────────────────────────────────────


class EvidencePackBuilder:
    """Build bounded, redacted evidence packs for gate phases.

    Subclass for phase-specific builders or use directly with
    a pack_type identifier.
    """

    def __init__(
        self,
        resolver: V2GateArtifactResolver,
        evidence_budget: int = DEFAULT_EVIDENCE_CHAR_BUDGET,
        summary_budget: int = DEFAULT_SUMMARY_CHAR_BUDGET,
        pack_budget: int = DEFAULT_PACK_CHAR_BUDGET,
    ) -> None:
        self._resolver = resolver
        self._evidence_budget = evidence_budget
        self._summary_budget = summary_budget
        self._pack_budget = pack_budget

    # ── Public API ─────────────────────────────────────────────────

    def build_analysis_pack(
        self,
        gate_id: str,
        refs: tuple[GateArtifactRef, ...] | None = None,
    ) -> EvidencePack:
        """Build an analysis evidence pack.

        Reads analysis_report, dependency_graph, test_inventory,
        and read_only_verification artifacts.
        """
        return self._build_pack(gate_id, "analysis", "analysis_review", refs)

    def build_planning_pack(
        self,
        gate_id: str,
        refs: tuple[GateArtifactRef, ...] | None = None,
    ) -> EvidencePack:
        """Build a planning evidence pack.

        Reads migration_plan, migration_units, and approval_request
        artifacts.
        """
        return self._build_pack(gate_id, "planning", "planning_review", refs)

    def build_approval_pack(
        self,
        gate_id: str,
        refs: tuple[GateArtifactRef, ...] | None = None,
    ) -> EvidencePack:
        """Build an approval evidence pack.

        Includes accepted analysis and plan refs with checksum proof.
        """
        return self._build_pack(gate_id, "approval", "approval_review", refs)

    def build_failure_pack(
        self,
        gate_id: str,
        refs: tuple[GateArtifactRef, ...] | None = None,
    ) -> EvidencePack:
        """Build a failure/repair evidence pack.

        Reads failed command logs, build logs, test reports,
        and failure classifications.
        """
        return self._build_pack(gate_id, "failure", "repair_review", refs)

    # ── Internal ───────────────────────────────────────────────────

    def _build_pack(
        self,
        gate_id: str,
        pack_type: str,
        gate_phase: str,
        refs: tuple[GateArtifactRef, ...] | None = None,
    ) -> EvidencePack:
        """Build an evidence pack for the given gate phase.

        If *refs* is provided, resolves those refs directly.
        Otherwise, resolves refs from the gate record.
        """
        if refs:
            result = self._resolver.resolve_gate_refs(refs)
        else:
            result = self._resolver.resolve_gate_artifacts(gate_id)

        # Build summary
        summary = self._build_summary(pack_type, result)

        # Truncate artifacts to budget
        artifacts = self._apply_budget(result.artifacts)

        # Track total counts
        total = len(result.artifacts) + len(result.missing_refs) + len(result.checksum_mismatches)

        return EvidencePack(
            pack_id=uuid4().hex[:12],
            pack_type=pack_type,
            gate_id=gate_id,
            gate_phase=gate_phase,
            summary=summary,
            artifacts=artifacts,
            missing_refs=result.missing_refs,
            checksum_mismatches=result.checksum_mismatches,
            failure_message=result.failure_message,
            resolved_artifact_count=len(result.artifacts),
            total_artifact_count=max(total, 1),
            redaction_status="applied",
            created_at=utc_now_text(),
        )

    def _build_summary(
        self,
        pack_type: str,
        result: ArtifactResolutionResult,
    ) -> str:
        """Build a phase-specific summary from resolved artifacts."""
        if result.failure_message and not result.artifacts:
            return f"Evidence could not be loaded: {result.failure_message}"

        artifact_lines: list[str] = []
        for art in result.artifacts:
            lines = art.content.split("\n")[:5]
            snippet = " | ".join(line.strip()[:120] for line in lines if line.strip())
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."

            artifact_lines.append(
                f"- {art.kind}: {snippet}"
            )

        summary_parts = [
            f"Gate phase: {pack_type}",
            f"Resolved artifacts: {len(result.artifacts)}",
            "",
            *artifact_lines,
        ]

        if result.missing_refs:
            summary_parts.append("")
            summary_parts.append(
                f"Missing artifacts: {', '.join(result.missing_refs)}"
            )
        if result.checksum_mismatches:
            summary_parts.append("")
            summary_parts.append(
                f"Checksum mismatches: {', '.join(result.checksum_mismatches)}"
            )

        summary = "\n".join(summary_parts)
        if len(summary) > self._summary_budget:
            summary = summary[:self._summary_budget] + "\n[... summary truncated ...]"

        return summary

    def _apply_budget(
        self,
        artifacts: tuple[ResolvedArtifact, ...],
    ) -> tuple[ResolvedArtifact, ...]:
        """Apply character budget to artifact content."""
        total_chars = 0
        result: list[ResolvedArtifact] = []

        for art in artifacts:
            if total_chars >= self._pack_budget:
                break

            content = art.content
            if len(content) > self._evidence_budget:
                content = content[:self._evidence_budget] + "\n[... content truncated due to evidence budget ...]"
            elif total_chars + len(content) > self._pack_budget:
                remaining = self._pack_budget - total_chars
                if remaining > 100:
                    content = content[:remaining] + "\n[... pack budget limit reached ...]"
                else:
                    break

            total_chars += len(content)
            # Recreate with truncated content
            result.append(ResolvedArtifact(
                kind=art.kind,
                checksum=art.checksum,
                checksum_verified=art.checksum_verified,
                content=content,
                size_bytes=art.size_bytes,
                truncated=art.truncated or len(content) < len(art.content),
            ))

        return tuple(result)


# ── Convenience functions ─────────────────────────────────────────────


def build_analysis_evidence_pack(
    resolver: V2GateArtifactResolver,
    gate_id: str,
) -> EvidencePack:
    """Convenience: build analysis evidence pack."""
    return EvidencePackBuilder(resolver).build_analysis_pack(gate_id)


def build_planning_evidence_pack(
    resolver: V2GateArtifactResolver,
    gate_id: str,
) -> EvidencePack:
    """Convenience: build planning evidence pack."""
    return EvidencePackBuilder(resolver).build_planning_pack(gate_id)


def build_approval_evidence_pack(
    resolver: V2GateArtifactResolver,
    gate_id: str,
) -> EvidencePack:
    """Convenience: build approval evidence pack."""
    return EvidencePackBuilder(resolver).build_approval_pack(gate_id)


def build_failure_evidence_pack(
    resolver: V2GateArtifactResolver,
    gate_id: str,
) -> EvidencePack:
    """Convenience: build failure evidence pack."""
    return EvidencePackBuilder(resolver).build_failure_pack(gate_id)


# ── Pack serialization ────────────────────────────────────────────────


def evidence_pack_to_dict(pack: EvidencePack) -> dict[str, Any]:
    """Convert an EvidencePack to a dict for API/assistant consumption.

    Redacts all sensitive content in the output.
    """
    artifacts_json: list[dict[str, Any]] = []
    for art in pack.artifacts:
        artifacts_json.append({
            "kind": art.kind,
            "checksum_verified": art.checksum_verified,
            "content": redact_absolute_paths(redact_model_summary(art.content)),
            "size_bytes": art.size_bytes,
            "truncated": art.truncated,
        })

    result: dict[str, Any] = {
        "pack_id": pack.pack_id,
        "pack_type": pack.pack_type,
        "gate_id": pack.gate_id,
        "gate_phase": pack.gate_phase,
        "summary": pack.summary,
        "artifacts": artifacts_json,
        "missing_refs": list(pack.missing_refs),
        "checksum_mismatches": list(pack.checksum_mismatches),
        "failure_message": pack.failure_message,
        "resolved_artifact_count": pack.resolved_artifact_count,
        "total_artifact_count": pack.total_artifact_count,
        "redaction_status": pack.redaction_status,
        "created_at": pack.created_at,
    }

    return result
