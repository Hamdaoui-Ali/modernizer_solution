"""SQLite connection policy for Control Tower."""

from __future__ import annotations

import sqlite3
from pathlib import Path


ALLOWED_JOURNAL_MODES = frozenset({"DELETE", "WAL"})


class ControlTowerSqliteError(Exception):
    """Base exception for Control Tower SQLite failures."""


class UnsupportedJournalModeError(ControlTowerSqliteError, ValueError):
    """Raised when journal mode is outside supported allowlist."""

    def __init__(self, journal_mode: str) -> None:
        self.journal_mode = journal_mode
        allowed = ", ".join(sorted(ALLOWED_JOURNAL_MODES))
        super().__init__(
            f"Unsupported SQLite journal mode: {journal_mode!r}. Allowed values: {allowed}."
        )


def connect_control_tower(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, isolation_level=None, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def configure_control_tower_journal_mode(
    connection: sqlite3.Connection,
    journal_mode: str = "DELETE",
) -> str:
    normalized_mode = journal_mode.upper()
    if normalized_mode not in ALLOWED_JOURNAL_MODES:
        raise UnsupportedJournalModeError(journal_mode)

    row = connection.execute(f"PRAGMA journal_mode = {normalized_mode}").fetchone()
    actual_mode = str(row[0]).upper() if row is not None else normalized_mode
    if actual_mode != normalized_mode:
        raise ControlTowerSqliteError(
            f"SQLite journal mode initialization failed: requested {normalized_mode}, got {actual_mode}."
        )
    return actual_mode
