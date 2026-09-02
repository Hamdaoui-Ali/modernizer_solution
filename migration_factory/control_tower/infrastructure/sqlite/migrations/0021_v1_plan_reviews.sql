-- V1-13: Gate plans with reviewer
--
-- Append-only reviewer decisions bound to exact plan revision checksum.
-- A revision may be reviewed at most once; duplicate same-checksum same-decision
-- requests are handled idempotently at the service layer.

CREATE TABLE v1_plan_review_decisions (
    review_decision_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL UNIQUE,
    amendment_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    reviewed_checksum TEXT NOT NULL,
    review_summary TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (revision_id) REFERENCES v1_plan_revisions(revision_id),
    FOREIGN KEY (amendment_id) REFERENCES v1_plan_amendments(amendment_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_plan_review_decisions_revision_id
ON v1_plan_review_decisions(revision_id);

CREATE INDEX ix_v1_plan_review_decisions_job_id
ON v1_plan_review_decisions(job_id, created_at DESC);

CREATE TRIGGER v1_plan_review_decisions_no_update
BEFORE UPDATE ON v1_plan_review_decisions
BEGIN
    SELECT RAISE(ABORT, 'v1_plan_review_decisions is append-only');
END;

CREATE TRIGGER v1_plan_review_decisions_no_delete
BEFORE DELETE ON v1_plan_review_decisions
BEGIN
    SELECT RAISE(ABORT, 'v1_plan_review_decisions is append-only');
END;
