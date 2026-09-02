-- V2 cockpit approval cards must be visible before a resume command exists.
-- Add a job_id directly to decision cards and index it for job-scoped reads.

ALTER TABLE v2_approval_decisions
ADD COLUMN job_id TEXT NOT NULL DEFAULT '';

CREATE INDEX ix_v2_approval_decisions_job
ON v2_approval_decisions(job_id);
