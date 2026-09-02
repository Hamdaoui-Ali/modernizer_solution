from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any


def start_total_run_timing(state: dict[str, Any]) -> None:
    timing = _timing_obj(state)
    timing.setdefault("total_run_started_at", _utc_now())
    timing.setdefault("total_run_started_monotonic", time.monotonic())


def record_phase_duration(state: dict[str, Any], *, phase: str, duration_seconds: float) -> None:
    timing = _timing_obj(state)
    phase_durations = timing.setdefault("phase_durations_seconds", {})
    if isinstance(phase_durations, dict):
        phase_durations[phase] = round(float(duration_seconds), 6)


def record_command_duration(
    state: dict[str, Any],
    *,
    label: str,
    duration_seconds: float,
    command: list[str] | None = None,
    cwd: str | None = None,
) -> None:
    timing = _timing_obj(state)
    commands = timing.setdefault("commands", [])
    if not isinstance(commands, list):
        return
    commands.append(
        {
            "label": label,
            "duration_seconds": round(float(duration_seconds), 6),
            "command": list(command or []),
            "cwd": cwd,
        }
    )


def compute_total_duration_seconds(state: dict[str, Any]) -> float | None:
    timing = _timing_obj(state)
    started = timing.get("total_run_started_monotonic")
    if not isinstance(started, (int, float)):
        return None
    return round(float(time.monotonic() - started), 6)


def write_timing_artifacts(state: dict[str, Any]) -> dict[str, str]:
    run_dir = Path(str(state.get("run_dir") or "")).expanduser().resolve()
    if not run_dir:
        return {}

    perf_dir = run_dir / "performance"
    perf_dir.mkdir(parents=True, exist_ok=True)
    report_path = perf_dir / "timing_report.json"
    summary_path = perf_dir / "timing_summary.md"

    timing = _timing_obj(state)
    phase_durations = dict(timing.get("phase_durations_seconds", {}) or {})
    total_duration = compute_total_duration_seconds(state)
    if total_duration is not None:
        phase_durations["total_run"] = total_duration

    commands = list(timing.get("commands", []) or [])
    command_rows = []
    for row in commands:
        if not isinstance(row, dict):
            continue
        duration = row.get("duration_seconds")
        if not isinstance(duration, (int, float)):
            continue
        command_rows.append(
            {
                "label": str(row.get("label") or ""),
                "duration_seconds": round(float(duration), 6),
                "command": list(row.get("command") or []),
                "cwd": row.get("cwd"),
            }
        )

    slowest_phases = sorted(
        ({"name": str(k), "duration_seconds": float(v)} for k, v in phase_durations.items() if isinstance(v, (int, float))),
        key=lambda item: item["duration_seconds"],
        reverse=True,
    )
    slowest_commands = sorted(command_rows, key=lambda item: item["duration_seconds"], reverse=True)

    payload = {
        "schema_version": "1.0.0",
        "run_id": state.get("run_id", ""),
        "generated_at": _utc_now(),
        "phase_durations_seconds": phase_durations,
        "commands": command_rows,
        "slowest_phases": slowest_phases,
        "slowest_commands": slowest_commands,
    }

    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path.write_text(_summary_markdown(payload), encoding="utf-8")
    return {
        "timing_report": str(report_path),
        "timing_summary": str(summary_path),
    }


def _timing_obj(state: dict[str, Any]) -> dict[str, Any]:
    timing = state.setdefault("timing", {})
    if isinstance(timing, dict):
        return timing
    replacement: dict[str, Any] = {}
    state["timing"] = replacement
    return replacement


def _summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Timing Summary",
        "",
        f"- total_duration_seconds: {payload.get('phase_durations_seconds', {}).get('total_run', 0)}",
        "",
        "## Slowest Phases",
    ]
    phases = list(payload.get("slowest_phases", []) or [])[:10]
    if phases:
        for phase in phases:
            lines.append(f"- {phase.get('name')}: {phase.get('duration_seconds')}s")
    else:
        lines.append("- none")

    lines.extend(["", "## Slowest Commands"])
    commands = list(payload.get("slowest_commands", []) or [])[:10]
    if commands:
        for row in commands:
            command = " ".join(str(item) for item in row.get("command", []) if str(item))
            label = row.get("label") or "command"
            suffix = f" ({command})" if command else ""
            lines.append(f"- {label}: {row.get('duration_seconds')}s{suffix}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
