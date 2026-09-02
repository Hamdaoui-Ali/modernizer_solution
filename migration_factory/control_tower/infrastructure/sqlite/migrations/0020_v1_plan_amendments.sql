-- V1-12A: Persist plan amendments and revisions
--
-- Append-only persistence for non-authoritative plan amendments and
-- immutable ordered revisions. These records are foundation only:
-- they do not apply source changes, mutate sandbox state, or alter
-- deterministic execution by themselves.

CREATE TABLE v1_plan_amendments (
    amendment_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('manual', 'fake_provider')),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    redacted_summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_plan_amendments_job_id
ON v1_plan_amendments(job_id);

CREATE INDEX ix_v1_plan_amendments_created_at
ON v1_plan_amendments(created_at);

CREATE TRIGGER v1_plan_amendments_no_update
BEFORE UPDATE ON v1_plan_amendments
BEGIN
    SELECT RAISE(ABORT, 'v1_plan_amendments is append-only');
END;

CREATE TRIGGER v1_plan_amendments_no_delete
BEFORE DELETE ON v1_plan_amendments
BEGIN
    SELECT RAISE(ABORT, 'v1_plan_amendments is append-only');
END;

CREATE TABLE v1_plan_revisions (
    revision_id TEXT PRIMARY KEY,
    amendment_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    revision_order INTEGER NOT NULL CHECK (revision_order >= 1),
    revision_state TEXT NOT NULL
        CHECK (revision_state IN ('draft', 'accepted', 'rejected', 'finalized')),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('manual', 'fake_provider')),
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    redacted_summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (amendment_id) REFERENCES v1_plan_amendments(amendment_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id),
    CHECK (
        (revision_state = 'draft' AND decided_at IS NULL AND decided_by IS NULL)
        OR
        (revision_state IN ('accepted', 'rejected', 'finalized')
         AND decided_at IS NOT NULL AND decided_by IS NOT NULL)
    ),
    UNIQUE (amendment_id, revision_order)
);

CREATE UNIQUE INDEX ux_v1_plan_revisions_terminal
ON v1_plan_revisions(amendment_id)
WHERE revision_state IN ('accepted', 'finalized');

CREATE INDEX ix_v1_plan_revisions_amendment_id
ON v1_plan_revisions(amendment_id);

CREATE INDEX ix_v1_plan_revisions_job_id
ON v1_plan_revisions(job_id);

CREATE INDEX ix_v1_plan_revisions_created_at
ON v1_plan_revisions(created_at);

CREATE TRIGGER v1_plan_revisions_no_update
BEFORE UPDATE ON v1_plan_revisions
BEGIN
    SELECT RAISE(ABORT, 'v1_plan_revisions is append-only');
END;

CREATE TRIGGER v1_plan_revisions_no_delete
BEFORE DELETE ON v1_plan_revisions
BEGIN
    SELECT RAISE(ABORT, 'v1_plan_revisions is append-only');
END;
