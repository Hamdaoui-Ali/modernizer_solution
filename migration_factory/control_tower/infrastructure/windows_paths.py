"""Windows-specific path helpers for Control Tower local state."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping


WINDOWS_STATE_DIRNAME = "AI-Migration-Control-Tower"
DB_FILENAME = "control_tower.sqlite3"


def default_windows_db_path(
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    env = environ or {}
    local_app_data = env.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return Path(local_app_data) / WINDOWS_STATE_DIRNAME / DB_FILENAME
