CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE runner_profiles (
    runner_profile_id TEXT NOT NULL,
    runner_profile_version TEXT NOT NULL,
    display_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    PRIMARY KEY (runner_profile_id, runner_profile_version)
);

CREATE TABLE pipeline_definitions (
    pipeline_id TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    display_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    graph_version TEXT NOT NULL,
    graph_state_schema_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    PRIMARY KEY (pipeline_id, pipeline_version)
);

CREATE TABLE migration_jobs (
    job_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    status TEXT NOT NULL,
    active_slot INTEGER CHECK (active_slot IS NULL OR active_slot = 1),
    last_event_sequence INTEGER NOT NULL DEFAULT 0
        CHECK (last_event_sequence >= 0),

    runner_profile_id TEXT NOT NULL,
    runner_profile_version TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,

    target_proof_level TEXT NOT NULL,
    achieved_proof_level TEXT,

    legacy_source_ref TEXT NOT NULL,
    output_root_ref TEXT NOT NULL,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    created_by TEXT NOT NULL,

    FOREIGN KEY (runner_profile_id, runner_profile_version)
        REFERENCES runner_profiles(
            runner_profile_id,
            runner_profile_version
        )
        ON DELETE RESTRICT,

    FOREIGN KEY (pipeline_id, pipeline_version)
        REFERENCES pipeline_definitions(
            pipeline_id,
            pipeline_version
        )
        ON DELETE RESTRICT,

    CHECK (
        status IN (
            'CREATED',
            'QUEUED',
            'STARTING',
            'RUNNING',
            'PAUSED_FOR_PLAN_APPROVAL',
            'PAUSED_FOR_REPAIR',
            'RESUMING',
            'CANCELLING',
            'ORPHANED',
            'RECOVERY_REQUIRED',
            'COMPLETED',
            'FAILED',
            'REJECTED',
            'CANCELLED'
        )
    ),

    CHECK (
        target_proof_level IN (
            'ANALYZED',
            'PLANNED',
            'TRANSFORMED',
            'BUILD_TEST_VERIFIED',
            'RUNTIME_VERIFIED',
            'ENDPOINT_VERIFIED'
        )
    ),

    CHECK (
        achieved_proof_level IS NULL
        OR achieved_proof_level IN (
            'ANALYZED',
            'PLANNED',
            'TRANSFORMED',
            'BUILD_TEST_VERIFIED',
            'RUNTIME_VERIFIED',
            'ENDPOINT_VERIFIED'
        )
    ),

    CHECK (
        (
            status IN (
                'CREATED',
                'QUEUED',
                'STARTING',
                'RUNNING',
                'PAUSED_FOR_PLAN_APPROVAL',
                'PAUSED_FOR_REPAIR',
                'RESUMING',
                'CANCELLING',
                'ORPHANED',
                'RECOVERY_REQUIRED'
            )
            AND active_slot = 1
        )
        OR
        (
            status IN (
                'COMPLETED',
                'FAILED',
                'REJECTED',
                'CANCELLED'
            )
            AND active_slot IS NULL
        )
    )
);

CREATE UNIQUE INDEX ux_one_active_job
ON migration_jobs(active_slot)
WHERE active_slot = 1;

CREATE INDEX ix_migration_jobs_status
ON migration_jobs(status);

CREATE INDEX ix_migration_jobs_created_at
ON migration_jobs(created_at);

CREATE TABLE run_configurations (
    run_configuration_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,

    schema_version TEXT NOT NULL,
    runner_profile_id TEXT NOT NULL,
    runner_profile_version TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,

    target_proof_level TEXT NOT NULL,
    enabled_gates_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,

    FOREIGN KEY (job_id)
        REFERENCES migration_jobs(job_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (runner_profile_id, runner_profile_version)
        REFERENCES runner_profiles(
            runner_profile_id,
            runner_profile_version
        )
        ON DELETE RESTRICT,

    FOREIGN KEY (pipeline_id, pipeline_version)
        REFERENCES pipeline_definitions(
            pipeline_id,
            pipeline_version
        )
        ON DELETE RESTRICT,

    CHECK (
        target_proof_level IN (
            'ANALYZED',
            'PLANNED',
            'TRANSFORMED',
            'BUILD_TEST_VERIFIED',
            'RUNTIME_VERIFIED',
            'ENDPOINT_VERIFIED'
        )
    )
);

CREATE TABLE stage_runs (
    stage_run_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index >= 1),
    stage_id TEXT NOT NULL,
    status TEXT NOT NULL,
    input_source_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,

    FOREIGN KEY (job_id)
        REFERENCES migration_jobs(job_id)
        ON DELETE RESTRICT,

    UNIQUE (job_id, stage_index),

    CHECK (
        status IN (
            'PENDING',
            'READY',
            'RUNNING',
            'PAUSED',
            'PASSED',
            'PASSED_WITH_WARNINGS',
            'FAILED',
            'SKIPPED_BY_POLICY',
            'BLOCKED',
            'CANCELLED'
        )
    )
);

CREATE TABLE run_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,

    FOREIGN KEY (job_id)
        REFERENCES migration_jobs(job_id)
        ON DELETE RESTRICT,

    UNIQUE (job_id, sequence),

    CHECK (
        event_type IN (
            'job_created',
            'job_state_changed',
            'artifact_registered'
        )
    )
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_run_id TEXT,

    artifact_type TEXT NOT NULL,
    registered_root_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    normalized_relative_path TEXT NOT NULL,

    content_type TEXT,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    checksum_algorithm TEXT NOT NULL,
    checksum TEXT NOT NULL,

    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,

    FOREIGN KEY (job_id)
        REFERENCES migration_jobs(job_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (stage_run_id)
        REFERENCES stage_runs(stage_run_id)
        ON DELETE RESTRICT,

    UNIQUE (
        job_id,
        registered_root_id,
        normalized_relative_path
    )
);

CREATE TABLE audit_records (
    audit_id TEXT PRIMARY KEY,
    job_id TEXT,

    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,

    prior_state TEXT,
    new_state TEXT,
    job_version INTEGER,

    correlation_id TEXT,
    causation_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,

    FOREIGN KEY (job_id)
        REFERENCES migration_jobs(job_id)
        ON DELETE RESTRICT
);

CREATE INDEX ix_stage_runs_job_id
ON stage_runs(job_id);

CREATE INDEX ix_run_events_job_sequence
ON run_events(job_id, sequence);

CREATE INDEX ix_artifacts_job_id
ON artifacts(job_id);

CREATE INDEX ix_audit_records_job_created_at
ON audit_records(job_id, created_at);

CREATE TRIGGER audit_records_no_update
BEFORE UPDATE ON audit_records
BEGIN
    SELECT RAISE(ABORT, 'audit_records are append-only');
END;

CREATE TRIGGER audit_records_no_delete
BEFORE DELETE ON audit_records
BEGIN
    SELECT RAISE(ABORT, 'audit_records are append-only');
END;
