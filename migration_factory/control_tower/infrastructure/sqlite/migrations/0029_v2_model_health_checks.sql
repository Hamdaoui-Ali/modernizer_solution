-- V2-A4: Azure model health checks (non-blocking)
--
-- This migration adds the v2_model_health_checks table for recording
-- redacted Azure Foundry role health check results.
--
-- Health checks are non-blocking: Azure BLOCKED/DEGRADED/ERROR does
-- not prevent deterministic migration start. It only affects AI
-- assistant features.
--
-- Invariants:
--   * No secrets, prompts, or raw responses stored.
--   * Health checks are bound to a profile checksum.
--   * Error classifications are redacted.
--   * Append-only (no update, no delete).

CREATE TABLE v2_model_health_checks (
    health_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    profile_checksum TEXT NOT NULL,
    overall_status TEXT NOT NULL DEFAULT 'unknown',
    role_checks_json TEXT NOT NULL DEFAULT '{}',
    structured_output_checks_json TEXT NOT NULL DEFAULT '{}',
    latency_ms_json TEXT NOT NULL DEFAULT '{}',
    error_classification TEXT NOT NULL DEFAULT '',
    artifact_id TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX ix_v2_model_health_checks_profile
ON v2_model_health_checks(profile_id, created_at);

CREATE INDEX ix_v2_model_health_checks_status
ON v2_model_health_checks(overall_status, created_at);

CREATE TRIGGER v2_model_health_checks_no_update
BEFORE UPDATE ON v2_model_health_checks
BEGIN
    SELECT RAISE(ABORT, 'v2_model_health_checks is append-only');
END;

CREATE TRIGGER v2_model_health_checks_no_delete
BEFORE DELETE ON v2_model_health_checks
BEGIN
    SELECT RAISE(ABORT, 'v2_model_health_checks is append-only');
END;
