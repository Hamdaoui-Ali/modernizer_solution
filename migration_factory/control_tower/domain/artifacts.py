"""Artifact metadata contracts for Control Tower."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtifactHashResult:
    registered_root_id: str
    root_kind: str
    relative_path: str
    normalized_relative_path: str
    checksum_algorithm: str
    checksum: str
    size_bytes: int
    mtime_ns: int
    file_identity: tuple[int | None, int | None]
