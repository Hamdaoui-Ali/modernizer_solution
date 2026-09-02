-- V1-00C: V1 event type registry
--
-- Records the canonical V1 event types in an append-only registry table.
-- This provides runtime introspection so the Control Tower and external
-- tooling can enumerate valid V1 event types without loading Python code.
--
-- The v1_event_type_registry table is seeded at migration time with every
-- canonical event type from the V1EventType enum. New event types are
-- added only via migration (append-only), never at runtime.
--
-- Invariants preserved:
--   * Pipeline ID remains springboot-216-to-356-java21-three-stage
--   * Stage 1: Java 11 / Spring Boot 2.7.18 / legacy_source
--   * Stage 2: Java 17 / Spring Boot 3.5.6 / previous_stage (stage 1)
--   * Stage 3: Java 21 / Spring Boot 3.5.6 / previous_stage (stage 2)
--   * Boot 4 NOT selectable
--   * 3.5.14 NOT execution-relevant
--   * No browser-selected raw paths, Maven goals, shell commands,
--     working directories, or model deployment IDs
--   * LLM cannot execute, approve, write files, or create proof

CREATE TABLE v1_event_type_registry (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'job_lifecycle',
        'command_lifecycle',
        'route_validation',
        'stage_chain_lifecycle'
    )),
    description TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE INDEX ix_v1_event_type_registry_category
ON v1_event_type_registry(category);

CREATE TRIGGER v1_event_type_registry_no_update
BEFORE UPDATE ON v1_event_type_registry
BEGIN
    SELECT RAISE(ABORT, 'v1_event_type_registry is append-only');
END;

CREATE TRIGGER v1_event_type_registry_no_delete
BEFORE DELETE ON v1_event_type_registry
BEGIN
    SELECT RAISE(ABORT, 'v1_event_type_registry is append-only');
END;

-- Seed all canonical V1 event types.
INSERT INTO v1_event_type_registry (event_type, category, description, is_active, created_at, created_by) VALUES
    ('job_created',          'job_lifecycle',       'Migration job created',                                             1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('artifact_registered',  'job_lifecycle',       'Artifact registered for a job',                                    1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('command_queued',       'command_lifecycle',   'Command queued for execution',                                     1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('command_starting',     'command_lifecycle',   'Command execution starting',                                       1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('command_running',      'command_lifecycle',   'Command execution running',                                        1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('command_finalized',    'command_lifecycle',   'Command execution finalized (terminal outcome)',                    1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('pipeline_validation',  'route_validation',    'Pipeline definition validated against locked V1 route',             1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('runner_validation',    'route_validation',    'Runner profile validated against locked V1 route',                 1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('chain_created',        'stage_chain_lifecycle','V1 stage chain ledger entry created',                             1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('chain_started',        'stage_chain_lifecycle','V1 stage chain execution started',                                1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('chain_completed',      'stage_chain_lifecycle','V1 stage chain execution completed',                              1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('chain_failed',         'stage_chain_lifecycle','V1 stage chain execution failed',                                 1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('stage_started',        'stage_chain_lifecycle','Individual V1 stage started',                                     1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('stage_completed',      'stage_chain_lifecycle','Individual V1 stage completed',                                   1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('stage_failed',         'stage_chain_lifecycle','Individual V1 stage failed',                                      1, '2026-06-12T00:00:00.000000Z', 'system'),
    ('output_registered',    'stage_chain_lifecycle','Stage output artifact registered in registry',                     1, '2026-06-12T00:00:00.000000Z', 'system');
