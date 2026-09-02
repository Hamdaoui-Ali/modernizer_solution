-- V2-A3: Setup persistence for local migration setups
--
-- This migration adds tables to persist V2 local migration setup
-- drafts and preflight results.
--
-- A setup is a snapshot of the user's local migration configuration
-- (paths, JDK homes, Maven, flags) that is validated before a
-- migration job can be created.
--
-- Invariants preserved:
--   * Setups are append-only (no update, no delete).
--   * A checksum of the setup fields is computed for gating.
--   * Preflight results are tied to a specific setup checksum.
--   * Stale preflight (checksum mismatch) blocks job creation.
--   * Local paths are accepted only in local operator mode.
--   * Backend validates paths before queueing commands.
--   * Browser cannot choose commands, Maven goals, working dirs,
--     model deployments, or Stage 2/3 inputs.

CREATE TABLE v2_migration_setups (
    setup_id TEXT PRIMARY KEY,
    run_name TEXT NOT NULL,
    legacy_app_path TEXT NOT NULL,
    output_parent_path TEXT NOT NULL,
    ai_hub_path TEXT NOT NULL,
    java11_home TEXT NOT NULL,
    java17_home TEXT NOT NULL,
    java21_home TEXT NOT NULL,
    maven_cmd TEXT NOT NULL,
    proof_level TEXT NOT NULL DEFAULT 'build_test_verified',
    skip_endpoint_smoke INTEGER NOT NULL DEFAULT 0,
    migration_flags_json TEXT NOT NULL DEFAULT '{}',
    setup_checksum TEXT NOT NULL,
    checksum_algorithm TEXT NOT NULL DEFAULT 'sha256',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'operator',
    correlation_id TEXT
);

CREATE INDEX ix_v2_migration_setups_checksum
ON v2_migration_setups(setup_checksum);

CREATE INDEX ix_v2_migration_setups_created
ON v2_migration_setups(created_at);

CREATE TABLE v2_preflight_results (
    preflight_id TEXT PRIMARY KEY,
    setup_id TEXT NOT NULL,
    setup_checksum TEXT NOT NULL,
    all_ready INTEGER NOT NULL DEFAULT 0,
    legacy_app_exists INTEGER NOT NULL DEFAULT 0,
    legacy_app_has_project_file INTEGER NOT NULL DEFAULT 0,
    legacy_app_not_in_output_parent INTEGER NOT NULL DEFAULT 0,
    output_parent_writable INTEGER NOT NULL DEFAULT 0,
    ai_hub_root_exists INTEGER NOT NULL DEFAULT 0,
    ai_hub_profiles_ready INTEGER NOT NULL DEFAULT 0,
    ai_hub_catalogs_ready INTEGER NOT NULL DEFAULT 0,
    ai_hub_policies_ready INTEGER NOT NULL DEFAULT 0,
    jdk11_ready INTEGER NOT NULL DEFAULT 0,
    jdk17_ready INTEGER NOT NULL DEFAULT 0,
    jdk21_ready INTEGER NOT NULL DEFAULT 0,
    maven_ready INTEGER NOT NULL DEFAULT 0,
    pipeline_route_ready INTEGER NOT NULL DEFAULT 0,
    legacy_marker_ready INTEGER NOT NULL DEFAULT 0,
    output_parent_gate_ready INTEGER NOT NULL DEFAULT 0,
    readiness_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    errors_json TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL,
    checked_by TEXT NOT NULL DEFAULT 'system',
    correlation_id TEXT,
    FOREIGN KEY (setup_id) REFERENCES v2_migration_setups(setup_id)
);

CREATE INDEX ix_v2_preflight_results_setup
ON v2_preflight_results(setup_id);

CREATE INDEX ix_v2_preflight_results_checksum
ON v2_preflight_results(setup_checksum);

CREATE TRIGGER v2_migration_setups_no_update
BEFORE UPDATE ON v2_migration_setups
BEGIN
    SELECT RAISE(ABORT, 'v2_migration_setups is append-only');
END;

CREATE TRIGGER v2_migration_setups_no_delete
BEFORE DELETE ON v2_migration_setups
BEGIN
    SELECT RAISE(ABORT, 'v2_migration_setups is append-only');
END;

CREATE TRIGGER v2_preflight_results_no_update
BEFORE UPDATE ON v2_preflight_results
BEGIN
    SELECT RAISE(ABORT, 'v2_preflight_results is append-only');
END;

CREATE TRIGGER v2_preflight_results_no_delete
BEFORE DELETE ON v2_preflight_results
BEGIN
    SELECT RAISE(ABORT, 'v2_preflight_results is append-only');
END;
