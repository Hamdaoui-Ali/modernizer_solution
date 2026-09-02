-- V1-14C: Generate fake-provider repair proposals
--
-- Extend append-only fake repair proposal storage with deterministic
-- generated-proposal metadata. No prompt, provider output, patch
-- content, deployment ID, or executable instructions are stored.

ALTER TABLE v1_fake_repair_proposals
ADD COLUMN proposal_kind TEXT NOT NULL DEFAULT 'manual'
CHECK (proposal_kind IN ('manual', 'repair_attempt', 'generated'));

ALTER TABLE v1_fake_repair_proposals
ADD COLUMN recommendation_type TEXT;

ALTER TABLE v1_fake_repair_proposals
ADD COLUMN confidence_label TEXT;

ALTER TABLE v1_fake_repair_proposals
ADD COLUMN confidence_score REAL;

ALTER TABLE v1_fake_repair_proposals
ADD COLUMN warning_codes_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE v1_fake_repair_proposals
ADD COLUMN applicable INTEGER NOT NULL DEFAULT 1
CHECK (applicable IN (0, 1));

ALTER TABLE v1_fake_repair_proposals
ADD COLUMN context_checksum TEXT;

CREATE UNIQUE INDEX ux_v1_fake_repair_proposals_generated_context
ON v1_fake_repair_proposals(classification_id, proposal_kind, context_checksum)
WHERE proposal_kind = 'generated' AND context_checksum IS NOT NULL;

-- V1-15A: Validate patch policy
--
-- Append-only persistence for patch policy validations and
-- sandbox snapshots. Actual patch content is never stored.

CREATE TABLE v1_patch_policy_validations (
    validation_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
    validation_code TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    target_path_hash TEXT NOT NULL,
    patch_size_bytes INTEGER NOT NULL CHECK (patch_size_bytes >= 0),
    metacharacter_hits INTEGER NOT NULL CHECK (metacharacter_hits >= 0),
    policy_version TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (command_id) REFERENCES command_executions(command_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_patch_policy_validations_command_id
ON v1_patch_policy_validations(command_id, created_at DESC);

CREATE INDEX ix_v1_patch_policy_validations_job_id
ON v1_patch_policy_validations(job_id, created_at DESC);

CREATE TRIGGER v1_patch_policy_validations_no_update
BEFORE UPDATE ON v1_patch_policy_validations
BEGIN
    SELECT RAISE(ABORT, 'v1_patch_policy_validations is append-only');
END;

CREATE TRIGGER v1_patch_policy_validations_no_delete
BEFORE DELETE ON v1_patch_policy_validations
BEGIN
    SELECT RAISE(ABORT, 'v1_patch_policy_validations is append-only');
END;

CREATE TABLE v1_sandbox_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index >= 1 AND stage_index <= 3),
    sandbox_artifact_id TEXT NOT NULL,
    sandbox_checksum TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (command_id) REFERENCES command_executions(command_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_sandbox_snapshots_command_id
ON v1_sandbox_snapshots(command_id, created_at DESC);

CREATE INDEX ix_v1_sandbox_snapshots_job_id
ON v1_sandbox_snapshots(job_id, created_at DESC);

CREATE TRIGGER v1_sandbox_snapshots_no_update
BEFORE UPDATE ON v1_sandbox_snapshots
BEGIN
    SELECT RAISE(ABORT, 'v1_sandbox_snapshots is append-only');
END;

CREATE TRIGGER v1_sandbox_snapshots_no_delete
BEFORE DELETE ON v1_sandbox_snapshots
BEGIN
    SELECT RAISE(ABORT, 'v1_sandbox_snapshots is append-only');
END;
