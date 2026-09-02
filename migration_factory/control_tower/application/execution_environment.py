"""Safe materialization of backend-owned execution environment manifests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


SAFE_ENV_KEYS = (
    "COMSPEC",
    "HOME",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
)

MANIFEST_ENV_KEYS = (
    "JAVA_HOME",
    "JAVA11_HOME",
    "JAVA17_HOME",
    "JAVA21_HOME",
    "MAVEN_CMD",
)


def decode_environment_manifest(value: str | None) -> dict[str, Any]:
    """Decode a persisted stage env_json object without widening its shape."""
    manifest = json.loads(value or "{}")
    if not isinstance(manifest, dict):
        raise ValueError("Persisted env_json must be an object")
    return manifest


def materialize_execution_environment(
    manifest: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Apply the normal V2 safe manifest/environment precedence rules."""
    source_environment = os.environ if environ is None else environ
    env = {
        key: value
        for key in SAFE_ENV_KEYS
        if (value := source_environment.get(key)) is not None
    }
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])

    for key in MANIFEST_ENV_KEYS:
        value = manifest.get(key)
        if isinstance(value, str) and value:
            env[key] = value

    path_prepend = manifest.get("PATH_PREPEND")
    if isinstance(path_prepend, str) and path_prepend:
        current_path = env.get("PATH", "")
        env["PATH"] = path_prepend + (os.pathsep + current_path if current_path else "")

    env["AI_MIGRATION_CONTROL_TOWER_EVENTS"] = "jsonl"
    return env
