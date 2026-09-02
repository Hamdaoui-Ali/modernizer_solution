-- V1-17A: Persist pending privileged actions
--
-- Append-only table for privileged action records.
-- Privileged actions are typed (Maven, write) with structured
-- parameters, policy references, checksums, actor attribution,
-- status tracking, and audit trails.
--
-- This table stores actions that are requested but not yet
-- approved or executed. Approval belongs to V1-17C. Execution
-- belongs to V1-17D. Policy/checksum validation beyond basic
-- storage belongs to V1-17B.
--
-- Invariants preserved:
--   * Pipeline ID remains springboot-216-to-356-java21-three-stage
--   * Stage 1: Java 11 / Spring Boot 2.7.18 / legacy_source
--   * Stage 2: Java 17 / Spring Boot 3.5.6 / previous_stage (stage 1)
--   * Stage 3: Java 21 / Spring Boot 3.5.6 / previous_stage (stage 2)
--   * Boot 4 NOT selectable
--   * 3.5.14 NOT execution-relevant
--   * No browser-selected raw paths, model deployments, or secrets
--   * LLM cannot execute, approve, write files, or create proof
--   * Shell is disabled by default; only typed Maven/write actions execute

CREATE TABLE v1_privileged_actions (
    action_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type IN ('maven', 'write')),
    action_version TEXT NOT NULL DEFAULT '1.0',
    parameters_json TEXT NOT NULL,
    parameters_checksum TEXT NOT NULL,
    policy_json TEXT,
    policy_version TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'executing', 'completed', 'failed')),
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    rejected_by TEXT,
    rejected_reason TEXT,
    executed_at TEXT,
    failure_reason TEXT,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_privileged_actions_job_id
ON v1_privileged_actions(job_id);

CREATE INDEX ix_v1_privileged_actions_status
ON v1_privileged_actions(status);

CREATE INDEX ix_v1_privileged_actions_requested_at
ON v1_privileged_actions(requested_at);

CREATE TRIGGER v1_privileged_actions_no_update
BEFORE UPDATE ON v1_privileged_actions
BEGIN
    SELECT RAISE(ABORT, 'v1_privileged_actions is append-only; use new record for status changes');
END;

CREATE TRIGGER v1_privileged_actions_no_delete
BEFORE DELETE ON v1_privileged_actions
BEGIN
    SELECT RAISE(ABORT, 'v1_privileged_actions is append-only');
END;
