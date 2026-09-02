-- F15-JOB-013: Artifact revision persistence
--
-- This migration adds the v2_artifact_revisions table for F15 versioned
-- evidence. Tracks analysis, planning, approval, and repair revisions
-- with lineage (prior_revision_id, superseded_by_revision_id) and
-- acceptance state.
--
-- Invariants preserved:
--   * Append-only: revisions are never updated after insertion.
--     Status transitions (draft -> accepted, accepted -> superseded)
--     are represented by inserting new revisions with appropriate
--     prior_revision_id / superseded_by_revision_id links.
--   * Checksum binding: every revision carries an evidence_checksum
--     and optional prior_revision_checksum for chain verification.
--   * Downstream phases consume only ACCEPTED revisions (enforced at
--     the service layer).

CREATE TABLE v2_artifact_revisions (
    revision_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3)),
    revision_kind TEXT NOT NULL,
    revision_status TEXT NOT NULL DEFAULT 'draft',
    revision_order INTEGER NOT NULL DEFAULT 0,
    evidence_checksum TEXT NOT NULL,
    prior_revision_checksum TEXT,
    artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    prior_revision_id TEXT,
    superseded_by_revision_id TEXT,
    accepted_at_gate_id TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    accepted_at TEXT,
    accepted_by TEXT
);

-- Lookup by job + stage
CREATE INDEX ix_v2_artifact_revisions_job_stage
ON v2_artifact_revisions(job_id, stage_index);

-- Lookup by job + kind + status (e.g. find accepted analysis)
CREATE INDEX ix_v2_artifact_revisions_job_kind_status
ON v2_artifact_revisions(job_id, revision_kind, revision_status);

-- Lookup by prior revision (chain traversal)
CREATE INDEX ix_v2_artifact_revisions_prior
ON v2_artifact_revisions(prior_revision_id);

-- Lookup by superseding revision
CREATE INDEX ix_v2_artifact_revisions_superseded_by
ON v2_artifact_revisions(superseded_by_revision_id);

-- At most one ACCEPTED revision per (job_id, stage_index, revision_kind)
CREATE UNIQUE INDEX uq_v2_artifact_revisions_accepted
ON v2_artifact_revisions(job_id, stage_index, revision_kind)
WHERE revision_status = 'accepted';

-- Append-only triggers
CREATE TRIGGER v2_artifact_revisions_no_update
BEFORE UPDATE ON v2_artifact_revisions
BEGIN
    SELECT RAISE(ABORT, 'v2_artifact_revisions is append-only: UPDATE forbidden');
END;

CREATE TRIGGER v2_artifact_revisions_no_delete
BEFORE DELETE ON v2_artifact_revisions
BEGIN
    SELECT RAISE(ABORT, 'v2_artifact_revisions is append-only: DELETE forbidden');
END;
