-- V1-08A: Enforce stage continuation policy
--
-- This migration adds tables to track and enforce stage continuation
-- policy for the V1 pipeline (springboot-216-to-356-java21-three-stage).
--
-- Stage 2 MUST use the Stage 1 sandbox output only.
-- Stage 3 MUST use the Stage 2 sandbox output only.
--
-- Each continuation policy entry records the input checksum and the
-- expected prior stage output checksum, so the system can deterministically
-- verify that each subsequent stage reads from the correct prior sandbox.
--
-- Policy violations produce deterministic Blocked/Queued/Failed events.
--
-- Invariants preserved:
--   * Stage 2 reads from Stage 1 sandbox only.
--   * Stage 3 reads from Stage 2 sandbox only.
--   * Locked route remains springboot-216-to-356-java21-three-stage.
--   * Boot 4 is NOT selectable.
--   * 3.5.14 is NOT execution-relevant for V1.
--   * Browser payloads cannot choose raw paths, Maven goals, shell
--     commands, working directories, or model deployments.
--   * LLM flows cannot execute commands, approve decisions, or write
--     files directly.

CREATE TABLE v1_stage_continuation_policy (
    policy_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL,
    pipeline_id TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    chain_rule TEXT NOT NULL CHECK (chain_rule IN ('previous_stage_sandbox')),
    expected_prior_stage_index INTEGER,
    expected_prior_output_checksum TEXT,
    input_checksum TEXT NOT NULL,
    sandbox_root_id TEXT,
    sandbox_relative_path TEXT,
    policy_status TEXT NOT NULL DEFAULT 'pending' CHECK (policy_status IN ('pending', 'matched', 'mismatched', 'orphaned')),
    created_at TEXT NOT NULL,
    checked_at TEXT,
    failure_reason TEXT,
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_stage_continuation_policy_job_stage
ON v1_stage_continuation_policy(job_id, stage_index);

CREATE INDEX ix_v1_stage_continuation_policy_status
ON v1_stage_continuation_policy(policy_status, created_at);

CREATE TABLE v1_stage_continuation_event (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_index INTEGER,
    policy_id TEXT,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'policy_created',
        'policy_matched',
        'policy_mismatched',
        'policy_orphaned',
        'stage_blocked',
        'stage_queued',
        'stage_failed'
    )),
    prior_status TEXT,
    new_status TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    payload_checksum TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX ix_v1_stage_continuation_event_job
ON v1_stage_continuation_event(job_id, stage_index, created_at DESC);

CREATE TRIGGER v1_stage_continuation_policy_no_update
BEFORE UPDATE OF policy_id, job_id, stage_index, pipeline_id, pipeline_version, chain_rule, expected_prior_stage_index, expected_prior_output_checksum, input_checksum, sandbox_root_id, sandbox_relative_path, created_at
ON v1_stage_continuation_policy
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_continuation_policy core fields are append-only');
END;

CREATE TRIGGER v1_stage_continuation_policy_no_delete
BEFORE DELETE ON v1_stage_continuation_policy
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_continuation_policy is append-only');
END;

CREATE TRIGGER v1_stage_continuation_event_no_update
BEFORE UPDATE ON v1_stage_continuation_event
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_continuation_event is append-only');
END;

CREATE TRIGGER v1_stage_continuation_event_no_delete
BEFORE DELETE ON v1_stage_continuation_event
BEGIN
    SELECT RAISE(ABORT, 'v1_stage_continuation_event is append-only');
END;
