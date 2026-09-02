"""SQLite foundation for Control Tower persistence."""

from migration_factory.control_tower.infrastructure.sqlite.artifact_paths import (
    ArtifactHashResult,
    ValidatedArtifactPath,
    hash_registered_artifact,
    normalize_registered_relative_path,
    validate_registered_artifact_path,
)
from migration_factory.control_tower.infrastructure.sqlite.connection import (
    ControlTowerSqliteError,
    UnsupportedJournalModeError,
    configure_control_tower_journal_mode,
    connect_control_tower,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    AppliedMigrationChecksumMismatchError,
    MigrationDiscoveryError,
    MigrationExecutionError,
    MigrationFile,
    MigrationSafetyError,
    apply_pending_migrations,
    discover_migrations,
    migrate_control_tower,
    split_sql_statements,
)
from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqliteArtifactRepository,
    SqliteAuditRecordRepository,
    SqliteMigrationJobRepository,
    SqlitePipelineDefinitionRepository,
    SqliteRunConfigurationRepository,
    SqliteRunEventRepository,
    SqliteRunnerProfileRepository,
    SqliteStageRunRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteControlTowerUnitOfWork,
    SqliteUnitOfWork,
)

__all__ = [
    "AppliedMigrationChecksumMismatchError",
    "ArtifactHashResult",
    "ControlTowerSqliteError",
    "MigrationDiscoveryError",
    "MigrationExecutionError",
    "MigrationFile",
    "MigrationSafetyError",
    "SqliteArtifactRepository",
    "SqliteAuditRecordRepository",
    "SqliteControlTowerUnitOfWork",
    "SqliteMigrationJobRepository",
    "SqlitePipelineDefinitionRepository",
    "SqliteRunConfigurationRepository",
    "SqliteRunEventRepository",
    "SqliteRunnerProfileRepository",
    "SqliteStageRunRepository",
    "SqliteUnitOfWork",
    "UnsupportedJournalModeError",
    "ValidatedArtifactPath",
    "apply_pending_migrations",
    "configure_control_tower_journal_mode",
    "connect_control_tower",
    "discover_migrations",
    "hash_registered_artifact",
    "migrate_control_tower",
    "normalize_registered_relative_path",
    "split_sql_statements",
    "validate_registered_artifact_path",
]
