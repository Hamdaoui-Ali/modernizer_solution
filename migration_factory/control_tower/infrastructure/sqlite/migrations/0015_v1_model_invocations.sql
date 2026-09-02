-- V1-10: Audit model invocations
--
-- Append-only table for model invocation audit records.
-- Stores only redacted summaries, token counts, and profile refs.
-- Raw prompts, secrets, and deployment IDs are never stored.
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

CREATE TABLE v1_model_invocations (
    invocation_id TEXT PRIMARY KEY,
    job_id TEXT,
    profile_id TEXT,
    provider_kind TEXT,
    model_name TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    redacted_summary TEXT,
    actor_type TEXT,
    actor_id TEXT,
    created_at TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_model_invocations_job_id
ON v1_model_invocations(job_id);

CREATE INDEX ix_v1_model_invocations_created_at
ON v1_model_invocations(created_at);

CREATE TRIGGER v1_model_invocations_no_update
BEFORE UPDATE ON v1_model_invocations
BEGIN
    SELECT RAISE(ABORT, 'v1_model_invocations is append-only');
END;

CREATE TRIGGER v1_model_invocations_no_delete
BEFORE DELETE ON v1_model_invocations
BEGIN
    SELECT RAISE(ABORT, 'v1_model_invocations is append-only');
END;
