"""Safe workspace service for atomic file publishing and workspace preparation."""

from __future__ import annotations

import json as _json
import os
from pathlib import Path
from uuid import uuid4

from migration_factory.control_tower.domain.artifacts import ArtifactHashResult
from migration_factory.control_tower.domain.checksums import canonical_json_bytes, sha256_hex, stream_sha256
from migration_factory.control_tower.domain.entities import RunConfigurationRecord
from migration_factory.control_tower.domain.errors import (
    ArtifactHashError,
    ArtifactPathError,
    ManifestIntegrityError,
    WorkspaceConflictError,
)
from migration_factory.control_tower.domain.manifests import CommandManifest, compute_manifest_checksum


def _get_artifact_paths_helpers():
    from migration_factory.control_tower.infrastructure.sqlite.artifact_paths import (
        _ensure_inside,
        _is_junction,
        _is_reparse_point,
        _normalize_relative_path,
        _reject_unsafe_existing_path,
        _resolve_trusted_path,
    )

    return (
        _ensure_inside,
        _is_junction,
        _is_reparse_point,
        _normalize_relative_path,
        _reject_unsafe_existing_path,
        _resolve_trusted_path,
    )


def atomic_publish(path: Path, content_bytes: bytes, *, allow_overwrite: bool = False) -> None:
    parent = path.parent
    temp_path = parent / f".{path.name}.{uuid4().hex}.tmp"

    if path.exists():
        existing = path.read_bytes()
        if existing == content_bytes:
            return
        if not allow_overwrite:
            raise WorkspaceConflictError(
                f"Cannot replace existing manifest with different content: {path}"
            )

    try:
        fd = os.open(str(temp_path), os.O_CREAT | os.O_WRONLY | os.O_EXCL)
        try:
            os.write(fd, content_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)

        os.replace(str(temp_path), str(path))

        _sync_directory(parent)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    _verify_file_contents(path, content_bytes)


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _verify_file_contents(path: Path, expected: bytes) -> None:
    with path.open("rb") as handle:
        actual = handle.read()
    if actual != expected:
        raise WorkspaceConflictError(
            f"Published file content mismatch: {path}"
        )


def materialize_run_config(run_config: RunConfigurationRecord, working_dir: Path, root_id: str) -> ArtifactHashResult:
    (_ensure_inside, _, _, _, _, _) = _get_artifact_paths_helpers()

    payload_json = run_config.payload_json
    if run_config.payload_checksum != sha256_hex(payload_json.encode("utf-8")):
        raise ArtifactHashError("Run configuration payload checksum mismatch")

    control_dir = working_dir / "control"
    _ensure_directory_safe(control_dir, working_dir, _ensure_inside)

    final_path = control_dir / "run_configuration.json"
    content_bytes = payload_json.encode("utf-8")

    atomic_publish(final_path, content_bytes)

    checksum, size_bytes = stream_sha256(final_path)
    expected_checksum = run_config.payload_checksum
    if checksum != expected_checksum:
        raise ArtifactHashError(
            f"Materialized run configuration checksum mismatch: {checksum} != {expected_checksum}"
        )

    stat_result = final_path.stat(follow_symlinks=False)
    return ArtifactHashResult(
        registered_root_id=root_id,
        root_kind="output",
        relative_path=str(final_path.relative_to(working_dir)),
        normalized_relative_path=str(final_path.relative_to(working_dir)),
        checksum_algorithm="sha256",
        checksum=checksum,
        size_bytes=size_bytes,
        mtime_ns=int(stat_result.st_mtime_ns),
        file_identity=(None, None),
    )


def materialize_command_manifest(
    manifest: CommandManifest,
    working_dir: Path,
    artifact_id: str,
    root_id: str,
) -> tuple[ArtifactHashResult, bytes]:
    (_ensure_inside, _, _, _, _, _) = _get_artifact_paths_helpers()

    checksum = compute_manifest_checksum(manifest)
    manifest = manifest.model_copy(update={"manifest_checksum": checksum})

    commands_dir = working_dir / "control" / "commands" / manifest.command_id
    _ensure_directory_safe(commands_dir, working_dir, _ensure_inside)

    final_path = commands_dir / "command_manifest.json"
    content_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))

    atomic_publish(final_path, content_bytes)

    final_checksum, size_bytes = stream_sha256(final_path)
    expected_checksum = sha256_hex(content_bytes)
    if final_checksum != expected_checksum:
        raise ManifestIntegrityError(
            f"Materialized manifest checksum mismatch: {final_checksum} != {expected_checksum}"
        )

    stat_result = final_path.stat(follow_symlinks=False)
    return ArtifactHashResult(
        registered_root_id=root_id,
        root_kind="output",
        relative_path=str(final_path.relative_to(working_dir)),
        normalized_relative_path=str(final_path.relative_to(working_dir)),
        checksum_algorithm="sha256",
        checksum=expected_checksum,
        size_bytes=size_bytes,
        mtime_ns=int(stat_result.st_mtime_ns),
        file_identity=(None, None),
    ), content_bytes


def prepare_safe_workspace(root_path: Path, relative_path: str) -> Path:
    (
        _ensure_inside,
        _is_junction,
        _is_reparse_point,
        _normalize_relative_path,
        _reject_unsafe_existing_path,
        _resolve_trusted_path,
    ) = _get_artifact_paths_helpers()

    normalized = _normalize_relative_path(relative_path)
    root = root_path.resolve(strict=False)

    if not root.is_dir():
        raise ArtifactPathError(f"Workspace root is not a directory: {root}")
    if root_path.is_symlink() or _is_junction(root_path) or _is_reparse_point(root_path):
        raise ArtifactPathError(f"Workspace root must not be a symlink, junction, or reparse point: {root_path}")

    current = root
    for part in normalized.split("/"):
        candidate = current / part
        if candidate.exists():
            _reject_unsafe_existing_path(candidate, root)
            current = candidate.resolve(strict=False)
        else:
            current = candidate

    os.makedirs(str(current), exist_ok=True)

    resolved = current.resolve(strict=False)
    _ensure_inside(resolved, root)
    return resolved


def cleanup_stale_temp_files(workspace_dir: Path) -> None:
    if not workspace_dir.exists():
        return
    for entry in workspace_dir.rglob("*.tmp"):
        if entry.is_file():
            try:
                entry.unlink(missing_ok=True)
            except OSError:
                pass


def _ensure_directory_safe(path: Path, root_path: Path, _ensure_inside) -> None:
    os.makedirs(str(path), exist_ok=True)
    resolved = path.resolve(strict=False)
    _ensure_inside(resolved, root_path)
