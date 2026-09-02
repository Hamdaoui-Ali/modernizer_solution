from __future__ import annotations

from migration_factory.orchestrator.state import MigrationState


def copilot_phase_assist(state: MigrationState) -> MigrationState:
    return dict(state)  # type: ignore[return-value]


def copilot_final_report(state: MigrationState) -> MigrationState:
    return dict(state)  # type: ignore[return-value]
