-- V2-P0-001: Job and command manifest persistence
--
-- This migration adds tables to persist V2 migration jobs and
-- stage command manifests. These are the core entities that make
-- the V2 migration flow durable across restarts.
--
-- Previously, V2MigrationJobService and V2WorkerStageService returned
-- in-memory-only results. This migration provides the backing store.
--
-- Invariants preserved:
--   * Jobs are append-only (no update, no delete).
--   * Commands are append-only (no update, no delete).
--   * A job references a unique setup checksum.
--   * A command references a job and stage index.
--   * Browser cannot supply argv/env — manifest is backend-owned.
--   * Stage inputs are fixed by pipeline, not user-selectable.

CREATE TABLE v2_migration_jobs (
    job_id TEXT PRIMARY KEY,
    setup_id TEXT NOT NULL,
    setup_checksum TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    stage_chain_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    correlation_id TEXT
);

CREATE INDEX ix_v2_migration_jobs_setup
ON v2_migration_jobs(setup_checksum);

CREATE INDEX ix_v2_migration_jobs_status
ON v2_migration_jobs(status);

CREATE TABLE v2_stage_commands (
    command_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3)),
    manifest_checksum TEXT NOT NULL,
    argv_json TEXT NOT NULL DEFAULT '[]',
    env_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'manifest_ready',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    result_json TEXT
);

CREATE INDEX ix_v2_stage_commands_job
ON v2_stage_commands(job_id, stage_index);

CREATE TRIGGER v2_migration_jobs_no_update
BEFORE UPDATE ON v2_migration_jobs
BEGIN
    SELECT RAISE(ABORT, 'v2_migration_jobs is append-only');
END;

CREATE TRIGGER v2_migration_jobs_no_delete
BEFORE DELETE ON v2_migration_jobs
BEGIN
    SELECT RAISE(ABORT, 'v2_migration_jobs is append-only');
END;

CREATE TRIGGER v2_stage_commands_no_update
BEFORE UPDATE ON v2_stage_commands
BEGIN
    SELECT RAISE(ABORT, 'v2_stage_commands is append-only');
END;

CREATE TRIGGER v2_stage_commands_no_delete
BEFORE DELETE ON v2_stage_commands
BEGIN
    SELECT RAISE(ABORT, 'v2_stage_commands is append-only');
END;
