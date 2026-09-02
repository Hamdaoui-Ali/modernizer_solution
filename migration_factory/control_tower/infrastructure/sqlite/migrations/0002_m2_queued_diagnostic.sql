CREATE TABLE event_types (
    event_type TEXT PRIMARY KEY,
    description TEXT NOT NULL
);

INSERT INTO event_types (event_type, description)
VALUES
    ('job_created', 'M1 migration job was created'),
    ('job_state_changed', 'Migration job state changed'),
    ('artifact_registered', 'Artifact metadata was registered'),
    ('command_queued', 'Diagnostic command was queued');

CREATE TABLE run_events_new (
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

    FOREIGN KEY (event_type)
        REFERENCES event_types(event_type)
        ON DELETE RESTRICT,

    UNIQUE (job_id, sequence)
);

INSERT INTO run_events_new (
    event_id, job_id, sequence, event_type, actor_type, actor_id,
    correlation_id, causation_id, payload_json, payload_checksum, created_at
)
SELECT
    event_id, job_id, sequence, event_type, actor_type, actor_id,
    correlation_id, causation_id, payload_json, payload_checksum, created_at
FROM run_events;

DROP TABLE run_events;

ALTER TABLE run_events_new RENAME TO run_events;

CREATE INDEX ix_run_events_job_sequence
ON run_events(job_id, sequence);

CREATE TABLE command_executions (
    command_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,

    FOREIGN KEY (job_id)
        REFERENCES migration_jobs(job_id)
        ON DELETE RESTRICT,

    CHECK (
        status IN (
            'QUEUED',
            'STARTING',
            'RUNNING',
            'CANCELLING',
            'SUCCEEDED',
            'FAILED',
            'TIMED_OUT',
            'CANCELLED'
        )
    )
);

CREATE UNIQUE INDEX ux_one_nonterminal_command_per_job
ON command_executions(job_id)
WHERE status IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCELLING');

CREATE INDEX ix_command_executions_job_created_at
ON command_executions(job_id, created_at);

CREATE TABLE idempotency_records (
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_checksum TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    original_status_code INTEGER NOT NULL,
    created_at TEXT NOT NULL,

    PRIMARY KEY (operation, idempotency_key)
);
