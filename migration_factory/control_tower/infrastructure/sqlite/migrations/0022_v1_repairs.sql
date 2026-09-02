-- V1-14A: Classify failed commands for repairability
--
-- Append-only persistence for deterministic repair classifications and
-- fake repair proposal metadata. No patch content is stored or applied.

CREATE TABLE v1_repair_classifications (
    classification_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    command_status TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    evidence_summary TEXT NOT NULL,
    evidence_checksum TEXT NOT NULL,
    classification_code TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    repairable INTEGER NOT NULL CHECK (repairable IN (0, 1)),
    attempt_limit INTEGER NOT NULL CHECK (attempt_limit >= 0),
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    UNIQUE (command_id, evidence_checksum),
    FOREIGN KEY (command_id) REFERENCES command_executions(command_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_repair_classifications_command_id
ON v1_repair_classifications(command_id, created_at DESC);

CREATE INDEX ix_v1_repair_classifications_job_id
ON v1_repair_classifications(job_id, created_at DESC);

CREATE TRIGGER v1_repair_classifications_no_update
BEFORE UPDATE ON v1_repair_classifications
BEGIN
    SELECT RAISE(ABORT, 'v1_repair_classifications is append-only');
END;

CREATE TRIGGER v1_repair_classifications_no_delete
BEFORE DELETE ON v1_repair_classifications
BEGIN
    SELECT RAISE(ABORT, 'v1_repair_classifications is append-only');
END;

CREATE TABLE v1_fake_repair_proposals (
    proposal_id TEXT PRIMARY KEY,
    classification_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    proposal_order INTEGER NOT NULL CHECK (proposal_order >= 1),
    proposal_summary TEXT NOT NULL,
    proposal_checksum TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    UNIQUE (classification_id, proposal_order),
    UNIQUE (classification_id, proposal_checksum),
    FOREIGN KEY (classification_id) REFERENCES v1_repair_classifications(classification_id),
    FOREIGN KEY (command_id) REFERENCES command_executions(command_id),
    FOREIGN KEY (job_id) REFERENCES migration_jobs(job_id)
);

CREATE INDEX ix_v1_fake_repair_proposals_classification_id
ON v1_fake_repair_proposals(classification_id, proposal_order);

CREATE INDEX ix_v1_fake_repair_proposals_command_id
ON v1_fake_repair_proposals(command_id, created_at DESC);

CREATE TRIGGER v1_fake_repair_proposals_no_update
BEFORE UPDATE ON v1_fake_repair_proposals
BEGIN
    SELECT RAISE(ABORT, 'v1_fake_repair_proposals is append-only');
END;

CREATE TRIGGER v1_fake_repair_proposals_no_delete
BEFORE DELETE ON v1_fake_repair_proposals
BEGIN
    SELECT RAISE(ABORT, 'v1_fake_repair_proposals is append-only');
END;
