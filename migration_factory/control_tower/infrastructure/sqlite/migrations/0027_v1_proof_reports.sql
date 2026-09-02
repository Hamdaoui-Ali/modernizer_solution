-- V1-19B: Generate final report artifact
--
-- This migration adds tables to store final proof report artifacts
-- for the springboot-216-to-356-java21-three-stage pipeline.
--
-- A final report is generated when all three deterministic proof
-- gates are present and verified. The report is a deterministic
-- summary of the proof gates, stage chain ledger entries, and
-- migration metadata.
--
-- Model summaries CANNOT create or override proof reports.
-- Reports are computed from proof gates and stage chain data only.
--
-- Invariants preserved:
--   * Proof requires all three deterministic stage gates.
--   * Model summaries cannot create or override proof reports.
--   * Locked route remains springboot-216-to-356-java21-three-stage.
--   * Boot 4 is NOT selectable.
--   * 3.5.14 is NOT execution-relevant for V1.
--   * Browser payloads cannot choose raw paths, Maven goals, shell
--     commands, working directories, or model deployments.
--   * LLM flows cannot execute commands, approve decisions, or write
--     files directly.

CREATE TABLE v1_proof_reports (
    report_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    report_version INTEGER NOT NULL DEFAULT 1,
    report_checksum TEXT NOT NULL,
    report_algorithm TEXT NOT NULL DEFAULT 'sha256',
    gate_count INTEGER NOT NULL DEFAULT 0,
    all_gates_present INTEGER NOT NULL DEFAULT 0,
    proof_complete INTEGER NOT NULL DEFAULT 0,
    target_proof_level TEXT NOT NULL DEFAULT 'BUILD_TEST_VERIFIED',
    pipeline_id TEXT NOT NULL,
    stage_count INTEGER NOT NULL DEFAULT 3,
    summary_json TEXT NOT NULL DEFAULT '{}',
    generated_at TEXT NOT NULL,
    generated_by TEXT NOT NULL DEFAULT 'system',
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_proof_reports_job
ON v1_proof_reports(job_id);

CREATE INDEX ix_v1_proof_reports_checksum
ON v1_proof_reports(report_checksum);

CREATE TABLE v1_proof_report_gates (
    report_gate_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL,
    output_checksum TEXT NOT NULL,
    proof_gate_checksum TEXT NOT NULL,
    chain_status TEXT NOT NULL,
    FOREIGN KEY (report_id) REFERENCES v1_proof_reports(report_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_proof_report_gates_report
ON v1_proof_report_gates(report_id);

CREATE TRIGGER v1_proof_reports_no_update
BEFORE UPDATE ON v1_proof_reports
BEGIN
    SELECT RAISE(ABORT, 'v1_proof_reports is append-only');
END;

CREATE TRIGGER v1_proof_reports_no_delete
BEFORE DELETE ON v1_proof_reports
BEGIN
    SELECT RAISE(ABORT, 'v1_proof_reports is append-only');
END;

CREATE TRIGGER v1_proof_report_gates_no_update
BEFORE UPDATE ON v1_proof_report_gates
BEGIN
    SELECT RAISE(ABORT, 'v1_proof_report_gates is append-only');
END;

CREATE TRIGGER v1_proof_report_gates_no_delete
BEFORE DELETE ON v1_proof_report_gates
BEGIN
    SELECT RAISE(ABORT, 'v1_proof_report_gates is append-only');
END;
