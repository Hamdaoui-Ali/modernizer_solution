-- V1-11A: Persist context pack manifests
--
-- Append-only table for context pack manifest records.
-- Context packs store evidence references, boundaries, redaction
-- metadata, and checksums. Raw prompts, secrets, and deployment
-- IDs are never stored.
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

CREATE TABLE v1_context_pack_manifests (
    manifest_id TEXT PRIMARY KEY,
    job_id TEXT,
    stage_run_id TEXT,
    pack_type TEXT NOT NULL,
    pack_version TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    evidence_refs_json TEXT,
    bounds_json TEXT,
    redaction_policy TEXT,
    redacted_summary TEXT,
    checksum_algorithm TEXT NOT NULL DEFAULT 'sha256',
    checksum TEXT NOT NULL,
    model_profile_id TEXT,
    model_name TEXT,
    token_count INTEGER,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id),
    FOREIGN KEY (stage_run_id) REFERENCES stage_runs(stage_run_id)
);

CREATE INDEX ix_v1_context_pack_manifests_job_id
ON v1_context_pack_manifests(job_id);

CREATE INDEX ix_v1_context_pack_manifests_stage_run_id
ON v1_context_pack_manifests(stage_run_id);

CREATE INDEX ix_v1_context_pack_manifests_created_at
ON v1_context_pack_manifests(created_at);

CREATE TRIGGER v1_context_pack_manifests_no_update
BEFORE UPDATE ON v1_context_pack_manifests
BEGIN
    SELECT RAISE(ABORT, 'v1_context_pack_manifests is append-only');
END;

CREATE TRIGGER v1_context_pack_manifests_no_delete
BEFORE DELETE ON v1_context_pack_manifests
BEGIN
    SELECT RAISE(ABORT, 'v1_context_pack_manifests is append-only');
END;
