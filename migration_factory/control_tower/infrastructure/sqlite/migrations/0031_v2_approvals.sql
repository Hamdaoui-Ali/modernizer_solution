-- V2-P0-004: Approval decision card and resume command persistence
--
-- This migration adds tables to persist V2 approval decision cards
-- and resume commands. Previously, V2ApprovalMappingService stored
-- everything in-memory.
--
-- Invariants preserved:
--   * Append-only (no update, no delete).
--   * Cards track checksum, stage, and status.
--   * Resume commands reference a decision card.
--   * LLM cannot approve — exact checksum match required.

CREATE TABLE v2_approval_decisions (
    card_id TEXT PRIMARY KEY,
    interrupt_id TEXT NOT NULL,
    request_checksum TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3)),
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE INDEX ix_v2_approval_decisions_status
ON v2_approval_decisions(status);

CREATE TABLE v2_resume_commands (
    resume_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3)),
    command_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX ix_v2_resume_commands_card
ON v2_resume_commands(card_id);

CREATE INDEX ix_v2_resume_commands_job
ON v2_resume_commands(job_id);

CREATE TRIGGER v2_approval_decisions_no_delete
BEFORE DELETE ON v2_approval_decisions
BEGIN
    SELECT RAISE(ABORT, 'v2_approval_decisions is append-only');
END;

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
