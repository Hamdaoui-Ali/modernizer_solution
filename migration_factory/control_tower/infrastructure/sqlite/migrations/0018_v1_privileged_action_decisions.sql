-- V1-17C: Privileged action decision records
--
-- Append-only table for approve/reject decisions on pending
-- privileged actions. One decision per action (action_id is PK
-- to prevent duplicates). Decisions require matching checksums
-- verified at the service layer.
--
-- This table stores decisions only. Execution belongs to V1-17D.
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

CREATE TABLE v1_privileged_action_decisions (
    action_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    parameters_checksum TEXT NOT NULL,
    rejection_reason TEXT,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (action_id) REFERENCES v1_privileged_actions(action_id)
);

CREATE INDEX ix_v1_privileged_action_decisions_decision
ON v1_privileged_action_decisions(decision);

CREATE INDEX ix_v1_privileged_action_decisions_decided_at
ON v1_privileged_action_decisions(decided_at);

CREATE TRIGGER v1_privileged_action_decisions_no_update
BEFORE UPDATE ON v1_privileged_action_decisions
BEGIN
    SELECT RAISE(ABORT, 'v1_privileged_action_decisions is append-only');
END;

CREATE TRIGGER v1_privileged_action_decisions_no_delete
BEFORE DELETE ON v1_privileged_action_decisions
BEGIN
    SELECT RAISE(ABORT, 'v1_privileged_action_decisions is append-only');
END;
