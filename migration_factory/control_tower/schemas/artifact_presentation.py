"""F1-T7 Artifact presentation contract — defines safe preview and download
behavior through artifact references and checksums.

This contract bridges:
  - ``ArtifactRevision`` — evidence versioning with checksum binding
  - ``PhaseGate`` — gate-bound artifact refs
  - ``GateArtifactRef`` — backend-owned artifact references
  - ``V2GateArtifactResolver`` — backend-owned resolution with redaction
  - FastAPI artifact routes — preview/download endpoints

Design invariants:
  - Never exposes sandbox_path, argv, env, raw commands, provider,
    deployment, or endpoint fields.
  - All artifact resolution is backend-owned — the frontend/chatbot
    only supplies a checkpoint ID and artifact ref.
  - Checksums are always verified before content is returned.
  - Stale and missing artifacts produce sanitized error messages.
  - All content is redacted before returning to the consumer.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from .common import NonEmptyString, StrictModel, require_non_empty_string


# ── Artifact presentation kinds ────────────────────────────────────────

class ArtifactPresentationKind(str, Enum):
    """How an artifact is presented to the user."""
    PREVIEW = "preview"
    DOWNLOAD = "download"


# ── Previewable artifact types ─────────────────────────────────────────
# Artifact types whose content can be rendered inline in the UI.
# These are text-based formats: markdown, yaml, json, plain text, logs.

PREVIEWABLE_ARTIFACT_TYPES: frozenset[str] = frozenset({
    "analysis_report.md",
    "analysis_report.json",
    "analysis_summary.md",
    "dependency_graph.json",
    "test_inventory.yaml",
    "read_only_verification.json",
    "migration_plan.yaml",
    "migration_plan.json",
    "migration_units.yaml",
    "plan_summary.md",
    "plan_validation_report.json",
    "approval_request.json",
    "build_log.txt",
    "test_report.json",
    "test_report.txt",
    "failure_classification.json",
    "repair_proposal.md",
    "orchestration_summary.md",
    "dependency_repair_plan.md",
    "dependency_policy_summary.md",
    "rewrite_impact_summary.json",
    "repair_ledger.json",
    "migration_ledger.json",
})


# ── Downloadable artifact types ────────────────────────────────────────
# Every artifact type is downloadable. This includes binary or large
# artifacts that are NOT previewable. Consumers resolve download by
# referencing the checkpoint's latest_download_artifact_ref.

DOWNLOADABLE_ARTIFACT_TYPES: frozenset[str] = frozenset({
    "analysis_report.md",
    "analysis_report.json",
    "analysis_summary.md",
    "dependency_graph.json",
    "test_inventory.yaml",
    "read_only_verification.json",
    "migration_plan.yaml",
    "migration_plan.json",
    "migration_units.yaml",
    "plan_summary.md",
    "plan_validation_report.json",
    "approval_request.json",
    "build_log.txt",
    "test_report.json",
    "test_report.txt",
    "failure_classification.json",
    "repair_proposal.md",
    "orchestration_summary.md",
    "dependency_repair_plan.md",
    "dependency_policy_summary.md",
    "rewrite_impact_summary.json",
    "rewrite_dry_run.patch",
    "openrewrite_plugin.xml",
    "repair_ledger.json",
    "migration_ledger.json",
    "approved_plan_lock.json",
    "phase2_log.txt",
    "post_transform_test_log.txt",
    "target_dependency_plan.yaml",
    "deterministic_repair_plan.json",
})

# Not all downloadable types are previewable.
assert DOWNLOADABLE_ARTIFACT_TYPES.issuperset(PREVIEWABLE_ARTIFACT_TYPES), (
    "Every previewable type must also be downloadable"
)


# ── Content-Type mapping (for HTTP responses) ─────────────────────────

_ARTIFACT_TYPE_TO_CONTENT_TYPE: dict[str, str] = {
    "md": "text/markdown; charset=utf-8",
    "yaml": "application/x-yaml; charset=utf-8",
    "yml": "application/x-yaml; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "log": "text/plain; charset=utf-8",
    "patch": "text/x-diff; charset=utf-8",
    "xml": "application/xml; charset=utf-8",
}

_DEFAULT_CONTENT_TYPE = "application/octet-stream"


def _infer_content_type(artifact_type: str) -> str:
    """Infer the HTTP Content-Type from an artifact type string.

    Returns application/octet-stream for unknown extensions so
    the consumer can still download the file.
    """
    suffix = artifact_type.rsplit(".", 1)[-1].lower() if "." in artifact_type else ""
    return _ARTIFACT_TYPE_TO_CONTENT_TYPE.get(suffix, _DEFAULT_CONTENT_TYPE)


# ── Artifact resolution state ──────────────────────────────────────────

class ArtifactResolutionState(str, Enum):
    """Outcome of resolving a single artifact ref."""
    AVAILABLE = "available"
    STALE = "stale"
    MISSING = "missing"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    REDACTION_APPLIED = "redaction_applied"


# Terminal resolution states where content should not be returned.
_TERMINAL_RESOLUTION_STATES: frozenset[ArtifactResolutionState] = frozenset({
    ArtifactResolutionState.STALE,
    ArtifactResolutionState.MISSING,
    ArtifactResolutionState.CHECKSUM_MISMATCH,
})


# ── Sanitized error messages ──────────────────────────────────────────

class ArtifactPresentationError(str, Enum):
    """Sanitized user-facing error messages for artifact resolution.

    These never expose absolute paths, secrets, or filesystem structure.
    """
    ARTIFACT_NOT_FOUND = (
        "The requested artifact could not be located in storage. "
        "It may have been moved, deleted, or the checkpoint is no longer valid."
    )
    ARTIFACT_STALE = (
        "The artifact checksum does not match the checkpoint record. "
        "The artifact may have been modified or superseded. "
        "Please request a re-analysis or re-planning if needed."
    )
    CHECKPOINT_NOT_FOUND = (
        "The checkpoint was not found or has been removed."
    )
    CHECKPOINT_NOT_WAITING = (
        "This checkpoint is no longer accepting artifact requests. "
        "It may already be accepted, stopped, or superseded."
    )
    UNAUTHORIZED_ARTIFACT = (
        "You are not authorized to access this artifact."
    )
    CONTENT_REDACTED = (
        "Artifact content has been redacted to remove sensitive information."
    )
    ARTIFACT_TOO_LARGE = (
        "The artifact exceeds the maximum preview size. "
        "Please use the download link instead."
    )


# ── Artifact presentation DTO ──────────────────────────────────────────

class ArtifactPresentationRef(StrictModel):
    """A lightweight reference to a single artifact for preview/download.

    This is the safe DTO returned to the consumer. It never contains
    absolute paths, raw commands, secrets, or sandbox internals.
    """

    artifact_id: str = Field(
        ...,
        description="Unique artifact identifier (artifact ref or revision-based ID).",
    )
    artifact_type: str = Field(
        ...,
        description="Artifact type (e.g., migration_plan.yaml, analysis_report.md).",
        max_length=256,
    )
    presentation_kind: ArtifactPresentationKind = Field(
        ...,
        description="Whether this artifact is previewable or downloadable.",
    )
    content_type: str = Field(
        ...,
        description="HTTP Content-Type for the artifact.",
    )
    checksum: str = Field(
        ...,
        description="SHA-256 hex digest of the artifact content.",
        min_length=8,
        max_length=128,
    )
    state: ArtifactResolutionState = Field(
        default=ArtifactResolutionState.AVAILABLE,
        description="Current resolution state of the artifact.",
    )
    size_bytes: int | None = Field(
        default=None,
        description="Artifact size in bytes, if known.",
    )

    # ── validation ─────────────────────────────────────────────────

    @field_validator("artifact_id", "artifact_type", "checksum")
    @classmethod
    def _non_empty_strings(cls, v: str, info: Any) -> str:
        return require_non_empty_string(v, info.field_name)

    @field_validator("content_type")
    @classmethod
    def _coerce_or_default_content_type(cls, v: str) -> str:
        if not v or not v.strip():
            return _DEFAULT_CONTENT_TYPE
        return v.strip()

    @model_validator(mode="after")
    def _previewable_must_be_in_allowlist(self) -> "ArtifactPresentationRef":
        if self.presentation_kind == ArtifactPresentationKind.PREVIEW:
            if self.artifact_type not in PREVIEWABLE_ARTIFACT_TYPES:
                raise ValueError(
                    f"Artifact type {self.artifact_type!r} is not previewable"
                )
        return self

    @model_validator(mode="after")
    def _downloadable_must_be_in_allowlist(self) -> "ArtifactPresentationRef":
        if self.presentation_kind == ArtifactPresentationKind.DOWNLOAD:
            if self.artifact_type not in DOWNLOADABLE_ARTIFACT_TYPES:
                raise ValueError(
                    f"Artifact type {self.artifact_type!r} is not downloadable"
                )
        return self

    @property
    def is_available(self) -> bool:
        """True when the artifact can be returned to the consumer."""
        return self.state not in _TERMINAL_RESOLUTION_STATES

    @property
    def sanitized_error(self) -> str | None:
        """Return a sanitized error message if the artifact is not available."""
        if self.state == ArtifactResolutionState.MISSING:
            return ArtifactPresentationError.ARTIFACT_NOT_FOUND.value
        if self.state == ArtifactResolutionState.STALE:
            return ArtifactPresentationError.ARTIFACT_STALE.value
        if self.state == ArtifactResolutionState.CHECKSUM_MISMATCH:
            return ArtifactPresentationError.ARTIFACT_STALE.value
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a safe dictionary for API responses."""
        result: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "presentation_kind": self.presentation_kind.value,
            "content_type": self.content_type,
            "checksum": self.checksum,
            "state": self.state.value,
        }
        if self.size_bytes is not None:
            result["size_bytes"] = self.size_bytes
        error = self.sanitized_error
        if error is not None:
            result["error"] = error
        return result

    def to_json(self) -> str:
        """Serialize to compact JSON."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactPresentationRef":
        """Deserialize from a dictionary (e.g., from a stored checkpoint).

        Database NULL columns appear as present-but-None keys.
        We guard every extraction with ``is not None`` before casting.
        """
        kind_raw = data.get("presentation_kind", "download")
        if kind_raw is None:
            kind_raw = "download"
        kind = (
            ArtifactPresentationKind(kind_raw)
            if isinstance(kind_raw, str)
            else kind_raw
        )

        state_raw = data.get("state", "available")
        if state_raw is None:
            state_raw = "available"
        state = (
            ArtifactResolutionState(state_raw)
            if isinstance(state_raw, str)
            else state_raw
        )

        _artifact_id = data.get("artifact_id", "")
        artifact_id = str(_artifact_id) if _artifact_id is not None else ""

        _artifact_type = data.get("artifact_type", "")
        artifact_type = str(_artifact_type) if _artifact_type is not None else ""

        _content_type = data.get("content_type", _DEFAULT_CONTENT_TYPE)
        content_type = str(_content_type) if _content_type is not None else _DEFAULT_CONTENT_TYPE

        _checksum = data.get("checksum", "")
        checksum = str(_checksum) if _checksum is not None else ""

        return cls(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            presentation_kind=kind,
            content_type=content_type,
            checksum=checksum,
            state=state,
            size_bytes=data.get("size_bytes"),
        )


# ── Artifact presentation batch response ──────────────────────────────

class ArtifactPresentationBatch(StrictModel):
    """A batch of artifact presentation refs for a single checkpoint.

    Returned by the GET /checkpoints/{id}/artifacts endpoint.
    """

    checkpoint_id: str = Field(
        ...,
        description="Checkpoint ID that owns these artifacts.",
    )
    job_id: str = Field(
        ...,
        description="Migration job ID.",
    )
    artifacts: tuple[ArtifactPresentationRef, ...] = Field(
        default_factory=tuple,
        description="Artifact presentation refs bound to this checkpoint.",
    )
    gate_id: str = Field(
        "",
        description="Phase gate ID that bound these artifacts.",
    )
    gate_checksum: str = Field(
        "",
        description="Checksum of the gate record for integrity verification.",
    )

    @field_validator("checkpoint_id", "job_id")
    @classmethod
    def _non_empty_strings(cls, v: str, info: Any) -> str:
        return require_non_empty_string(v, info.field_name)

    @property
    def available_count(self) -> int:
        """Number of artifacts currently available."""
        return sum(1 for a in self.artifacts if a.is_available)

    @property
    def previewable_count(self) -> int:
        """Number of previewable artifacts (regardless of state)."""
        return sum(
            1 for a in self.artifacts
            if a.presentation_kind == ArtifactPresentationKind.PREVIEW
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a safe dictionary."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "job_id": self.job_id,
            "gate_id": self.gate_id,
            "gate_checksum": self.gate_checksum,
            "available_count": self.available_count,
            "previewable_count": self.previewable_count,
            "artifacts": [a.to_dict() for a in self.artifacts],
        }

    def to_json(self) -> str:
        """Serialize to compact JSON."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactPresentationBatch":
        """Deserialize from a dictionary.

        Database NULL columns appear as present-but-None keys.
        We guard every extraction with ``is not None`` before casting.
        """
        artifacts_raw = data.get("artifacts", [])
        artifacts: list[ArtifactPresentationRef] = []
        if isinstance(artifacts_raw, list):
            for item in artifacts_raw:
                if isinstance(item, dict):
                    artifacts.append(ArtifactPresentationRef.from_dict(item))

        _checkpoint_id = data.get("checkpoint_id", "")
        checkpoint_id = str(_checkpoint_id) if _checkpoint_id is not None else ""

        _job_id = data.get("job_id", "")
        job_id = str(_job_id) if _job_id is not None else ""

        _gate_id = data.get("gate_id", "")
        gate_id = str(_gate_id) if _gate_id is not None else ""

        _gate_checksum = data.get("gate_checksum", "")
        gate_checksum = str(_gate_checksum) if _gate_checksum is not None else ""

        return cls(
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            artifacts=tuple(artifacts),
            gate_id=gate_id,
            gate_checksum=gate_checksum,
        )


# ── Safe artifact presentation fields ──────────────────────────────────

ARTIFACT_PRESENTATION_FIELDS: frozenset[str] = frozenset({
    "artifact_id",
    "artifact_type",
    "presentation_kind",
    "content_type",
    "checksum",
    "state",
    "size_bytes",
    "checkpoint_id",
    "job_id",
    "gate_id",
    "gate_checksum",
    "available_count",
    "previewable_count",
    "artifacts",
})

# ── Dangerous fields enforcement ──────────────────────────────────────

_DANGEROUS_FIELDS: frozenset[str] = frozenset({
    "sandbox_path",
    "argv",
    "env",
    "raw_command",
    "absolute_path",
    "filesystem_target",
    "provider",
    "deployment",
    "endpoint",
    "env_ref",
    "secret",
    "password",
    "token",
    "api_key",
    "authorization_header",
})

assert ARTIFACT_PRESENTATION_FIELDS.isdisjoint(_DANGEROUS_FIELDS), (
    "ARTIFACT_PRESENTATION_FIELDS must not contain dangerous fields"
)


# ── Redaction rules ───────────────────────────────────────────────────

ARTIFACT_REDACTION_RULES: frozenset[str] = frozenset({
    "redact_absolute_paths",
    "redact_env_vars",
    "redact_secrets",
    "redact_model_summary",
    "redact_sandbox_paths",
    "redact_command_argv",
    "redact_endpoint_urls",
    "redact_provider_names",
    "truncate_to_max_size",
    "strip_ansi_control_chars",
})

REDACTED_PLACEHOLDER: str = "[REDACTED]"
TRUNCATION_MARKER: str = "\n\n[... content truncated for preview ...]"

# Maximum preview content size in characters (32 KB).
MAX_PREVIEW_CONTENT_CHARS: int = 32_768


# ── Validation helpers ─────────────────────────────────────────────────

def is_previewable(artifact_type: str) -> bool:
    """Return True if *artifact_type* can be previewed inline in the UI."""
    return artifact_type in PREVIEWABLE_ARTIFACT_TYPES


def is_downloadable(artifact_type: str) -> bool:
    """Return True if *artifact_type* can be downloaded."""
    return artifact_type in DOWNLOADABLE_ARTIFACT_TYPES


def get_content_type(artifact_type: str) -> str:
    """Return the HTTP Content-Type for an artifact type."""
    return _infer_content_type(artifact_type)


def validate_artifact_presentation_kind(
    artifact_type: str,
    kind: ArtifactPresentationKind,
) -> bool:
    """Return True if *artifact_type* supports *kind* of presentation.

    Every artifact is downloadable; only a subset is previewable.
    """
    if kind == ArtifactPresentationKind.PREVIEW:
        return is_previewable(artifact_type)
    if kind == ArtifactPresentationKind.DOWNLOAD:
        return is_downloadable(artifact_type)
    return False


def build_presentation_ref(
    artifact_id: str,
    artifact_type: str,
    checksum: str,
    *,
    kind: ArtifactPresentationKind | None = None,
    state: ArtifactResolutionState = ArtifactResolutionState.AVAILABLE,
    size_bytes: int | None = None,
) -> ArtifactPresentationRef:
    """Build a safe ArtifactPresentationRef from raw fields.

    Auto-detects presentation kind if not provided.
    """
    if kind is None:
        kind = (
            ArtifactPresentationKind.PREVIEW
            if is_previewable(artifact_type)
            else ArtifactPresentationKind.DOWNLOAD
        )
    content_type = get_content_type(artifact_type)
    return ArtifactPresentationRef(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        presentation_kind=kind,
        content_type=content_type,
        checksum=checksum,
        state=state,
        size_bytes=size_bytes,
    )
