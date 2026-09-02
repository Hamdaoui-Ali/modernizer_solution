-- V1-15D: Validate patch with typed Maven operation
--
-- Append-only persistence for typed Maven validation records
-- that verify applied patches. Only compile and test-compile
-- goals are allowed. Raw Maven goals, shell commands, and
-- arbitrary execution are rejected.

CREATE TABLE v1_patch_maven_validations (
    maven_validation_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    maven_goal TEXT NOT NULL
        CHECK (maven_goal IN ('compile', 'test-compile')),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    result_summary TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (application_id) REFERENCES v1_patch_applications(application_id),
    FOREIGN KEY (command_id) REFERENCES command_executions(command_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_patch_maven_validations_application_id
ON v1_patch_maven_validations(application_id, created_at DESC);

CREATE INDEX ix_v1_patch_maven_validations_job_id
ON v1_patch_maven_validations(job_id, created_at DESC);

CREATE TRIGGER v1_patch_maven_validations_no_update
BEFORE UPDATE ON v1_patch_maven_validations
BEGIN
    SELECT RAISE(ABORT, 'v1_patch_maven_validations is append-only');
END;

CREATE TRIGGER v1_patch_maven_validations_no_delete
BEFORE DELETE ON v1_patch_maven_validations
BEGIN
    SELECT RAISE(ABORT, 'v1_patch_maven_validations is append-only');
END;
