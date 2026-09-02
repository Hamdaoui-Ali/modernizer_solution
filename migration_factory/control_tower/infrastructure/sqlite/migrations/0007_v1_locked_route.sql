-- V1-02: Lock V1 migration route
--
-- Records the locked V1 pipeline configuration so that the Control Tower
-- can validate that only the approved three-stage pipeline is used.
--
-- The `v1_route_config` table stores a single row that captures the exact
-- V1 route contract. Attempting to insert a second row raises a constraint
-- failure.
--
-- The `v1_route_validation_events` table records validation outcomes when
-- a pipeline/runner registration or job creation is checked against the
-- locked route. This is an append-only audit log.
--
-- Invariants preserved:
--   * Pipeline ID: springboot-216-to-356-java21-three-stage
--   * Stage 1: Java 11 / Spring Boot 2.7.18 / legacy_source
--   * Stage 2: Java 17 / Spring Boot 3.5.6 / previous_stage (stage 1)
--   * Stage 3: Java 21 / Spring Boot 3.5.6 / previous_stage (stage 2)
--   * Boot 4 NOT selectable
--   * 3.5.14 NOT execution-relevant

CREATE TABLE v1_route_config (
    row_id INTEGER PRIMARY KEY CHECK (row_id = 1) UNIQUE,
    pipeline_id TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    stage_count INTEGER NOT NULL CHECK (stage_count = 3),
    stage1_id TEXT NOT NULL,
    stage1_jdk TEXT NOT NULL,
    stage1_spring_boot TEXT NOT NULL,
    stage1_java INTEGER NOT NULL,
    stage2_id TEXT NOT NULL,
    stage2_jdk TEXT NOT NULL,
    stage2_spring_boot TEXT NOT NULL,
    stage2_java INTEGER NOT NULL,
    stage3_id TEXT NOT NULL,
    stage3_jdk TEXT NOT NULL,
    stage3_spring_boot TEXT NOT NULL,
    stage3_java INTEGER NOT NULL,
    boot4_selectable INTEGER NOT NULL DEFAULT 0 CHECK (boot4_selectable = 0),
    selectable_boot4_allowed INTEGER NOT NULL DEFAULT 0 CHECK (selectable_boot4_allowed = 0),
    execution_relevant_3514 INTEGER NOT NULL DEFAULT 0 CHECK (execution_relevant_3514 = 0),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE TRIGGER v1_route_config_no_update
BEFORE UPDATE ON v1_route_config
BEGIN
    SELECT RAISE(ABORT, 'v1_route_config is append-only; insert is limited to exactly one row');
END;

CREATE TRIGGER v1_route_config_no_delete
BEFORE DELETE ON v1_route_config
BEGIN
    SELECT RAISE(ABORT, 'v1_route_config is append-only');
END;

CREATE TABLE v1_route_validation_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    pipeline_version TEXT,
    runner_profile_id TEXT,
    runner_profile_version TEXT,
    validation_result TEXT NOT NULL CHECK (validation_result IN ('pass', 'fail')),
    failure_reason TEXT,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE INDEX ix_v1_route_validation_events_created_at
ON v1_route_validation_events(created_at);

CREATE TRIGGER v1_route_validation_events_no_update
BEFORE UPDATE ON v1_route_validation_events
BEGIN
    SELECT RAISE(ABORT, 'v1_route_validation_events are append-only');
END;

CREATE TRIGGER v1_route_validation_events_no_delete
BEFORE DELETE ON v1_route_validation_events
BEGIN
    SELECT RAISE(ABORT, 'v1_route_validation_events are append-only');
END;

-- Seed the single locked route row at migration time.
-- This enforces the V1 route contract at the database level.
INSERT INTO v1_route_config (
    row_id,
    pipeline_id,
    pipeline_version,
    schema_version,
    stage_count,
    stage1_id,
    stage1_jdk,
    stage1_spring_boot,
    stage1_java,
    stage2_id,
    stage2_jdk,
    stage2_spring_boot,
    stage2_java,
    stage3_id,
    stage3_jdk,
    stage3_spring_boot,
    stage3_java,
    boot4_selectable,
    selectable_boot4_allowed,
    execution_relevant_3514,
    created_at,
    created_by
) VALUES (
    1,
    'springboot-216-to-356-java21-three-stage',
    '2026.06',
    '1.0.0',
    3,
    'springboot-216-to-27-java11',
    'java11',
    '2.7.18',
    11,
    'springboot-27-to-35-java17',
    'java17',
    '3.5.6',
    17,
    'springboot-35-java17-to-java21',
    'java21',
    '3.5.6',
    21,
    0,
    0,
    0,
    '2026-06-12T00:00:00.000000Z',
    'system'
);
