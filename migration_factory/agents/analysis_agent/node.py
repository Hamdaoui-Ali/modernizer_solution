from migration_factory.orchestrator.state import MigrationState


def analysis_node(state: MigrationState) -> MigrationState:
    return {
        "analysis_status": "PASS",
        "current_unit": "analysis",
    }
