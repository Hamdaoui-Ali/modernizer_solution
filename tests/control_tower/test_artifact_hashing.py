from __future__ import annotations

from pathlib import Path

import pytest

from migration_factory.control_tower.infrastructure.sqlite.artifact_paths import hash_registered_artifact
from migration_factory.control_tower.domain.errors import ArtifactHashError
from migration_factory.control_tower.schemas.runner_profile import RegisteredFilesystemRoot


def _roots(tmp_path: Path) -> list[RegisteredFilesystemRoot]:
    source = tmp_path / "source"
    source.mkdir()
    return [RegisteredFilesystemRoot(root_id="source-root", kind="source", path=str(source))]


def test_large_artifact_hashed_in_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    file_path = Path(roots[0].path) / "reports" / "artifact.bin"
    file_path.parent.mkdir(parents=True)
    file_bytes = b"a" * (1024 * 1024 * 2 + 128)
    file_path.write_bytes(file_bytes)

    read_sizes: list[int] = []
    original_open = Path.open

    def wrapped_open(self: Path, mode: str = "r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == file_path and "b" in mode:
            return _RecordingHandle(handle, read_sizes)
        return handle

    monkeypatch.setattr(Path, "open", wrapped_open)

    result = hash_registered_artifact(roots, "source-root", r"reports\artifact.bin")

    assert result.checksum
    assert result.size_bytes == len(file_bytes)
    assert max(read_sizes) == 1024 * 1024
    assert len(read_sizes) >= 3
    assert not hasattr(result, "resolved_path")


def test_file_change_during_hash_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    file_path = Path(roots[0].path) / "reports" / "artifact.bin"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"a" * 1024 * 1024)

    original_open = Path.open

    def wrapped_open(self: Path, mode: str = "r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == file_path and "b" in mode:
            return _MutatingHandle(handle, file_path)
        return handle

    monkeypatch.setattr(Path, "open", wrapped_open)

    with pytest.raises(ArtifactHashError, match="changed while being hashed"):
        hash_registered_artifact(roots, "source-root", r"reports\artifact.bin")


class _RecordingHandle:
    def __init__(self, handle, read_sizes: list[int]) -> None:
        self._handle = handle
        self._read_sizes = read_sizes

    def read(self, size: int = -1):
        self._read_sizes.append(size)
        return self._handle.read(size)

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._handle.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._handle, name)


class _MutatingHandle(_RecordingHandle):
    def __init__(self, handle, file_path: Path) -> None:
        super().__init__(handle, [])
        self._file_path = file_path
        self._mutated = False

    def read(self, size: int = -1):
        chunk = super().read(size)
        if not self._mutated:
            self._mutated = True
            self._file_path.write_bytes(b"changed contents")
        return chunk
