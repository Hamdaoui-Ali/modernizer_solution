from migration_factory.orchestrator.state import MigrationState


def transformation_node(state: MigrationState) -> MigrationState:
    if state.get("approval_status") != "approved":
        errors = list(state.get("errors", []))
        errors.append("Transformation blocked until approval_status is 'approved'.")
        return {
            "transformation_status": "FAIL",
            "current_unit": "transformation",
            "errors": errors,
        }

    return {
        "transformation_status": "PASS",
        "current_unit": "transformation",
    }
