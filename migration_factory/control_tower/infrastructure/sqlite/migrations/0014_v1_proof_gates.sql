-- V1-19A: Compute deterministic proof gates
--
-- This migration adds tables to store deterministic proof gate
-- computations for the springboot-216-to-356-java21-three-stage
-- pipeline.
--
-- Proof gates are deterministic checksums computed from stage chain
-- ledger output checksums. All three gates (Stage 1 sandbox,
-- Stage 2 sandbox, Stage 3 sandbox) must be present for proof to
-- be considered complete.
--
-- Model summaries CANNOT create or override proof gates. Proof gates
-- are computed from the stage chain ledger only.
--
-- Invariants preserved:
--   * Proof requires all three deterministic stage gates.
--   * Model summaries cannot create or override proof gates.
--   * Locked route remains springboot-216-to-356-java21-three-stage.
--   * Boot 4 is NOT selectable.
--   * 3.5.14 is NOT execution-relevant for V1.
--   * Browser payloads cannot choose raw paths, Maven goals, shell
--     commands, working directories, or model deployments.
--   * LLM flows cannot execute commands, approve decisions, or write
--     files directly.

CREATE TABLE v1_proof_gates (
    proof_gate_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL,
    output_checksum TEXT NOT NULL,
    output_artifact_id TEXT,
    proof_gate_checksum TEXT NOT NULL,
    proof_gate_algorithm TEXT NOT NULL DEFAULT 'sha256',
    chain_status TEXT NOT NULL DEFAULT 'passed',
    computed_at TEXT NOT NULL,
    computed_by TEXT NOT NULL DEFAULT 'system',
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_proof_gates_job_stage
ON v1_proof_gates(job_id, stage_index);

CREATE INDEX ix_v1_proof_gates_checksum
ON v1_proof_gates(proof_gate_checksum);

CREATE TABLE v1_proof_gate_summary (
    summary_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    gate_count INTEGER NOT NULL DEFAULT 0,
    required_gates INTEGER NOT NULL DEFAULT 3,
    all_gates_computed INTEGER NOT NULL DEFAULT 0,
    proof_complete INTEGER NOT NULL DEFAULT 0,
    target_proof_level TEXT NOT NULL DEFAULT 'BUILD_TEST_VERIFIED',
    summary_checksum TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    computed_by TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX ix_v1_proof_gate_summary_job
ON v1_proof_gate_summary(job_id);

CREATE TRIGGER v1_proof_gates_no_update
BEFORE UPDATE ON v1_proof_gates
BEGIN
    SELECT RAISE(ABORT, 'v1_proof_gates is append-only');
END;

CREATE TRIGGER v1_proof_gates_no_delete
BEFORE DELETE ON v1_proof_gates
BEGIN
    SELECT RAISE(ABORT, 'v1_proof_gates is append-only');
END;

CREATE TRIGGER v1_proof_gate_summary_no_update
BEFORE UPDATE OF summary_id, job_id, gate_count, required_gates, summary_checksum, computed_at
ON v1_proof_gate_summary
BEGIN
    SELECT RAISE(ABORT, 'v1_proof_gate_summary core fields are append-only');
END;

CREATE TRIGGER v1_proof_gate_summary_no_delete
BEFORE DELETE ON v1_proof_gate_summary
BEGIN
    SELECT RAISE(ABORT, 'v1_proof_gate_summary is append-only');
END;
