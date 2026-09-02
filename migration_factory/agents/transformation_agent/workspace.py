from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from typing import Iterable


EXCLUDED_NAMES = {
    ".cache",
    ".git",
    ".gradle",
    ".migration",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "__pycache__",
    "build",
    "node_modules",
    "target",
}


class TransformationWorkspaceError(ValueError):
    """Raised when a sandbox workspace cannot be prepared safely."""


@dataclass(frozen=True)
class SandboxWorkspace:
    path: Path
    checkpoint_type: str
    checkpoint_ref: str


def prepare_sandbox_workspace(
    *,
    legacy_app_path: str | Path,
    modernized_app_path: str | Path,
    run_dir: str | Path,
) -> SandboxWorkspace:
    legacy_path = _resolve_existing_dir(legacy_app_path, "legacy_app_path")
    modernized_path = Path(modernized_app_path).expanduser().resolve()
    run_path = Path(run_dir).expanduser()
    run_path.mkdir(parents=True, exist_ok=True)
    run_path = run_path.resolve()

    sandbox_path = run_path / "workspaces" / "sandbox"
    sandbox_resolved = sandbox_path.resolve(strict=False)
    _ensure_inside(sandbox_resolved, run_path, "sandbox must stay inside run_dir")

    if sandbox_resolved == legacy_path:
        raise TransformationWorkspaceError("sandbox must not be the legacy_app_path")
    if sandbox_resolved == modernized_path:
        raise TransformationWorkspaceError("sandbox must not be the modernized_app_path")
    if sandbox_path.is_symlink():
        raise TransformationWorkspaceError(f"sandbox path must not be a symlink: {sandbox_path}")

    _validate_source_symlinks(legacy_path, sandbox_resolved)

    if sandbox_path.exists():
        _remove_existing_sandbox(sandbox_path, run_path)
    sandbox_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        legacy_path,
        sandbox_path,
        ignore=_ignore_excluded_names,
        symlinks=True,
    )

    sandbox_resolved = sandbox_path.resolve()
    _ensure_inside(sandbox_resolved, run_path, "sandbox must stay inside run_dir")

    checkpoint_type, checkpoint_ref = _create_baseline_checkpoint(sandbox_path)
    return SandboxWorkspace(
        path=sandbox_path,
        checkpoint_type=checkpoint_type,
        checkpoint_ref=checkpoint_ref,
    )


def _resolve_existing_dir(path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise TransformationWorkspaceError(f"{label} does not exist or is not a directory: {resolved}")
    return resolved


def _ignore_excluded_names(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDED_NAMES}


def _remove_existing_sandbox(sandbox_path: Path, run_path: Path) -> None:
    sandbox_resolved = sandbox_path.resolve(strict=False)
    _ensure_inside(sandbox_resolved, run_path, "sandbox must stay inside run_dir")
    expected_sandbox = (run_path / "workspaces" / "sandbox").resolve(strict=False)
    if sandbox_resolved != expected_sandbox:
        raise TransformationWorkspaceError(f"sandbox cleanup target is not the expected sandbox path: {sandbox_path}")
    if sandbox_path.is_symlink():
        raise TransformationWorkspaceError(f"sandbox path must not be a symlink: {sandbox_path}")

    if sys.platform == "win32":
        _clear_readonly_attributes(sandbox_path)

    try:
        shutil.rmtree(sandbox_path)
    except OSError as exc:
        raise TransformationWorkspaceError(_sandbox_clean_failed_message(sandbox_path, exc)) from exc


def _clear_readonly_attributes(path: Path) -> None:
    for root, dirnames, filenames in os.walk(path, followlinks=False):
        for name in [*dirnames, *filenames]:
            item = Path(root) / name
            try:
                item.chmod(stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
    try:
        path.chmod(stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _sandbox_clean_failed_message(sandbox_path: Path, exc: OSError) -> str:
    return (
        "SANDBOX_CLEAN_FAILED\n"
        f"Sandbox path: {sandbox_path}\n"
        f"Cleanup error: {exc}\n"
        "Advice:\n"
        "- stop Java process / close terminals/editors\n"
        "- delete sandbox manually\n"
        "- use a new run id"
    )


def _validate_source_symlinks(source_path: Path, sandbox_path: Path) -> None:
    for root, dirnames, filenames in os.walk(source_path, followlinks=False):
        root_path = Path(root)

        for name in list(dirnames):
            if name in EXCLUDED_NAMES:
                dirnames.remove(name)
                continue
            path = root_path / name
            if path.is_symlink():
                _validate_source_symlink(path, source_path, sandbox_path)
                dirnames.remove(name)

        for name in filenames:
            if name in EXCLUDED_NAMES:
                continue
            path = root_path / name
            if path.is_symlink():
                _validate_source_symlink(path, source_path, sandbox_path)


def _validate_source_symlink(path: Path, source_path: Path, sandbox_path: Path) -> None:
    target = os.readlink(path)
    if Path(target).is_absolute():
        raise TransformationWorkspaceError(f"Symlink escapes sandbox: {path}")

    source_target = (path.parent / target).resolve()
    _ensure_inside(source_target, source_path, f"Symlink escapes legacy_app_path: {path}")

    relative_link_path = path.relative_to(source_path)
    sandbox_target = (sandbox_path / relative_link_path).parent.joinpath(target).resolve(strict=False)
    _ensure_inside(sandbox_target, sandbox_path, f"Symlink escapes sandbox: {path}")


def _ensure_inside(path: Path, parent: Path, message: str) -> None:
    if path == parent:
        return
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise TransformationWorkspaceError(f"{message}: {path}") from exc


def _create_baseline_checkpoint(sandbox_path: Path) -> tuple[str, str]:
    git_path = shutil.which("git")
    if git_path:
        return _create_git_checkpoint(sandbox_path, git_path)
    manifest_path = _write_baseline_manifest(sandbox_path)
    return "manifest", str(manifest_path)


def _create_git_checkpoint(sandbox_path: Path, git_path: str) -> tuple[str, str]:
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "migration-factory",
        "GIT_AUTHOR_EMAIL": "migration-factory@example.invalid",
        "GIT_COMMITTER_NAME": "migration-factory",
        "GIT_COMMITTER_EMAIL": "migration-factory@example.invalid",
    }
    _run_git(git_path, ["init"], sandbox_path, git_env)
    _run_git(git_path, ["add", "-A"], sandbox_path, git_env)
    _run_git(git_path, ["commit", "--allow-empty", "-m", "Baseline sandbox checkpoint"], sandbox_path, git_env)
    result = _run_git(git_path, ["rev-parse", "HEAD"], sandbox_path, git_env)
    return "git", result.stdout.strip()


def _run_git(git_path: str, args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [git_path, *args],
            cwd=str(cwd),
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise TransformationWorkspaceError(f"Failed to create git baseline checkpoint: {detail}") from exc


def _write_baseline_manifest(sandbox_path: Path) -> Path:
    manifest_path = sandbox_path / "baseline_manifest.json"
    payload = {
        "checkpoint_type": "manifest",
        "files": list(_manifest_entries(sandbox_path, manifest_path)),
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def _manifest_entries(sandbox_path: Path, manifest_path: Path) -> Iterable[dict[str, object]]:
    for path in sorted(item for item in sandbox_path.rglob("*") if item.is_file()):
        if path == manifest_path:
            continue
        relative_path = path.relative_to(sandbox_path).as_posix()
        content = path.read_bytes()
        yield {
            "path": relative_path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
