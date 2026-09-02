COPILOT_ASSIST_STATUSES: tuple[str, ...] = (
    "USED",
    "SKIPPED",
    "UNAVAILABLE",
    "ERROR",
)

COPILOT_ADVISORY_CAN_MODIFY_FLAGS: tuple[str, ...] = (
    "can_modify_source",
    "can_modify_plan",
    "can_modify_blockers",
    "can_modify_executable",
    "can_modify_unit_order",
    "can_modify_approval_decision",
    "can_modify_tools",
)


def advisory_can_modify_flags() -> dict[str, bool]:
    return {name: False for name in COPILOT_ADVISORY_CAN_MODIFY_FLAGS}
