"""Filesystem-only artifact path validation and hashing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import os
import stat
from typing import Sequence

from migration_factory.control_tower.domain.artifacts import ArtifactHashResult
from migration_factory.control_tower.domain.checksums import stream_sha256
from migration_factory.control_tower.domain.errors import ArtifactHashError, ArtifactPathError
from migration_factory.control_tower.schemas.runner_profile import RegisteredFilesystemRoot


@dataclass(frozen=True, slots=True)
class ValidatedArtifactPath:
    registered_root_id: str
    root_kind: str
    relative_path: str
    normalized_relative_path: str


@dataclass(frozen=True, slots=True)
class _PathSnapshot:
    resolved_path: str
    file_identity: tuple[int | None, int | None]
    size_bytes: int
    mtime_ns: int


def validate_registered_artifact_path(
    registered_roots: Sequence[RegisteredFilesystemRoot],
    registered_root_id: str,
    relative_path: str | Path,
) -> ValidatedArtifactPath:
    root = _lookup_root(registered_roots, registered_root_id)
    normalized_relative_path = _normalize_relative_path(relative_path)
    _assert_root_is_safe(root)
    _validate_traversal_inside_root(root, normalized_relative_path)
    return ValidatedArtifactPath(
        registered_root_id=root.root_id,
        root_kind=root.kind,
        relative_path=str(relative_path),
        normalized_relative_path=normalized_relative_path,
    )


def hash_registered_artifact(
    registered_roots: Sequence[RegisteredFilesystemRoot],
    registered_root_id: str,
    relative_path: str | Path,
) -> ArtifactHashResult:
    root = _lookup_root(registered_roots, registered_root_id)
    normalized_relative_path = _normalize_relative_path(relative_path)
    root_path = _assert_root_is_safe(root)
    resolved_path = _resolve_trusted_path(root_path, normalized_relative_path)
    before = _snapshot(resolved_path)
    checksum, size_bytes = stream_sha256(resolved_path)
    after = _snapshot(resolved_path)

    if before != after:
        raise ArtifactHashError(
            "Artifact changed while being hashed: "
            f"{relative_path!s}"
        )

    if size_bytes != after.size_bytes:
        raise ArtifactHashError(
            "Artifact size changed while being hashed: "
            f"{relative_path!s}"
        )

    return ArtifactHashResult(
        registered_root_id=root.root_id,
        root_kind=root.kind,
        relative_path=str(relative_path),
        normalized_relative_path=normalized_relative_path,
        checksum_algorithm="sha256",
        checksum=checksum,
        size_bytes=size_bytes,
        mtime_ns=after.mtime_ns,
        file_identity=after.file_identity,
    )


def normalize_registered_relative_path(relative_path: str | Path) -> str:
    return _normalize_relative_path(relative_path)


def _lookup_root(
    registered_roots: Sequence[RegisteredFilesystemRoot],
    registered_root_id: str,
) -> RegisteredFilesystemRoot:
    for root in registered_roots:
        if root.root_id == registered_root_id:
            return root
    raise ArtifactPathError(f"Unknown registered root ID: {registered_root_id}")


def _assert_root_is_safe(root: RegisteredFilesystemRoot) -> Path:
    root_path = Path(root.path).expanduser()
    if not root_path.exists():
        raise ArtifactPathError(f"Registered root path does not exist: {root.path}")
    if not root_path.is_dir():
        raise ArtifactPathError(f"Registered root path is not a directory: {root.path}")
    if root_path.is_symlink():
        raise ArtifactPathError(f"Registered root path must not be a symlink: {root.path}")
    if _is_junction(root_path):
        raise ArtifactPathError(f"Registered root path must not be a junction: {root.path}")
    if _is_reparse_point(root_path):
        raise ArtifactPathError(f"Registered root path must not be a reparse point: {root.path}")
    return root_path.resolve(strict=False)


def _validate_traversal_inside_root(root: RegisteredFilesystemRoot, normalized_relative_path: str) -> None:
    root_path = _assert_root_is_safe(root)
    _resolve_trusted_path(root_path, normalized_relative_path)


def _resolve_trusted_path(root_path: Path, normalized_relative_path: str) -> Path:
    current = root_path
    for part in normalized_relative_path.split("/"):
        candidate = current / part
        if candidate.exists():
            _reject_unsafe_existing_path(candidate, root_path)
            current = candidate.resolve(strict=False)
        else:
            current = candidate
    resolved = current.resolve(strict=False)
    _ensure_inside(resolved, root_path)
    return resolved


def _reject_unsafe_existing_path(candidate: Path, root_path: Path) -> None:
    if candidate.is_symlink():
        target = candidate.resolve(strict=False)
        _ensure_inside(target, root_path)
    elif _is_junction(candidate):
        target = candidate.resolve(strict=False)
        _ensure_inside(target, root_path)
    elif _is_reparse_point(candidate):
        raise ArtifactPathError(f"Unsupported reparse point encountered: {candidate}")


def _ensure_inside(path: Path, root_path: Path) -> None:
    try:
        path.relative_to(root_path)
    except ValueError as exc:
        raise ArtifactPathError(f"Path escapes registered root: {path}") from exc


def _normalize_relative_path(relative_path: str | Path) -> str:
    raw_text = str(relative_path).strip()
    if not raw_text:
        raise ArtifactPathError("Relative path must not be empty")

    windows_path = PureWindowsPath(raw_text)
    if windows_path.is_absolute() or windows_path.drive or windows_path.anchor:
        raise ArtifactPathError(f"Absolute or drive-qualified paths are not allowed: {raw_text}")

    parts: list[str] = []
    for part in windows_path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise ArtifactPathError(f"Parent traversal is not allowed: {raw_text}")
        parts.append(part)

    if not parts:
        raise ArtifactPathError("Relative path must contain at least one real segment")

    normalized_parts = [part.casefold() for part in parts]
    normalized = "/".join(normalized_parts)
    if ".." in normalized.split("/"):
        raise ArtifactPathError(f"Parent traversal is not allowed: {raw_text}")
    return normalized


def _snapshot(path: Path) -> _PathSnapshot:
    stat_result = path.stat(follow_symlinks=False)
    resolved = path.resolve(strict=False)
    file_identity = (
        _safe_int(getattr(stat_result, "st_dev", None)),
        _safe_int(getattr(stat_result, "st_ino", None)),
    )
    return _PathSnapshot(
        resolved_path=str(resolved),
        file_identity=file_identity,
        size_bytes=int(stat_result.st_size),
        mtime_ns=int(stat_result.st_mtime_ns),
    )


def _safe_int(value: int | None) -> int | None:
    return None if value is None else int(value)


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker() if callable(checker) else False)


def _is_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        stat_result = path.stat(follow_symlinks=False)
    except OSError:
        return False
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not (attributes & reparse_flag):
        return False
    return not path.is_symlink() and not _is_junction(path)
