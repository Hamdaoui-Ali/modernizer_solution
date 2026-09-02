-- V1-17D: Privileged action execution records
--
-- Append-only table for recording results of approved privileged
-- action executions. Each action may have at most one execution
-- (enforced by action_id PK constraint).
--
-- Results are stored with redacted summaries. No raw paths,
-- secrets, or sensitive data are stored.
--
-- Invariants preserved:
--   * Pipeline: springboot-216-to-356-java21-three-stage
--   * Stage 1: Java 11 / Boot 2.7.18 / legacy_source
--   * Stage 2: Java 17 / Boot 3.5.6 / previous_stage
--   * Stage 3: Java 21 / Boot 3.5.6 / previous_stage
--   * Boot 4 NOT selectable / 3.5.14 NOT execution-relevant
--   * No browser-selected raw paths, model deployments, or secrets
--   * LLM cannot execute, approve, write files, or create proof
--   * Shell is disabled by default
--   * Maven/write actions are typed privileged actions only

CREATE TABLE v1_privileged_action_executions (
    action_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    parameters_checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'executing'
        CHECK (status IN ('executing', 'completed', 'failed')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    result_summary TEXT,
    failure_reason TEXT,
    executed_by TEXT NOT NULL,
    execution_version TEXT NOT NULL DEFAULT '1.0',
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (action_id) REFERENCES v1_privileged_actions(action_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_privileged_action_executions_status
ON v1_privileged_action_executions(status);

CREATE INDEX ix_v1_privileged_action_executions_job_id
ON v1_privileged_action_executions(job_id);

CREATE TRIGGER v1_privileged_action_executions_no_update
BEFORE UPDATE ON v1_privileged_action_executions
BEGIN
    SELECT RAISE(ABORT, 'v1_privileged_action_executions is append-only');
END;

CREATE TRIGGER v1_privileged_action_executions_no_delete
BEFORE DELETE ON v1_privileged_action_executions
BEGIN
    SELECT RAISE(ABORT, 'v1_privileged_action_executions is append-only');
END;
