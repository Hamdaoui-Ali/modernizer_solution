-- F15-JOB-012: Gate decision persistence
--
-- This migration adds the v2_gate_decisions table for F15 gate decision
-- records. Each row is an append-only record of a human/chatbot decision
-- at a gate, protected by idempotency and checksum binding.
--
-- Invariants preserved:
--   * Append-only: no UPDATE, no DELETE. Once inserted, immutable.
--   * Idempotency: UNIQUE (idempotency_key, request_checksum) prevents
--     duplicate submissions of the same payload.
--   * Conflicting payload: a different request_checksum under the same
--     idempotency_key violates the UNIQUE constraint on idempotency_key
--     alone (checked at the service layer before INSERT).
--   * Checksum binding: expected_gate_checksum ties the decision to an
--     exact gate snapshot.
--   * Result references are backend-owned, never frontend-supplied.

CREATE TABLE v2_gate_decisions (
    decision_id TEXT PRIMARY KEY,
    gate_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    action TEXT NOT NULL,
    expected_gate_checksum TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_checksum TEXT NOT NULL,
    result_gate_id TEXT,
    result_command_id TEXT,
    result_revision_id TEXT,
    decided_by TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL DEFAULT '',
    actor_type TEXT NOT NULL DEFAULT 'human',
    actor_id TEXT NOT NULL DEFAULT '',
    correlation_id TEXT,
    causation_id TEXT
);

-- Lookup decisions by gate
CREATE INDEX ix_v2_gate_decisions_gate
ON v2_gate_decisions(gate_id);

-- Lookup decisions by job
CREATE INDEX ix_v2_gate_decisions_job
ON v2_gate_decisions(job_id);

-- Idempotency: duplicate (key, checksum) returns same result
CREATE UNIQUE INDEX uq_v2_gate_decisions_idempotency
ON v2_gate_decisions(idempotency_key, request_checksum);

-- Append-only triggers
CREATE TRIGGER v2_gate_decisions_no_update
BEFORE UPDATE ON v2_gate_decisions
BEGIN
    SELECT RAISE(ABORT, 'v2_gate_decisions is append-only: UPDATE forbidden');
END;

CREATE TRIGGER v2_gate_decisions_no_delete
BEFORE DELETE ON v2_gate_decisions
BEGIN
    SELECT RAISE(ABORT, 'v2_gate_decisions is append-only: DELETE forbidden');
END;
