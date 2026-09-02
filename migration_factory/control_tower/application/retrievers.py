"""Bounded evidence retrievers for V1 context pack assembly.

V1-11B: Retrieves evidence from artifact and command-output sources
with bounds enforcement (size limits, file count, depth limits,
absolute path rejection, and deterministic metadata).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from migration_factory.control_tower.domain.checksums import (
    canonical_json_text,
    sha256_canonical_json,
)
from migration_factory.control_tower.domain.errors import ControlTowerError


# ── Domain errors for evidence bounds violations ──────────────────


class EvidenceBoundsError(ControlTowerError):
    """Raised when an evidence retrieval request exceeds configured bounds."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Evidence bounds violation: {reason}")


class EvidenceRetrievalError(ControlTowerError):
    """Raised when evidence cannot be retrieved from a source."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"Evidence retrieval failed from {source!r}: {reason}")


# ── Path validation helpers ───────────────────────────────────────


def _looks_absolute_or_rooted_path(raw: str) -> bool:
    """Check if a path looks absolute or rooted on any platform.

    Returns True for:
    - POSIX absolute paths (/foo/bar)
    - Windows rooted paths (\\foo\\bar)
    - Windows drive absolute paths (C:\\foo\\bar, C:/foo/bar)
    - UNC paths (\\\\server\\share, //server/share)
    - URI file scheme (file:///path)
    """
    stripped = raw.strip()
    if not stripped:
        return False

    # URI/file scheme
    if stripped.lower().startswith("file://"):
        return True

    # UNC (backslash): \\server\share
    if stripped.startswith("\\\\"):
        return True

    # UNC (forward slash): //server/share
    if stripped.startswith("//"):
        return True

    # POSIX absolute: /foo
    if stripped.startswith("/"):
        return True

    # Windows rooted (single backslash at start): \foo
    if stripped.startswith("\\"):
        return True

    # Windows drive absolute: C:\foo or C:/foo
    if (
        len(stripped) >= 3
        and stripped[0].isalpha()
        and stripped[1] == ":"
        and stripped[2] in ("\\", "/")
    ):
        return True

    return False


# ── Bounds configuration ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EvidenceBounds:
    """Bounds configuration for evidence retrieval.

    Attributes:
        max_bytes: Maximum total bytes for file content retrieval.
        max_files: Maximum number of files to include.
        max_depth: Maximum directory traversal depth (0 = flat).
        allow_absolute_paths: If False, reject absolute paths.
        allowed_prefixes: Tuple of allowed relative path prefixes.
        forbidden_suffixes: Tuple of forbidden file suffixes.
    """

    max_bytes: int = 1_000_000          # 1 MB default
    max_files: int = 50                 # 50 files default
    max_depth: int = 5                  # 5 levels default
    allow_absolute_paths: bool = False  # reject absolute paths by default
    allowed_prefixes: tuple[str, ...] = ()
    forbidden_suffixes: tuple[str, ...] = (
        ".class",
        ".jar",
        ".log",
        ".bin",
        ".exe",
        ".dll",
        ".so",
        ".pyc",
        ".pyo",
        ".pyd",
        ".git",
        ".env",
    )


# ── Evidence reference ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A single evidence reference extracted from context pack assembly.

    Attributes:
        source_type: Type of evidence source (artifact, command_output, etc.).
        source_id: Identifier for the source (artifact_id, command_id).
        relative_path: Relative path of the evidence within the source.
        content_type: MIME or content type of the evidence.
        size_bytes: Size of the evidence content in bytes.
        checksum_algorithm: Algorithm used for the evidence checksum.
        checksum: Hex digest of the evidence content or reference.
        metadata_json: Deterministic JSON metadata for this evidence ref.
    """

    source_type: str
    source_id: str
    relative_path: str
    content_type: str | None = None
    size_bytes: int = 0
    checksum_algorithm: str = "sha256"
    checksum: str = ""
    metadata_json: str = ""


# ── Evidence source port ──────────────────────────────────────────


class EvidenceSourceRepository(Protocol):
    """Port for reading raw evidence from a storage source."""

    def read_bytes(
        self,
        *,
        source_type: str,
        source_id: str,
        relative_path: str,
        max_bytes: int,
    ) -> bytes | None:
        """Read up to max_bytes from an evidence source.

        Returns None if the source or path does not exist.
        Raises EvidenceBoundsError if the path violates source bounds.
        """
        ...

    def resolve_entries(
        self,
        *,
        source_type: str,
        source_id: str,
        prefix: str,
        max_files: int,
        max_depth: int,
    ) -> tuple[dict[str, object], ...]:
        """List entries under a prefix within bounds.

        Returns a tuple of dicts with keys: relative_path, size_bytes, content_type.
        Returns an empty tuple if the prefix does not exist.
        Raises EvidenceBoundsError if bounds are exceeded.
        """
        ...


# ── Bounded evidence retriever ────────────────────────────────────


class BoundedEvidenceRetriever:
    """Retrieves evidence from bounded sources with bounds enforcement.

    Used during context pack assembly to collect evidence refs
    from artifacts, command outputs, and other bounded sources
    without exposing raw secrets, absolute paths, or forbidden
    content.
    """

    def __init__(
        self,
        evidence_source: EvidenceSourceRepository,
        default_bounds: EvidenceBounds | None = None,
    ) -> None:
        self._evidence_source = evidence_source
        self._default_bounds = default_bounds or EvidenceBounds()

    def retrieve_evidence(
        self,
        *,
        source_type: str,
        source_id: str,
        evidence_paths: tuple[str, ...],
        bounds: EvidenceBounds | None = None,
    ) -> tuple[EvidenceRef, ...]:
        """Retrieve bounded evidence references from a source.

        Validates each evidence path against bounds:
        - Rejects absolute paths when bounds.allow_absolute_paths is False.
        - Rejects paths exceeding max_depth.
        - Rejects forbidden suffixes.
        - Caps total files at max_files.
        - Caps total bytes at max_bytes.

        Returns an ordered tuple of EvidenceRef objects with
        deterministic checksums and metadata.

        Raises EvidenceBoundsError if any path violates bounds.
        """
        actual_bounds = bounds or self._default_bounds

        if not evidence_paths:
            return ()

        validated_paths = self._validate_paths(evidence_paths, actual_bounds)
        if len(validated_paths) > actual_bounds.max_files:
            raise EvidenceBoundsError(
                f"Evidence path count {len(validated_paths)} exceeds max_files {actual_bounds.max_files}"
            )

        refs: list[EvidenceRef] = []
        total_bytes = 0

        for path in validated_paths:
            entry = self._read_single_evidence(source_type, source_id, path, actual_bounds, total_bytes)
            if entry is not None:
                ref, bytes_read = entry
                refs.append(ref)
                total_bytes += bytes_read
                if total_bytes > actual_bounds.max_bytes:
                    raise EvidenceBoundsError(
                        f"Evidence total bytes {total_bytes} exceeds max_bytes "
                        f"{actual_bounds.max_bytes}"
                    )

        return tuple(refs)

    def resolve_and_retrieve(
        self,
        *,
        source_type: str,
        source_id: str,
        prefix: str,
        bounds: EvidenceBounds | None = None,
    ) -> tuple[EvidenceRef, ...]:
        """List entries under a prefix, then retrieve each within bounds."""
        actual_bounds = bounds or self._default_bounds

        entries = self._evidence_source.resolve_entries(
            source_type=source_type,
            source_id=source_id,
            prefix=prefix,
            max_files=actual_bounds.max_files,
            max_depth=actual_bounds.max_depth,
        )

        if not entries:
            return ()

        paths = tuple(str(e.get("relative_path", "")) for e in entries if e.get("relative_path"))
        return self.retrieve_evidence(
            source_type=source_type,
            source_id=source_id,
            evidence_paths=paths,
            bounds=actual_bounds,
        )

    def _validate_paths(
        self,
        paths: tuple[str, ...],
        bounds: EvidenceBounds,
    ) -> tuple[str, ...]:
        """Validate and normalize evidence paths against bounds.

        Returns the validated relative paths, or raises EvidenceBoundsError.
        """
        validated: list[str] = []

        for path in paths:
            cleaned = path.strip()
            if not cleaned:
                continue

            p = Path(cleaned)

            # Reject absolute/rooted paths (cross-platform: POSIX /foo,
            # Windows \foo, C:\foo, C:/foo, UNC, file://)
            if not bounds.allow_absolute_paths and _looks_absolute_or_rooted_path(cleaned):
                raise EvidenceBoundsError(
                    f"Absolute path not allowed: {cleaned!r}"
                )

            # Reject path traversal (.. components)
            normalized_parts = p.as_posix().split("/")
            if ".." in normalized_parts:
                raise EvidenceBoundsError(
                    f"Path traversal not allowed: {cleaned!r}"
                )

            # Reject traversing above allowed prefixes
            if bounds.allowed_prefixes:
                normalized = p.as_posix()
                if not any(normalized.startswith(prefix) for prefix in bounds.allowed_prefixes):
                    raise EvidenceBoundsError(
                        f"Path {normalized!r} does not match any allowed prefix: "
                        f"{bounds.allowed_prefixes}"
                    )

            # Check depth
            parts = p.parts
            depth = len(parts)
            if depth > bounds.max_depth:
                raise EvidenceBoundsError(
                    f"Path depth {depth} exceeds max_depth {bounds.max_depth}: {cleaned!r}"
                )

            # Check forbidden suffixes
            suffix = p.suffix.lower()
            if suffix in bounds.forbidden_suffixes:
                raise EvidenceBoundsError(
                    f"Path has forbidden suffix {suffix!r}: {cleaned!r}"
                )

            validated.append(cleaned)

        return tuple(validated)

    def _read_single_evidence(
        self,
        source_type: str,
        source_id: str,
        relative_path: str,
        bounds: EvidenceBounds,
        current_total_bytes: int,
    ) -> tuple[EvidenceRef, int] | None:
        """Read a single evidence entry and return (EvidenceRef, bytes_read).

        Returns None if the source entry does not exist.
        """
        remaining = bounds.max_bytes - current_total_bytes
        if remaining <= 0:
            return None

        read_size = min(remaining, len(relative_path.encode("utf-8")) + 1024)
        read_size = max(read_size, 1024 * 1024)  # at least 1 MB for real content

        raw = self._evidence_source.read_bytes(
            source_type=source_type,
            source_id=source_id,
            relative_path=relative_path,
            max_bytes=read_size,
        )

        if raw is None:
            return None

        bytes_read = len(raw)
        checksum = sha256_canonical_json({"path": relative_path, "bytes": bytes_read})

        metadata = canonical_json_text({
            "source_type": source_type,
            "source_id": source_id,
            "relative_path": relative_path,
            "bytes_read": bytes_read,
            "checksum": checksum,
        })

        ref = EvidenceRef(
            source_type=source_type,
            source_id=source_id,
            relative_path=relative_path,
            content_type="application/octet-stream",
            size_bytes=bytes_read,
            checksum_algorithm="sha256",
            checksum=checksum,
            metadata_json=metadata,
        )
        return ref, bytes_read


# ── Assembly helper: build evidence_refs_json for a manifest ──────


def build_evidence_refs_json(
    refs: tuple[EvidenceRef, ...],
) -> str:
    """Build a deterministic JSON string from evidence refs for manifest storage."""
    import json

    entries = []
    for ref in refs:
        entries.append({
            "source_type": ref.source_type,
            "source_id": ref.source_id,
            "relative_path": ref.relative_path,
            "content_type": ref.content_type,
            "size_bytes": ref.size_bytes,
            "checksum_algorithm": ref.checksum_algorithm,
            "checksum": ref.checksum,
        })
    # Sort entries by their JSON representation for deterministic output
    entries.sort(key=lambda e: json.dumps(e, separators=(",", ":"), sort_keys=True))
    return json.dumps(entries, separators=(",", ":"), sort_keys=True)
