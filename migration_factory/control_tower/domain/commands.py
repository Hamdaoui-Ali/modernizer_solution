"""Command execution state contracts for Control Tower."""

from __future__ import annotations

from enum import Enum


class CommandState(str, Enum):
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


NONTERMINAL_COMMAND_STATES = frozenset(
    {
        CommandState.QUEUED,
        CommandState.STARTING,
        CommandState.RUNNING,
        CommandState.CANCELLING,
    }
)


TERMINAL_COMMAND_STATES = frozenset(state for state in CommandState if state not in NONTERMINAL_COMMAND_STATES)


def is_nonterminal_command_state(state: CommandState) -> bool:
    return state in NONTERMINAL_COMMAND_STATES
