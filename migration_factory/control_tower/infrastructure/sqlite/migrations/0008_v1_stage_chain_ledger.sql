-- V1-03A: Add stage-chain ledger schema
--
-- Records the three-stage chain progress for each V1 migration job.
-- Each job gets exactly three immutable chain entries linked to the
-- stage_runs that satisfy the stage's requirements.
--
-- Tables:
--   v1_stage_chain_ledger     - one row per job per stage in the chain
--   v1_stage_output_registry  - registered output artifacts from each stage
--   v1_stage_chain_events     - append-only event log for stage transitions
--
-- Invariants preserved:
--   * Pipeline ID: springboot-216-to-356-java21-three-stage
--   * Stage 1: Java 11 / Spring Boot 2.7.18 / legacy_source
--   * Stage 2: Java 17 / Spring Boot 3.5.6 / previous_stage (stage 1)
--   * Stage 3: Java 21 / Spring Boot 3.5.6 / previous_stage (stage 2)
--   * Ledger rows are append-only with no UPDATE or DELETE
--   * Boot 4 NOT selectable
--   * 3.5.14 NOT execution-relevant

CREATE TABLE v1_stage_chain_ledger (
    ledger_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES migration_jobs(job_id),
    stage_index INTEGER NOT NULL CHECK (stage_index >= 1 AND stage_index <= 3),
    stage_run_id TEXT NOT NULL,
    chain_status TEXT NOT NULL DEFAULT 'pending' CHECK (chain_status IN ('pending', 'in_progress', 'completed', 'failed', 'skipped')),
    input_source_kind TEXT NOT NULL,
    input_checksum TEXT,
    output_artifact_id TEXT,
    output_checksum TEXT,
    output_registered_at TEXT,
    checksum_guard TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    UNIQUE(job_id, stage_index),
    FOREIGN KEY (stage_run_id) REFERENCES stage_runs(stage_run_id)
);

CREATE TRIGGER v1_stage_chain_ledger_no_update
BEFORE UPDATE ON v1_stage_chain_ledger
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_chain_ledger is append-only');
END;

CREATE TRIGGER v1_stage_chain_ledger_no_delete
BEFORE DELETE ON v1_stage_chain_ledger
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_chain_ledger is append-only');
END;

CREATE TABLE v1_stage_output_registry (
    output_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES migration_jobs(job_id),
    stage_index INTEGER NOT NULL CHECK (stage_index >= 1 AND stage_index <= 3),
    stage_run_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    artifact_type TEXT NOT NULL,
    output_kind TEXT NOT NULL CHECK (output_kind IN ('sandbox', 'manifest', 'evidence', 'proof')),
    checksum_algorithm TEXT NOT NULL,
    checksum TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    registered_by TEXT NOT NULL,
    FOREIGN KEY (job_id, stage_index) REFERENCES v1_stage_chain_ledger(job_id, stage_index),
    FOREIGN KEY (stage_run_id) REFERENCES stage_runs(stage_run_id)
);

CREATE TRIGGER v1_stage_output_registry_no_update
BEFORE UPDATE ON v1_stage_output_registry
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_output_registry is append-only');
END;

CREATE TRIGGER v1_stage_output_registry_no_delete
BEFORE DELETE ON v1_stage_output_registry
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_output_registry is append-only');
END;

CREATE TABLE v1_stage_chain_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES migration_jobs(job_id),
    stage_index INTEGER CHECK (stage_index IS NULL OR (stage_index >= 1 AND stage_index <= 3)),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'chain_created',
        'chain_started',
        'chain_completed',
        'chain_failed',
        'stage_started',
        'stage_completed',
        'stage_failed',
        'output_registered'
    )),
    prior_status TEXT,
    new_status TEXT,
    ledger_id TEXT REFERENCES v1_stage_chain_ledger(ledger_id),
    output_id TEXT REFERENCES v1_stage_output_registry(output_id),
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE INDEX ix_v1_stage_chain_events_job_id
ON v1_stage_chain_events(job_id);

CREATE INDEX ix_v1_stage_chain_events_created_at
ON v1_stage_chain_events(created_at);

CREATE TRIGGER v1_stage_chain_events_no_update
BEFORE UPDATE ON v1_stage_chain_events
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_chain_events are append-only');
END;

CREATE TRIGGER v1_stage_chain_events_no_delete
BEFORE DELETE ON v1_stage_chain_events
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_chain_events are append-only');
END;
