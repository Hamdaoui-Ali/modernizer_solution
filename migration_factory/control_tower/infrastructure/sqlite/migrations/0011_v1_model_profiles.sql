-- V1-09: V1 model profiles registry
--
-- Records Azure model profiles as environment references only.
-- No raw prompts, provider secrets, or deployment IDs are stored.
-- Each profile includes a provider_kind (default: 'fake') and env refs
-- for the model endpoint and deployment. Live Azure checks are opt-in;
-- fake provider tests are the default.
--
-- The v1_model_profiles table is append-only. Profiles are registered
-- before use and referenced by profile_id in runner profiles.
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

CREATE TABLE v1_model_profiles (
    profile_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    provider_kind TEXT NOT NULL DEFAULT 'fake' CHECK (provider_kind IN ('fake', 'azure_openai')),
    model_env_ref TEXT NOT NULL,
    endpoint_env_ref TEXT NOT NULL,
    deployment_env_ref TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE INDEX ix_v1_model_profiles_provider_kind
ON v1_model_profiles(provider_kind);

CREATE TRIGGER v1_model_profiles_no_update
BEFORE UPDATE ON v1_model_profiles
BEGIN
    SELECT RAISE(ABORT, 'v1_model_profiles is append-only');
END;

CREATE TRIGGER v1_model_profiles_no_delete
BEFORE DELETE ON v1_model_profiles
BEGIN
    SELECT RAISE(ABORT, 'v1_model_profiles is append-only');
END;

-- Event log for model profile registration audits.
CREATE TABLE v1_model_profile_events (
    event_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES v1_model_profiles(profile_id),
    event_type TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT
);

CREATE INDEX ix_v1_model_profile_events_profile_id
ON v1_model_profile_events(profile_id);

CREATE TRIGGER v1_model_profile_events_no_update
BEFORE UPDATE ON v1_model_profile_events
BEGIN
    SELECT RAISE(ABORT, 'v1_model_profile_events is append-only');
END;

CREATE TRIGGER v1_model_profile_events_no_delete
BEFORE DELETE ON v1_model_profile_events
BEGIN
    SELECT RAISE(ABORT, 'v1_model_profile_events is append-only');
END;

-- Seed a default fake provider profile for development/default use.
-- The fake provider requires no real Azure credentials.
INSERT INTO v1_model_profiles (
    profile_id,
    display_name,
    provider_kind,
    model_env_ref,
    endpoint_env_ref,
    deployment_env_ref,
    is_active,
    created_at,
    created_by
) VALUES (
    'default-fake',
    'Default fake provider (no Azure credentials required)',
    'fake',
    'V1_MODEL_NAME',
    'V1_MODEL_ENDPOINT',
    'V1_MODEL_DEPLOYMENT',
    1,
    '2026-06-12T00:00:00.000000Z',
    'system'
);
