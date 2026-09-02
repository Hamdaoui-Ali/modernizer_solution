from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import os
import shutil


MAVEN_EXECUTABLE_NAMES = {"mvn", "mvn.cmd", "mvn.bat", "mvn.exe"}


def resolve_maven_executable(env: Mapping[str, str] | None = None) -> str:
    effective_env = _effective_env(env)
    configured = str(effective_env.get("MAVEN_CMD") or effective_env.get("MVN_CMD") or "").strip()
    if configured and _looks_like_maven_executable(configured):
        return configured

    path_value = effective_env.get("PATH")
    candidates = ("mvn.cmd", "mvn.bat", "mvn.exe", "mvn") if os.name == "nt" else ("mvn",)
    for candidate in candidates:
        resolved = shutil.which(candidate, path=path_value)
        if resolved:
            return resolved
    return "mvn"


def resolve_maven_command(command: list[str], env: Mapping[str, str] | None = None) -> list[str]:
    if not command:
        return command
    if Path(str(command[0])).name.lower() not in MAVEN_EXECUTABLE_NAMES:
        return command
    return [resolve_maven_executable(env), *command[1:]]


def _effective_env(env: Mapping[str, str] | None) -> dict[str, str]:
    merged = os.environ.copy()
    if env:
        merged.update(dict(env))
    return merged


def _looks_like_maven_executable(value: str) -> bool:
    name = Path(value).name.lower()
    if name not in MAVEN_EXECUTABLE_NAMES:
        return False
    if Path(value).is_file():
        return True
    return any(separator in value for separator in ("/", "\\"))
