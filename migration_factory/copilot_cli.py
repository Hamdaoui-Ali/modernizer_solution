from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable


def _is_windows() -> bool:
    return os.name == "nt"


def resolve_copilot_cli_executable(
    executable: str = "copilot",
    *,
    is_windows: bool | None = None,
    which: Callable[[str], str | None] | None = None,
) -> str | None:
    """Resolve the GitHub Copilot CLI to a subprocess-safe executable path."""

    requested = str(executable or "").strip() or "copilot"
    which_func = which or shutil.which
    path = Path(requested)
    if path.is_absolute() or any(separator in requested for separator in ("\\", "/")):
        return requested

    windows = _is_windows() if is_windows is None else is_windows
    names: tuple[str, ...]
    if requested.lower() in {"copilot", "copilot.cmd"}:
        names = ("copilot.cmd", "copilot") if windows else ("copilot",)
    else:
        names = (requested,)

    for name in names:
        resolved = which_func(name)
        if resolved:
            return resolved
    return None
