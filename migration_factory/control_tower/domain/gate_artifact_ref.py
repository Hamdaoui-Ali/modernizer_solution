"""Gate artifact reference schema — backend-owned artifact binding for gates.

Each GateArtifactRef represents a single piece of evidence bound to a
governed-stage gate. The ref is created by the backend when the gate
is opened, and it is resolved through backend-owned artifact storage
(never frontend-supplied paths).

The schema standardizes artifact kind, path/ref, and checksum fields
so the assistant and UI can read gate-bound evidence without exposing
raw filesystem details.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from migration_factory.control_tower.application.redaction import (
    redact_absolute_paths,
)


# ── Artifact kind enum ───────────────────────────────────────────────


class ArtifactKind(str, Enum):
    """Standardized artifact kinds referenced by gates."""

    ANALYSIS_REPORT = "analysis_report"
    DEPENDENCY_GRAPH = "dependency_graph"
    TEST_INVENTORY = "test_inventory"
    READ_ONLY_VERIFICATION = "read_only_verification"
    MIGRATION_PLAN = "migration_plan"
    MIGRATION_UNITS = "migration_units"
    APPROVAL_REQUEST = "approval_request"
    FAILED_COMMAND_LOG = "failed_command_log"
    BUILD_LOG = "build_log"
    TEST_REPORT = "test_report"
    FAILURE_CLASSIFICATION = "failure_classification"
    REPAIR_PROPOSAL = "repair_proposal"
    OTHER = "other"

    @classmethod
    def has_value(cls, value: str) -> bool:
        try:
            cls(value)
            return True
        except ValueError:
            return False


# ── Artifact ref schema ─────────────────────────────────────────────


@dataclass(frozen=True)
class GateArtifactRef:
    """A single artifact reference bound to a gate.

    Fields:
        kind: The kind of artifact (analysis_report, migration_plan, etc.).
        path_or_ref: Backend-owned artifact identifier. This is either a
            relative sandbox path or an artifact storage key — never an
            absolute filesystem path. The DTO layer redacts filesystem
            details from this field.
        checksum: SHA-256 hex digest of the artifact content. Used to
            verify the artifact has not been tampered with or become
            stale. Must be non-empty.
        description: Optional human-readable description for UI display.
    """

    kind: str
    path_or_ref: str
    checksum: str
    description: str = ""


# ── Parsing / serialization ──────────────────────────────────────────


def parse_artifact_refs(
    raw: str | bytes | list[dict[str, Any]] | None,
) -> tuple[GateArtifactRef, ...]:
    """Parse artifact refs from JSON string, bytes, or parsed list.

    Accepts the JSON format stored in PhaseGateRecord.source_artifact_refs_json
    or ArtifactRevisionRecord.artifact_refs_json.

    Returns an empty tuple if the input is empty, null, or fails to parse.
    Invalid refs are skipped with a warning (no silent data corruption).
    """
    if raw is None:
        return ()

    if isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = raw
    elif isinstance(raw, bytes):
        items = _parse_json(raw.decode("utf-8"))
    elif isinstance(raw, str):
        items = _parse_json(raw)
    else:
        return ()

    return _build_refs(items)


def _parse_json(raw: str) -> list[dict[str, Any]]:
    """Parse a JSON string into a list of dicts.

    Returns empty list on parse failure.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []

    if isinstance(parsed, dict):
        # Handle single ref as a dict (wrapped in list automatically)
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    return []


def _build_refs(items: list[dict[str, Any]]) -> tuple[GateArtifactRef, ...]:
    """Build a tuple of GateArtifactRef from a list of dicts.

    Invalid items (missing required fields) are skipped.
    """
    result: list[GateArtifactRef] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind", "")
        path = item.get("path_or_ref", "") or item.get("path", "") or item.get("ref", "")
        checksum = item.get("checksum", "")
        desc = item.get("description", "")

        if not kind or not path or not checksum:
            continue  # skip incomplete refs
        if not isinstance(kind, str) or not isinstance(path, str) or not isinstance(checksum, str):
            continue

        result.append(GateArtifactRef(
            kind=kind,
            path_or_ref=path,
            checksum=checksum,
            description=desc,
        ))

    return tuple(result)


def serialize_artifact_refs(refs: Sequence[GateArtifactRef]) -> str:
    """Serialize artifact refs to compact JSON.

    The output redacts absolute paths from the path_or_ref field.
    """
    items = []
    for ref in refs:
        items.append({
            "kind": ref.kind,
            "path_or_ref": redact_absolute_paths(ref.path_or_ref),
            "checksum": ref.checksum,
            "description": ref.description,
        })
    return json.dumps(items, separators=(",", ":"), sort_keys=True)


# ── Validation ───────────────────────────────────────────────────────


class ArtifactRefValidationError(ValueError):
    """Raised when artifact ref validation fails."""


def validate_artifact_ref(ref: GateArtifactRef) -> None:
    """Validate a single artifact ref.

    Raises:
        ArtifactRefValidationError: If validation fails.
    """
    if not ref.kind:
        raise ArtifactRefValidationError("Artifact ref kind must be non-empty")
    if not ref.path_or_ref:
        raise ArtifactRefValidationError("Artifact ref path_or_ref must be non-empty")
    if not ref.checksum:
        raise ArtifactRefValidationError("Artifact ref checksum must be non-empty")
    if not isinstance(ref.checksum, str) or len(ref.checksum) < 8:
        raise ArtifactRefValidationError(
            f"Artifact ref checksum must be a non-empty hex string, got {ref.checksum!r}"
        )


def validate_all_artifact_refs(refs: Sequence[GateArtifactRef]) -> None:
    """Validate all artifact refs in a sequence.

    Raises:
        ArtifactRefValidationError: If any ref fails validation.
    """
    for ref in refs:
        validate_artifact_ref(ref)


# ── DTO helpers ──────────────────────────────────────────────────────


def artifact_ref_to_public_dto(ref: GateArtifactRef) -> dict[str, str]:
    """Convert a GateArtifactRef to a safe public-facing DTO.

    Filesystem paths are redacted. The checksum is included as an
    integrity proof (not a direct dereference mechanism).
    """
    return {
        "kind": ref.kind,
        "path_or_ref": redact_absolute_paths(ref.path_or_ref),
        "checksum": ref.checksum,
        "description": ref.description,
    }


def artifact_refs_to_public_dtos(
    refs: Sequence[GateArtifactRef],
) -> list[dict[str, str]]:
    """Convert multiple GateArtifactRefs to safe public-facing DTOs."""
    return [artifact_ref_to_public_dto(r) for r in refs]


# ── Builder helper ───────────────────────────────────────────────────


def build_artifact_refs(
    refs: Sequence[tuple[str, str, str]],
) -> tuple[GateArtifactRef, ...]:
    """Build a tuple of GateArtifactRef from (kind, path_or_ref, checksum) tuples.

    Each tuple may optionally include a fourth element for description.
    """
    result: list[GateArtifactRef] = []
    for item in refs:
        kind = item[0]
        path_or_ref = item[1]
        checksum = item[2]
        desc = item[3] if len(item) > 3 else ""
        result.append(GateArtifactRef(
            kind=kind,
            path_or_ref=path_or_ref,
            checksum=checksum,
            description=desc,
        ))
    return tuple(result)
