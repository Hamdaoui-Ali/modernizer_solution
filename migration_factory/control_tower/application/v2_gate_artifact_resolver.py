"""V2 Gate Artifact Resolver — backend-owned artifact resolution for gates.

Resolves gate-bound artifact references from backend-owned storage,
verifies checksums, and returns redacted content. Uses existing
artifact path validation (artifact_paths.py) for safe path resolution.

The resolver never accepts frontend-supplied paths, commands, env,
or sandbox arguments. All resolution uses gate-bound refs stored
at gate creation time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from migration_factory.control_tower.application.redaction import (
    redact_absolute_paths,
    redact_model_summary,
    SENSITIVE_ENV_VARS,
)
from migration_factory.control_tower.domain.checksums import (
    sha256_hex,
    stream_sha256,
)
from migration_factory.control_tower.domain.gate_artifact_ref import GateArtifactRef
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)


# ── Resolution failure reasons ────────────────────────────────────────


class ResolutionFailureReason(str):
    """Canonical sanitized failure reasons for artifact resolution.

    These messages are safe to return to the assistant or UI.
    They do not leak absolute paths, secrets, or filesystem structure.
    """

    GATE_NOT_FOUND = "Gate was not found or has been removed."
    GATE_NOT_OPEN = "Gate is resolved or superseded and its artifacts are unavailable."
    NO_ARTIFACT_REFS = "Gate has no bound artifact references."
    ARTIFACT_NOT_FOUND = "One or more artifacts could not be located in storage."
    CHECKSUM_MISMATCH = "One or more artifact checksums do not match the gate record."
    READ_ERROR = "An error occurred while reading artifact content."
    STALE_REFERENCE = "Artifact reference is stale and no longer resolves correctly."


# ── Result types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedArtifact:
    """Result of resolving a single gate-bound artifact ref.

    Content is always redacted for assistant/UI consumption.
    """

    kind: str
    checksum: str
    checksum_verified: bool
    content: str
    size_bytes: int | None = None
    truncated: bool = False


@dataclass(frozen=True)
class ArtifactResolutionResult:
    """Result of resolving all artifact refs bound to a gate.

    If resolution fails entirely, *artifacts* will be empty and
    *failure_message* will contain a sanitized reason.

    Partial failures return successfully resolved artifacts in
    *artifacts* and report failures through *missing_refs* and
    *checksum_mismatches*.
    """

    gate_id: str
    artifacts: tuple[ResolvedArtifact, ...]
    missing_refs: tuple[str, ...] = ()
    checksum_mismatches: tuple[str, ...] = ()
    failure_message: str | None = None
    resolution_id: str = ""


# ── Content budget ────────────────────────────────────────────────────

# Default maximum content size per artifact (in characters)
_DEFAULT_MAX_CONTENT_CHARS = 100_000

# Default maximum content size per artifact when truncated for
# assistant context (in characters)
_DEFAULT_MAX_ASSISTANT_CONTENT_CHARS = 20_000


def _artifact_label(value: str, *, default: str = "artifact") -> str:
    """Return a short public label for a possibly absolute artifact ref."""
    text = str(value or "").strip()
    if not text:
        return default
    normalized = text.replace("\\", "/").rstrip("/")
    if not normalized:
        return default
    label = normalized.split("/")[-1].strip()
    if not label:
        return default
    if label.endswith(":") and len(label) <= 2:
        return default
    return label


# ── Resolver ──────────────────────────────────────────────────────────


class V2GateArtifactResolver:
    """Backend-owned artifact resolver for governed-stage gates.

    Resolves gate-bound artifact refs from backend-controlled storage
    paths, verifying checksums before returning content. All output
    is redacted for safe consumption by the assistant and UI.

    The resolver uses a *storage_root* — a backend-owned base directory
    under which all artifact content resides. This is never a
    frontend-supplied path.
    """

    def __init__(
        self,
        gate_repo: SqlitePhaseGateRepository,
        storage_root: str | Path | None = None,
        max_content_chars: int = _DEFAULT_MAX_CONTENT_CHARS,
    ) -> None:
        self._gate_repo = gate_repo
        self._storage_root = Path(storage_root).resolve() if storage_root else None
        self._max_content_chars = max_content_chars

    # ── Public API ─────────────────────────────────────────────────

    def resolve_gate_artifacts(
        self,
        gate_id: str,
    ) -> ArtifactResolutionResult:
        """Resolve all artifact refs bound to the given gate.

        Returns a resolution result with redacted content. Never
        raises — all failures are returned as structured results
        with sanitized messages.
        """
        gate = self._gate_repo.get(gate_id)
        if gate is None:
            return ArtifactResolutionResult(
                gate_id=gate_id,
                artifacts=(),
                failure_message=ResolutionFailureReason.GATE_NOT_FOUND,
                resolution_id=uuid4().hex[:12],
            )

        refs = self._parse_gate_artifact_entries(gate.source_artifact_refs_json)

        if not refs:
            return ArtifactResolutionResult(
                gate_id=gate_id,
                artifacts=(),
                failure_message=ResolutionFailureReason.NO_ARTIFACT_REFS,
                resolution_id=uuid4().hex[:12],
            )

        # Resolve each ref
        resolved: list[ResolvedArtifact] = []
        missing: list[str] = []
        mismatches: list[str] = []

        for kind, path_or_ref, expected_checksum, description in refs:
            result = self._resolve_single_artifact(
                kind=kind,
                path_or_ref=path_or_ref,
                expected_checksum=expected_checksum,
                description=description,
            )
            if result is None:
                missing.append(kind)
            elif not result.checksum_verified:
                mismatches.append(kind)
            else:
                resolved.append(result)

        if not resolved and (missing or mismatches):
            # All refs failed — return overall failure
            if mismatches and not missing:
                msg = ResolutionFailureReason.CHECKSUM_MISMATCH
            elif missing and not mismatches:
                msg = ResolutionFailureReason.ARTIFACT_NOT_FOUND
            else:
                msg = "One or more artifacts are missing or have checksum mismatches."

            return ArtifactResolutionResult(
                gate_id=gate_id,
                artifacts=(),
                missing_refs=tuple(missing),
                checksum_mismatches=tuple(mismatches),
                failure_message=msg,
                resolution_id=uuid4().hex[:12],
            )

        # Partial success
        if missing or mismatches:
            msg = (
                "Some artifacts could not be resolved. "
                "Resolved artifacts are included below."
            )
        else:
            msg = None

        return ArtifactResolutionResult(
            gate_id=gate_id,
            artifacts=tuple(resolved),
            missing_refs=tuple(missing),
            checksum_mismatches=tuple(mismatches),
            failure_message=msg,
            resolution_id=uuid4().hex[:12],
        )

    def resolve_gate_refs(
        self,
        refs: Sequence[GateArtifactRef],
    ) -> ArtifactResolutionResult:
        """Resolve a specific set of artifact refs (not bound to a gate).

        Useful for re-resolving individual artifacts from a gate
        or resolving refs from an ArtifactRevisionRecord.
        """
        resolved: list[ResolvedArtifact] = []
        missing: list[str] = []
        mismatches: list[str] = []

        for ref in refs:
            result = self._resolve_single_artifact(
                kind=ref.kind,
                path_or_ref=ref.path_or_ref,
                expected_checksum=ref.checksum,
                description=ref.description,
            )
            if result is None:
                missing.append(ref.kind)
            elif not result.checksum_verified:
                mismatches.append(ref.kind)
            else:
                resolved.append(result)

        return ArtifactResolutionResult(
            gate_id="",
            artifacts=tuple(resolved),
            missing_refs=tuple(missing),
            checksum_mismatches=tuple(mismatches),
            failure_message=(
                ResolutionFailureReason.CHECKSUM_MISMATCH
                if mismatches and not resolved
                else None
            ),
            resolution_id=uuid4().hex[:12],
        )

    # ── Internal resolution ────────────────────────────────────────

    def _resolve_single_artifact(
        self,
        *,
        kind: str,
        path_or_ref: str,
        expected_checksum: str | None = None,
        description: str = "",
    ) -> ResolvedArtifact | None:
        """Resolve a single artifact ref.

        Returns None if the artifact cannot be found.
        Returns a ResolvedArtifact with checksum_verified=False
        if the checksum does not match.
        """
        # Resolve the artifact path
        content, size_bytes = self._read_artifact_content(path_or_ref)
        if content is None:
            return None

        # Verify checksum
        actual_checksum = sha256_hex(content.encode("utf-8"))
        checksum_ok = True if expected_checksum is None else actual_checksum == expected_checksum
        checksum_value = expected_checksum or actual_checksum

        # Redact content
        redacted = self._redact_artifact_content(content, kind)
        truncated = len(redacted) > self._max_content_chars
        if truncated:
            redacted = redacted[:self._max_content_chars] + "\n\n[... content truncated ...]"

        return ResolvedArtifact(
            kind=kind,
            checksum=checksum_value,
            checksum_verified=checksum_ok,
            content=redacted,
            size_bytes=size_bytes,
            truncated=truncated,
        )

    def _parse_gate_artifact_entries(
        self,
        raw: str | bytes | list[dict[str, Any]] | None,
    ) -> tuple[tuple[str, str, str | None, str], ...]:
        """Parse gate artifact refs into backend-owned resolution entries.

        The current approval_review slice stores plain string refs in some
        gates and structured refs in others. Plain strings are treated as
        backend-owned storage keys and resolved without per-artifact checksum
        verification, while structured refs keep their explicit checksums.
        """
        if raw is None:
            return ()

        if isinstance(raw, bytes):
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                return ()
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return ()
        else:
            parsed = raw

        if isinstance(parsed, dict):
            items: list[Any] = [parsed]
        elif isinstance(parsed, list):
            items = list(parsed)
        else:
            return ()

        entries: list[tuple[str, str, str | None, str]] = []
        for item in items:
            if isinstance(item, dict):
                kind = _artifact_label(str(item.get("kind", "") or ""), default="")
                path_or_ref = str(item.get("path_or_ref", "") or item.get("path", "") or item.get("ref", "")).strip()
                checksum_raw = item.get("checksum")
                checksum = str(checksum_raw).strip() if isinstance(checksum_raw, str) and checksum_raw.strip() else None
                description_raw = item.get("description", "")
                description = redact_absolute_paths(str(description_raw).strip()) if description_raw is not None else ""
                if not kind:
                    kind = _artifact_label(path_or_ref)
                if kind and path_or_ref:
                    entries.append((kind, path_or_ref, checksum, description))
                continue

            if isinstance(item, str):
                ref = item.strip()
                if not ref:
                    continue
                kind = _artifact_label(ref)
                entries.append((kind, ref, None, ""))

        return tuple(entries)

    def _read_artifact_content(
        self,
        path_or_ref: str,
    ) -> tuple[str | None, int | None]:
        """Read artifact content from backend-owned storage.

        Resolves *path_or_ref* relative to the configured storage_root.
        Returns (content, size_bytes) or (None, None) if the artifact
        cannot be found.

        This method never follows symlinks out of the storage_root
        and never reads files outside the configured root.
        """
        if self._storage_root is None:
            # In-memory or test mode — return None
            return None, None

        candidate_path = Path(path_or_ref)
        if candidate_path.is_absolute():
            resolved_path = candidate_path.resolve()
        else:
            resolved_path = (self._storage_root / candidate_path).resolve()

        # Ensure the resolved path is within the storage root
        try:
            resolved_path.relative_to(self._storage_root.resolve())
        except ValueError:
            return None, None

        if not resolved_path.exists() or not resolved_path.is_file():
            return None, None

        try:
            content = resolved_path.read_text(encoding="utf-8", errors="replace")
            size = resolved_path.stat().st_size
            return content, size
        except (OSError, PermissionError):
            return None, None

    def _redact_artifact_content(
        self,
        content: str,
        kind: str,
    ) -> str:
        """Redact sensitive content from artifact text.

        Applies the existing redaction pipeline:
        1. Absolute path redaction
        2. Model summary redaction (secrets, env vars)
        3. Sensitive env var pattern removal
        """
        redacted = redact_absolute_paths(content)
        redacted = redact_model_summary(redacted)
        return redacted


# ── Content budget helpers ────────────────────────────────────────────


def truncate_for_assistant(
    content: str,
    max_chars: int = _DEFAULT_MAX_ASSISTANT_CONTENT_CHARS,
    kind: str = "artifact",
) -> str:
    """Truncate artifact content for assistant context budget.

    If truncated, appends a truncation marker so the assistant
    knows the content was cut short.
    """
    if len(content) <= max_chars:
        return content

    return content[:max_chars] + f"\n\n[... {kind} truncated to {max_chars} characters ...]"
