"""Hygiene tests that enforce git hygiene for runtime artifacts.

These tests fail when runtime DB files, caches, logs, or other generated
junk are tracked in the git index. They protect against accidental commits
of local development artifacts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

TRACKED_ARTIFACT_PATTERNS = (
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".db",
)


def _tracked_files() -> list[str]:
    """Return all files currently tracked in the git index."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        pytest.fail(f"git ls-files failed: {result.stderr}")
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def _is_runtime_artifact(path: str) -> bool:
    """Check if a tracked path looks like a runtime artifact."""
    lower = path.lower()
    if path.startswith(".control-tower-dev/"):
        return True
    for pat in TRACKED_ARTIFACT_PATTERNS:
        if lower.endswith(pat):
            return True
    return False


@pytest.mark.hygiene
class TestRuntimeArtifactsNotTracked:
    """Guard: no SQLite DBs, runtime folders, or generated junk in git index."""

    def test_no_sqlite_db_tracked(self) -> None:
        """Fail if any .sqlite3, .sqlite3-shm, .sqlite3-wal, or .db file is tracked."""
        offenders = [f for f in _tracked_files() if _is_runtime_artifact(f)]
        assert not offenders, (
            f"Runtime artifacts found in git index ({len(offenders)}):\n"
            + "\n".join(f"  {f}" for f in offenders)
        )

    def test_no_control_tower_dev_tracked(self) -> None:
        """Fail if anything under .control-tower-dev/ is tracked."""
        offenders = [f for f in _tracked_files() if f.startswith(".control-tower-dev/")]
        assert not offenders, (
            f"Tracked files under .control-tower-dev/:\n"
            + "\n".join(f"  {f}" for f in offenders)
        )

    @pytest.mark.usefixtures("tmp_path")
    def test_hypothetical_sqlite_would_fail(self, tmp_path: Path) -> None:
        """Verify the detection logic itself would catch a hypothetical tracked .sqlite3."""
        assert _is_runtime_artifact(".control-tower-dev/control_tower.sqlite3")
        assert _is_runtime_artifact("data/cache.sqlite3")
        assert _is_runtime_artifact("data/db.sqlite3-shm")
        assert _is_runtime_artifact("data/db.sqlite3-wal")
        assert _is_runtime_artifact("data/app.db")
        assert not _is_runtime_artifact("src/main.py")
        assert not _is_runtime_artifact("tests/test_app.py")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


@pytest.mark.hygiene
class TestMigrationImmutability:
    """Guard: migration files must never be modified after being merged."""

    MIGRATIONS_DIR = REPO_ROOT / "migration_factory" / "control_tower" / "infrastructure" / "sqlite" / "migrations"

    def test_migration_files_exist_and_are_ordered(self) -> None:
        """Verify all migration files are present with strictly ascending versions."""
        import re as _re
        import hashlib as _hashlib

        files = sorted(self.MIGRATIONS_DIR.glob("*.sql"))
        assert files, f"No .sql files found in {self.MIGRATIONS_DIR}"

        version_re = _re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
        last_version = -1
        for path in files:
            match = version_re.fullmatch(path.name)
            assert match is not None, f"Invalid migration filename: {path.name}"
            version = int(match.group("version"))
            assert version > last_version, (
                f"Migration versions must be strictly ascending: "
                f"{path.name} after version {last_version:04d}"
            )
            last_version = version

    def test_no_migration_hash_changes(self) -> None:
        """Checksum every migration file and report any that differ from git HEAD.

        This test PASSES when all migration files on disk match what is
        committed at HEAD (after normalising line-endings).  If a migration
        has uncommitted content edits this test FAILS.  The fix is to REVERT
        the edit and create a NEW migration (0036, 0037, ...) instead.
        """
        import hashlib as _hashlib

        for path in sorted(self.MIGRATIONS_DIR.glob("*.sql")):
            disk_bytes = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            disk_hash = _hashlib.sha256(disk_bytes).hexdigest()
            git_bytes = _git_blob_bytes("HEAD", str(path.relative_to(REPO_ROOT)))
            if git_bytes is None:
                continue
            git_hash = _hashlib.sha256(git_bytes).hexdigest()
            assert disk_hash == git_hash, (
                f"Migration file {path.name} has UNCOMMITTED content changes!\n"
                f"  Disk SHA-256:  {disk_hash}\n"
                f"  HEAD SHA-256:  {git_hash}\n"
                f"  Revert: git checkout HEAD -- {path.relative_to(REPO_ROOT)}\n"
                f"  Then create a NEW migration file (e.g. 00XX_new_change.sql) instead."
            )

    def test_checksum_mismatch_dev_mode_reset_safety(self) -> None:
        """Verify that a checksum mismatch in dev mode calls reset, not crash.

        This is a unit-level test of the _is_dev_mode / _dev_reset_database
        path without needing a real corrupted DB.
        """
        from migration_factory.control_tower.infrastructure.sqlite.migrations import (
            _is_dev_mode,
            _dev_reset_database,
            apply_pending_migrations,
        )
        import sqlite3 as _sqlite3

        # _is_dev_mode must be False without the env var
        assert _is_dev_mode() is False

        # _dev_reset_database must not raise on an empty in-memory db
        conn = _sqlite3.connect(":memory:")
        conn.row_factory = _sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Apply migrations first to have tables
        apply_pending_migrations(conn)
        # Reset should drop everything
        _dev_reset_database(conn)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert len(tables) == 0, f"Expected 0 user tables after reset, got {[t[0] for t in tables]}"
        conn.close()


def _git_blob_bytes(treeish: str, rel_path: str) -> bytes | None:
    """Return the raw LF-normalised content of *rel_path* at *treeish*.

    Uses ``git show <treeish>:<rel_path>`` and normalizes line-endings to
    LF so cross-platform hash comparisons are stable.
    """
    import subprocess as _sp
    git_path = rel_path.replace("\\", "/")
    result = _sp.run(
        ["git", "show", f"{treeish}:{git_path}"],
        capture_output=True, cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        return None
    return result.stdout.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
