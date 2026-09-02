from __future__ import annotations

from typing import Any

from migration_factory.final_report.context_builder import build_report_context, write_report_context
from migration_factory.final_report.writer import generate_final_migration_report

_COPILOT_EXPORTS = {
    "CopilotAdapterStatus",
    "build_copilot_report_request",
    "debug_status_payload",
    "detect_copilot_cli_status",
    "generate_copilot_report",
    "generate_copilot_report_skeleton",
    "load_copilot_report_manifest",
    "render_copilot_report_template",
    "write_failed_copilot_report_response",
}

__all__ = [
    "generate_final_migration_report",
    "build_report_context",
    "write_report_context",
    *_COPILOT_EXPORTS,
]


def __getattr__(name: str) -> Any:
    if name in _COPILOT_EXPORTS:
        from migration_factory.final_report import copilot

        return getattr(copilot, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
