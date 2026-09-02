-- V1-15E: Roll back failed repair
--
-- Append-only persistence for patch rollback records.
-- A rollback requires:
--   1. An existing sandbox snapshot for the command.
--   2. A failed Maven validation (passed=0) for the patch application.
--   3. The patch application status is set to 'rolled_back'.
--
-- This table records the deterministic metadata of a rollback.
-- Actual file operations are handled by downstream privileged actions.

CREATE TABLE v1_patch_rollbacks (
    rollback_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    maven_validation_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index >= 1 AND stage_index <= 3),
    target_path_hash TEXT NOT NULL,
    rolled_back_by TEXT NOT NULL,
    rolled_back_at TEXT NOT NULL,
    reason_code TEXT NOT NULL
        CHECK (reason_code IN ('maven_validation_failed', 'patch_application_failed')),
    redacted_summary TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (command_id) REFERENCES command_executions(command_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id),
    FOREIGN KEY (application_id) REFERENCES v1_patch_applications(application_id),
    FOREIGN KEY (snapshot_id) REFERENCES v1_sandbox_snapshots(snapshot_id),
    FOREIGN KEY (maven_validation_id) REFERENCES v1_patch_maven_validations(maven_validation_id)
);

CREATE INDEX ix_v1_patch_rollbacks_command_id
ON v1_patch_rollbacks(command_id, rolled_back_at DESC);

CREATE INDEX ix_v1_patch_rollbacks_job_id
ON v1_patch_rollbacks(job_id, rolled_back_at DESC);

CREATE INDEX ix_v1_patch_rollbacks_application_id
ON v1_patch_rollbacks(application_id, rolled_back_at DESC);

CREATE TRIGGER v1_patch_rollbacks_no_update
BEFORE UPDATE ON v1_patch_rollbacks
BEGIN
    SELECT RAISE(ABORT, 'v1_patch_rollbacks is append-only');
END;

CREATE TRIGGER v1_patch_rollbacks_no_delete
BEFORE DELETE ON v1_patch_rollbacks
BEGIN
    SELECT RAISE(ABORT, 'v1_patch_rollbacks is append-only');
END;
