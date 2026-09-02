"""Path resolution for Control Tower SQLite state."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

from migration_factory.control_tower.infrastructure.windows_paths import (
    DB_FILENAME,
    default_windows_db_path,
)


XDG_STATE_DIRNAME = "ai-migration-control-tower"
CONTROL_TOWER_DB_PATH_ENV = "CONTROL_TOWER_DB_PATH"


def resolve_control_tower_db_path(
    explicit_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
    create_parent: bool = True,
) -> Path:
    env = dict(os.environ if environ is None else environ)
    raw_path: Path | None = None

    if explicit_path is not None:
        raw_path = Path(explicit_path)
    elif env.get(CONTROL_TOWER_DB_PATH_ENV):
        raw_path = Path(env[CONTROL_TOWER_DB_PATH_ENV])
    else:
        current_platform = sys.platform if platform is None else platform
        if current_platform.startswith("win"):
            raw_path = default_windows_db_path(env)
        if raw_path is None and env.get("XDG_STATE_HOME"):
            raw_path = Path(env["XDG_STATE_HOME"]) / XDG_STATE_DIRNAME / DB_FILENAME
        if raw_path is None:
            base_home = Path.home() if home is None else home
            raw_path = base_home / ".local" / "state" / XDG_STATE_DIRNAME / DB_FILENAME

    resolved_path = raw_path.expanduser().resolve()
    if create_parent:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return resolved_path
