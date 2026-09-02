-- V1-15C: Apply approved patch in sandbox
--
-- Append-only persistence for patch application records.
-- Ties together the policy validation and sandbox snapshot
-- that preceded the application. Actual patch content is
-- never stored.

CREATE TABLE v1_patch_applications (
    application_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    validation_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index >= 1 AND stage_index <= 3),
    target_path_hash TEXT NOT NULL,
    patch_size_bytes INTEGER NOT NULL CHECK (patch_size_bytes >= 0),
    applied_by TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'applied'
        CHECK (status IN ('applied', 'validated', 'rolled_back')),
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (command_id) REFERENCES command_executions(command_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id),
    FOREIGN KEY (validation_id) REFERENCES v1_patch_policy_validations(validation_id),
    FOREIGN KEY (snapshot_id) REFERENCES v1_sandbox_snapshots(snapshot_id)
);

CREATE INDEX ix_v1_patch_applications_command_id
ON v1_patch_applications(command_id, applied_at DESC);

CREATE INDEX ix_v1_patch_applications_job_id
ON v1_patch_applications(job_id, applied_at DESC);

CREATE TRIGGER v1_patch_applications_no_update
BEFORE UPDATE ON v1_patch_applications
BEGIN
    SELECT RAISE(ABORT, 'v1_patch_applications is append-only');
END;

CREATE TRIGGER v1_patch_applications_no_delete
BEFORE DELETE ON v1_patch_applications
BEGIN
    SELECT RAISE(ABORT, 'v1_patch_applications is append-only');
END;
