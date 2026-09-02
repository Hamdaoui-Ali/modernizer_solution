-- PR-B: Extend v2_repair_proposals for job-scoped reviewed-diff persistence.
--
-- Adds nullable columns for reviewed-diff artifacts, reviewer verdict,
-- gate binding, and attempt metadata. All new columns are nullable so
-- existing command-scoped proposals continue to load without changes.
--
-- Indexes support job-scoped and gate-scoped queries.

ALTER TABLE v2_repair_proposals ADD COLUMN job_id TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN route_step_index INTEGER;
ALTER TABLE v2_repair_proposals ADD COLUMN attempt_number INTEGER;
ALTER TABLE v2_repair_proposals ADD COLUMN failure_evidence_ref TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN repair_context_ref TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN diagnosis_ref TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN repair_plan_ref TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN diff_ref TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN diff_checksum TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN safe_diff_preview_ref TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN reviewer_verdict_id TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN reviewer_verdict_ref TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN reviewer_output_checksum TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN policy_validation_checksum TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN gate_id TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN status_reason TEXT;

CREATE INDEX IF NOT EXISTS ix_v2_repair_proposals_job_created
ON v2_repair_proposals(job_id, created_at);

CREATE INDEX IF NOT EXISTS ix_v2_repair_proposals_job_status
ON v2_repair_proposals(job_id, status);

CREATE INDEX IF NOT EXISTS ix_v2_repair_proposals_gate
ON v2_repair_proposals(gate_id);
