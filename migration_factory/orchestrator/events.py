from __future__ import annotations

import json
import os
from typing import Any

_PREFIX = "CONTROL_TOWER_EVENT "


def emit_control_tower_event(*, phase: str, status: str, message: str, **payload: Any) -> None:
    if os.environ.get("AI_MIGRATION_CONTROL_TOWER_EVENTS") != "jsonl":
        return
    data = {
        "phase": phase,
        "status": status,
        "message": message,
        **payload,
    }
    print(_PREFIX + json.dumps(data, separators=(",", ":"), sort_keys=True), flush=True)
