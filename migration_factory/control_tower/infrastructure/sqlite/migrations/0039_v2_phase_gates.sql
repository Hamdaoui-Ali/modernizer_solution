-- F15-JOB-011: Phase gate persistence
--
-- This migration adds the v2_phase_gates table for F15 governed-stage
-- gate state. Each row represents a single wait-state gate at an
-- analysis, planning, approval, repair, or stage-completion review point.
--
-- Invariants preserved:
--   * Append-only: once a gate is resolved or superseded, its row
--     must never be updated. Status changes for open gates (e.g.
--     supersede) are done via INSERT-only patterns at the service layer.
--   * Open-gate uniqueness: at most one row per (job_id, gate_phase,
--     stage_index) may have gate_status = 'open'. Enforced via a
--     partial unique index.
--   * Artifact binding: every gate stores a checksum of the evidence
--     it reviews. Explanations must read this checksum, not stale previews.
--   * No sandbox_path, argv, env, or raw filesystem targets stored.

CREATE TABLE v2_phase_gates (
    gate_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    gate_phase TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3)),
    gate_status TEXT NOT NULL DEFAULT 'open',
    gate_decision TEXT NOT NULL DEFAULT 'pending',
    source_artifact_checksum TEXT NOT NULL DEFAULT '',
    resolved_artifact_checksum TEXT,
    source_artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT
);

-- Job-level lookup for all gates
CREATE INDEX ix_v2_phase_gates_job
ON v2_phase_gates(job_id);

-- Lookup by job + stage for gate panel queries
CREATE INDEX ix_v2_phase_gates_job_stage
ON v2_phase_gates(job_id, stage_index);

-- Open-gate uniqueness: at most one open gate per (job_id, gate_phase, stage_index)
CREATE UNIQUE INDEX uq_v2_phase_gates_open
ON v2_phase_gates(job_id, gate_phase, stage_index)
WHERE gate_status = 'open';

-- Append-only triggers: resolved and superseded rows are immutable
CREATE TRIGGER v2_phase_gates_no_delete
BEFORE DELETE ON v2_phase_gates
BEGIN
    SELECT RAISE(ABORT, 'v2_phase_gates is append-only: DELETE forbidden');
END;

CREATE TRIGGER v2_phase_gates_no_update_resolved
BEFORE UPDATE ON v2_phase_gates
WHEN OLD.gate_status IN ('resolved', 'superseded')
BEGIN
    SELECT RAISE(ABORT, 'v2_phase_gates: resolved/superseded gates are immutable');
END;
