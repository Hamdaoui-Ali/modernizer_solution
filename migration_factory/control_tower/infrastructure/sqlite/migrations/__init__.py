"""Safe SQLite migration runner for Control Tower.

Immutable migration policy
---------------------------
* Every migration file (NNNN_name.sql) is **append-only** and must never be
  modified after it is merged to the default branch.
* Any schema change that follows an already-merged migration MUST be a NEW
  file (0036, 0037, …).  Do NOT edit an existing migration file.
* The migration runner verifies a SHA-256 checksum of every applied
  migration against the current file on disk.  A mismatch is a hard error
  in production to prevent silent drift.
* In local dev (``CONTROL_TOWER_DEV_MODE=1``), a checksum mismatch
  auto-resets the dev database and re-applies all migrations from scratch.

Recovering from a checksum mismatch in local dev
------------------------------------------------
If you hit ``AppliedMigrationChecksumMismatchError`` during local
development::

   1. Stop the backend process.
   2. Set ``CONTROL_TOWER_DEV_MODE=1`` (already set in dev_app.py).
   3. Restart – the runner will auto-delete the stale database and
      re-migrate.
   4. All local state (jobs, runs) is lost; this is expected for dev.

Alternative (manual)::

   1. Stop the backend.
   2. Delete ``%USERPROFILE%\\.local\\state\\ai-migration-control-tower\\control_tower.sqlite3``
      (or the path in ``CONTROL_TOWER_DB_PATH``).
   3. Restart.

Do NOT auto-delete the database in production or in any path where
``CONTROL_TOWER_DEV_MODE`` is absent or set to ``0``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
from typing import Sequence

from migration_factory.control_tower.infrastructure.sqlite.connection import (
    configure_control_tower_journal_mode,
    connect_control_tower,
)


MIGRATION_FILENAME_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
BLOCKED_STATEMENT_KEYWORDS = frozenset(
    {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}
)
BLOCKED_PRAGMAS = frozenset({"FOREIGN_KEYS", "JOURNAL_MODE", "LOCKING_MODE"})


class MigrationError(Exception):
    """Base exception for Control Tower migrations."""


class MigrationDiscoveryError(MigrationError):
    """Raised when migration files are missing or invalid."""


class AppliedMigrationMissingError(MigrationDiscoveryError):
    """Raised when schema history references a migration no longer on disk."""


class MigrationSafetyError(MigrationError):
    """Raised when migration SQL violates safety rules."""


class MigrationExecutionError(MigrationError):
    """Raised when migration execution fails."""


class AppliedMigrationChecksumMismatchError(MigrationError):
    """Raised when applied migration bytes differ from discovered bytes."""

    def __init__(self, version: int, expected_checksum: str, actual_checksum: str) -> None:
        self.version = version
        self.expected_checksum = expected_checksum
        self.actual_checksum = actual_checksum
        super().__init__(
            "Checksum mismatch for applied migration "
            f"{version:04d}: expected {expected_checksum}, found {actual_checksum}."
        )


@dataclass(frozen=True)
class MigrationFile:
    version: int
    name: str
    path: Path
    checksum: str
    sql: str


def migrate_control_tower(
    db_path: Path,
    *,
    journal_mode: str = "DELETE",
    migrations_dir: Path | None = None,
) -> list[MigrationFile]:
    connection = connect_control_tower(db_path)
    try:
        configure_control_tower_journal_mode(connection, journal_mode=journal_mode)
        return apply_pending_migrations(connection, migrations_dir=migrations_dir)
    finally:
        connection.close()


def apply_pending_migrations(
    connection: sqlite3.Connection,
    *,
    migrations_dir: Path | None = None,
) -> list[MigrationFile]:
    discovered = discover_migrations(migrations_dir)
    applied = _load_applied_migrations(connection)
    try:
        _verify_applied_checksums(applied, discovered)
    except (AppliedMigrationChecksumMismatchError, AppliedMigrationMissingError):
        if not _is_dev_mode():
            raise
        _dev_reset_database(connection)
        applied = {}

    pending = [migration for migration in discovered if migration.version not in applied]
    for migration in pending:
        _apply_single_migration(connection, migration)

    _run_foreign_key_check(connection)
    return pending


def discover_migrations(migrations_dir: Path | None = None) -> list[MigrationFile]:
    directory = _default_migrations_dir() if migrations_dir is None else migrations_dir
    if not directory.is_dir():
        raise MigrationDiscoveryError(f"Migration directory not found: {directory}")

    discovered: list[MigrationFile] = []
    seen_versions: set[int] = set()
    last_version = -1

    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_FILENAME_RE.fullmatch(path.name)
        if match is None:
            raise MigrationDiscoveryError(f"Invalid migration filename: {path.name}")

        version = int(match.group("version"))
        if version in seen_versions:
            raise MigrationDiscoveryError(f"Duplicate migration version: {version:04d}")
        if version <= last_version:
            raise MigrationDiscoveryError(
                f"Migration versions must be strictly ascending: {path.name}"
            )

        file_bytes = path.read_bytes()
        discovered.append(
            MigrationFile(
                version=version,
                name=match.group("name"),
                path=path,
                checksum=hashlib.sha256(file_bytes).hexdigest(),
                sql=file_bytes.decode("utf-8"),
            )
        )
        seen_versions.add(version)
        last_version = version

    return discovered


def split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    token_buffer: list[str] = []
    statement_tokens: list[str] = []
    in_line_comment = False
    in_block_comment = False
    in_single_quote = False
    in_double_quote = False
    in_bracket_quote = False
    in_backtick_quote = False
    trigger_candidate = False
    in_trigger_body = False
    pending_trigger_end = False
    index = 0

    def flush_token() -> None:
        nonlocal trigger_candidate, in_trigger_body, pending_trigger_end
        if not token_buffer:
            return
        token = "".join(token_buffer)
        token_buffer.clear()
        upper_token = token.upper()
        statement_tokens.append(upper_token)

        if len(statement_tokens) >= 2 and statement_tokens[:2] == ["CREATE", "TRIGGER"]:
            trigger_candidate = True
        elif (
            len(statement_tokens) >= 3
            and statement_tokens[:3] == ["CREATE", "TEMP", "TRIGGER"]
        ):
            trigger_candidate = True
        elif (
            len(statement_tokens) >= 3
            and statement_tokens[:3] == ["CREATE", "TEMPORARY", "TRIGGER"]
        ):
            trigger_candidate = True

        if trigger_candidate and upper_token == "BEGIN" and not in_trigger_body:
            in_trigger_body = True
            pending_trigger_end = False
            return

        if in_trigger_body and upper_token == "END":
            pending_trigger_end = True
            return

        if pending_trigger_end:
            pending_trigger_end = False

    def finalize_statement() -> None:
        nonlocal statement_tokens, trigger_candidate, in_trigger_body, pending_trigger_end
        flush_token()
        statement = "".join(buffer).strip()
        buffer.clear()
        statement_tokens = []
        trigger_candidate = False
        in_trigger_body = False
        pending_trigger_end = False
        if not statement:
            return
        if not _is_complete_statement_text(statement):
            raise MigrationSafetyError("Incomplete SQL statement in migration.")
        statements.append(statement)

    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if in_line_comment:
            buffer.append(char)
            if char == "\n":
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            buffer.append(char)
            if char == "*" and next_char == "/":
                buffer.append(next_char)
                index += 2
                in_block_comment = False
                continue
            index += 1
            continue

        if in_single_quote:
            buffer.append(char)
            if char == "'" and next_char == "'":
                buffer.append(next_char)
                index += 2
                continue
            if char == "'":
                in_single_quote = False
            index += 1
            continue

        if in_double_quote:
            buffer.append(char)
            if char == '"':
                in_double_quote = False
            index += 1
            continue

        if in_bracket_quote:
            buffer.append(char)
            if char == "]":
                in_bracket_quote = False
            index += 1
            continue

        if in_backtick_quote:
            buffer.append(char)
            if char == "`":
                in_backtick_quote = False
            index += 1
            continue

        if char == "-" and next_char == "-":
            flush_token()
            buffer.append(char)
            buffer.append(next_char)
            index += 2
            in_line_comment = True
            continue

        if char == "/" and next_char == "*":
            flush_token()
            buffer.append(char)
            buffer.append(next_char)
            index += 2
            in_block_comment = True
            continue

        if char == "'":
            flush_token()
            buffer.append(char)
            in_single_quote = True
            index += 1
            continue

        if char == '"':
            flush_token()
            buffer.append(char)
            in_double_quote = True
            index += 1
            continue

        if char == "[":
            flush_token()
            buffer.append(char)
            in_bracket_quote = True
            index += 1
            continue

        if char == "`":
            flush_token()
            buffer.append(char)
            in_backtick_quote = True
            index += 1
            continue

        if char.isalnum() or char == "_":
            token_buffer.append(char)
            buffer.append(char)
            index += 1
            continue

        flush_token()

        if pending_trigger_end:
            if char.isspace():
                buffer.append(char)
                index += 1
                continue
            if char == ";":
                buffer.append(char)
                index += 1
                finalize_statement()
                continue
            pending_trigger_end = False

        if char == ";" and not in_trigger_body:
            buffer.append(char)
            index += 1
            finalize_statement()
            continue

        buffer.append(char)
        index += 1

    flush_token()
    tail = "".join(buffer).strip()
    if tail:
        finalize_statement()
    return statements


def _apply_single_migration(connection: sqlite3.Connection, migration: MigrationFile) -> None:
    statements = split_sql_statements(migration.sql)
    transaction_started = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        transaction_started = True
        for statement in statements:
            _validate_migration_statement(statement)
            connection.execute(statement)
        _run_foreign_key_check(connection)
        connection.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum, applied_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                migration.version,
                migration.name,
                migration.checksum,
                _utc_now_text(),
            ),
        )
        connection.execute("COMMIT")
    except Exception as exc:
        if transaction_started and connection.in_transaction:
            connection.execute("ROLLBACK")
        raise MigrationExecutionError(
            f"Migration {migration.version:04d}_{migration.name} failed: {exc}"
        ) from exc


def _validate_migration_statement(statement: str) -> None:
    tokens = _leading_keyword_tokens(statement)
    if not tokens:
        return

    if tokens[0] in BLOCKED_STATEMENT_KEYWORDS:
        raise MigrationSafetyError(
            f"Migration contains forbidden transaction-control statement: {tokens[0]}."
        )

    if tokens[0] == "PRAGMA":
        pragma_name = _pragma_name(tokens)
        if pragma_name in BLOCKED_PRAGMAS:
            raise MigrationSafetyError(
                f"Migration contains forbidden PRAGMA statement: {pragma_name}."
            )


def _leading_keyword_tokens(statement: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    in_line_comment = False
    in_block_comment = False
    index = 0

    while index < len(statement) and len(tokens) < 3:
        char = statement[index]
        next_char = statement[index + 1] if index + 1 < len(statement) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                index += 2
                in_block_comment = False
                continue
            index += 1
            continue

        if char == "-" and next_char == "-":
            index += 2
            in_line_comment = True
            continue

        if char == "/" and next_char == "*":
            index += 2
            in_block_comment = True
            continue

        if char.isalpha() or char == "_":
            current.append(char)
            index += 1
            continue

        if current:
            tokens.append("".join(current).upper())
            current.clear()

        if char.isspace() or char in ".=();,":
            index += 1
            continue

        break

    if current and len(tokens) < 3:
        tokens.append("".join(current).upper())
    return tokens


def _pragma_name(tokens: Sequence[str]) -> str | None:
    if len(tokens) < 2:
        return None
    if tokens[1] == "MAIN" and len(tokens) >= 3:
        return tokens[2]
    return tokens[1]


def _run_foreign_key_check(connection: sqlite3.Connection) -> None:
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise MigrationSafetyError(f"Foreign key check failed: {violations!r}")


def _load_applied_migrations(connection: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    table_exists = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).fetchone()
    if table_exists is None:
        return {}

    rows = connection.execute(
        """
        SELECT version, name, checksum, applied_at
        FROM schema_migrations
        ORDER BY version ASC
        """
    ).fetchall()
    return {int(row["version"]): row for row in rows}


def _verify_applied_checksums(
    applied: dict[int, sqlite3.Row],
    discovered: Sequence[MigrationFile],
) -> None:
    by_version = {migration.version: migration for migration in discovered}
    for version, row in applied.items():
        migration = by_version.get(version)
        if migration is None:
            raise AppliedMigrationMissingError(
                f"Applied migration missing from disk: {version:04d}"
            )
        actual_checksum = str(row["checksum"])
        if actual_checksum != migration.checksum:
            raise AppliedMigrationChecksumMismatchError(
                version,
                actual_checksum,
                migration.checksum,
            )


def _default_migrations_dir() -> Path:
    return Path(__file__).resolve().parent


def _is_complete_statement_text(statement: str) -> bool:
    stripped = statement.strip()
    return sqlite3.complete_statement(stripped) or sqlite3.complete_statement(
        stripped + ";"
    )


def _utc_now_text() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _is_dev_mode() -> bool:
    """Return True when CONTROL_TOWER_DEV_MODE is set to a truthy value.

    Only in dev mode may the migration runner auto-reset the database on
    stale local schema history.
    """
    val = os.environ.get("CONTROL_TOWER_DEV_MODE", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _dev_reset_database(connection: sqlite3.Connection) -> None:
    """Drop every user table in the database so migrations start clean.

    This is only called in dev mode after stale schema history is detected.
    The schema_migrations table itself is dropped last so the next
    apply_pending_migrations call sees zero applied migrations.
    """
    # Get all user table names (exclude sqlite_ internal tables)
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    table_names = [row[0] for row in rows]

    # Drop in dependency-safe order: schema_migrations last
    if "schema_migrations" in table_names:
        table_names.remove("schema_migrations")
        table_names.append("schema_migrations")

    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        for name in table_names:
            # Use double-quote escaping (SQLite standard)
            safe_name = name.replace('"', '""')
            connection.execute(f'DROP TABLE IF EXISTS "{safe_name}"')
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
