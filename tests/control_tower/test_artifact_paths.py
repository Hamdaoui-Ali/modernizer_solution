from __future__ import annotations

from pathlib import Path

import pytest

from migration_factory.control_tower.infrastructure.sqlite.artifact_paths import (
    normalize_registered_relative_path,
    validate_registered_artifact_path,
)
from migration_factory.control_tower.domain.errors import ArtifactPathError
from migration_factory.control_tower.schemas.runner_profile import RegisteredFilesystemRoot


def _roots(tmp_path: Path) -> list[RegisteredFilesystemRoot]:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    return [
        RegisteredFilesystemRoot(root_id="source-root", kind="source", path=str(source)),
        RegisteredFilesystemRoot(root_id="output-root", kind="output", path=str(output)),
    ]


def test_unknown_root_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPathError, match="Unknown registered root ID"):
        validate_registered_artifact_path(_roots(tmp_path), "missing", "docs/report.txt")


@pytest.mark.parametrize(
    "relative_path",
    [
        r"C:\escape.txt",
        r"\\server\share\escape.txt",
        r"\\?\C:\escape.txt",
        r"\\.\C:\escape.txt",
        r"D:escape.txt",
    ],
)
def test_absolute_and_drive_qualified_paths_are_rejected(tmp_path: Path, relative_path: str) -> None:
    with pytest.raises(ArtifactPathError):
        validate_registered_artifact_path(_roots(tmp_path), "source-root", relative_path)


@pytest.mark.parametrize(
    "relative_path",
    [
        r"..\escape.txt",
        r"folder\..\escape.txt",
        r"./../escape.txt",
    ],
)
def test_parent_traversal_is_rejected_before_and_after_normalization(
    tmp_path: Path,
    relative_path: str,
) -> None:
    with pytest.raises(ArtifactPathError):
        validate_registered_artifact_path(_roots(tmp_path), "source-root", relative_path)


def test_case_aliases_share_normalized_key(tmp_path: Path) -> None:
    normalized_a = normalize_registered_relative_path(r"Folder\Sub\File.TXT")
    normalized_b = normalize_registered_relative_path(r"folder/sub/file.txt")

    assert normalized_a == normalized_b
    assert normalized_a == "folder/sub/file.txt"


def test_valid_relative_path_does_not_expose_absolute_path(tmp_path: Path) -> None:
    result = validate_registered_artifact_path(_roots(tmp_path), "source-root", r"Folder\Sub\File.txt")

    assert result.registered_root_id == "source-root"
    assert result.normalized_relative_path == "folder/sub/file.txt"
    assert not Path(result.normalized_relative_path).is_absolute()


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    inside = Path(roots[0].path)
    link = inside / "linked"
    target = outside / "escape.txt"
    target.write_text("x", encoding="utf-8")

    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege is not available")

    with pytest.raises(ArtifactPathError):
        validate_registered_artifact_path(roots, "source-root", r"linked\child.txt")
