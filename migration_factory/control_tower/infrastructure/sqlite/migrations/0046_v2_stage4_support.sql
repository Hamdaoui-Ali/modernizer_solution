-- 0046_v2_stage4_support.sql
-- Widen all V2/F15 stage constraints from 1..3 to 1..4.
-- Preserve complete post-0045 schemas and immutable behavior.

-- ── 1. v2_stage_commands ────────────────────────────────────────────
ALTER TABLE v2_stage_commands
RENAME TO v2_stage_commands_old_0046;

DROP INDEX IF EXISTS ix_v2_stage_commands_job;
DROP TRIGGER IF EXISTS v2_stage_commands_no_update;
DROP TRIGGER IF EXISTS v2_stage_commands_no_delete;

CREATE TABLE v2_stage_commands (
    command_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3, 4)),
    manifest_checksum TEXT NOT NULL,
    argv_json TEXT NOT NULL DEFAULT '[]',
    env_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'manifest_ready',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    result_json TEXT,
    gate_id TEXT,
    decision_id TEXT
);

INSERT INTO v2_stage_commands (
    command_id,
    job_id,
    stage_index,
    manifest_checksum,
    argv_json,
    env_json,
    status,
    created_at,
    updated_at,
    result_json,
    gate_id,
    decision_id
)
SELECT
    command_id,
    job_id,
    stage_index,
    manifest_checksum,
    argv_json,
    env_json,
    status,
    created_at,
    updated_at,
    result_json,
    gate_id,
    decision_id
FROM v2_stage_commands_old_0046;

CREATE INDEX ix_v2_stage_commands_job
ON v2_stage_commands(job_id, stage_index);

CREATE TRIGGER v2_stage_commands_no_update
BEFORE UPDATE ON v2_stage_commands
BEGIN
    SELECT RAISE(ABORT, 'v2_stage_commands is append-only');
END;

CREATE TRIGGER v2_stage_commands_no_delete
BEFORE DELETE ON v2_stage_commands
BEGIN
    SELECT RAISE(ABORT, 'v2_stage_commands is append-only');
END;

DROP TABLE v2_stage_commands_old_0046;

-- ── 2. v2_approval_decisions ────────────────────────────────────────
ALTER TABLE v2_approval_decisions
RENAME TO v2_approval_decisions_old_0046;

DROP INDEX IF EXISTS ix_v2_approval_decisions_job;
DROP INDEX IF EXISTS ix_v2_approval_decisions_status;
DROP TRIGGER IF EXISTS v2_approval_decisions_no_delete;

CREATE TABLE v2_approval_decisions (
    card_id TEXT PRIMARY KEY,
    interrupt_id TEXT NOT NULL,
    request_checksum TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3, 4)),
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    job_id TEXT NOT NULL DEFAULT ''
);

INSERT INTO v2_approval_decisions (
    card_id,
    interrupt_id,
    request_checksum,
    stage_index,
    summary,
    status,
    created_at,
    job_id
)
SELECT
    card_id,
    interrupt_id,
    request_checksum,
    stage_index,
    summary,
    status,
    created_at,
    job_id
FROM v2_approval_decisions_old_0046;

CREATE INDEX ix_v2_approval_decisions_status
ON v2_approval_decisions(status);

CREATE INDEX ix_v2_approval_decisions_job
ON v2_approval_decisions(job_id);

CREATE TRIGGER v2_approval_decisions_no_delete
BEFORE DELETE ON v2_approval_decisions
BEGIN
    SELECT RAISE(ABORT, 'v2_approval_decisions is append-only');
END;

DROP TABLE v2_approval_decisions_old_0046;

-- ── 3. v2_resume_commands ──────────────────────────────────────────
ALTER TABLE v2_resume_commands
RENAME TO v2_resume_commands_old_0046;

DROP INDEX IF EXISTS ix_v2_resume_commands_card;
DROP INDEX IF EXISTS ix_v2_resume_commands_job;
DROP TRIGGER IF EXISTS v2_resume_commands_no_update;
DROP TRIGGER IF EXISTS v2_resume_commands_no_delete;

CREATE TABLE v2_resume_commands (
    resume_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3, 4)),
    command_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

INSERT INTO v2_resume_commands (
    resume_id,
    card_id,
    decision,
    job_id,
    stage_index,
    command_json,
    created_at
)
SELECT
    resume_id,
    card_id,
    decision,
    job_id,
    stage_index,
    command_json,
    created_at
FROM v2_resume_commands_old_0046;

CREATE INDEX ix_v2_resume_commands_card
ON v2_resume_commands(card_id);

CREATE INDEX ix_v2_resume_commands_job
ON v2_resume_commands(job_id);

CREATE TRIGGER v2_resume_commands_no_update
BEFORE UPDATE ON v2_resume_commands
BEGIN
    SELECT RAISE(ABORT, 'v2_resume_commands is append-only');
END;

CREATE TRIGGER v2_resume_commands_no_delete
BEFORE DELETE ON v2_resume_commands
BEGIN
    SELECT RAISE(ABORT, 'v2_resume_commands is append-only');
END;

DROP TABLE v2_resume_commands_old_0046;

-- ── 4. v2_pending_action_drafts ─────────────────────────────────────
ALTER TABLE v2_pending_action_drafts
RENAME TO v2_pending_action_drafts_old_0046;

DROP INDEX IF EXISTS ix_v2_pending_action_drafts_job;
DROP TRIGGER IF EXISTS v2_pending_action_drafts_no_delete;

CREATE TABLE v2_pending_action_drafts (
    action_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3, 4)),
    payload_checksum TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);

INSERT INTO v2_pending_action_drafts (
    action_id,
    job_id,
    action_type,
    reason,
    stage_index,
    payload_checksum,
    status,
    created_at
)
SELECT
    action_id,
    job_id,
    action_type,
    reason,
    stage_index,
    payload_checksum,
    status,
    created_at
FROM v2_pending_action_drafts_old_0046;

CREATE INDEX ix_v2_pending_action_drafts_job
ON v2_pending_action_drafts(job_id, status);

CREATE TRIGGER v2_pending_action_drafts_no_delete
BEFORE DELETE ON v2_pending_action_drafts
BEGIN
    SELECT RAISE(ABORT, 'v2_pending_action_drafts is append-only');
END;

DROP TABLE v2_pending_action_drafts_old_0046;

-- ── 5. v2_job_events ───────────────────────────────────────────────
ALTER TABLE v2_job_events
RENAME TO v2_job_events_old_0046;

DROP INDEX IF EXISTS ix_v2_job_events_job_sequence;
DROP INDEX IF EXISTS ix_v2_job_events_job_type;
DROP TRIGGER IF EXISTS v2_job_events_no_update;
DROP TRIGGER IF EXISTS v2_job_events_no_delete;

CREATE TABLE v2_job_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage INTEGER CHECK (stage IS NULL OR stage IN (1, 2, 3, 4)),
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    sequence INTEGER NOT NULL UNIQUE
);

INSERT INTO v2_job_events (
    event_id,
    job_id,
    stage,
    type,
    status,
    message,
    payload_json,
    created_at,
    sequence
)
SELECT
    event_id,
    job_id,
    stage,
    type,
    status,
    message,
    payload_json,
    created_at,
    sequence
FROM v2_job_events_old_0046;

CREATE INDEX ix_v2_job_events_job_sequence
ON v2_job_events(job_id, sequence);

CREATE INDEX ix_v2_job_events_job_type
ON v2_job_events(job_id, type);

CREATE TRIGGER v2_job_events_no_update
BEFORE UPDATE ON v2_job_events
BEGIN
    SELECT RAISE(ABORT, 'v2_job_events is append-only');
END;

CREATE TRIGGER v2_job_events_no_delete
BEFORE DELETE ON v2_job_events
BEGIN
    SELECT RAISE(ABORT, 'v2_job_events is append-only');
END;

DROP TABLE v2_job_events_old_0046;

-- ── 6. v2_phase_gates ──────────────────────────────────────────────
ALTER TABLE v2_phase_gates
RENAME TO v2_phase_gates_old_0046;

DROP INDEX IF EXISTS ix_v2_phase_gates_job;
DROP INDEX IF EXISTS ix_v2_phase_gates_job_stage;
DROP INDEX IF EXISTS uq_v2_phase_gates_open;
DROP TRIGGER IF EXISTS v2_phase_gates_no_delete;
DROP TRIGGER IF EXISTS v2_phase_gates_no_update_resolved;

CREATE TABLE v2_phase_gates (
    gate_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    gate_phase TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3, 4)),
    gate_status TEXT NOT NULL DEFAULT 'open',
    gate_decision TEXT NOT NULL DEFAULT 'pending',
    source_artifact_checksum TEXT NOT NULL DEFAULT '',
    resolved_artifact_checksum TEXT,
    source_artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT
);

INSERT INTO v2_phase_gates (
    gate_id,
    job_id,
    gate_phase,
    stage_index,
    gate_status,
    gate_decision,
    source_artifact_checksum,
    resolved_artifact_checksum,
    source_artifact_refs_json,
    created_at,
    resolved_at,
    resolved_by
)
SELECT
    gate_id,
    job_id,
    gate_phase,
    stage_index,
    gate_status,
    gate_decision,
    source_artifact_checksum,
    resolved_artifact_checksum,
    source_artifact_refs_json,
    created_at,
    resolved_at,
    resolved_by
FROM v2_phase_gates_old_0046;

CREATE INDEX ix_v2_phase_gates_job
ON v2_phase_gates(job_id);

CREATE INDEX ix_v2_phase_gates_job_stage
ON v2_phase_gates(job_id, stage_index);

CREATE UNIQUE INDEX uq_v2_phase_gates_open
ON v2_phase_gates(job_id, gate_phase, stage_index)
WHERE gate_status = 'open';

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

DROP TABLE v2_phase_gates_old_0046;

-- ── 7. v2_artifact_revisions ───────────────────────────────────────
ALTER TABLE v2_artifact_revisions
RENAME TO v2_artifact_revisions_old_0046;

DROP INDEX IF EXISTS ix_v2_artifact_revisions_job_stage;
DROP INDEX IF EXISTS ix_v2_artifact_revisions_job_kind_status;
DROP INDEX IF EXISTS ix_v2_artifact_revisions_prior;
DROP INDEX IF EXISTS ix_v2_artifact_revisions_superseded_by;
DROP INDEX IF EXISTS uq_v2_artifact_revisions_accepted;
DROP TRIGGER IF EXISTS v2_artifact_revisions_no_update;
DROP TRIGGER IF EXISTS v2_artifact_revisions_no_delete;

CREATE TABLE v2_artifact_revisions (
    revision_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3, 4)),
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

INSERT INTO v2_artifact_revisions (
    revision_id,
    job_id,
    stage_index,
    revision_kind,
    revision_status,
    revision_order,
    evidence_checksum,
    prior_revision_checksum,
    artifact_refs_json,
    prior_revision_id,
    superseded_by_revision_id,
    accepted_at_gate_id,
    created_at,
    created_by,
    accepted_at,
    accepted_by
)
SELECT
    revision_id,
    job_id,
    stage_index,
    revision_kind,
    revision_status,
    revision_order,
    evidence_checksum,
    prior_revision_checksum,
    artifact_refs_json,
    prior_revision_id,
    superseded_by_revision_id,
    accepted_at_gate_id,
    created_at,
    created_by,
    accepted_at,
    accepted_by
FROM v2_artifact_revisions_old_0046;

CREATE INDEX ix_v2_artifact_revisions_job_stage
ON v2_artifact_revisions(job_id, stage_index);

CREATE INDEX ix_v2_artifact_revisions_job_kind_status
ON v2_artifact_revisions(job_id, revision_kind, revision_status);

CREATE INDEX ix_v2_artifact_revisions_prior
ON v2_artifact_revisions(prior_revision_id);

CREATE INDEX ix_v2_artifact_revisions_superseded_by
ON v2_artifact_revisions(superseded_by_revision_id);

CREATE UNIQUE INDEX uq_v2_artifact_revisions_accepted
ON v2_artifact_revisions(job_id, stage_index, revision_kind)
WHERE revision_status = 'accepted';

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

DROP TABLE v2_artifact_revisions_old_0046;
